"""NATS worker implementation for the public-weight NowcastNet shadow path."""

from __future__ import annotations

import os
import time
from collections.abc import Callable, Sequence
from dataclasses import dataclass, replace
from functools import lru_cache
from pathlib import Path
from typing import Any

import numpy as np
import yaml
from minio import Minio

from rainpulse_algo.grid import RegularLatLonGrid, load_grid_config
from rainpulse_algo.products.profile import ProductBuilderProfile, load_product_builder_profile
from rainpulse_algo.worker.domain_contracts import NowcastNetShadowRequested
from rainpulse_algo.worker.object_store import ArtifactObjectReader, minio_client_from_environment
from rainpulse_algo.worker.runtime import WorkerResult

from .nowcastnet_adapter import (
    NowcastNetInputError,
    run_nowcastnet_batch_fields,
    run_nowcastnet_fields,
)
from .nowcastnet_official_backend import OfficialNowcastNetBackend
from .nowcastnet_profile import NowcastNetProfile, load_nowcastnet_profile
from .nowcastnet_shadow_products import build_nowcastnet_shadow_product_bundle
from .nowcastnet_shadow_service import AnalysisReference, ShadowProbeError, load_analysis_frame
from .nowcastnet_tile_atlas import (
    PreparedTile,
    TileAtlas,
    chunked,
    group_prepared_tiles,
    load_tile_atlas,
    prepare_atlas_tiles,
    stitch_member_tiles,
)
from .temporal_adapter import adapt_members_to_five_minutes


class NowcastNetShadowWorkerError(ValueError):
    """Raised when the formal shadow task cannot safely publish a product."""


@dataclass(frozen=True)
class ShadowTaskConfiguration:
    profile_version: str
    source_model_profile: str
    grid_id: str
    grid_config_version: str
    tile_atlas_version: str
    product_profile: str
    input_frames: int
    issue_cadence_minutes: int
    input_timestep_minutes: int
    native_output_timestep_minutes: int
    product_timestep_minutes: int
    native_output_lead_minutes: tuple[int, ...]
    gpu_batch_size: int
    batch_fallback: str
    temporal_adapter: str


@dataclass(frozen=True)
class LoadedShadowRuntime:
    task: ShadowTaskConfiguration
    parent_profile: NowcastNetProfile
    atlas: TileAtlas
    grid: RegularLatLonGrid
    product_profile: ProductBuilderProfile
    capsule_root: str
    device: str


BackendFactory = Callable[[NowcastNetProfile], Any]


def execute_nowcastnet_shadow(request: NowcastNetShadowRequested) -> WorkerResult:
    runtime = _load_runtime(
        str(_required_file("RAINPULSE_NOWCASTNET_SHADOW_TASK_CONFIG")),
        str(_required_file("RAINPULSE_NOWCASTNET_CONFIG")),
        str(_required_file("RAINPULSE_NOWCASTNET_TILE_ATLAS_CONFIG")),
        str(_required_file("RAINPULSE_GRID_CONFIG")),
        str(_required_file("RAINPULSE_NOWCASTNET_PRODUCT_CONFIG")),
        str(_required_directory("RAINPULSE_NOWCASTNET_CAPSULE_ROOT")),
        os.getenv("RAINPULSE_NOWCASTNET_DEVICE", "cuda:0"),
    )
    return _execute_nowcastnet_shadow(
        request,
        minio_client_from_environment(),
        runtime=runtime,
        backend_factory=lambda profile: OfficialNowcastNetBackend(
            runtime.capsule_root, profile=profile, device=runtime.device
        ),
    )


@lru_cache(maxsize=1)
def _load_runtime(
    task_path: str,
    model_path: str,
    atlas_path: str,
    grid_path: str,
    product_path: str,
    capsule_root: str,
    device: str,
) -> LoadedShadowRuntime:
    task = load_shadow_task_configuration(task_path)
    parent = load_nowcastnet_profile(model_path)
    parent.require_offline_ready()
    atlas = load_tile_atlas(atlas_path)
    grid = load_grid_config(Path(grid_path))
    product = load_product_builder_profile(product_path)
    if (
        task.source_model_profile != parent.profile_version
        or task.grid_id != grid.grid_id
        or task.grid_config_version != grid.config_version
        or task.tile_atlas_version != atlas.atlas_version
        or task.product_profile != product.profile_version
        or atlas.grid_id != grid.grid_id
        or atlas.grid_config_version != grid.config_version
        or product.grid_id != grid.grid_id
        or product.grid_config_version != grid.config_version
    ):
        raise NowcastNetShadowWorkerError("NowcastNet shadow lineage configuration differs")
    if (
        task.input_frames != parent.protocol.input_frames
        or task.input_timestep_minutes != parent.protocol.timestep_minutes
        or task.native_output_timestep_minutes != parent.protocol.timestep_minutes
        or task.native_output_lead_minutes != tuple(range(10, 121, 10))
    ):
        raise NowcastNetShadowWorkerError("NowcastNet shadow protocol differs from frozen parent")
    return LoadedShadowRuntime(
        task=task,
        parent_profile=parent,
        atlas=atlas,
        grid=grid,
        product_profile=product,
        capsule_root=capsule_root,
        device=device,
    )


def load_shadow_task_configuration(path: str | Path) -> ShadowTaskConfiguration:
    source = Path(path)
    try:
        raw = yaml.safe_load(source.read_text(encoding="utf-8"))
        protocol = raw["protocol"]
        execution = raw["execution"]
        configuration = ShadowTaskConfiguration(
            profile_version=str(raw["profile_version"]),
            source_model_profile=str(raw["source_model_profile"]),
            grid_id=str(raw["grid_id"]),
            grid_config_version=str(raw["grid_config_version"]),
            tile_atlas_version=str(raw["tile_atlas_version"]),
            product_profile=str(raw["product_profile"]),
            input_frames=int(protocol["input_frames"]),
            issue_cadence_minutes=int(protocol["issue_cadence_minutes"]),
            input_timestep_minutes=int(protocol["input_timestep_minutes"]),
            native_output_timestep_minutes=int(protocol["native_output_timestep_minutes"]),
            product_timestep_minutes=int(protocol["product_timestep_minutes"]),
            native_output_lead_minutes=tuple(
                int(value) for value in protocol["native_output_lead_minutes"]
            ),
            gpu_batch_size=int(execution["gpu_batch_size"]),
            batch_fallback=str(execution["batch_fallback"]),
            temporal_adapter=str(execution["temporal_adapter"]),
        )
    except (OSError, KeyError, TypeError, ValueError, yaml.YAMLError) as error:
        raise NowcastNetShadowWorkerError(
            f"invalid NowcastNet shadow task configuration {source}: {error}"
        ) from error
    if not isinstance(raw, dict) or raw.get("schema_version") != "1.0":
        raise NowcastNetShadowWorkerError("unsupported NowcastNet shadow task schema")
    if (
        not configuration.profile_version
        or configuration.source_model_profile != "rp026-nowcastnet-offline-v1"
        or configuration.input_frames != 9
        or configuration.issue_cadence_minutes != 5
        or configuration.input_timestep_minutes != 10
        or configuration.native_output_timestep_minutes != 10
        or configuration.product_timestep_minutes != 5
        or configuration.native_output_lead_minutes != tuple(range(10, 121, 10))
        or configuration.gpu_batch_size < 1
        or configuration.batch_fallback != "serial"
        or configuration.temporal_adapter
        != "bidirectional-dense-optical-flow-advection-v1"
        or raw.get("lifecycle") != "shadow"
        or raw.get("operational_eligible") is not False
    ):
        raise NowcastNetShadowWorkerError("NowcastNet shadow task configuration differs")
    return configuration


def _execute_nowcastnet_shadow(
    request: NowcastNetShadowRequested,
    client: Minio,
    *,
    runtime: LoadedShadowRuntime,
    backend_factory: BackendFactory,
) -> WorkerResult:
    _validate_request(request, runtime)
    reader = ArtifactObjectReader(client)
    references = [
        AnalysisReference(
            analysis_id=str(frame.analysis_id),
            analysis_time=frame.analysis_time,
            grid_id=request.payload.grid_id,
            analysis_uri=frame.analysis_uri,
        )
        for frame in request.payload.input_frames
    ]
    try:
        loaded = [load_analysis_frame(reader, reference) for reference in references]
    except ShadowProbeError as error:
        raise NowcastNetShadowWorkerError(f"load NowcastNet shadow inputs: {error}") from error
    rates = np.stack([item[0] for item in loaded], axis=0)
    valid = np.stack([item[1] for item in loaded], axis=0)
    started = time.perf_counter()
    native_members, native_valid, atlas_summary = run_fixed_tile_atlas(
        rates,
        valid,
        runtime=runtime,
        backend_factory=backend_factory,
        random_seed=request.payload.random_seed,
    )
    adapted = adapt_members_to_five_minutes(
        rates[-1],
        valid[-1],
        native_members,
        native_valid,
        native_leads=runtime.task.native_output_lead_minutes,
    )
    runtime_ms = max(0, round((time.perf_counter() - started) * 1000))
    info = dict(atlas_summary)
    info["runtime_ms"] = runtime_ms
    info["temporal_adapter"] = runtime.task.temporal_adapter
    info["native_frame_count"] = len(runtime.task.native_output_lead_minutes)
    info["derived_frame_count"] = sum(
        frame.frame_kind == "derived" for frame in adapted.frames
    )
    objects = build_nowcastnet_shadow_product_bundle(
        adapted,
        run_id=request.run_id,
        job_id=request.job_id,
        algorithm_run_id=request.payload.algorithm_run_id,
        issue_time=request.payload.issue_time,
        grid=runtime.grid,
        model_profile=runtime.parent_profile,
        shadow_profile_version=runtime.task.profile_version,
        atlas_version=runtime.atlas.atlas_version,
        product_profile=runtime.product_profile,
        input_analysis=[
            {
                "analysis_id": str(frame.analysis_id),
                "analysis_time": frame.analysis_time.isoformat(),
                "analysis_uri": frame.analysis_uri,
            }
            for frame in request.payload.input_frames
        ],
        runtime=info,
    )
    return WorkerResult(
        objects=objects,
        diagnostics={
            "nowcastnet_shadow": {
                "schema_version": "1.0",
                "run_id": str(request.run_id),
                "job_id": str(request.job_id),
                "algorithm_run_id": str(request.payload.algorithm_run_id),
                "issue_time": request.payload.issue_time.isoformat(),
                "grid_id": request.payload.grid_id,
                "model_id": request.payload.model_id,
                "model_version": request.payload.model_version,
                "config_version": request.payload.config_version,
                "source_model_config_version": request.payload.source_model_config_version,
                "tile_atlas_version": request.payload.tile_atlas_version,
                "input_analysis_ids": [
                    str(frame.analysis_id) for frame in request.payload.input_frames
                ],
                "input_analysis_uris": [
                    frame.analysis_uri for frame in request.payload.input_frames
                ],
                "native_lead_count": len(runtime.task.native_output_lead_minutes),
                "product_lead_count": len(adapted.frames),
                "native_output_timestep_minutes": runtime.task.native_output_timestep_minutes,
                "product_timestep_minutes": runtime.task.product_timestep_minutes,
                "lifecycle": "shadow",
                "operational_eligible": False,
                **info,
            }
        },
        metrics={
            "product_frame_count": float(len(adapted.frames)),
            "native_frame_count": float(len(runtime.task.native_output_lead_minutes)),
            "derived_frame_count": float(
                sum(frame.frame_kind == "derived" for frame in adapted.frames)
            ),
            "atlas_eligible_tile_count": float(atlas_summary["eligible_tile_count"]),
            "atlas_rejected_tile_count": float(atlas_summary["rejected_tile_count"]),
            "atlas_trusted_coverage_ratio": float(atlas_summary["trusted_coverage_ratio"]),
            "batch_fallback_count": float(atlas_summary["batch_fallback_count"]),
            "model_runtime_ms": float(runtime_ms),
            "operational_eligible": 0.0,
        },
    )


def run_fixed_tile_atlas(
    rain_rate_mm_h: np.ndarray,
    valid_mask: np.ndarray,
    *,
    runtime: LoadedShadowRuntime,
    backend_factory: BackendFactory,
    random_seed: int,
) -> tuple[np.ndarray, np.ndarray, dict[str, Any]]:
    """Infer eligible fixed tiles in batches, with exact serial fallback."""

    preparation = prepare_atlas_tiles(rain_rate_mm_h, valid_mask, runtime.atlas)
    if not preparation.eligible:
        raise NowcastNetShadowWorkerError("Tile Atlas has no fully valid input window")
    tile_forecasts: list[tuple[Any, np.ndarray]] = []
    clipped_input = 0
    clipped_negative = 0
    fallback_count = 0
    batch_sizes: list[int] = []
    runtime_info: dict[str, Any] | None = None
    for _, group in group_prepared_tiles(preparation.eligible).items():
        first = group[0]
        tile_profile = _tile_profile(
            runtime.parent_profile,
            height=first.tile.height,
            width=first.tile.width,
            shadow_profile_version=runtime.task.profile_version,
        )
        backend = backend_factory(tile_profile)
        if hasattr(backend, "runtime_info"):
            runtime_info = dict(backend.runtime_info())
        for batch in chunked(group, runtime.task.gpu_batch_size):
            try:
                batch_result = run_nowcastnet_batch_fields(
                    np.stack([item.rain_rate_mm_h for item in batch], axis=0),
                    np.stack([item.valid_mask for item in batch], axis=0),
                    profile=tile_profile,
                    backend=backend,
                    random_seed=random_seed,
                )
            except NowcastNetInputError:
                fallback_count += len(batch)
                serial, input_count, negative_count = _run_serial_batch(
                    batch,
                    profile=tile_profile,
                    backend=backend,
                    random_seed=random_seed,
                )
                tile_forecasts.extend(serial)
                clipped_input += input_count
                clipped_negative += negative_count
                continue
            batch_sizes.append(len(batch))
            clipped_input += batch_result.clipped_input_pixel_count
            clipped_negative += batch_result.clipped_negative_output_pixel_count
            for index, item in enumerate(batch):
                tile_forecasts.append(
                    (
                        item.tile,
                        batch_result.rain_rate_mm_h[
                            :, index, : len(runtime.task.native_output_lead_minutes)
                        ],
                    )
                )
    stitched, stitched_valid = stitch_member_tiles(tile_forecasts, output_shape=runtime.grid.shape)
    summary: dict[str, Any] = {
        "eligible_tile_count": len(preparation.eligible),
        "rejected_tile_count": len(preparation.rejected),
        "rejected_tiles": [
            {"tile_id": tile_id, "reason": reason}
            for tile_id, reason in preparation.rejected
        ],
        "trusted_coverage_ratio": preparation.trusted_coverage_ratio,
        "batch_sizes": batch_sizes,
        "batch_fallback_count": fallback_count,
        "batch_mode": "native_batch_with_serial_fallback",
        "clipped_input_pixel_count": clipped_input,
        "clipped_negative_output_pixel_count": clipped_negative,
    }
    if runtime_info is not None:
        summary["runtime"] = runtime_info
    return stitched, stitched_valid, summary


def _run_serial_batch(
    batch: Sequence[PreparedTile],
    *,
    profile: NowcastNetProfile,
    backend: Any,
    random_seed: int,
) -> tuple[list[tuple[Any, np.ndarray]], int, int]:
    values: list[tuple[Any, np.ndarray]] = []
    clipped_input = 0
    clipped_negative = 0
    for item in batch:
        result = run_nowcastnet_fields(
            item.rain_rate_mm_h,
            item.valid_mask,
            profile=profile,
            backend=backend,
            random_seed=random_seed,
        )
        values.append((item.tile, result.rain_rate_mm_h[:, :12]))
        clipped_input += result.clipped_input_pixel_count
        clipped_negative += result.clipped_negative_output_pixel_count
    return values, clipped_input, clipped_negative


def _tile_profile(
    parent: NowcastNetProfile,
    *,
    height: int,
    width: int,
    shadow_profile_version: str,
) -> NowcastNetProfile:
    return replace(
        parent,
        profile_version=f"{shadow_profile_version}-{height}x{width}",
        protocol=replace(parent.protocol, input_height=height, input_width=width),
    )


def _validate_request(
    request: NowcastNetShadowRequested,
    runtime: LoadedShadowRuntime,
) -> None:
    payload = request.payload
    expected = (
        ("model_id", payload.model_id, runtime.parent_profile.model_id),
        ("model_version", payload.model_version, runtime.parent_profile.model_version),
        ("config_version", payload.config_version, runtime.task.profile_version),
        (
            "source_model_config_version",
            payload.source_model_config_version,
            runtime.parent_profile.profile_version,
        ),
        ("tile_atlas_version", payload.tile_atlas_version, runtime.atlas.atlas_version),
        ("grid_id", payload.grid_id, runtime.grid.grid_id),
        (
            "issue_cadence_minutes",
            payload.issue_cadence_minutes,
            runtime.task.issue_cadence_minutes,
        ),
        (
            "input_timestep_minutes",
            payload.input_timestep_minutes,
            runtime.task.input_timestep_minutes,
        ),
        (
            "native_output_timestep_minutes",
            payload.native_output_timestep_minutes,
            runtime.task.native_output_timestep_minutes,
        ),
        (
            "product_timestep_minutes",
            payload.product_timestep_minutes,
            runtime.task.product_timestep_minutes,
        ),
    )
    for name, requested, configured in expected:
        if requested != configured:
            raise NowcastNetShadowWorkerError(
                f"requested {name} differs from mounted NowcastNet shadow configuration"
            )


def _required_file(name: str) -> Path:
    value = os.getenv(name)
    if not value:
        raise NowcastNetShadowWorkerError(f"{name} is required")
    path = Path(value).resolve(strict=True)
    if not path.is_file():
        raise NowcastNetShadowWorkerError(f"{name} must identify a file")
    return path


def _required_directory(name: str) -> Path:
    value = os.getenv(name)
    if not value:
        raise NowcastNetShadowWorkerError(f"{name} is required")
    path = Path(value).resolve(strict=True)
    if not path.is_dir():
        raise NowcastNetShadowWorkerError(f"{name} must identify a directory")
    return path
