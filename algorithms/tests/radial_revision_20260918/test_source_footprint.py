import numpy as np
from .conftest import Native,load


def fixture():
    n=Native(np.full((9,300),20.))
    parent=np.zeros(n.shape,'uint32');parent[2:7,10:290]=1
    seed=np.zeros_like(parent);seed[3:6,10:290]=1
    seed[4,140:160]=0
    group={'RV2_RAW_FAN_ID':parent,'RV2_SOURCE_LEDGER_SEED_ID':seed}
    return n,group,np.zeros(n.shape,bool)


def run(n,g,b):return load('radial_revision.source_footprint').qualify(n,b,g)


def test_frozen_original_footprint_tracks_weak_tail_but_not_peripheral_growth():
    n,g,b=fixture();raw=n.fields['DBZH'].copy()
    out,report=run(n,g,b)
    assert out['RV2_SOURCE_FOOTPRINT_QUALIFIED_MASK'][4,140:160].all()
    assert not out['RV2_SOURCE_FOOTPRINT_QUALIFIED_MASK'][[2,6]].any()
    assert report['actions']==0 and not report['recursive_growth']
    assert np.array_equal(n.fields['DBZH'],raw)


def test_no_anchor_or_target_local_source_cannot_train_boundary():
    n,g,b=fixture();g['RV2_SOURCE_LEDGER_SEED_ID'][:]=0
    out,_=run(n,g,b);assert not out['RV2_SOURCE_FOOTPRINT_QUALIFIED_MASK'].any()
    n,g,b=fixture();g['RV2_SOURCE_LEDGER_SEED_ID'][:,:120]=0;g['RV2_SOURCE_LEDGER_SEED_ID'][:,180:]=0
    out,_=run(n,g,b);assert not out['RV2_SOURCE_FOOTPRINT_QUALIFIED_MASK'][4,140:160].any()


def test_barrier_missing_or_unstable_source_boundary_abstains():
    n,g,b=fixture();b[:,135]=True;b[:,165]=True
    out,_=run(n,g,b);assert not out['RV2_SOURCE_FOOTPRINT_QUALIFIED_MASK'][4,140:160].any()
    n,g,b=fixture();n.field_available['DBZH'][4,140:160]=False
    out,_=run(n,g,b);assert not out['RV2_SOURCE_FOOTPRINT_QUALIFIED_MASK'][4,140:160].any()
    n,g,b=fixture();g['RV2_SOURCE_LEDGER_SEED_ID'][3,:120]=0;g['RV2_SOURCE_LEDGER_SEED_ID'][5,180:]=0
    n.gap_after[3]=True
    out,_=run(n,g,b);assert not out['RV2_SOURCE_FOOTPRINT_QUALIFIED_MASK'].any()


def test_separate_source_rays_do_not_fill_angular_gap_or_wandering_boundary():
    n,g,b=fixture();g['RV2_SOURCE_LEDGER_SEED_ID'][4]=0
    out,_=run(n,g,b);assert not out['RV2_SOURCE_FOOTPRINT_QUALIFIED_MASK'].any()
    n=Native(np.full((13,300),20.));parent=np.ones(n.shape,'uint32');seed=np.zeros_like(parent)
    seed[2:4,10:120]=1;seed[7:9,180:290]=2
    out,_=run(n,{'RV2_RAW_FAN_ID':parent,'RV2_SOURCE_LEDGER_SEED_ID':seed},np.zeros(n.shape,bool))
    assert not out['RV2_SOURCE_FOOTPRINT_QUALIFIED_MASK'][5,140:160].any()


def test_current_measured_weather_like_polar_veto_and_unavailable_is_not_vote():
    n,g,b=fixture()
    for k,v in (('RHOHV',.99),('SNR',20.)):
        n.fields[k]=np.full(n.shape,v,'float32');n.field_available[k]=np.ones(n.shape,bool)
    out,report=run(n,g,b)
    assert not out['RV2_SOURCE_FOOTPRINT_QUALIFIED_MASK'][4,140:160].any()
    assert report['current_polar_retained_gates']>0
    n.field_available['RHOHV'][:]=False
    out,_=run(n,g,b)
    assert out['RV2_SOURCE_FOOTPRINT_QUALIFIED_MASK'][4,140:160].all()


def test_serialized_replay_rejects_forged_proofs_and_restores_native_order():
    import pytest
    n,g,b=fixture();module=load('radial_revision.source_footprint')
    out,_=run(n,g,b);out.update(module.evidence(n));out.update(g)
    module.validate(out,n.field_available['DBZH'],b)
    for key in ('REJECTION_CODE','QUALIFIED_MASK','RAW_PARENT_ID','LEFT_DEG','REFERENCE_BLOCKS','NATIVE_ORDER','NATIVE_RANGE_M'):
        bad={k:v.copy() for k,v in out.items()}
        bad[module.PREFIX+key][4,140]+=1
        with pytest.raises(ValueError):module.validate(bad,n.field_available['DBZH'],b)
    permutation=np.roll(np.arange(n.shape[0]),3)
    restored={k:v[permutation] for k,v in out.items()}
    module.validate(restored,n.field_available['DBZH'][permutation],b[permutation])
    bad={k:v.copy() for k,v in out.items()}
    bad['RV2_SOURCE_LEDGER_SEED_ID'][:]=0
    with pytest.raises(ValueError):module.validate(bad,n.field_available['DBZH'],b)
    bad={k:v.copy() for k,v in out.items()}
    bad[module.PREFIX+'NATIVE_GAP_MASK'][3]=1
    with pytest.raises(ValueError):module.validate(bad,n.field_available['DBZH'],b)
    protected=b.copy();protected[:,135]=1;protected[:,165]=1
    with pytest.raises(ValueError):module.validate(out,n.field_available['DBZH'],protected)


def test_serialized_polar_veto_is_recomputed_not_trusted_from_mask():
    import pytest
    n,g,b=fixture();module=load('radial_revision.source_footprint')
    out,_=run(n,g,b);out.update(module.evidence(n));out.update(g)
    for name,value in (('RHOHV',.99),('SNR',20.)):
        out[module.PREFIX+'MEASURED_'+name][4,140:160]=value
        out[module.PREFIX+name+'_AVAILABLE_MASK'][4,140:160]=1
    with pytest.raises(ValueError):module.validate(out,n.field_available['DBZH'],b)


def test_engine_and_writer_integration_with_default_off_and_audit_no_action():
    import pytest
    from .conftest import evaluate
    from .test_fan_joint import fixture as fan_fixture
    n,source,tail=fan_fixture()
    source[8:15,(n.ranges>=280000.)&(n.ranges<292000.)]=True
    n.fields['DBZH'][source]=20.
    n.field_available['DBZH']=np.isfinite(n.fields['DBZH'])
    n.fields['SNR']=np.where(source,30.,np.nan).astype('float32')
    n.field_available['SNR']=source.copy()
    for name,value in (('RHOHV',.99),('ZDR',.5),('PHIDP',20.)):
        n.fields[name]=np.where(source,value,np.nan).astype('float32')
        n.field_available[name]=source.copy()
    module=load('radial_revision.config')
    with pytest.raises(ValueError):module.FragmentLineConfig(source_footprint_enabled=True)
    assert not module.FragmentLineConfig().source_footprint_enabled
    cfg=module.RadialRevisionConfig(step=3,mode='experiment_quarantine',fragment_line={
        'raw_fragment_families_enabled':True,'source_ledger_enabled':True,
        'raw_fan_families_enabled':True,'source_footprint_enabled':True})
    out,_=evaluate(n,cfg,source)
    assert out['RV2_SOURCE_FOOTPRINT_QUALIFIED_MASK'][tail].any()
    assert out['RV2_ACTION_PROPOSAL_MASK'][tail].any()
    load('radial_revision.validation').validate_revision_fields(out,n.field_available['DBZH'],source,np.zeros(n.shape,bool))
    audit,_=evaluate(n,cfg.model_copy(update={'mode':'audit'}),source)
    assert not audit['RV2_ACTION_PROPOSAL_MASK'].any()
    load('radial_revision.validation').validate_revision_fields(audit,n.field_available['DBZH'],source,np.zeros(n.shape,bool))
    config=load('config').SourceReviewConfig(narrow_enabled=False,radial_revision=cfg.model_dump())
    _,arrays,_=load('source').source_additions(n,config,source,np.zeros(n.shape,'float32'))
    arrays['SRC_REVIEW_REFERENCE_FOLD_ID']=source.astype('uint32')
    load('source_validation').validate_source_fields(arrays,n.field_available['DBZH'])
    assert 'RV2_SOURCE_FOOTPRINT_NATIVE_ORDER' in arrays


def test_decision_trace_distinguishes_missing_anchor_extent_and_reference_failure():
    n,g,b=fixture();out,report=run(n,g,b);p='RV2_SOURCE_FOOTPRINT_'
    assert (out[p+'REJECTION_CODE'][4,140:160]==0).all()
    assert (out[p+'REJECTION_CODE'][[2,6],10:290]==4).all()
    assert sum(report['candidate_decisions'].values())==report['candidate_gates']
    n,g,b=fixture();g['RV2_SOURCE_LEDGER_SEED_ID'][:]=0
    out,_=run(n,g,b)
    assert (out[p+'REJECTION_CODE'][out[p+'CANDIDATE_MASK']==1]==1).all()
    n,g,b=fixture();b[:,135]=1;b[:,165]=1
    out,_=run(n,g,b)
    assert (out[p+'REJECTION_CODE'][4,140:160]==6).all()
    n,g,b=fixture();n.gap_after[3]=1
    out,_=run(n,g,b)
    within=(out[p+'CANDIDATE_MASK'][3:6]==1)
    assert (out[p+'REJECTION_CODE'][3:6][within]==3).all()
    assert (out[p+'REJECTION_CODE'][[2,6],10:290]==4).all()


def test_disconnected_original_bundles_track_separately_without_filling_gap():
    n=Native(np.full((12,300),20.))
    parent=np.ones(n.shape,'uint32');seed=np.zeros_like(parent)
    seed[2:5,10:290]=1;seed[7:10,10:290]=2
    seed[3,140:160]=0;seed[8,140:160]=0
    group={'RV2_RAW_FAN_ID':parent,'RV2_SOURCE_LEDGER_SEED_ID':seed}
    b=np.zeros(n.shape,bool);b[6]=True
    out,detail=run(n,group,b)
    qualified=out['RV2_SOURCE_FOOTPRINT_QUALIFIED_MASK']
    assert qualified[3,140:160].all() and qualified[8,140:160].all()
    assert not qualified[5:7].any()
    assert detail['original_source_components']==2
    arrays={**group,**out,**load('radial_revision.source_footprint').evidence(n)}
    load('radial_revision.source_footprint').validate(arrays,n.field_available['DBZH'],b)


def test_bundles_cannot_borrow_another_parents_source_or_short_islands_support():
    n=Native(np.full((12,300),20.))
    parent=np.ones(n.shape,'uint32');parent[:,150:]=2
    seed=np.zeros_like(parent);seed[2:5,150:]=1
    seed[7:10,10:30]=2
    group={'RV2_RAW_FAN_ID':parent,'RV2_SOURCE_LEDGER_SEED_ID':seed}
    out,_=run(n,group,np.zeros(n.shape,bool))
    assert not out['RV2_SOURCE_FOOTPRINT_QUALIFIED_MASK'][2:5,:150].any()
    assert not out['RV2_SOURCE_FOOTPRINT_QUALIFIED_MASK'][7:10].any()
