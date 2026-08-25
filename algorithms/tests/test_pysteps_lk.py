from __future__ import annotations

import hashlib
import json
from dataclasses import replace
from datetime import UTC, datetime, timedelta
from pathlib import Path
from uuid import UUID

import numpy as np
import pytest
import zarr
from zarr.storage import MemoryStore

from rainpulse_algo.grid import RegularLatLonGrid
from rainpulse_algo.nowcast.forecast_zarr import (
    build_forecast_output_zarr_store,
    validate_forecast_output_zarr_store,
)
from rainpulse_algo.nowcast.pysteps_lk import run_pysteps_lk
from rainpulse_algo.nowcast.pysteps_profile import load_pysteps_lk_profile
from rainpulse_algo.nowcast.pysteps_worker import _execute_pysteps_lk
from rainpulse_algo.worker.domain_contracts import PystepsLKRequested
from rainpulse_algo.worker.object_store import artifact_sha256

from .test_object_store import FakeMinio

REPOSITORY_ROOT = Path(__file__).resolve().parents[2]
PROFILE_PATH = REPOSITORY_ROOT / "configs" / "nowcast" / "rp014-pysteps-lk-v1.yaml"
ISSUE_TIME = datetime(2026, 8, 25, 12, 10, tzinfo=UTC)
INPUT_ASSET_IDS = [
    UUID("91000000-0000-4000-8000-000000000001"),
    UUID("91000000-0000-4000-8000-000000000002"),
    UUID("91000000-0000-4000-8000-000000000003"),
]
ANALYSIS_IDS = [
    UUID("92000000-0000-4000-8000-000000000001"),
    UUID("92000000-0000-4000-8000-000000000002"),
    UUID("92000000-0000-4000-8000-000000000003"),
]


def tiny_grid() -> RegularLatLonGrid:
    return RegularLatLonGrid(
        grid_id="tiny_pysteps_grid_v1",
        config_version="tiny-pysteps-grid-v1",
        west=118.0,
        east=118.63,
        south=25.0,
        north=25.63,
        longitude_interval_deg=0.01,
        latitude_interval_deg=0.01,
        longitude_count=64,
        latitude_count=64,
        reference_latitude_deg=25.315,
        ancillary_domain_id="tiny-domain-v1",
    )


def profile():
    configured = load_pysteps_lk_profile(PROFILE_PATH)
    grid = tiny_grid()
    return replace(
        configured,
        grid_id=grid.grid_id,
        grid_config_version=grid.config_version,
    )


def nowcast_input(*, rain: bool = True) -> dict[str, bytes]:
    grid = tiny_grid()
    frame_count = 3
    shape = (frame_count, *grid.shape)
    valid = np.ones(shape, dtype="uint8")
    valid[:, :, -5:] = 0
    missing = valid == 0
    dbzh = np.zeros(shape, dtype="float32")
    rate = np.zeros(shape, dtype="float32")
    if rain:
        for frame, x_start in enumerate((18, 20, 22)):
            dbzh[frame, 20:40, x_start : x_start + 20] = 35.0
            rate[frame, 20:40, x_start : x_start + 20] = 8.0
    quality = np.full(shape, 0.8, dtype="float32")
    low = np.zeros(shape, dtype="uint8")
    low[:, 20:25, 22:27] = 1
    age = np.full(shape, 0.5, dtype="float32")
    flags = np.zeros(shape, dtype="uint32")
    flags[missing] = np.uint32(4096)
    for values in (dbzh, rate, quality, age):
        values[missing] = np.nan

    times = [ISSUE_TIME - timedelta(minutes=10), ISSUE_TIME - timedelta(minutes=5), ISSUE_TIME]
    summary = {
        "schema_version": "1.0",
        "issue_time_utc": ISSUE_TIME.isoformat(),
        "grid_id": grid.grid_id,
        "profile_version": "rp013-fixed-5min-v1",
        "preprocess_version": "nowcast-input-builder-1.0.0",
        "analysis_ids": [str(value) for value in ANALYSIS_IDS],
        "input_asset_ids": [str(value) for value in INPUT_ASSET_IDS],
        "input_uris": [f"s3://rainpulse/analysis/{value}/analysis.zarr" for value in ANALYSIS_IDS],
        "frame_count": frame_count,
        "timestep_minutes": 5,
        "valid_coverage_ratio": float(np.mean(valid)),
        "mean_quality_index": float(np.mean(quality[~missing])),
        "max_data_age_minutes": float(np.max(age[~missing])),
        "valid_cell_count": int(np.count_nonzero(valid)),
        "missing_cell_count": int(np.count_nonzero(missing)),
        "low_quality_cell_count": int(np.count_nonzero(low)),
        "operational_eligible": True,
        "operational_reasons": [],
    }
    store = MemoryStore()
    root = zarr.group(store=store, overwrite=True)
    root.attrs.update(
        {
            "contract_name": "rainpulse.nowcast-input",
            "contract_version": "1.2",
            "asset_id": "93000000-0000-4000-8000-000000000001",
            "crs": "EPSG:4326",
            "registration": "point",
            "grid_id": grid.grid_id,
            "grid_config_version": grid.config_version,
            "coordinate_sha256": grid.coordinate_sha256,
            "grid_metric_version": grid.metric().version,
            "timestep_minutes": 5,
            "issue_time_utc": ISSUE_TIME.isoformat(),
            "input_asset_ids": [str(value) for value in INPUT_ASSET_IDS],
            "analysis_ids": [str(value) for value in ANALYSIS_IDS],
            "frame_count": frame_count,
            "operational_eligible": True,
            "operational_reasons": [],
        }
    )
    root.create_dataset(
        "time",
        data=np.asarray([value.replace(tzinfo=None) for value in times], dtype="datetime64[ns]"),
    )
    root.create_dataset("lat", data=grid.latitude)
    root.create_dataset("lon", data=grid.longitude)
    for name, values in {
        "DBZH_QC": dbzh,
        "RATE_QPE": rate,
        "QUALITY_INDEX": quality,
        "QC_FLAGS": flags,
        "VALID_MASK": valid,
        "LOW_QUALITY_MASK": low,
        "DATA_AGE": age,
    }.items():
        root.create_dataset(name, data=values)
    store["input/summary.json"] = json.dumps(summary).encode()
    zarr.consolidate_metadata(store)
    return {str(key): bytes(value) for key, value in store.items()}


def test_runs_real_lucas_kanade_and_writes_forecast_output() -> None:
    grid = tiny_grid()
    configured = profile()
    result = run_pysteps_lk(nowcast_input(), profile=configured, grid=grid)

    storm = np.s_[20:40, 24:42]
    assert result.motion_fallback_used is False
    assert np.median(result.velocity_pixels_per_step[0][storm]) == pytest.approx(2.0, abs=0.35)
    assert np.median(result.velocity_pixels_per_step[1][storm]) == pytest.approx(0.0, abs=0.35)
    assert result.rain_rate.shape == (1, 24, 64, 64)
    assert result.motion_u[30, 30] > 5.0
    assert np.any(result.output_valid_mask == 0)
    assert np.all(np.isnan(result.rain_rate[0][result.output_valid_mask == 0]))
    assert np.all(np.isfinite(result.rain_rate[0][result.output_valid_mask == 1]))
    assert np.all(result.persistence_valid_mask[:, :, -1] == 0)

    objects = build_forecast_output_zarr_store(
        result,
        run_id=UUID("94000000-0000-4000-8000-000000000001"),
        job_id=UUID("94000000-0000-4000-8000-000000000002"),
        issue_time=ISSUE_TIME,
        input_uri="s3://rainpulse/nowcast-input/input.zarr",
        input_asset_ids=INPUT_ASSET_IDS,
        profile=configured,
        grid=grid,
        runtime_ms=125,
    )
    validation = validate_forecast_output_zarr_store(objects)

    assert validation["shape"] == (1, 24, 64, 64)
    assert validation["lead_count"] == 24
    assert validation["motion_fallback_used"] is False


def test_uses_explicit_zero_motion_fallback_for_no_rain() -> None:
    result = run_pysteps_lk(nowcast_input(rain=False), profile=profile(), grid=tiny_grid())

    assert result.motion_fallback_used is True
    assert result.trackable_rain_pixel_count == 0
    assert np.all(result.velocity_pixels_per_step == 0)
    assert np.all(result.rain_rate[0, :, :, :-5] == 0)
    assert np.all(result.persistence_rain_rate[:, :, :-5] == 0)
    assert np.all(result.translation_rain_rate[:, :, :-5] == 0)
    assert np.all(np.isnan(result.rain_rate[0, :, :, -5:]))


def test_real_worker_reads_verified_input_and_returns_forecast_bundle(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    objects = nowcast_input()
    prefix = "nowcast-input/test/input.zarr"
    client = FakeMinio()
    manifest = []
    for key, value in objects.items():
        client.objects[("rainpulse", f"{prefix}/{key}")] = value
        manifest.append(
            {
                "key": key,
                "sha256": hashlib.sha256(value).hexdigest(),
                "size_bytes": len(value),
            }
        )
    client.objects[("rainpulse", f"{prefix}/_SUCCESS.json")] = json.dumps(
        {
            "schema_version": "1.0",
            "sha256": artifact_sha256(objects),
            "size_bytes": sum(map(len, objects.values())),
            "objects": sorted(manifest, key=lambda item: item["key"]),
        }
    ).encode()
    monkeypatch.setenv("RAINPULSE_PYSTEPS_LK_CONFIG", str(PROFILE_PATH))
    monkeypatch.setenv(
        "RAINPULSE_GRID_CONFIG",
        str(REPOSITORY_ROOT / "configs" / "grids" / "fuzhou-0p01deg-v1.yaml"),
    )
    monkeypatch.setattr(
        "rainpulse_algo.nowcast.pysteps_worker.load_pysteps_lk_profile",
        lambda _: profile(),
    )
    monkeypatch.setattr(
        "rainpulse_algo.nowcast.pysteps_worker.load_grid_config",
        lambda _: tiny_grid(),
    )
    request = PystepsLKRequested.model_validate(
        {
            "schema_version": "1.0",
            "event_id": "95000000-0000-4000-8000-000000000001",
            "event_type": "forecast.pysteps_lk.requested.v1",
            "occurred_at": "2026-08-25T12:10:01Z",
            "run_id": "95000000-0000-4000-8000-000000000002",
            "job_id": "95000000-0000-4000-8000-000000000003",
            "trace_id": "95000000-0000-4000-8000-000000000004",
            "payload": {
                "input_uri": f"s3://rainpulse/{prefix}",
                "output_prefix": "s3://rainpulse/forecast/test/pysteps-lk-1.0.0/",
                "issue_time": ISSUE_TIME.isoformat(),
                "grid_id": tiny_grid().grid_id,
                "input_asset_ids": [str(value) for value in INPUT_ASSET_IDS],
                "model_id": "pysteps-lk",
                "model_version": "pysteps-lk-1.0.0",
                "config_version": "rp014-pysteps-lk-v1",
                "forecast_contract_version": "1.1",
                "baseline_models": ["persistence", "translation"],
            },
        }
    )

    worker_result = _execute_pysteps_lk(request, client)  # type: ignore[arg-type]
    validation = validate_forecast_output_zarr_store(worker_result.objects or {})

    assert validation["lead_count"] == 24
    assert worker_result.metrics["model_runtime_ms"] >= 0
    assert worker_result.diagnostics["pysteps_lk"]["input_uri"] == request.payload.input_uri
