import numpy as np
import pytest
from .conftest import Native,load,evaluate
from .test_raw_families import short_family
from .test_discontinuous import fragmented


def families(n,blocked=None):
    return load('radial_revision.raw_families').detect(n,np.zeros(n.shape,bool) if blocked is None else blocked)[0]


def seeded():
    n,hit=short_family()
    n.fields['DBZH'][:]=np.nan
    seeds=np.zeros(n.shape,bool);seeds[6,200:280]=True
    hit[:]=False;hit[6,320:322]=True;hit[7,400:402]=True
    n.fields['DBZH'][seeds]=30.;n.fields['DBZH'][hit]=15.
    n.field_available['DBZH']=np.isfinite(n.fields['DBZH'])
    return n,seeds,hit


def qualify(n,seeds,blocked=None):
    blocked=np.zeros(n.shape,bool) if blocked is None else blocked
    return load('radial_revision.family_joint').qualify(n,families(n,blocked),blocked,seeds)


def test_frozen_original_source_carries_short_weak_remnants_without_polar():
    n,seeds,hit=seeded();out,report=qualify(n,seeds)
    assert out['RV2_FAMILY_JOINT_QUALIFIED_MASK'][hit].all()
    assert out['RV2_FAMILY_JOINT_ANCHORED_MASK'][hit].all()
    assert not out['RV2_FAMILY_JOINT_POLAR_AVAILABLE_MASK'].any()
    assert np.isin(out['RV2_FAMILY_JOINT_PARENT_ID'][hit],out['RV2_FAMILY_JOINT_SOURCE_SEED_ID'][seeds]).all()
    assert report['recursive_growth'] is False


def test_short_source_seed_is_not_sufficient_and_no_recursive_growth():
    n,seeds,hit=seeded();seeds[6,210:280]=False
    out,_=qualify(n,seeds)
    assert not out['RV2_FAMILY_JOINT_QUALIFIED_MASK'][hit].any()
    n,seeds,hit=seeded();n.fields['DBZH'][7,880:882]=15.;n.field_available['DBZH']=np.isfinite(n.fields['DBZH'])
    out,_=qualify(n,seeds)
    assert not out['RV2_FAMILY_JOINT_QUALIFIED_MASK'][7,880:882].any()


def test_unanchored_requires_reliable_local_polar_and_two_measured_windows():
    n,hit=fragmented(dr=250.,spacing=.5,measured=True)
    n.fields.update(SNR=np.full(n.shape,15.,'float32'),RHOHV=np.full(n.shape,.5,'float32'))
    n.field_available.update(SNR=np.ones(n.shape,bool),RHOHV=np.ones(n.shape,bool))
    out,_=qualify(n,np.zeros(n.shape,bool))
    assert out['RV2_FAMILY_JOINT_POLAR_MASK'][hit].all()
    within = hit & (n.ranges[None,:] < 300000.)
    assert out['RV2_FAMILY_JOINT_QUALIFIED_MASK'][within].all()
    assert not out['RV2_FAMILY_JOINT_QUALIFIED_MASK'][hit & ~within].any()
    for kind in ('low_snr','missing','one_flank'):
        bad=n.clone()
        if kind=='low_snr':bad.fields['SNR'][:]=5.
        if kind=='missing':bad.field_available['RHOHV'][:]=False
        if kind=='one_flank':bad.field_available['DBZH'][4]=False;bad.field_available['DBZH'][3]=False
        out,_=qualify(bad,np.zeros(bad.shape,bool))
        assert not out['RV2_FAMILY_JOINT_QUALIFIED_MASK'][hit].any()


def test_weather_barrier_and_audit_mode_preserve_observations():
    n,seeds,hit=seeded();barrier=np.zeros(n.shape,bool);barrier[:,300:305]=True
    out,_=qualify(n,seeds,barrier)
    assert not out['RV2_FAMILY_JOINT_QUALIFIED_MASK'][hit].any()
    cfg=load('radial_revision.config').RadialRevisionConfig(fragment_line={
        'raw_fragment_families_enabled':True,'family_joint_enabled':True})
    out,_=evaluate(n,cfg,seeds)
    assert not out['RV2_ACTION_PROPOSAL_MASK'].any()
    load('radial_revision.validation').validate_revision_fields(out,n.field_available['DBZH'],seeds,np.zeros(n.shape,bool))


def test_phase_wrap_and_missingness_are_diagnostics_never_action_votes():
    n,hit=fragmented(dr=250.,spacing=.5,measured=True)
    n.fields['PHIDP']=np.broadcast_to(np.where(np.arange(n.shape[1])%2,359.,1.),n.shape).astype('float32').copy()
    n.field_available['PHIDP']=np.ones(n.shape,bool)
    out,_=qualify(n,np.zeros(n.shape,bool))
    variance=out['RV2_FAMILY_JOINT_PHIDP_CIRCULAR_VARIANCE']
    assert np.nanmax(variance)<.001
    assert not out['RV2_FAMILY_JOINT_QUALIFIED_MASK'].any()
    n.field_available['PHIDP'][:]=False
    out,_=qualify(n,np.zeros(n.shape,bool))
    assert np.isnan(out['RV2_FAMILY_JOINT_PHIDP_CIRCULAR_VARIANCE']).all()


def test_switch_requires_raw_family_and_serialization_rejects_forged_parent():
    with pytest.raises(ValueError):
        load('radial_revision.config').FragmentLineConfig(family_joint_enabled=True)
    n,seeds,hit=seeded()
    cfg=load('radial_revision.config').RadialRevisionConfig(mode='experiment_quarantine',fragment_line={
        'raw_fragment_families_enabled':True,'family_joint_enabled':True})
    # Strong SOURCE measurements have SNR/polar support; weak tails do not.
    for key,value in (('SNR',25.),('RHOHV',.99),('ZDR',.5),('PHIDP',20.)):
        n.fields[key]=np.where(seeds,value,np.nan).astype('float32');n.field_available[key]=seeds.copy()
    out,_=evaluate(n,cfg,seeds)
    assert out['RV2_ACTION_PROPOSAL_MASK'][hit].all()
    validate=load('radial_revision.validation').validate_revision_fields
    validate(out,n.field_available['DBZH'],seeds,np.zeros(n.shape,bool))
    out['RV2_FAMILY_JOINT_PARENT_ID'][hit]=9999
    with pytest.raises(ValueError):validate(out,n.field_available['DBZH'],seeds,np.zeros(n.shape,bool))


def test_serialization_rejects_forged_local_polar_and_seed_support():
    n,seeds,hit=seeded()
    for key,value in (('SNR',25.),('RHOHV',.99),('ZDR',.5),('PHIDP',20.)):
        n.fields[key]=np.where(seeds,value,np.nan).astype('float32');n.field_available[key]=seeds.copy()
    cfg=load('radial_revision.config').RadialRevisionConfig(mode='experiment_quarantine',fragment_line={
        'raw_fragment_families_enabled':True,'family_joint_enabled':True})
    out,_=evaluate(n,cfg,seeds)
    validate=load('radial_revision.validation').validate_revision_fields
    bad={k:v.copy() for k,v in out.items()}
    bad['RV2_FAMILY_JOINT_POLAR_AVAILABLE_MASK'][hit]=1
    with pytest.raises(ValueError):validate(bad,n.field_available['DBZH'],seeds,np.zeros(n.shape,bool))
    bad={k:v.copy() for k,v in out.items()}
    bad['RV2_FAMILY_JOINT_SOURCE_SEED_ID'][seeds]=0
    bad['RV2_FAMILY_JOINT_SOURCE_SEED_ID'][6,200]=bad['RV2_RAW_FAMILY_ID'][6,200]
    with pytest.raises(ValueError):validate(bad,n.field_available['DBZH'],seeds,np.zeros(n.shape,bool))


def test_persisted_source_and_step3_identities_include_joint_actions():
    n,seeds,hit=seeded()
    for key,value in (('SNR',25.),('RHOHV',.99),('ZDR',.5),('PHIDP',20.)):
        n.fields[key]=np.where(seeds,value,np.nan).astype('float32');n.field_available[key]=seeds.copy()
    config=load('config').SourceReviewConfig(narrow_enabled=False,radial_revision={
        'step':3,'mode':'experiment_quarantine','fragment_line':{
        'raw_fragment_families_enabled':True,'family_joint_enabled':True}})
    qualified,arrays,_=load('source').source_additions(n,config,seeds,np.zeros(n.shape,'float32'))
    assert qualified[hit].all()
    assert np.all(arrays['RV2_OBJECT_ID'][hit]>0)
    arrays['SRC_REVIEW_REFERENCE_FOLD_ID']=seeds.astype('uint32')
    load('source_validation').validate_source_fields(arrays,n.field_available['DBZH'])
