from __future__ import annotations

import os
from pathlib import Path
from uuid import NAMESPACE_URL, uuid5

import numpy as np
import yaml
from minio import Minio

from rainpulse_algo.grid import load_grid_config
from rainpulse_algo.worker.domain_contracts import AnalysisMosaicRequestedV2
from rainpulse_algo.worker.object_store import (
    ArtifactObjectReader,
    minio_client_from_environment,
)
from rainpulse_algo.worker.runtime import WorkerResult

from .mosaic import RadarMosaicInput, RadarMosaicInputError, build_radar_mosaic
from .mosaic_profile import load_radar_mosaic_profile
from .mosaic_zarr import (
    build_radar_mosaic_zarr_store,
    validate_radar_mosaic_zarr_store,
)


def execute_radar_mosaic(request: AnalysisMosaicRequestedV2) -> WorkerResult:
    return _execute_radar_mosaic(request, minio_client_from_environment())


def _execute_radar_mosaic(
    request: AnalysisMosaicRequestedV2, client: Minio
) -> WorkerResult:
    profile = load_radar_mosaic_profile(
        _required_file("RAINPULSE_RADAR_MOSAIC_CONFIG")
    )
    grid = load_grid_config(_required_file("RAINPULSE_GRID_CONFIG"))
    _validate_request_versions(request, profile, grid.grid_id, grid.config_version)
    flag_masks = _load_flag_masks(_required_file("RAINPULSE_QC_FLAG_DEFINITIONS"))
    reader = ArtifactObjectReader(client)
    inputs = tuple(
        RadarMosaicInput(
            radar_id=item.radar_id,
            scan_id=str(item.scan_id),
            grid_uri=item.grid_uri,
            time_offset_seconds=item.time_offset_seconds,
            hybrid_scan_version=item.hybrid_scan_version,
            objects=reader.load(item.grid_uri),
        )
        for item in request.payload.inputs
    )
    result = build_radar_mosaic(
        inputs,
        analysis_time=request.payload.analysis_time,
        grid=grid,
        profile=profile,
        flag_masks=flag_masks,
    )
    asset_id = uuid5(NAMESPACE_URL, f"rainpulse:mosaic-asset:{request.job_id}")
    objects = build_radar_mosaic_zarr_store(
        result,
        asset_id=asset_id,
        provenance={
            "analysis_id": str(request.payload.analysis_id),
            "run_id": str(request.run_id),
            "job_id": str(request.job_id),
            "trace_id": str(request.trace_id),
        },
    )
    validation = validate_radar_mosaic_zarr_store(objects)
    summary = result.summary
    return WorkerResult(
        objects=objects,
        diagnostics={"radar_mosaic": summary},
        metrics={
            "output_size_bytes": float(validation["size_bytes"]),
            "zarr_object_count": float(validation["object_count"]),
            "grid_cell_count": float(summary["grid_cell_count"]),
            "valid_cell_count": float(validation["valid_cell_count"]),
            "missing_cell_count": float(validation["missing_cell_count"]),
            "blended_cell_count": float(validation["blended_cell_count"]),
            "valid_coverage_ratio": float(summary["valid_coverage_ratio"]),
            "mean_quality_index": float(validation["mean_quality_index"]),
            "actual_contributing_radar_count": float(
                summary["actual_contributing_radar_count"]
            ),
            "operational_eligible": float(bool(validation["operational_eligible"])),
        },
    )


def _validate_request_versions(
    request: AnalysisMosaicRequestedV2,
    profile: object,
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
            "mosaic_config_version",
            request.payload.mosaic_config_version,
            getattr(profile, "profile_version"),
        ),
        (
            "mosaic_algorithm_version",
            request.payload.mosaic_algorithm_version,
            getattr(profile, "algorithm_version"),
        ),
        (
            "flag_definition_version",
            request.payload.flag_definition_version,
            getattr(profile, "flag_definition_version"),
        ),
    )
    for name, requested, configured in expected:
        if requested != configured:
            raise RadarMosaicInputError(
                f"requested {name} differs from mounted configuration"
            )


def _load_flag_masks(path: Path) -> dict[str, np.uint32]:
    value = yaml.safe_load(path.read_text())
    if value.get("storage_dtype") != "uint32":
        raise RadarMosaicInputError("QC flag storage dtype must be uint32")
    return {
        str(item["name"]): np.uint32(item["mask"])
        for item in value.get("flags", [])
    }


def _required_file(name: str) -> Path:
    value = os.getenv(name)
    if not value:
        raise RadarMosaicInputError(f"{name} is required")
    path = Path(value).resolve(strict=True)
    if not path.is_file():
        raise RadarMosaicInputError(f"{name} must identify a file")
    return path
