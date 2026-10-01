import numpy as np
import pytest
from .conftest import Native,load,evaluate

P='RV2_SOURCE_LEDGER_'


def case():
    z=np.full((13,500),np.nan,'float32')
    seeds=np.zeros(z.shape,bool);seeds[6,20:240]=True
    z[seeds]=30.;z[6,300:305]=5.;z[6,420:425]=5.
    n=Native(z,start=0.)
    candidates=np.zeros(n.shape,bool);candidates[6,300:305]=True;candidates[6,420:425]=True
    return n,seeds,candidates


def freeze(n,seeds,candidates,blocked=None):
    barrier=np.zeros(n.shape,bool) if blocked is None else blocked
    module=load('radial_revision.source_ledger')
    out,report=module.freeze(n,barrier,seeds,candidates)
    module.validate(out,n.field_available['DBZH'],barrier,seeds)
    return out,report


def test_original_source_is_complete_and_independent_of_narrow_family_tiles():
    n,seeds,candidates=case()
    families=load('radial_revision.raw_families').detect(n,np.zeros(n.shape,bool))[0]
    assert len(np.unique(families['RV2_RAW_FAMILY_ID'][seeds]))>1
    out,report=freeze(n,seeds,candidates)
    assert np.array_equal(out[P+'SEED_MASK']==1,seeds)
    assert len(np.unique(out[P+'SEED_ID'][seeds]))==1
    assert (out[P+'SUPPORT_M'][seeds]==220000.).all()
    assert out[P+'LINK_MASK'][6,300:305].all()
    assert not out[P+'LINK_MASK'][6,420:425].any()
    assert report['action_gates']==0 and not report['recursive_growth']
    # Removing/changing nominations cannot change the original ledger or bounds.
    again,_=freeze(n,seeds,np.zeros(n.shape,bool))
    for key in ('SEED_ID','SUPPORT_M','START_M','END_M','RAW_START_M','RAW_END_M'):
        assert np.array_equal(out[P+key],again[P+key],equal_nan=True)


def test_short_and_wide_original_sources_are_recorded_but_not_linked():
    n,seeds,candidates=case();seeds[:,29:]=False
    out,_=freeze(n,seeds,candidates)
    assert out[P+'SEED_ID'][seeds].all()
    assert (out[P+'SOURCE_HOLD'][seeds]&1).all()
    assert not out[P+'LINK_MASK'].any()
    n,seeds,candidates=case()
    n.fields['DBZH'][2:11,20:240]=30.
    n.field_available['DBZH']=np.isfinite(n.fields['DBZH'])
    out,_=freeze(n,seeds,candidates)
    assert out[P+'SEED_ID'][seeds].all()
    assert (out[P+'SOURCE_HOLD'][seeds]&2).all()
    assert not out[P+'LINK_MASK'].any()


def test_empty_protected_gap_and_geometry_barriers_stop_lineage():
    n,seeds,candidates=case();barrier=np.zeros(n.shape,bool);barrier[:,270:272]=True
    out,_=freeze(n,seeds,candidates,barrier)
    assert not out[P+'LINK_MASK'].any()
    n.gap_after[5]=True
    out,_=freeze(n,seeds,candidates)
    assert not out[P+'LINK_MASK'].any()


def test_three_competing_original_sources_remain_ambiguous():
    n,seeds,candidates=case();n.fields['DBZH'][:]=np.nan;seeds[:]=False;candidates[:]=False
    for a,b in ((20,40),(101,121),(182,202)):seeds[6,a:b]=True
    n.fields['DBZH'][seeds]=30.;n.fields['DBZH'][6,140:143]=5.;candidates[6,140:143]=True
    n.field_available['DBZH']=np.isfinite(n.fields['DBZH'])
    out,report=freeze(n,seeds,candidates)
    assert report['objects']==3
    assert not out[P+'LINK_MASK'][candidates].any()
    assert (out[P+'LINK_HOLD'][candidates]==16).all()
    assert not out[P+'RAW_PARENT_ID'][candidates].any()


def test_no_angular_relay_and_raw_preserved_after_core_removed_from_qc():
    n,seeds,candidates=case();raw=n.fields['DBZH'].copy()
    n.fields['DBZH'][6,300:305]=np.nan;candidates[6,300:305]=False
    n.fields['DBZH'][7,300:305]=5.;n.fields['DBZH'][8,310:315]=5.
    n.field_available['DBZH']=np.isfinite(n.fields['DBZH'])
    candidates[7,300:305]=True;candidates[8,310:315]=True
    n.fields['DBZH_QC']=n.fields['DBZH'].copy();n.fields['DBZH_QC'][seeds]=np.nan
    raw=n.fields['DBZH'].copy()
    out,_=freeze(n,seeds,candidates)
    assert out[P+'LINK_MASK'][7,300:305].all()
    assert not out[P+'LINK_MASK'][8,310:315].any()
    assert not out[P+'SEED_ID'][7:].any()
    assert np.array_equal(n.fields['DBZH'],raw,equal_nan=True)


def test_serialization_rejects_forged_parent_support_and_distance():
    n,seeds,candidates=case();out,_=freeze(n,seeds,candidates)
    validate=load('radial_revision.source_ledger').validate
    for key,value,where in (('LINK_PARENT_ID',9999,out[P+'LINK_MASK']==1),
                            ('SUPPORT_M',999999,seeds),('LINK_DISTANCE_M',1.,out[P+'LINK_MASK']==1)):
        bad={k:v.copy() for k,v in out.items()};bad[P+key][where]=value
        with pytest.raises(ValueError):validate(bad,n.field_available['DBZH'],np.zeros(n.shape,bool),seeds)


def test_engine_ledger_is_diagnostic_and_never_changes_actions_or_source_fit():
    n,seeds,_=case()
    for key,value in (('SNR',25.),('RHOHV',.99),('ZDR',.5),('PHIDP',20.)):
        n.fields[key]=np.where(seeds,value,np.nan).astype('float32');n.field_available[key]=seeds.copy()
    cfg=load('radial_revision.config').RadialRevisionConfig(mode='experiment_quarantine',fragment_line={
        'raw_fragment_families_enabled':True,'source_ledger_enabled':True})
    out,_=evaluate(n,cfg,seeds)
    baseline,_=evaluate(n,cfg.model_copy(update={'fragment_line':cfg.fragment_line.model_copy(update={'source_ledger_enabled':False})}),seeds)
    for key,value in baseline.items():assert np.array_equal(out[key],value,equal_nan=True)
    assert np.array_equal(out[P+'SEED_MASK']==1,seeds)
    load('radial_revision.validation').validate_revision_fields(out,n.field_available['DBZH'],seeds,np.zeros(n.shape,bool))
    with pytest.raises(ValueError):load('radial_revision.config').FragmentLineConfig(source_ledger_enabled=True)


def test_complete_ledger_persists_through_source_writer_validation():
    n,seeds,_=case()
    for key,value in (('SNR',25.),('RHOHV',.99),('ZDR',.5),('PHIDP',20.)):
        n.fields[key]=np.where(seeds,value,np.nan).astype('float32');n.field_available[key]=seeds.copy()
    config=load('config').SourceReviewConfig(narrow_enabled=False,radial_revision={
        'step':3,'mode':'experiment_quarantine','fragment_line':{
        'raw_fragment_families_enabled':True,'source_ledger_enabled':True}})
    _,arrays,_=load('source').source_additions(n,config,seeds,np.zeros(n.shape,'float32'))
    arrays['SRC_REVIEW_REFERENCE_FOLD_ID']=seeds.astype('uint32')
    load('source_validation').validate_source_fields(arrays,n.field_available['DBZH'])
    assert np.array_equal(arrays[P+'SEED_MASK']==1,seeds)
    bad={k:v.copy() for k,v in arrays.items()};bad[P+'KIND'][seeds]=16
    with pytest.raises(ValueError):load('source_validation').validate_source_fields(bad,n.field_available['DBZH'])
