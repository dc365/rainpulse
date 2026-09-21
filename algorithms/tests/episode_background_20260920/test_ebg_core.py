from dataclasses import replace
from datetime import timedelta
import numpy as np
import pytest
from episode_helpers import *
from volume_review.episode_background.core import evaluate,State,applicable
from volume_review.episode_background.data import map_footprints

@pytest.fixture(scope='module')
def bg():return model()


def test_supported_partial_target(bg):
    s=target(missing=('ZDR',));before=s.raw_digest
    e=evaluate(s,bg,EpisodeConfig())
    assert e.arrays['EBG_ACTION_CANDIDATE_MASK'].all()
    assert e.arrays['EBG_PARTIAL_MASK'].all()
    assert s.raw_digest==before
    assert not e.summary['probabilities_calibrated']


def test_tail_partial_route_and_wrong_tail_not_accepted():
    bg=model(episode(zdr=8.))
    a=evaluate(target(zdr=8.),bg,EpisodeConfig()).arrays
    assert a['EBG_TAIL_STATE_MATCH_MASK'].all()
    assert a['EBG_ACTION_CANDIDATE_MASK'].all()
    b=evaluate(target(zdr=-8.),bg,EpisodeConfig()).arrays
    assert not b['EBG_ACTION_CANDIDATE_MASK'].any()


@pytest.mark.parametrize('missing',[('RHOHV',),('SNR',),('RHOHV','PHIDP','ZDR'),('SNR','PHIDP','RHOHV','ZDR')])
def test_missing_evidence_never_renormalized_to_confident(bg,missing):
    e=evaluate(target(missing=missing),bg,EpisodeConfig())
    assert not e.arrays['EBG_ACTION_CANDIDATE_MASK'].any()


@pytest.mark.parametrize('kwargs',[{'z':35.},{'z':9.},{'snr':2.},{'rho':.99},{'z':-20.}])
def test_weather_enhancement_strong_weak_noecho_controls(bg,kwargs):
    e=evaluate(target(**kwargs),bg,EpisodeConfig())
    assert not e.arrays['EBG_ACTION_CANDIDATE_MASK'].any()


def test_local_high_rho_background_stays_mixed_not_confident_weather():
    bg=model(episode(rho=.99))
    cfg=EpisodeConfig(review_local_weather_conflicts=True,withhold_mixed=True)
    e=evaluate(target(rho=.99),bg,cfg)
    assert (e.arrays['EBG_STATE'][:,1:]==State.MIXED).all()
    assert not e.arrays['EBG_SUPPORTED_MASK'].any()
    for protection in ['hard_weather','legacy_protected']:
        ev=evaluate(target(rho=.99),bg,cfg,**{protection:np.ones(target().shape,bool)})
        assert not ev.arrays['EBG_ACTION_CANDIDATE_MASK'].any()


def test_local_review_opt_in_only(bg):
    s=target();local=np.ones(s.shape,bool)
    assert not evaluate(s,bg,EpisodeConfig(),local_weather=local).arrays['EBG_ACTION_CANDIDATE_MASK'].any()
    assert evaluate(s,bg,EpisodeConfig(review_local_weather_conflicts=True,withhold_mixed=True),local_weather=local).arrays['EBG_ACTION_CANDIDATE_MASK'].all()


def test_missing_model_preserves_diagnostic_state(bg):
    a=evaluate(target(),None,EpisodeConfig()).arrays
    assert (a['EBG_STATE']==State.MODEL_UNAVAILABLE).all()
    assert not a['EBG_ACTION_CANDIDATE_MASK'].any()


@pytest.mark.parametrize('fault,expected',[
    ('future','FUTURE_BACKGROUND_REFUSED'),('old','BACKGROUND_TOO_OLD'),('inside','TARGET_IN_TRAINING_INTERVAL'),
    ('station','RADAR_MISMATCH'),('processor','PROCESSING_MISMATCH'),('sweep','SWEEP_NOT_BOUND')])
def test_applicability(bg,fault,expected):
    s=target()
    if fault=='future':s=replace(s,observed_at=(T0-timedelta(days=1)).isoformat())
    if fault=='old':s=replace(s,observed_at=(T0+timedelta(days=3)).isoformat())
    if fault=='inside':s=replace(s,observed_at=(T0+timedelta(minutes=50)).isoformat())
    if fault=='station':s=replace(s,radar_id='another')
    if fault=='processor':s=replace(s,processing_id='another')
    if fault=='sweep':s=replace(s,sweep_id='another')
    e=evaluate(s,bg,EpisodeConfig());assert e.summary['status']==expected
    assert not e.arrays['EBG_ACTION_CANDIDATE_MASK'].any()


def test_overlap_always_refused_even_retrospective(bg):
    c=EpisodeConfig(temporal_policy='retrospective',retrospective_target_dates=('2026-09-18',))
    with pytest.raises(ValueError,match='overlap'):evaluate(replace(target(),scan_id='scan-0'),bg,c)
    with pytest.raises(ValueError):evaluate(target(),bg,EpisodeConfig(mode='experiment_cr'),validation_holdout=True)


def test_retrospective_dates_explicit(bg):
    s=replace(target(),observed_at='2026-08-28T00:12:00+00:00')
    c=EpisodeConfig(temporal_policy='retrospective',retrospective_target_dates=('2026-08-28',))
    assert evaluate(s,bg,c).arrays['EBG_ACTION_CANDIDATE_MASK'].all()
    assert not evaluate(replace(s,observed_at='2026-08-29T00:12:00Z'),bg,c).arrays['EBG_ACTION_CANDIDATE_MASK'].any()


def test_geometry_rotation_row_order_range_gap_and_elevation(bg):
    s=target();order=np.arange(s.shape[0])[::-1]
    q=replace(s,azimuth=s.azimuth[order],elevation=s.elevation[order],fields={k:v[order] for k,v in s.fields.items()},
              available={k:v[order] for k,v in s.available.items()})
    assert evaluate(q,bg,EpisodeConfig()).arrays['EBG_ACTION_CANDIDATE_MASK'].all()
    q=replace(s,azimuth=s.azimuth+100)
    assert not evaluate(q,bg,EpisodeConfig()).arrays['EBG_ACTION_CANDIDATE_MASK'].any()
    q=replace(s,elevation=s.elevation+1.)
    assert not evaluate(q,bg,EpisodeConfig()).arrays['EBG_ACTION_CANDIDATE_MASK'].any()
    q=replace(s,ranges=s.ranges+100000.)
    assert not evaluate(q,bg,EpisodeConfig()).arrays['EBG_ACTION_CANDIDATE_MASK'].any()


def test_missing_observation_never_filled(bg):
    s=target();s.fields['DBZH'][:,10:15]=np.nan;s.available['DBZH'][:,10:15]=False
    e=evaluate(s,bg,EpisodeConfig())
    assert (e.arrays['EBG_STATE'][:,10:15]==State.MISSING).all()
    assert not e.arrays['EBG_ACTION_CANDIDATE_MASK'][:,10:15].any()


def test_geometry_no_interpolation_over_large_gaps():
    c=BuildConfig()
    rows,gates,ok,_,_=map_footprints(np.array([0,1,20,21]),np.full(4,.5),np.array([1000,1250,5000,5250]),
        np.ones(4,bool),np.array([.2,10.,20.2]),np.full(3,.5),np.array([1000,3000,5000]),c)
    assert not ok[1].any() and not ok[:,1].any()
    assert ok[0,0] and ok[2,2]


def test_duplicate_target_rays_not_treated_as_independent_observations(bg):
    s=target();s.azimuth[1]=s.azimuth[0]
    e=evaluate(s,bg,EpisodeConfig())
    assert not e.arrays['EBG_ACTION_CANDIDATE_MASK'][:2].any()
