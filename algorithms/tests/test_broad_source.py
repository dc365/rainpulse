from types import SimpleNamespace

import numpy as np

from rainpulse_algo.radar.qc_engine.broad_source import BroadSourceConfig, infer_broad_source


def scene():
    r = np.arange(50125, 450000, 250.0)
    z = np.broadcast_to(15 + 20 * np.log10(r / 1000), (12, len(r))).copy()
    rng = np.random.default_rng(1)
    fields = {
        "DBZH": z,
        "SNR": np.full(z.shape, 54.0),
        "PHIDP": rng.normal(0, 1.5, z.shape),
        "ZDR": rng.normal(0, 0.2, z.shape),
        "RHOHV": rng.uniform(0.94, 1, z.shape),
    }
    return SimpleNamespace(
        shape=z.shape,
        fields=fields,
        field_available={k: np.ones(z.shape, bool) for k in fields},
        geometry_good=np.ones(12, bool),
        ranges=r,
        gate_spacing_m=250.0,
        azimuth=np.arange(12.0),
        gap_after=np.r_[np.zeros(11, bool), True],
        full_ppi=False,
    )


def test_mixed_source_predicts_target_without_strict_coherence():
    n = scene()
    a, s = infer_broad_source(n, BroadSourceConfig())
    assert a["BWS_CANDIDATE_MASK"].sum() > 0.7 * np.prod(n.shape)
    assert not s["operational_eligible"]


def test_target_and_guard_changes_do_not_change_fitted_reference():
    n = scene()
    a, s = infer_broad_source(n, BroadSourceConfig())
    target = (n.ranges >= 200000) & (n.ranges < 250000)
    guard = (n.ranges >= 150000) & (n.ranges < 300000)
    n.fields["DBZH"][:, guard] += 30
    n.fields["PHIDP"][:, guard] += 90
    b, t = infer_broad_source(n, BroadSourceConfig())
    original = [x for x in s["folds"] if x["target_block"] == 3]
    changed = [x for x in t["folds"] if x["target_block"] == 3]
    assert original and len(original) == len(changed)
    for x, y in zip(original, changed):
        assert (
            x["intercept_db"] == y["intercept_db"]
            and x["phase_center_deg"] == y["phase_center_deg"]
            and x["support_rays"] == y["support_rays"]
        )
    assert not b["BWS_CANDIDATE_MASK"][:, target].any()


def test_weather_and_missing_and_enhancement_are_not_candidates():
    n = scene()
    weather = np.zeros(n.shape, bool)
    weather[:, 900:950] = True
    n.fields["DBZH"][:, 500:550] = np.nan
    n.field_available["DBZH"][:, 500:550] = False
    n.fields["DBZH"][:, 1000:1020] += 10
    a, _ = infer_broad_source(n, BroadSourceConfig(), weather=weather)
    assert not a["BWS_CANDIDATE_MASK"][:, 900:950].any()
    assert not a["BWS_CANDIDATE_MASK"][:, 500:550].any()
    assert not a["BWS_CANDIDATE_MASK"][:, 1000:1020].any()


def test_no_angular_support_and_non_range_weather_fail():
    n = scene()
    n.gap_after[:] = True
    a, _ = infer_broad_source(n, BroadSourceConfig())
    assert not a["BWS_CANDIDATE_MASK"].any()
    n = scene()
    n.fields["DBZH"][:] = 40
    a, _ = infer_broad_source(n, BroadSourceConfig())
    assert not a["BWS_CANDIDATE_MASK"].any()


def test_missing_polarization_and_unverified_plateau_are_not_evidence():
    n = scene()
    n.fields.pop("PHIDP")
    a, _ = infer_broad_source(n, BroadSourceConfig())
    assert not a["BWS_CANDIDATE_MASK"].any()
    n = scene()
    n.fields["DBZH"][:, -200:] = 70
    a, _ = infer_broad_source(n, BroadSourceConfig())
    assert not a["BWS_CANDIDATE_MASK"][:, -200:].any()


def test_stage_audit_is_non_mutating_and_experiment_only_quarantines():
    from rainpulse_algo.radar.qc import load_qc_profile
    from rainpulse_algo.radar.qc_engine.generalization import broad_source_review

    from .test_generalization_p0p2 import FLAGS, P, keep, oc_fixture

    n = scene()
    n.original_indices = np.arange(n.shape[0])
    n.audit = {"azimuth_spacing_deg": 1.0}
    n.attrs = {}
    n.name = "sweep_000"
    # Range evidence adapter needs the same native geometry attributes.
    n.elevation = np.full(n.shape[0], 0.5)
    p = load_qc_profile(P.with_name("fujian-qc-broad-source-v1.yaml"), FLAGS)
    e, o = oc_fixture(n)
    e.arrays["reason"][:] = 0
    e.arrays["family_code"][:] = 0
    audit = p.model_copy(
        update={
            "generalization": p.generalization.model_copy(
                update={"broad_source": BroadSourceConfig(mode="audit")}
            )
        }
    )
    baseline = keep(n)
    d, s = broad_source_review(n, baseline, audit, e, o)
    assert d.arrays["BWS_CANDIDATE_MASK"].any()
    assert np.array_equal(d.arrays["QPE_ELIGIBLE_MASK"], baseline.arrays["QPE_ELIGIBLE_MASK"])
    d, s = broad_source_review(n, baseline, p, e, o)
    candidate = d.arrays["BWS_CANDIDATE_MASK"] == 1
    assert not d.arrays["QPE_ELIGIBLE_MASK"][candidate].any()
    assert not np.isfinite(d.arrays["DBZH_USABLE"][candidate]).any()
    assert s["new_confirmed_gates"] == 0


def test_shared_term_heldout_and_inconsistent_groups():
    from rainpulse_algo.radar.qc_engine.broad_source import shared_range_term

    n = scene()
    n.fields["DBZH"] += 0.011 * n.ranges[None, :] / 1000
    reference = (n.ranges < 150000) | (n.ranges >= 300000)
    valid = np.ones(n.shape, bool)
    a, diag = shared_range_term(n, valid, reference)
    assert abs(a - 0.011) < 1e-8 and diag["status"] == "measured_consistent"
    n.fields["DBZH"][:, ~reference] += 100
    b, other = shared_range_term(n, valid, reference)
    assert a == b and diag == other
    n.fields["DBZH"][::2] += 0.006 * n.ranges[None, :] / 1000
    b, diag = shared_range_term(n, valid, reference)
    assert b == 0 and diag["status"] == "inconsistent_ray_groups"


def test_range_term_recovers_source_without_changing_old_model():
    n = scene()
    n.fields["DBZH"] += 0.011 * n.ranges[None, :] / 1000
    old, _ = infer_broad_source(n, BroadSourceConfig())
    new, s = infer_broad_source(n, BroadSourceConfig(shared_range_term=True))
    assert new["BWS_CANDIDATE_MASK"].sum() > old["BWS_CANDIDATE_MASK"].sum() * 1.2
    assert all(x["status"] == "measured_consistent" for x in s["range_term_folds"])


def test_observed_range_includes_last_real_gate_and_no_missing():
    n = scene()
    n.ranges += 20000
    n.fields["DBZH"] = np.broadcast_to(15 + 20 * np.log10(n.ranges / 1000), n.shape).copy()
    n.fields["DBZH"][0, -1] = np.nan
    n.field_available["DBZH"][0, -1] = False
    a, _ = infer_broad_source(n, BroadSourceConfig(observed_range=True))
    assert a["BWS_CANDIDATE_MASK"][1:, -1].any()
    assert a["BWS_CANDIDATE_MASK"][0, -1] == 0


def test_range_term_insufficient_support_falls_back():
    from rainpulse_algo.radar.qc_engine.broad_source import shared_range_term

    n = scene()
    coefficient, diag = shared_range_term(n, np.zeros(n.shape, bool), np.ones(n.shape[1], bool))
    assert coefficient == 0 and diag["status"] == "insufficient_paired_support"


def test_enabled_model_keeps_target_guard_out_of_calibration():
    n = scene()
    n.fields["DBZH"] += 0.011 * n.ranges[None, :] / 1000
    cfg = BroadSourceConfig(shared_range_term=True, observed_range=True)
    _, before = infer_broad_source(n, cfg)
    guard = (n.ranges >= 150000) & (n.ranges < 300000)
    n.fields["DBZH"][:, guard] = 70
    n.fields["SNR"][:, guard] = 15
    _, after = infer_broad_source(n, cfg)
    left = [x for x in before["range_term_folds"] if x["target_block"] == 3]
    right = [x for x in after["range_term_folds"] if x["target_block"] == 3]
    assert left and left == right
    assert left[0]["status"] == "measured_consistent"


def distance_scene():
    n = scene()
    rng = np.random.default_rng(93)
    near = n.ranges < 100000
    n.fields["PHIDP"][:, near] = rng.normal(0, 5, (12, near.sum()))
    n.fields["ZDR"][:, near] = rng.normal(0, 0.55, (12, near.sum()))
    n.fields["RHOHV"][:, near] = rng.uniform(0.85, 1, (12, near.sum()))
    return n, near


def test_distance_polar_recovers_heldout_near_source():
    n, near = distance_scene()
    old, _ = infer_broad_source(n, BroadSourceConfig())
    new, diag = infer_broad_source(n, BroadSourceConfig(distance_polar_reference=True))
    assert new["BWS_CANDIDATE_MASK"][:, near].sum() > old["BWS_CANDIDATE_MASK"][:, near].sum() * 2
    assert any(
        x.get("distance_polar", {}).get("status") == "measured_consistent" for x in diag["folds"]
    )


def test_distance_polar_target_guard_cannot_train_reference():
    n, near = distance_scene()
    cfg = BroadSourceConfig(distance_polar_reference=True)
    _, a = infer_broad_source(n, cfg)
    for k in ("PHIDP", "ZDR", "RHOHV"):
        n.fields[k][4:7, near] += 40
    out, b = infer_broad_source(n, cfg)

    def ref(d):
        return next(
            x["distance_polar"] for x in d["folds"] if x["ray"] == 5 and x["target_block"] == 0
        )

    assert ref(a) == ref(b)
    assert not out["BWS_CANDIDATE_MASK"][5, near].any()


def test_distance_polar_protection_enhancement_and_sparse_fallback():
    n, near = distance_scene()
    cfg = BroadSourceConfig(distance_polar_reference=True)
    weather = np.zeros(n.shape, bool)
    weather[5, :40] = True
    n.fields["DBZH"][5, 40:80] += 10
    n.field_available["DBZH"][5, 80:100] = False
    out, _ = infer_broad_source(n, cfg, weather=weather)
    assert out["BWS_CANDIDATE_MASK"][:, near].any()
    assert not out["BWS_CANDIDATE_MASK"][5, :100].any()
    n.gap_after[:] = True
    out, _ = infer_broad_source(n, cfg)
    assert not out["BWS_CANDIDATE_MASK"].any()


def test_distance_phase_wrap_preserves_candidates():
    n, _ = distance_scene()
    cfg = BroadSourceConfig(distance_polar_reference=True)
    a, _ = infer_broad_source(n, cfg)
    n.fields["PHIDP"] = (n.fields["PHIDP"] + 359) % 360
    b, _ = infer_broad_source(n, cfg)
    assert np.array_equal(a["BWS_CANDIDATE_MASK"], b["BWS_CANDIDATE_MASK"])


def test_distance_inconsistent_donors_do_not_expand_old_mask():
    n, near = distance_scene()
    # Target ray 5 has donor rays 0,1,2,3,7,8,9,10 in connected ±5°.
    for ray in (1, 3, 8, 10):
        n.fields["PHIDP"][ray, near] += 4
    a, _ = infer_broad_source(n, BroadSourceConfig())
    b, diag = infer_broad_source(n, BroadSourceConfig(distance_polar_reference=True))
    fold = next(x for x in diag["folds"] if x["ray"] == 5 and x["target_block"] == 0)
    assert fold["distance_polar"]["status"] != "measured_consistent"
    assert np.array_equal(a["BWS_CANDIDATE_MASK"][5, near], b["BWS_CANDIDATE_MASK"][5, near])
