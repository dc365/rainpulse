from __future__ import annotations

import os
import tempfile
from collections.abc import Iterator
from contextlib import contextmanager
from pathlib import Path, PurePosixPath
from urllib.parse import unquote, urlparse

from rainpulse_algo.worker.domain_contracts import RadarDecodeRequested
from rainpulse_algo.worker.object_store import minio_client_from_environment
from rainpulse_algo.worker.runtime import WorkerResult

from .config import RadarConfigError, load_radar_config
from .fmt import DECODER_VERSION, decode_fmt_volume
from .health import assess_volume_health, load_radar_health_config
from .zarr_volume import build_zarr_store


def execute_fmt_decode(request: RadarDecodeRequested) -> WorkerResult:
    if request.payload.decoder_version != DECODER_VERSION:
        raise RadarConfigError(
            f"requested decoder {request.payload.decoder_version!r} is not {DECODER_VERSION!r}"
        )
    config_dir = _required_directory("RAINPULSE_RADAR_CONFIG_DIR")
    config = load_radar_config(config_dir / f"{request.payload.radar_id}.yaml")
    health_config = load_radar_health_config(
        _required_file("RAINPULSE_RADAR_HEALTH_CONFIG")
    )
    if config.radar_id != request.payload.radar_id:
        raise RadarConfigError("request radar_id differs from decoder configuration")
    if config.config_version != request.payload.radar_config_version:
        raise RadarConfigError("request radar_config_version differs from decoder configuration")
    if config.source["format"] != request.payload.source_format:
        raise RadarConfigError("request source_format differs from decoder configuration")

    with _materialized_input(request.payload.input_uri, request.payload.radar_id) as input_path:
        volume = decode_fmt_volume(input_path, config)
        if volume.input_sha256 != request.payload.input_sha256:
            raise ValueError("raw radar archive SHA-256 differs from the registered asset")
        if volume.input_size_bytes != request.payload.input_size_bytes:
            raise ValueError("raw radar archive size differs from the registered asset")
        health = assess_volume_health(volume, config, health_config)
        objects = build_zarr_store(
            volume,
            config,
            asset_id=request.payload.asset_id,
            source_uri=request.payload.input_uri,
            health=health,
            provenance={
                "scan_id": str(request.payload.scan_id),
                "run_id": str(request.run_id),
                "job_id": str(request.job_id),
                "trace_id": str(request.trace_id),
            },
        )
    return WorkerResult(
        objects=objects,
        diagnostics={"radar_health": health.value},
        metrics=health.metrics
        | {
            "input_size_bytes": float(volume.input_size_bytes),
            "output_size_bytes": float(sum(len(value) for value in objects.values())),
            "zarr_object_count": float(len(objects)),
            "sweep_count": float(len(volume.sweeps)),
            "ray_count": float(volume.ray_count),
            "field_count": float(len(volume.canonical_fields)),
            "decode_warning_count": float(len(volume.warnings)),
        },
    )


def _allowed_input_path(uri: str) -> Path:
    parsed = urlparse(uri)
    if parsed.scheme != "file" or parsed.netloc not in {"", "localhost"}:
        raise ValueError("RP-006 decoder accepts only local file:// input URIs")
    path = Path(unquote(parsed.path)).resolve(strict=True)
    roots_value = os.getenv("RAINPULSE_RADAR_INPUT_ROOTS")
    if not roots_value:
        raise ValueError("RAINPULSE_RADAR_INPUT_ROOTS is required")
    roots = [Path(item).resolve(strict=True) for item in roots_value.split(os.pathsep) if item]
    if not any(path == root or root in path.parents for root in roots):
        raise ValueError("radar input path is outside the configured read-only roots")
    if not path.is_file():
        raise ValueError("radar input path is not a file")
    return path


@contextmanager
def _materialized_input(uri: str, radar_id: str) -> Iterator[Path]:
    parsed = urlparse(uri)
    if parsed.scheme == "file":
        yield _allowed_input_path(uri)
        return
    expected_bucket = os.getenv("RAINPULSE_OBJECT_STORE_BUCKET", "rainpulse")
    expected_prefix = f"radar/raw/{radar_id}/"
    key = unquote(parsed.path).lstrip("/")
    if parsed.scheme != "s3" or parsed.netloc != expected_bucket or not key.startswith(
        expected_prefix
    ):
        raise ValueError("RP-006 decoder requires a configured immutable raw-archive URI")
    suffix = "".join(PurePosixPath(key).suffixes)
    if len(suffix) > 32:
        suffix = ".bin"
    with tempfile.TemporaryDirectory(prefix="rainpulse-radar-") as directory:
        target = Path(directory) / f"volume{suffix}"
        minio_client_from_environment().fget_object(parsed.netloc, key, str(target))
        if not target.is_file():
            raise ValueError("raw radar archive download did not create a regular file")
        yield target


def _required_directory(name: str) -> Path:
    value = os.getenv(name)
    if not value:
        raise ValueError(f"{name} is required")
    path = Path(value).resolve(strict=True)
    if not path.is_dir():
        raise ValueError(f"{name} must identify a directory")
    return path


def _required_file(name: str) -> Path:
    value = os.getenv(name)
    if not value:
        raise ValueError(f"{name} is required")
    path = Path(value).resolve(strict=True)
    if not path.is_file():
        raise ValueError(f"{name} must identify a file")
    return path
