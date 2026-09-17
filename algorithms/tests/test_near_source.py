import numpy as np

from rainpulse_algo.radar.qc_engine.broad_source import BroadSourceConfig, infer_broad_source

from .test_broad_source import scene


def test_near_targets_do_not_train_far_model_and_preserve_vetoes():
    n = scene()
    n.ranges = np.arange(125, 400000, 250.0)
    n.fields["DBZH"][:] = 15 + 20 * np.log10(n.ranges[None, :] / 1000)
    near = n.ranges < 50000
    old, _ = infer_broad_source(n, BroadSourceConfig())
    cfg = BroadSourceConfig(near_range_targets=True)
    weather = np.zeros(n.shape, bool)
    weather[4, 60:70] = True
    conflict = np.zeros(n.shape, bool)
    conflict[4, 80:90] = True
    new, summary = infer_broad_source(n, cfg, weather=weather, conflicts=conflict)
    assert not old["BWS_CANDIDATE_MASK"][:, near].any()
    assert new["BWS_CANDIDATE_MASK"][:, near].sum() > 100
    assert not new["BWS_CANDIDATE_MASK"][4, 60:70].any()
    assert not new["BWS_CANDIDATE_MASK"][4, 80:90].any()
    n.fields["DBZH"][:, near] += 20
    changed, after = infer_broad_source(n, cfg)
    assert not changed["BWS_CANDIDATE_MASK"][:, near].any()
    a = [v for v in summary["folds"] if v["target_block"] == -1]
    b = [v for v in after["folds"] if v["target_block"] == -1]
    assert a and a == b


def test_near_polar_conflict_cannot_be_bypassed_by_shape_or_edge():
    n = scene()
    n.ranges = np.arange(125, 400000, 250.0)
    n.fields["DBZH"][:] = 15 + 20 * np.log10(n.ranges[None, :] / 1000)
    near = n.ranges < 50000
    cfg = BroadSourceConfig(near_range_targets=True, radial_opening=True, source_edge=True)
    before, _ = infer_broad_source(n, cfg)
    assert before["BWS_CANDIDATE_MASK"][5, near].any()
    n.fields["PHIDP"][5, near] += 90
    after, _ = infer_broad_source(n, cfg)
    assert not after["BWS_CANDIDATE_MASK"][5, near].any()
    assert np.array_equal(
        before["BWS_CANDIDATE_MASK"][:, ~near], after["BWS_CANDIDATE_MASK"][:, ~near]
    )
