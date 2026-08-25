from __future__ import annotations

import json
from dataclasses import replace
from datetime import UTC, datetime, timedelta
from pathlib import Path
from uuid import UUID

import numpy as np
import pytest
import yaml
import zarr
from zarr.storage import MemoryStore

from rainpulse_algo.grid import RegularLatLonGrid
from rainpulse_algo.radar.mosaic import (
    RadarMosaicInput,
    RadarMosaicInputError,
    build_radar_mosaic,
)
from rainpulse_algo.radar.mosaic_profile import load_radar_mosaic_profile
from rainpulse_algo.radar.mosaic_zarr import (
    build_radar_mosaic_zarr_store,
    validate_radar_mosaic_zarr_store,
)

REPOSITORY_ROOT = Path(__file__).resolve().parents[2]
PROFILE_PATH = REPOSITORY_ROOT / "configs" / "mosaic" / "rp010-qi-mosaic-v1.yaml"
FLAG_PATH = REPOSITORY_ROOT / "configs" / "qc" / "flag-definitions.yaml"
ANALYSIS_TIME = datetime(2026, 8, 25, 12, 0, tzinfo=UTC)


def small_grid() -> RegularLatLonGrid:
    return RegularLatLonGrid(
        grid_id="synthetic-mosaic-grid-v1",
        config_version="synthetic-mosaic-grid-config-v1",
        west=118.0,
        east=118.01,
        south=25.0,
        north=25.01,
        longitude_interval_deg=0.01,
        latitude_interval_deg=0.01,
        longitude_count=2,
        latitude_count=2,
        reference_latitude_deg=25.0,
        ancillary_domain_id="synthetic-ancillary-v1",
    )


def profile():
    grid = small_grid()
    return replace(
        load_radar_mosaic_profile(PROFILE_PATH),
        grid_id=grid.grid_id,
        grid_config_version=grid.config_version,
    )


def flag_masks() -> dict[str, np.uint32]:
    value = yaml.safe_load(FLAG_PATH.read_text())
    return {item["name"]: np.uint32(item["mask"]) for item in value["flags"]}


def radar_grid_fixture(
    radar_id: str,
    scan_id: str,
    dbzh: np.ndarray,
    quality: np.ndarray,
    *,
    offset_seconds: int = 0,
    operational_eligible: bool = True,
) -> dict[str, bytes]:
    grid = small_grid()
    flags = flag_masks()
    valid = np.isfinite(dbzh).astype("uint8")
    missing = valid == 0
    store = MemoryStore()
    root = zarr.group(store=store, overwrite=True)
    root.attrs.update(
        {
            "contract_name": "rainpulse.radar-grid",
            "contract_version": "1.3",
            "asset_id": "60000000-0000-4000-8000-000000000001",
            "input_asset_ids": [f"raw-{scan_id}"],
            "scan_id": scan_id,
            "radar_id": radar_id,
            "qc_asset_id": "50000000-0000-4000-8000-000000000001",
            "qc_volume_uri": f"s3://rainpulse/qc/{radar_id}/volume.zarr",
            "normalized_volume_uri": f"s3://rainpulse/normalized/{radar_id}/volume.zarr",
            "radar_config_version": f"{radar_id}-config-v1",
            "qc_profile": "rp008-basic-v1",
            "qc_pipeline_version": "rp008-basic-1.0.4",
            "flag_definition_version": "qc-flags-v1",
            "grid_id": grid.grid_id,
            "grid_config_version": grid.config_version,
            "coordinate_sha256": grid.coordinate_sha256,
            "crs": "EPSG:4326",
            "registration": "point",
            "hybrid_scan_version": "hybrid-scan-1.0.1",
            "operational_eligible": operational_eligible,
            "operational_reasons": [] if operational_eligible else ["engineering_input"],
            "volume_start_time_utc": (
                ANALYSIS_TIME + timedelta(seconds=offset_seconds - 60)
            ).isoformat(),
            "volume_end_time_utc": (
                ANALYSIS_TIME + timedelta(seconds=offset_seconds)
            ).isoformat(),
        }
    )
    root.create_dataset("lat", data=grid.latitude)
    root.create_dataset("lon", data=grid.longitude)
    float_fields = {
        "DBZH_QC": dbzh,
        "QUALITY_INDEX": quality,
        "QI_METEO": np.full(grid.shape, 0.9, dtype="float32"),
        "QI_BLOCKAGE": np.full(grid.shape, 0.8, dtype="float32"),
        "QI_BEAM_HEIGHT": np.full(grid.shape, 0.7, dtype="float32"),
        "QI_ATTENUATION": np.full(grid.shape, np.nan, dtype="float32"),
        "QI_INTERFERENCE": np.ones(grid.shape, dtype="float32"),
        "QI_CALIBRATION": np.full(grid.shape, np.nan, dtype="float32"),
        "QI_RANGE": np.full(grid.shape, 0.85, dtype="float32"),
        "SOURCE_ELEVATION": np.full(grid.shape, 0.5, dtype="float32"),
        "BEAM_HEIGHT": np.full(grid.shape, 1000.0, dtype="float32"),
        "TERRAIN_HEIGHT": np.full(grid.shape, 100.0, dtype="float32"),
        "BLOCKAGE_RATE": np.full(grid.shape, 0.2, dtype="float32"),
        "DATA_AGE": np.zeros(grid.shape, dtype="float32"),
    }
    for name, values in float_fields.items():
        values = values.astype("float32", copy=True)
        values[missing] = np.nan
        root.create_dataset(name, data=values)
    qc_flags = np.zeros(grid.shape, dtype="uint32")
    qc_flags[missing] = flags["MISSING"]
    root.create_dataset("QC_FLAGS", data=qc_flags)
    source_sweep = np.zeros(grid.shape, dtype="int16")
    source_sweep[missing] = -1
    root.create_dataset("SOURCE_SWEEP", data=source_sweep)
    root.create_dataset("VALID_MASK", data=valid)
    root.create_dataset("LOW_QUALITY_MASK", data=np.zeros(grid.shape, dtype="uint8"))
    polar = root.create_group("polar").create_group("sweep_000")
    polar.create_dataset("azimuth", data=np.array([0.0], dtype="float32"))
    polar.create_dataset("elevation", data=np.array([0.5], dtype="float32"))
    polar.create_dataset("range", data=np.array([1000.0], dtype="float32"))
    for name, value in (
        ("PARTIAL_BLOCKAGE", 0.2),
        ("BLOCKAGE_RATE", 0.2),
        ("BEAM_HEIGHT", 1000.0),
        ("TERRAIN_HEIGHT", 100.0),
    ):
        polar.create_dataset(name, data=np.array([[value]], dtype="float32"))
    polar.create_dataset("SUPPORT_MASK", data=np.ones((1, 1), dtype="uint8"))
    store["grid/summary.json"] = json.dumps(
        {"valid_cell_count": int(np.count_nonzero(valid))}
    ).encode()
    zarr.consolidate_metadata(store)
    return {str(key): bytes(value) for key, value in store.items()}


def mosaic_input(
    radar_id: str,
    scan_id: str,
    dbzh: np.ndarray,
    quality: np.ndarray,
    *,
    offset_seconds: int = 0,
    operational_eligible: bool = True,
) -> RadarMosaicInput:
    return RadarMosaicInput(
        radar_id=radar_id,
        scan_id=scan_id,
        grid_uri=f"s3://rainpulse/grid/{radar_id}/grid.zarr",
        time_offset_seconds=offset_seconds,
        hybrid_scan_version="hybrid-scan-1.0.1",
        objects=radar_grid_fixture(
            radar_id,
            scan_id,
            dbzh,
            quality,
            offset_seconds=offset_seconds,
            operational_eligible=operational_eligible,
        ),
    )


def test_selects_clear_best_qi_and_blends_similar_qi_in_linear_z() -> None:
    first = mosaic_input(
        "radar-a",
        "10000000-0000-4000-8000-000000000001",
        np.array([[10.0, 10.0], [10.0, np.nan]], dtype="float32"),
        np.array([[0.8, 0.9], [0.8, 0.8]], dtype="float32"),
    )
    second = mosaic_input(
        "radar-b",
        "20000000-0000-4000-8000-000000000001",
        np.array([[20.0, 20.0], [np.nan, np.nan]], dtype="float32"),
        np.array([[0.75, 0.6], [0.7, 0.7]], dtype="float32"),
    )
    result = build_radar_mosaic(
        (first, second),
        analysis_time=ANALYSIS_TIME,
        grid=small_grid(),
        profile=profile(),
        flag_masks=flag_masks(),
        created_at=ANALYSIS_TIME,
    )

    weights = np.array([0.8**2, 0.75**2])
    expected = 10 * np.log10(np.average([10.0, 100.0], weights=weights))
    assert result.fields["DBZH_QC"][0, 0] == pytest.approx(expected, rel=1e-5)
    assert result.fields["DBZH_QC"][0, 0] != pytest.approx(15.0)
    assert result.fields["SOURCE_RADAR"][0, 0] == 65535
    assert result.fields["CONTRIBUTOR_COUNT"][0, 0] == 2
    assert result.fields["DBZH_QC"][0, 1] == pytest.approx(10.0)
    assert result.fields["SOURCE_RADAR"][0, 1] == 1
    assert result.fields["VALID_MASK"][1, 1] == 0
    assert np.isnan(result.fields["DBZH_QC"][1, 1])
    assert result.operational_eligible

    objects = build_radar_mosaic_zarr_store(
        result,
        asset_id=UUID("70000000-0000-4000-8000-000000000001"),
        provenance={"job_id": "70000000-0000-4000-8000-000000000002"},
    )
    validation = validate_radar_mosaic_zarr_store(objects)
    assert validation["shape"] == (2, 2)
    assert validation["blended_cell_count"] == 1
    assert "mosaic/summary.json" in objects


def test_time_quality_changes_selection_and_tracks_data_age() -> None:
    recent = mosaic_input(
        "radar-a",
        "10000000-0000-4000-8000-000000000002",
        np.full((2, 2), 12.0, dtype="float32"),
        np.full((2, 2), 0.7, dtype="float32"),
    )
    stale = mosaic_input(
        "radar-b",
        "20000000-0000-4000-8000-000000000002",
        np.full((2, 2), 30.0, dtype="float32"),
        np.full((2, 2), 0.95, dtype="float32"),
        offset_seconds=75,
    )
    result = build_radar_mosaic(
        (recent, stale),
        analysis_time=ANALYSIS_TIME,
        grid=small_grid(),
        profile=profile(),
        flag_masks=flag_masks(),
    )
    assert result.fields["DBZH_QC"][0, 0] == pytest.approx(12.0)
    assert result.fields["QI_TIME"][0, 0] == pytest.approx(1.0)
    assert result.fields["DATA_AGE"][0, 0] == pytest.approx(0.0)


def test_single_engineering_input_is_valid_but_not_operational() -> None:
    only = mosaic_input(
        "radar-a",
        "10000000-0000-4000-8000-000000000003",
        np.full((2, 2), 15.0, dtype="float32"),
        np.full((2, 2), 0.8, dtype="float32"),
        operational_eligible=False,
    )
    result = build_radar_mosaic(
        (only,),
        analysis_time=ANALYSIS_TIME,
        grid=small_grid(),
        profile=profile(),
        flag_masks=flag_masks(),
    )
    assert not result.operational_eligible
    assert "insufficient_operational_contributors" in result.operational_reasons
    assert "input_not_operational:radar-a" in result.operational_reasons


def test_rejects_request_time_offset_that_differs_from_grid() -> None:
    source = mosaic_input(
        "radar-a",
        "10000000-0000-4000-8000-000000000004",
        np.full((2, 2), 15.0, dtype="float32"),
        np.full((2, 2), 0.8, dtype="float32"),
    )
    mismatched = replace(source, time_offset_seconds=1)
    with pytest.raises(RadarMosaicInputError, match="time offset"):
        build_radar_mosaic(
            (mismatched,),
            analysis_time=ANALYSIS_TIME,
            grid=small_grid(),
            profile=profile(),
            flag_masks=flag_masks(),
        )
