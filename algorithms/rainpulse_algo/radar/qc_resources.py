"""QC geometry I/O boundary shared by online preparation and offline replay.

No Worker, message publication or algorithm decisions are imported here. The
provider object is deliberately small: tests/replay can supply frozen local
resources, while online callers retain the established environment semantics.
"""
from __future__ import annotations

import hashlib
import io
from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path
from pathlib import PurePosixPath
from typing import Any
from urllib.parse import unquote, urlparse

import numpy as np


@dataclass(frozen=True)
class GeometryProviders:
    radar_config: Callable[[Path], Any]
    beam_context: Callable[[Any], Any]
    ancillary_source: Callable[[Path], Any]
    terrain_store: Callable[..., Any]
    optional_file: Callable[[str], Path | None]
    optional_directory: Callable[[str], Path | None]


def _parse_s3_uri(uri: str) -> tuple[str, str]:
    parsed = urlparse(uri)
    key = parsed.path.strip("/")
    key_path = PurePosixPath(key)
    if (
        parsed.scheme != "s3"
        or not parsed.netloc
        or parsed.username is not None
        or parsed.query
        or parsed.fragment
        or not key
        or ".." in key_path.parts
        or str(key_path) != key
    ):
        raise ValueError(f"expected s3 URI, got {uri!r}")
    return parsed.netloc, key


def load_npz(uri: str, client: Any) -> dict[str, np.ndarray]:
    """Load non-pickled NPZ assets without importing the Worker implementation."""
    from .qc import QCConfigError
    from .qc_resource_paths import required_roots

    parsed = urlparse(uri)
    if parsed.scheme == "s3":
        bucket, key = _parse_s3_uri(uri)
        response = client.get_object(bucket, key)
        try:
            data = response.read()
        finally:
            response.close()
            response.release_conn()
    elif parsed.scheme == "file":
        path = Path(unquote(parsed.path)).resolve(strict=True)
        roots = required_roots("RAINPULSE_QC_ASSET_ROOTS")
        if not any(path == root or root in path.parents for root in roots):
            raise QCConfigError("QC ancillary file is outside the configured roots")
        data = path.read_bytes()
    else:
        raise QCConfigError(f"unsupported QC ancillary URI {uri!r}")
    with np.load(io.BytesIO(data), allow_pickle=False) as archive:
        return {name: archive[name].astype("float32") for name in archive.files}


def environment_providers() -> GeometryProviders:
    # Resolve I/O at the boundary, not by importing the Worker implementation.
    from .ancillary import load_source
    from .config import load_radar_config
    from .dem import VerifiedDEMTileStore
    from .qc_geometry import radar_beam_context_from_config
    from .qc_resource_paths import optional_directory, optional_file

    return GeometryProviders(
        load_radar_config, radar_beam_context_from_config, load_source,
        VerifiedDEMTileStore, optional_file, optional_directory,
    )


def load_geometry_resources(
    request: Any,
    profile: Any,
    *,
    audit: dict[str, Any] | None = None,
    providers: GeometryProviders | None = None,
) -> tuple[Any | None, Any | None, Path | None, str | None]:
    record = audit if audit is not None else {}
    record.update(
        status="not_requested",
        beam_status="unavailable",
        terrain_status="unavailable",
        resources={},
    )
    if (
        getattr(profile, "engine", None) != "open_source"
        and profile.decision_version != "evidence-v2"
    ):
        return None, None, None, None
    io = providers if providers is not None else environment_providers()
    radar_config_dir = io.optional_directory("RAINPULSE_RADAR_CONFIG_DIR")
    if radar_config_dir is None:
        record["status"] = "radar_config_directory_unavailable"
        return None, None, None, None
    record["status"] = "radar_config_invalid"
    try:
        path = radar_config_dir / f"{request.payload.radar_id}.yaml"
        before = path.read_bytes()
        current_radar_config = io.radar_config(path)
        if path.read_bytes() != before:
            raise ValueError("radar geometry config changed while reading")
        record["resources"]["current_radar_config"] = {
            "sha256": hashlib.sha256(before).hexdigest(),
            "config_version": current_radar_config.config_version,
        }
        if current_radar_config.radar_id.lower() != request.payload.radar_id.lower():
            record["status"] = "radar_identity_mismatch"
            return None, None, radar_config_dir, None
        current_beam_context = io.beam_context(current_radar_config)
    except (OSError, ValueError) as error:
        record["error_type"] = type(error).__name__
        return None, None, radar_config_dir, None
    record.update(
        status="beam_loaded",
        beam_status="loaded",
        altitude_datum_status=current_beam_context.altitude_datum_status,
        terrain_status="not_configured",
    )
    ancillary_config_path = io.optional_file("RAINPULSE_ANCILLARY_CONFIG")
    ancillary_root = io.optional_directory("RAINPULSE_ANCILLARY_ROOT")
    expected_dem_asset_version = current_radar_config.ancillary.get("dem_asset_version")
    if (
        ancillary_config_path is None
        or ancillary_root is None
        or not isinstance(expected_dem_asset_version, str)
        or not expected_dem_asset_version
    ):
        return current_beam_context, None, radar_config_dir, None
    try:
        before = ancillary_config_path.read_bytes()
        ancillary_source = io.ancillary_source(ancillary_config_path)
        if ancillary_config_path.read_bytes() != before:
            raise ValueError("ancillary config changed while reading")
        record["resources"]["ancillary_config"] = {
            "sha256": hashlib.sha256(before).hexdigest(),
        }
        terrain = io.terrain_store(
            ancillary_source,
            ancillary_root,
            expected_asset_version=expected_dem_asset_version,
            expected_config_version=ancillary_source.config_version,
        )
        record["resources"]["dem_manifest"] = {"sha256": terrain.manifest_sha256}
        record.update(status="resources_loaded", terrain_status="manifest_verified")
        # Individual raster bytes are still verified lazily by the original store.
    except (OSError, RuntimeError, ValueError) as error:
        terrain = None
        record.update(terrain_status="invalid", error_type=type(error).__name__)
    return current_beam_context, terrain, radar_config_dir, expected_dem_asset_version
