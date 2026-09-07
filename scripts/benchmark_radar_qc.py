#!/usr/bin/env python3
from __future__ import annotations

import argparse
import hashlib
import io
import json
import resource
import sys
import time
from collections.abc import Mapping, Sequence
from datetime import UTC, datetime, timedelta
from pathlib import Path
from types import SimpleNamespace
from typing import Any
from uuid import UUID

import numpy as np
import zarr
from minio.error import S3Error
from zarr.storage import MemoryStore

REPOSITORY_ROOT = Path(__file__).resolve().parents[1]
ALGORITHMS_ROOT = REPOSITORY_ROOT / "algorithms"
if str(ALGORITHMS_ROOT) not in sys.path:
    sys.path.insert(0, str(ALGORITHMS_ROOT))

from rainpulse_algo.radar.qc import BasicQCProfile, apply_basic_qc, load_qc_profile
from rainpulse_algo.radar.qc_worker import _load_radial_context
from rainpulse_algo.radar.qc_zarr import (
    DEFAULT_QC_ZARR_LAYOUT,
    QCZarrWriteSettings,
    SUPPORTED_QC_ZARR_LAYOUTS,
    build_validated_qc_zarr_store,
)
from rainpulse_algo.worker.contracts import CompletedAsset, JobCompleted, JobCompletedPayload
from rainpulse_algo.worker.domain_contracts import RadarQCRequested
from rainpulse_algo.worker.object_store import ArtifactObjectReader, AtomicObjectPublisher, artifact_sha256

DEFAULT_PROFILE = REPOSITORY_ROOT / "configs" / "qc" / "rp047-fujian-radial-evidence-v1.yaml"
DEFAULT_FLAG_DEFINITIONS = REPOSITORY_ROOT / "configs" / "qc" / "flag-definitions.yaml"
DEFAULT_BUCKET = "rainpulse"
DEFAULT_CURRENT_PREFIX = "benchmark/current"
DEFAULT_CONTEXT_PREFIX = "benchmark/context"
DEFAULT_OUTPUT_PREFIX = "benchmark/output"
CURRENT_SCAN_ID = "10000000-0000-4000-8000-000000000004"
CURRENT_RADAR_ID = "z9598"
RADAR_CONFIG_VERSION = "benchmark-radar-config-v1"
LAYOUT_PIPELINE_IMPROVEMENT_THRESHOLD = 0.10
LAYOUT_DIAGNOSTIC_REGRESSION_LIMIT = 0.20
LAYOUT_RSS_INCREASE_LIMIT = 0.10


class MemoryObjectResponse(io.BytesIO):
    def release_conn(self) -> None:
        return None


class BenchmarkObjectStore:
    def __init__(self, objects: Mapping[tuple[str, str], bytes] | None = None) -> None:
        self._objects = dict(objects or {})
        self.get_count = 0
        self.put_count = 0
        self.stat_count = 0
        self.read_bytes = 0
        self.write_bytes = 0

    def get_object(self, bucket: str, key: str) -> MemoryObjectResponse:
        payload = self._objects.get((bucket, key))
        if payload is None:
            raise _s3_error("NoSuchKey", bucket=bucket, key=key, status=404)
        self.get_count += 1
        self.read_bytes += len(payload)
        return MemoryObjectResponse(payload)

    def put_object(
        self,
        bucket: str,
        key: str,
        body: io.BytesIO,
        length: int,
        **_: object,
    ) -> None:
        value = body.read(length)
        if len(value) != length:
            raise RuntimeError("short object write in benchmark object store")
        self._objects[(bucket, key)] = value
        self.put_count += 1
        self.write_bytes += len(value)

    def _put_object(
        self,
        bucket: str,
        key: str,
        body: bytes,
        headers: Mapping[str, str] | None = None,
    ) -> None:
        if headers and headers.get("If-None-Match") == "*" and (bucket, key) in self._objects:
            raise _s3_error("PreconditionFailed", bucket=bucket, key=key, status=412)
        self._objects[(bucket, key)] = body
        self.put_count += 1
        self.write_bytes += len(body)

    def stat_object(self, bucket: str, key: str) -> SimpleNamespace:
        payload = self._objects.get((bucket, key))
        if payload is None:
            raise _s3_error("NoSuchObject", bucket=bucket, key=key, status=404)
        self.stat_count += 1
        return SimpleNamespace(size=len(payload), bucket_name=bucket, object_name=key)


class CountingMemoryStore(MemoryStore):
    def __init__(self, objects: Mapping[str, bytes]) -> None:
        super().__init__()
        self.update({key: bytes(value) for key, value in objects.items()})
        self.get_count = 0
        self.read_bytes = 0

    def __getitem__(self, key: str) -> bytes:
        value = super().__getitem__(key)
        self.get_count += 1
        self.read_bytes += len(value)
        return value


class StageTimer:
    def __enter__(self) -> StageTimer:
        self._started = time.perf_counter()
        self.elapsed_seconds = 0.0
        return self

    def __exit__(self, exc_type: object, exc: object, tb: object) -> None:
        del exc_type, exc, tb
        self.elapsed_seconds = time.perf_counter() - self._started


class CounterSnapshot:
    def __init__(self, client: BenchmarkObjectStore) -> None:
        self.get_count = client.get_count
        self.put_count = client.put_count
        self.stat_count = client.stat_count
        self.read_bytes = client.read_bytes
        self.write_bytes = client.write_bytes

    def delta(self, client: BenchmarkObjectStore) -> dict[str, int]:
        return {
            "get_count": client.get_count - self.get_count,
            "put_count": client.put_count - self.put_count,
            "stat_count": client.stat_count - self.stat_count,
            "read_bytes": client.read_bytes - self.read_bytes,
            "write_bytes": client.write_bytes - self.write_bytes,
        }


def _parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Benchmark the radar QC pipeline on synthetic inputs.")
    parser.add_argument("--seed", type=int, default=47)
    parser.add_argument("--shape", default="360x1200", help="Synthetic sweep shape as raysxgates.")
    parser.add_argument("--profile", default=str(DEFAULT_PROFILE))
    parser.add_argument("--flag-definitions", default=str(DEFAULT_FLAG_DEFINITIONS))
    parser.add_argument("--output", help="Optional JSON file path for the benchmark report.")
    parser.add_argument("--warmup-runs", type=int, default=1)
    parser.add_argument("--measurement-runs", type=int, default=7)
    parser.add_argument("--temporal-context-count", type=int, default=2)
    parser.add_argument("--cross-radar-count", type=int, default=2)
    parser.add_argument(
        "--layout",
        choices=SUPPORTED_QC_ZARR_LAYOUTS,
        default=DEFAULT_QC_ZARR_LAYOUT,
        help="QC Zarr chunk layout for the primary pipeline run.",
    )
    parser.add_argument(
        "--write-empty-chunks",
        choices=("true", "false"),
        default="false",
        help="Whether to materialize all-fill chunks in the primary pipeline run.",
    )
    parser.add_argument(
        "--skip-layout-experiment",
        action="store_true",
        help="Skip the A6.3 layout comparison report and only run the primary layout.",
    )
    return parser.parse_args()


def _parse_shape(value: str) -> tuple[int, int]:
    try:
        left, right = value.lower().split("x", maxsplit=1)
        ray_count = int(left)
        gate_count = int(right)
    except ValueError as exc:
        raise ValueError(f"invalid shape {value!r}; expected raysxgates") from exc
    if ray_count <= 0 or gate_count <= 0:
        raise ValueError("shape dimensions must be positive")
    return ray_count, gate_count


def _s3_error(code: str, *, bucket: str, key: str, status: int) -> S3Error:
    return S3Error(
        SimpleNamespace(status=status),
        code,
        code,
        key,
        "benchmark-request",
        "benchmark-host",
        bucket,
        key,
    )


def _json_bytes(payload: Mapping[str, Any]) -> bytes:
    return json.dumps(payload, sort_keys=True, separators=(",", ":")).encode("utf-8")


def _hex_sha256(payload: bytes) -> str:
    return hashlib.sha256(payload).hexdigest()


def _rss_bytes() -> int:
    factor = 1 if sys.platform == "darwin" else 1024
    return resource.getrusage(resource.RUSAGE_SELF).ru_maxrss * factor


def _phase_stats(samples: Sequence[float]) -> dict[str, Any]:
    values = list(samples)
    return {
        "samples": values,
        "count": len(values),
        "min": min(values),
        "max": max(values),
        "median": float(np.median(values)),
        "mean": sum(values) / len(values),
    }


def _label_path(path: Path) -> str:
    try:
        return str(path.relative_to(REPOSITORY_ROOT))
    except ValueError:
        return str(path)


def _to_jsonable(value: Any) -> Any:
    if isinstance(value, np.generic):
        return value.item()
    if isinstance(value, dict):
        return {str(key): _to_jsonable(item) for key, item in value.items()}
    if isinstance(value, (list, tuple)):
        return [_to_jsonable(item) for item in value]
    return value


def _bool_argument(value: str) -> bool:
    return value.lower() == "true"


def _diagnostic_read_sample(qc_objects: Mapping[str, bytes]) -> dict[str, float]:
    store = CountingMemoryStore(qc_objects)
    started = time.perf_counter()
    root = zarr.open_group(store=store, mode="r")
    group = root["sweep_000"]
    ray_limit = min(32, len(group["azimuth"]))
    gate_limit = min(256, len(group["range"]))
    _ = group["DBZH_QC"][:ray_limit, :gate_limit]
    _ = group["QUALITY_INDEX"][:ray_limit, :gate_limit]
    return {
        "seconds": time.perf_counter() - started,
        "get_count": float(store.get_count),
        "read_bytes": float(store.read_bytes),
    }


def _synthetic_dbzh(ray_count: int, gate_count: int, seed: int, *, ray_shift: int = 0) -> np.ndarray:
    rng = np.random.default_rng(seed)
    dbzh = rng.normal(loc=8.0, scale=2.0, size=(ray_count, gate_count)).astype(np.float32)
    rays = np.arange(ray_count, dtype=np.float32)[:, None]
    gates = np.arange(gate_count, dtype=np.float32)[None, :]

    storm_core = 18.0 * np.exp(-((rays - ray_count * 0.28) ** 2) / (2.0 * (ray_count * 0.04) ** 2))
    storm_core = storm_core * np.exp(-((gates - gate_count * 0.38) ** 2) / (2.0 * (gate_count * 0.08) ** 2))
    dbzh += storm_core.astype(np.float32)

    outer_band = 11.0 * np.exp(-((rays - ray_count * 0.67) ** 2) / (2.0 * (ray_count * 0.07) ** 2))
    outer_band = outer_band * np.exp(-((gates - gate_count * 0.72) ** 2) / (2.0 * (gate_count * 0.12) ** 2))
    dbzh += outer_band.astype(np.float32)

    start_gate = max(gate_count // 3, 8)
    interference_rays = [ray_count // 7, ray_count // 5, ray_count // 3]
    for base_ray in interference_rays:
        ray_index = (base_ray + ray_shift) % ray_count
        dbzh[ray_index, start_gate:] = np.maximum(
            dbzh[ray_index, start_gate:],
            np.linspace(40.0, 63.0, gate_count - start_gate, dtype=np.float32),
        )
        neighbor_index = (ray_index + 1) % ray_count
        dbzh[neighbor_index, start_gate:] = np.maximum(
            dbzh[neighbor_index, start_gate:],
            np.linspace(37.0, 57.0, gate_count - start_gate, dtype=np.float32),
        )

    gap_mask = rng.random((ray_count, gate_count)) < 0.004
    dbzh[gap_mask] = np.nan
    return dbzh


def _normalized_volume_objects(
    *,
    dbzh: np.ndarray,
    radar_id: str,
    scan_id: str,
    asset_id: str,
    radar_config_version: str,
    site_longitude_deg: float,
    site_latitude_deg: float,
    volume_end_time: datetime,
    radar_health: str = "HEALTHY",
    scan_completeness: float = 1.0,
    azimuth_offset_deg: float = 0.0,
) -> dict[str, bytes]:
    ray_count, gate_count = dbzh.shape
    azimuth = (np.linspace(0.0, 360.0, ray_count, endpoint=False, dtype=np.float32) + azimuth_offset_deg) % 360.0
    ranges = np.arange(250.0, 250.0 * (gate_count + 1), 250.0, dtype=np.float32)

    store = MemoryStore()
    root = zarr.group(store=store, overwrite=True)
    root.attrs.update(
        {
            "contract_name": "rainpulse.normalized-radar-volume",
            "asset_id": asset_id,
            "scan_id": scan_id,
            "radar_id": radar_id,
            "radar_config_version": radar_config_version,
            "site_longitude_deg": site_longitude_deg,
            "site_latitude_deg": site_latitude_deg,
            "volume_start_time_utc": (volume_end_time - timedelta(minutes=6)).isoformat().replace("+00:00", "Z"),
            "volume_end_time_utc": volume_end_time.isoformat().replace("+00:00", "Z"),
            "scan_completeness": scan_completeness,
            "radar_health": radar_health,
        }
    )
    root.create_dataset("sweep_number", data=np.array([0], dtype="int16"))
    root.create_dataset("sweep_start_ray_index", data=np.array([0], dtype="int32"))
    root.create_dataset("sweep_end_ray_index", data=np.array([ray_count - 1], dtype="int32"))
    sweep = root.create_group("sweep_000")
    sweep.attrs.update({"elevation_deg": 0.5, "fixed_angle_deg": 0.5})
    sweep.create_dataset("azimuth", data=azimuth)
    sweep.create_dataset("elevation", data=np.full(ray_count, 0.5, dtype="float32"))
    sweep.create_dataset("ray_time", data=np.arange(ray_count, dtype="float64"))
    sweep.create_dataset("horizontal_noise", data=np.zeros(ray_count, dtype="float32"))
    sweep.create_dataset("vertical_noise", data=np.zeros(ray_count, dtype="float32"))
    sweep.create_dataset("range", data=ranges)
    sweep.create_dataset("DBZH", data=np.asarray(dbzh, dtype="float32"))

    objects = {str(key): bytes(value) for key, value in store.items()}
    objects["health/summary.json"] = _json_bytes(
        {
            "schema_version": "1.0",
            "radar_id": radar_id,
            "health": radar_health,
        }
    )
    return objects


def _publish_input_artifact(
    bucket: str,
    artifact_prefix: str,
    objects: Mapping[str, bytes],
) -> tuple[dict[tuple[str, str], bytes], str]:
    object_map: dict[tuple[str, str], bytes] = {}
    manifest: list[dict[str, Any]] = []
    for relative_name, payload in sorted(objects.items()):
        object_map[(bucket, f"{artifact_prefix}/{relative_name}")] = payload
        manifest.append(
            {
                "key": relative_name,
                "sha256": _hex_sha256(payload),
                "size_bytes": len(payload),
            }
        )
    object_map[(bucket, f"{artifact_prefix}/_SUCCESS.json")] = _json_bytes(
        {
            "schema_version": "1.0",
            "sha256": artifact_sha256(objects),
            "size_bytes": sum(len(payload) for payload in objects.values()),
            "objects": manifest,
        }
    )
    return object_map, f"s3://{bucket}/{artifact_prefix}"


def _build_request(
    *,
    profile: BasicQCProfile,
    current_uri: str,
    temporal_context: Sequence[dict[str, str]],
    cross_radar_context: Sequence[dict[str, str]],
) -> RadarQCRequested:
    return RadarQCRequested.model_validate(
        {
            "schema_version": "1.0",
            "event_id": "30000000-0000-4000-8000-000000000001",
            "event_type": "radar.qc.requested.v1",
            "occurred_at": "2026-08-24T03:00:20Z",
            "run_id": "10000000-0000-4000-8000-000000000001",
            "job_id": "30000000-0000-4000-8000-000000000002",
            "trace_id": "10000000-0000-4000-8000-000000000003",
            "payload": {
                "scan_id": CURRENT_SCAN_ID,
                "radar_id": CURRENT_RADAR_ID,
                "input_uri": current_uri,
                "output_prefix": f"s3://{DEFAULT_BUCKET}/{DEFAULT_OUTPUT_PREFIX}/base",
                "radar_config_version": RADAR_CONFIG_VERSION,
                "qc_profile": profile.profile_version,
                "qc_pipeline_version": profile.pipeline_version,
                "flag_definition_version": profile.flag_definition_version,
                "temporal_context": list(temporal_context),
                "cross_radar_context": list(cross_radar_context),
            },
        }
    )


def _build_case(
    *,
    profile: BasicQCProfile,
    seed: int,
    shape: tuple[int, int],
    temporal_context_count: int,
    cross_radar_count: int,
) -> tuple[dict[tuple[str, str], bytes], RadarQCRequested]:
    ray_count, gate_count = shape
    volume_end_time = datetime(2024, 8, 1, 12, 0, tzinfo=UTC)
    current_prefix = f"{DEFAULT_CURRENT_PREFIX}/{CURRENT_SCAN_ID}/volume.zarr"

    current_objects = _normalized_volume_objects(
        dbzh=_synthetic_dbzh(ray_count, gate_count, seed),
        radar_id=CURRENT_RADAR_ID,
        scan_id=CURRENT_SCAN_ID,
        asset_id="11111111-1111-4111-8111-111111111111",
        radar_config_version=RADAR_CONFIG_VERSION,
        site_longitude_deg=119.30,
        site_latitude_deg=26.08,
        volume_end_time=volume_end_time,
    )
    object_map, current_uri = _publish_input_artifact(DEFAULT_BUCKET, current_prefix, current_objects)

    temporal_context: list[dict[str, str]] = []
    for index in range(temporal_context_count):
        scan_id = f"10000000-0000-4000-8000-00000000001{index + 1}"
        prefix = f"{DEFAULT_CONTEXT_PREFIX}/{scan_id}/volume.zarr"
        temporal_objects = _normalized_volume_objects(
            dbzh=_synthetic_dbzh(ray_count, gate_count, seed + index + 1, ray_shift=index + 1),
            radar_id=CURRENT_RADAR_ID,
            scan_id=scan_id,
            asset_id=f"11111111-1111-4111-8111-11111111111{index + 2}",
            radar_config_version=RADAR_CONFIG_VERSION,
            site_longitude_deg=119.30,
            site_latitude_deg=26.08,
            volume_end_time=volume_end_time - timedelta(minutes=6 * (index + 1)),
        )
        artifact_objects, uri = _publish_input_artifact(DEFAULT_BUCKET, prefix, temporal_objects)
        object_map.update(artifact_objects)
        temporal_context.append({"radar_id": CURRENT_RADAR_ID, "input_uri": uri})

    cross_radar_context: list[dict[str, str]] = []
    for index in range(cross_radar_count):
        radar_id = f"z96{index + 1:02d}"
        scan_id = f"10000000-0000-4000-8000-00000000002{index + 1}"
        prefix = f"{DEFAULT_CONTEXT_PREFIX}/{scan_id}/volume.zarr"
        cross_objects = _normalized_volume_objects(
            dbzh=_synthetic_dbzh(ray_count, gate_count, seed + temporal_context_count + index + 11, ray_shift=index + 2),
            radar_id=radar_id,
            scan_id=scan_id,
            asset_id=f"22222222-2222-4222-8222-22222222222{index + 1}",
            radar_config_version=RADAR_CONFIG_VERSION,
            site_longitude_deg=119.30 + 0.18 * (index + 1),
            site_latitude_deg=26.08 + 0.06 * (index + 1),
            volume_end_time=volume_end_time,
            azimuth_offset_deg=0.4 * (index + 1),
        )
        artifact_objects, uri = _publish_input_artifact(DEFAULT_BUCKET, prefix, cross_objects)
        object_map.update(artifact_objects)
        cross_radar_context.append({"radar_id": radar_id, "input_uri": uri})

    request = _build_request(
        profile=profile,
        current_uri=current_uri,
        temporal_context=temporal_context,
        cross_radar_context=cross_radar_context,
    )
    return object_map, request


def _completion_event(
    *,
    output_prefix: str,
    request: RadarQCRequested,
    qc_objects: Mapping[str, bytes],
    finished_at: datetime,
    runtime_ms: int,
    mean_quality_index: float,
) -> JobCompleted:
    asset = CompletedAsset.model_validate(
        {
            "asset_type": "radar_qc_volume",
            "uri": f"{output_prefix.rstrip('/')}/volume.zarr",
            "sha256": artifact_sha256(qc_objects),
            "size_bytes": sum(len(payload) for payload in qc_objects.values()),
            "media_type": "application/vnd.rainpulse.qc-radar-volume+zarr",
        }
    )
    payload = JobCompletedPayload.model_validate(
        {
            "status": "succeeded",
            "started_at": request.occurred_at,
            "finished_at": finished_at,
            "runtime_ms": runtime_ms,
            "assets": [asset],
            "metrics": {"mean_quality_index": mean_quality_index},
            "diagnostics": {},
        }
    )
    return JobCompleted.model_validate(
        {
            "schema_version": "1.0",
            "event_id": "30000000-0000-4000-8000-000000000005",
            "event_type": "job.completed",
            "occurred_at": finished_at,
            "run_id": str(request.run_id),
            "job_id": str(request.job_id),
            "trace_id": str(request.trace_id),
            "payload": payload,
        }
    )


def _run_iteration(
    *,
    profile: BasicQCProfile,
    request: RadarQCRequested,
    objects: Mapping[tuple[str, str], bytes],
    index: int,
    write_settings: QCZarrWriteSettings,
) -> dict[str, Any]:
    client = BenchmarkObjectStore(dict(objects))
    reader = ArtifactObjectReader(client)
    pipeline_started = time.perf_counter()

    input_before = CounterSnapshot(client)
    with StageTimer() as input_timer:
        normalized = reader.load(request.payload.input_uri)
    input_counters = input_before.delta(client)

    context_before = CounterSnapshot(client)
    with StageTimer() as context_timer:
        radial_context, context_provenance = _load_radial_context(request, normalized, profile, client)
    context_counters = context_before.delta(client)

    with StageTimer() as core_timer:
        result = apply_basic_qc(normalized, profile, radial_context=radial_context)

    with StageTimer() as serialization_timer:
        qc_objects, validation = build_validated_qc_zarr_store(
            normalized,
            result,
            asset_id=UUID("50000000-0000-4000-8000-000000000001"),
            normalized_volume_uri=request.payload.input_uri,
            provenance={
                "scan_id": str(request.payload.scan_id),
                "run_id": str(request.run_id),
                "job_id": str(request.job_id),
                "trace_id": str(request.trace_id),
                "context_fingerprint": str(context_provenance["context_fingerprint"]),
                "radial_context": json.dumps(context_provenance, sort_keys=True),
            },
            write_settings=write_settings,
        )
    diagnostic_read = _diagnostic_read_sample(qc_objects)

    output_prefix = f"s3://{DEFAULT_BUCKET}/{DEFAULT_OUTPUT_PREFIX}/iter-{index:02d}"
    completion = _completion_event(
        output_prefix=output_prefix,
        request=request,
        qc_objects=qc_objects,
        finished_at=datetime.now(UTC),
        runtime_ms=int((time.perf_counter() - pipeline_started) * 1000.0),
        mean_quality_index=float(result.summary["mean_quality_index"]),
    )
    publisher = AtomicObjectPublisher(client)
    publish_before = CounterSnapshot(client)
    with StageTimer() as publish_timer:
        published = publisher.publish(
            output_prefix=output_prefix,
            job_id=request.job_id,
            data=None,
            completion=completion,
            artifact_name="volume.zarr",
            objects=qc_objects,
        )
    publish_counters = publish_before.delta(client)

    return {
        "input_seconds": input_timer.elapsed_seconds,
        "context_seconds": context_timer.elapsed_seconds,
        "core_seconds": core_timer.elapsed_seconds,
        "serialization_validation_seconds": serialization_timer.elapsed_seconds,
        "object_io_seconds": publish_timer.elapsed_seconds,
        "rss_bytes": _rss_bytes(),
        "input_counters": input_counters,
        "context_counters": context_counters,
        "object_io_counters": publish_counters,
        "validation": _to_jsonable(validation),
        "zarr_write_settings": {
            "layout": write_settings.layout,
            "write_empty_chunks": write_settings.write_empty_chunks,
        },
        "publish_result": {
            "artifact_uri": published.asset_uri,
            "marker_key": published.marker_key,
            "sha256": published.sha256,
            "size_bytes": published.size_bytes,
            "reused": published.reused,
        },
        "diagnostic_read_seconds": float(diagnostic_read["seconds"]),
        "diagnostic_get_count": float(diagnostic_read["get_count"]),
        "diagnostic_read_bytes": float(diagnostic_read["read_bytes"]),
        "context_fingerprint": str(context_provenance.get("context_fingerprint", "")),
        "radial_context_summary": {
            "temporal_requested_count": int(context_provenance.get("temporal_requested_count", 0)),
            "temporal_available_count": int(context_provenance.get("temporal_available_count", 0)),
            "temporal_used_count": int(context_provenance.get("temporal_used_count", 0)),
            "cross_radar_requested_count": int(context_provenance.get("cross_radar_requested_count", 0)),
            "cross_radar_available_count": int(context_provenance.get("cross_radar_available_count", 0)),
            "cross_radar_used_count": int(context_provenance.get("cross_radar_used_count", 0)),
        },
        "qc_summary": {
            "mean_quality_index": float(result.summary["mean_quality_index"]),
            "radial_interference_ray_count": int(result.summary["radial_interference_ray_count"]),
            "radial_interference_gate_count": int(result.summary["radial_interference_gate_count"]),
            "low_quality_gate_count": int(result.summary["low_quality_gate_count"]),
        },
    }


def _aggregate_report(
    *,
    args: argparse.Namespace,
    shape: tuple[int, int],
    profile_path: Path,
    measurement_runs: list[dict[str, Any]],
    write_settings: QCZarrWriteSettings,
    layout_experiment: dict[str, Any] | None,
) -> dict[str, Any]:
    def series(key: str) -> list[float]:
        return [float(run[key]) for run in measurement_runs]

    return {
        "metadata": {
            "seed": args.seed,
            "shape": {"rays": shape[0], "gates": shape[1]},
            "profile": _label_path(profile_path),
            "warmup_runs": args.warmup_runs,
            "measurement_runs": args.measurement_runs,
            "temporal_context_count": args.temporal_context_count,
            "cross_radar_count": args.cross_radar_count,
            "primary_layout": write_settings.layout,
            "write_empty_chunks": write_settings.write_empty_chunks,
            "generated_at": datetime.now(UTC).isoformat().replace("+00:00", "Z"),
        },
        "timings": {
            "input_seconds": _phase_stats(series("input_seconds")),
            "context_seconds": _phase_stats(series("context_seconds")),
            "core_seconds": _phase_stats(series("core_seconds")),
            "serialization_validation_seconds": _phase_stats(series("serialization_validation_seconds")),
            "object_io_seconds": _phase_stats(series("object_io_seconds")),
            "diagnostic_read_seconds": _phase_stats(series("diagnostic_read_seconds")),
            "pipeline_seconds": _phase_stats(
                [
                    float(run["input_seconds"])
                    + float(run["context_seconds"])
                    + float(run["core_seconds"])
                    + float(run["serialization_validation_seconds"])
                    + float(run["object_io_seconds"])
                    for run in measurement_runs
                ]
            ),
        },
        "rss_bytes": _phase_stats(series("rss_bytes")),
        "object_io": {
            "input_get_count": _phase_stats([float(run["input_counters"]["get_count"]) for run in measurement_runs]),
            "context_get_count": _phase_stats([float(run["context_counters"]["get_count"]) for run in measurement_runs]),
            "publish_put_count": _phase_stats([float(run["object_io_counters"]["put_count"]) for run in measurement_runs]),
            "publish_stat_count": _phase_stats([float(run["object_io_counters"]["stat_count"]) for run in measurement_runs]),
            "input_read_bytes": _phase_stats([float(run["input_counters"]["read_bytes"]) for run in measurement_runs]),
            "context_read_bytes": _phase_stats([float(run["context_counters"]["read_bytes"]) for run in measurement_runs]),
            "publish_write_bytes": _phase_stats([float(run["object_io_counters"]["write_bytes"]) for run in measurement_runs]),
            "diagnostic_get_count": _phase_stats([float(run["diagnostic_get_count"]) for run in measurement_runs]),
            "diagnostic_read_bytes": _phase_stats([float(run["diagnostic_read_bytes"]) for run in measurement_runs]),
        },
        "sample_run": {
            "validation": measurement_runs[-1]["validation"],
            "zarr_write_settings": measurement_runs[-1]["zarr_write_settings"],
            "publish_result": measurement_runs[-1]["publish_result"],
            "context_fingerprint": measurement_runs[-1]["context_fingerprint"],
            "radial_context_summary": measurement_runs[-1]["radial_context_summary"],
            "qc_summary": measurement_runs[-1]["qc_summary"],
        },
        "storage_layout_experiment": layout_experiment,
    }


def _aggregate_layout_experiment(
    *,
    measurement_runs_by_layout: Mapping[str, list[dict[str, Any]]],
    write_empty_chunks: bool,
) -> dict[str, Any]:
    layouts: dict[str, Any] = {}
    for layout, runs in measurement_runs_by_layout.items():
        layouts[layout] = _aggregate_layout_runs(runs)
    selection = _select_layout(layouts)
    return {
        "baseline_layout": DEFAULT_QC_ZARR_LAYOUT,
        "write_empty_chunks": write_empty_chunks,
        "thresholds": {
            "pipeline_improvement_fraction": LAYOUT_PIPELINE_IMPROVEMENT_THRESHOLD,
            "diagnostic_regression_fraction": LAYOUT_DIAGNOSTIC_REGRESSION_LIMIT,
            "rss_increase_fraction": LAYOUT_RSS_INCREASE_LIMIT,
        },
        "selected_layout": selection["layout"],
        "selection_reason": selection["reason"],
        "layouts": layouts,
    }


def _aggregate_layout_runs(runs: list[dict[str, Any]]) -> dict[str, Any]:
    def phase(key: str) -> dict[str, Any]:
        return _phase_stats([float(run[key]) for run in runs])

    return {
        "timings": {
            "pipeline_seconds": _phase_stats(
                [
                    float(run["input_seconds"])
                    + float(run["context_seconds"])
                    + float(run["core_seconds"])
                    + float(run["serialization_validation_seconds"])
                    + float(run["object_io_seconds"])
                    for run in runs
                ]
            ),
            "serialization_validation_seconds": phase("serialization_validation_seconds"),
            "object_io_seconds": phase("object_io_seconds"),
            "diagnostic_read_seconds": phase("diagnostic_read_seconds"),
        },
        "rss_bytes": _phase_stats([float(run["rss_bytes"]) for run in runs]),
        "object_io": {
            "publish_put_count": _phase_stats([float(run["object_io_counters"]["put_count"]) for run in runs]),
            "publish_stat_count": _phase_stats([float(run["object_io_counters"]["stat_count"]) for run in runs]),
            "diagnostic_get_count": _phase_stats([float(run["diagnostic_get_count"]) for run in runs]),
            "diagnostic_read_bytes": _phase_stats([float(run["diagnostic_read_bytes"]) for run in runs]),
        },
        "sample_run": {
            "validation": runs[-1]["validation"],
            "zarr_write_settings": runs[-1]["zarr_write_settings"],
        },
    }


def _select_layout(layouts: Mapping[str, dict[str, Any]]) -> dict[str, str]:
    baseline = layouts[DEFAULT_QC_ZARR_LAYOUT]
    baseline_pipeline = float(baseline["timings"]["pipeline_seconds"]["median"])
    baseline_diagnostic = float(baseline["timings"]["diagnostic_read_seconds"]["median"])
    baseline_rss = float(baseline["rss_bytes"]["median"])
    baseline_chunk_shape = tuple(baseline["sample_run"]["validation"]["field_chunk_shape"])
    baseline_object_count = int(baseline["sample_run"]["validation"]["object_count"])
    qualified: list[tuple[float, str]] = []
    for layout, report in layouts.items():
        if layout == DEFAULT_QC_ZARR_LAYOUT:
            continue
        if (
            tuple(report["sample_run"]["validation"]["field_chunk_shape"])
            == baseline_chunk_shape
            and int(report["sample_run"]["validation"]["object_count"])
            == baseline_object_count
        ):
            continue
        pipeline = float(report["timings"]["pipeline_seconds"]["median"])
        diagnostic = float(report["timings"]["diagnostic_read_seconds"]["median"])
        rss = float(report["rss_bytes"]["median"])
        pipeline_improvement = 1.0 - (pipeline / baseline_pipeline)
        diagnostic_regression = (diagnostic / baseline_diagnostic) - 1.0
        rss_increase = (rss / baseline_rss) - 1.0
        if (
            pipeline_improvement >= LAYOUT_PIPELINE_IMPROVEMENT_THRESHOLD
            and diagnostic_regression <= LAYOUT_DIAGNOSTIC_REGRESSION_LIMIT
            and rss_increase <= LAYOUT_RSS_INCREASE_LIMIT
        ):
            qualified.append((pipeline_improvement, layout))
    if not qualified:
        return {
            "layout": DEFAULT_QC_ZARR_LAYOUT,
            "reason": "no alternative satisfied the A6.3 improvement and regression thresholds",
        }
    qualified.sort(reverse=True)
    chosen = qualified[0][1]
    return {
        "layout": chosen,
        "reason": "selected the highest-median pipeline improvement that stayed within diagnostic and RSS limits",
    }


def main() -> int:
    args = _parse_args()
    if args.warmup_runs < 0:
        raise SystemExit("--warmup-runs must be >= 0")
    if args.measurement_runs <= 0:
        raise SystemExit("--measurement-runs must be > 0")
    if args.temporal_context_count < 0 or args.cross_radar_count < 0:
        raise SystemExit("context counts must be >= 0")
    if args.temporal_context_count > 3 or args.cross_radar_count > 3:
        raise SystemExit("context counts must be <= 3 to match RadarQCRequested limits")

    try:
        shape = _parse_shape(args.shape)
    except ValueError as exc:
        raise SystemExit(str(exc)) from exc

    profile_path = Path(args.profile).resolve()
    flag_definitions_path = Path(args.flag_definitions).resolve()
    profile = load_qc_profile(profile_path, flag_definitions_path)
    write_settings = QCZarrWriteSettings(
        layout=args.layout,
        write_empty_chunks=_bool_argument(args.write_empty_chunks),
    )
    objects, request = _build_case(
        profile=profile,
        seed=args.seed,
        shape=shape,
        temporal_context_count=args.temporal_context_count,
        cross_radar_count=args.cross_radar_count,
    )

    for index in range(args.warmup_runs):
        _run_iteration(
            profile=profile,
            request=request,
            objects=objects,
            index=index,
            write_settings=write_settings,
        )

    measurement_runs = [
        _run_iteration(
            profile=profile,
            request=request,
            objects=objects,
            index=args.warmup_runs + index,
            write_settings=write_settings,
        )
        for index in range(args.measurement_runs)
    ]
    layout_experiment = None
    if not args.skip_layout_experiment:
        layout_runs: dict[str, list[dict[str, Any]]] = {write_settings.layout: measurement_runs}
        next_index = args.warmup_runs + args.measurement_runs
        for layout in SUPPORTED_QC_ZARR_LAYOUTS:
            if layout == write_settings.layout:
                continue
            candidate_settings = QCZarrWriteSettings(
                layout=layout,
                write_empty_chunks=write_settings.write_empty_chunks,
            )
            layout_runs[layout] = []
            for _ in range(args.measurement_runs):
                layout_runs[layout].append(
                    _run_iteration(
                        profile=profile,
                        request=request,
                        objects=objects,
                        index=next_index,
                        write_settings=candidate_settings,
                    )
                )
                next_index += 1
        if DEFAULT_QC_ZARR_LAYOUT not in layout_runs:
            baseline_settings = QCZarrWriteSettings(
                layout=DEFAULT_QC_ZARR_LAYOUT,
                write_empty_chunks=write_settings.write_empty_chunks,
            )
            layout_runs[DEFAULT_QC_ZARR_LAYOUT] = [
                _run_iteration(
                    profile=profile,
                    request=request,
                    objects=objects,
                    index=next_index + index,
                    write_settings=baseline_settings,
                )
                for index in range(args.measurement_runs)
            ]
        layout_experiment = _aggregate_layout_experiment(
            measurement_runs_by_layout=layout_runs,
            write_empty_chunks=write_settings.write_empty_chunks,
        )
    report = _to_jsonable(
        _aggregate_report(
            args=args,
            shape=shape,
            profile_path=profile_path,
            measurement_runs=measurement_runs,
            write_settings=write_settings,
            layout_experiment=layout_experiment,
        )
    )
    rendered = json.dumps(report, indent=2, sort_keys=True)
    if args.output:
        Path(args.output).write_text(rendered + "\n", encoding="utf-8")
    print(rendered)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
