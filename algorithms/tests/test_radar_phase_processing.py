from __future__ import annotations

from datetime import UTC, datetime
from pathlib import Path

import numpy as np
import pytest

from rainpulse_algo.radar.phase_processing import (
    build_phase_processing_artifact,
    load_phase_processing_profile,
    process_phidp_ray,
)

REPOSITORY_ROOT = Path(__file__).resolve().parents[2]
PROFILE_PATH = (
    REPOSITORY_ROOT / "configs" / "verification" / "fujian-phidp-kdp-shadow-v1.yaml"
)


def _range_m(count: int, spacing_m: float = 500.0) -> np.ndarray:
    return (np.arange(count, dtype="float32") + 1.0) * spacing_m


def _linear_phidp_deg(range_m: np.ndarray, *, phi0_deg: float, kdp_deg_per_km: float) -> np.ndarray:
    return phi0_deg + 2.0 * kdp_deg_per_km * (range_m / 1000.0)


def test_phase_processing_profile_is_frozen_for_shadow_only_c1a() -> None:
    profile = load_phase_processing_profile(PROFILE_PATH)

    assert profile.profile_version == "fujian-phidp-kdp-shadow-v1"
    assert profile.phase_field_name == "PHIDP"
    assert profile.phase_wrap_period_degrees == pytest.approx(360.0)
    assert profile.shadow_processing_enabled is True
    assert profile.worker_integration_enabled is False


def test_constant_phidp_recovers_zero_kdp() -> None:
    profile = load_phase_processing_profile(PROFILE_PATH)
    range_m = _range_m(31)
    phidp = np.full(range_m.shape, 42.0, dtype="float32")

    result = process_phidp_ray(phidp, range_m, profile=profile)

    assert result.system_phase_deg == pytest.approx(42.0)
    assert np.nanmax(np.abs(result.kdp_deg_per_km[5:-5])) < 1e-6
    assert np.count_nonzero(result.available_mask) == len(range_m)


def test_linear_phidp_recovers_expected_kdp() -> None:
    profile = load_phase_processing_profile(PROFILE_PATH)
    range_m = _range_m(41)
    phidp = _linear_phidp_deg(range_m, phi0_deg=35.0, kdp_deg_per_km=1.5).astype("float32")

    result = process_phidp_ray(phidp, range_m, profile=profile)

    assert np.nanmedian(result.kdp_deg_per_km[6:-6]) == pytest.approx(1.5, abs=0.03)
    assert np.nanmax(result.kdp_uncertainty_deg_per_km[6:-6]) < 0.05


def test_wrapped_phidp_is_unwrapped_within_each_segment() -> None:
    profile = load_phase_processing_profile(PROFILE_PATH)
    range_m = _range_m(36)
    wrapped = np.mod(
        _linear_phidp_deg(range_m, phi0_deg=350.0, kdp_deg_per_km=1.0),
        360.0,
    ).astype("float32")

    result = process_phidp_ray(wrapped, range_m, profile=profile)

    assert np.nanmedian(result.kdp_deg_per_km[6:-6]) == pytest.approx(1.0, abs=0.05)
    assert np.all(np.diff(result.unwrapped_phidp_deg[result.available_mask == 1]) >= -1e-6)


def test_robust_local_fit_limits_single_gate_outlier_impact() -> None:
    profile = load_phase_processing_profile(PROFILE_PATH)
    range_m = _range_m(45)
    phidp = _linear_phidp_deg(range_m, phi0_deg=28.0, kdp_deg_per_km=1.25).astype("float32")
    phidp[20] += 80.0
    phidp += np.asarray(
        [
            0.0,
            0.2,
            -0.3,
            0.1,
            -0.1,
            0.3,
            -0.2,
            0.1,
            0.0,
            -0.1,
            0.2,
            -0.1,
            0.0,
            0.1,
            -0.2,
            0.2,
            -0.1,
            0.0,
            0.1,
            -0.2,
            0.0,
            0.2,
            -0.1,
            0.1,
            -0.1,
            0.2,
            0.0,
            -0.2,
            0.1,
            0.0,
            0.1,
            -0.1,
            0.0,
            0.2,
            -0.2,
            0.1,
            0.0,
            -0.1,
            0.2,
            -0.1,
            0.0,
            0.1,
            -0.2,
            0.2,
            0.0,
        ],
        dtype="float32",
    )

    result = process_phidp_ray(phidp, range_m, profile=profile)

    assert np.nanmedian(result.kdp_deg_per_km[8:-8]) == pytest.approx(1.25, abs=0.12)


def test_nonuniform_gate_spacing_uses_range_coordinates_instead_of_gate_index() -> None:
    profile = load_phase_processing_profile(PROFILE_PATH)
    range_m = np.asarray(
        [
            250.0,
            750.0,
            1250.0,
            2000.0,
            3000.0,
            4500.0,
            6000.0,
            8000.0,
            11000.0,
            14000.0,
            18000.0,
            22000.0,
            26000.0,
        ],
        dtype="float32",
    )
    phidp = _linear_phidp_deg(range_m, phi0_deg=20.0, kdp_deg_per_km=2.0).astype("float32")

    result = process_phidp_ray(phidp, range_m, profile=profile)

    assert np.nanmedian(result.kdp_deg_per_km[3:-3]) == pytest.approx(2.0, abs=0.08)


def test_missing_gap_splits_segments_without_cross_gap_fit() -> None:
    profile = load_phase_processing_profile(PROFILE_PATH)
    range_m = _range_m(24, spacing_m=1000.0)
    phidp = _linear_phidp_deg(range_m, phi0_deg=15.0, kdp_deg_per_km=0.8).astype("float32")
    phidp[10:12] = np.nan

    result = process_phidp_ray(phidp, range_m, profile=profile)

    assert np.array_equal(result.available_mask[10:12], np.zeros(2, dtype="uint8"))
    assert result.segment_index[9] != result.segment_index[12]
    assert result.diagnostics["segment_count"] == 2
    assert np.nanmedian(
        np.concatenate((result.kdp_deg_per_km[4:9], result.kdp_deg_per_km[13:18]))
    ) == pytest.approx(0.8, abs=0.05)


def test_short_segment_stays_unavailable_but_raw_phidp_is_preserved() -> None:
    profile = load_phase_processing_profile(PROFILE_PATH)
    range_m = _range_m(8, spacing_m=1000.0)
    phidp = _linear_phidp_deg(range_m, phi0_deg=22.0, kdp_deg_per_km=1.1).astype("float32")

    result = process_phidp_ray(phidp, range_m, profile=profile)

    np.testing.assert_allclose(result.raw_phidp_deg, phidp)
    assert np.count_nonzero(result.available_mask) == 0
    assert np.isnan(result.kdp_deg_per_km).all()
    assert result.diagnostics["processed_segment_count"] == 0
    assert result.diagnostics["segment_failures"] == ["segment_too_short"]


def test_radian_input_is_converted_before_fitting() -> None:
    profile = load_phase_processing_profile(PROFILE_PATH)
    range_m = _range_m(35)
    phidp_deg = _linear_phidp_deg(range_m, phi0_deg=18.0, kdp_deg_per_km=0.9).astype("float32")
    phidp_rad = np.deg2rad(phidp_deg).astype("float32")

    degree_result = process_phidp_ray(phidp_deg, range_m, profile=profile)
    radian_result = process_phidp_ray(
        phidp_rad,
        range_m,
        profile=profile,
        input_phase_unit="radian",
    )

    np.testing.assert_allclose(radian_result.raw_phidp_deg, phidp_deg, atol=1e-5)
    assert np.nanmedian(radian_result.kdp_deg_per_km[6:-6]) == pytest.approx(
        np.nanmedian(degree_result.kdp_deg_per_km[6:-6]),
        abs=1e-5,
    )


def test_phase_processing_artifact_records_shadow_only_provenance() -> None:
    profile = load_phase_processing_profile(PROFILE_PATH)
    range_m = _range_m(31)
    phidp = _linear_phidp_deg(range_m, phi0_deg=25.0, kdp_deg_per_km=1.0).astype("float32")
    result = process_phidp_ray(phidp, range_m, profile=profile)

    artifact = build_phase_processing_artifact(
        {"sweep_000": result},
        profile=profile,
        artifact_id="50000000-0000-4000-8000-000000000201",
        created_at_utc=datetime(2026, 9, 6, tzinfo=UTC),
        radar_id="z9598",
        scan_id="10000000-0000-4000-8000-000000000201",
        source_normalized_uri="s3://rainpulse/radar/normalized/z9598/example/volume.zarr",
        source_manifest_sha256="a" * 64,
        input_phase_unit="degree",
    )

    assert artifact["artifact_contract_version"] == "1.0"
    assert artifact["profile_version"] == "fujian-phidp-kdp-shadow-v1"
    assert artifact["operational_eligible"] is False
    assert artifact["source_input"]["input_phase_unit"] == "degree"
    assert artifact["sweeps"]["sweep_000"]["available_gate_count"] > 0