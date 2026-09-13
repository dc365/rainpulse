"""Synthetic mechanisms, NOT real screenshot acceptance or calibrated RFI skill."""

from __future__ import annotations

from dataclasses import replace

import numpy as np
import pytest

from rainpulse_algo.radar.qc import apply_basic_qc, load_qc_profile
from rainpulse_algo.radar.qc_engine.adapters import adapt_sweep
from rainpulse_algo.radar.qc_engine.algorithms import library_evidence
from rainpulse_algo.radar.qc_engine.audit import audit_sweep
from rainpulse_algo.radar.qc_engine.decision import decide
from rainpulse_algo.radar.qc_engine.multivariate import joint_moment_evidence
from rainpulse_algo.radar.qc_engine.objects import objects_for_profile
from rainpulse_algo.radar.qc_engine.objects_v3 import bounded_search
from rainpulse_algo.radar.qc_engine.profile import OpenSourceQCProfile
from rainpulse_algo.radar.qc_engine.refinement import V3Path, temporal_votes
from rainpulse_algo.radar.qc_input import open_qc_input
from rainpulse_algo.radar.qc_zarr import build_qc_zarr_store, validate_qc_zarr_store

from .test_radar_qc import synthetic_normalized_fixture
from .test_rfi_objects_v2 import FLAGS, ROOT, scene

PROFILE = ROOT / "configs/qc/fujian-qc-rfi-multivariate-v3.yaml"


def profile():
    return load_qc_profile(PROFILE, FLAGS)


def make_native(*, noise=0, phase_noise=False, zdr=1, **kwargs):
    obj, _ = scene(**kwargs)
    native = adapt_sweep(open_qc_input(obj).root, "sweep_000", profile())
    fields = {k: v.copy() for k, v in native.fields.items()}
    footprint = np.zeros(native.shape, bool)
    footprint[100 : 101 if kwargs.get("thin") else 130, 80:500] = True
    if kwargs.get("other_directions"):
        footprint[200:215, 80:500] = footprint[300:330, 80:500] = True
    target = footprint & native.field_available["DBZH"]
    rng = np.random.default_rng(20260913)
    fields["DBZH"][target] += rng.normal(0, noise, target.sum())
    if "ZDR" in fields:
        fields["ZDR"][target] = zdr
    if phase_noise:
        fields["PHIDP"][target] = rng.uniform(0, 360, target.sum())
    # Freeze domain on ORIGINAL echoes, not on detector-selected candidates.
    target &= fields["DBZH"] >= profile().rfi_objects.minimum_echo_dbz
    return replace(native, fields=fields), target


def run_native(native, configured=None, **kwargs):
    configured = configured or profile()
    evidence = library_evidence(native, configured)
    objects = objects_for_profile(native, configured)
    result = decide(native, evidence, configured, object_evidence=objects, **kwargs)
    return objects, result


@pytest.mark.parametrize("noise", [0, 5, 8])
@pytest.mark.parametrize("missing_background", [False, True])
def test_rough_or_smooth_fans_do_not_require_low_axial_standard_deviation(
    noise, missing_background
):
    native, domain = make_native(noise=noise, missing_background=missing_background)
    objects, result = run_native(native)
    assert objects.records
    assert np.mean(result.arrays["QPE_ELIGIBLE_MASK"][domain] == 0) > 0.99
    assert np.mean(result.arrays["RFI_RISK_STATE"][domain] == 3) > 0.96
    if noise == 8:
        assert objects.arrays["RFI_ROUGH_CANDIDATE_MASK"][domain].any()


@pytest.mark.parametrize(
    "kwargs", [dict(thin=True), dict(dropouts=True), dict(other_directions=True)]
)
def test_thin_discontinuous_and_multi_direction_domains(kwargs):
    native, domain = make_native(**kwargs)
    _, result = run_native(native)
    assert np.mean(result.arrays["QPE_ELIGIBLE_MASK"][domain] == 0) > 0.99
    missing = ~native.field_available["DBZH"]
    assert not result.arrays["RFI_OBJECT_ID"][missing].any()
    assert np.isnan(result.arrays["DBZH_USABLE"][missing]).all()


@pytest.mark.parametrize("rho", [0.92, 0.97, 0.99])
def test_high_rho_joint_phase_zdr_anomaly_is_not_unconditionally_protected(rho):
    native, target = make_native(rho=rho, phase_noise=True, zdr=8)
    _, result = run_native(native)
    assert np.mean(result.arrays["RFI_RISK_STATE"][target] == 3) > 0.90
    assert result.arrays["RFI_V3_JOINT_CONFIRMED_MASK"][target].any()


@pytest.mark.parametrize("phase_noise,zdr", [(False, 1), (True, 1), (False, 8)])
def test_single_polarimetric_abnormality_is_not_a_high_rho_hard_cause(phase_noise, zdr):
    native, target = make_native(rho=0.99, phase_noise=phase_noise, zdr=zdr)
    _, result = run_native(native)
    assert not (result.arrays["RFI_RISK_STATE"][target] == 3).any()
    if not phase_noise and zdr == 1:
        assert not result.arrays["RFI_QUARANTINE_MASK"][target].any()
        assert result.arrays["QPE_ELIGIBLE_MASK"][target].all()


@pytest.mark.parametrize("slope", [0.2, 5, 20])
def test_phase_branch_cut_and_smooth_strong_gradient_are_not_noise(slope):
    native, target = make_native(rho=0.99)
    fields = dict(native.fields)
    ramp = (358 + np.arange(native.shape[1]) * slope) % 360
    fields["PHIDP"] = np.broadcast_to(ramp, native.shape).astype("float32").copy()
    native = replace(native, fields=fields)
    ev = joint_moment_evidence(native, profile())
    assert not ev["RFI_PHASE_ANOMALY_MASK"].any()
    supported = ev["RFI_PHASE_PAIR_AVAILABLE_MASK"] == 1
    assert np.nanmax(ev["RFI_PHASE_CURVATURE_DEG"][supported]) < 0.001
    _, result = run_native(native)
    assert result.arrays["QPE_ELIGIBLE_MASK"][target].all()


def test_phase_constant_offset_and_period_shift_invariance():
    native, _ = make_native(rho=0.97, phase_noise=True, zdr=8)
    first = joint_moment_evidence(native, profile())
    fields = dict(native.fields)
    fields["PHIDP"] = fields["PHIDP"] + 720
    other = joint_moment_evidence(replace(native, fields=fields), profile())
    np.testing.assert_array_equal(first["RFI_PHASE_ANOMALY_MASK"], other["RFI_PHASE_ANOMALY_MASK"])
    np.testing.assert_allclose(
        first["RFI_PHASE_CURVATURE_DEG"], other["RFI_PHASE_CURVATURE_DEG"], atol=0.001
    )


def test_phase_stencil_never_uses_values_across_a_missing_gap():
    native, _ = make_native(rho=0.97, phase_noise=True, zdr=8)
    available = {k: v.copy() for k, v in native.field_available.items()}
    available["PHIDP"][:, 300] = False
    native = replace(native, field_available=available)
    ev = joint_moment_evidence(native, profile())
    assert not ev["RFI_PHASE_PAIR_AVAILABLE_MASK"][:, 297:304].any()
    assert np.isnan(ev["RFI_PHASE_NOISE_FRACTION"][:, 294:307]).all()


@pytest.mark.parametrize("snr", [1, None])
def test_missing_or_low_snr_never_confirms_joint_rfi(snr):
    native, target = make_native(rho=0.97, phase_noise=True, zdr=8, snr=1 if snr == 1 else 25)
    if snr is None:
        available = dict(native.field_available)
        available["SNR"] = np.zeros(native.shape, bool)
        native = replace(native, field_available=available)
    _, result = run_native(native)
    assert not (result.arrays["RFI_RISK_STATE"][target] == 3).any()
    assert result.arrays["RFI_QUARANTINE_MASK"][target].any()
    assert not np.any(result.flags[result.arrays["RFI_QUARANTINE_MASK"] == 1] & (1 << 15))


def test_confirmed_pollution_is_not_restored_by_weather_support():
    native, target = make_native(rho=0.97, phase_noise=True, zdr=8)
    _, result = run_native(native, weather_support=np.ones(native.shape))
    assert result.arrays["RFI_MIXED_MASK"][target].any()
    assert result.arrays["RFI_V3_JOINT_CONFIRMED_MASK"][target].any()


def test_independent_periphery_finds_a_short_neighbour_outside_original_object():
    native, _ = make_native(thin=True)
    fields = {k: v.copy() for k, v in native.fields.items()}
    available = {k: v.copy() for k, v in native.field_available.items()}
    for key in fields:
        fields[key][101, 220:240] = fields[key][100, 220:240]
        available[key][101, 220:240] = True
    native = replace(native, fields=fields, field_available=available)
    objects, result = run_native(native)
    assert not objects.arrays["RFI_STRUCTURAL_SEED_MASK"][101, 230]
    assert objects.arrays["RFI_PERIPHERY_MASK"][101, 230]
    assert result.arrays["RFI_V3_DECISION_PATH"][101, 230] == V3Path.BOUNDED_PERIPHERY


def test_bounded_search_stops_at_missing_clear_air_and_weather_barrier():
    native, _ = make_native(thin=True)
    seed = np.zeros(native.shape, "uint32")
    seed[100, 100] = 1
    allowed = np.zeros(native.shape, bool)
    allowed[100, 100:140] = True
    allowed[100, 105] = False
    result = bounded_search(native, seed, allowed, 4000, 0, 128)
    assert result[100, 104]
    assert not result[100, 105:].any()
    allowed[100, 105] = True
    result = bounded_search(native, seed, allowed, 1000, 0, 128)
    assert not result[100, 105:].any()
    with pytest.raises(ValueError, match="budget"):
        bounded_search(native, seed, allowed, 4000, 2, 1)


def test_exact_two_of_three_votes_and_corruption_detection():
    p = profile()
    count, fraction, votes, accepted = temporal_votes(
        (2, 2), np.full((2, 2), 2 / 3), np.full((2, 2), 3), p
    )
    assert accepted.all() and (votes == 2).all()
    for persistence, samples in [(0.5, 3), (np.nan, 2), (1.1, 2), (0.5, 1.5)]:
        with pytest.raises(ValueError):
            temporal_votes((2, 2), np.full((2, 2), persistence), np.full((2, 2), samples), p)
    _, _, _, accepted = temporal_votes((2, 2), np.ones((2, 2)), np.ones((2, 2)), p)
    assert not accepted.any()


def test_temporal_support_requires_corroborating_measurements():
    native, target = make_native(rho=0.88, phase_noise=True)
    count = np.full(native.shape, 3, "uint8")
    _, low = run_native(native, temporal_samples=count, temporal_persistence=np.zeros(native.shape))
    _, high = run_native(
        native, temporal_samples=count, temporal_persistence=np.full(native.shape, 2 / 3)
    )
    assert not low.arrays["RFI_TEMPORAL_USED_MASK"].any()
    assert high.arrays["RFI_TEMPORAL_USED_MASK"][target].any()
    quiet, _ = make_native(rho=0.88)
    _, result = run_native(
        quiet, temporal_samples=count, temporal_persistence=np.ones(native.shape)
    )
    assert not result.arrays["RFI_TEMPORAL_USED_MASK"].any()


def test_disabled_context_cannot_promote_repeated_hypotheses():
    p = profile()
    p = p.model_copy(update={"context": p.context.model_copy(update={"enabled": False})})
    _, _, _, accepted = temporal_votes((1, 1), np.ones((1, 1)), np.full((1, 1), 3), p)
    assert not accepted.any()


def test_v1_v2_semantic_identities_remain_frozen():
    expected = {
        "fujian-qc-opensource-v1.yaml": (
            "7de1109ac5b749ee34fcd50ed220d80ac59df6b0da6df8820420e96e091efd0f"
        ),
        "fujian-qc-rfi-objects-v2.yaml": (
            "357d3085e3cfa40c0577fc9538f3c9e562ba23fa22bc4029751747399e809720"
        ),
    }
    for filename, sha in expected.items():
        p = load_qc_profile(ROOT / "configs/qc" / filename, FLAGS)
        assert p.parameters_hash == sha
        assert "rfi_refinement" not in p.model_dump(mode="json")


@pytest.mark.parametrize(
    "changes",
    [
        {"pipeline_version": "qc-opensource-2.0.0"},
        {"rfi_refinement": None},
        {"rfi_refinement": {"search_range_m": 0, "confirm_range_m": 100}},
        {"rfi_refinement": {"temporal_vote_numerator": 1, "temporal_vote_denominator": 3}},
        {"rfi_refinement": {"station_id": "z9598"}},
    ],
)
def test_invalid_or_mixed_v3_profile_is_rejected(changes):
    data = profile().model_dump(mode="json")
    data.update(changes)
    with pytest.raises(ValueError):
        OpenSourceQCProfile.model_validate(data)


def test_roundtrip_and_repeatability_preserve_raw_and_native_masks(tmp_path):
    native, _ = make_native(rho=0.97, phase_noise=True, zdr=8)
    obj = synthetic_normalized_fixture(
        native.fields["DBZH"],
        range_m=native.ranges,
        moments={k: v for k, v in native.fields.items() if k != "DBZH"},
    )
    before = dict(obj)
    result = apply_basic_qc(obj, profile())
    artifact = build_qc_zarr_store(obj, result, asset_id="v3", normalized_volume_uri="local")
    assert validate_qc_zarr_store(artifact)["valid_gate_count"] > 0
    assert obj == before
    assert "rfi_v3_audit" in result.summary["sweeps"]["sweep_000"]


def test_audit_domain_is_fixed_even_if_candidate_shrinks_to_nothing():
    native, _ = make_native()
    _, result = run_native(native)
    one = audit_sweep(
        native.fields["DBZH"], native.field_available["DBZH"], result.arrays, native.ranges
    )
    arrays = dict(result.arrays)
    arrays["RFI_OBJECT_ID"] = np.zeros(native.shape, "uint32")
    two = audit_sweep(native.fields["DBZH"], native.field_available["DBZH"], arrays, native.ranges)
    assert one["original_domain_gates"] == two["original_domain_gates"]
    assert "NOT a residual RFI truth" in one["interpretation"]
