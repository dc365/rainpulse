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
    INTERFERENCE_TYPE_CODES,
    QCInputError,
    _cross_radar_consistency_by_ray,
    _detect_radial_interference,
    _dual_pol_meteorological_probability,
    _higher_elevation_radial_extent_fractions,
    _radial_probability,
    _temporal_radial_persistence,
    _vertical_consistency_probabilities,
    apply_basic_qc,
    audit_long_range_saturated_radials,
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
RP042_QC_CONFIG = REPOSITORY_ROOT / "configs" / "qc" / "rp042-fujian-evidence-v1.yaml"
RP043_QC_CONFIG = REPOSITORY_ROOT / "configs" / "qc" / "rp043-fujian-radial-closure-v1.yaml"
RP047_QC_CONFIG = REPOSITORY_ROOT / "configs" / "qc" / "rp047-fujian-radial-evidence-v1.yaml"
FLAG_CONFIG = REPOSITORY_ROOT / "configs" / "qc" / "flag-definitions.yaml"


def normalized_fixture(tmp_path: Path) -> dict[str, bytes]:
    radar_config = load_radar_config(make_config(tmp_path))
    volume = decode_fmt_volume(make_fmt_fixture(tmp_path, noise=(8717, 8744)), radar_config)
    health = assess_volume_health(volume, radar_config, load_radar_health_config(HEALTH_CONFIG))
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
    assert "P_METEO_DUAL_POL" not in sweep
    assert "P_VERTICAL_CONSISTENCY" not in sweep
    assert "INTERFERENCE_TYPE" not in sweep
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
    assert np.all(probability[4:10, :] >= profile.radial_interference.flag_probability)
    assert np.nanmax(probability[:4, :]) == 0.0
    assert np.nanmax(probability[10:, :]) == 0.0


def test_radial_interference_detects_two_thirds_high_long_range_ray(
    tmp_path: Path,
) -> None:
    """The observed Z9591 boundary ray is still interference at 66.7% high gates."""
    profile = load_qc_profile(QC_CONFIG, FLAG_CONFIG)
    dbzh = np.full((12, 600), np.nan, dtype="float32")

    dbzh[:, :180] = 30.0
    dbzh[6, :] = np.linspace(35.0, 65.0, 600, dtype="float32")
    valid = np.isfinite(dbzh)

    probability, flagged_count = _radial_probability(
        dbzh,
        valid,
        profile.radial_interference,
    )

    assert flagged_count == 1
    assert np.all(probability[6, :] >= profile.radial_interference.flag_probability)
    assert np.nanmax(probability[:6, :]) == 0.0
    assert np.nanmax(probability[7:, :]) == 0.0


def test_rp043_radial_fan_closure_fills_bounded_boundary_ray() -> None:
    """A near-threshold ray between hard fan seeds must not remain as a hole."""
    profile = load_qc_profile(RP043_QC_CONFIG, FLAG_CONFIG)
    dbzh = np.full((24, 600), 5.0, dtype="float32")
    dbzh[10, :] = np.linspace(46.0, 66.0, 600, dtype="float32")
    dbzh[12, :] = np.linspace(46.0, 66.0, 600, dtype="float32")
    # This boundary ray has strong range growth but misses the legacy 400-gate
    # saturated run. Similar contaminated neighbours also suppress its local
    # difference, matching the observed 10:25 and 10:45 CST holes.
    dbzh[11, :] = np.linspace(30.0, 70.0, 600, dtype="float32")

    detection = _detect_radial_interference(
        dbzh,
        np.isfinite(dbzh),
        profile.radial_interference,
        ranges_m=np.arange(600, dtype="float32") * 250.0,
    )

    assert np.all(detection.probability[11] >= profile.radial_interference.flag_probability)
    assert np.all(detection.interference_type[11] == INTERFERENCE_TYPE_CODES["broad"])


def test_rp043_multiscale_promotion_confirms_discontinuous_spike() -> None:
    """Sparse longitudinal spikes require wider azimuth context for promotion."""
    profile = load_qc_profile(RP043_QC_CONFIG, FLAG_CONFIG)
    dbzh = np.full((100, 600), 5.0, dtype="float32")
    dbzh[50, :] = np.nan
    dbzh[50, :50] = 20.0
    dbzh[50, 350:] = np.linspace(35.0, 65.0, 250, dtype="float32")

    detection = _detect_radial_interference(
        dbzh,
        np.isfinite(dbzh),
        profile.radial_interference,
        ranges_m=np.arange(600, dtype="float32") * 250.0,
    )

    typed = detection.interference_type[50] != INTERFERENCE_TYPE_CODES["none"]
    assert np.count_nonzero(typed) >= 100
    assert np.all(detection.probability[50, typed] >= profile.radial_interference.flag_probability)


def test_rp043_does_not_expand_fan_closure_into_continuous_precipitation() -> None:
    profile = load_qc_profile(RP043_QC_CONFIG, FLAG_CONFIG)
    dbzh = np.full((24, 600), 5.0, dtype="float32")
    # A spatially continuous rain band spans several rays but only part of the
    # range axis. It has neither seeded fan boundaries nor far-range growth.
    dbzh[8:16, 180:300] = 48.0

    detection = _detect_radial_interference(
        dbzh,
        np.isfinite(dbzh),
        profile.radial_interference,
        ranges_m=np.arange(600, dtype="float32") * 250.0,
    )

    assert detection.flagged_ray_count == 0
    assert not np.any(
        np.nan_to_num(detection.probability[8:16], nan=0.0)
        >= profile.radial_interference.flag_probability
    )


def test_rp047_promotes_adjacent_weak_long_range_rays_with_vertical_evidence() -> None:
    """The 08:20-like pair is invisible to immediate-neighbour differencing."""
    profile = load_qc_profile(RP047_QC_CONFIG, FLAG_CONFIG)
    ranges = (np.arange(600, dtype="float32") + 1.0) * 250.0
    dbzh = np.full((24, 600), np.nan, dtype="float32")
    dbzh[:, :120] = 18.0
    power_stable = 20.0 * np.log10(ranges / 1_000.0) - 12.0
    dbzh[10:12, :] = power_stable.astype("float32")
    higher_extent = np.full(24, np.nan, dtype="float32")
    higher_extent[10:12] = 0.23

    detection = _detect_radial_interference(
        dbzh,
        np.isfinite(dbzh),
        profile.radial_interference,
        ranges_m=ranges,
        higher_elevation_extent_fraction=higher_extent,
    )

    assert detection.flagged_ray_count == 2
    assert np.all(detection.probability[10:12] >= profile.radial_interference.flag_probability)


def test_rp047_closes_four_ray_hole_inside_confirmed_fan() -> None:
    """The 08:35-like four-ray hole is closed only between hard fan seeds."""
    profile = load_qc_profile(RP047_QC_CONFIG, FLAG_CONFIG)
    ranges = (np.arange(600, dtype="float32") + 1.0) * 250.0
    dbzh = np.full((24, 600), np.nan, dtype="float32")
    dbzh[:, :120] = 18.0
    dbzh[8, :] = np.linspace(46.0, 66.0, 600, dtype="float32")
    dbzh[13, :] = np.linspace(46.0, 66.0, 600, dtype="float32")
    for ray_index in range(9, 13):
        dbzh[ray_index, :384] = (20.0 * np.log10(ranges[:384] / 1_000.0) - 12.0).astype("float32")

    detection = _detect_radial_interference(
        dbzh,
        np.isfinite(dbzh),
        profile.radial_interference,
        ranges_m=ranges,
    )

    for ray_index in range(9, 13):
        ray_valid = np.isfinite(dbzh[ray_index])
        assert np.all(
            detection.probability[ray_index, ray_valid]
            >= profile.radial_interference.flag_probability
        )
    # The two outer gap rays are independently caught by their seed contrast;
    # the two interior rays are the actual fan-closure regression.
    for ray_index in range(10, 12):
        ray_valid = np.isfinite(dbzh[ray_index])
        assert np.all(
            detection.interference_type[ray_index, ray_valid] == INTERFERENCE_TYPE_CODES["broad"]
        )


def test_rp047_extends_confirmed_fan_into_open_truncated_edge() -> None:
    """A 14:25-like open fan edge is strong geometry, not a weak candidate."""
    profile = load_qc_profile(RP047_QC_CONFIG, FLAG_CONFIG)
    ranges = (np.arange(600, dtype="float32") + 1.0) * 250.0
    dbzh = np.full((24, 600), np.nan, dtype="float32")
    dbzh[:, :120] = 18.0
    dbzh[10:13, :] = np.linspace(46.0, 66.0, 600, dtype="float32")
    # The contaminated tail reaches only 80% of the nominal range.  Its fixed
    # far quartile is therefore incomplete, but the observed support remains
    # a long, high-occupancy, range-growing radial next to the hard fan.
    dbzh[13, :480] = np.linspace(35.0, 58.0, 480, dtype="float32")

    detection = _detect_radial_interference(
        dbzh,
        np.isfinite(dbzh),
        profile.radial_interference,
        ranges_m=ranges,
        cross_radar_consistency=np.ones(24, dtype="float32"),
    )

    tail_valid = np.isfinite(dbzh[13])
    assert np.all(
        detection.probability[13, tail_valid]
        >= profile.radial_interference.flag_probability
    )
    assert np.all(
        detection.interference_type[13, tail_valid] == INTERFERENCE_TYPE_CODES["broad"]
    )


def test_rp047_does_not_promote_broad_long_range_precipitation() -> None:
    profile = load_qc_profile(RP047_QC_CONFIG, FLAG_CONFIG)
    ranges = (np.arange(600, dtype="float32") + 1.0) * 250.0
    dbzh = np.full((24, 600), np.nan, dtype="float32")
    dbzh[:, :120] = 18.0
    broad_echo = 20.0 * np.log10(ranges / 1_000.0) - 12.0
    dbzh[5:17, :] = broad_echo.astype("float32")
    higher_extent = np.full(24, 0.23, dtype="float32")

    detection = _detect_radial_interference(
        dbzh,
        np.isfinite(dbzh),
        profile.radial_interference,
        ranges_m=ranges,
        higher_elevation_extent_fraction=higher_extent,
    )

    assert detection.flagged_ray_count == 0


def test_rp047_weak_geometry_without_context_stays_diagnostic() -> None:
    """Weak geometry lowers QI but does not become a hard removal by itself."""
    profile = load_qc_profile(RP047_QC_CONFIG, FLAG_CONFIG)
    ranges = (np.arange(600, dtype="float32") + 1.0) * 250.0
    dbzh = np.full((24, 600), np.nan, dtype="float32")
    power_stable = 20.0 * np.log10(ranges / 1_000.0) - 12.0
    dbzh[10, :] = power_stable.astype("float32")
    dbzh[12, :] = power_stable.astype("float32")
    higher_extent = np.full(24, 0.60, dtype="float32")

    detection = _detect_radial_interference(
        dbzh,
        np.isfinite(dbzh),
        profile.radial_interference,
        ranges_m=ranges,
        higher_elevation_extent_fraction=higher_extent,
    )

    assert detection.flagged_ray_count == 0
    assert np.all(
        detection.probability[[10, 12]]
        == profile.radial_interference.morphology.diagnostic_probability
    )
    assert profile.radial_interference.morphology.mode == "quality_index"


def test_rp047_temporal_persistence_is_second_independent_evidence() -> None:
    profile = load_qc_profile(RP047_QC_CONFIG, FLAG_CONFIG)
    ranges = (np.arange(600, dtype="float32") + 1.0) * 250.0
    dbzh = np.full((24, 600), np.nan, dtype="float32")
    power_stable = 20.0 * np.log10(ranges / 1_000.0) - 12.0
    dbzh[10, :] = power_stable.astype("float32")
    dbzh[12, :] = power_stable.astype("float32")
    higher_extent = np.full(24, 0.60, dtype="float32")
    temporal = np.full(24, np.nan, dtype="float32")
    temporal[[10, 12]] = 2.0 / 3.0

    detection = _detect_radial_interference(
        dbzh,
        np.isfinite(dbzh),
        profile.radial_interference,
        ranges_m=ranges,
        higher_elevation_extent_fraction=higher_extent,
        temporal_persistence=temporal,
    )

    assert detection.context_promoted_ray_count == 2
    assert np.all(
        detection.probability[[10, 12]]
        >= profile.radial_interference.flag_probability
    )


def test_rp047_cross_radar_absence_promotes_weak_candidate() -> None:
    profile = load_qc_profile(RP047_QC_CONFIG, FLAG_CONFIG)
    ranges = (np.arange(600, dtype="float32") + 1.0) * 250.0
    dbzh = np.full((24, 600), np.nan, dtype="float32")
    power_stable = 20.0 * np.log10(ranges / 1_000.0) - 12.0
    dbzh[10:12, :] = power_stable.astype("float32")
    higher_extent = np.full(24, 0.60, dtype="float32")
    cross_radar = np.full(24, np.nan, dtype="float32")
    cross_radar[10:12] = 0.05

    detection = _detect_radial_interference(
        dbzh,
        np.isfinite(dbzh),
        profile.radial_interference,
        ranges_m=ranges,
        higher_elevation_extent_fraction=higher_extent,
        cross_radar_consistency=cross_radar,
    )

    assert detection.context_promoted_ray_count == 2


def test_rp047_cross_radar_support_vetoes_weak_hard_removal() -> None:
    profile = load_qc_profile(RP047_QC_CONFIG, FLAG_CONFIG)
    ranges = (np.arange(600, dtype="float32") + 1.0) * 250.0
    dbzh = np.full((24, 600), np.nan, dtype="float32")
    power_stable = 20.0 * np.log10(ranges / 1_000.0) - 12.0
    dbzh[10:12, :] = power_stable.astype("float32")
    higher_extent = np.full(24, 0.20, dtype="float32")
    temporal = np.full(24, 1.0, dtype="float32")
    cross_radar = np.full(24, 0.95, dtype="float32")

    detection = _detect_radial_interference(
        dbzh,
        np.isfinite(dbzh),
        profile.radial_interference,
        ranges_m=ranges,
        higher_elevation_extent_fraction=higher_extent,
        temporal_persistence=temporal,
        cross_radar_consistency=cross_radar,
    )

    assert detection.flagged_ray_count == 0
    assert detection.cross_radar_vetoed_ray_count == 2
    assert np.all(
        detection.probability[10:12]
        == profile.radial_interference.morphology.diagnostic_probability
    )


def test_rp047_cross_radar_support_also_vetoes_local_morphology_candidate() -> None:
    profile = load_qc_profile(RP047_QC_CONFIG, FLAG_CONFIG)
    dbzh = np.full((7, 40), 5.0, dtype="float32")
    dbzh[3, 10:30] = 35.0
    vertical = np.full(dbzh.shape, np.nan, dtype="float32")
    vertical[3, 10:30] = 0.0
    cross_radar = np.full(7, np.nan, dtype="float32")
    cross_radar[3] = 0.95

    detection = _detect_radial_interference(
        dbzh,
        np.isfinite(dbzh),
        profile.radial_interference,
        ranges_m=np.arange(40, dtype="float32") * 250.0,
        vertical_consistency=vertical,
        cross_radar_consistency=cross_radar,
    )

    assert detection.flagged_ray_count == 0
    assert detection.cross_radar_vetoed_ray_count == 1
    assert np.all(
        detection.probability[3, 10:30]
        == profile.radial_interference.morphology.diagnostic_probability
    )


def test_rp047_strong_geometry_ignores_cross_radar_veto() -> None:
    profile = load_qc_profile(RP047_QC_CONFIG, FLAG_CONFIG)
    dbzh = np.full((12, 600), np.nan, dtype="float32")
    dbzh[:, :180] = 20.0
    dbzh[6, :] = np.linspace(46.0, 66.0, 600, dtype="float32")

    detection = _detect_radial_interference(
        dbzh,
        np.isfinite(dbzh),
        profile.radial_interference,
        ranges_m=(np.arange(600, dtype="float32") + 1.0) * 250.0,
        cross_radar_consistency=np.ones(12, dtype="float32"),
    )

    assert detection.flagged_ray_count == 1
    assert detection.cross_radar_vetoed_ray_count == 0


def test_rp047_temporal_context_aligns_azimuth_and_requires_two_scans() -> None:
    azimuth = np.array([0.0, 90.0, 180.0, 270.0], dtype="float32")
    shifted = np.array([359.0, 89.0, 179.0, 269.0], dtype="float32")
    persistence = _temporal_radial_persistence(
        azimuth,
        (
            (shifted, np.array([True, True, False, False])),
            (shifted, np.array([True, False, False, True])),
            (shifted, np.array([False, True, False, True])),
        ),
        minimum_context_scans=2,
        maximum_context_scans=3,
    )

    np.testing.assert_allclose(persistence, [2.0 / 3.0, 2.0 / 3.0, 0.0, 2.0 / 3.0])
    unavailable = _temporal_radial_persistence(
        azimuth,
        ((shifted, np.ones(4, dtype=bool)),),
        minimum_context_scans=2,
        maximum_context_scans=3,
    )
    assert np.all(np.isnan(unavailable))


def test_rp047_cross_radar_consistency_uses_only_observed_overlap() -> None:
    dbzh = np.full((2, 100), 25.0, dtype="float32")
    neighbour = np.full((2, 100), np.nan, dtype="float32")
    neighbour[0, :80] = 5.0
    neighbour[1, :79] = 25.0

    consistency = _cross_radar_consistency_by_ray(
        dbzh,
        np.isfinite(dbzh),
        (neighbour,),
        echo_threshold_dbzh=10.0,
        minimum_overlap_gates=80,
    )

    assert consistency[0] == 0.0
    assert np.isnan(consistency[1])


def test_radial_morphology_classifies_interrupted_and_short_range_segments(
    tmp_path: Path,
) -> None:
    profile = load_qc_profile(QC_CONFIG, FLAG_CONFIG)
    morphology = replace(
        profile.radial_interference.morphology,
        enabled=True,
        minimum_segment_gates=4,
        intermittent_minimum_segments=3,
        short_range_max_m=3_000.0,
    )
    radial = replace(profile.radial_interference, morphology=morphology)
    dbzh = np.full((7, 40), 5.0, dtype="float32")
    dbzh[2, 4:10] = 35.0
    dbzh[2, 15:21] = 35.0
    dbzh[2, 27:33] = 35.0
    dbzh[5, 2:10] = 38.0
    ranges = np.arange(40, dtype="float32") * 250.0

    detection = _detect_radial_interference(
        dbzh,
        np.isfinite(dbzh),
        radial,
        ranges_m=ranges,
    )

    assert detection.type_ray_counts["intermittent"] == 1
    assert detection.type_ray_counts["short_range"] == 1
    assert np.all(detection.interference_type[2, 4:10] == INTERFERENCE_TYPE_CODES["intermittent"])
    assert np.all(detection.interference_type[5, 2:10] == INTERFERENCE_TYPE_CODES["short_range"])
    assert np.all(detection.probability[2, 10:15] == 0.0)


def test_radial_morphology_classifies_reverse_range_spike(tmp_path: Path) -> None:
    profile = load_qc_profile(QC_CONFIG, FLAG_CONFIG)
    morphology = replace(
        profile.radial_interference.morphology,
        enabled=True,
        minimum_segment_gates=8,
        reverse_minimum_drop_db=12.0,
    )
    radial = replace(profile.radial_interference, morphology=morphology)
    dbzh = np.full((7, 80), 5.0, dtype="float32")
    dbzh[3, :] = np.linspace(45.0, 10.0, 80, dtype="float32")

    detection = _detect_radial_interference(
        dbzh,
        np.isfinite(dbzh),
        radial,
        ranges_m=np.arange(80, dtype="float32") * 250.0,
    )

    assert detection.type_ray_counts["reverse"] == 1
    assert (
        np.count_nonzero(detection.interference_type[3] == INTERFERENCE_TYPE_CODES["reverse"]) >= 8
    )


def test_radial_morphology_keeps_legacy_high_confidence_flags() -> None:
    profile = load_qc_profile(RP042_QC_CONFIG, FLAG_CONFIG)
    dbzh = np.full((7, 100), 5.0, dtype="float32")
    dbzh[3, :] = 35.0

    detection = _detect_radial_interference(
        dbzh,
        np.isfinite(dbzh),
        profile.radial_interference,
        ranges_m=np.arange(100, dtype="float32") * 250.0,
    )

    assert detection.flagged_ray_count >= 1
    assert np.all(detection.probability[3] >= profile.radial_interference.flag_probability)


def test_rp042_zarr_writes_optional_evidence_fields(tmp_path: Path) -> None:
    normalized = normalized_fixture(tmp_path)
    profile = load_qc_profile(RP042_QC_CONFIG, FLAG_CONFIG)

    result = apply_basic_qc(normalized, profile)
    objects = build_qc_zarr_store(
        normalized,
        result,
        asset_id=UUID("50000000-0000-4000-8000-000000000042"),
        normalized_volume_uri="s3://rainpulse/radar/normalized/z9598/scan/volume.zarr",
    )
    sweep = open_store(objects)["sweep_000"]

    assert "P_METEO_DUAL_POL" in sweep
    assert "P_VERTICAL_CONSISTENCY" in sweep
    assert "INTERFERENCE_TYPE" in sweep


def test_radial_morphology_preserves_full_legacy_ray_when_segments_are_partial() -> None:
    profile = load_qc_profile(RP042_QC_CONFIG, FLAG_CONFIG)
    dbzh = np.full((7, 100), 5.0, dtype="float32")
    dbzh[3, :60] = 35.0

    detection = _detect_radial_interference(
        dbzh,
        np.isfinite(dbzh),
        profile.radial_interference,
        ranges_m=np.arange(100, dtype="float32") * 250.0,
    )

    assert np.all(detection.probability[3] >= profile.radial_interference.flag_probability)


def test_vertical_consistency_matches_next_distinct_elevation() -> None:
    low = {
        "dbzh": np.array([[30.0, 25.0], [20.0, 15.0]], dtype="float32"),
        "azimuth": np.array([0.0, 180.0], dtype="float32"),
        "range": np.array([1_000.0, 2_000.0], dtype="float32"),
        "elevation": 0.5,
    }
    high = {
        "dbzh": np.array([[28.0, 20.0], [np.nan, np.nan]], dtype="float32"),
        "azimuth": np.array([0.2, 180.2], dtype="float32"),
        "range": np.array([1_000.0, 2_000.0], dtype="float32"),
        "elevation": 1.5,
    }

    probabilities = _vertical_consistency_probabilities(
        (low, high),
        minimum_dbzh=10.0,
        support_tolerance_db=12.0,
        maximum_range_m=100_000.0,
    )

    assert probabilities[0][0, 0] > 0.8
    assert probabilities[0][1, 0] == 0.0
    assert np.isnan(probabilities[1]).all()


def test_higher_elevation_extent_matches_nearest_azimuth() -> None:
    low = {
        "dbzh": np.ones((2, 4), dtype="float32"),
        "azimuth": np.array([0.0, 180.0], dtype="float32"),
        "range": np.array([1_000.0, 2_000.0, 3_000.0, 4_000.0], dtype="float32"),
        "elevation": 0.5,
    }
    high = {
        "dbzh": np.array(
            [[20.0, 20.0, np.nan, np.nan], [20.0, 20.0, 20.0, 20.0]],
            dtype="float32",
        ),
        "azimuth": np.array([180.2, 0.2], dtype="float32"),
        "range": np.array([1_000.0, 2_000.0, 3_000.0, 4_000.0], dtype="float32"),
        "elevation": 1.5,
    }

    extents = _higher_elevation_radial_extent_fractions((low, high))

    assert extents[0] == pytest.approx(np.array([1.0, 0.5], dtype="float32"))
    assert np.isnan(extents[1]).all()


def test_dual_pol_fuzzy_probability_is_diagnostic_and_missing_aware(
    tmp_path: Path,
) -> None:
    profile = load_qc_profile(QC_CONFIG, FLAG_CONFIG)
    fuzzy = replace(profile.dual_pol_fuzzy, enabled=True)
    dbzh = np.array([[25.0, 25.0, np.nan]], dtype="float32")
    valid = np.isfinite(dbzh)
    probability = _dual_pol_meteorological_probability(
        dbzh,
        valid,
        np.zeros_like(valid),
        snr=np.array([[20.0, 2.0, np.nan]], dtype="float32"),
        rhohv=np.array([[0.99, 0.55, np.nan]], dtype="float32"),
        zdr=np.array([[0.5, 7.8, np.nan]], dtype="float32"),
        phidp=np.array([[40.0, 200.0, np.nan]], dtype="float32"),
        config=fuzzy,
        echo=profile.echo,
    )

    assert probability[0, 0] > 0.8
    assert probability[0, 1] < 0.3
    assert np.isnan(probability[0, 2])


def test_radial_audit_reports_the_same_two_thirds_signature(tmp_path: Path) -> None:
    """The read-only audit must select the exact residual-ray shape QC rejects."""
    store = MemoryStore()
    root = zarr.group(store=store)
    root.attrs["contract_name"] = "rainpulse.normalized-radar-volume"
    root.create_dataset("sweep_number", data=np.array([0], dtype="int16"))
    sweep = root.create_group("sweep_000")
    gate_count = 600
    values = np.full((2, gate_count), 30.0, dtype="float32")
    values[1, :] = np.linspace(35.0, 65.0, gate_count, dtype="float32")
    sweep.create_dataset("DBZH", data=values)
    sweep.create_dataset("azimuth", data=np.array([10.0, 63.52], dtype="float32"))
    normalized = {str(key): bytes(value) for key, value in store.items()}

    audit = audit_long_range_saturated_radials(
        normalized,
        load_qc_profile(QC_CONFIG, FLAG_CONFIG),
    )

    assert audit["saturated_ray_count"] == 1
    assert audit["sweeps"][0]["saturated_ray_count"] == 1
    evidence = audit["sweeps"][0]["rays"][0]
    assert evidence["ray_index"] == 1
    assert evidence["high_gate_fraction"] == pytest.approx(2 / 3, abs=0.02)
    assert evidence["range_growth_db"] >= 12.0


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
        ancillary_maps={"sweep_000": {"ground_clutter": clutter, "sea_clutter": sea, "ap": ap}},
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


def test_qc_worker_records_selected_temporal_and_cross_radar_context(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    normalized = normalized_fixture(tmp_path)
    cross_radar = dict(normalized)
    cross_attrs = json.loads(cross_radar[".zattrs"])
    cross_attrs["radar_id"] = "z9593"
    cross_radar[".zattrs"] = json.dumps(cross_attrs).encode()
    client = FakeMinio()

    def publish(prefix: str, objects: dict[str, bytes]) -> None:
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

    current_prefix = "radar/normalized/z9598/current/volume.zarr"
    temporal_a_prefix = "radar/normalized/z9598/temporal-a/volume.zarr"
    temporal_b_prefix = "radar/normalized/z9598/temporal-b/volume.zarr"
    cross_prefix = "radar/normalized/z9593/aligned/volume.zarr"
    for prefix, objects in (
        (current_prefix, normalized),
        (temporal_a_prefix, normalized),
        (temporal_b_prefix, normalized),
        (cross_prefix, cross_radar),
    ):
        publish(prefix, objects)

    monkeypatch.setenv("RAINPULSE_RADAR_QC_CONFIG", str(RP047_QC_CONFIG))
    monkeypatch.setenv("RAINPULSE_QC_FLAG_DEFINITIONS", str(FLAG_CONFIG))
    request = RadarQCRequested.model_validate(
        {
            "schema_version": "1.0",
            "event_id": "30000000-0000-4000-8000-000000000021",
            "event_type": "radar.qc.requested.v1",
            "occurred_at": "2026-08-28T02:30:20Z",
            "run_id": "10000000-0000-4000-8000-000000000022",
            "job_id": "30000000-0000-4000-8000-000000000023",
            "trace_id": "10000000-0000-4000-8000-000000000024",
            "payload": {
                "scan_id": "10000000-0000-4000-8000-000000000004",
                "radar_id": "z9598",
                "input_uri": f"s3://rainpulse/{current_prefix}",
                "output_prefix": "s3://rainpulse/radar/qc/z9598/current/rp047/",
                "radar_config_version": "z9598-test-v1",
                "qc_profile": "rp047-fujian-radial-evidence-v1",
                "qc_pipeline_version": "rp047-fujian-radial-evidence-1.2.0",
                "flag_definition_version": "qc-flags-v1",
                "temporal_context": [
                    {"radar_id": "z9598", "input_uri": f"s3://rainpulse/{temporal_a_prefix}"},
                    {"radar_id": "z9598", "input_uri": f"s3://rainpulse/{temporal_b_prefix}"},
                ],
                "cross_radar_context": [
                    {"radar_id": "z9593", "input_uri": f"s3://rainpulse/{cross_prefix}"},
                ],
            },
        }
    )

    result = _execute_basic_qc(request, client)  # type: ignore[arg-type]
    context = result.diagnostics["radar_qc"]["radial_context"]

    assert context["temporal_requested_count"] == 2
    assert context["temporal_available_count"] == 2
    assert context["cross_radar_requested_count"] == 1
    assert context["cross_radar_available_count"] == 1
    assert result.metrics["radial_temporal_context_volume_count"] == 2.0
