import numpy as np
import pytest
from rainpulse_algo.radar.qc_engine.review_extension.clutter_score import score_candidate


def run(**changes):
    x=dict(z=np.array([0.]),eligible=np.array([1]),protected=np.array([0]),
        background=np.array([1]),low_snr=np.array([1]),polarimetric=np.array([0]),
        temporal=np.array([0]),doppler=np.array([0]),texture=np.array([0]))
    x.update(changes)
    return score_candidate(**x)[0].item()


def test_correlated_persistence_does_not_make_third_family():
    assert not run(temporal=np.array([1]))


def test_single_texture_can_support_three_families():
    assert run(texture=np.array([1]))
    assert not run(texture=np.array([1]),omit='texture')


@pytest.mark.parametrize('changes',[{'protected':np.array([1])},{'z':np.array([np.nan])},
    {'z':np.array([35.])},{'eligible':np.array([0])}])
def test_protection_and_observation_barriers(changes):
    assert not run(texture=np.array([1]),**changes)


def test_unknown_evidence_not_counted():
    with pytest.raises(ValueError):run(texture=np.array([np.nan]))


def test_low_snr_pol_cannot_double_count():
    assert not run(polarimetric=np.array([1]))


def test_ablation_never_creates_new_candidates():
    rng=np.random.default_rng(12);shape=(1000,)
    kwargs={k:rng.integers(0,2,shape) for k in ('eligible','protected','background','low_snr','polarimetric','temporal','doppler','texture')}
    kwargs['z']=np.zeros(shape)
    baseline,_,_=score_candidate(**kwargs)
    saved={k:v.copy() for k,v in kwargs.items()}
    for name in ('background','low_snr','polarimetric','temporal','doppler','texture'):
        subset,_,_=score_candidate(**kwargs,omit=name)
        assert not (subset&~baseline).any()
    for k in kwargs:np.testing.assert_array_equal(kwargs[k],saved[k])
