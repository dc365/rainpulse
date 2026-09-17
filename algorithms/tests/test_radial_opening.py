import numpy as np

from rainpulse_algo.radar.qc_engine.radial_opening import radial_opening


def test_opening_raw_length_missing_and_no_input_mutation():
    z = np.full((3, 800), 50.0)
    z[0, 300:310] = np.nan
    z[1, 100:] = 10
    valid = np.isfinite(z)
    before = z.copy()
    mask = radial_opening(z, valid, 250.0)
    assert mask[2, 400]
    assert not mask[1].any()
    assert not mask[0, 300:310].any()
    assert np.array_equal(z, before, equal_nan=True)


def test_source_constrained_opening_preserves_weather_enhancement_and_audit():
    from rainpulse_algo.radar.qc_engine.broad_source import BroadSourceConfig, infer_broad_source

    from .test_broad_source import distance_scene

    n, near = distance_scene()
    n.fields["PHIDP"][:, near] += 40
    weather = np.zeros(n.shape, bool)
    weather[5, :50] = True
    n.fields["DBZH"][5, 60:90] += 10
    old, _ = infer_broad_source(n, BroadSourceConfig(), weather=weather)
    new, _ = infer_broad_source(n, BroadSourceConfig(radial_opening=True), weather=weather)
    assert new["BWS_CANDIDATE_MASK"][:, near].sum() > old["BWS_CANDIDATE_MASK"][:, near].sum()
    assert not new["BWS_CANDIDATE_MASK"][5, :50].any()
    assert not new["BWS_CANDIDATE_MASK"][5, 60:90].any()
    assert ((new["BWS_REASON"][:, near] & 64) != 0).any()
    assert ((new["BWS_REASON"][:, near] & 16) != 0).any()
