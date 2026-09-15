import numpy as np
from .test_fragment_radials import scene
from .test_residual_v6 import config, v5_decision
from rainpulse_algo.radar.qc_engine.interrupted_objects import interrupted_objects
from rainpulse_algo.radar.qc_engine.fragment_radials import FragmentConfig, apply_fragment_decision


def test_fragment_identity_requires_anchor_and_never_fills_missing():
    n = scene()
    anchor = np.zeros(n.shape, bool)
    assert not interrupted_objects(n, anchor)[0].any()
    anchor[5, 84:104] = True
    candidate, ids, records = interrupted_objects(n, anchor)
    assert candidate.any() and records
    assert not candidate[~n.field_available["DBZH"]].any()
    assert not candidate[5, n.ranges > n.ranges[0] + 60000].any()
    assert not ids[~candidate].any()


def test_identity_alone_cannot_reject_weather():
    n = scene()
    before = v5_decision(n)
    before.arrays["QC_ACTION"][5, 84:104] = 2
    before.flags[5, 84:104] |= config().flag_masks["RADIAL_INTERFERENCE"]
    n.fields["RHOHV"][:] = 0.99
    saved = n.fields["DBZH"].copy()
    after, stats = apply_fragment_decision(
        n, before, FragmentConfig(interrupted_objects_enabled=True), config()
    )
    assert after.arrays["FRAGMENT_OBJECT_CANDIDATE_MASK"].any()
    assert not after.arrays["FRAGMENT_CONFIRMED_ADDITION_MASK"].any()
    np.testing.assert_equal(n.fields["DBZH"], saved)


def test_object_local_evidence_confirms_but_cross_weather_conflicts():
    n = scene()
    before = v5_decision(n)
    # Remove shoulder support: the original contrast route cannot nominate gates.
    for ray in range(n.shape[0]):
        if ray != 5:
            n.fields["DBZH"][ray] = np.nan
            n.field_available["DBZH"][ray] = False
    before.arrays["QC_ACTION"][5, 84:104] = 2
    before.flags[5, 84:104] |= config().flag_masks["RADIAL_INTERFERENCE"]
    cfg = FragmentConfig(interrupted_objects_enabled=True)
    old, _ = apply_fragment_decision(n, before, FragmentConfig(), config())
    assert not old.arrays["FRAGMENT_CONFIRMED_ADDITION_MASK"].any()
    after, _ = apply_fragment_decision(n, before, cfg, config())
    assert after.arrays["FRAGMENT_CONFIRMED_ADDITION_MASK"].any()
    protected, _ = apply_fragment_decision(n, before, cfg, config(), cross_support=np.ones(n.shape))
    assert not protected.arrays["FRAGMENT_CONFIRMED_ADDITION_MASK"].any()
    assert protected.arrays["FRAGMENT_OBJECT_CONFLICT_MASK"].any()
