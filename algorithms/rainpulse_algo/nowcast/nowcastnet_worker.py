from __future__ import annotations

import json
import os
import time
from dataclasses import dataclass
from functools import lru_cache
from pathlib import Path
from typing import Any

from minio import Minio

from rainpulse_algo.worker.domain_contracts import NowcastNetOfflineRequested
from rainpulse_algo.worker.object_store import ArtifactObjectReader, minio_client_from_environment
from rainpulse_algo.worker.runtime import WorkerResult

from .nowcastnet_adapter import NowcastNetBackend, NowcastNetInputError, run_nowcastnet_fields
from .nowcastnet_official_backend import OfficialNowcastNetBackend
from .nowcastnet_offline_zarr import (
    build_nowcastnet_offline_output_zarr_store,
    load_nowcastnet_offline_input,
    validate_nowcastnet_offline_output,
)
from .nowcastnet_profile import NowcastNetProfile, load_nowcastnet_profile


@dataclass(frozen=True)
class LoadedNowcastNetRuntime:
    profile: NowcastNetProfile
    backend: OfficialNowcastNetBackend


def execute_nowcastnet_offline(request: NowcastNetOfflineRequested) -> WorkerResult:
    runtime = _load_runtime(
        str(_required_file("RAINPULSE_NOWCASTNET_CONFIG")),
        str(_required_directory("RAINPULSE_NOWCASTNET_CAPSULE_ROOT")),
        os.getenv("RAINPULSE_NOWCASTNET_DEVICE", "cuda:0"),
    )
    return _execute_nowcastnet_offline(
        request,
        minio_client_from_environment(),
        profile=runtime.profile,
        backend=runtime.backend,
        runtime_info=runtime.backend.runtime_info(),
    )


@lru_cache(maxsize=1)
def _load_runtime(
    profile_path: str,
    capsule_root: str,
    device: str,
) -> LoadedNowcastNetRuntime:
    profile = load_nowcastnet_profile(profile_path)
    profile.require_offline_ready()
    backend = OfficialNowcastNetBackend(
        capsule_root,
        profile=profile,
        device=device,
    )
    return LoadedNowcastNetRuntime(profile=profile, backend=backend)


def _execute_nowcastnet_offline(
    request: NowcastNetOfflineRequested,
    client: Minio,
    *,
    profile: NowcastNetProfile,
    backend: NowcastNetBackend,
    runtime_info: dict[str, Any],
) -> WorkerResult:
    profile.require_offline_ready()
    _validate_request(request, profile)
    input_objects = ArtifactObjectReader(client).load(request.payload.input_uri)
    fields = load_nowcastnet_offline_input(input_objects, profile=profile)
    _validate_input_identity(request, fields)

    started = time.perf_counter()
    result = run_nowcastnet_fields(
        fields.rain_rate_mm_h,
        fields.valid_mask,
        profile=profile,
        backend=backend,
        random_seed=request.payload.random_seed,
    )
    runtime_ms = max(0, round((time.perf_counter() - started) * 1000))
    objects = build_nowcastnet_offline_output_zarr_store(
        result,
        run_id=request.run_id,
        job_id=request.job_id,
        issue_time=request.payload.issue_time,
        input_uri=request.payload.input_uri,
        input_asset_ids=request.payload.input_asset_ids,
        grid_id=request.payload.grid_id,
        latitude=fields.latitude,
        longitude=fields.longitude,
        source_group=fields.source_group,
        profile=profile,
        runtime_ms=runtime_ms,
        runtime_info=runtime_info,
    )
    validation = validate_nowcastnet_offline_output(objects, profile=profile)
    summary = json.loads(objects["forecast/summary.json"])
    return WorkerResult(
        objects=objects,
        diagnostics={"nowcastnet_offline": summary, "runtime": runtime_info},
        metrics={
            "output_size_bytes": float(validation["size_bytes"]),
            "zarr_object_count": float(validation["object_count"]),
            "member_count": float(validation["member_count"]),
            "lead_count": float(validation["lead_count"]),
            "common_valid_coverage_ratio": float(
                validation["common_valid_coverage_ratio"]
            ),
            "maximum_forecast_rate_mm_h": float(
                validation["maximum_forecast_rate_mm_h"]
            ),
            "clipped_input_pixel_count": float(result.clipped_input_pixel_count),
            "clipped_negative_output_pixel_count": float(
                result.clipped_negative_output_pixel_count
            ),
            "model_runtime_ms": float(runtime_ms),
            "operational_eligible": 0.0,
            "product_publication_enabled": 0.0,
        },
    )


def _validate_request(
    request: NowcastNetOfflineRequested,
    profile: NowcastNetProfile,
) -> None:
    expected = (
        ("model_id", request.payload.model_id, profile.model_id),
        ("model_version", request.payload.model_version, profile.model_version),
        ("config_version", request.payload.config_version, profile.profile_version),
        ("input_contract_version", request.payload.input_contract_version, "1.0"),
        ("output_contract_version", request.payload.output_contract_version, "1.0"),
    )
    for name, requested, configured in expected:
        if requested != configured:
            raise NowcastNetInputError(
                f"requested {name} differs from the mounted NowcastNet configuration"
            )
    if request.payload.issue_time.utcoffset() is None:
        raise NowcastNetInputError("NowcastNet issue time must include a UTC offset")
    if request.payload.issue_time.timestamp() % (profile.protocol.timestep_minutes * 60):
        raise NowcastNetInputError("NowcastNet issue time is not on a ten-minute boundary")


def _validate_input_identity(request: NowcastNetOfflineRequested, fields: Any) -> None:
    if fields.issue_time != request.payload.issue_time:
        raise NowcastNetInputError("requested issue time differs from NowcastNet offline input")
    if fields.grid_id != request.payload.grid_id:
        raise NowcastNetInputError("requested grid ID differs from NowcastNet offline input")
    if list(fields.input_asset_ids) != request.payload.input_asset_ids:
        raise NowcastNetInputError(
            "requested input asset IDs differ from NowcastNet offline input"
        )


def _required_file(name: str) -> Path:
    value = os.getenv(name)
    if not value:
        raise NowcastNetInputError(f"{name} is required")
    path = Path(value).resolve(strict=True)
    if not path.is_file():
        raise NowcastNetInputError(f"{name} must identify a file")
    return path


def _required_directory(name: str) -> Path:
    value = os.getenv(name)
    if not value:
        raise NowcastNetInputError(f"{name} is required")
    path = Path(value).resolve(strict=True)
    if not path.is_dir():
        raise NowcastNetInputError(f"{name} must identify a directory")
    return path
