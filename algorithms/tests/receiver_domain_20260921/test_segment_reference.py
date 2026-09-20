"""Finite receiver states: positive cases plus hard-negative/metamorphic contracts."""
from dataclasses import replace
import copy
import json
import numpy as np
import pytest
from volume_review.data import Sweep, ResourceLimit
from volume_review.config import VolumeReviewConfig
from volume_review.receiver_domain.config import ReceiverDomainConfig as C, SegmentReferenceConfig as S
from volume_review.receiver_domain.core import evaluate, domains, fit_fold, abstained
from volume_review.receiver_domain.segment_reference import fit_segment_models, project_segment_models
from volume_review.receiver_domain.disposition import apply, CR
from volume_review.receiver_domain.integration import attributes
from volume_review.receiver_domain.validation import validate_serialized
from test_receiver import baseline, altered


def finite_scene(level=44., phase=275., shift=0., dr=500.):
    r = np.arange(125., 260000., dr)
    az = (np.arange(9.)+102.+shift) % 360
    sn = np.broadcast_to((10.+np.sin(r/13000))[None, :], (9, len(r))).copy()
    z = np.broadcast_to((16.+np.sin(r/5000))[None,:], sn.shape).copy()
    active = ((r >= 12000) & (r < 78000)) | ((r >= 115000) & (r < 123500))
    sn[4] = -5.; sn[4,active] = level
    z[4] = np.nan
    z[4,active] = level+20*np.log10(r[active]/1000.)+.01*r[active]/1000.-41.5
    ph=np.full_like(sn,40.);ph[4,active]=phase
    zdr=np.full_like(sn,.7);zdr[4,active]=-.6
    rho=np.full_like(sn,.98)
    f=dict(DBZH=z,SNR=sn,PHIDP=ph,ZDR=zdr,RHOHV=rho)
    gaps=np.zeros(9,bool);gaps[-1]=True
    s=Sweep('sweep_004',az,2.39,r,f,{k:np.isfinite(v) for k,v in f.items()},np.ones(9,bool),gaps)
    return s


def config(**kwargs):
    seg=kwargs.pop('seg',S(mode='experiment'))
    return C(mode='quarantine',local_policy='source_joint_review',segment_reference=seg,**kwargs)


def tail(s):
    return (s.ranges>=115000)&(s.ranges<123500)


def test_finite_source_fallback_and_end_of_source_noise():
    s=finite_scene();old=evaluate(s,C());before=s.digest
    assert not old.arrays['RDR_FULL_MATCH_MASK'][4,tail(s)].any()
    c=config();e=evaluate(s,c)
    assert e.arrays['RDR_SOURCE_MASK'][4,tail(s)].all()
    assert e.arrays['RDR_SEGMENT_REFERENCE_MASK'][4,tail(s)].all()
    assert e.arrays['RDR_SEGMENT_REFERENCE_DISTANCE_M'][4,tail(s)].max()<=50000
    assert s.digest==before
    assert not e.arrays['RDR_FULL_MATCH_MASK'][~s.observed].any()
    assert not e.arrays['RDR_SOURCE_MASK'][[0,1,2,3,5,6,7,8]].any()
    for m in e.models:
        if m.get('reference_route')=='finite_receiver_state':
            assert len(m['reference_cross_predictions'])==2
            assert m['pair_error_db']<=c.maximum_relation_error_db


@pytest.mark.parametrize('block',[3,5,6])
def test_state_discovery_and_shoulders_exclude_target_guard(block):
    s=finite_scene();c=config();models,_=fit_segment_models(s,4,block,c)
    assert models
    band=abs((s.ranges//c.block_m).astype(int)-block)<=c.guard_blocks
    f={k:v.copy() for k,v in s.fields.items()}
    for v in f.values():v[:,band]=np.nan
    modified=replace(s,fields=f,available={k:np.isfinite(v) for k,v in f.items()})
    other,_=fit_segment_models(modified,4,block,c)
    assert models==other
    for m in models:
        for key in ['snr_intervals','pair_intervals','polar_intervals']:
            for lo,hi in m[key]:assert not band[lo:hi].any()
        for side in m['shoulders']:
            for lo,hi in side['intervals']:assert not band[lo:hi].any()


@pytest.mark.parametrize('dr',[250.,500.,1000.])
def test_physical_resolutions(dr):
    s=finite_scene(dr=dr);e=evaluate(s,config())
    assert e.arrays['RDR_SOURCE_MASK'][4,tail(s)].all()


@pytest.mark.parametrize('level',[28.,36.,48.])
def test_no_fixed_signal_level(level):
    s=finite_scene(level=level);e=evaluate(s,config())
    assert e.arrays['RDR_SOURCE_MASK'][4,tail(s)].all()


@pytest.mark.parametrize('phase',[12.,82.,359.])
def test_no_fixed_phase(phase):
    a=evaluate(finite_scene(),config()).arrays
    b=evaluate(finite_scene(phase=phase),config()).arrays
    np.testing.assert_equal(a['RDR_SOURCE_MASK'],b['RDR_SOURCE_MASK'])


@pytest.mark.parametrize('shift',[70.,180.,253.])
def test_rotation_including_north(shift):
    a=evaluate(finite_scene(),config()).arrays;b=evaluate(finite_scene(shift=shift),config()).arrays
    np.testing.assert_equal(a['RDR_SOURCE_MASK'],b['RDR_SOURCE_MASK'])


@pytest.mark.parametrize('kind',['independent_weather','unknown_protection'])
def test_external_and_unattributed_weather_remain_barriers(kind):
    s=finite_scene();m=np.zeros(s.shape,bool);m[4,tail(s)]=True
    e=evaluate(s,config(),**{kind:m});o,_=apply(baseline(s),e.arrays,config(),low_quality_flag=1024)
    assert e.arrays['RDR_FULL_MATCH_MASK'][4,tail(s)].all()
    assert not o['RDR_QUARANTINE_MASK'][4,tail(s)].any()
    assert o[CR][4,tail(s)].all()


def test_conservative_local_mode_remains_conservative():
    s=finite_scene();loc=np.zeros(s.shape,bool);loc[4]=True
    c=C(mode='quarantine',segment_reference=S(mode='experiment'))
    e=evaluate(s,c,local_coherence=loc)
    assert not e.arrays['RDR_SOURCE_MASK'][4,tail(s)].any()


@pytest.mark.parametrize('key',['PHIDP','ZDR','RHOHV'])
def test_partial_never_becomes_qpe_quarantine(key):
    s=finite_scene();s=altered(s,key,np.s_[4,tail(s)],np.nan)
    c=config(seg=S(mode='experiment',partial_policy='cr_withhold'));e=evaluate(s,c)
    b=baseline(s);o,_=apply(b,e.arrays,c,low_quality_flag=1024)
    assert e.arrays['RDR_PARTIAL_MATCH_MASK'][4,tail(s)].all()
    assert not o[CR][4,tail(s)].any()
    assert o['QPE_ELIGIBLE_MASK'][4,tail(s)].all()
    assert not o['RDR_QUARANTINE_MASK'][4,tail(s)].any()


@pytest.mark.parametrize('key,value',[('PHIDP',120.),('ZDR',3.),('RHOHV',.5),('ZDR',8.)])
def test_actual_polar_counterevidence_not_ignored(key,value):
    s=finite_scene();s=altered(s,key,np.s_[4,tail(s)],value);e=evaluate(s,config())
    assert not e.arrays['RDR_FULL_MATCH_MASK'][4,tail(s)].any()
    assert not e.arrays['RDR_PARTIAL_MATCH_MASK'][4,tail(s)].any()


@pytest.mark.parametrize('value',[np.nan,43.])
def test_target_flanks_must_be_measured_and_weaker(value):
    s=finite_scene();s=altered(s,'SNR',np.s_[3,tail(s)],value);e=evaluate(s,config())
    assert e.arrays['RDR_FULL_MATCH_MASK'][4,tail(s)].all()
    assert e.arrays['RDR_TARGET_SIDE_CONFLICT_MASK'][4,tail(s)].all()
    assert not e.arrays['RDR_SOURCE_MASK'][4,tail(s)].any()


def test_extrapolation_is_bounded_to_actual_paired_samples():
    s=finite_scene();c=config(seg=S(mode='experiment',maximum_reference_distance_m=30000.))
    e=evaluate(s,c)
    assert not e.arrays['RDR_SOURCE_MASK'][4,tail(s)].any()
    assert (e.arrays['RDR_SEGMENT_REFERENCE_DISTANCE_M'][4,tail(s)]>30000).all()


def test_noise_or_broad_rain_is_not_a_source():
    s=finite_scene();s=altered(s,'SNR',np.s_[:,:],30.)
    assert not evaluate(s,config()).arrays['RDR_SOURCE_MASK'].any()


def test_tiny_training_island_cannot_fake_large_reference_span():
    s=finite_scene();f={k:v.copy() for k,v in s.fields.items()}
    ref=(s.ranges<100000)&(s.ranges>15000)
    keep=(s.ranges<17000)|((s.ranges>60000)&(s.ranges<60500))
    f['SNR'][4,ref&~keep]=-5.
    q=replace(s,fields=f,available={k:np.isfinite(v) for k,v in f.items()})
    assert not evaluate(q,config()).arrays['RDR_SOURCE_MASK'][4,tail(s)].any()


def test_two_compatible_models_abstain_not_choose_nearest_target():
    s=finite_scene();c=config();p=domains(s,c);mods,_=fit_segment_models(s,4,5,c)
    assert mods
    e=abstained(s,c,'test');out={k:v.copy() for k,v in e.arrays.items()}
    record=[];project_segment_models(s,4,5,c,p,s.observed,[mods[0],copy.deepcopy(mods[0])],record,out)
    j=(s.ranges//c.block_m).astype(int)==5;j&=s.observed[4]
    assert (out['RDR_SEGMENT_MATCH_COUNT'][4,j]==2).all()
    assert not out['RDR_FULL_MATCH_MASK'][4,j].any()
    assert not out['RDR_PARTIAL_MATCH_MASK'][4,j].any()


def test_state_trial_limit_is_explicit():
    with pytest.raises(ResourceLimit):evaluate(finite_scene(),config(seg=S(mode='experiment',maximum_state_trials=1)))


def test_geometry_break_does_not_find_flanks_across_gap():
    s=finite_scene();g=s.gap_after.copy();g[4]=True
    assert not evaluate(replace(s,gap_after=g),config()).arrays['RDR_SOURCE_MASK'][4].any()


def test_segment_audit_keeps_parent_original_fields_exact():
    s=finite_scene();b=baseline(s)
    parent=C(mode='quarantine',local_policy='source_joint_review')
    old,_=apply(b,evaluate(s,parent).arrays,parent,low_quality_flag=1024)
    c=config(seg=S(mode='audit'));e=evaluate(s,c)
    assert e.arrays['RDR_SOURCE_MASK'][4,tail(s)].all()
    new,_=apply(b,e.arrays,c,low_quality_flag=1024)
    for key in b:np.testing.assert_equal(old[key],new[key])


def test_serialization_checks_segment_fields_and_exact_parent():
    s=finite_scene();c=config();b=baseline(s);e=evaluate(s,c)
    o,_=apply(b,e.arrays,c,low_quality_flag=1024);calls=[]
    def parent(g,attrs):
        for k in b:np.testing.assert_equal(g[k],b[k])
        calls.append(1)
    validate_serialized(o,attributes(c,1024),parent);assert calls==[1]
    bad={k:v.copy() for k,v in o.items()};bad['RDR_SEGMENT_REFERENCE_DISTANCE_M'][4,tail(s)]=100000
    with pytest.raises(ValueError):validate_serialized(bad,attributes(c,1024),lambda *_:None)
    bad={k:v.copy() for k,v in o.items()};bad['RDR_SEGMENT_MATCH_COUNT'][4,tail(s)]=2
    with pytest.raises(ValueError):validate_serialized(bad,attributes(c,1024),lambda *_:None)


def test_config_omission_preserves_frozen_serialization():
    c=C();assert 'segment_reference' not in c.model_dump(mode='json')
    assert 'segment_reference' not in json.loads(c.model_dump_json())
    assert C(segment_reference=None).digest==c.digest
    with pytest.raises(ValueError):C(segment_reference=S(reference_minimum_range_m=60000))


def test_target_power_enhancement_is_not_recruited():
    s=finite_scene();f={k:v.copy() for k,v in s.fields.items()}
    for key in ['DBZH','SNR']:f[key][4,tail(s)]+=10.
    t=replace(s,fields=f,available={k:np.isfinite(v) for k,v in f.items()})
    assert not evaluate(t,config()).arrays['RDR_SOURCE_MASK'][4,tail(s)].any()


def test_coarse_weak_or_missing_geometry_not_assumed_weather_free():
    s=finite_scene();a=dict(s.available);a['SNR']=a['SNR'].copy();a['SNR'][3]=False
    t=replace(s,available=a)
    # Farther original observed shoulder can be used, but its absence does not
    # contribute false contrast; remove all left-side SNR to force abstention.
    a['SNR'][:4]=False
    t=replace(s,available=a);e=evaluate(t,config())
    assert not e.arrays['RDR_SOURCE_MASK'][4].any()


def test_enabled_extension_is_a_fallback_not_a_replacement():
    from test_receiver import scene
    s=scene();a=evaluate(s,C()).arrays;b=evaluate(s,C(segment_reference=S())).arrays
    for key,value in a.items():np.testing.assert_equal(value,b[key])
    assert not b['RDR_SEGMENT_REFERENCE_MASK'].any()


def test_generator_preserves_global_modes_and_all_unrelated_settings(tmp_path):
    from pathlib import Path
    import sys,yaml
    sys.path.insert(0,str(Path(__file__).resolve().parents[3]/'scripts'))
    from make_receiver_segment_profiles import generate
    parent={'operational_eligible':False,'profile_version':'real-p3',
        'echo':{'no_rain_below_dbz':-10},'nonprecip_review':{'near_enabled':True},
        'volume_review':{'phase':3,'mode':'experiment_quarantine',
                         'receiver_domain':C(mode='quarantine',local_policy='source_joint_review').model_dump(mode='json')}}
    p=tmp_path/'parent.yaml';p.write_text(yaml.safe_dump(parent));original=p.read_bytes()
    records=generate(p,tmp_path/'out');assert len(records)==3 and p.read_bytes()==original
    for rec in records:
        child=yaml.safe_load((tmp_path/'out'/rec['file']).read_text())
        assert child['nonprecip_review']==parent['nonprecip_review']
        rc=child['volume_review']['receiver_domain'];rc.pop('segment_reference')
        assert rc==parent['volume_review']['receiver_domain']
    with pytest.raises(FileExistsError):generate(p,tmp_path/'out')


@pytest.mark.parametrize('permuted',[False,True])
def test_segment_real_adapter_and_serialized_before_state_contract(permuted):
    from test_hooks import Q,Result,Native,group
    from types import SimpleNamespace as NS
    from volume_review.receiver_domain.integration import review_result
    from volume_review.receipts import snapshot_group,npz_bytes,load_npz
    s=finite_scene();n=Native(s,permuted);b=baseline(s)
    b.update({k+'_RAW':v.copy() for k,v in s.fields.items()})
    b={k:n.restore(v) for k,v in b.items()}
    names=('DBZH_RAW','DBZH_QC','QC_FLAGS','QUALITY_INDEX','VALID_MASK','LOW_QUALITY_MASK')
    q=Q(s.name,{k:v for k,v in b.items() if k not in names},*[b[k] for k in names],{})
    c=config();profile=NS(volume_review=VolumeReviewConfig(mode='experiment_quarantine',receiver_domain=c),
        context=NS(strong_support=.7),echo=NS(no_rain_below_dbz=-10),
        quality_index=NS(quantitative_minimum=.5),flag_masks={'LOW_QUALITY':np.uint32(1024)})
    initial=Result(profile,(q,),{'sweeps':{s.name:{}}})
    result=review_result(initial,[n]);out=group(result.sweeps[0]);calls=[]
    def parent(view,attrs):
        for k,v in b.items():np.testing.assert_equal(view[k],v)
        calls.append(1)
    validate_serialized(out,attributes(c,1024),parent);assert calls==[1]
    assert out['RDR_QUARANTINE_MASK'][n.original_indices[4],tail(s)].all()
    records=json.loads(result.volume_review_artifacts['qc/volume_review/receiver_domain.json'])['models']
    for model in records:assert model['ray']==n.original_indices[model['native_sorted_ray']]
    snap=load_npz(npz_bytes(snapshot_group(out)))
    assert 'RDR_SEGMENT_REFERENCE_MASK' in snap
    assert not any(k.startswith('RDR_BEFORE_') for k in snap)


def test_segment_budget_whole_volume_falls_back_with_no_partial_actions():
    from test_hooks import fixture,group
    from volume_review.integration import review_result
    initial,nn=fixture('quarantine')
    # On this legacy stable scene normal RDR would act. Force the existing
    # overall resource guard as well: the optional segment arrays must be valid.
    c=C(mode='quarantine',segment_reference=S(mode='experiment'),maximum_volume_gates=1)
    initial.profile.volume_review=initial.profile.volume_review.model_copy(update={'receiver_domain':None})
    parent=review_result(initial,nn)
    initial.profile.volume_review=initial.profile.volume_review.model_copy(update={'receiver_domain':c})
    out=review_result(initial,nn)
    for k,v in group(parent.sweeps[0]).items():np.testing.assert_equal(group(out.sweeps[0])[k],v)
    assert out.summary['receiver_domain']['status']=='RESOURCE_ABSTAINED'
