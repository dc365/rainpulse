"""Analytic / adversarial algorithm checks; no real-weather acceptance claims."""

from __future__ import annotations

import hashlib
import json
from dataclasses import replace
from pathlib import Path

import numpy as np
import pytest

from rainpulse_algo.radar.qc import load_qc_profile
from rainpulse_algo.radar.qc_engine.adapters import NativeSweep
from rainpulse_algo.radar.qc_engine.afl import PaperEvidence, afl_evidence, membership, shifted
from rainpulse_algo.radar.qc_engine.algorithms import EvidenceSet
from rainpulse_algo.radar.qc_engine.decision import Action, Decision
from rainpulse_algo.radar.qc_engine.paper_fusion import fuse_paper_decision
from rainpulse_algo.radar.qc_engine.paper_profile import AFLConfig, Curve, LiteratureConfig
from rainpulse_algo.radar.qc_engine.profile import OpenSourceQCProfile
from rainpulse_algo.radar.qc_engine.rdd_reference import (
    RDD_DOI,
    geometry_sha256,
    load_rdd_reference,
    unavailable_rdd,
)

ROOT = Path(__file__).resolve().parents[2]
FLAGS = ROOT / "configs/qc/flag-definitions-v2.yaml"
V3 = ROOT / "configs/qc/fujian-qc-rfi-objects-v3.yaml"
V4 = ROOT / "configs/qc/fujian-qc-paper-fusion-v4.yaml"


def config():
    return load_qc_profile(V4, FLAGS)


def native(z=None, *, rho=0.99, zdr=1.0, phase=None, snr=25.0):
    ranges = (np.arange(160) + 1) * 1000.0
    z = np.full((2, 160), 25.0) if z is None else np.broadcast_to(z, (2, 160)).copy()
    fields = {
        "DBZH": z.astype("float32"),
        "RHOHV": np.full(z.shape, rho, "float32"),
        "ZDR": np.full(z.shape, zdr, "float32"),
        "PHIDP": np.broadcast_to(20.0 if phase is None else phase, z.shape).astype("float32"),
        "SNR": np.full(z.shape, snr, "float32"),
    }
    return NativeSweep(
        "sweep_000",
        np.array([20.0, 21.0]),
        np.array([0.5, 0.5]),
        ranges,
        np.zeros(2),
        fields,
        {k: np.isfinite(v) for k, v in fields.items()},
        np.arange(2),
        False,
        np.ones(2, bool),
        np.array([False, True]),
        {},
        {"azimuth_spacing_deg": 1.0},
    )


def evaluate(
    n, *, weather=None, persistence=None, samples=None, old_reject=False, old_quarantine=False
):
    cfg = config()
    observed = n.field_available["DBZH"]
    arrays = {
        "QC_ACTION": np.where(observed, Action.KEEP, Action.MISSING).astype("uint8"),
        "QC_DECISION_REASON": np.zeros(n.shape, "uint16"),
        "REFLECTIVITY_TRUST_MASK": observed.astype("uint8"),
        "QPE_ELIGIBLE_MASK": observed.astype("uint8"),
        "DBZH_USABLE": n.fields["DBZH"].copy(),
        "RFI_QUARANTINE_MASK": np.zeros(n.shape, "uint8"),
        "RFI_RISK_STATE": np.zeros(n.shape, "uint8"),
        "RFI_MIXED_MASK": np.zeros(n.shape, "uint8"),
    }
    for f in ("RHOHV", "ZDR", "PHIDP", "VR", "SW", "SNR"):
        arrays[f + "_TRUST_MASK"] = observed.astype("uint8")
    flags = np.zeros(n.shape, "uint32")
    quality = np.where(observed, 1.0, np.nan).astype("float32")
    if old_reject:
        arrays["QC_ACTION"][observed] = Action.REJECT
        flags[observed] = cfg.flag_masks["NON_METEOROLOGICAL"]
        quality[observed] = 0
    if old_quarantine:
        arrays["QC_ACTION"][observed] = Action.DOWNWEIGHT
        arrays["RFI_QUARANTINE_MASK"][observed] = 1
        arrays["RFI_RISK_STATE"][observed] = 2
        quality[observed] = 0.25
    if old_reject or old_quarantine:
        arrays["REFLECTIVITY_TRUST_MASK"][:] = 0
        arrays["QPE_ELIGIBLE_MASK"][:] = 0
    base = Decision(arrays, flags, quality)
    before = {k: v.copy() for k, v in arrays.items()}
    ev = EvidenceSet(
        {
            "OS_POL_RAW_MOMENT_COUNT": sum(
                n.field_available[k].astype("uint8") for k in ("RHOHV", "ZDR", "PHIDP")
            )
        },
        (),
        {},
    )
    candidate = observed.astype("uint8")
    papers = PaperEvidence(
        {
            "AFL_CANDIDATE_MASK": candidate,
            "AFL_LOCAL_CANDIDATE_MASK": candidate,
            "RDD_CANDIDATE_MASK": candidate,
        },
        {},
    )
    result = fuse_paper_decision(
        n,
        ev,
        base,
        papers,
        cfg,
        weather_support=weather,
        temporal_persistence=persistence,
        temporal_samples=samples,
    )
    for key, value in before.items():
        np.testing.assert_array_equal(base.arrays[key], value)
    return result


def test_afl_adjacent_difference_formula_is_not_standard_deviation():
    result = afl_evidence(native(np.arange(160) * 0.5), AFLConfig()).arrays
    np.testing.assert_allclose(result["AFL_T_DBZ_DB2"][:, 6:-6], 0.25)
    assert np.isnan(result["AFL_T_DBZ_DB2"][:, :5]).all()
    assert np.isnan(result["AFL_T_DBZ_DB2"][:, -6:]).all()


def test_afl_signed_delta_and_range_km():
    n = native(10 + 20 * np.log10(np.arange(1, 161)))
    result = afl_evidence(n, AFLConfig()).arrays
    np.testing.assert_allclose(result["AFL_D_B_DB"], 0, atol=3e-6)
    varying = afl_evidence(native(np.arange(160) * 0.2), AFLConfig()).arrays
    assert np.nanmin(varying["AFL_D_B_DB"]) < 0


@pytest.mark.parametrize("jump,expected", [(1, 0), (2, 0), (3, 11)])
def test_spin_counts_exact_eleven_triples_strict_threshold(jump, expected):
    fields = afl_evidence(native(np.arange(160) * jump), AFLConfig()).arrays
    np.testing.assert_array_equal(fields["AFL_S_PIN"][:, 6:-6], expected)


def test_missing_tail_is_unavailable_not_zero_score():
    z = np.full(160, 20.0)
    z[-16:] = np.nan
    result = afl_evidence(native(z), AFLConfig()).arrays
    assert not result["AFL_AVAILABLE_MASK"].any()
    assert np.isnan(result["AFL_SCORE"]).all()
    assert not result["AFL_CANDIDATE_MASK"].any()
    np.testing.assert_allclose(result["AFL_R_REF_PCT"][:, :-16], 90)


def test_local_adaptation_can_operate_when_far_tail_is_unavailable():
    z = 10 + 20 * np.log10(np.arange(1, 161))
    z[-20:] = np.nan
    ev = afl_evidence(native(z), AFLConfig()).arrays
    assert ev["AFL_LOCAL_AVAILABLE_MASK"][:, 50:100].all()
    assert not ev["AFL_AVAILABLE_MASK"].any()
    assert not ev["AFL_LOCAL_AVAILABLE_MASK"][:, :25].any()


def test_hole_invalidates_stencil_without_inventing_observations():
    z = np.full(160, 20.0)
    z[80] = np.nan
    n = native(z)
    before = {k: v.copy() for k, v in n.fields.items()}
    ev = afl_evidence(n, AFLConfig()).arrays
    assert not ev["AFL_TEXTURE_AVAILABLE_MASK"][:, 74:86].any()
    assert not ev["AFL_SPIN_AVAILABLE_MASK"][:, 74:87].any()
    for key in before:
        np.testing.assert_array_equal(n.fields[key], before[key])


def test_membership_and_output_scope():
    np.testing.assert_allclose(
        membership(np.array([-1.0, 0, 1, 2]), Curve(x=(0, 1), y=(0, 1))), [0, 0, 1, 1]
    )
    ev = afl_evidence(native(), AFLConfig())
    assert ev.metadata["author_complete_reproduction"] is False
    assert "SWAN" not in ev.metadata["algorithm"]
    assert ev.metadata["score_semantics"] == "uncalibrated_membership"


@pytest.mark.parametrize(
    "bad", [dict(x=(1, 0), y=(0, 1)), dict(x=(0, 1), y=(0, 2)), dict(x=(0, 1), y=(0,))]
)
def test_membership_rejects_ambiguous_parameters(bad):
    with pytest.raises(ValueError):
        Curve(**bad)


def test_shifting_never_wraps_range_or_creates_phase_pair():
    a = np.array([[1.0, 2, 3]])
    np.testing.assert_array_equal(shifted(a, 1), [[2.0, 3.0, np.nan]])
    np.testing.assert_array_equal(shifted(a, -1), [[np.nan, 1.0, 2.0]])
    np.testing.assert_array_equal(shifted(a, 0), a)


def test_all_missing_support_and_no_fake_candidates():
    result = afl_evidence(native(np.full(160, np.nan)), AFLConfig()).arrays
    assert not result["AFL_AVAILABLE_MASK"].any()
    assert not result["AFL_LOCAL_AVAILABLE_MASK"].any()


def test_agreeing_reflectivity_methods_are_not_independent_rejection_votes():
    result = evaluate(native())
    assert not result.arrays["PAPER_CONFIRMED_ADDITION_MASK"].any()
    assert not result.arrays["RFI_QUARANTINE_MASK"].any()
    assert result.arrays["QPE_ELIGIBLE_MASK"].all()


def test_high_rho_can_be_rejected_with_two_reliable_distinct_moment_anomalies():
    result = evaluate(native(zdr=8.0, phase=np.arange(160) % 2 * 120), weather=np.ones((2, 160)))
    assert result.arrays["PAPER_CONFIRMED_ADDITION_MASK"][:, 20:150].all()
    assert result.arrays["RFI_MIXED_MASK"][:, 20:150].all()
    assert not result.arrays["QPE_ELIGIBLE_MASK"][:, 20:150].any()
    assert not result.arrays["PHIDP_TRUST_MASK"][:, 20:150].any()


@pytest.mark.parametrize("snr", [1.0, np.nan])
def test_unreliable_polarimetry_cannot_confirm(snr):
    result = evaluate(native(zdr=8.0, rho=0.3, phase=np.arange(160) % 2 * 120, snr=snr))
    assert not result.arrays["PAPER_CONFIRMED_ADDITION_MASK"].any()


def test_phase_wrap_is_not_an_anomaly():
    result = evaluate(native(phase=np.arange(160) % 2 * 359, zdr=1.0))
    assert not result.arrays["PAPER_CONFIRMED_ADDITION_MASK"].any()
    assert np.nanmax(result.arrays["PAPER_PHASE_BAD_PAIR_FRACTION"]) == 0


def test_missing_current_phase_cannot_have_phase_evidence():
    p = np.zeros(160)
    p[80] = np.nan
    result = evaluate(native(phase=p))
    assert not result.arrays["PAPER_PHASE_AVAILABLE_MASK"][:, 80].any()


def test_single_low_rho_is_quarantine_not_confirmation():
    result = evaluate(native(rho=0.3))
    assert not result.arrays["PAPER_CONFIRMED_ADDITION_MASK"].any()
    assert result.arrays["PAPER_QUARANTINED_ADDITION_MASK"][:, 20:150].all()
    assert not (result.flags & config().flag_masks["NON_METEOROLOGICAL"]).any()


def test_two_of_three_temporal_votes_are_exact_integer_counts():
    n = native(rho=0.3, snr=np.nan)
    result = evaluate(n, persistence=np.full(n.shape, 2 / 3), samples=np.full(n.shape, 3))
    assert result.arrays["PAPER_TEMPORAL_SUPPORT_MASK"].all()
    assert result.arrays["PAPER_QUARANTINED_ADDITION_MASK"][:, 20:150].all()
    assert not result.arrays["PAPER_CONFIRMED_ADDITION_MASK"].any()


@pytest.mark.parametrize("p,count", [(1.1, 3), (0.7, 4), (0.8, 2.5)])
def test_invalid_temporal_votes_are_not_accepted(p, count):
    with pytest.raises(ValueError, match="temporal vote"):
        evaluate(native(), persistence=np.full((2, 160), p), samples=np.full((2, 160), count))


@pytest.mark.parametrize("kind", ["old_reject", "old_quarantine"])
def test_fusion_never_restores_withheld_v3_measurement(kind):
    result = evaluate(native(), **{kind: True})
    assert not result.arrays["QPE_ELIGIBLE_MASK"].any()
    assert not result.arrays["PAPER_CONFIRMED_ADDITION_MASK"].any()


def test_version_coherence_and_old_parameter_hash():
    p = config()
    data = p.model_dump()
    data["literature"] = None
    with pytest.raises(ValueError, match="versioned"):
        OpenSourceQCProfile.model_validate(data)
    v3 = load_qc_profile(V3, FLAGS)
    data = v3.model_dump(mode="json")
    data.pop("literature")
    data.pop("residual", None)  # Absent V6 extension is not an old parameter.
    data.pop("cross_radar", None)
    digest = hashlib.sha256(
        json.dumps(data, sort_keys=True, separators=(",", ":")).encode()
    ).hexdigest()
    assert v3.parameters_hash == digest
    assert p.literature == LiteratureConfig()


def write_reference(tmp, n, *, changes=None, missing_candidate=False):
    arr = np.zeros((*n.shape, 2), "uint8")
    arr[..., 0] = n.restore(n.field_available["DBZH"].astype("uint8"))
    arr[0, 50, 1] = 1
    if missing_candidate:
        arr[0, 50, 0] = 0
    np.save(tmp / "mask.npy", arr)
    meta = {
        "schema_version": "rainpulse.rdd-reference.v1",
        "algorithm": "RDD",
        "source_doi": RDD_DOI,
        "input_sha256": "a" * 64,
        "geometry_sha256": geometry_sha256(n),
        "sweep": "sweep_000",
        "ordering": "source_ray_gate",
        "granularity": "gate",
        "interpolated": False,
        "implementation_revision": "external-test-not-author-implementation",
        "parameters_sha256": "b" * 64,
        "review_record": "synthetic contract fixture only",
        "mask_file": "mask.npy",
        "mask_sha256": hashlib.sha256((tmp / "mask.npy").read_bytes()).hexdigest(),
    }
    meta.update(changes or {})
    file = tmp / "rdd.json"
    file.write_text(json.dumps(meta))
    return file, hashlib.sha256(file.read_bytes()).hexdigest()


def test_rdd_absence_is_not_a_negative_detection():
    ev = unavailable_rdd((2, 3))
    assert ev.metadata["status"].startswith("not_executed")
    assert not ev.arrays["RDD_AVAILABLE_MASK"].any()
    assert "score" not in ev.metadata


def test_rdd_reference_preserves_native_order_and_full_hash(tmp_path):
    n = replace(native(), original_indices=np.array([1, 0]))
    file, digest = write_reference(tmp_path, n)
    ev = load_rdd_reference(
        file, metadata_sha256=digest, input_sha256="a" * 64, native=n, sweep_name=n.name
    )
    assert ev.arrays["RDD_CANDIDATE_MASK"][1, 50] == 1
    assert ev.arrays["RDD_CANDIDATE_MASK"].sum() == 1
    assert ev.metadata["author_reproduction"] is False


@pytest.mark.parametrize(
    "change",
    [
        {"interpolated": True},
        {"geometry_sha256": "b" * 64},
        {"input_sha256": "c" * 64},
        {"ordering": "sorted"},
        {"granularity": "ray"},
        {"mask_file": "../mask.npy"},
    ],
)
def test_rdd_wrong_lineage_or_granularity_rejected(tmp_path, change):
    n = native()
    f, digest = write_reference(tmp_path, n, changes=change)
    with pytest.raises(ValueError):
        load_rdd_reference(
            f, metadata_sha256=digest, input_sha256="a" * 64, native=n, sweep_name=n.name
        )


def test_rdd_detection_without_support_rejected(tmp_path):
    n = native()
    f, digest = write_reference(tmp_path, n, missing_candidate=True)
    with pytest.raises(ValueError, match="unavailable"):
        load_rdd_reference(
            f, metadata_sha256=digest, input_sha256="a" * 64, native=n, sweep_name=n.name
        )
