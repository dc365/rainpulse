"""Shared-source regression: synthetic measurements are not weather truth."""
from dataclasses import replace
from types import SimpleNamespace as NS
import copy
import json
import numpy as np
import pytest
from volume_review.data import Sweep, ResourceLimit
from volume_review.receiver_domain.config import ReceiverDomainConfig as C, SourceFamilyConfig as F
from volume_review.receiver_domain.core import evaluate, fit_fold, domains
from volume_review.receiver_domain.source_family import fit_family, project_family, Reason
from volume_review.receiver_domain.disposition import apply, CR
from volume_review.receiver_domain.integration import review_result, attributes, restore_reference_indices
from volume_review.receiver_domain.validation import validate_serialized
from volume_review.receiver_domain.family_validation import validate_family_records
from test_receiver import baseline


def scene():
    r = np.arange(50125.,460000.,500.)
    az = np.arange(50.,65.); nr = len(az); sh = (nr,len(r))
    f = {k: np.full(sh,np.nan,'float32') for k in ('DBZH','SNR','PHIDP','ZDR','RHOHV')}
    f['SNR'][:] = -7.5
    for row in (8,9,10):
        f['SNR'][row] = 55.5
        f['DBZH'][row] = 55.5+20*np.log10(r/1000)+.01*r/1000-44.6
        f['PHIDP'][row] = 65.; f['ZDR'][row] = .2; f['RHOHV'][row] = .99
    target = ((r>=390000)&(r<403000))|(r>=429000)
    f['SNR'][7,target] = 48.5
    f['DBZH'][7,target] = 48.5+20*np.log10(r[target]/1000)+.01*r[target]/1000-44.3
    f['PHIDP'][7,target] = 65.; f['ZDR'][7,target] = .2; f['RHOHV'][7,target] = .99
    near = r<200000
    f['SNR'][7,near] = np.linspace(20,54,near.sum()); f['DBZH'][7,near] = 25.
    f['PHIDP'][7,near] = 20.; f['ZDR'][7,near] = 5.; f['RHOHV'][7,near] = .6
    gaps = np.zeros(nr,bool); gaps[-1] = True
    s = Sweep('sweep_000',az,np.full(nr,.5),r,f,{k:np.isfinite(v) for k,v in f.items()},np.ones(nr,bool),gaps,np.arange(nr)*2.)
    c = C(mode='quarantine',local_policy='source_joint_review',source_family=F(mode='experiment'))
    return s,c,target


def modify(s, fn):
    f = {k:v.copy() for k,v in s.fields.items()}; a = {k:v.copy() for k,v in s.available.items()}
    fn(f,a)
    return replace(s,fields=f,available=a)


def fold(s,c,row=7,block=22):
    m,why = fit_family(s,row,block,c)
    assert m is not None,why
    j = np.flatnonzero(s.observed[row] & ((s.ranges//c.block_m).astype(int)==block))
    return m,j,project_family(s,row,j,m,c,domains(s,c))


def test_shared_reference_covers_edge_only_and_raw_immutable():
    s,c,target = scene(); before = s.digest
    assert fit_fold(s,7,22,c)[0] is None
    m,j,v = fold(s,c)
    assert v['full'].all() and np.all(target[j]) and s.digest == before
    assert 7 not in [x['ray'] for x in m['donors']]
    assert all(7 not in [x['ray'] for x in d['shoulders']] for d in m['donors'])
    validate_family_records([dict(m,id=1)],s,c)
    assert abs(m['states'][0]['ray_bias_db']-.3)<.01


@pytest.mark.parametrize('block',[19,20,21,22])
def test_target_guard_all_rays_do_not_change_model(block):
    s,c,_ = scene();m,_,_ = fold(s,c,block=block)
    guard = abs((s.ranges//c.block_m).astype(int)-block)<=c.guard_blocks
    def mutate(f,a):
        for k in f:
            f[k][:,guard] = 0.25 if k=='RHOHV' else 120.
    q = modify(s,mutate);new,why = fit_family(q,7,block,c)
    assert new == m and why == 'FITTED'


@pytest.mark.parametrize('field,value',[('PHIDP',100.),('ZDR',8.),('RHOHV',.6),('SNR',35.),('DBZH',25.)])
def test_current_target_contradictions_cannot_be_overridden(field,value):
    s,c,_ = scene(); j = ((s.ranges//c.block_m).astype(int)==22)
    q = modify(s,lambda f,a:f[field].__setitem__((7,j),value))
    _,_,v = fold(q,c)
    assert not v['full'].any() and not v['partial'].any()


def test_missing_polar_is_partial_never_quarantine():
    s,c,_ = scene();t=(s.ranges//c.block_m).astype(int)==22
    q=modify(s,lambda f,a:a['ZDR'].__setitem__((7,t),False))
    e=evaluate(q,c);b=baseline(q);o,_=apply(b,e.arrays,c,low_quality_flag=1024)
    assert e.arrays['RDR_PARTIAL_MATCH_MASK'][7,t].any()
    assert not o['RDR_QUARANTINE_MASK'][7,t].any()
    np.testing.assert_equal(o['QPE_ELIGIBLE_MASK'][7,t],b['QPE_ELIGIBLE_MASK'][7,t])
    c2=c.model_copy(update={'source_family':c.source_family.model_copy(update={'partial_policy':'cr_withhold'})})
    e2=evaluate(q,c2);o2,_=apply(b,e2.arrays,c2,low_quality_flag=1024)
    assert (o2[CR][7,t]<b[CR][7,t]).any()
    np.testing.assert_equal(o2['QPE_ELIGIBLE_MASK'][7,t],b['QPE_ELIGIBLE_MASK'][7,t])


@pytest.mark.parametrize('kind',['donors','sides_missing','sides_bright'])
def test_same_range_donors_and_outer_sides_are_required(kind):
    s,c,_ = scene();m,_,_=fold(s,c);t=(s.ranges//c.block_m).astype(int)==22
    def mut(f,a):
        if kind=='donors':
            for row in (8,9,10):a['DBZH'][row,t]=False
        else:
            for d in m['shoulders']:
                if kind=='sides_missing':a['SNR'][d['ray'],t]=False
                else:f['SNR'][d['ray'],t]=48.5
    q=modify(s,mut);_,_,v=fold(q,c)
    assert not v['full'].any() and not v['partial'].any()


@pytest.mark.parametrize('shift',[29.,173.,300.])
def test_phase_rotation(shift):
    s,c,_=scene();_,j,v=fold(s,c)
    q=modify(s,lambda f,a:f.__setitem__('PHIDP',(f['PHIDP']+shift)%360))
    _,jj,vv=fold(q,c);np.testing.assert_equal(v['full'],vv['full']);np.testing.assert_equal(j,jj)


@pytest.mark.parametrize('shift',[45.,310.])
def test_azimuth_rotation(shift):
    s,c,_=scene();_,_,v=fold(s,c)
    q=replace(s,azimuth=(s.azimuth+shift)%360)
    _,_,vv=fold(q,c);np.testing.assert_equal(v['full'],vv['full'])


def test_rotated_acquisition_order():
    s,c,_=scene();_,_,v=fold(s,c);o=np.roll(np.arange(s.shape[0]),5);row=int(np.flatnonzero(o==7)[0])
    q=replace(s,azimuth=s.azimuth[o],elevation=s.elevation[o],fields={k:v[o] for k,v in s.fields.items()},
              available={k:v[o] for k,v in s.available.items()},good=s.good[o],gap_after=s.gap_after[o],ray_time_s=s.ray_time_s[o])
    _,_,vv=fold(q,c,row=row);np.testing.assert_equal(v['full'],vv['full'])


@pytest.mark.parametrize('case',['gap','only_one','no_times','stale_time','elevation','variable_donors'])
def test_bad_donor_reference_abstains(case):
    s,c,_=scene()
    if case=='gap':
        gaps=s.gap_after.copy();gaps[7]=True;s=replace(s,gap_after=gaps)
    elif case=='only_one':
        s=modify(s,lambda f,a:a['SNR'].__setitem__(([9,10],slice(None)),False))
    elif case=='no_times':s=replace(s,ray_time_s=None)
    elif case=='stale_time':
        times=s.ray_time_s.copy();times[8:11]+=600;s=replace(s,ray_time_s=times)
    elif case=='elevation':
        el=s.elevation.copy();el[8:11]+=1.;s=replace(s,elevation=el)
    else:s=modify(s,lambda f,a:f['SNR'].__setitem__(([8,9,10],slice(None)),np.linspace(50,10,len(s.ranges))))
    assert fit_family(s,7,22,c)[0] is None


def test_parent_model_explicit_mismatch_not_reinterpreted():
    s,c,_=scene();old=evaluate(s,c.model_copy(update={'source_family':None}));new=evaluate(s,c)
    has=old.arrays['RDR_MODEL_AVAILABLE_MASK']==1
    assert has.any()
    for k,v in old.arrays.items():np.testing.assert_equal(v[has],new.arrays[k][has])
    assert not new.arrays['RDR_FAMILY_REFERENCE_MASK'][has].any()


@pytest.mark.parametrize('protection',['independent_weather','unknown_protection','local_coherence'])
def test_weather_vetoes(protection):
    s,c,t=scene();mask=np.zeros(s.shape,bool);mask[7]=True
    if protection=='local_coherence':c=c.model_copy(update={'local_policy':'retain_conflict'})
    e=evaluate(s,c,**{protection:mask})
    assert not e.arrays['RDR_SOURCE_MASK'][7].any()
    assert e.arrays['RDR_FAMILY_UNRESOLVED_MASK'][7,t].any()


@pytest.mark.parametrize('mode',['audit','cr_only','quarantine'])
@pytest.mark.parametrize('family_mode',['audit','experiment'])
def test_modes_parent_view_and_no_revive(mode,family_mode):
    s,c,_=scene();c=c.model_copy(update={'mode':mode,'source_family':c.source_family.model_copy(update={'mode':family_mode})})
    b=baseline(s);e=evaluate(s,c);o,d=apply(b,e.arrays,c,low_quality_flag=1024)
    oldc=c.model_copy(update={'source_family':None});old,_=apply(b,evaluate(s,oldc).arrays,oldc,low_quality_flag=1024)
    calls=[]
    def parent(g,attrs):
        for k,v in b.items():np.testing.assert_equal(g[k],v)
        calls.append(1)
    validate_serialized(o,attributes(c,1024),parent)
    assert calls==[1]
    if family_mode=='audit' or mode=='audit':
        for k,v in b.items():np.testing.assert_equal(o[k],old[k])
    else:assert o['RDR_FAMILY_CR_WITHHELD_MASK'].any()
    if mode!='quarantine' or family_mode=='audit':assert not o['RDR_FAMILY_QUARANTINE_MASK'].any()
    for k in ('DBZH_RAW','DBZH_QC','VALID_MASK'):np.testing.assert_equal(o[k],b[k])
    assert not ((o[CR]==1)&(b[CR]!=1)).any()


@pytest.mark.parametrize('field',['RDR_FAMILY_SAME_RANGE_DONORS','RDR_FAMILY_STATE_ID','RDR_FAMILY_REASON','RDR_FAMILY_CURRENT_SUPPORT_MASK'])
def test_corrupt_evidence_rejected(field):
    s,c,_=scene();e=evaluate(s,c);e.arrays[field][7,:]=0
    with pytest.raises(ValueError):apply(baseline(s),e.arrays,c,low_quality_flag=1024)


def test_all_nested_reference_indices_restored():
    s,c,_=scene();m,_,_=fold(s,c);order=np.roll(np.arange(s.shape[0]),4)
    rec=restore_reference_indices(m,order)
    assert rec['ray']==order[m['ray']]
    for before,after in zip(m['donors'],rec['donors']):
        assert after['ray']==order[before['ray']]
        for a,b in zip(before['shoulders'],after['shoulders']):assert b['ray']==order[a['ray']]
    assert m['reference_sha256']==rec['reference_sha256']
    validate_family_records([dict(m,id=1)],s,c)


@pytest.mark.parametrize('limit',['maximum_family_folds','maximum_donor_trials','maximum_ordered_blocks'])
def test_family_resources_raise_no_partial_result(limit):
    s,c,_=scene();c=c.model_copy(update={'source_family':c.source_family.model_copy(update={limit:1})})
    with pytest.raises(ResourceLimit):evaluate(s,c)


def test_ordered_reference_runs_preserve_gaps_and_guards():
    s,c,_=scene();m,_,_=fold(s,c)
    for state in m['states']:
        assert state['ordered_runs']
        for run in state['ordered_runs']:
            assert all(y==x+1 for x,y in zip(run['blocks'],run['blocks'][1:]))
            for lo,hi in run['intervals']:
                r=s.ranges[lo:hi]
                assert np.all(abs((r//c.block_m).astype(int)-22)>c.guard_blocks)
        for check in state['cross_predictions']:
            assert not set(check['train_blocks'])&set(check['validation_blocks'])


def test_ambiguous_state_is_not_selected_by_target_residual():
    s,c,_=scene();m,j,v=fold(s,c);m=copy.deepcopy(m)
    other=copy.deepcopy(m['states'][0]);other['state_id']=2;m['states'].append(other)
    ambiguous=project_family(s,7,j,m,c,domains(s,c))
    assert (ambiguous['votes']==2).all()
    assert not ambiguous['full'].any() and not ambiguous['partial'].any()
    assert not ambiguous['state_id'].any()


def test_donor_independent_weather_cannot_support_a_target_action():
    s,c,_=scene();wx=np.zeros(s.shape,bool);wx[8:11]=True
    e=evaluate(s,c,independent_weather=wx)
    assert e.arrays['RDR_FAMILY_REFERENCE_MASK'][7].any()
    assert not e.arrays['RDR_SOURCE_MASK'][7].any()
    assert not e.arrays['RDR_FAMILY_SAME_RANGE_DONORS'][7].any()


def family_fixture(two=False,permuted=False):
    from test_hooks import Native,Q,Result,group
    s,c,_=scene();c=c.model_copy(update={'source_family':c.source_family.model_copy(update={'full_policy':'quarantine'})})
    sweeps=[s] if not two else [s,replace(s,name='sweep_002')]
    nn=[];qs=[]
    for sample in sweeps:
        n=Native(sample,permuted);b=baseline(sample)
        b={k:n.restore(v) for k,v in b.items()}
        keys=('DBZH_RAW','DBZH_QC','QC_FLAGS','QUALITY_INDEX','VALID_MASK','LOW_QUALITY_MASK')
        q=Q(n.name,{k:v for k,v in b.items() if k not in keys},*[b[k] for k in keys],{})
        nn.append(n);qs.append(q)
    from volume_review.config import VolumeReviewConfig
    profile=NS(volume_review=VolumeReviewConfig(mode='experiment_quarantine',unknown_cr_policy='retain_with_risk',receiver_domain=c),
               context=NS(strong_support=.7),echo=NS(no_rain_below_dbz=-10.),quality_index=NS(quantitative_minimum=.5),flag_masks={'LOW_QUALITY':1024})
    return Result(profile,tuple(qs),{'sweeps':{s.name:{} for s in sweeps}}),nn


@pytest.mark.parametrize('permuted',[False,True])
def test_adapter_serialization_and_nested_family_receipts(permuted):
    from test_hooks import group
    r,nn=family_fixture(permuted=permuted);before=group(r.sweeps[0]);out=review_result(r,nn)
    a=group(out.sweeps[0]);calls=[]
    def parent(g,attrs):
        assert set(g)==set(before)
        for k,v in before.items():np.testing.assert_equal(g[k],v)
        calls.append(1)
    validate_serialized(a,attributes(r.profile.volume_review.receiver_domain,1024),parent)
    assert calls==[1] and a['RDR_FAMILY_QUARANTINE_MASK'].any()
    records=json.loads(out.volume_review_artifacts['qc/volume_review/receiver_domain.json'])['models']
    family=[m for m in records if m.get('reference_route')=='shared_coherent_source_family'];assert family
    for m in family:
        for donor in m['donors']:
            assert donor['ray']==nn[0].original_indices[donor['native_sorted_ray']]
            for side in donor['shoulders']:assert side['ray']==nn[0].original_indices[side['native_sorted_ray']]


def test_second_sweep_family_failure_discards_all_new_actions_but_keeps_parent(monkeypatch):
    from test_hooks import group
    import volume_review.receiver_domain.integration as adapter
    from volume_review.receiver_domain.source_family import FamilyResourceLimit
    r,nn=family_fixture(two=True);cfg=r.profile.volume_review.receiver_domain
    original=adapter.evaluate
    def injected(s,cfg,**kwargs):
        if cfg.source_family is not None and s.name=='sweep_002':raise FamilyResourceLimit('injected second-sweep family limit')
        return original(s,cfg,**kwargs)
    monkeypatch.setattr(adapter,'evaluate',injected)
    out=review_result(r,nn)
    r.profile.volume_review=r.profile.volume_review.model_copy(update={'receiver_domain':cfg.model_copy(update={'source_family':None})})
    parent=review_result(r,nn)
    for a,b in zip(out.sweeps,parent.sweeps):
        actual,expected=group(a),group(b)
        for key,value in expected.items():np.testing.assert_equal(actual[key],value)
        assert not actual['RDR_FAMILY_QUARANTINE_MASK'].any()
        assert actual['RDR_QUARANTINE_MASK'].any()  # parent RDR remains effective
    assert out.summary['receiver_domain']['source_family_status']=='RESOURCE_ABSTAINED_PARENT_RETAINED'


def test_family_cr_winners_and_leakage_barrier():
    from test_hooks import Root,group
    from volume_review.integration import root_attributes
    from volume_review.composite import build_composite,trace_pixel
    r,nn=family_fixture();out=review_result(r,nn)
    root=Root({**root_attributes(r.profile),'radar_id':'SYNTHETIC','scan_id':'scan','asset_id':'asset',
               'site_longitude_deg':0.,'site_latitude_deg':0.,'qc_parameters_sha256':'a'*64})
    root['sweep_number']=np.array([0],dtype='int32')
    root['sweep_000']={**group(out.sweeps[0]),'azimuth':nn[0].azimuth,'range':nn[0].ranges,'elevation':nn[0].elevation}
    product=build_composite([root],0,maximum_size=128)
    assert np.isfinite(product.arrays['CR_RECEIVER_FAMILY_WITHHELD']).any()
    count=0
    for row,col in np.argwhere(np.isfinite(product.arrays['CR_TRUSTED'])):
        assert trace_pixel(product,[root],int(row),int(col))['status']=='RECONSTRUCTED';count+=1
    assert count>0
    a=root['sweep_000'];a[CR][a['RDR_FAMILY_CR_WITHHELD_MASK']==1]=1
    with pytest.raises(ValueError,match='leaked'):build_composite([root],0,maximum_size=128)


def test_family_profile_generation_preserves_old_parent_modes(tmp_path):
    import importlib.util,yaml
    from pathlib import Path
    script=Path(__file__).resolve().parents[3]/'scripts/make_receiver_family_profiles.py'
    spec=importlib.util.spec_from_file_location('family_profiles',script);mod=importlib.util.module_from_spec(spec);spec.loader.exec_module(mod)
    r,_=family_fixture();vc=r.profile.volume_review
    vc=vc.model_copy(update={'receiver_domain':vc.receiver_domain.model_copy(update={'source_family':None})})
    parent={'operational_eligible':False,'profile_version':'test-parent','volume_review':vc.model_dump(mode='json')}
    path=tmp_path/'parent.yaml';path.write_text(yaml.safe_dump(parent));raw=path.read_bytes()
    records=mod.generate(path,tmp_path/'profiles');assert len(records)==4 and path.read_bytes()==raw
    for rec in records:
        child=yaml.safe_load((tmp_path/'profiles'/rec['file']).read_bytes())
        family=child['volume_review']['receiver_domain'].pop('source_family')
        assert child['volume_review']==parent['volume_review']
        assert child['operational_eligible'] is False
    with pytest.raises(ValueError):mod.generate(path,tmp_path/'profiles')


def test_serialized_qc_audit_helper_checks_net_delta_and_raw(tmp_path):
    import importlib.util
    from pathlib import Path
    from test_hooks import Root,group
    script=Path(__file__).resolve().parents[3]/'scripts/audit_receiver_family_outputs.py'
    spec=importlib.util.spec_from_file_location('family_output_audit',script);mod=importlib.util.module_from_spec(spec);spec.loader.exec_module(mod)
    r,nn=family_fixture();c=r.profile.volume_review.receiver_domain
    result=review_result(r,nn)
    r.profile.volume_review=r.profile.volume_review.model_copy(update={'receiver_domain':c.model_copy(update={'source_family':None})})
    parent=review_result(r,nn)
    def root(res,cfg):
        rt=Root({**attributes(cfg,1024),'radar_id':'SYNTHETIC','scan_id':'scan'})
        rt['sweep_number']=np.array([0]);rt['sweep_000']={**group(res.sweeps[0]),'azimuth':nn[0].azimuth,
                                                      'elevation':nn[0].elevation,'range':nn[0].ranges}
        return rt
    old,new=root(parent,r.profile.volume_review.receiver_domain),root(result,c)
    report=mod.compare(old,new);assert report['total_new_cr_excluded']>0 and report['total_new_qpe_excluded']>0
    new['sweep_000']['DBZH_RAW']=new['sweep_000']['DBZH_RAW'].copy();new['sweep_000']['DBZH_RAW'][7,-1]+=1
    with pytest.raises(ValueError,match='raw'):mod.compare(old,new)
