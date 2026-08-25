from __future__ import annotations

import json
import os
from pathlib import Path
from uuid import NAMESPACE_URL, uuid5

from minio import Minio

from rainpulse_algo.grid import load_grid_config
from rainpulse_algo.worker.domain_contracts import NowcastInputRequested
from rainpulse_algo.worker.object_store import (
    ArtifactObjectReader,
    minio_client_from_environment,
)
from rainpulse_algo.worker.runtime import WorkerResult

from .input_profile import NowcastInputProfile, load_nowcast_input_profile
from .input_zarr import (
    NowcastInputError,
    build_nowcast_input_zarr_store,
    validate_nowcast_input_zarr_store,
)


def execute_nowcast_input(request: NowcastInputRequested) -> WorkerResult:
    return _execute_nowcast_input(request, minio_client_from_environment())


def _execute_nowcast_input(
    request: NowcastInputRequested,
    client: Minio,
) -> WorkerResult:
    profile = load_nowcast_input_profile(
        _required_file("RAINPULSE_NOWCAST_INPUT_CONFIG")
    )
    grid = load_grid_config(_required_file("RAINPULSE_GRID_CONFIG"))
    _validate_request(request, profile, grid.grid_id, grid.config_version)
    reader = ArtifactObjectReader(client)
    frames = [reader.load(uri) for uri in request.payload.input_uris]
    asset_id = uuid5(NAMESPACE_URL, f"rainpulse:nowcast-input-asset:{request.job_id}")
    objects = build_nowcast_input_zarr_store(
        frames,
        analysis_ids=request.payload.analysis_ids,
        input_uris=request.payload.input_uris,
        issue_time=request.payload.issue_time,
        profile=profile,
        grid=grid,
        asset_id=asset_id,
        provenance={
            "run_id": str(request.run_id),
            "job_id": str(request.job_id),
            "trace_id": str(request.trace_id),
        },
    )
    validation = validate_nowcast_input_zarr_store(objects)
    summary = json.loads(objects["input/summary.json"])
    return WorkerResult(
        objects=objects,
        diagnostics={"nowcast_input": summary},
        metrics={
            "output_size_bytes": float(validation["size_bytes"]),
            "zarr_object_count": float(validation["object_count"]),
            "frame_count": float(validation["frame_count"]),
            "valid_cell_count": float(validation["valid_cell_count"]),
            "missing_cell_count": float(validation["missing_cell_count"]),
            "low_quality_cell_count": float(validation["low_quality_cell_count"]),
            "valid_coverage_ratio": float(validation["valid_coverage_ratio"]),
            "mean_quality_index": float(validation["mean_quality_index"]),
            "max_data_age_minutes": float(validation["max_data_age_minutes"]),
            "operational_eligible": 1.0,
        },
    )


def _validate_request(
    request: NowcastInputRequested,
    profile: NowcastInputProfile,
    grid_id: str,
    grid_config_version: str,
) -> None:
    expected = (
        ("grid_id", request.payload.grid_id, grid_id),
        ("grid_id", request.payload.grid_id, profile.grid_id),
        ("grid_config_version", grid_config_version, profile.grid_config_version),
        (
            "preprocess_version",
            request.payload.preprocess_version,
            profile.builder_version,
        ),
        (
            "gate_config_version",
            request.payload.gate_config_version,
            profile.profile_version,
        ),
    )
    for name, requested, configured in expected:
        if requested != configured:
            raise NowcastInputError(
                f"requested {name} differs from mounted configuration"
            )
    if request.payload.issue_time.timestamp() % (
        profile.sequence.timestep_minutes * 60
    ):
        raise NowcastInputError("issue time is not on a five-minute UTC boundary")


def _required_file(name: str) -> Path:
    value = os.getenv(name)
    if not value:
        raise NowcastInputError(f"{name} is required")
    path = Path(value).resolve(strict=True)
    if not path.is_file():
        raise NowcastInputError(f"{name} must identify a file")
    return path
