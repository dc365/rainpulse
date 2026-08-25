from __future__ import annotations

import json
import os
from pathlib import Path

import zarr
from minio import Minio
from zarr.storage import MemoryStore

from rainpulse_algo.grid import RegularLatLonGrid, load_grid_config
from rainpulse_algo.worker.domain_contracts import ProductBuildRequested
from rainpulse_algo.worker.object_store import (
    ArtifactObjectReader,
    artifact_sha256,
    minio_client_from_environment,
)
from rainpulse_algo.worker.runtime import WorkerResult

from .builder import (
    ProductBuildInputError,
    build_application_product_bundle,
    validate_application_product_bundle,
)
from .profile import ProductBuilderProfile, load_product_builder_profile


def execute_product_build(request: ProductBuildRequested) -> WorkerResult:
    return _execute_product_build(request, minio_client_from_environment())


def _execute_product_build(
    request: ProductBuildRequested,
    client: Minio,
) -> WorkerResult:
    profile = load_product_builder_profile(_required_file("RAINPULSE_PRODUCT_CONFIG"))
    grid = load_grid_config(_required_file("RAINPULSE_GRID_CONFIG"))
    _validate_request(request, profile, grid)
    forecast_objects = ArtifactObjectReader(client).load(request.payload.input_uri)
    if artifact_sha256(forecast_objects) != request.payload.input_sha256:
        raise ProductBuildInputError("requested ForecastOutput SHA-256 differs from artifact")
    _validate_forecast_request_identity(forecast_objects, request)
    product_ids = {
        "rain_rate": request.payload.product_ids.rain_rate,
        "accumulation_60": request.payload.product_ids.accumulation_60,
        "accumulation_120": request.payload.product_ids.accumulation_120,
    }
    objects = build_application_product_bundle(
        forecast_objects,
        source_forecast_uri=request.payload.input_uri,
        source_forecast_sha256=request.payload.input_sha256,
        run_id=request.run_id,
        job_id=request.job_id,
        model_run_id=request.payload.model_run_id,
        product_ids=product_ids,
        profile=profile,
        grid=grid,
    )
    validation = validate_application_product_bundle(objects)
    manifest = json.loads(objects["manifest.json"])
    return WorkerResult(
        objects=objects,
        diagnostics={"product_bundle": manifest},
        metrics={
            "output_size_bytes": float(validation["size_bytes"]),
            "object_count": float(validation["object_count"]),
            "product_count": float(validation["product_count"]),
            "asset_count": float(validation["asset_count"]),
            "rain_rate_lead_count": 24.0,
        },
    )


def _validate_request(
    request: ProductBuildRequested,
    profile: ProductBuilderProfile,
    grid: RegularLatLonGrid,
) -> None:
    expected = (
        ("grid_id", request.payload.grid_id, grid.grid_id),
        ("grid_id", request.payload.grid_id, profile.grid_id),
        ("grid_config_version", grid.config_version, profile.grid_config_version),
        (
            "product_config_version",
            request.payload.product_config_version,
            profile.profile_version,
        ),
        (
            "product_bundle_contract_version",
            request.payload.product_bundle_contract_version,
            profile.bundle_contract_version,
        ),
    )
    for name, requested, configured in expected:
        if requested != configured:
            raise ProductBuildInputError(f"requested {name} differs from mounted configuration")
    if request.payload.model_run_id.int == 0:
        raise ProductBuildInputError("product build model-run ID cannot be nil")
    issue_time = request.payload.issue_time
    if issue_time.utcoffset() is None or issue_time.timestamp() % 300:
        raise ProductBuildInputError("product issue time is not on a five-minute UTC boundary")


def _validate_forecast_request_identity(
    objects: dict[str, bytes],
    request: ProductBuildRequested,
) -> None:
    store = MemoryStore()
    store.update(objects)
    root = zarr.open_group(store=store, mode="r")
    expected = (
        (root.attrs.get("run_id"), str(request.run_id)),
        (root.attrs.get("issue_time"), request.payload.issue_time.isoformat()),
        (root.attrs.get("grid_id"), request.payload.grid_id),
        (root.attrs.get("model_id"), request.payload.model_id),
        (root.attrs.get("model_version"), request.payload.model_version),
        (root.attrs.get("config_version"), request.payload.model_config_version),
    )
    if any(actual != wanted for actual, wanted in expected):
        raise ProductBuildInputError("product request differs from committed ForecastOutput")


def _required_file(name: str) -> Path:
    value = os.getenv(name)
    if not value:
        raise ProductBuildInputError(f"{name} is required")
    path = Path(value).resolve(strict=True)
    if not path.is_file():
        raise ProductBuildInputError(f"{name} must identify a file")
    return path
