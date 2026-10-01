"""Complete RAW fragment families must qualify with held-out physical evidence."""
import numpy as np
import pytest
from rainpulse_algo.multiband.xqc_v2.geometry import adapt
from rainpulse_algo.multiband.xqc_v2.fragment_source import detect
from .helpers import config, fixture


def sample(bearing=70, elevation=.5):
    v, _ = fixture('empty', rays=360, gates=3000)
    cut = v.sweeps[0]
    cut.elevation_deg[:] = elevation
    f, r = cut.fields, cut.range_m
    f['DBZH'][:] = np.nan
    f['SNRH'][:] = -4
    target = (r>=50000) & (r<160000) & (np.arange(len(r))%4!=0)
    f['SNRH'][bearing,target]=5
    f['DBZH'][bearing,target]=5+20*np.log10(r[target]/1000)-26
    f['OBSERVED_MASK'][:]=np.isfinite(f['DBZH'])
    cfg=config(noise_censor_snr_db=3,radial_source_enabled=True)
    return cut,cfg,target


@pytest.mark.parametrize('bearing,elevation', [(0,.5), (70,3.36), (359,14.5)])
def test_weak_intermittent_receiver_source_qualifies_without_polar_exception(bearing,elevation):
    cut,cfg,target=sample(bearing,elevation)
    s=adapt(cut,cfg).sweep
    before=s.digest
    mask,record=detect(s,cfg,protected=np.zeros(s.shape,bool))
    assert mask[bearing,target].mean()>.95
    assert not mask[~s.observed].any()
    assert before==s.digest
    assert record['diagnostic_only'] and record['rho_is_weather_truth'] is False
    for family in record['families']:
        for mode in family['modes']:
            for model in mode['models']:
                assert model['target_block'] not in model['reference_blocks']
                assert all(abs(b-model['target_block'])>cfg.receiver.guard_blocks for b in model['reference_blocks'])


def test_protected_gates_and_local_enhancement_do_not_inherit_source():
    cut,cfg,target=sample()
    r=cut.range_m
    enhanced=(r>=100000)&(r<105000)
    cut.fields['SNRH'][70,enhanced]+=15
    cut.fields['DBZH'][70,enhanced]+=15
    s=adapt(cut,cfg).sweep
    protected=np.zeros(s.shape,bool);protected[70,(r>=75000)&(r<80000)]=True
    mask,_=detect(s,cfg,protected=protected)
    assert mask.any()
    assert not mask[protected|np.broadcast_to(enhanced,s.shape)].any()


def test_range_varying_weather_is_not_qualified_by_fragment_geometry():
    cut,cfg,target=sample()
    r=cut.range_m
    cut.fields['DBZH'][70,target]=25+5*np.sin(r[target]/7000)
    cut.fields['SNRH'][70,target]=cut.fields['DBZH'][70,target]-20*np.log10(r[target]/1000)+26
    s=adapt(cut,cfg).sweep
    assert not detect(s,cfg,protected=np.zeros(s.shape,bool))[0].any()


def test_separated_short_families_cannot_pool_reference_range():
    cut,cfg,_=sample()
    r=cut.range_m
    keep=((r>=50000)&(r<62000))|((r>=130000)&(r<142000))
    cut.fields['DBZH'][70,~keep]=np.nan
    cut.fields['SNRH'][70,~keep]=-4
    cut.fields['OBSERVED_MASK'][:]=np.isfinite(cut.fields['DBZH'])
    s=adapt(cut,cfg).sweep
    mask,record=detect(s,cfg,protected=np.zeros(s.shape,bool))
    assert not mask.any()
    assert not record['families']


def test_unknown_receiver_is_not_filled_and_global_model_limit_abstains():
    from rainpulse_algo.radar.qc_engine.volume_review.data import ResourceLimit
    cut,cfg,_=sample()
    cut.fields['SNRH'][70,900:1000]=np.nan
    s=adapt(cut,cfg).sweep
    mask,_=detect(s,cfg,protected=np.zeros(s.shape,bool))
    assert not mask[70,900:1000].any()
    cut,cfg,_=sample()
    cfg=cfg.model_copy(update={'source_maximum_trials':1})
    s=adapt(cut,cfg).sweep
    with pytest.raises(ResourceLimit,match='model-trial'):
        detect(s,cfg,protected=np.zeros(s.shape,bool))
