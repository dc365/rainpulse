from __future__ import annotations

import hashlib
import json
from datetime import UTC, datetime, timedelta
from pathlib import Path
from uuid import UUID

import numpy as np
import zarr
from zarr.storage import MemoryStore

from rainpulse_algo.nowcast.forecast_zarr import build_forecast_output_zarr_store
from rainpulse_algo.nowcast.pysteps_lk import PystepsLKResult
from rainpulse_algo.verification.operational import (
    OperationalVerificationInputError,
    build_operational_verification_result,
    load_operational_verification_profile,
)
from rainpulse_algo.verification.worker import _execute_forecast_verification
from rainpulse_algo.worker.domain_contracts import ForecastVerificationRequested
from rainpulse_algo.worker.object_store import artifact_sha256

from .test_object_store import FakeMinio
from .test_pysteps_lk import profile as pysteps_profile
from .test_pysteps_lk import tiny_grid

REPOSITORY_ROOT = Path(__file__).resolve().parents[2]
PROFILE_PATH = (
    REPOSITORY_ROOT
    / "configs"
    / "verification"
    / "rp031-operational-deterministic-v1.yaml"
)
ISSUE_TIME = datetime(2026, 8, 30, 8, 0, tzinfo=UTC)
RUN_ID = UUID("31000000-0000-4000-8000-000000000001")
JOB_ID = UUID("31000000-0000-4000-8000-000000000002")


def _forecast_objects() -> dict[str, bytes]:
    grid = tiny_grid()
    shape = (24, *grid.shape)
    valid = np.ones(shape, dtype="uint8")
    valid[:, :, -1] = 0
    lk = np.ones(shape, dtype="float32")
    persistence = np.zeros(shape, dtype="float32")
    translation = np.full(shape, 2.0, dtype="float32")
    for values in (lk, persistence, translation):
        values[valid == 0] = np.nan
    accum_60 = np.full((1, *grid.shape), 1.0, dtype="float32")
    accum_120 = np.full((1, *grid.shape), 2.0, dtype="float32")
    accum_60[:, :, -1] = np.nan
    accum_120[:, :, -1] = np.nan
    result = PystepsLKResult(
        rain_rate=lk[np.newaxis, ...],
        output_valid_mask=valid,
        confidence=np.where(valid == 1, 0.8, np.nan).astype("float32"),
        motion_u=np.zeros(grid.shape, dtype="float32"),
        motion_v=np.zeros(grid.shape, dtype="float32"),
        motion_valid_mask=np.ones(grid.shape, dtype="uint8"),
        persistence_rain_rate=persistence,
        persistence_valid_mask=valid.copy(),
        translation_rain_rate=translation,
        translation_valid_mask=valid.copy(),
        accum_60=accum_60,
        accum_120=accum_120,
        velocity_pixels_per_step=np.zeros((2, *grid.shape), dtype="float32"),
        global_translation_pixels_per_step=(0.0, 0.0),
        motion_fallback_used=False,
        motion_fallback_reason=None,
        motion_feature_count=8,
        trackable_rain_pixel_count=32,
    )
    return build_forecast_output_zarr_store(
        result,
        run_id=RUN_ID,
        job_id=JOB_ID,
        issue_time=ISSUE_TIME,
        input_uri="s3://rainpulse/nowcast-input/test/input.zarr",
        input_asset_ids=[UUID("31000000-0000-4000-8000-000000000003")],
        profile=pysteps_profile(),
        grid=grid,
        runtime_ms=10,
    )


def _truth_objects(index: int) -> dict[str, bytes]:
    grid = tiny_grid()
    valid_time = ISSUE_TIME + timedelta(minutes=(index + 1) * 5)
    valid = np.ones(grid.shape, dtype="uint8")
    valid[0, 0] = 0
    rate = np.ones(grid.shape, dtype="float32")
    rate[valid == 0] = np.nan
    store = MemoryStore()
    root = zarr.group(store=store, overwrite=True)
    root.attrs.update(
        {
            "contract_name": "rainpulse.radar-analysis",
            "contract_version": "1.2",
            "analysis_id": f"31000000-0000-4000-8000-{index + 100:012d}",
            "analysis_time": valid_time.isoformat(),
            "grid_id": grid.grid_id,
            "grid_config_version": grid.config_version,
            "crs": "EPSG:4326",
            "registration": "point",
        }
    )
    root.create_dataset("lat", data=grid.latitude)
    root.create_dataset("lon", data=grid.longitude)
    root.create_dataset("RATE_QPE", data=rate)
    root.create_dataset("VALID_MASK", data=valid)
    zarr.consolidate_metadata(store)
    return {str(key): bytes(value) for key, value in store.items()}


def _publish(client: FakeMinio, uri: str, objects: dict[str, bytes]) -> str:
    bucket, key = uri.removeprefix("s3://").split("/", 1)
    manifest = []
    for name, value in objects.items():
        client.objects[(bucket, f"{key}/{name}")] = value
        manifest.append(
            {"key": name, "sha256": hashlib.sha256(value).hexdigest(), "size_bytes": len(value)}
        )
    digest = artifact_sha256(objects)
    client.objects[(bucket, f"{key}/_SUCCESS.json")] = json.dumps(
        {
            "schema_version": "2.0",
            "sha256": digest,
            "size_bytes": sum(len(value) for value in objects.values()),
            "objects": sorted(manifest, key=lambda item: item["key"]),
        }
    ).encode()
    return digest


def _request(forecast_sha256: str, truth_sha256: list[str]) -> ForecastVerificationRequested:
    return ForecastVerificationRequested.model_validate(
        {
            "schema_version": "1.0",
            "event_id": "31000000-0000-4000-8000-000000000010",
            "event_type": "forecast.verification.requested.v1",
            "occurred_at": "2026-08-30T10:05:00Z",
            "run_id": str(RUN_ID),
            "job_id": str(JOB_ID),
            "trace_id": "31000000-0000-4000-8000-000000000011",
            "payload": {
                "forecast_uri": "s3://rainpulse/forecast/test/forecast.zarr",
                "forecast_sha256": forecast_sha256,
                "truth_frames": [
                    {
                        "analysis_id": f"31000000-0000-4000-8000-{index + 100:012d}",
                        "valid_time": (ISSUE_TIME + timedelta(minutes=(index + 1) * 5)).isoformat(),
                        "input_uri": f"s3://rainpulse/analysis/{index}/analysis.zarr",
                        "input_sha256": truth_sha256[index],
                    }
                    for index in range(24)
                ],
                "output_prefix": "s3://rainpulse/verification/test/",
                "issue_time": ISSUE_TIME.isoformat(),
                "grid_id": tiny_grid().grid_id,
                "model_id": "pysteps-lk",
                "model_version": "pysteps-lk-1.1.0",
                "forecast_contract_version": "1.1",
                "verification_config_version": "rp031-operational-deterministic-v1",
                "result_contract_version": "1.0",
            },
        }
    )


def test_result_scores_all_leads_and_preserves_truth_missing_support() -> None:
    objects = build_operational_verification_result(
        _forecast_objects(),
        [_truth_objects(index) for index in range(24)],
        profile=load_operational_verification_profile(PROFILE_PATH),
        run_id=RUN_ID,
        job_id=JOB_ID,
        forecast_uri="s3://rainpulse/products/test/forecast.zarr",
        truth_uris=[f"s3://rainpulse/analysis/{index}/analysis.zarr" for index in range(24)],
    )

    summary = json.loads(objects["summary.json"])
    metrics = json.loads(objects["metrics.json"])
    first_lk = next(
        row
        for row in metrics
        if row["model"] == "lk"
        and row["lead_minutes"] == 5
        and row["threshold_mm_h"] == 1.0
        and row["window_target_km"] == 1.0
    )
    first_persistence = next(
        row
        for row in metrics
        if row["model"] == "persistence"
        and row["lead_minutes"] == 5
        and row["threshold_mm_h"] == 1.0
        and row["window_target_km"] == 1.0
    )

    assert summary["contract_name"] == "rainpulse.forecast-verification-result"
    assert summary["contract_version"] == "1.0"
    assert summary["lead_count"] == 24
    assert summary["truth_frame_count"] == 24
    assert summary["metric_row_count"] == 3 * 24 * 6 * 5
    assert first_lk["fss"] == 1.0
    assert first_lk["truth_coverage"] == (64 * 64 - 1) / (64 * 64)
    assert first_persistence["fss"] == 0.0


def test_worker_reads_checksum_verified_forecast_and_truth_artifacts(
    monkeypatch,
) -> None:
    client = FakeMinio()
    forecast_sha256 = _publish(
        client, "s3://rainpulse/forecast/test/forecast.zarr", _forecast_objects()
    )
    truth_sha256 = [
        _publish(
            client,
            f"s3://rainpulse/analysis/{index}/analysis.zarr",
            _truth_objects(index),
        )
        for index in range(24)
    ]
    monkeypatch.setenv("RAINPULSE_VERIFICATION_CONFIG", str(PROFILE_PATH))

    result = _execute_forecast_verification(
        _request(forecast_sha256, truth_sha256), client  # type: ignore[arg-type]
    )

    assert result.objects is not None
    assert json.loads(result.objects["summary.json"])["metric_row_count"] == 2160
    assert result.metrics["truth_frame_count"] == 24.0

    with np.testing.assert_raises(OperationalVerificationInputError):
        _execute_forecast_verification(
            _request("0" * 64, truth_sha256), client  # type: ignore[arg-type]
        )
