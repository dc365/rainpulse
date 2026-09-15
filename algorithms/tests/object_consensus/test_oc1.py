from dataclasses import replace, dataclass
import json
import numpy as np
import pytest

from rainpulse_algo.radar.qc_engine.object_consensus import (Config, Policy, RawScan, Reason, WeatherSupport, infer, apply_policy)
from rainpulse_algo.radar.qc_engine.object_consensus.geometry import build_domains
from rainpulse_algo.radar.qc_engine.object_consensus.io import write_npz, sha_file
from rainpulse_algo.radar.qc_engine.object_consensus.adapter import evaluate_native, scalar_completion


def scene(kind='noisy', nr=9, dr=250.):
    r=np.arange(dr/2,460000,dr)
    shape=(nr,len(r));ray=nr//2
    d={'range_m':r,'azimuth_rotated_deg':np.arange(nr,dtype=float),
       'elevation_deg':np.full(nr,.5),'geometry_good':np.ones(nr,bool),
       'gap_after':np.r_[np.zeros(nr-1,bool),True]}
    for name,value in [('DBZH',-20.),('SNR',0.),('RHOHV',.99),('PHIDP',20.),('ZDR',1.)]:
        d['raw_'+name]=np.full(shape,value,dtype='float32');d['available_'+name]=np.ones(shape,bool)
    selected=(r>=50000)&(r<450000)
    d['raw_DBZH'][ray,selected]=20*np.log10(r[selected]/1000)
    d['raw_SNR'][ray,selected]=12 if kind=='noisy' else 30
    d['raw_RHOHV'][ray,selected]=.3 if kind=='noisy' else 1
    if kind=='noisy':d['raw_PHIDP'][ray,selected]=np.where(np.arange(selected.sum())%2,90,0)
    d['review_roi']=np.zeros(shape,bool);d['review_roi'][ray,selected]=True
    d['baseline_rfi_anchor']=np.zeros(shape,bool)
    return d


def raw(d):
    return RawScan.from_arrays(d,full_ppi=False)


def baseline(r):
    obs=r.observed
    return {'QC_ACTION':np.where(obs,0,3).astype('uint8'),
            'QPE_ELIGIBLE_MASK':obs.astype('uint8'),'RFI_QUARANTINE_MASK':np.zeros(r.shape,'uint8'),
            'QUALITY_INDEX':np.where(obs,.8,np.nan).astype('float32'),
            'DBZH_USABLE':np.where(obs,r.fields['DBZH'],np.nan).astype('float32'),
            'DBZH_QC':np.where(obs,r.fields['DBZH'],np.nan).astype('float32'),
            'business_visible':obs.copy()}


@pytest.mark.parametrize('updates',[{'guard_blocks':0},{'block_m':0},{'maximum_identity_gap_fraction':.8},
                                    {'minimum_train_blocks':1},{'maximum_gates':True},{'range_min_m':np.nan}])
def test_invalid_configs(updates):
    with pytest.raises(ValueError):Config(**updates)


def test_no_implicit_apply():
    with pytest.raises(ValueError):Policy(mode='experiment_quarantine')
    assert Policy().mode=='audit'


def test_raw_missing_distinct_from_valid_noecho():
    d=scene();d['available_DBZH'][4,400]=False;d['raw_DBZH'][4,500]=-25
    r=raw(d);e=infer(r)
    assert e.arrays['state'][4,400]==0
    assert e.arrays['state'][4,500]!=0
    assert e.arrays['family_code'][4,500]==0


def test_nan_available_is_not_observed():
    d=scene();d['raw_DBZH'][4,400]=np.nan
    assert not raw(d).observed[4,400]


@pytest.mark.parametrize('fault',['shape','inf','mask','range','period'])
def test_input_rejections(fault):
    d=scene()
    if fault=='shape':d['raw_RHOHV']=np.zeros((3,4))
    if fault=='inf':d['raw_SNR'][0,0]=np.inf
    if fault=='mask':d['available_PHIDP']=np.full(d['raw_DBZH'].shape,2)
    if fault=='range':d['range_m'][5]+=10
    if fault=='period':
        with pytest.raises(ValueError):RawScan.from_arrays(d,full_ppi=False,phase_period=0)
        return
    with pytest.raises(ValueError):raw(d)


def test_noisy_source_forms_heldout_models():
    d=scene();e=infer(raw(d));m=(d['range_m']>=200000)&(d['range_m']<250000)
    assert np.all(e.arrays['family_code'][4,m]==1)
    assert np.all(e.arrays['state'][4,m]==5)
    fold=next(x for x in e.folds if x['ray']==4 and x['target_block']==3)
    assert not set([2,3,4]) & set(fold['train_blocks'])
    for a,b in fold['reference_intervals']:
        assert not np.any((d['range_m'][a:b]>=150000)&(d['range_m'][a:b]<300000))


def test_target_cannot_fit_itself_or_guard():
    d=scene();first=infer(raw(d));m=(d['range_m']>=150000)&(d['range_m']<300000)
    d['raw_SNR'][4,m]=44;d['raw_RHOHV'][4,m]=1;d['raw_PHIDP'][4,m]=5;d['raw_ZDR'][4,m]=3
    second=infer(raw(d))
    a=next(x for x in first.folds if x['ray']==4 and x['target_block']==3)
    b=next(x for x in second.folds if x['ray']==4 and x['target_block']==3)
    for key in ('reference_digest','family','train_blocks','snr_median_db','low_rho_fraction','increment_variance'):
        assert a[key]==b[key]
    target=(d['range_m']>=200000)&(d['range_m']<250000)
    assert np.all(second.arrays['state'][4,target]!=5)


def test_flip_baseline_roi_anchor_does_not_change_inference():
    d=scene();a=infer(raw(d));rng=np.random.default_rng(7)
    for k in ('review_roi','baseline_rfi_anchor'):
        d[k]=rng.random(d[k].shape)>.5
    d['baseline_QC_ACTION']=rng.integers(0,4,d['raw_DBZH'].shape)
    b=infer(raw(d));assert a.identity==b.identity
    for k in a.arrays:assert np.array_equal(a.arrays[k],b.arrays[k],equal_nan=True)


def test_source_inputs_immutable():
    d=scene();copy={k:v.copy() for k,v in d.items()};r=raw(d);infer(r)
    for k in copy:assert np.array_equal(copy[k],d[k],equal_nan=True)
    assert not r.fields['DBZH'].flags.writeable


def test_long_hole_splits_reference_identity():
    d=scene();hole=(d['range_m']>=210000)&(d['range_m']<250000)
    d['available_DBZH'][4,hole]=False
    r=raw(d);ds,ids,*_=build_domains(r,Config())
    assert not np.any(ids[4,hole])
    assert all(not(x.lo<840 and x.hi>1000) for x in ds if x.ray==4)
    e=infer(r);assert not e.arrays['family_code'][4].any()


def test_short_hole_links_identity_not_samples():
    d=scene();d['available_DBZH'][4,850:853]=False
    r=raw(d);ds,ids,*_=build_domains(r,Config());assert ids[4,849]==ids[4,853]>0
    assert not ids[4,850:853].any()
    e=infer(r);assert not e.arrays['family_code'][4,850:853].any()


def test_measured_clear_gate_is_identity_barrier():
    d=scene();d['raw_DBZH'][4,900]=-25
    ds,ids,*_=build_domains(raw(d),Config())
    assert not ids[4,900]
    assert all(not(x.lo<900 and x.hi>901) for x in ds if x.ray==4)


@pytest.mark.parametrize('missing',['SNR','RHOHV','PHIDP'])
def test_missing_measurements_abstain(missing):
    d=scene();d.pop('raw_'+missing);d.pop('available_'+missing)
    e=infer(raw(d));assert not e.arrays['family_code'].any()


def test_phase_wrap_and_azimuth_rotation_invariant():
    d=scene();a=infer(raw(d));d['raw_PHIDP']=(d['raw_PHIDP']+353)%360
    d['azimuth_rotated_deg']=(d['azimuth_rotated_deg']+358)%360
    b=infer(raw(d))
    for k in ('family_code','state','domain_id'):assert np.array_equal(a.arrays[k],b.arrays[k])


def test_linear_phase_is_not_random_phase_noise():
    d=scene();d['raw_PHIDP'][4]=np.mod(np.arange(d['raw_PHIDP'].shape[1])*.1,360)
    e=infer(raw(d));assert not e.arrays['family_code'].any()


def test_target_healthy_weather_blocks_noisy_signature():
    d=scene();m=(d['range_m']>=200000)&(d['range_m']<250000)
    d['raw_RHOHV'][4,m]=1;d['raw_PHIDP'][4,m]=30
    e=infer(raw(d));assert np.all(e.arrays['family_code'][4,m]==1)
    assert np.sum(e.arrays['state'][4,m]==4)>=m.sum()-2


def test_small_strong_cell_is_not_whole_ray_action():
    d=scene();m=(d['range_m']>=215000)&(d['range_m']<220000)
    d['raw_SNR'][4,m]=40;d['raw_DBZH'][4,m]=65;d['raw_RHOHV'][4,m]=.98
    e=infer(raw(d));assert not np.any(e.arrays['state'][4,m]==5)


def test_short_weak_line_cannot_borrow_distant_object():
    d=scene();d['raw_DBZH'][4]=-20;m=(d['range_m']>=380000)&(d['range_m']<390000)
    d['raw_DBZH'][4,m]=15
    assert not infer(raw(d)).arrays['family_code'].any()


def test_broad_healthy_weather_not_withheld():
    d=scene('coherent');m=(d['range_m']>=50000)&(d['range_m']<450000)
    for k in ('DBZH','SNR','RHOHV','PHIDP','ZDR'):
        d['raw_'+k][:,m]=d['raw_'+k][4,m]
    # Full 360-degree field: no narrow directional interpretation.
    d['azimuth_rotated_deg']=np.arange(9)*40.;d['gap_after'][:]=False
    e=infer(RawScan.from_arrays(d,full_ppi=True))
    assert not np.any(e.arrays['state']==5)


def test_identical_coherent_weather_can_be_indistinguishable_and_is_not_confirmed():
    d=scene('coherent');r=raw(d);e=infer(r)
    assert np.any(e.arrays['family_code']==2)
    policy=Policy(mode='experiment_quarantine',allow_coherent_quarantine=True,
                  acknowledge_uncalibrated_model=True,maximum_new_eligible_loss_fraction=1)
    o=apply_policy(r,e,baseline(r),policy)
    assert o.summary['confirmed_additions']==0 and o.summary['precision'] is None
    # This counterexample intentionally does NOT claim zero weather false positives.
    assert o.added_quarantine.any()


def test_coherent_bundle_is_not_rejected_solely_by_narrow_width():
    d=scene('coherent',nr=15)
    for k in ('DBZH','SNR','RHOHV','PHIDP','ZDR'):
        d['raw_'+k][6]=d['raw_'+k][7];d['raw_'+k][8]=d['raw_'+k][7]
    e=infer(raw(d));m=(e.arrays['family_code']==2)&(e.arrays['source_width_deg']>2.5)
    assert m.any() and np.any(e.arrays['structural_kind'][m]==2)


def test_geometry_gap_prevents_cross_ray_bundle():
    d=scene('noisy');d['raw_DBZH'][5]=d['raw_DBZH'][4]
    d['gap_after'][4]=True
    r=raw(d);ds,ids,bundle,_,links=build_domains(r,Config())
    assert bundle[4,800] != bundle[5,800]


def test_unverified_weather_not_accepted():
    d=scene();r=raw(d)
    w=WeatherSupport(np.ones(r.shape),np.ones(r.shape,bool),'a'*64,False)
    with pytest.raises(ValueError):infer(r,weather=w)


def test_weather_missing_is_not_negative_evidence():
    r=raw(scene());w=WeatherSupport(np.full(r.shape,np.nan),np.zeros(r.shape,bool),'a'*64,True)
    a=infer(r);b=infer(r,weather=w)
    assert np.array_equal(a.arrays['state'],b.arrays['state'])


def test_vetted_weather_conflict():
    r=raw(scene());w=WeatherSupport(np.ones(r.shape),np.ones(r.shape,bool),'a'*64,True)
    e=infer(r,weather=w);assert not np.any(e.arrays['state']==5)


def test_audit_policy_preserves_every_baseline_array():
    r=raw(scene());e=infer(r);b=baseline(r);o=apply_policy(r,e,b)
    assert not o.added_quarantine.any()
    for k in b:assert b[k].tobytes()==o.arrays[k].tobytes()


def test_experiment_withholds_measured_targets_but_never_restores():
    r=raw(scene());e=infer(r);b=baseline(r)
    b['QC_ACTION'][4,820]=2;b['QPE_ELIGIBLE_MASK'][4,820]=0;b['DBZH_USABLE'][4,820]=np.nan;b['business_visible'][4,820]=False
    o=apply_policy(r,e,b,Policy(mode='experiment_quarantine',acknowledge_uncalibrated_model=True,maximum_new_eligible_loss_fraction=1))
    assert o.added_quarantine.any();assert o.arrays['QC_ACTION'][4,820]==2
    assert not np.any((o.arrays['QPE_ELIGIBLE_MASK']==1)&(b['QPE_ELIGIBLE_MASK']!=1))
    assert np.all(np.isnan(o.arrays['DBZH_USABLE'][o.added_quarantine]))


def test_whole_cut_budget_reverts_without_partial_best_gates():
    r=raw(scene());e=infer(r);b=baseline(r)
    p=Policy(mode='experiment_quarantine',acknowledge_uncalibrated_model=True,maximum_new_eligible_loss_fraction=.0001)
    o=apply_policy(r,e,b,p);assert o.summary['status']=='blocked_budget_whole_cut_reverted'
    for k in b:assert b[k].tobytes()==o.arrays[k].tobytes()


def test_unbracketed_endpoints_not_applied_by_default():
    r=raw(scene());e=infer(r);b=baseline(r)
    o=apply_policy(r,e,b,Policy(mode='experiment_quarantine',acknowledge_uncalibrated_model=True,maximum_new_eligible_loss_fraction=1))
    assert not np.any(o.added_quarantine & (e.arrays['bracketed_reference_mask']==0))


def test_policy_identity_separates_audit_and_experiment():
    r=raw(scene());e=infer(r);b=baseline(r);a=apply_policy(r,e,b)
    q=apply_policy(r,e,b,Policy(mode='experiment_quarantine',acknowledge_uncalibrated_model=True))
    assert a.summary['outcome_sha256']!=q.summary['outcome_sha256']


def test_deterministic_npz_and_repeat_inference(tmp_path):
    r=raw(scene());a=infer(r);b=infer(r);assert a.identity==b.identity
    write_npz(tmp_path/'a.npz',a.arrays);write_npz(tmp_path/'b.npz',b.arrays)
    assert sha_file(tmp_path/'a.npz')==sha_file(tmp_path/'b.npz')


def test_gate_budget_stops_without_partial():
    with pytest.raises(ValueError):RawScan.from_arrays(scene(),full_ppi=False,maximum_gates=10)
    with pytest.raises(ValueError):infer(raw(scene()),replace(Config(),maximum_folds=1))


def test_two_distance_resolutions_same_source_family():
    for dr in (250.,500.):
        d=scene(dr=dr);e=infer(raw(d));m=(d['range_m']>=200000)&(d['range_m']<250000)
        assert np.all(e.arrays['family_code'][4,m]==1)


def test_decision_adapter_abi_and_flags():
    @dataclass
    class Decision:
        arrays:dict
        flags:np.ndarray
        quality:np.ndarray
    class Native:pass
    r=raw(scene());n=Native();n.fields=r.fields;n.field_available=r.available
    n.ranges=r.ranges;n.azimuth=r.azimuth;n.elevation=r.elevation;n.geometry_good=r.good
    n.gap_after=r.gaps;n.full_ppi=r.full_ppi
    b=baseline(r);q=b.pop('QUALITY_INDEX');b.pop('business_visible');b.pop('DBZH_QC')
    b['REFLECTIVITY_TRUST_MASK']=r.observed.astype('uint8')
    original=Decision(b,np.zeros(r.shape,'uint32'),q)
    policy=Policy(mode='experiment_quarantine',acknowledge_uncalibrated_model=True,maximum_new_eligible_loss_fraction=1)
    decision,e,o=evaluate_native(n,original,policy=policy,low_quality_flag=16384)
    assert o.added_quarantine.any()
    assert np.all(decision.flags[o.added_quarantine]==16384)
    assert not np.any(decision.flags & (8|32768))
    assert np.all(decision.arrays['REFLECTIVITY_TRUST_MASK'][o.added_quarantine]==0)
    assert len(json.dumps(scalar_completion(e,o)))<4096


def test_bad_geometry_abstains_but_does_not_reclassify_valid_measurements_as_missing():
    d=scene();d['geometry_good'][4]=False;r=raw(d);e=infer(r)
    assert r.observed[4].all()
    assert not e.arrays['family_code'][4].any()
    assert not np.any(e.arrays['state'][4]==0)
    assert np.all((e.arrays['reason'][4]&int(Reason.GEOMETRY_UNAVAILABLE))!=0)
    b=baseline(r);o=apply_policy(r,e,b)
    assert np.all(o.arrays['QC_ACTION'][4]==0)


def test_evidence_tampering_refused_before_any_action():
    r=raw(scene());e=infer(r);e.arrays['state'][4,1000]=1
    with pytest.raises(ValueError,match='content changed'):apply_policy(r,e,baseline(r))


def test_raw_dictionary_cannot_be_rebound():
    r=raw(scene())
    with pytest.raises(TypeError):r.fields['DBZH']=np.zeros(r.shape)
