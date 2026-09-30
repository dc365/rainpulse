from dataclasses import replace
import copy
from types import ModuleType
from pathlib import Path
import numpy as np
import pytest
from rainpulse_algo.radar.qc_engine.volume_review.data import Sweep, ResourceLimit
from rainpulse_algo.multiband.xqc_v2.source_summary import SourceStatistics
from rainpulse_algo.multiband.xqc_v2 import source_blocks, source_fans, radial_source
from rainpulse_algo.multiband.xqc_v2.geometry import adapt
from .helpers import fixture,config
from .reference import source_blocks as old_blocks
from .reference import source_fans as old_fans


def dense(count):
    nr=count+2;ng=180;r=500+400*np.arange(ng,dtype=float);az=.1*np.arange(nr)
    sn=np.full((nr,ng),44.,dtype='float32');sn[[0,-1]]=-4.
    z=(sn+20*np.log10(r/1000)-22).astype('float32');z[[0,-1]]=np.nan
    gap=np.zeros(nr,bool);gap[-1]=True
    return Sweep('sweep_000',az,np.full(nr,.5),r,{'DBZH':z,'SNR':sn},
        {'DBZH':np.isfinite(z),'SNR':np.isfinite(sn)},np.ones(nr,bool),gap,
        np.arange(nr,dtype=float))


def policy():
    return config(radial_source_enabled=True,noise_censor_snr_db=3.,
        radial_source_fan_model_enabled=True,source_maximum_trials=2_000_000)


@pytest.mark.parametrize('count',[255,256,257,258,259,1000])
def test_support_reducer_saturates_without_wrap(monkeypatch,count):
    s=dense(count);c=policy()
    # Isolate the reducer with explicit synthetic prequalified block models.
    candidate=s.observed.copy()
    def supplied(s,cfg,*,protected,fan=False,family_width_deg=None,prepared=None):
        value=np.zeros(s.shape,bool) if family_width_deg is not None else candidate.copy()
        return value,{'models':[],'source_gates':int(value.sum())}
    monkeypatch.setattr(source_fans,'detect_blocks',supplied)
    out,_=source_fans.detect(s,c,protected=np.zeros(s.shape,bool))
    np.testing.assert_array_equal(out,candidate)


def test_full_dense_counterexample_matches_wide_counter_reference():
    s=dense(257);c=policy();p=np.zeros(s.shape,bool)
    broken,old_record=old_fans.detect(s,c,protected=p)
    assert not broken.any() and old_record['proposed_gates']>0
    out,record=source_fans.detect(s,c,protected=p)
    assert out.sum()==record['proposed_gates'] and out.sum()>40000
    # One deliberate in-memory uint16 change is the count-only reference.
    source=Path(old_fans.__file__).read_text().replace('np.zeros(s.shape, np.uint8)','np.zeros(s.shape, np.uint16)')
    mod=ModuleType('wide_count');mod.__package__=old_fans.__package__
    exec(compile(source,'wide-count-reference','exec'),mod.__dict__)
    wide,wr=mod.detect(s,c,protected=p)
    np.testing.assert_array_equal(out,wide)
    assert record==wr


@pytest.mark.parametrize('fan',[False,True])
def test_prepared_summary_keeps_models_and_heldout_intervals(fan):
    s=dense(9);c=policy();p=np.zeros(s.shape,bool)
    a,old=old_blocks.detect(s,c,protected=p,fan=fan)
    stats=SourceStatistics.build(s,c)
    b,new=source_blocks.detect(s,c,protected=p,fan=fan,prepared=stats)
    np.testing.assert_array_equal(a,b);assert old==new
    assert not stats.receiver.flags.writeable
    for m in new['models']:
        assert all(abs(k-m['target_block'])>c.receiver.guard_blocks for k in m['reference_blocks'])
    with pytest.raises(ValueError):stats.use(dense(9),c)
    with pytest.raises(ValueError):stats.use(s,c.model_copy(update={'radial_flank_contrast_db':12.}))


@pytest.mark.parametrize('field,limit',[('source_maximum_trials',1),('source_maximum_models',1),('source_maximum_summary_bytes',4096)])
def test_source_resource_budget_is_explicit(field,limit):
    s=dense(9);c=policy().model_copy(update={field:limit})
    with pytest.raises(ResourceLimit):source_fans.detect(s,c,protected=np.zeros(s.shape,bool))


def test_source_zero_protected_and_no_quiet_telemetry_does_not_fake_noecho():
    s=dense(9);c=policy()
    assert not source_fans.detect(s,c,protected=np.ones(s.shape,bool))[0].any()
    a=dict(s.available);sn=a['SNR'].copy();sn[[0,-1]]=False;a['SNR']=sn
    missing=replace(s,available=a)
    assert not source_fans.detect(missing,c,protected=np.zeros(s.shape,bool))[0].any()


def test_branch_ledger_counts_union_not_multiple_independent_votes():
    s=dense(9);c=policy();details={}
    out,rec=radial_source.detect(s,c,protected=np.zeros(s.shape,bool),details=details)
    kinds=details['source_kind']
    np.testing.assert_array_equal(kinds>0,out)
    assert rec['kind_counts']['union']==int(out.sum())
    assert not rec['kind_bits_are_independent_votes']
    assert rec['work']['receiver_summary_builds']==1
    assert not rec['work']['fitted_model_cache']


def test_source_budget_does_not_discard_independent_parent_candidates(monkeypatch):
    from rainpulse_algo.multiband.xqc_v2 import core
    v,_=fixture('empty',rays=120,gates=80)
    c=config(receiver_enabled=False,radial_objects_enabled=False,clutter_enabled=False,isolation_enabled=False,
        noise_censor_snr_db=3.,noise_censor_maximum_fraction=1.,maximum_new_exclusion_fraction=1.)
    def limited(*args,**kwargs):raise ResourceLimit('fixture source budget')
    monkeypatch.setattr(radial_source,'detect',limited)
    ev=core.evaluate_cut(v.sweeps[0],v.metadata,c)
    assert ev.record['module_records']['radial_source']['status']=='RESOURCE_LIMIT_ABSTAINED'
    assert ev.record['status']=='DEGRADED_SOURCE_RESOURCE_LIMIT'
    assert not ev.arrays['XQC_RADIAL_SOURCE_MASK'].any()
    assert ev.arrays['XQC_NOISE_FLOOR_MASK'].any() and ev.arrays['XQC_PROPOSED_MASK'].any()
    assert not ev.arrays['XQC_SOURCE_KIND'].any()


def test_target_and_guard_changes_do_not_train_their_own_block_model():
    s=dense(9);c=policy();p=np.zeros(s.shape,bool)
    _,before=source_blocks.detect(s,c,protected=p,fan=True)
    fields={k:v.copy() for k,v in s.fields.items()}
    blocks=(s.ranges//c.receiver.block_m).astype(int);target=7
    held=np.abs(blocks-target)<=c.receiver.guard_blocks
    fields['SNR'][4,held]+=.2;fields['DBZH'][4,held]+=.2
    changed=replace(s,fields=fields)
    _,after=source_blocks.detect(changed,c,protected=p,fan=True)
    a=[x for x in before['models'] if x['ray']==4 and x['target_block']==target]
    b=[x for x in after['models'] if x['ray']==4 and x['target_block']==target]
    assert a and a==b


def test_duplicate_seed_enumeration_does_not_exhaust_model_budget():
    s=dense(9);c=policy().model_copy(update={'source_maximum_trials':5000})
    expected,_=radial_source.detect(s,policy(),protected=np.zeros(s.shape,bool))
    actual,record=radial_source.detect(s,c,protected=np.zeros(s.shape,bool))
    np.testing.assert_array_equal(actual,expected)
    assert record['status']=='EVALUATED'
    assert record['work']['model_trials']<=5000


def test_later_source_budget_preserves_completed_continuous_stage(monkeypatch):
    s=dense(9);c=policy();details={}
    fields={k:v.copy() for k,v in s.fields.items()}
    fields['DBZH']-=30.;fields['SNR'][1:-1]-=30.
    s=replace(s,fields=fields)
    continuous_cfg=c.model_copy(update={'radial_source_fan_model_enabled':False})
    expected,_=radial_source.detect(s,continuous_cfg,protected=np.zeros(s.shape,bool))
    assert expected.any()
    def limited(*args,**kwargs):raise ResourceLimit('fixture later fan budget')
    monkeypatch.setattr(source_fans,'detect',limited)
    actual,record=radial_source.detect(s,c,protected=np.zeros(s.shape,bool),details=details)
    np.testing.assert_array_equal(actual,expected)
    assert record['status']=='PARTIAL_RESOURCE_LIMIT'
    assert record['failed_module']=='fan'
    assert record['complete'] is False
    assert record['source_gates']==int(actual.sum())
    np.testing.assert_array_equal(details['source_kind']>0,actual)


def test_geometry_steps_do_not_consume_model_trials():
    from rainpulse_algo.multiband.xqc_v2.source_summary import SourceStatistics
    s=dense(9);c=policy().model_copy(update={'source_maximum_trials':1})
    stats=SourceStatistics.build(s,c)
    for _ in range(100):stats.geometry()
    assert stats.receipt()['model_trials']==0
    assert stats.receipt()['geometry_comparisons']==100
    stats.trial()
    with pytest.raises(ResourceLimit,match='model-trial'):stats.trial()
    with pytest.raises(ResourceLimit,match='geometry'):
        stats.geometry(32*s.shape[0]**2)


def test_stationary_upper_receiver_mode_survives_changing_lower_mode():
    s=dense(9);c=policy();f={k:v.copy() for k,v in s.fields.items()}
    row=5;low=np.arange(s.shape[1])%3==0
    f['SNR'][row,low]=np.linspace(14.,38.,s.shape[1])[low]
    f['DBZH'][row]=f['SNR'][row]+20*np.log10(s.ranges/1000)-22
    s=replace(s,fields=f);out,_=source_blocks.detect(s,c,protected=np.zeros(s.shape,bool),fan=True)
    upper=(~low)&(s.ranges>=c.receiver.minimum_range_m)
    assert np.mean(out[row,upper])>.95
    # The same receiver modes cannot establish a source from a flat rain REF.
    f={k:v.copy() for k,v in s.fields.items()};f['DBZH'][row]=28.
    flat=replace(s,fields=f);weather,_=source_blocks.detect(flat,c,protected=np.zeros(flat.shape,bool),fan=True)
    assert not weather[row].any()
