from __future__ import annotations

from pathlib import Path

import numpy as np

from rainpulse_algo.radar.qc import (
    _detect_radial_interference,
    _higher_elevation_radial_extent_fractions,
    _vertical_consistency_probabilities,
    load_qc_profile,
)

REPOSITORY_ROOT = Path(__file__).resolve().parents[2]
V2_QC_CONFIG = REPOSITORY_ROOT / "configs" / "qc" / "fujian-qc-evidence-v2.yaml"
FLAG_CONFIG = REPOSITORY_ROOT / "configs" / "qc" / "flag-definitions.yaml"


def test_evidence_v2_does_not_hard_flag_adjacent_background_rays() -> None:
    profile = load_qc_profile(V2_QC_CONFIG, FLAG_CONFIG)
    gate_count = 920
    ray_count = 360
    ranges = (np.arange(gate_count, dtype="float32") + 1.0) * 250.0
    dbzh = np.full((ray_count, gate_count), 5.0, dtype="float32")
    dbzh[180, :] = 35.0

    detection = _detect_radial_interference(
        dbzh,
        np.isfinite(dbzh),
        profile.radial_interference,
        ranges_m=ranges,
    )

    hard = (
        np.nan_to_num(detection.probability, nan=0.0)
        >= profile.radial_interference.flag_probability
    )
    assert np.any(hard[180])
    assert not np.any(hard[179])
    assert not np.any(hard[181])


def test_evidence_v2_requires_both_neighbour_rays_for_strong_seed() -> None:
    profile = load_qc_profile(V2_QC_CONFIG, FLAG_CONFIG)
    gate_count = 920
    ray_count = 360
    ranges = (np.arange(gate_count, dtype="float32") + 1.0) * 250.0
    dbzh = np.full((ray_count, gate_count), 5.0, dtype="float32")
    dbzh[179, :] = np.nan
    dbzh[180, :] = 35.0

    detection = _detect_radial_interference(
        dbzh,
        np.isfinite(dbzh),
        profile.radial_interference,
        ranges_m=ranges,
    )

    hard = (
        np.nan_to_num(detection.probability, nan=0.0)
        >= profile.radial_interference.flag_probability
    )
    assert not np.any(hard[180])


def test_higher_elevation_extent_is_unavailable_when_upper_sweep_is_missing() -> None:
    low = {
        "dbzh": np.full((2, 4), 20.0, dtype="float32"),
        "azimuth": np.array([0.0, 180.0], dtype="float32"),
        "range": np.array([1_000.0, 2_000.0, 3_000.0, 4_000.0], dtype="float32"),
        "elevation": 0.5,
    }
    high = {
        "dbzh": np.full((2, 4), np.nan, dtype="float32"),
        "azimuth": np.array([0.2, 180.2], dtype="float32"),
        "range": np.array([1_000.0, 2_000.0, 3_000.0, 4_000.0], dtype="float32"),
        "elevation": 1.5,
    }

    extents = _higher_elevation_radial_extent_fractions(
        (low, high),
        strict_observability=True,
    )

    assert np.isnan(extents[0]).all()


def test_vertical_consistency_is_unavailable_when_upper_sweep_is_missing() -> None:
    low = {
        "dbzh": np.array([[30.0, 25.0], [20.0, 15.0]], dtype="float32"),
        "azimuth": np.array([0.0, 180.0], dtype="float32"),
        "range": np.array([1_000.0, 2_000.0], dtype="float32"),
        "elevation": 0.5,
    }
    high = {
        "dbzh": np.full((2, 2), np.nan, dtype="float32"),
        "azimuth": np.array([0.2, 180.2], dtype="float32"),
        "range": np.array([1_000.0, 2_000.0], dtype="float32"),
        "elevation": 1.5,
    }

    probabilities = _vertical_consistency_probabilities(
        (low, high),
        minimum_dbzh=10.0,
        support_tolerance_db=12.0,
        maximum_range_m=100_000.0,
        strict_observability=True,
        maximum_azimuth_offset_deg=0.75,
    )

    assert np.isnan(probabilities[0]).all()


def test_vertical_consistency_is_unavailable_beyond_upper_range_coverage() -> None:
    low = {
        "dbzh": np.array([[30.0, 25.0]], dtype="float32"),
        "azimuth": np.array([0.0], dtype="float32"),
        "range": np.array([50_000.0, 150_000.0], dtype="float32"),
        "elevation": 0.5,
    }
    high = {
        "dbzh": np.array([[28.0, 28.0]], dtype="float32"),
        "azimuth": np.array([0.2], dtype="float32"),
        "range": np.array([10_000.0, 50_000.0], dtype="float32"),
        "elevation": 1.5,
    }

    probabilities = _vertical_consistency_probabilities(
        (low, high),
        minimum_dbzh=10.0,
        support_tolerance_db=12.0,
        maximum_range_m=200_000.0,
        strict_observability=True,
        maximum_azimuth_offset_deg=0.75,
    )

    assert np.isnan(probabilities[0][0, 1])


def test_v2_residual_cannot_inherit_an_edge_expansion_seed() -> None:
    profile = load_qc_profile(V2_QC_CONFIG, FLAG_CONFIG)
    ranges = (np.arange(920, dtype="float32") + 1) * 500
    values = np.full((360, 920), np.nan, dtype="float32")
    values[:, :120] = 18
    values[10] = np.linspace(46, 66, 920)
    for ray in (11, 12):
        values[ray, :680] = 20 * np.log10(np.arange(1, 681)) + 1
    values[13, 500:600] = 20 * np.log10(np.arange(501, 601)) + 1
    detection = _detect_radial_interference(
        values,
        np.isfinite(values),
        profile.radial_interference,
        ranges_m=ranges,
        azimuth_deg=np.arange(360),
    )
    assert np.all(detection.probability[10] >= profile.radial_interference.flag_probability)
    assert not np.any(detection.probability[13] >= profile.radial_interference.flag_probability)


def test_temporal_persistence_requires_observed_angular_support() -> None:
    from rainpulse_algo.radar.qc import _temporal_radial_persistence

    history = (np.array([180.0, 181.0, 182.0, 183.0]), np.ones(4, bool), np.ones(4, bool))
    result = _temporal_radial_persistence(
        np.array([0.0, 1.0, 2.0, np.nan]),
        (history, history),
        minimum_context_scans=2,
        maximum_context_scans=3,
    )
    assert np.isnan(result).all()


def test_v2_cross_support_protects_all_expansion_gates() -> None:
    profile = load_qc_profile(V2_QC_CONFIG, FLAG_CONFIG)
    ranges = (np.arange(920, dtype="float32") + 1) * 500
    values = np.full((360, 920), np.nan, dtype="float32")
    values[:, :120] = 18
    values[10] = np.linspace(46, 66, 920)
    for ray in (11, 12):
        values[ray, :680] = 20 * np.log10(np.arange(1, 681)) + 1
    detection = _detect_radial_interference(
        values,
        np.isfinite(values),
        profile.radial_interference,
        ranges_m=ranges,
        azimuth_deg=np.arange(360),
        cross_radar_consistency=np.ones(360, dtype="float32"),
    )
    hard = detection.probability >= profile.radial_interference.flag_probability
    assert np.count_nonzero(hard) == 920
    assert np.all(hard[10])
    assert detection.decision_summary["strong_seed_override_gate_count"] == 920


def test_temporal_missing_history_does_not_increase_denominator() -> None:
    from rainpulse_algo.radar.qc import _temporal_radial_persistence

    nearby = (np.array([359.5, 90.0]), np.array([True, False]), np.array([True, True]))
    absent = (np.array([np.nan, np.nan]), np.ones(2, bool), np.ones(2, bool))
    result = _temporal_radial_persistence(
        np.array([0.0, 90.0]),
        (nearby, absent, nearby),
        minimum_context_scans=2,
        maximum_context_scans=3,
    )
    np.testing.assert_array_equal(result, [1.0, 0.0])
