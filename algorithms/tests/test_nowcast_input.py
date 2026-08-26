from __future__ import annotations

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
from rainpulse_algo.nowcast.input_profile import load_nowcast_input_profile
from rainpulse_algo.nowcast.input_zarr import (
    NowcastInputError,
    build_nowcast_input_zarr_store,
    validate_nowcast_input_zarr_store,
)
from rainpulse_algo.radar.mosaic_zarr import REQUIRED_FIELDS as MOSAIC_FIELDS

REPOSITORY_ROOT = Path(__file__).resolve().parents[2]
PROFILE_PATH = (
    REPOSITORY_ROOT / "configs" / "nowcast" / "rp013-fixed-5min-v1.yaml"
)
ISSUE_TIME = datetime(2026, 8, 25, 12, 10, tzinfo=UTC)
ANALYSIS_IDS = [
    UUID("81000000-0000-4000-8000-000000000001"),
    UUID("81000000-0000-4000-8000-000000000002"),
    UUID("81000000-0000-4000-8000-000000000003"),
]


def tiny_grid() -> RegularLatLonGrid:
    return RegularLatLonGrid(
        grid_id="tiny_nowcast_grid_v1",
        config_version="tiny-grid-v1",
        west=118.0,
        east=118.01,
        south=25.0,
        north=25.01,
        longitude_interval_deg=0.01,
        latitude_interval_deg=0.01,
        longitude_count=2,
        latitude_count=2,
        reference_latitude_deg=25.0,
        ancillary_domain_id="tiny-domain-v1",
    )


def profile():
    configured = load_nowcast_input_profile(PROFILE_PATH)
    grid = tiny_grid()
    return replace(
        configured,
        grid_id=grid.grid_id,
        grid_config_version=grid.config_version,
    )


def analysis_fixture(
    analysis_id: UUID,
    analysis_time: datetime,
    *,
    operational_eligible: bool = True,
    quality: float = 0.8,
    data_age: float = 0.3,
    qpe_config_version: str = "rp011-basic-qpe-v1",
) -> dict[str, bytes]:
    grid = tiny_grid()
    shape = grid.shape
    valid = np.array([[1, 1], [1, 0]], dtype="uint8")
    missing = valid == 0
    store = MemoryStore()
    root = zarr.group(store=store, overwrite=True)
    root.attrs.update(
        {
            "contract_name": "rainpulse.radar-analysis",
            "contract_version": "1.2",
            "asset_id": str(analysis_id),
            "analysis_id": str(analysis_id),
            "analysis_time": analysis_time.isoformat(),
            "grid_id": grid.grid_id,
            "grid_config_version": grid.config_version,
            "coordinate_sha256": grid.coordinate_sha256,
            "crs": "EPSG:4326",
            "registration": "point",
            "profile_version": "rp016-qi-mosaic-v1",
            "mosaic_algorithm_version": "qi-mosaic-1.1.0",
            "flag_definition_version": "qc-flags-v1",
            "input_mosaic_uri": f"s3://rainpulse/mosaic/{analysis_id}/mosaic.zarr",
            "input_asset_ids": [f"raw-{analysis_id}"],
            "qc_pipeline_versions": ["basic-polar-qc-1.0.0"],
            "qpe_config_version": qpe_config_version,
            "qpe_algorithm_version": "basic-zr-qpe-1.0.0",
            "qpe_maximum_rate_mm_h": 300.0,
            "operational_eligible": operational_eligible,
            "operational_reasons": [] if operational_eligible else ["engineering_input"],
        }
    )
    root.create_dataset("lat", data=grid.latitude)
    root.create_dataset("lon", data=grid.longitude)
    for name, dtype in MOSAIC_FIELDS.items():
        if dtype == np.dtype("float32"):
            value = quality if name == "QUALITY_INDEX" else 1.0
            if name == "DATA_AGE":
                value = data_age
            values = np.full(shape, value, dtype=dtype)
            values[missing] = np.nan
        elif name == "QC_FLAGS":
            values = np.zeros(shape, dtype=dtype)
            values[missing] = np.uint32(4096)
        elif name in {"SOURCE_RADAR", "CONTRIBUTOR_COUNT", "VALID_MASK"}:
            values = valid.astype(dtype)
        else:
            values = np.zeros(shape, dtype=dtype)
        root.create_dataset(name, data=values)
    rate = np.array([[0.0, 1.0], [2.0, np.nan]], dtype="float32")
    root.create_dataset("RATE_QPE", data=rate)
    store["mosaic/summary.json"] = json.dumps({"valid_cell_count": 3}).encode()
    store["qpe/summary.json"] = json.dumps(
        {
            "analysis_id": str(analysis_id),
            "qpe_config_version": qpe_config_version,
            "qpe_algorithm_version": "basic-zr-qpe-1.0.0",
            "input_mosaic_uri": root.attrs["input_mosaic_uri"],
            "valid_cell_count": 3,
            "missing_cell_count": 1,
            "rain_cell_count": 2,
            "no_rain_cell_count": 1,
            "capped_cell_count": 0,
            "mean_rate_mm_h": 1.0,
            "maximum_observed_rate_mm_h": 2.0,
        }
    ).encode()
    zarr.consolidate_metadata(store)
    return {str(key): bytes(value) for key, value in store.items()}


def sequence(*, gap: bool = False, **frame_values):
    times = [ISSUE_TIME - timedelta(minutes=10), ISSUE_TIME - timedelta(minutes=5), ISSUE_TIME]
    if gap:
        times[1] -= timedelta(minutes=5)
    return [
        analysis_fixture(analysis_id, analysis_time, **frame_values)
        for analysis_id, analysis_time in zip(ANALYSIS_IDS, times, strict=True)
    ]


def build(frames):
    return build_nowcast_input_zarr_store(
        frames,
        analysis_ids=ANALYSIS_IDS,
        input_uris=[f"s3://rainpulse/analysis/{value}/analysis.zarr" for value in ANALYSIS_IDS],
        issue_time=ISSUE_TIME,
        profile=profile(),
        grid=tiny_grid(),
        asset_id="82000000-0000-4000-8000-000000000001",
    )


def test_builds_fixed_step_nowcast_input_and_preserves_three_states() -> None:
    objects = build(sequence())
    validation = validate_nowcast_input_zarr_store(objects)
    store = MemoryStore()
    store.update(objects)
    root = zarr.open_group(store=store, mode="r")

    assert validation["shape"] == (3, 2, 2)
    assert root.attrs["analysis_ids"] == [str(value) for value in ANALYSIS_IDS]
    assert root.attrs["grid_metric_version"] == "wgs84-geod-grid-metric-v1"
    assert root.attrs["valid_coverage_ratio"] == pytest.approx(0.75)
    assert root["RATE_QPE"][:][0, 0, 0] == 0
    assert root["LOW_QUALITY_MASK"][:][0, 0, 0] == 0
    assert np.isnan(root["RATE_QPE"][:][0, 1, 1])
    assert root["VALID_MASK"][:][0, 1, 1] == 0


def test_rejects_missing_fixed_time_step() -> None:
    with pytest.raises(NowcastInputError, match="contiguous five-minute"):
        build(sequence(gap=True))


def test_rejects_upstream_non_operational_frame() -> None:
    with pytest.raises(NowcastInputError, match="upstream_analysis_not_operational"):
        build(sequence(operational_eligible=False))


def test_rejects_quality_and_data_age_below_gate() -> None:
    with pytest.raises(NowcastInputError, match="mean_quality_below_threshold"):
        build(sequence(quality=0.2, data_age=11.0))


def test_rejects_mixed_qpe_versions() -> None:
    frames = sequence()
    frames[-1] = analysis_fixture(
        ANALYSIS_IDS[-1], ISSUE_TIME, qpe_config_version="different-qpe-v1"
    )
    with pytest.raises(NowcastInputError, match="mix QPE versions"):
        build(frames)
