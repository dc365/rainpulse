from dataclasses import replace
from pathlib import Path
from types import SimpleNamespace as NS
import copy,json,numpy as np,pytest
from fusion_helpers import scene,baseline,cfg,Native,fixture,group
from volume_review.data import Sweep,ResourceLimit
from volume_review.clutter_fusion.near_revision_config import NearRevisionConfig as N,StrongNearConfig
from volume_review.clutter_fusion import partial_moments as pm,causal_temporal as ct,terrain_admission as ta
from volume_review.clutter_fusion.engine import evaluate_volume
from volume_review.clutter_fusion.near_runtime import RuntimeContext
from volume_review.clutter_fusion.disposition import apply,CR
from volume_review.clutter_fusion.validation import validate_serialized
from volume_review.clutter_fusion.integration import attributes
from review_extension.config import NonPrecipConfig
from review_extension.near_reliability import NearReliabilityConfig
from review_extension.near_clutter import candidates


def config(mode='cr_withhold',**kw):return cfg(mode='quarantine',near_revision=N(mode=mode,**kw))
def strong_config(mode='quarantine'):
    return config(strong_near=StrongNearConfig(mode=mode))
def strong_object_config(mode='quarantine'):
    return config(strong_near=StrongNearConfig(mode=mode,object_propagation=True))
def change(s,k,value=None,missing=False):
    f={k:v.copy() for k,v in s.fields.items()};a={k:v.copy() for k,v in s.available.items()}
    if missing:
        a[k][:]=False;f[k][:]=np.nan
    else:f[k][:]=value
    return replace(s,fields=f,available=a)
def partial(**kw):return scene(missing=('ZDR',),**kw)
def strong_scene(**kw):
    z=kw.pop('z',35.);rho=kw.pop('rho',.70);snr=kw.pop('snr',20.)
    s=partial(z=z,rho=rho,snr=snr,**kw)
    if z==35.:
        pattern=np.array([35.,42.,25.,18.],dtype='float32')
        values=np.broadcast_to(np.tile(pattern,s.shape[1]//4+1)[:s.shape[1]],s.shape).copy()
    else:
        values=np.full(s.shape,z,dtype='float32')
    return replace(s,fields={**s.fields,'DBZH':values})
def one(s,c,**kw):return evaluate_volume([s],c,**kw)[0]
def check(s,c,**kw):
    e=one(s,c,**kw);out,d=apply(baseline(s),e.arrays,c,low_quality_flag=1024);return e,out,d


def test_no_optin_preserves_old_config_serialization():
    c=cfg();assert 'near_revision' not in c.model_dump()
    n=NonPrecipConfig();assert 'near_reliability' not in n.model_dump()


def test_enabled_near_retains_old_complete_measurements():
    s=scene(kind='ground',z=15.,zdr=5.);n=Native(s)
    a=NonPrecipConfig(near_enabled=True);b=a.model_copy(update={'near_reliability':NearReliabilityConfig()})
    old=candidates(n,a,np.ones(s.shape,bool))[0];new=candidates(n,b,np.ones(s.shape,bool))[0]
    assert new.any() and np.array_equal(old,new)

@pytest.mark.parametrize('zdr',[-7.8125,7.8125,-8.,8.])
def test_new_near_cannot_use_unverified_tail(zdr):
    s=scene(kind='ground',zdr=zdr);n=Native(s)
    assert candidates(n,NonPrecipConfig(near_enabled=True),np.ones(s.shape,bool))[0].any()
    c=NonPrecipConfig(near_enabled=True,near_reliability=NearReliabilityConfig())
    assert not candidates(n,c,np.ones(s.shape,bool))[0].any()


def test_partial_works_without_zdr_and_without_rough_z():
    s=partial(z=12.);e,out,d=check(s,config())
    assert e.arrays['CF_NR_STRICT_MASK'].any();assert d['near_revision_cr_loss_gates']>0
    assert d['near_revision_new_qpe_loss_gates']==0
    assert np.array_equal(out['QPE_ELIGIBLE_MASK'],baseline(s)['QPE_ELIGIBLE_MASK'])
    assert np.array_equal(out['DBZH_RAW'],s.fields['DBZH'])


def test_strong_near_audit_records_but_does_not_act():
    s=strong_scene();e,out,d=check(s,strong_config('audit'))
    assert e.arrays['CF_NR_STRONG_CANDIDATE_MASK'].any()
    assert not e.arrays['CF_NR_STRONG_ACTION_MASK'].any()
    _,without_strong,_=check(s,config())
    assert d['strong_near_quarantine_gates']==0 and d['near_revision_new_qpe_loss_gates']==0
    assert np.array_equal(out['QC_ACTION'],without_strong['QC_ACTION'])
    assert np.array_equal(out['QPE_ELIGIBLE_MASK'],without_strong['QPE_ELIGIBLE_MASK'])


def test_strong_near_quarantine_removes_only_supported_gates():
    s=strong_scene();e,out,d=check(s,strong_config('quarantine'))
    selected=e.arrays['CF_NR_STRONG_CANDIDATE_MASK']==1
    assert selected.any() and np.array_equal(e.arrays['CF_NR_STRONG_ACTION_MASK']==1,selected)
    assert d['strong_near_quarantine_gates']==int(selected.sum())
    assert d['near_revision_new_qpe_loss_gates']==int(selected.sum())
    assert np.array_equal(out['QC_ACTION'][selected],np.full(int(selected.sum()),1,np.uint8))
    assert not out['QPE_ELIGIBLE_MASK'][selected].any()
    assert not np.isfinite(out['DBZH_USABLE'][selected]).any()
    assert not out['REFLECTIVITY_TRUST_MASK'][selected].any()


def test_strong_near_object_propagation_extends_bounded_objects():
    s=strong_scene();e=one(s,strong_object_config('quarantine'))
    core=e.arrays['CF_NR_STRONG_CORE_MASK']==1
    propagated=e.arrays['CF_NR_STRONG_OBJECT_PROPAGATED_MASK']==1
    candidate=e.arrays['CF_NR_STRONG_CANDIDATE_MASK']==1
    assert core.any() and propagated.any() and not np.any(core&propagated)
    assert np.array_equal(candidate,core|propagated)
    assert np.array_equal(e.arrays['CF_NR_STRONG_ACTION_MASK']==1,candidate)
    ids=set(map(int,np.unique(e.arrays['CF_NR_STRONG_OBJECT_ID'][candidate])))
    assert ids and all(i>0 for i in ids)


def test_strong_near_object_audit_records_without_actions():
    s=strong_scene();e=one(s,strong_object_config('audit'))
    assert (e.arrays['CF_NR_STRONG_CANDIDATE_MASK']==1).any()
    assert (e.arrays['CF_NR_STRONG_OBJECT_PROPAGATED_MASK']==1).any()
    assert not (e.arrays['CF_NR_STRONG_ACTION_MASK']==1).any()


def test_strong_near_object_budget_preserves_parent_cf():
    gaps=strong_scene().gap_after.copy();gaps[5]=True
    s=replace(strong_scene(),gap_after=gaps);c=config(strong_near=StrongNearConfig(
        mode='quarantine',object_propagation=True,maximum_strong_objects=1))
    e=one(s,c)
    assert e.summary['near_revision']['status']=='RESOURCE_LIMIT_ABSTAINED'
    assert not (e.arrays['CF_NR_STRONG_ACTION_MASK']==1).any()


@pytest.mark.parametrize('which',[0,1,2])
def test_strong_near_keeps_every_external_protection(which):
    protect=[np.zeros(strong_scene().shape,bool) for _ in range(3)];protect[which][:]=True
    e=one(strong_scene(),strong_config('quarantine'),protections=[tuple(protect)])
    assert e.arrays['CF_NR_STRONG_PROTECTED_MASK'].any()
    assert not e.arrays['CF_NR_STRONG_ACTION_MASK'].any()


@pytest.mark.parametrize('kw',[
    {'rho':.99},{'snr':5.},{'z':25.},{'z':55.},
])
def test_strong_near_requires_bounded_measurement_and_two_families(kw):
    s=strong_scene(**kw);e=one(s,strong_config('quarantine'))
    assert not e.arrays['CF_NR_STRONG_CANDIDATE_MASK'].any()

@pytest.mark.parametrize('key',['PHIDP','RHOHV','SNR'])
def test_no_target_measurement_no_candidate(key):
    s=partial();s=change(s,key,missing=True);e=one(s,config())
    assert not e.arrays['CF_NR_ACTION_MASK'].any()

@pytest.mark.parametrize('key,value',[('SNR',5.),('RHOHV',.99),('DBZH',35.),('DBZH',-20.)])
def test_good_weather_no_rain_and_low_signal_not_removed(key,value):
    s=change(partial(),key,value);e=one(s,config())
    assert not e.arrays['CF_NR_ACTION_MASK'].any()


def test_phase_wrap_and_smooth_gradient_not_noise():
    s=partial()
    for phase in (np.tile([359.,1.],50),np.arange(100)*5%360):
        q=change(s,'PHIDP',phase);e=one(q,config());assert not e.arrays['CF_NR_STRICT_MASK'].any()


def test_sparse_evidence_requires_own_phase_pair_and_six_samples():
    s=partial();f={k:v.copy() for k,v in s.fields.items()};a={k:np.zeros(s.shape,bool) for k in f}
    for k in f:a[k][5,50:52]=True
    s=replace(s,fields=f,available=a)
    assert not pm.extract(s,config())['CF_NR_STRICT_MASK'].any()


def test_gap_and_bad_ray_do_not_spread():
    s=partial();g=s.gap_after.copy();g[5]=True;s=replace(s,gap_after=g)
    e=pm.extract(s,config());assert not e['CF_NR_STRICT_MASK'][4:8].any()


def test_circular_azimuth_rotation_equivariance():
    s=partial(nr=360);s=replace(s,gap_after=np.zeros(360,bool))
    before=pm.extract(s,config())
    k=70;after=pm.extract(replace(s,azimuth=np.roll(s.azimuth,k),elevation=np.roll(s.elevation,k),
         good=np.roll(s.good,k),gap_after=np.roll(s.gap_after,k),ray_time_s=np.roll(s.ray_time_s,k),
         fields={n:np.roll(a,k,axis=0) for n,a in s.fields.items()},
         available={n:np.roll(a,k,axis=0) for n,a in s.available.items()}),config())
    for n in before:assert np.array_equal(np.roll(before[n],k,axis=0),after[n],equal_nan=True)

@pytest.mark.parametrize('which',[0,1,2])
def test_each_weather_protection_remains(which):
    s=partial();protect=[np.zeros(s.shape,bool) for _ in range(3)];protect[which][:]=True
    e=one(s,config(),protections=[tuple(protect)]);assert not e.arrays['CF_NR_ACTION_MASK'].any()


def test_audit_has_no_actions_even_when_parent_cf_quarantines():
    s=partial();e,out,d=check(s,config(mode='audit'))
    assert e.arrays['CF_NR_ACTION_MASK'].any();assert d['near_revision_cr_loss_gates']==0
    for k,a in baseline(s).items():assert np.array_equal(a,out[k],equal_nan=True)


def test_parent_audit_wins_over_child_experiment():
    s=partial();c=config().model_copy(update={'mode':'audit'});e,out,d=check(s,c)
    assert d['cr_loss_gates']==0


def test_temporal_strict_causal_mapping_and_original_indices():
    s=partial(time=1000.);p=partial(time=280.)
    donor=ct.PastSweep(p,'a'*64,'prior','site_a','p1',np.roll(np.arange(12),3))
    a=pm.extract(s,config());ev,rec=ct.derive(s,a,[donor],config(),radar_id='site_a',processing_id='p1',scan_id='now')
    assert ev['CF_NR_TEMPORAL_SUPPORT_MASK'].any();assert len(rec['sources'])==1
    m=ev['CF_NR_TEMPORAL_MEASURED_MASK']==1;assert np.allclose(ev['CF_NR_TEMPORAL_AGE_S'][m],720)
    assert ev['CF_NR_TEMPORAL_RAY'][5,50]==np.roll(np.arange(12),3)[5]

@pytest.mark.parametrize('age',[0.,-1.,901.])
def test_temporal_reject_future_self_time_and_stale(age):
    s=partial(time=1000.);p=partial(time=1000-age)
    ev,_=ct.derive(s,pm.extract(s,config()),[ct.PastSweep(p,'a'*64,'old','a','p')],config(),radar_id='a',processing_id='p',scan_id='new')
    assert not ev['CF_NR_TEMPORAL_SUPPORT_MASK'].any()

@pytest.mark.parametrize('radar,proc,scan',[('b','p','old'),('a','q','old'),('a','p','new')])
def test_temporal_identity_must_match(radar,proc,scan):
    s=partial(time=1000.);p=partial(time=280.)
    ev,_=ct.derive(s,pm.extract(s,config()),[ct.PastSweep(p,'a'*64,scan,radar,proc)],config(),radar_id='a',processing_id='p',scan_id='new')
    assert not ev['CF_NR_TEMPORAL_SUPPORT_MASK'].any()


def test_newer_conflicting_past_blocks_cherrypicking():
    s=partial(time=1000.);p=partial(time=900.,z=20.);older=partial(time=500.)
    ps=[ct.PastSweep(x,h*64,n,'a','p') for x,h,n in [(p,'a','newer'),(older,'b','older')]]
    ev,_=ct.derive(s,pm.extract(s,config()),ps,config(),radar_id='a',processing_id='p',scan_id='new')
    assert ev['CF_NR_TEMPORAL_MEASURED_MASK'].any();assert not ev['CF_NR_TEMPORAL_SUPPORT_MASK'].any()


def test_temporal_missing_time_unavailable():
    s=partial(time=None);ev,r=ct.derive(s,pm.extract(s,config()),[],config(),radar_id='a',processing_id='p',scan_id='new')
    assert r['status']=='CURRENT_TIME_UNAVAILABLE'


def test_resource_fallback_retains_whole_parent_cf():
    s=partial(time=1000.);p=partial(time=280.)
    c=config(maximum_previous_gates=1)
    ctx=RuntimeContext('a','now','p',(ct.PastSweep(p,'a'*64,'old','a','p'),))
    e=one(s,c,near_context=ctx)
    assert e.summary['near_revision']['status']=='RESOURCE_LIMIT_ABSTAINED'
    assert not e.arrays['CF_NR_ACTION_MASK'].any()
    old=one(s,cfg(mode='quarantine'))
    for k in ('CF_NONMET_SUPPORTED_MASK','CF_QUARANTINE_SUPPORTED_MASK'):assert np.array_equal(e.arrays[k],old.arrays[k])


def dem(s,c,verified=True):
    p=np.zeros(s.shape,'float32');p[:,10]=.95
    b=np.maximum.accumulate(p,axis=1);h=np.full(s.shape,500.,'float32');t=np.full(s.shape,100.,'float32')
    return ta.project(s,p,b,h,t,c,verified=verified)


def test_pbb_is_local_cbb_remains_behind_mountain():
    s=partial();a=dem(s,config())
    assert a['CF_NR_DEM_LOCAL_INTERCEPTION_MASK'][:,10].all()
    assert not a['CF_NR_DEM_LOCAL_INTERCEPTION_MASK'][:,20].any()
    assert a['CF_NR_DEM_SEVERE_MASK'][:,20].all()


def test_dem_qualification_before_max_does_not_claim_clutter():
    s=partial();c=config(partial_enabled=False,dem_policy='cr_withhold')
    e=one(s,c);e.arrays.update(dem(s,c))
    from volume_review.clutter_fusion.classifier import decide
    e.arrays.update(decide(e.arrays,c));out,d=apply(baseline(s),e.arrays,c,low_quality_flag=1024)
    assert out['CF_NR_DEM_CR_WITHHELD_MASK'].any();assert d['qpe_loss_gates']==0
    assert not (out[CR][:,20:]==1).any();assert np.array_equal(out['QC_FLAGS'],baseline(s)['QC_FLAGS'])


def test_unverified_dem_no_action():
    s=partial();a=dem(s,config(),verified=False);assert a['CF_NR_DEM_AVAILABLE_MASK'].any()
    assert not a['CF_NR_DEM_SEVERE_MASK'].any()


def test_unknown_upstream_dem_invalidates_downstream():
    s=partial();c=config();p=np.zeros(s.shape);b=p.copy();p[:,8]=np.nan
    a=ta.project(s,p,b,np.ones(s.shape),np.ones(s.shape),c,verified=True)
    assert not a['CF_NR_DEM_AVAILABLE_MASK'][:,8:].any()

@pytest.mark.parametrize('mode',['audit','disabled'])
def test_dem_diagnostics_not_disposition(mode):
    s=partial();c=config(partial_enabled=False,dem_policy=mode);e=one(s,c);e.arrays.update(dem(s,c))
    from volume_review.clutter_fusion.classifier import decide
    e.arrays.update(decide(e.arrays,c));o,d=apply(baseline(s),e.arrays,c,low_quality_flag=1024)
    assert not o['CF_NR_DEM_CR_WITHHELD_MASK'].any()


def test_terrain_datum_and_resource_absence():
    s=partial();c=config();a,r=ta.from_sampler(s,c,None,None,None);assert r['status']=='NO_TERRAIN_CONTEXT'
    beam=NS(altitude_datum_status='incompatible_with_epsg_3855')
    a,r=ta.from_sampler(s,c,beam,NS(cache_identity='verified-resource'),'dem-v1')
    assert r['status']=='VERTICAL_DATUM_UNVERIFIED' and not a['CF_NR_DEM_SEVERE_MASK'].any()


def test_serialized_additions_and_exact_parent_validation():
    s=partial();c=config();e,o,d=check(s,c);called=[]
    at=attributes(c,1024);at['operational_eligible']=False
    def parent(view,attrs):
        called.append(1)
        for k,a in baseline(s).items():assert np.array_equal(a,view[k],equal_nan=True)
    validate_serialized(o,at,parent);assert called==[1]
    bad={k:a.copy() for k,a in o.items()};bad[CR][5,50]=1
    with pytest.raises(ValueError):validate_serialized(bad,at,parent)


def test_parent_full_chain_adapter_with_new_fields():
    c=config();res,native=fixture(c,near=True,receiver=True)
    from volume_review.integration import review_result,root_attributes
    out=review_result(res,native)
    from volume_review.validation import validate_serialized as full_validate
    calls=[]
    full_validate(group(out.sweeps[0]),root_attributes(out.profile),lambda *_:calls.append(1))
    assert calls==[1];assert 'CF_NR_REASON' in out.sweeps[0].optional_qc_fields


def test_optional_digest_new_not_equal_old():
    assert config().digest!=cfg().digest
    assert config(mode='audit').digest!=config().digest

@pytest.mark.parametrize('key,value',[('DBZH',90.),('RHOHV',-1.),('RHOHV',1.1)])
def test_invalid_raw_physical_value_not_classified(key,value):
    s=change(partial(),key,value);e=one(s,config())
    assert not e.arrays['CF_NR_ACTION_MASK'].any()
