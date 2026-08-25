from __future__ import annotations

import json
import os
import time
from pathlib import Path

import zarr
from minio import Minio
from zarr.storage import MemoryStore

from rainpulse_algo.grid import RegularLatLonGrid, load_grid_config
from rainpulse_algo.worker.domain_contracts import PystepsLKRequested
from rainpulse_algo.worker.object_store import (
    ArtifactObjectReader,
    minio_client_from_environment,
)
from rainpulse_algo.worker.runtime import WorkerResult

from .forecast_zarr import (
    build_forecast_output_zarr_store,
    validate_forecast_output_zarr_store,
)
from .pysteps_lk import PystepsLKInputError, run_pysteps_lk
from .pysteps_profile import PystepsLKProfile, load_pysteps_lk_profile


def execute_pysteps_lk(request: PystepsLKRequested) -> WorkerResult:
    return _execute_pysteps_lk(request, minio_client_from_environment())


def _execute_pysteps_lk(
    request: PystepsLKRequested,
    client: Minio,
) -> WorkerResult:
    profile = load_pysteps_lk_profile(_required_file("RAINPULSE_PYSTEPS_LK_CONFIG"))
    grid = load_grid_config(_required_file("RAINPULSE_GRID_CONFIG"))
    _validate_request(request, profile, grid)
    input_objects = ArtifactObjectReader(client).load(request.payload.input_uri)
    _validate_input_identity(input_objects, request)

    started = time.perf_counter()
    result = run_pysteps_lk(input_objects, profile=profile, grid=grid)
    runtime_ms = max(0, round((time.perf_counter() - started) * 1000))
    objects = build_forecast_output_zarr_store(
        result,
        run_id=request.run_id,
        job_id=request.job_id,
        issue_time=request.payload.issue_time,
        input_uri=request.payload.input_uri,
        input_asset_ids=request.payload.input_asset_ids,
        profile=profile,
        grid=grid,
        runtime_ms=runtime_ms,
    )
    validation = validate_forecast_output_zarr_store(objects)
    summary = json.loads(objects["forecast/summary.json"])
    return WorkerResult(
        objects=objects,
        diagnostics={"pysteps_lk": summary},
        metrics={
            "output_size_bytes": float(validation["size_bytes"]),
            "zarr_object_count": float(validation["object_count"]),
            "lead_count": float(validation["lead_count"]),
            "first_lead_valid_coverage_ratio": float(validation["first_lead_valid_coverage_ratio"]),
            "last_lead_valid_coverage_ratio": float(validation["last_lead_valid_coverage_ratio"]),
            "maximum_forecast_rate_mm_h": float(validation["maximum_forecast_rate_mm_h"]),
            "motion_fallback_used": float(validation["motion_fallback_used"]),
            "model_runtime_ms": float(runtime_ms),
        },
    )


def _validate_request(
    request: PystepsLKRequested,
    profile: PystepsLKProfile,
    grid: RegularLatLonGrid,
) -> None:
    expected = (
        ("grid_id", request.payload.grid_id, grid.grid_id),
        ("grid_id", request.payload.grid_id, profile.grid_id),
        ("grid_config_version", grid.config_version, profile.grid_config_version),
        ("model_id", request.payload.model_id, profile.model_id),
        ("model_version", request.payload.model_version, profile.model_version),
        ("config_version", request.payload.config_version, profile.profile_version),
        (
            "forecast_contract_version",
            request.payload.forecast_contract_version,
            profile.forecast_output_contract_version,
        ),
        ("baseline_models", request.payload.baseline_models, profile.extrapolation.baselines),
    )
    for name, requested, configured in expected:
        if requested != configured:
            raise PystepsLKInputError(f"requested {name} differs from mounted configuration")
    if request.payload.issue_time.utcoffset() is None:
        raise PystepsLKInputError("forecast issue time must include a UTC offset")
    if request.payload.issue_time.timestamp() % (profile.sequence.timestep_minutes * 60):
        raise PystepsLKInputError("forecast issue time is not on a five-minute UTC boundary")


def _validate_input_identity(
    objects: dict[str, bytes],
    request: PystepsLKRequested,
) -> None:
    store = MemoryStore()
    store.update(objects)
    root = zarr.open_group(store=store, mode="r")
    expected_assets = [str(value) for value in request.payload.input_asset_ids]
    if root.attrs.get("input_asset_ids") != expected_assets:
        raise PystepsLKInputError("requested input asset IDs differ from NowcastInput")
    if root.attrs.get("issue_time_utc") != request.payload.issue_time.isoformat():
        raise PystepsLKInputError("requested issue time differs from NowcastInput")


def _required_file(name: str) -> Path:
    value = os.getenv(name)
    if not value:
        raise PystepsLKInputError(f"{name} is required")
    path = Path(value).resolve(strict=True)
    if not path.is_file():
        raise PystepsLKInputError(f"{name} must identify a file")
    return path
