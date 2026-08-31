import hashlib
import json
from dataclasses import replace
from pathlib import Path
from uuid import UUID

import numpy as np
import pytest
import zarr
from zarr.storage import MemoryStore

from rainpulse_algo.radar.config import load_radar_config
from rainpulse_algo.radar.fmt import decode_fmt_volume
from rainpulse_algo.radar.health import assess_volume_health, load_radar_health_config
from rainpulse_algo.radar.qc import (
    QCInputError,
    _radial_probability,
    apply_basic_qc,
    load_qc_profile,
)
from rainpulse_algo.radar.qc_worker import _execute_basic_qc
from rainpulse_algo.radar.qc_zarr import build_qc_zarr_store, validate_qc_zarr_store
from rainpulse_algo.radar.zarr_volume import build_zarr_store
from rainpulse_algo.worker.domain_contracts import RadarQCRequested
from rainpulse_algo.worker.object_store import artifact_sha256

from .test_fmt_decoder import HEALTH_CONFIG, make_config, make_fmt_fixture
from .test_object_store import FakeMinio

REPOSITORY_ROOT = Path(__file__).resolve().parents[2]
QC_CONFIG = REPOSITORY_ROOT / "configs" / "qc" / "rp008-basic-v1.yaml"
FLAG_CONFIG = REPOSITORY_ROOT / "configs" / "qc" / "flag-definitions.yaml"


def normalized_fixture(tmp_path: Path) -> dict[str, bytes]:
    radar_config = load_radar_config(make_config(tmp_path))
    volume = decode_fmt_volume(make_fmt_fixture(tmp_path, noise=(8717, 8744)), radar_config)
    health = assess_volume_health(
        volume, radar_config, load_radar_health_config(HEALTH_CONFIG)
    )
    return build_zarr_store(
        volume,
        radar_config,
        asset_id=UUID("44444444-4444-4444-8444-444444444444"),
        source_uri="file:///fixtures/z9598.bin.bz2",
        health=health,
        provenance={"scan_id": "10000000-0000-4000-8000-000000000004"},
    )


def open_store(objects: dict[str, bytes]) -> zarr.Group:
    store = MemoryStore()
    store.update(objects)
    return zarr.open_group(store=store, mode="r")


def test_basic_qc_preserves_geometry_missing_and_no_rain_states(tmp_path: Path) -> None:
    normalized = normalized_fixture(tmp_path)
    profile = load_qc_profile(QC_CONFIG, FLAG_CONFIG)

    result = apply_basic_qc(normalized, profile)
    objects = build_qc_zarr_store(
        normalized,
        result,
        asset_id=UUID("50000000-0000-4000-8000-000000000001"),
        normalized_volume_uri="s3://rainpulse/radar/normalized/z9598/scan/volume.zarr",
        provenance={"job_id": "50000000-0000-4000-8000-000000000002"},
    )
    summary = validate_qc_zarr_store(objects)
    root = open_store(objects)
    sweep = root["sweep_000"]

    assert root.attrs["contract_name"] == "rainpulse.qc-radar-volume"
    assert root.attrs["qc_profile"] == "rp008-basic-v1"
    assert summary["sweep_count"] == 2
    assert summary["ray_count"] == 4
    assert np.array_equal(root["sweep_start_ray_index"][:], np.array([0, 2]))
    assert sweep["DBZH_RAW"][0, 3] == pytest.approx(0.0)
    assert sweep["DBZH_QC"][0, 3] == pytest.approx(0.0)
    assert sweep["VALID_MASK"][0, 3] == 1
    assert sweep["VALID_MASK"][0, 0] == 0
    assert sweep["QC_FLAGS"][0, 0] & np.uint32(4096)
    assert np.isnan(sweep["P_AP"][:]).all()
    assert np.isnan(sweep["P_SEA_CLUTTER"][:]).all()
    assert np.isnan(sweep["QI_BLOCKAGE"][:]).all()
    assert result.summary["no_rain_gate_count"] > 0
    assert result.module_status("static_ground_clutter") == "skipped"
    assert result.module_status("sea_ap") == "skipped"
    assert "qc/summary.json" in objects


def test_basic_qc_preserves_sweep_when_dbzh_moment_is_absent(tmp_path: Path) -> None:
    normalized = normalized_fixture(tmp_path)
    store = MemoryStore()
    store.update(normalized)
    root = zarr.open_group(store=store, mode="a")
    expected_shape = (
        len(root["sweep_001/azimuth"]),
        len(root["sweep_001/range"]),
    )
    del root["sweep_001/DBZH"]
    normalized = {str(key): bytes(value) for key, value in store.items()}

    result = apply_basic_qc(normalized, load_qc_profile(QC_CONFIG, FLAG_CONFIG))
    missing_sweep = result.sweeps[1]

    assert missing_sweep.dbzh_raw.shape == expected_shape
    assert np.isnan(missing_sweep.dbzh_raw).all()
    assert np.isnan(missing_sweep.dbzh_qc).all()
    assert np.count_nonzero(missing_sweep.valid_mask) == 0
    assert np.all(missing_sweep.qc_flags & np.uint32(4096))

    objects = build_qc_zarr_store(
        normalized,
        result,
        asset_id=UUID("50000000-0000-4000-8000-000000000003"),
        normalized_volume_uri="s3://rainpulse/radar/normalized/z9598/scan/volume.zarr",
    )
    assert validate_qc_zarr_store(objects)["sweep_count"] == 2


def test_radial_interference_flags_without_erasing_observation(tmp_path: Path) -> None:
    normalized = normalized_fixture(tmp_path)
    store = MemoryStore()
    store.update(normalized)
    root = zarr.open_group(store=store, mode="a")
    values = root["sweep_000/DBZH"][:]
    values[0, :] = 35.0
    values[1, :] = 0.0
    root["sweep_000/DBZH"][:] = values
    normalized = {str(key): bytes(value) for key, value in store.items()}
    profile = load_qc_profile(QC_CONFIG, FLAG_CONFIG)
    radial = replace(
        profile.radial_interference,
        minimum_valid_gate_fraction=0.5,
        minimum_consecutive_gates=3,
        neighbour_difference_db=10.0,
    )
    profile = replace(profile, radial_interference=radial)

    result = apply_basic_qc(normalized, profile)
    flagged = result.sweeps[0]

    assert np.all(flagged.p_radial_interference[0] >= 0.8)
    assert np.all(flagged.qc_flags[0] & np.uint32(8))
    assert np.all(flagged.valid_mask[0] == 1)
    assert np.all(flagged.dbzh_qc[0] == 35.0)
    assert result.summary["radial_interference_ray_count"] >= 1


def test_radial_interference_detects_adjacent_long_range_saturated_rays(
    tmp_path: Path,
) -> None:
    """A contiguous interference fan must not hide behind similar neighbours."""
    profile = load_qc_profile(QC_CONFIG, FLAG_CONFIG)
    dbzh = np.full((12, 600), np.nan, dtype="float32")

    # Ordinary meteorological echoes have limited radial support in this fixture.
    dbzh[:, :180] = 30.0
    # Z9591-like constant-power interference rises with range after calibration.
    dbzh[4:10, :] = np.linspace(46.0, 66.0, 600, dtype="float32")
    valid = np.isfinite(dbzh)

    probability, flagged_count = _radial_probability(
        dbzh,
        valid,
        profile.radial_interference,
    )

    assert flagged_count == 6
    assert np.all(
        probability[4:10, :]
        >= profile.radial_interference.flag_probability
    )
    assert np.nanmax(probability[:4, :]) == 0.0
    assert np.nanmax(probability[10:, :]) == 0.0


def test_unavailable_radar_health_is_a_hard_qc_gate(tmp_path: Path) -> None:
    normalized = normalized_fixture(tmp_path)
    health = json.loads(normalized["health/summary.json"])
    health["health"] = "UNAVAILABLE"
    normalized["health/summary.json"] = json.dumps(health).encode()

    with pytest.raises(QCInputError, match="UNAVAILABLE"):
        apply_basic_qc(normalized, load_qc_profile(QC_CONFIG, FLAG_CONFIG))


def test_versioned_ancillary_probabilities_set_flags_but_do_not_delete_echo(
    tmp_path: Path,
) -> None:
    normalized = normalized_fixture(tmp_path)
    profile = load_qc_profile(QC_CONFIG, FLAG_CONFIG)
    profile = replace(
        profile,
        static_ground_clutter=replace(
            profile.static_ground_clutter,
            asset_uri="file:///fixtures/clutter.npz",
            asset_version="synthetic-clutter-v1",
        ),
        sea_ap=replace(
            profile.sea_ap,
            coastline_asset_uri="file:///fixtures/coastline.npz",
            asset_version="synthetic-coastline-v1",
        ),
    )
    shape = open_store(normalized)["sweep_000/DBZH"].shape
    clutter = np.zeros(shape, dtype="float32")
    sea = np.zeros(shape, dtype="float32")
    ap = np.zeros(shape, dtype="float32")
    clutter[0, 3] = 0.9
    sea[0, 4] = 0.8
    ap[1, 3] = 0.75

    result = apply_basic_qc(
        normalized,
        profile,
        ancillary_maps={
            "sweep_000": {"ground_clutter": clutter, "sea_clutter": sea, "ap": ap}
        },
    )
    sweep = result.sweeps[0]

    assert sweep.qc_flags[0, 3] & np.uint32(1)
    assert sweep.qc_flags[0, 4] & np.uint32(2)
    assert sweep.qc_flags[1, 3] & np.uint32(4)
    assert sweep.valid_mask[0, 3] == 1
    assert np.isfinite(sweep.dbzh_qc[0, 3])
    assert result.module_status("static_ground_clutter") == "applied"
    assert result.module_status("sea_ap") == "applied"


def test_real_qc_worker_reads_verified_normalized_artifact_and_builds_qc_zarr(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    normalized = normalized_fixture(tmp_path)
    client = FakeMinio()
    prefix = "radar/normalized/z9598/scan/volume.zarr"
    manifest = []
    for key, value in normalized.items():
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
            "sha256": artifact_sha256(normalized),
            "size_bytes": sum(map(len, normalized.values())),
            "objects": sorted(manifest, key=lambda item: item["key"]),
        }
    ).encode()
    monkeypatch.setenv("RAINPULSE_RADAR_QC_CONFIG", str(QC_CONFIG))
    monkeypatch.setenv("RAINPULSE_QC_FLAG_DEFINITIONS", str(FLAG_CONFIG))
    request = RadarQCRequested.model_validate(
        {
            "schema_version": "1.0",
            "event_id": "30000000-0000-4000-8000-000000000001",
            "event_type": "radar.qc.requested.v1",
            "occurred_at": "2026-08-24T03:00:20Z",
            "run_id": "10000000-0000-4000-8000-000000000002",
            "job_id": "30000000-0000-4000-8000-000000000002",
            "trace_id": "10000000-0000-4000-8000-000000000003",
            "payload": {
                "scan_id": "10000000-0000-4000-8000-000000000004",
                "radar_id": "z9598",
                "input_uri": f"s3://rainpulse/{prefix}",
                "output_prefix": "s3://rainpulse/radar/qc/z9598/scan/rp008-basic-1.0.4/",
                "radar_config_version": "z9598-test-v1",
                "qc_profile": "rp008-basic-v1",
                "qc_pipeline_version": "rp008-basic-1.0.4",
                "flag_definition_version": "qc-flags-v1",
            },
        }
    )

    result = _execute_basic_qc(request, client)  # type: ignore[arg-type]
    validation = validate_qc_zarr_store(result.objects or {})

    assert validation["sweep_count"] == 2
    assert result.metrics["mean_quality_index"] > 0
    assert result.diagnostics["radar_qc"]["health_state"] == "DEGRADED"
