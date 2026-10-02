import numpy as np

from .conftest import Native, load


def fixture():
    n = Native(np.full((9, 300), 20.0))
    parent = np.zeros(n.shape, "uint32")
    parent[2:7, 10:290] = 1
    seed = np.zeros_like(parent)
    seed[3:6, 10:290] = 1
    seed[4, 140:160] = 0
    group = {"RV2_RAW_FAN_ID": parent, "RV2_SOURCE_LEDGER_SEED_ID": seed}
    return n, group, np.zeros(n.shape, bool)


def run(n, g, b):
    return load("radial_revision.source_footprint").qualify(n, b, g)


def test_frozen_original_footprint_tracks_weak_tail_but_not_peripheral_growth():
    n, g, b = fixture()
    raw = n.fields["DBZH"].copy()
    out, report = run(n, g, b)
    assert out["RV2_SOURCE_FOOTPRINT_QUALIFIED_MASK"][4, 140:160].all()
    assert not out["RV2_SOURCE_FOOTPRINT_QUALIFIED_MASK"][[2, 6]].any()
    assert report["actions"] == 0 and not report["recursive_growth"]
    assert np.array_equal(n.fields["DBZH"], raw)


def test_no_anchor_or_target_local_source_cannot_train_boundary():
    n, g, b = fixture()
    g["RV2_SOURCE_LEDGER_SEED_ID"][:] = 0
    out, _ = run(n, g, b)
    assert not out["RV2_SOURCE_FOOTPRINT_QUALIFIED_MASK"].any()
    n, g, b = fixture()
    g["RV2_SOURCE_LEDGER_SEED_ID"][:, :120] = 0
    g["RV2_SOURCE_LEDGER_SEED_ID"][:, 180:] = 0
    out, _ = run(n, g, b)
    assert not out["RV2_SOURCE_FOOTPRINT_QUALIFIED_MASK"][4, 140:160].any()


def test_barrier_missing_or_unstable_source_boundary_abstains():
    n, g, b = fixture()
    b[:, 135] = True
    b[:, 165] = True
    out, _ = run(n, g, b)
    assert not out["RV2_SOURCE_FOOTPRINT_QUALIFIED_MASK"][4, 140:160].any()
    n, g, b = fixture()
    n.field_available["DBZH"][4, 140:160] = False
    out, _ = run(n, g, b)
    assert not out["RV2_SOURCE_FOOTPRINT_QUALIFIED_MASK"][4, 140:160].any()
    n, g, b = fixture()
    g["RV2_SOURCE_LEDGER_SEED_ID"][3, :120] = 0
    g["RV2_SOURCE_LEDGER_SEED_ID"][5, 180:] = 0
    n.gap_after[3] = True
    out, _ = run(n, g, b)
    assert not out["RV2_SOURCE_FOOTPRINT_QUALIFIED_MASK"].any()


def test_separate_source_rays_do_not_fill_angular_gap_or_wandering_boundary():
    n, g, b = fixture()
    g["RV2_SOURCE_LEDGER_SEED_ID"][4] = 0
    out, _ = run(n, g, b)
    assert not out["RV2_SOURCE_FOOTPRINT_QUALIFIED_MASK"].any()
    n = Native(np.full((13, 300), 20.0))
    parent = np.ones(n.shape, "uint32")
    seed = np.zeros_like(parent)
    seed[2:4, 10:120] = 1
    seed[7:9, 180:290] = 2
    out, _ = run(
        n, {"RV2_RAW_FAN_ID": parent, "RV2_SOURCE_LEDGER_SEED_ID": seed}, np.zeros(n.shape, bool)
    )
    assert not out["RV2_SOURCE_FOOTPRINT_QUALIFIED_MASK"][5, 140:160].any()


def test_current_measured_weather_like_polar_veto_and_unavailable_is_not_vote():
    n, g, b = fixture()
    for k, v in (("RHOHV", 0.99), ("SNR", 20.0)):
        n.fields[k] = np.full(n.shape, v, "float32")
        n.field_available[k] = np.ones(n.shape, bool)
    out, report = run(n, g, b)
    assert not out["RV2_SOURCE_FOOTPRINT_QUALIFIED_MASK"][4, 140:160].any()
    assert report["current_polar_retained_gates"] > 0
    n.field_available["RHOHV"][:] = False
    out, _ = run(n, g, b)
    assert out["RV2_SOURCE_FOOTPRINT_QUALIFIED_MASK"][4, 140:160].all()


def test_serialized_replay_rejects_forged_proofs_and_restores_native_order():
    import pytest

    n, g, b = fixture()
    module = load("radial_revision.source_footprint")
    out, _ = run(n, g, b)
    out.update(module.evidence(n))
    out.update(g)
    module.validate(out, n.field_available["DBZH"], b)
    for key in (
        "REJECTION_CODE",
        "QUALIFIED_MASK",
        "RAW_PARENT_ID",
        "LEFT_DEG",
        "REFERENCE_BLOCKS",
        "NATIVE_ORDER",
        "NATIVE_RANGE_M",
    ):
        bad = {k: v.copy() for k, v in out.items()}
        bad[module.PREFIX + key][4, 140] += 1
        with pytest.raises(ValueError):
            module.validate(bad, n.field_available["DBZH"], b)
    permutation = np.roll(np.arange(n.shape[0]), 3)
    restored = {k: v[permutation] for k, v in out.items()}
    module.validate(restored, n.field_available["DBZH"][permutation], b[permutation])
    bad = {k: v.copy() for k, v in out.items()}
    bad["RV2_SOURCE_LEDGER_SEED_ID"][:] = 0
    with pytest.raises(ValueError):
        module.validate(bad, n.field_available["DBZH"], b)
    bad = {k: v.copy() for k, v in out.items()}
    bad[module.PREFIX + "NATIVE_GAP_MASK"][3] = 1
    with pytest.raises(ValueError):
        module.validate(bad, n.field_available["DBZH"], b)
    protected = b.copy()
    protected[:, 135] = 1
    protected[:, 165] = 1
    with pytest.raises(ValueError):
        module.validate(out, n.field_available["DBZH"], protected)


def test_serialized_polar_veto_is_recomputed_not_trusted_from_mask():
    import pytest

    n, g, b = fixture()
    module = load("radial_revision.source_footprint")
    out, _ = run(n, g, b)
    out.update(module.evidence(n))
    out.update(g)
    for name, value in (("RHOHV", 0.99), ("SNR", 20.0)):
        out[module.PREFIX + "MEASURED_" + name][4, 140:160] = value
        out[module.PREFIX + name + "_AVAILABLE_MASK"][4, 140:160] = 1
    with pytest.raises(ValueError):
        module.validate(out, n.field_available["DBZH"], b)


def test_engine_and_writer_integration_with_default_off_and_audit_no_action():
    import pytest

    from .conftest import evaluate
    from .test_fan_joint import fixture as fan_fixture

    n, source, tail = fan_fixture()
    source[8:15, (n.ranges >= 280000.0) & (n.ranges < 292000.0)] = True
    n.fields["DBZH"][source] = 20.0
    n.field_available["DBZH"] = np.isfinite(n.fields["DBZH"])
    n.fields["SNR"] = np.where(source, 30.0, np.nan).astype("float32")
    n.field_available["SNR"] = source.copy()
    for name, value in (("RHOHV", 0.99), ("ZDR", 0.5), ("PHIDP", 20.0)):
        n.fields[name] = np.where(source, value, np.nan).astype("float32")
        n.field_available[name] = source.copy()
    module = load("radial_revision.config")
    with pytest.raises(ValueError):
        module.FragmentLineConfig(source_footprint_enabled=True)
    assert not module.FragmentLineConfig().source_footprint_enabled
    cfg = module.RadialRevisionConfig(
        step=3,
        mode="experiment_quarantine",
        fragment_line={
            "raw_fragment_families_enabled": True,
            "source_ledger_enabled": True,
            "raw_fan_families_enabled": True,
            "source_footprint_enabled": True,
        },
    )
    out, _ = evaluate(n, cfg, source)
    assert out["RV2_SOURCE_FOOTPRINT_QUALIFIED_MASK"][tail].any()
    assert out["RV2_ACTION_PROPOSAL_MASK"][tail].any()
    load("radial_revision.validation").validate_revision_fields(
        out, n.field_available["DBZH"], source, np.zeros(n.shape, bool)
    )
    audit, _ = evaluate(n, cfg.model_copy(update={"mode": "audit"}), source)
    assert not audit["RV2_ACTION_PROPOSAL_MASK"].any()
    load("radial_revision.validation").validate_revision_fields(
        audit, n.field_available["DBZH"], source, np.zeros(n.shape, bool)
    )
    config = load("config").SourceReviewConfig(
        narrow_enabled=False, radial_revision=cfg.model_dump()
    )
    _, arrays, _ = load("source").source_additions(n, config, source, np.zeros(n.shape, "float32"))
    arrays["SRC_REVIEW_REFERENCE_FOLD_ID"] = source.astype("uint32")
    load("source_validation").validate_source_fields(arrays, n.field_available["DBZH"])
    assert "RV2_SOURCE_FOOTPRINT_NATIVE_ORDER" in arrays


def test_decision_trace_distinguishes_missing_anchor_extent_and_reference_failure():
    n, g, b = fixture()
    out, report = run(n, g, b)
    p = "RV2_SOURCE_FOOTPRINT_"
    assert (out[p + "REJECTION_CODE"][4, 140:160] == 0).all()
    assert (out[p + "REJECTION_CODE"][[2, 6], 10:290] == 4).all()
    assert sum(report["candidate_decisions"].values()) == report["candidate_gates"]
    n, g, b = fixture()
    g["RV2_SOURCE_LEDGER_SEED_ID"][:] = 0
    out, _ = run(n, g, b)
    assert (out[p + "REJECTION_CODE"][out[p + "CANDIDATE_MASK"] == 1] == 1).all()
    n, g, b = fixture()
    b[:, 135] = 1
    b[:, 165] = 1
    out, _ = run(n, g, b)
    assert (out[p + "REJECTION_CODE"][4, 140:160] == 6).all()
    n, g, b = fixture()
    n.gap_after[3] = 1
    out, _ = run(n, g, b)
    within = out[p + "CANDIDATE_MASK"][3:6] == 1
    assert (out[p + "REJECTION_CODE"][3:6][within] == 3).all()
    assert (out[p + "REJECTION_CODE"][[2, 6], 10:290] == 4).all()


def test_disconnected_original_bundles_track_separately_without_filling_gap():
    n = Native(np.full((12, 300), 20.0))
    parent = np.ones(n.shape, "uint32")
    seed = np.zeros_like(parent)
    seed[2:5, 10:290] = 1
    seed[7:10, 10:290] = 2
    seed[3, 140:160] = 0
    seed[8, 140:160] = 0
    group = {"RV2_RAW_FAN_ID": parent, "RV2_SOURCE_LEDGER_SEED_ID": seed}
    b = np.zeros(n.shape, bool)
    b[6] = True
    out, detail = run(n, group, b)
    qualified = out["RV2_SOURCE_FOOTPRINT_QUALIFIED_MASK"]
    assert qualified[3, 140:160].all() and qualified[8, 140:160].all()
    assert not qualified[5:7].any()
    assert detail["original_source_components"] == 2
    arrays = {**group, **out, **load("radial_revision.source_footprint").evidence(n)}
    load("radial_revision.source_footprint").validate(arrays, n.field_available["DBZH"], b)


def test_bundles_cannot_borrow_another_parents_source_or_short_islands_support():
    n = Native(np.full((12, 300), 20.0))
    parent = np.ones(n.shape, "uint32")
    parent[:, 150:] = 2
    seed = np.zeros_like(parent)
    seed[2:5, 150:] = 1
    seed[7:10, 10:30] = 2
    group = {"RV2_RAW_FAN_ID": parent, "RV2_SOURCE_LEDGER_SEED_ID": seed}
    out, _ = run(n, group, np.zeros(n.shape, bool))
    assert not out["RV2_SOURCE_FOOTPRINT_QUALIFIED_MASK"][2:5, :150].any()
    assert not out["RV2_SOURCE_FOOTPRINT_QUALIFIED_MASK"][7:10].any()


def test_anchored_integration_persists_actual_raw_and_replays_native_order():
    import pytest

    from .test_anchored_radial_shape import fixture as anchored_fixture

    n, b, g = anchored_fixture()
    module = load("radial_revision.source_footprint")
    old, _ = module.qualify(n, b, g, anchored_enabled=False)
    out, detail = module.qualify(n, b, g)
    assert not old[module.PREFIX + "QUALIFIED_MASK"].any()
    assert out[module.PREFIX + "ANCHORED_ADDED_MASK"][2, 155]
    assert out[module.PREFIX + "REJECTION_CODE"][2, 155] == 11
    assert detail["anchored_added_gates"] > 0 and detail["actions"] == 0
    arrays = {**g, **out, **module.evidence(n)}
    assert np.array_equal(arrays[module.PREFIX + "MEASURED_DBZH"], n.fields["DBZH"], equal_nan=True)
    module.validate(arrays, n.field_available["DBZH"], b)
    order = np.array([3, 0, 4, 1, 2])
    module.validate(
        {k: v[order] for k, v in arrays.items()}, n.field_available["DBZH"][order], b[order]
    )
    for key in ("ANCHORED_POLICY_CODE", "MEASURED_DBZH", "DBZH_AVAILABLE_MASK"):
        bad = {k: v.copy() for k, v in arrays.items()}
        bad[module.PREFIX + key][2, 155] = {
            "DBZH_AVAILABLE_MASK": 0,
            "ANCHORED_POLICY_CODE": 3,
            "MEASURED_DBZH": -1,
        }[key]
        with pytest.raises(ValueError):
            module.validate(bad, n.field_available["DBZH"], b)
    bad = {k: v.copy() for k, v in arrays.items()}
    bad["RV2_ANCHORED_RADIAL_SHAPE_SOURCE_ID"][2, 155] = 999
    with pytest.raises(ValueError):
        module.validate(bad, n.field_available["DBZH"], b)
    historical = {
        k: v
        for k, v in {**g, **old, **module.evidence(n)}.items()
        if k not in (module.PREFIX + "MEASURED_DBZH", module.PREFIX + "DBZH_AVAILABLE_MASK")
    }
    module.validate(historical, n.field_available["DBZH"], b)


def test_anchored_shape_reaches_engine_actions_writer_and_audit_mode():
    from .conftest import evaluate

    z = np.full((5, 340), np.nan, "float32")
    z[2, 20:320] = 25 + 8 * np.sin(np.arange(300) / 7)
    source = np.zeros(z.shape, bool)
    columns = np.arange(20, 320)
    source[2, columns[columns % 20 < 10]] = True
    n = Native(z, start=0, fields={"SNR": np.where(source, 40.0, 8.0)})
    for name, value in [("RHOHV", 0.8), ("ZDR", 3.0), ("PHIDP", 20.0)]:
        n.fields[name] = np.where(source, value, np.nan).astype("float32")
        n.field_available[name] = source.copy()
    original = n.fields["DBZH"].copy()
    cfg = load("radial_revision.config").RadialRevisionConfig(
        step=3,
        mode="experiment_quarantine",
        fragment_line={
            "raw_fragment_families_enabled": True,
            "source_ledger_enabled": True,
            "raw_fan_families_enabled": True,
            "source_footprint_enabled": True,
        },
    )
    enabled, _ = evaluate(n, cfg, source)
    assert enabled["RV2_SOURCE_FOOTPRINT_ANCHORED_ADDED_MASK"][2, 155]
    assert enabled["RV2_ACTION_PROPOSAL_MASK"][2, 155]
    assert np.array_equal(original, n.fields["DBZH"], equal_nan=True)
    load("radial_revision.validation").validate_revision_fields(
        enabled, n.field_available["DBZH"], source, np.zeros(n.shape, bool)
    )
    audit, _ = evaluate(n, cfg.model_copy(update={"mode": "audit"}), source)
    assert audit["RV2_SOURCE_FOOTPRINT_ANCHORED_ADDED_MASK"][2, 155]
    assert not audit["RV2_ACTION_PROPOSAL_MASK"].any()
    disabled, _ = evaluate(
        n,
        cfg.model_copy(
            update={
                "fragment_line": cfg.fragment_line.model_copy(
                    update={"source_footprint_enabled": False}
                )
            }
        ),
        source,
    )
    assert "RV2_SOURCE_FOOTPRINT_ANCHORED_ADDED_MASK" not in disabled
    review = load("config").SourceReviewConfig(
        narrow_enabled=False, radial_revision=cfg.model_dump()
    )
    _, fields, _ = load("source").source_additions(n, review, source, np.zeros(n.shape, "float32"))
    fields["SRC_REVIEW_REFERENCE_FOLD_ID"] = source.astype("uint32")
    assert fields["RV2_SOURCE_FOOTPRINT_ANCHORED_ADDED_MASK"][2, 155]
    load("source_validation").validate_source_fields(fields, n.field_available["DBZH"])


def test_original_fan_integration_keeps_native_proof_and_historical_policy():
    import pytest

    from .test_original_fan_shape import fixture as fan_fixture

    n, b, g = fan_fixture()
    m = load("radial_revision.source_footprint")
    old, _ = m.qualify(n, b, g, original_fan_enabled=False)
    new, report = m.qualify(n, b, g)
    assert not old[m.PREFIX + "QUALIFIED_MASK"][3, 155]
    assert new[m.PREFIX + "ORIGINAL_FAN_ADDED_MASK"][3, 155]
    assert new[m.PREFIX + "QUALIFIED_MASK"][3, 155]
    assert report["original_fan_added_gates"] > 0
    for out in (old, new):
        m.validate({**g, **out, **m.evidence(n)}, n.field_available["DBZH"], b)
    arrays = {**g, **new, **m.evidence(n)}
    for key in (
        "RV2_ORIGINAL_FAN_SHAPE_SOURCE_REFERENCE_MASK",
        "RV2_ORIGINAL_FAN_SHAPE_SOURCE_ID",
        m.PREFIX + "ORIGINAL_FAN_ADDED_MASK",
    ):
        bad = {k: v.copy() for k, v in arrays.items()}
        bad[key][3, 155] = 0 if bad[key][3, 155] else 1
        with pytest.raises(ValueError, match="replay differs"):
            m.validate(bad, n.field_available["DBZH"], b)


def test_complete_fan_proof_reaches_engine_writer_but_audit_does_not_act():
    from .conftest import evaluate
    from .test_original_fan_shape import fixture as fan_fixture

    native, _, group = fan_fixture()
    source = group["RV2_SOURCE_LEDGER_SEED_ID"] > 0
    n = Native(native.fields["DBZH"], start=0, fields={"SNR": np.where(source, 40.0, 8.0)})
    for name, value in [("RHOHV", 0.8), ("ZDR", 3.0), ("PHIDP", 20.0)]:
        n.fields[name] = np.where(source, value, np.nan).astype("float32")
        n.field_available[name] = source.copy()
    raw = n.fields["DBZH"].copy()
    cfg = load("radial_revision.config").RadialRevisionConfig(
        step=3,
        mode="experiment_quarantine",
        fragment_line={
            "raw_fragment_families_enabled": True,
            "source_ledger_enabled": True,
            "raw_fan_families_enabled": True,
            "source_footprint_enabled": True,
        },
    )
    active, _ = evaluate(n, cfg, source)
    assert active["RV2_SOURCE_FOOTPRINT_ORIGINAL_FAN_ADDED_MASK"][3, 155]
    assert active["RV2_ACTION_PROPOSAL_MASK"][3, 155]
    load("radial_revision.validation").validate_revision_fields(
        active, n.field_available["DBZH"], source, np.zeros(n.shape, bool)
    )
    audit, _ = evaluate(n, cfg.model_copy(update={"mode": "audit"}), source)
    assert audit["RV2_SOURCE_FOOTPRINT_ORIGINAL_FAN_ADDED_MASK"][3, 155]
    assert not audit["RV2_ACTION_PROPOSAL_MASK"].any()
    review = load("config").SourceReviewConfig(
        narrow_enabled=False, radial_revision=cfg.model_dump()
    )
    _, fields, _ = load("source").source_additions(n, review, source, np.zeros(n.shape, "float32"))
    fields["SRC_REVIEW_REFERENCE_FOLD_ID"] = source.astype("uint32")
    assert fields["RV2_SOURCE_FOOTPRINT_ORIGINAL_FAN_ADDED_MASK"][3, 155]
    load("source_validation").validate_source_fields(fields, n.field_available["DBZH"])
    assert np.array_equal(raw, n.fields["DBZH"], equal_nan=True)
