from __future__ import annotations

import io
import os
from pathlib import Path
from urllib.parse import unquote, urlparse
from uuid import NAMESPACE_URL, uuid5

import numpy as np
from minio import Minio

from rainpulse_algo.worker.domain_contracts import RadarQCRequested
from rainpulse_algo.worker.object_store import (
    ArtifactObjectReader,
    minio_client_from_environment,
    parse_s3_uri,
)
from rainpulse_algo.worker.runtime import WorkerResult

from .qc import BasicQCProfile, QCConfigError, apply_basic_qc, load_qc_profile
from .qc_zarr import build_qc_zarr_store, validate_qc_zarr_store


def execute_basic_qc(request: RadarQCRequested) -> WorkerResult:
    return _execute_basic_qc(request, minio_client_from_environment())


def _execute_basic_qc(request: RadarQCRequested, client: Minio) -> WorkerResult:
    profile = load_qc_profile(
        _required_file("RAINPULSE_RADAR_QC_CONFIG"),
        _required_file("RAINPULSE_QC_FLAG_DEFINITIONS"),
    )
    _validate_request_versions(request, profile)
    normalized = ArtifactObjectReader(client).load(request.payload.input_uri)
    ancillary = _load_ancillary_maps(profile, client)
    result = apply_basic_qc(normalized, profile, ancillary_maps=ancillary)
    qc_asset_id = uuid5(NAMESPACE_URL, f"rainpulse:qc-asset:{request.job_id}")
    objects = build_qc_zarr_store(
        normalized,
        result,
        asset_id=qc_asset_id,
        normalized_volume_uri=request.payload.input_uri,
        provenance={
            "scan_id": str(request.payload.scan_id),
            "run_id": str(request.run_id),
            "job_id": str(request.job_id),
            "trace_id": str(request.trace_id),
        },
    )
    validation = validate_qc_zarr_store(objects)
    summary = result.summary
    return WorkerResult(
        objects=objects,
        diagnostics={"radar_qc": summary},
        metrics={
            "output_size_bytes": float(validation["size_bytes"]),
            "zarr_object_count": float(validation["object_count"]),
            "sweep_count": float(validation["sweep_count"]),
            "ray_count": float(validation["ray_count"]),
            "valid_gate_count": float(validation["valid_gate_count"]),
            "missing_gate_count": float(validation["missing_gate_count"]),
            "low_quality_gate_count": float(summary["low_quality_gate_count"]),
            "mean_quality_index": float(summary["mean_quality_index"]),
            "radial_interference_ray_count": float(
                summary["radial_interference_ray_count"]
            ),
            "radial_interference_gate_count": float(
                summary["radial_interference_gate_count"]
            ),
            "radial_interference_area_km2": float(
                summary["radial_interference_area_km2"]
            ),
        },
    )


def _validate_request_versions(
    request: RadarQCRequested,
    profile: BasicQCProfile,
) -> None:
    payload = request.payload
    expected = (
        ("qc_profile", payload.qc_profile, profile.profile_version),
        ("qc_pipeline_version", payload.qc_pipeline_version, profile.pipeline_version),
        (
            "flag_definition_version",
            payload.flag_definition_version,
            profile.flag_definition_version,
        ),
    )
    for name, requested, configured in expected:
        if requested != configured:
            raise QCConfigError(f"requested {name} differs from the mounted QC profile")


def _load_ancillary_maps(
    profile: BasicQCProfile,
    client: Minio,
) -> dict[str, dict[str, np.ndarray]] | None:
    values: dict[str, dict[str, np.ndarray]] = {}
    if profile.static_ground_clutter.asset_uri:
        _merge_npz(values, _load_npz(profile.static_ground_clutter.asset_uri, client))
    if profile.sea_ap.coastline_asset_uri:
        _merge_npz(values, _load_npz(profile.sea_ap.coastline_asset_uri, client))
    return values or None


def _load_npz(uri: str, client: Minio) -> dict[str, np.ndarray]:
    parsed = urlparse(uri)
    if parsed.scheme == "s3":
        bucket, key = parse_s3_uri(uri)
        response = client.get_object(bucket, key)
        try:
            data = response.read()
        finally:
            response.close()
            response.release_conn()
    elif parsed.scheme == "file":
        path = Path(unquote(parsed.path)).resolve(strict=True)
        roots = _required_roots("RAINPULSE_QC_ASSET_ROOTS")
        if not any(path == root or root in path.parents for root in roots):
            raise QCConfigError("QC ancillary file is outside the configured roots")
        data = path.read_bytes()
    else:
        raise QCConfigError(f"unsupported QC ancillary URI {uri!r}")
    with np.load(io.BytesIO(data), allow_pickle=False) as archive:
        return {name: archive[name].astype("float32") for name in archive.files}


def _merge_npz(
    target: dict[str, dict[str, np.ndarray]],
    arrays: dict[str, np.ndarray],
) -> None:
    for key, values in arrays.items():
        sweep, separator, field = key.partition("__")
        if not separator or not sweep.startswith("sweep_") or field not in {
            "ground_clutter",
            "sea_clutter",
            "ap",
        }:
            raise QCConfigError(f"invalid QC ancillary array key {key!r}")
        target.setdefault(sweep, {})[field] = values


def _required_file(name: str) -> Path:
    value = os.getenv(name)
    if not value:
        raise QCConfigError(f"{name} is required")
    path = Path(value).resolve(strict=True)
    if not path.is_file():
        raise QCConfigError(f"{name} must identify a file")
    return path


def _required_roots(name: str) -> tuple[Path, ...]:
    value = os.getenv(name)
    if not value:
        raise QCConfigError(f"{name} is required for file ancillary assets")
    return tuple(Path(item).resolve(strict=True) for item in value.split(os.pathsep) if item)
