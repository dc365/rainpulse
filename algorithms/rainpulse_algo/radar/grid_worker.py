from __future__ import annotations

import os
from pathlib import Path
from uuid import NAMESPACE_URL, uuid5

import numpy as np
import yaml
from minio import Minio

from rainpulse_algo.grid import load_grid_config
from rainpulse_algo.worker.domain_contracts import RadarGridRequested
from rainpulse_algo.worker.object_store import (
    ArtifactObjectReader,
    minio_client_from_environment,
)
from rainpulse_algo.worker.runtime import WorkerResult

from .ancillary import load_source, sha256_file
from .config import load_radar_config
from .dem import VerifiedDEMTileStore
from .grid_profile import RadarGridConfigError, load_radar_grid_profile
from .grid_zarr import build_radar_grid_zarr_store, validate_radar_grid_zarr_store
from .hybrid import build_hybrid_scan


def execute_radar_grid(request: RadarGridRequested) -> WorkerResult:
    return _execute_radar_grid(request, minio_client_from_environment())


def _execute_radar_grid(request: RadarGridRequested, client: Minio) -> WorkerResult:
    profile = load_radar_grid_profile(_required_file("RAINPULSE_RADAR_GRID_CONFIG"))
    grid = load_grid_config(_required_file("RAINPULSE_GRID_CONFIG"))
    ancillary_source = load_source(_required_file("RAINPULSE_ANCILLARY_CONFIG"))
    radar_config_dir = _required_directory("RAINPULSE_RADAR_CONFIG_DIR")
    radar_config = load_radar_config(radar_config_dir / f"{request.payload.radar_id}.yaml")
    _validate_request_versions(request, profile, grid.grid_id, grid.config_version)
    if radar_config.radar_id != request.payload.radar_id:
        raise RadarGridConfigError("requested radar differs from the mounted radar config")
    if radar_config.ancillary.get("dem_asset_version") != profile.dem.asset_version:
        raise RadarGridConfigError("radar DEM version differs from the grid profile")

    flags = _load_flag_masks(_required_file("RAINPULSE_QC_FLAG_DEFINITIONS"))
    ancillary_root = _required_directory("RAINPULSE_ANCILLARY_ROOT")
    terrain = VerifiedDEMTileStore(
        ancillary_source,
        ancillary_root,
        expected_asset_version=profile.dem.asset_version,
        expected_config_version=profile.ancillary_config_version,
    )
    qc_objects = ArtifactObjectReader(client).load(request.payload.input_uri)
    result = build_hybrid_scan(
        qc_objects,
        radar_config=radar_config,
        grid=grid,
        profile=profile,
        terrain=terrain,
        flag_masks=flags,
        expected_scan_id=str(request.payload.scan_id),
    )
    grid_asset_id = uuid5(NAMESPACE_URL, f"rainpulse:grid-asset:{request.job_id}")
    objects = build_radar_grid_zarr_store(
        result,
        asset_id=grid_asset_id,
        qc_volume_uri=request.payload.input_uri,
        provenance={
            "run_id": str(request.run_id),
            "job_id": str(request.job_id),
            "trace_id": str(request.trace_id),
            "ancillary_manifest_sha256": sha256_file(terrain.manifest_path),
        },
    )
    validation = validate_radar_grid_zarr_store(objects)
    summary = result.summary
    return WorkerResult(
        objects=objects,
        diagnostics={"radar_grid": summary},
        metrics={
            "output_size_bytes": float(validation["size_bytes"]),
            "zarr_object_count": float(validation["object_count"]),
            "grid_cell_count": float(summary["grid_cell_count"]),
            "valid_cell_count": float(validation["valid_cell_count"]),
            "missing_cell_count": float(validation["missing_cell_count"]),
            "low_quality_cell_count": float(summary["low_quality_cell_count"]),
            "valid_coverage_ratio": float(summary["valid_coverage_ratio"]),
            "mean_quality_index": float(validation["mean_quality_index"]),
            "beam_blocked_missing_cell_count": float(
                summary["beam_blocked_missing_cell_count"]
            ),
            "operational_eligible": float(bool(validation["operational_eligible"])),
        },
    )


def _validate_request_versions(
    request: RadarGridRequested,
    profile: object,
    grid_id: str,
    grid_config_version: str,
) -> None:
    expected = (
        ("grid_id", request.payload.grid_id, grid_id),
        ("grid_config_version", request.payload.grid_config_version, grid_config_version),
        (
            "hybrid_scan_version",
            request.payload.hybrid_scan_version,
            getattr(profile, "algorithm_version"),
        ),
    )
    for name, requested, configured in expected:
        if requested != configured:
            raise RadarGridConfigError(f"requested {name} differs from mounted configuration")


def _load_flag_masks(path: Path) -> dict[str, np.uint32]:
    value = yaml.safe_load(path.read_text())
    if value.get("storage_dtype") != "uint32":
        raise RadarGridConfigError("QC flag storage dtype must be uint32")
    return {
        str(item["name"]): np.uint32(item["mask"])
        for item in value.get("flags", [])
    }


def _required_file(name: str) -> Path:
    value = os.getenv(name)
    if not value:
        raise RadarGridConfigError(f"{name} is required")
    path = Path(value).resolve(strict=True)
    if not path.is_file():
        raise RadarGridConfigError(f"{name} must identify a file")
    return path


def _required_directory(name: str) -> Path:
    value = os.getenv(name)
    if not value:
        raise RadarGridConfigError(f"{name} is required")
    path = Path(value).resolve(strict=True)
    if not path.is_dir():
        raise RadarGridConfigError(f"{name} must identify a directory")
    return path
