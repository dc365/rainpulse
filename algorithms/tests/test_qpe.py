from __future__ import annotations

import json
from pathlib import Path
from uuid import UUID

import numpy as np
import pytest
import zarr
from zarr.storage import MemoryStore

from rainpulse_algo.radar.analysis_zarr import (
    build_radar_analysis_zarr_store,
    validate_radar_analysis_zarr_store,
)
from rainpulse_algo.radar.qpe import QPEInputError, convert_dbzh_to_rate
from rainpulse_algo.radar.qpe_profile import load_qpe_profile

REPOSITORY_ROOT = Path(__file__).resolve().parents[2]
PROFILE_PATH = REPOSITORY_ROOT / "configs" / "qpe" / "rp011-basic-zr-v1.yaml"
ANALYSIS_ID = UUID("75000000-0000-4000-8000-000000000001")


def profile():
    return load_qpe_profile(PROFILE_PATH)


def mosaic_fixture(*, operational_eligible: bool = True) -> dict[str, bytes]:
    shape = (2, 2)
    dbzh = np.array([[5.0, 20.0], [60.0, np.nan]], dtype="float32")
    valid = np.array([[1, 1], [1, 0]], dtype="uint8")
    missing = valid == 0
    store = MemoryStore()
    root = zarr.group(store=store, overwrite=True)
    root.attrs.update(
        {
            "contract_name": "rainpulse.radar-mosaic",
            "contract_version": "1.0",
            "asset_id": "76000000-0000-4000-8000-000000000001",
            "analysis_id": str(ANALYSIS_ID),
            "analysis_time": "2026-08-25T12:05:00+00:00",
            "grid_id": "fuzhou_118_123_25_27_0p01deg_v1",
            "grid_config_version": "fuzhou-grid-0p01deg-v1",
            "coordinate_sha256": "synthetic-coordinate-sha256",
            "crs": "EPSG:4326",
            "registration": "point",
            "profile_version": "rp010-qi-mosaic-v1",
            "mosaic_algorithm_version": "qi-mosaic-1.0.0",
            "analysis_cycle_version": "analysis-cycle-rp010-v1",
            "flag_definition_version": "qc-flags-v1",
            "contributors": [{"radar_id": "z9598", "scan_id": "fixture"}],
            "radar_source_codes": {"z9598": 1},
            "blended_source_code": 65535,
            "operational_eligible": operational_eligible,
            "operational_reasons": [] if operational_eligible else ["engineering_input"],
        }
    )
    root.create_dataset("lat", data=np.array([25.0, 25.01], dtype="float32"))
    root.create_dataset("lon", data=np.array([118.0, 118.01], dtype="float32"))
    float_fields = {
        "DBZH_QC": dbzh,
        "REF_NOWCAST": dbzh.copy(),
        "QUALITY_INDEX": np.full(shape, 0.8, dtype="float32"),
        "QI_METEO": np.full(shape, np.nan, dtype="float32"),
        "QI_BLOCKAGE": np.full(shape, 0.8, dtype="float32"),
        "QI_BEAM_HEIGHT": np.full(shape, 0.7, dtype="float32"),
        "QI_ATTENUATION": np.full(shape, np.nan, dtype="float32"),
        "QI_INTERFERENCE": np.full(shape, np.nan, dtype="float32"),
        "QI_TIME": np.full(shape, 0.9, dtype="float32"),
        "QI_CALIBRATION": np.full(shape, np.nan, dtype="float32"),
        "QI_RANGE": np.full(shape, np.nan, dtype="float32"),
        "SOURCE_ELEVATION": np.full(shape, 0.5, dtype="float32"),
        "BEAM_HEIGHT": np.full(shape, 1000.0, dtype="float32"),
        "TERRAIN_HEIGHT": np.full(shape, 100.0, dtype="float32"),
        "BLOCKAGE_RATE": np.full(shape, 0.2, dtype="float32"),
        "DATA_AGE": np.full(shape, 0.3, dtype="float32"),
    }
    for name, values in float_fields.items():
        values = values.copy()
        values[missing] = np.nan
        root.create_dataset(name, data=values)
    flags = np.zeros(shape, dtype="uint32")
    flags[missing] = np.uint32(4096)
    root.create_dataset("QC_FLAGS", data=flags)
    source = np.ones(shape, dtype="uint16")
    source[missing] = 0
    root.create_dataset("SOURCE_RADAR", data=source)
    count = np.ones(shape, dtype="uint8")
    count[missing] = 0
    root.create_dataset("CONTRIBUTOR_COUNT", data=count)
    root.create_dataset("VALID_MASK", data=valid)
    root.create_dataset("LOW_QUALITY_MASK", data=np.zeros(shape, dtype="uint8"))
    store["mosaic/summary.json"] = json.dumps({"valid_cell_count": 3}).encode()
    zarr.consolidate_metadata(store)
    return {str(key): bytes(value) for key, value in store.items()}


def test_power_law_zr_preserves_no_rain_and_missing() -> None:
    dbzh = np.array([[5.0, 20.0], [30.0, np.nan]], dtype="float32")
    valid = np.array([[1, 1], [1, 0]], dtype="uint8")

    rate, diagnostics = convert_dbzh_to_rate(dbzh, valid, profile())

    assert rate[0, 0] == pytest.approx(0.0)
    expected = ((10.0 ** (20.0 / 10.0)) / 200.0) ** (1.0 / 1.6)
    assert rate[0, 1] == pytest.approx(expected)
    assert np.isnan(rate[1, 1])
    assert diagnostics["no_rain_cell_count"] == 1
    assert diagnostics["rain_cell_count"] == 2


def test_qpe_caps_and_reports_extreme_rates() -> None:
    dbzh = np.array([[80.0]], dtype="float32")
    valid = np.ones((1, 1), dtype="uint8")

    rate, diagnostics = convert_dbzh_to_rate(dbzh, valid, profile())

    assert rate[0, 0] == pytest.approx(300.0)
    assert diagnostics["capped_cell_count"] == 1
    assert diagnostics["uncapped_max_rate_mm_h"] > 300.0


def test_radar_analysis_adds_rate_and_preserves_mosaic_fields() -> None:
    mosaic = mosaic_fixture(operational_eligible=False)
    objects = build_radar_analysis_zarr_store(
        mosaic,
        mosaic_uri="s3://rainpulse/analysis/mosaic/fixture/mosaic.zarr",
        analysis_id=ANALYSIS_ID,
        profile=profile(),
        asset_id="77000000-0000-4000-8000-000000000001",
        provenance={"run_id": "78000000-0000-4000-8000-000000000001"},
    )
    validation = validate_radar_analysis_zarr_store(objects)
    store = MemoryStore()
    store.update(objects)
    root = zarr.open_group(store=store, mode="r")

    assert root.attrs["contract_version"] == "1.2"
    assert root.attrs["qpe_config_version"] == "rp011-basic-qpe-v1"
    assert root.attrs["gauge_adjustment_enabled"] is False
    assert "DBZH_RAW" not in root
    assert "INTERFERENCE_TYPE" not in root
    assert np.array_equal(
        root["VALID_MASK"][:],
        np.array([[1, 1], [1, 0]], dtype="uint8"),
    )
    assert np.isnan(root["RATE_QPE"][:][1, 1])
    assert validation["operational_eligible"] is False
    assert validation["valid_cell_count"] == 3


def test_radar_analysis_rejects_wrong_analysis_identity() -> None:
    with pytest.raises(QPEInputError, match="analysis ID"):
        build_radar_analysis_zarr_store(
            mosaic_fixture(),
            mosaic_uri="s3://rainpulse/analysis/mosaic/fixture/mosaic.zarr",
            analysis_id=UUID("75000000-0000-4000-8000-000000000002"),
            profile=profile(),
            asset_id="77000000-0000-4000-8000-000000000001",
        )
