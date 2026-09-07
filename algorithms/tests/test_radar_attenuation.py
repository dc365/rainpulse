from __future__ import annotations

from datetime import UTC, datetime
from pathlib import Path

import numpy as np
import pytest

from rainpulse_algo.radar.attenuation import (
    AttenuationApplicabilityConfig,
    AttenuationCoefficientConfig,
    AttenuationProfile,
    build_attenuation_artifact,
    load_attenuation_profile,
    process_kdp_attenuation_ray,
)

REPOSITORY_ROOT = Path(__file__).resolve().parents[2]
PROFILE_PATH = (
    REPOSITORY_ROOT / "configs" / "verification" / "fujian-kdp-attenuation-shadow-v1.yaml"
)


def _range_m(count: int, spacing_m: float = 1_000.0) -> np.ndarray:
    return (np.arange(count, dtype="float32") + 1.0) * spacing_m


def _configured_profile(
    *,
    coefficient_a: float = 0.04,
    exponent_b: float = 1.0,
    minimum_kdp_deg_per_km: float = 0.0,
    maximum_specific_attenuation_db_per_km: float = 5.0,
    maximum_correction_db: float = 20.0,
    maximum_blockage_fraction: float = 0.30,
    corrected_reflectivity_cap_dbz: float = 75.0,
) -> AttenuationProfile:
    return AttenuationProfile(
        profile_version="test-kdp-attenuation-shadow-v1",
        artifact_contract_version="1.0",
        source_normalized_radar_volume_contract_version="1.0",
        source_phase_processing_profile_version="fujian-phidp-kdp-shadow-v1",
        radar_band="S",
        method_name="kdp_path_integral",
        integration_scheme="two_way_trapezoidal",
        coefficients=AttenuationCoefficientConfig(
            source="explicit_shadow_coefficients",
            coefficient_a=coefficient_a,
            exponent_b=exponent_b,
            minimum_kdp_deg_per_km=minimum_kdp_deg_per_km,
            maximum_specific_attenuation_db_per_km=(maximum_specific_attenuation_db_per_km),
            maximum_correction_db=maximum_correction_db,
        ),
        applicability=AttenuationApplicabilityConfig(
            maximum_blockage_fraction=maximum_blockage_fraction,
            corrected_reflectivity_cap_dbz=corrected_reflectivity_cap_dbz,
            unavailable_segment_policy="split_and_reset",
        ),
        shadow_processing_enabled=True,
        worker_integration_enabled=False,
        required_gate=(
            "verified_s_band_attenuation_coefficients_and_"
            "independent_calibration_reference_required"
        ),
    )


def test_attenuation_profile_is_frozen_for_shadow_only_c2a() -> None:
    profile = load_attenuation_profile(PROFILE_PATH)

    assert profile.profile_version == "fujian-kdp-attenuation-shadow-v1"
    assert profile.radar_band == "S"
    assert profile.method_name == "kdp_path_integral"
    assert profile.integration_scheme == "two_way_trapezoidal"
    assert profile.coefficients.source == "unconfigured"
    assert profile.coefficients.coefficient_a is None
    assert profile.coefficients.exponent_b is None
    assert profile.shadow_processing_enabled is True
    assert profile.worker_integration_enabled is False


def test_zero_kdp_preserves_reflectivity_with_zero_correction() -> None:
    profile = _configured_profile()
    range_m = _range_m(6)
    dbzh = np.asarray([18.0, 22.0, 25.0, 30.0, 33.0, 35.0], dtype="float32")
    kdp = np.zeros(range_m.shape, dtype="float32")

    result = process_kdp_attenuation_ray(
        dbzh, kdp, range_m, profile=profile, blockage_fraction=np.zeros(dbzh.shape)
    )

    np.testing.assert_allclose(result.corrected_dbzh_dbz, dbzh)
    np.testing.assert_allclose(result.attenuation_correction_db, 0.0)
    np.testing.assert_allclose(result.specific_attenuation_db_per_km, 0.0)
    assert np.count_nonzero(result.available_mask) == len(range_m)


def test_constant_kdp_integrates_two_way_attenuation_correction() -> None:
    profile = _configured_profile(coefficient_a=0.04, exponent_b=1.0)
    range_m = _range_m(5)
    dbzh = np.full(range_m.shape, 35.0, dtype="float32")
    kdp = np.full(range_m.shape, 2.0, dtype="float32")

    result = process_kdp_attenuation_ray(
        dbzh, kdp, range_m, profile=profile, blockage_fraction=np.zeros(dbzh.shape)
    )

    expected_specific = np.full(range_m.shape, 0.08, dtype="float32")
    expected_correction = np.asarray([0.0, 0.16, 0.32, 0.48, 0.64], dtype="float32")
    np.testing.assert_allclose(result.specific_attenuation_db_per_km, expected_specific)
    np.testing.assert_allclose(result.attenuation_correction_db, expected_correction)
    np.testing.assert_allclose(result.corrected_dbzh_dbz, dbzh + expected_correction)


def test_missing_kdp_gap_splits_segments_without_cross_gap_accumulation() -> None:
    profile = _configured_profile(coefficient_a=0.05)
    range_m = _range_m(8)
    dbzh = np.full(range_m.shape, 30.0, dtype="float32")
    kdp = np.asarray([1.0, 1.0, 1.0, np.nan, np.nan, 1.0, 1.0, 1.0], dtype="float32")

    result = process_kdp_attenuation_ray(
        dbzh, kdp, range_m, profile=profile, blockage_fraction=np.zeros(dbzh.shape)
    )

    assert np.array_equal(result.available_mask[3:5], np.zeros(2, dtype="uint8"))
    assert result.segment_index[2] != result.segment_index[5]
    assert result.attenuation_correction_db[5] == pytest.approx(0.0)
    assert result.attenuation_correction_db[6] > result.attenuation_correction_db[5]


def test_blockage_threshold_prevents_amplification_and_resets_segments() -> None:
    profile = _configured_profile(coefficient_a=0.05, maximum_blockage_fraction=0.30)
    range_m = _range_m(7)
    dbzh = np.full(range_m.shape, 28.0, dtype="float32")
    kdp = np.full(range_m.shape, 1.5, dtype="float32")
    blockage = np.asarray([0.0, 0.0, 0.45, 0.45, 0.0, 0.0, 0.0], dtype="float32")

    result = process_kdp_attenuation_ray(
        dbzh,
        kdp,
        range_m,
        profile=profile,
        blockage_fraction=blockage,
    )

    assert np.array_equal(result.available_mask[2:4], np.zeros(2, dtype="uint8"))
    assert result.segment_index[1] != result.segment_index[4]
    assert result.attenuation_correction_db[4] == pytest.approx(0.0)
    assert np.isnan(result.corrected_dbzh_dbz[2])
    assert np.isnan(result.corrected_dbzh_dbz[3])


def test_unconfigured_coefficients_keep_fail_closed_unavailable_fields() -> None:
    profile = load_attenuation_profile(PROFILE_PATH)
    range_m = _range_m(6)
    dbzh = np.full(range_m.shape, 32.0, dtype="float32")
    kdp = np.full(range_m.shape, 1.0, dtype="float32")

    result = process_kdp_attenuation_ray(
        dbzh, kdp, range_m, profile=profile, blockage_fraction=np.zeros(dbzh.shape)
    )

    assert np.count_nonzero(result.available_mask) == 0
    assert np.isnan(result.attenuation_correction_db).all()
    assert np.isnan(result.corrected_dbzh_dbz).all()
    assert result.diagnostics["skip_reasons"] == ["attenuation_coefficients_unconfigured"]


def test_correction_and_reflectivity_are_capped() -> None:
    profile = _configured_profile(
        coefficient_a=0.4,
        exponent_b=1.0,
        maximum_specific_attenuation_db_per_km=3.0,
        maximum_correction_db=0.25,
        corrected_reflectivity_cap_dbz=60.0,
    )
    range_m = _range_m(4)
    dbzh = np.full(range_m.shape, 59.9, dtype="float32")
    kdp = np.full(range_m.shape, 8.0, dtype="float32")

    result = process_kdp_attenuation_ray(
        dbzh, kdp, range_m, profile=profile, blockage_fraction=np.zeros(dbzh.shape)
    )

    assert np.nanmax(result.attenuation_correction_db) == pytest.approx(0.25)
    assert np.nanmax(result.corrected_dbzh_dbz) == pytest.approx(60.0)
    assert result.diagnostics["capped_gate_count"] > 0


def test_attenuation_artifact_records_shadow_only_provenance() -> None:
    profile = _configured_profile(coefficient_a=0.04, exponent_b=1.0)
    range_m = _range_m(5)
    dbzh = np.full(range_m.shape, 35.0, dtype="float32")
    kdp = np.full(range_m.shape, 2.0, dtype="float32")
    result = process_kdp_attenuation_ray(
        dbzh, kdp, range_m, profile=profile, blockage_fraction=np.zeros(dbzh.shape)
    )

    artifact = build_attenuation_artifact(
        {"sweep_000": result},
        profile=profile,
        artifact_id="50000000-0000-4000-8000-000000000301",
        created_at_utc=datetime(2026, 9, 6, tzinfo=UTC),
        radar_id="z9598",
        scan_id="10000000-0000-4000-8000-000000000301",
        source_normalized_uri="s3://rainpulse/radar/normalized/z9598/example/volume.zarr",
        source_manifest_sha256="b" * 64,
        kdp_input_unit="degree/km",
    )

    assert artifact["artifact_contract_version"] == "1.0"
    assert artifact["profile_version"] == "test-kdp-attenuation-shadow-v1"
    assert artifact["operational_eligible"] is False
    assert artifact["source_input"]["kdp_input_unit"] == "degree/km"
    assert artifact["sweeps"]["sweep_000"]["available_gate_count"] > 0


def test_missing_blockage_cannot_produce_attenuation_correction() -> None:
    result = process_kdp_attenuation_ray(
        np.full(5, 30.0), np.ones(5), _range_m(5), profile=_configured_profile()
    )
    assert not np.any(result.available_mask)
    assert np.isnan(result.corrected_dbzh_dbz).all()
    assert "blockage_unavailable" in result.diagnostics["skip_reasons"]
