from __future__ import annotations

import hashlib
import json
from datetime import UTC, datetime
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
from rainpulse_algo.radar.qpe_worker import _execute_analysis_qpe
from rainpulse_algo.worker.domain_contracts import AnalysisQPERequestedV1
from rainpulse_algo.worker.object_store import artifact_sha256

from .test_object_store import FakeMinio

REPOSITORY_ROOT = Path(__file__).resolve().parents[2]
PROFILE_PATH = REPOSITORY_ROOT / "configs" / "qpe" / "rp011-basic-zr-v1.yaml"
VPR_PROFILE_PATH = REPOSITORY_ROOT / "configs" / "qpe" / "rp017-stratiform-vpr-v1.yaml"
GRID_CONFIG_PATH = REPOSITORY_ROOT / "configs" / "grids" / "fuzhou-0p01deg-v1.yaml"
ANALYSIS_ID = UUID("75000000-0000-4000-8000-000000000001")
VPR_FLAG_MASKS = {
    "MISSING": np.uint32(4096),
    "BRIGHT_BAND": np.uint32(512),
    "CORRECTED": np.uint32(8192),
}


def profile():
    return load_qpe_profile(PROFILE_PATH)


def profile_vpr():
    return load_qpe_profile(VPR_PROFILE_PATH)


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
            "profile_version": "rp016-qi-mosaic-v1",
            "mosaic_algorithm_version": "qi-mosaic-1.1.0",
            "analysis_cycle_version": "analysis-cycle-rp010-v1",
            "flag_definition_version": "qc-flags-v1",
            "contributors": [{"radar_id": "z9598", "scan_id": "fixture"}],
            "input_asset_ids": ["74000000-0000-4000-8000-000000000001"],
            "qc_pipeline_versions": ["rp008-basic-qc-1.0.0"],
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


def vpr_mosaic_fixture(*, include_explicit_inputs: bool = True) -> dict[str, bytes]:
    shape = (1, 5)
    dbzh = np.full(shape, 30.0, dtype="float32")
    valid = np.ones(shape, dtype="uint8")
    store = MemoryStore()
    root = zarr.group(store=store, overwrite=True)
    root.attrs.update(
        {
            "contract_name": "rainpulse.radar-mosaic",
            "contract_version": "1.0",
            "asset_id": "76000000-0000-4000-8000-000000000011",
            "analysis_id": str(ANALYSIS_ID),
            "analysis_time": "2026-08-25T12:05:00+00:00",
            "grid_id": "fuzhou_118_123_25_27_0p01deg_v1",
            "grid_config_version": "fuzhou-grid-0p01deg-v1",
            "coordinate_sha256": "synthetic-coordinate-sha256-vpr",
            "crs": "EPSG:4326",
            "registration": "point",
            "profile_version": "rp016-qi-mosaic-v1",
            "mosaic_algorithm_version": "qi-mosaic-1.1.0",
            "analysis_cycle_version": "analysis-cycle-rp010-v1",
            "flag_definition_version": "qc-flags-v1",
            "contributors": [{"radar_id": "z9598", "scan_id": "fixture-vpr"}],
            "input_asset_ids": ["74000000-0000-4000-8000-000000000011"],
            "qc_pipeline_versions": ["rp008-basic-qc-1.0.0"],
            "radar_source_codes": {"z9598": 1},
            "blended_source_code": 65535,
            "operational_eligible": True,
            "operational_reasons": [],
        }
    )
    root.create_dataset("lat", data=np.array([25.0], dtype="float32"))
    root.create_dataset(
        "lon", data=np.array([118.0, 118.01, 118.02, 118.03, 118.04], dtype="float32")
    )
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
        "BEAM_HEIGHT": np.array([[1200.0, 2000.0, 3000.0, 3000.0, 4300.0]], dtype="float32"),
        "TERRAIN_HEIGHT": np.full(shape, 100.0, dtype="float32"),
        "BLOCKAGE_RATE": np.full(shape, 0.2, dtype="float32"),
        "DATA_AGE": np.full(shape, 0.3, dtype="float32"),
    }
    if include_explicit_inputs:
        float_fields["MELTING_LAYER_BOTTOM_HEIGHT"] = np.full(
            shape, 1500.0, dtype="float32"
        )
        float_fields["MELTING_LAYER_TOP_HEIGHT"] = np.full(
            shape, 2500.0, dtype="float32"
        )
    for name, values in float_fields.items():
        root.create_dataset(name, data=values)
    if include_explicit_inputs:
        root.create_dataset(
            "PRECIP_TYPE",
            data=np.array([[1, 1, 1, 2, 1]], dtype="uint8"),
        )
    root.create_dataset("QC_FLAGS", data=np.zeros(shape, dtype="uint32"))
    root.create_dataset("SOURCE_RADAR", data=np.ones(shape, dtype="uint16"))
    root.create_dataset("CONTRIBUTOR_COUNT", data=np.ones(shape, dtype="uint8"))
    root.create_dataset("VALID_MASK", data=valid)
    root.create_dataset("LOW_QUALITY_MASK", data=np.zeros(shape, dtype="uint8"))
    store["mosaic/summary.json"] = json.dumps({"valid_cell_count": 5}).encode()
    zarr.consolidate_metadata(store)
    return {str(key): bytes(value) for key, value in store.items()}


def _publish_artifact(client: FakeMinio, prefix: str, objects: dict[str, bytes]) -> None:
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
            "size_bytes": sum(len(value) for value in objects.values()),
            "objects": sorted(manifest, key=lambda item: item["key"]),
        }
    ).encode()


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
    assert np.isnan(root["QI_ATTENUATION"][:]).all()
    assert np.isnan(root["QI_CALIBRATION"][:]).all()
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


def test_vpr_profile_rejects_missing_explicit_inputs() -> None:
    with pytest.raises(QPEInputError, match="PRECIP_TYPE"):
        build_radar_analysis_zarr_store(
            mosaic_fixture(),
            mosaic_uri="s3://rainpulse/analysis/mosaic/fixture/mosaic.zarr",
            analysis_id=ANALYSIS_ID,
            profile=profile_vpr(),
            asset_id="77000000-0000-4000-8000-000000000021",
            flag_masks=VPR_FLAG_MASKS,
        )


def test_radar_analysis_applies_stratiform_vpr_and_marks_far_overshoot_missing() -> None:
    objects = build_radar_analysis_zarr_store(
        vpr_mosaic_fixture(),
        mosaic_uri="s3://rainpulse/analysis/mosaic/fixture-vpr/mosaic.zarr",
        analysis_id=ANALYSIS_ID,
        profile=profile_vpr(),
        asset_id="77000000-0000-4000-8000-000000000022",
        flag_masks=VPR_FLAG_MASKS,
    )
    validation = validate_radar_analysis_zarr_store(objects)
    store = MemoryStore()
    store.update(objects)
    root = zarr.open_group(store=store, mode="r")
    summary = json.loads(objects["qpe/summary.json"])

    np.testing.assert_allclose(root["DBZH_VPR_INPUT"][:], np.full((1, 5), 30.0, dtype="float32"))
    np.testing.assert_allclose(root["DBZH_QC"][:], [[30.0, 30.0, 30.0, 30.0, np.nan]])
    assert root.attrs["qpe_reflectivity_field"] == "DBZH_VPR_CORRECTED"
    assert root.attrs["vpr_correction_enabled"] is True
    assert root["DBZH_VPR_CORRECTED"][:][0, 0] == pytest.approx(30.0)
    assert root["DBZH_VPR_CORRECTED"][:][0, 1] == pytest.approx(24.0)
    assert root["DBZH_VPR_CORRECTED"][:][0, 2] == pytest.approx(32.0)
    assert root["DBZH_VPR_CORRECTED"][:][0, 3] == pytest.approx(30.0)
    assert np.isnan(root["DBZH_VPR_CORRECTED"][:][0, 4])
    assert np.array_equal(
        root["VPR_APPLIED_MASK"][:],
        np.array([[0, 1, 1, 0, 0]], dtype="uint8"),
    )
    assert np.array_equal(
        root["VPR_STRATIFORM_MASK"][:],
        np.array([[1, 1, 1, 0, 1]], dtype="uint8"),
    )
    assert np.array_equal(
        root["VPR_OVERSHOOT_MASK"][:],
        np.array([[0, 0, 0, 0, 1]], dtype="uint8"),
    )
    assert np.array_equal(
        root["VALID_MASK"][:],
        np.array([[1, 1, 1, 1, 0]], dtype="uint8"),
    )
    assert root["RATE_QPE"][:][0, 1] < root["RATE_QPE"][:][0, 0]
    assert root["RATE_QPE"][:][0, 2] > root["RATE_QPE"][:][0, 0]
    assert root["RATE_QPE"][:][0, 3] == pytest.approx(root["RATE_QPE"][:][0, 0])
    assert np.isnan(root["RATE_QPE"][:][0, 4])
    assert int(root["QC_FLAGS"][:][0, 1]) == int(
        VPR_FLAG_MASKS["BRIGHT_BAND"] | VPR_FLAG_MASKS["CORRECTED"]
    )
    assert int(root["QC_FLAGS"][:][0, 2]) == int(VPR_FLAG_MASKS["CORRECTED"])
    assert int(root["QC_FLAGS"][:][0, 4]) == int(VPR_FLAG_MASKS["MISSING"])
    assert validation["valid_cell_count"] == 4
    assert summary["input_field"] == "DBZH_VPR_CORRECTED"
    assert summary["vpr_corrected_cell_count"] == 2
    assert summary["vpr_bright_band_cell_count"] == 1
    assert summary["vpr_overshoot_missing_cell_count"] == 1


def test_qpe_worker_requires_flag_definitions_for_vpr_profile(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    client = FakeMinio()
    _publish_artifact(client, "analysis/mosaic/fixture-vpr/mosaic.zarr", vpr_mosaic_fixture())
    request = AnalysisQPERequestedV1.model_validate(
        {
            "schema_version": "1.0",
            "event_id": "79000000-0000-4000-8000-000000000001",
            "event_type": "analysis.qpe.requested.v1",
            "occurred_at": datetime(2026, 8, 25, 12, 6, tzinfo=UTC).isoformat(),
            "run_id": "79000000-0000-4000-8000-000000000002",
            "job_id": "79000000-0000-4000-8000-000000000003",
            "trace_id": "79000000-0000-4000-8000-000000000004",
            "payload": {
                "analysis_id": str(ANALYSIS_ID),
                "analysis_time": "2026-08-25T12:05:00+00:00",
                "grid_id": "fuzhou_118_123_25_27_0p01deg_v1",
                "grid_config_version": "fuzhou-grid-0p01deg-v1",
                "input_uri": "s3://rainpulse/analysis/mosaic/fixture-vpr/mosaic.zarr",
                "output_prefix": "s3://rainpulse/analysis/qpe/fixture-vpr/",
                "mosaic_config_version": "rp016-qi-mosaic-v1",
                "mosaic_algorithm_version": "qi-mosaic-1.1.0",
                "qpe_config_version": "rp017-stratiform-vpr-v1",
                "qpe_algorithm_version": "stratiform-vpr-qpe-1.0.1",
                "flag_definition_version": "qc-flags-v1",
            },
        }
    )

    monkeypatch.setenv("RAINPULSE_QPE_CONFIG", str(VPR_PROFILE_PATH))
    monkeypatch.setenv("RAINPULSE_GRID_CONFIG", str(GRID_CONFIG_PATH))
    monkeypatch.delenv("RAINPULSE_QC_FLAG_DEFINITIONS", raising=False)

    with pytest.raises(
        QPEInputError,
        match="RAINPULSE_QC_FLAG_DEFINITIONS is required when VPR correction is enabled",
    ):
        _execute_analysis_qpe(request, client)  # type: ignore[arg-type]


def test_vpr_overshoot_analysis_is_consumable_by_nowcast_input() -> None:
    from dataclasses import replace
    from datetime import timedelta

    from rainpulse_algo.nowcast.input_zarr import build_nowcast_input_zarr_store
    from rainpulse_algo.radar.vpr_shadow_replay import _build_mosaic_objects

    from .test_nowcast_input import ANALYSIS_IDS, ISSUE_TIME, tiny_grid
    from .test_nowcast_input import profile as input_profile

    grid = tiny_grid()
    configured = replace(
        profile_vpr(), grid_id=grid.grid_id, grid_config_version=grid.config_version
    )
    frames = []
    for index, analysis_id in enumerate(ANALYSIS_IDS):
        mosaic = _build_mosaic_objects(
            case_id="vpr-integration",
            analysis_id=analysis_id,
            input_data={
                "lat": grid.latitude.tolist(), "lon": grid.longitude.tolist(),
                "DBZH_QC": [[30, 30], [30, 30]],
                "BEAM_HEIGHT": [[1200, 2000], [3000, 4300]],
                "PRECIP_TYPE": [[1, 1], [1, 1]],
                "MELTING_LAYER_BOTTOM_HEIGHT": [[1500, 1500], [1500, 1500]],
                "MELTING_LAYER_TOP_HEIGHT": [[2500, 2500], [2500, 2500]],
                "LOW_QUALITY_MASK": [[0, 0], [0, 1]],
            },
        )
        store = MemoryStore()
        store.update(mosaic)
        root = zarr.open_group(store=store, mode="a")
        root.attrs.update({
            "grid_id": grid.grid_id, "grid_config_version": grid.config_version,
            "coordinate_sha256": grid.coordinate_sha256,
            "analysis_time": (ISSUE_TIME - timedelta(minutes=(2 - index) * 5)).isoformat(),
        })
        zarr.consolidate_metadata(store)
        inputs = dict(store)
        original_hash = artifact_sha256(inputs)
        frames.append(build_radar_analysis_zarr_store(
            inputs, mosaic_uri=f"s3://rainpulse/mosaic/{analysis_id}",
            analysis_id=analysis_id, profile=configured, asset_id=str(analysis_id),
            flag_masks=VPR_FLAG_MASKS,
        ))
        assert artifact_sha256(inputs) == original_hash

    objects = build_nowcast_input_zarr_store(
        frames, analysis_ids=ANALYSIS_IDS,
        input_uris=[f"s3://rainpulse/analysis/{value}" for value in ANALYSIS_IDS],
        issue_time=ISSUE_TIME, profile=input_profile(), grid=grid, asset_id="vpr-nowcast-test",
    )
    store = MemoryStore()
    store.update(objects)
    root = zarr.open_group(store=store, mode="r")
    assert np.all(root["VALID_MASK"][:, 1, 1] == 0)
    assert np.all(root["LOW_QUALITY_MASK"][:, 1, 1] == 0)
    for name in ("DBZH_QC", "RATE_QPE", "QUALITY_INDEX", "DATA_AGE"):
        assert np.all(np.isnan(root[name][:, 1, 1]))
    assert np.all(np.isfinite(root["RATE_QPE"][:, 0, 0]))


@pytest.mark.parametrize("field,value", [
    ("DBZH_QC", 30.0), ("QUALITY_INDEX", 0.8), ("DATA_AGE", 0.3),
    ("LOW_QUALITY_MASK", 1), ("SOURCE_RADAR", 1), ("VALID_MASK", 2),
])
def test_analysis_validator_rejects_inconsistent_missing_cells(field, value) -> None:
    objects = build_radar_analysis_zarr_store(
        mosaic_fixture(), mosaic_uri="s3://rainpulse/mosaic/test",
        analysis_id=ANALYSIS_ID, profile=profile(), asset_id="validator-test",
    )
    store = MemoryStore()
    store.update(objects)
    root = zarr.open_group(store=store, mode="a")
    root[field][1, 1] = value
    zarr.consolidate_metadata(store)
    with pytest.raises(QPEInputError):
        validate_radar_analysis_zarr_store(dict(store))
