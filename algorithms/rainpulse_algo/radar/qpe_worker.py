from __future__ import annotations

import json
import os
from pathlib import Path
from uuid import NAMESPACE_URL, uuid5

import zarr
from minio import Minio
from zarr.storage import MemoryStore

from rainpulse_algo.grid import load_grid_config
from rainpulse_algo.worker.domain_contracts import AnalysisQPERequestedV1
from rainpulse_algo.worker.object_store import (
    ArtifactObjectReader,
    minio_client_from_environment,
)
from rainpulse_algo.worker.runtime import WorkerResult

from .analysis_zarr import (
    build_radar_analysis_zarr_store,
    validate_radar_analysis_zarr_store,
)
from .qpe import QPEInputError
from .qpe_profile import QPEProfile, load_qpe_profile


def execute_analysis_qpe(request: AnalysisQPERequestedV1) -> WorkerResult:
    return _execute_analysis_qpe(request, minio_client_from_environment())


def _execute_analysis_qpe(
    request: AnalysisQPERequestedV1,
    client: Minio,
) -> WorkerResult:
    profile = load_qpe_profile(_required_file("RAINPULSE_QPE_CONFIG"))
    grid = load_grid_config(_required_file("RAINPULSE_GRID_CONFIG"))
    _validate_request_versions(request, profile, grid.grid_id, grid.config_version)
    reader = ArtifactObjectReader(client)
    mosaic_objects = reader.load(request.payload.input_uri)
    _validate_mosaic_request_identity(request, mosaic_objects)
    asset_id = uuid5(NAMESPACE_URL, f"rainpulse:analysis-asset:{request.job_id}")
    objects = build_radar_analysis_zarr_store(
        mosaic_objects,
        mosaic_uri=request.payload.input_uri,
        analysis_id=request.payload.analysis_id,
        profile=profile,
        asset_id=asset_id,
        provenance={
            "run_id": str(request.run_id),
            "job_id": str(request.job_id),
            "trace_id": str(request.trace_id),
        },
    )
    validation = validate_radar_analysis_zarr_store(objects)
    summary = json.loads(objects["qpe/summary.json"])
    return WorkerResult(
        objects=objects,
        diagnostics={"analysis_qpe": summary},
        metrics={
            "output_size_bytes": float(validation["size_bytes"]),
            "zarr_object_count": float(validation["object_count"]),
            "grid_cell_count": float(summary["grid_cell_count"]),
            "valid_cell_count": float(validation["valid_cell_count"]),
            "missing_cell_count": float(validation["missing_cell_count"]),
            "rain_cell_count": float(validation["rain_cell_count"]),
            "no_rain_cell_count": float(validation["no_rain_cell_count"]),
            "capped_cell_count": float(validation["capped_cell_count"]),
            "valid_coverage_ratio": float(summary["valid_coverage_ratio"]),
            "mean_quality_index": float(summary["mean_quality_index"]),
            "mean_rate_mm_h": float(validation["mean_rate_mm_h"]),
            "maximum_observed_rate_mm_h": float(
                validation["maximum_observed_rate_mm_h"]
            ),
            "operational_eligible": float(bool(validation["operational_eligible"])),
        },
    )


def _validate_request_versions(
    request: AnalysisQPERequestedV1,
    profile: QPEProfile,
    grid_id: str,
    grid_config_version: str,
) -> None:
    expected = (
        ("grid_id", request.payload.grid_id, grid_id),
        (
            "grid_config_version",
            request.payload.grid_config_version,
            grid_config_version,
        ),
        (
            "qpe_config_version",
            request.payload.qpe_config_version,
            profile.profile_version,
        ),
        (
            "qpe_algorithm_version",
            request.payload.qpe_algorithm_version,
            profile.algorithm_version,
        ),
        (
            "flag_definition_version",
            request.payload.flag_definition_version,
            profile.flag_definition_version,
        ),
    )
    for name, requested, configured in expected:
        if requested != configured:
            raise QPEInputError(f"requested {name} differs from mounted configuration")


def _required_file(name: str) -> Path:
    value = os.getenv(name)
    if not value:
        raise QPEInputError(f"{name} is required")
    path = Path(value).resolve(strict=True)
    if not path.is_file():
        raise QPEInputError(f"{name} must identify a file")
    return path


def _validate_mosaic_request_identity(
    request: AnalysisQPERequestedV1,
    objects: dict[str, bytes],
) -> None:
    store = MemoryStore()
    store.update(objects)
    root = zarr.open_group(store=store, mode="r")
    expected = (
        ("analysis_id", str(root.attrs.get("analysis_id")), str(request.payload.analysis_id)),
        (
            "analysis_time",
            str(root.attrs.get("analysis_time")),
            request.payload.analysis_time.isoformat(),
        ),
        (
            "mosaic_config_version",
            str(root.attrs.get("profile_version")),
            request.payload.mosaic_config_version,
        ),
        (
            "mosaic_algorithm_version",
            str(root.attrs.get("mosaic_algorithm_version")),
            request.payload.mosaic_algorithm_version,
        ),
        (
            "flag_definition_version",
            str(root.attrs.get("flag_definition_version")),
            request.payload.flag_definition_version,
        ),
    )
    for name, actual, requested in expected:
        if actual != requested:
            raise QPEInputError(f"RadarMosaic {name} differs from the QPE request")
