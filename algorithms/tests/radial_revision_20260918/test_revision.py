import copy
import json
import numpy as np
import pytest
from .conftest import load, line, states, Native, evaluate

C = load("radial_revision.config").RadialRevisionConfig
SC = load("config").SourceReviewConfig


def test_missing_flanks_are_candidate_not_clear_air():
    n = line()
    out, report = evaluate(n, C(mode="experiment_quarantine"), n.field_available["DBZH"])
    assert out["RV2_TOPOLOGY_MASK"][6].sum() > 200
    assert out["RV2_UNKNOWN_FLANK_MASK"][6].sum() > 200
    assert np.array_equal(out["RV2_ACTION_PROPOSAL_MASK"], out["RV2_TOPOLOGY_MASK"])
    assert not out["RV2_ACTION_PROPOSAL_MASK"][~n.field_available["DBZH"]].any()
    assert report["filled_gates"] == report["confirmed_gates"] == 0


def test_shape_without_source_has_no_action():
    n = line()
    out, _ = evaluate(n, C(mode="experiment_quarantine"))
    assert out["RV2_TOPOLOGY_MASK"].any()
    assert not out["RV2_ACTION_PROPOSAL_MASK"].any()


@pytest.mark.parametrize("key", ["weather", "conflicts"])
def test_weather_mixed_and_conflict_are_barriers(key):
    n = line(); barrier = np.zeros(n.shape, bool); barrier[6, 70:120] = True
    out, _ = evaluate(n, C(mode="experiment_quarantine"), n.field_available["DBZH"], **{key: barrier})
    assert not out["RV2_CANDIDATE_MASK"][barrier].any()
    assert out["RV2_BARRED_MASK"][barrier].all()


def test_audit_cannot_change_legacy_qualification():
    n = line(); src = n.field_available["DBZH"]
    q0, a0, _ = load("source").source_additions(n, SC(narrow_enabled=False), src, np.zeros(n.shape, "float32"))
    q1, a1, _ = load("source").source_additions(n, SC(narrow_enabled=False, radial_revision=C()), src, np.zeros(n.shape, "float32"))
    assert a1["RV2_QUALIFIED_MASK"].any()
    assert np.array_equal(q0, q1)
    assert not a1["RV2_ACTION_PROPOSAL_MASK"].any()
    for k in a0:
        assert np.array_equal(a0[k], a1[k], equal_nan=True), k


def test_gap_geometry_no_candidate_crossing():
    n = line(); n.gap_after[5] = True
    out, _ = evaluate(n, C(mode="experiment_quarantine"), n.field_available["DBZH"])
    assert not out["RV2_CANDIDATE_MASK"].any()


def test_scan_seam_is_conservatively_not_connected():
    n = line(); n.fields["DBZH"][0] = 40; n.field_available["DBZH"][0] = True
    out, _ = evaluate(n, C(), n.field_available["DBZH"])
    assert not out["RV2_TOPOLOGY_MASK"][0].any()


def test_nonuniform_geometry_is_rejected():
    n = line(); n.ranges[100] += 40
    with pytest.raises(ValueError): evaluate(n, C())


@pytest.mark.parametrize("key", ["weather", "conflicts"])
def test_nan_mask_is_not_truthy(key):
    n = line(); m = np.zeros(n.shape); m[1, 1] = np.nan
    with pytest.raises(ValueError): evaluate(n, C(), **{key: m})


def test_raw_fields_not_mutated():
    n = states(); original = n.clone()
    evaluate(n, C(step=3, mode="experiment_quarantine", allow_segmented_quarantine=True))
    for k in n.fields:
        assert np.array_equal(n.fields[k], original.fields[k], equal_nan=True)
        assert np.array_equal(n.field_available[k], original.field_available[k])


def test_no_rain_missing_and_full_weather_distinct():
    n = Native(np.zeros((15, 160)))
    out, _ = evaluate(n, C(step=3, mode="experiment_quarantine"), np.ones(n.shape, bool))
    assert not out["RV2_CANDIDATE_MASK"].any()
    n.fields["DBZH"][:] = 25.
    out, _ = evaluate(n, C(step=3, mode="experiment_quarantine"))
    assert not out["RV2_ACTION_PROPOSAL_MASK"].any()


def test_plateau_ambiguity_is_preserved():
    n = line(); n.fields["DBZH"][6] = 65
    out, _ = evaluate(n, C(mode="experiment_quarantine"), n.field_available["DBZH"])
    assert out["RV2_PLATEAU_MASK"][6].all()
    assert not out["RV2_ACTION_PROPOSAL_MASK"].any()


def reference_run(n, **kw):
    cfg = C(step=2, **kw)
    c = np.zeros(n.shape, bool); c[5] = True
    records = []
    out, summary = load("radial_revision.segments").segmented_references(n, cfg, c, np.zeros(n.shape, bool), records_out=records)
    return out, summary, records


def test_strong_and_weak_states_do_not_share_actions():
    n = states()
    out, summary, records = reference_run(n)
    high = n.fields["SNR"][5] >= 20
    assert out["RV2_SEGMENT_MATCH_MASK"][5, high].sum() > 150
    assert not out["RV2_SEGMENT_MATCH_MASK"][5, ~high].any()
    assert out["RV2_WEAK_MATCH_MASK"][5, ~high].sum() > 100
    assert summary["weak_actions"] == 0
    assert all(r["target_block"] not in r["reference_blocks"] for r in records)
    assert all(all(abs(b-r["target_block"]) > 1 for b in r["reference_blocks"]) for r in records)


def test_incomplete_polar_stays_diagnostic():
    out, _, _ = reference_run(states(polar=False))
    assert out["RV2_WEAK_MATCH_MASK"].any()
    assert not out["RV2_SEGMENT_MATCH_MASK"].any()


def test_target_and_guard_mutation_does_not_change_its_reference():
    n = states(); a, s, records = reference_run(n)
    changed = n.clone(); blocks = np.floor((n.ranges-50000)/50000).astype(int)
    mutation = abs(blocks-5) <= 1
    for k in ("DBZH", "SNR", "PHIDP", "ZDR"):
        changed.fields[k][:, mutation] += 17
    b, s2, other = reference_run(changed)
    ref = lambda rs: [{k:v for k,v in r.items() if k not in ("model_id", "fold_id")} for r in rs if r["ray"] == 5 and r["target_block"] == 5]
    assert ref(records) and ref(records) == ref(other)
    # Counterexample: changing references MUST alter their fit/provenance.
    changed.fields["SNR"][:, ~mutation] += 9
    _, _, third = reference_run(changed)
    assert ref(records) != ref(third)


def test_target_power_enhancement_is_not_censored():
    n = states(); n.fields["DBZH"][5, 250:260] += 12
    out, _, _ = reference_run(n)
    assert not out["RV2_SEGMENT_MATCH_MASK"][5, 250:260].any()


def test_paired_calibration_requires_independent_groups():
    n = states(); mod = load("radial_revision.segments")
    paired = np.ones(n.shape, bool); train = np.ones(n.shape[1], bool)
    c, d = mod.range_relation(n.ranges, n.fields["DBZH"], n.fields["SNR"], paired, train)
    assert d["status"] == "measured_consistent" and c == pytest.approx(.012, abs=1e-6)
    n.fields["DBZH"][::2] += .006*n.ranges[None, :]/1000
    c, d = mod.range_relation(n.ranges, n.fields["DBZH"], n.fields["SNR"], paired, train)
    assert d["status"] == "inconsistent_ray_groups"


def test_actual_bundle_flanks_and_nonuniform_interior():
    z = np.zeros((17, 150), "float32"); z[7] = 20; z[8] = 45; z[9] = 27
    n = Native(z, fields={"SNR": 30})
    out, _ = evaluate(n, C(step=3, mode="experiment_quarantine"), np.ones(n.shape, bool))
    assert out["RV2_BUNDLE_MASK"][7].sum() > 100
    assert out["RV2_BUNDLE_MASK"][9].sum() > 100
    assert not out["RV2_BUNDLE_MASK"][0].any()
    assert not out["RV2_TOPOLOGY_MASK"].any()  # all raw coordinates observed


def test_unknown_bundle_flanks_not_replaced_by_low_values():
    n = line()
    a = load("radial_revision.bundles").bundle_candidates(n, C(step=3), np.zeros(n.shape, bool))
    assert not a["RV2_BUNDLE_MASK"].any()


def test_fragment_links_only_identity_and_gap_not_filled():
    n = line(); n.fields["DBZH"][6, 20:22] = np.nan; n.field_available["DBZH"][6, 20:22] = False
    candidate = np.zeros(n.shape, bool); candidate[6, 10:20] = True; candidate[6, 22:30] = True
    a, _ = load("radial_revision.bundles").fragment_identities(n, C(step=3), candidate, np.zeros(n.shape, bool))
    assert a["RV2_OBJECT_ID"][6, 19] == a["RV2_OBJECT_ID"][6, 22] > 0
    assert not a["RV2_OBJECT_ID"][6, 20:22].any()
    assert not a["RV2_LINKED_SEGMENT_MASK"][6, 20:22].any()


@pytest.mark.parametrize("barrier", ["observed", "weather", "signature"])
def test_links_stop_at_measurements_weather_or_state_change(barrier):
    n = line(); candidate = np.zeros(n.shape, bool); candidate[6, 10:20] = True; candidate[6, 22:30] = True
    blocked = np.zeros(n.shape, bool)
    if barrier != "observed":
        n.field_available["DBZH"][6, 20:22] = False
        n.fields["DBZH"][6, 20:22] = np.nan
    if barrier == "weather": blocked[6, 21] = True
    if barrier == "signature": n.fields["SNR"][6, 22:30] += 20
    a, _ = load("radial_revision.bundles").fragment_identities(n, C(step=3), candidate, blocked)
    assert a["RV2_OBJECT_ID"][6, 19] != a["RV2_OBJECT_ID"][6, 22]


def test_resource_budget_abstains_without_partial_actions():
    n = line()
    out, s = evaluate(n, C(mode="experiment_quarantine", maximum_gates=1), n.field_available["DBZH"])
    assert s["status"] == "resource_limit_abstained"
    assert not out["RV2_ACTION_PROPOSAL_MASK"].any()


def test_source_entry_and_serialized_contract():
    n = line(); cfg = SC(narrow_enabled=False, radial_revision=C(mode="experiment_quarantine"))
    q, a, _ = load("source").source_additions(n, cfg, n.field_available["DBZH"], np.zeros(n.shape, "float32"))
    a["SRC_REVIEW_REFERENCE_FOLD_ID"] = n.field_available["DBZH"].astype("uint32")
    load("source_validation").validate_source_fields(a, n.field_available["DBZH"])
    assert q[6].sum() > 200
    a["RV2_ACTION_PROPOSAL_MASK"][0, 0] = 1
    with pytest.raises(ValueError): load("source_validation").validate_source_fields(a, n.field_available["DBZH"])


@pytest.mark.parametrize("change", ["mode", "weak", "residual", "fold", "missing"])
def test_serialized_tampering_rejected(change):
    n = line(); cfg = SC(narrow_enabled=False, radial_revision=C(mode="experiment_quarantine"))
    q, a, _ = load("source").source_additions(n, cfg, n.field_available["DBZH"], np.zeros(n.shape, "float32"))
    a["SRC_REVIEW_REFERENCE_FOLD_ID"] = n.field_available["DBZH"].astype("uint32")
    if change == "mode": a["RV2_MODE_CODE"][:] = 0
    if change == "weak": a["RV2_WEAK_MATCH_MASK"][6, 60] = 1; a["RV2_SEGMENT_MATCH_MASK"][6, 60] = 1
    if change == "residual": a["RV2_SEGMENT_RESIDUAL_DB"][6, 60] = np.inf
    if change == "fold": a["SRC_REVIEW_REFERENCE_FOLD_ID"][:] = 0
    if change == "missing": del a["RV2_BUNDLE_MASK"]
    with pytest.raises(ValueError): load("source_validation").validate_source_fields(a, n.field_available["DBZH"])


@pytest.mark.parametrize("kwargs", [dict(step=0), dict(step=1, allow_segmented_quarantine=True), dict(weak_policy="quarantine"), dict(outside_support_fraction=.9), dict(scales_m=(10000, 5000)), dict(maximum_power_residual_db=20), dict(maximum_states=99), dict(block_m=float("nan"))])
def test_invalid_config_is_rejected(kwargs):
    with pytest.raises(ValueError): C(**kwargs)


def test_disabled_child_is_stripped_from_legacy_hash():
    value = {"review_extension_version": "qc-review-20260917-v1", "nonprecip_review": None,
             "generalization": {"broad_source": {"source_review": SC().model_dump(mode="json")}}}
    actual = load("profile_support").strip_absent_review_fields(copy.deepcopy(value))
    assert "radial_revision" not in actual["generalization"]["broad_source"]["source_review"]
    value["generalization"]["broad_source"]["source_review"]["radial_revision"] = C().model_dump(mode="json")
    actual = load("profile_support").strip_absent_review_fields(copy.deepcopy(value))
    assert actual["generalization"]["broad_source"]["source_review"]["radial_revision"]["version"] == C().version


def state_line():
    old = states()
    snr = np.full((20, 600), 25., dtype="float32"); snr[9] = old.fields["SNR"][5]
    r = old.ranges
    z = snr-35.+20.*np.log10(r[None, :]/1000.)+.012*r[None, :]/1000.
    z[[8, 10]] = np.nan
    return Native(z, fields={"SNR": snr, "PHIDP": 30., "RHOHV": .99, "ZDR": .5})


def test_segmented_source_wired_through_real_source_entry_and_validator():
    n = state_line(); cfg = SC(narrow_enabled=False, mode="experiment_quarantine",
          radial_revision=C(step=2, mode="experiment_quarantine", allow_segmented_quarantine=True))
    q, a, _ = load("source").source_additions(n, cfg, np.zeros(n.shape, bool), np.full(n.shape, np.nan, "float32"))
    a["SRC_REVIEW_REFERENCE_FOLD_ID"] = np.zeros(n.shape, "uint32")
    load("source_validation").validate_source_fields(a, n.field_available["DBZH"])
    assert q[9, n.fields["SNR"][9] > 20].sum() > 100
    assert not q[9, n.fields["SNR"][9] < 20].any()
    assert not a["SRC_REVIEW_SOURCE_MATCH_MASK"].any()  # qualification uses only new references


@pytest.mark.parametrize("mode,allow", [("audit", True), ("experiment_quarantine", False)])
def test_segmented_two_separate_policy_switches(mode, allow):
    n = state_line(); out, _ = evaluate(n, C(step=2, mode=mode, allow_segmented_quarantine=allow))
    assert out["RV2_SEGMENT_MATCH_MASK"].any()
    assert not out["RV2_ACTION_PROPOSAL_MASK"].any()


def test_fold_budget_abstains_whole_extension_not_prefix():
    n = state_line(); out, s = evaluate(n, C(step=2, mode="experiment_quarantine", allow_segmented_quarantine=True, maximum_folds=1))
    assert s["status"] == "resource_limit_abstained"
    assert not out["RV2_SEGMENT_MATCH_MASK"].any()
    assert not out["RV2_ACTION_PROPOSAL_MASK"].any()


def test_weak_receiver_model_is_not_forced_through_strong_dbzh_fit():
    n = states()
    n.fields["DBZH"][5] += 7*np.sin(np.arange(600)*.9)
    out, summary, records = reference_run(n)
    assert not out["RV2_SEGMENT_MATCH_MASK"][5].any()
    assert out["RV2_WEAK_MATCH_MASK"][5].sum() > 100
    assert any(r.get("strong_power_verified") is False and r["status"] == "weak_receiver_reference" for r in records)
    assert summary["weak_actions"] == 0


def test_identity_transitivity_is_bounded_from_first_segment():
    n = line(); n.field_available["DBZH"][6] = False; n.fields["DBZH"][6] = np.nan
    c = np.zeros(n.shape, bool)
    for lo in (10, 19, 28, 37):
        c[6, lo:lo+7] = True; n.field_available["DBZH"][6, lo:lo+7] = True; n.fields["DBZH"][6, lo:lo+7] = 40
    a, _ = load("radial_revision.bundles").fragment_identities(n, C(step=3, maximum_identity_span_m=20000), c, np.zeros(n.shape, bool))
    assert a["RV2_OBJECT_ID"][6, 10] == a["RV2_OBJECT_ID"][6, 19]
    assert a["RV2_OBJECT_ID"][6, 10] != a["RV2_OBJECT_ID"][6, 28]


def test_new_runtime_attributes_and_legacy_absence():
    from types import SimpleNamespace as NS
    profile = NS(review_extension_version="qc-review-20260917-v1", nonprecip_review=None,
                 generalization=NS(broad_source=NS(source_review=SC())))
    before = load("runtime").review_attributes(profile)
    assert "qc_radial_revision_version" not in before
    profile.generalization.broad_source.source_review = SC(radial_revision=C(step=3))
    after = load("runtime").review_attributes(profile)
    assert after["qc_radial_revision_step"] == 3
    assert after["qc_radial_revision_version"] == C().version


def test_uncalibrated_segment_cannot_be_enabled():
    with pytest.raises(ValueError): C(step=2, require_measured_range_term=False)


def test_global_plateau_veto_does_not_select_training_membership(monkeypatch):
    n = line(); n.fields["DBZH"][6] = 65
    captured = {}
    def observe(native, cfg, candidate, blocked, **kwargs):
        captured["candidate"] = candidate.copy()
        captured["blocked"] = blocked.copy()
        return {}, {"status": "test_capture"}
    monkeypatch.setattr(load("radial_revision.engine"), "segmented_references", observe)
    out, _ = evaluate(n, C(step=2))
    assert out["RV2_PLATEAU_MASK"][6].all()
    assert not captured["candidate"][6].any()
    assert not captured["blocked"][6].any()  # references use block-confined plateau checks
