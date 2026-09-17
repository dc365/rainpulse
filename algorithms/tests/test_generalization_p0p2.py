"""Mechanism and contract regression; labels are synthetic, not weather truth."""

from copy import deepcopy
from dataclasses import replace
from types import SimpleNamespace

import numpy as np
import pytest

from rainpulse_algo.radar.qc import load_qc_profile
from rainpulse_algo.radar.qc_engine.decision import Action, Decision
from rainpulse_algo.radar.qc_engine.finalize import finalize_decision
from rainpulse_algo.radar.qc_engine.generalization import broad_source_review
from rainpulse_algo.radar.qc_engine.object_consensus.adapter import raw_from_native
from rainpulse_algo.radar.qc_engine.object_consensus.engine import Reason, infer
from rainpulse_algo.radar.qc_engine.quality_policy import health_facets
from rainpulse_algo.radar.qc_engine.range_signature import range_signatures

from .test_crossradar_v5 import FLAGS, ROOT, long_scene

P = ROOT / "configs/qc/fujian-qc-generalization-p0p2.yaml"
OLD = ROOT / "configs/qc/fujian-qc-object-consensus-oc1.yaml"


def profile():
    return load_qc_profile(P, FLAGS)


def keep(n, quality=0.6):
    obs = n.field_available["DBZH"]
    a = {
        "QC_ACTION": np.where(obs, Action.KEEP, Action.MISSING).astype("uint8"),
        "QPE_ELIGIBLE_MASK": obs.astype("uint8"),
        "REFLECTIVITY_TRUST_MASK": obs.astype("uint8"),
        "RFI_QUARANTINE_MASK": np.zeros(n.shape, "uint8"),
        "RFI_RISK_STATE": np.zeros(n.shape, "uint8"),
        "RFI_MIXED_MASK": np.zeros(n.shape, "uint8"),
        "DBZH_USABLE": np.where(obs, n.fields["DBZH"], np.nan).astype("float32"),
    }
    a.update(
        {
            f + "_TRUST_MASK": (obs & n.field_available.get(f, np.zeros(n.shape, bool))).astype(
                "uint8"
            )
            for f in ("RHOHV", "ZDR", "PHIDP", "VR", "SW", "SNR")
        }
    )
    return Decision(
        a, np.zeros(n.shape, "uint32"), np.where(obs, quality, np.nan).astype("float32")
    )


def health(reasons=("CONFIG_NOT_READY",), channel="OK"):
    return dict(
        health="DEGRADED",
        health_reasons=list(reasons),
        config_lifecycle="draft",
        channel_status=channel,
    )


@pytest.mark.parametrize(
    "reasons,channel,expected",
    [
        (["CONFIG_NOT_READY"], "OK", 1.0),
        (["CONFIG_NOT_READY", "NOISE_OUT_OF_RANGE"], "DEGRADED", 0.8),
        (["CONFIG_NOT_READY", "AZIMUTH_GAP"], "OK", 0.8),
        (["CONFIG_NOT_READY", "UNKNOWN_FUTURE_REASON"], "OK", 0.8),
        (["CONFIG_NOT_READY"], "UNKNOWN", 0.8),
        ([], "OK", 0.8),
        (["NOISE_TELEMETRY_MISSING"], "UNKNOWN", 0.8),
    ],
)
def test_reason_specific_projection(reasons, channel, expected):
    h = health(reasons, channel)
    before = deepcopy(h)
    f = health_facets(h, profile())
    assert f["physical_quality_multiplier"] == expected
    assert not f["admission_ready"] and not f["operational_eligible"]
    assert h == before


def test_legacy_profile_still_penalizes_admin_and_new_profile_preserves_measurement():
    n = long_scene(dr=1000)
    old = keep(n)
    new = keep(n)
    p = profile()
    qo, *_ = finalize_decision(n, old, load_qc_profile(OLD, FLAGS), health())
    qn, *_ = finalize_decision(n, new, p, health())
    assert np.allclose(qo, 0.48) and np.allclose(qn, 0.6)
    assert not old.arrays["QPE_ELIGIBLE_MASK"].any()
    assert new.arrays["QPE_ELIGIBLE_MASK"].all()
    assert new.arrays["P2_ADMIN_PENALTY_REMOVED_MASK"].all()
    assert health_facets(health(), p)["config_lifecycle"] == "draft"


def test_physical_damage_and_prior_rejection_not_restored():
    n = long_scene(dr=1000)
    b = keep(n)
    b.arrays["QC_ACTION"][0, 30] = 2
    b.arrays["QPE_ELIGIBLE_MASK"][0, 30] = 0
    b.arrays["REFLECTIVITY_TRUST_MASK"][0, 30] = 0
    b.arrays["DBZH_USABLE"][0, 30] = np.nan
    b.quality[0, 30] = 0
    finalize_decision(n, b, profile(), health())
    assert not b.arrays["QPE_ELIGIBLE_MASK"][0, 30] and b.arrays["QC_ACTION"][0, 30] == 2
    c = keep(n)
    finalize_decision(n, c, profile(), health(["NOISE_OUT_OF_RANGE"], "DEGRADED"))
    assert not c.arrays["QPE_ELIGIBLE_MASK"].any()


def broad_scene(width=160, dr=1000):
    n = long_scene(dr=dr)
    return replace(
        n,
        azimuth=np.linspace(10, 10 + width, 12),
        audit={**n.audit, "azimuth_spacing_deg": width / 11},
        gap_after=np.r_[np.zeros(11, bool), True],
    )


@pytest.mark.parametrize("width", [12, 89, 161, 300])
def test_width_routes_keep_original_measurements_and_legacy_mask(width):
    n = broad_scene(width)
    p = profile()
    old = range_signatures(n, p.cross_radar)
    routed = range_signatures(n, p.cross_radar, route_all_shapes=True)
    for k, v in old.arrays.items():
        np.testing.assert_array_equal(v, routed.arrays[k])
    assert routed.arrays["P2_RANGE_MEASUREMENT_MASK"].any()
    if width >= 89:
        assert not old.arrays["V5_RANGE_CANDIDATE_MASK"].any()
        assert np.any(routed.arrays["P2_RANGE_ROUTE_CODE"] > 1)


def oc_fixture(n, state=4, bits=None):
    # Deliberately labelled fixture for testing arbitration, not physical inference.
    if bits is None:
        bits = int(
            Reason.SOURCE_MODEL_COMPATIBLE
            | Reason.BROAD_WEATHER_CONTINUITY
            | Reason.WEATHER_SOURCE_CONFLICT
        )
    a = {
        "family_code": np.full(n.shape, 2, "uint8"),
        "state": np.full(n.shape, state, "uint8"),
        "reason": np.full(n.shape, bits, "uint32"),
        "bracketed_reference_mask": np.ones(n.shape, "uint8"),
    }
    return SimpleNamespace(arrays=a), SimpleNamespace(
        summary={"status": "experimental_quarantine_applied_not_confirmed_rfi"},
        proposed_quarantine=np.zeros(n.shape, bool),
    )


def test_concurrence_isolates_broad_inliers_not_confirms_and_marks_budget():
    n = broad_scene()
    p = profile()
    e, o = oc_fixture(n)
    b = keep(n, 0.8)
    d, summary = broad_source_review(n, b, p, e, o)
    added = d.arrays["P2_ADDED_QUARANTINE_MASK"] == 1
    assert added.any() and summary["review_required"]
    assert np.all(d.arrays["QC_ACTION"][added] == Action.DOWNWEIGHT)
    assert not (d.flags & p.flag_masks["RADIAL_INTERFERENCE"]).any()
    assert not (d.flags & p.flag_masks["NON_METEOROLOGICAL"]).any()
    assert not d.arrays["QPE_ELIGIBLE_MASK"][added].any()
    assert np.isnan(d.arrays["DBZH_USABLE"][added]).all()
    assert b.arrays["QPE_ELIGIBLE_MASK"].all()


@pytest.mark.parametrize(
    "blocker",
    [
        "no_source",
        "range_weather",
        "independent_weather",
        "enhancement",
        "missing_snr_evidence",
        "reference",
    ],
)
def test_no_single_condition_cleanup(blocker):
    n = broad_scene()
    p = profile()
    e, o = oc_fixture(n)
    weather = None
    if blocker == "no_source":
        e.arrays["family_code"][:] = 0
    if blocker == "range_weather":
        n.fields["DBZH"][:] = 60
    if blocker == "independent_weather":
        weather = np.ones(n.shape)
    if blocker == "enhancement":
        e.arrays["reason"] |= int(Reason.LOCAL_POWER_ENHANCEMENT)
    if blocker == "missing_snr_evidence":
        e.arrays["reason"] |= int(Reason.TARGET_POL_MISSING)
    if blocker == "reference":
        e.arrays["bracketed_reference_mask"][:] = 0
    d, s = broad_source_review(n, keep(n), p, e, o, weather_support=weather)
    assert not d.arrays["P2_ADDED_QUARANTINE_MASK"].any()


def test_missing_never_becomes_range_model_or_action():
    n = broad_scene()
    n.fields["DBZH"][:, 150:190] = np.nan
    n.field_available["DBZH"][:, 150:190] = False
    p = profile()
    e, o = oc_fixture(n)
    d, _ = broad_source_review(n, keep(n), p, e, o)
    assert not d.arrays["P2_RANGE_MEASUREMENT_MASK"][:, 150:190].any()
    assert not d.arrays["P2_ADDED_QUARANTINE_MASK"][:, 150:190].any()
    assert np.all(d.arrays["QC_ACTION"][:, 150:190] == Action.MISSING)


def test_budget_overflow_keeps_high_risk_isolated_not_silently_normal():
    n = long_scene(dr=1000)
    p = profile()
    e, o = oc_fixture(n)
    e.arrays["family_code"][:] = 0
    proposed = np.zeros(n.shape, bool)
    proposed[:, 50:300] = True
    o = SimpleNamespace(
        summary={"status": "blocked_budget_whole_cut_reverted"}, proposed_quarantine=proposed
    )
    d, s = broad_source_review(n, keep(n), p, e, o)
    assert s["legacy_budget_recovered"] and s["review_required"]
    assert np.array_equal(d.arrays["P2_ADDED_QUARANTINE_MASK"] == 1, proposed)
    assert not d.arrays["QPE_ELIGIBLE_MASK"][proposed].any()
    assert np.all(d.arrays["QC_ACTION"][proposed] == Action.DOWNWEIGHT)


def test_switches_off_keep_decision_and_old_thresholds():
    n = broad_scene()
    p = profile()
    e, o = oc_fixture(n)
    p = p.model_copy(
        update={
            "generalization": p.generalization.model_copy(
                update={
                    "split_admission_health": False,
                    "retain_broad_measurements": False,
                    "resolve_coherent_self_protection": False,
                }
            )
        }
    )
    b = keep(n)
    d, s = broad_source_review(n, b, p, e, o)
    for k, v in b.arrays.items():
        np.testing.assert_array_equal(v, d.arrays[k])
    old = load_qc_profile(OLD, FLAGS)
    for name in ("echo", "quality_index", "cross_radar", "rfi_objects", "literature", "residual"):
        assert getattr(p, name) == getattr(old, name)
    assert health_facets(health(), p)["physical_quality_multiplier"] == 0.8


def test_actual_oc1_same_source_conflict_is_recorded_and_only_concurrent_fit_acts():
    n = broad_scene(width=89)
    p = profile()
    r = raw_from_native(n, 360)
    e = infer(r)
    o = SimpleNamespace(
        summary={"status": "audit_baseline_unchanged"}, proposed_quarantine=np.zeros(n.shape, bool)
    )
    assert np.any((e.arrays["family_code"] == 2) & (e.arrays["state"] == 4))
    d, s = broad_source_review(n, keep(n), p, e, o)
    assert d.arrays["P2_ADDED_QUARANTINE_MASK"].any()
    assert np.all(d.arrays["P2_RANGE_MEASUREMENT_MASK"][d.arrays["P2_ADDED_QUARANTINE_MASK"] == 1])


def test_budget_recovery_can_be_disabled_and_independent_weather_still_vetoes():
    n = long_scene(dr=1000)
    p = profile()
    e, _ = oc_fixture(n)
    e.arrays["family_code"][:] = 0
    proposed = np.zeros(n.shape, bool)
    proposed[:, 50:300] = True
    o = SimpleNamespace(
        summary={"status": "blocked_budget_whole_cut_reverted"}, proposed_quarantine=proposed
    )
    off = p.model_copy(
        update={
            "generalization": p.generalization.model_copy(
                update={"prevent_budget_reversion": False}
            )
        }
    )
    d, s = broad_source_review(n, keep(n), off, e, o)
    assert not d.arrays["P2_ADDED_QUARANTINE_MASK"].any()
    assert s["review_required"]  # Existing budget issue never silently becomes accepted.
    d, s = broad_source_review(n, keep(n), p, e, o, weather_support=np.ones(n.shape))
    assert not d.arrays["P2_ADDED_QUARANTINE_MASK"].any()
    assert s["review_required"]


def test_unknown_health_does_not_receive_administrative_exemption():
    for reason in (["CONFIG_NOT_READY", "UNKNOWN"], None, [], "CONFIG_NOT_READY"):
        h = health()
        h["health_reasons"] = reason
        result = health_facets(h, profile())
        assert result["physical_quality_multiplier"] == 0.8
        assert not result["administrative_penalty_removed"]


def test_all_extensions_disabled_match_prior_actions_on_actual_coherent_fixture():
    n = broad_scene(width=89)
    p = profile()
    r = raw_from_native(n, 360)
    e = infer(r)
    o = SimpleNamespace(
        summary={"status": "audit_baseline_unchanged"}, proposed_quarantine=np.zeros(n.shape, bool)
    )
    switches = p.generalization.model_copy(
        update={
            "split_admission_health": False,
            "retain_broad_measurements": False,
            "resolve_coherent_self_protection": False,
            "prevent_budget_reversion": False,
        }
    )
    p = p.model_copy(update={"generalization": switches})
    b = keep(n)
    d, _ = broad_source_review(n, b, p, e, o)
    for name, value in b.arrays.items():
        np.testing.assert_array_equal(value, d.arrays[name])
    np.testing.assert_array_equal(b.quality, d.quality)
    np.testing.assert_array_equal(b.flags, d.flags)
