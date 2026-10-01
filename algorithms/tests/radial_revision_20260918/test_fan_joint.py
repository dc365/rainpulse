import numpy as np
import pytest
from .conftest import Native, load, evaluate as engine_evaluate

P = 'RV2_FAN_JOINT_'


def fixture():
    r = np.arange(1000., 361000., 1000.)
    z = np.full((20,len(r)),np.nan,'float32');source = np.zeros(z.shape,bool)
    for start in (60000.,100000.,140000.,180000.):
        source[8:15,(r>=start)&(r<start+12000.)] = True
    tail = np.zeros(z.shape,bool);tail[8:15,(r>=240000.)&(r<242000.)] = True
    z[source|tail] = np.broadcast_to(20.+20.*np.log10(r/50000.),z.shape)[source|tail]
    n = Native(z, start=1000.)
    return n,source,tail


def evaluate(n,source,blocked=None):
    blocked=np.zeros(n.shape,bool) if blocked is None else blocked
    ledger=load('radial_revision.source_ledger').freeze(n,blocked,source,np.zeros(n.shape,bool))[0]
    fans=load('radial_revision.raw_fans').detect(n,blocked,ledger['RV2_SOURCE_LEDGER_SEED_ID'])[0]
    module=load('radial_revision.fan_joint')
    fields,report=module.qualify(n,blocked,{**ledger,**fans})
    out={**ledger,**fans,**fields}
    module.validate(out,n.field_available['DBZH'],blocked)
    return out,report


def test_original_source_checks_sparse_tail_with_independent_reference_blocks():
    n,source,tail=fixture();raw=n.fields['DBZH'].copy()
    out,report=evaluate(n,source)
    assert out[P+'QUALIFIED_MASK'][tail].all()
    assert not out[P+'ORIGINAL_SOURCE_MASK'][tail].any()
    assert (out[P+'REFERENCE_SUPPORT_M'][tail]>=10000.).all()
    assert report['source_claim'] is False and report['recursive_growth'] is False
    assert np.array_equal(raw,n.fields['DBZH'],equal_nan=True)
    # A weather-like target anomaly cannot be fitted back into references.
    n.fields['DBZH'][tail]+=15.
    bad,_=evaluate(n,source)
    assert not bad[P+'QUALIFIED_MASK'][tail].any()
    assert np.array_equal(out[P+'INTERCEPT_DB'][tail],bad[P+'INTERCEPT_DB'][tail],equal_nan=True)


def test_missing_weather_gap_and_no_source_abstain():
    n,source,tail=fixture();barrier=np.zeros(n.shape,bool);barrier[:,210:212]=True
    out,_=evaluate(n,source,barrier)
    assert not out[P+'QUALIFIED_MASK'][tail].any()
    out,_=evaluate(n,np.zeros(n.shape,bool))
    assert not out[P+'QUALIFIED_MASK'].any()
    assert not out[P+'QUALIFIED_MASK'][~n.field_available['DBZH']].any()


def test_target_guard_seed_support_cannot_self_validate():
    n,source,tail=fixture();source[:]=False
    source[8:15,(n.ranges>=220000.)&(n.ranges<280000.)]=True
    n.fields['DBZH'][source]=np.broadcast_to(20.+20.*np.log10(n.ranges/50000.),n.shape)[source]
    source[tail]=False;n.field_available['DBZH']=np.isfinite(n.fields['DBZH'])
    out,_=evaluate(n,source)
    assert not out[P+'QUALIFIED_MASK'][tail].any()


def test_two_original_sources_cannot_claim_the_same_tail():
    n,source,tail=fixture();n.fields['DBZH'][:]=np.nan;source[:]=False;tail[:]=False
    for start in (60000.,100000.,140000.,230000.,270000.,310000.):
        source[8:15,(n.ranges>=start)&(n.ranges<start+12000.)]=True
    tail[8:15,(n.ranges>=200000.)&(n.ranges<202000.)]=True
    n.fields['DBZH'][source|tail]=np.broadcast_to(20.+20.*np.log10(n.ranges/50000.),n.shape)[source|tail]
    n.field_available['DBZH']=np.isfinite(n.fields['DBZH'])
    out,_=evaluate(n,source)
    assert out[P+'MODEL_AVAILABLE_MASK'][tail].all()
    assert not out[P+'QUALIFIED_MASK'][tail].any()
    assert (out[P+'HOLD_REASON'][tail]==32).all()


def test_serialized_model_recomputes_original_references_and_rejects_forgery():
    n,source,tail=fixture();out,_=evaluate(n,source)
    module=load('radial_revision.fan_joint');blocked=np.zeros(n.shape,bool)
    for key in ('SOURCE_ID','REFERENCE_SUPPORT_M','INTERCEPT_DB','RESIDUAL_DB'):
        bad={k:v.copy() for k,v in out.items()};bad[P+key][tail]+=10
        with pytest.raises(ValueError):module.validate(bad,n.field_available['DBZH'],blocked)


def test_engine_and_source_writer_keep_weak_quarantine_separate_from_source_claim():
    n,source,tail=fixture()
    for key,value in (('SNR',25.),('RHOHV',.99),('ZDR',.5),('PHIDP',20.)):
        n.fields[key]=np.where(source,value,np.nan).astype('float32')
        n.field_available[key]=source.copy()
    module=load('radial_revision.config')
    cfg=module.RadialRevisionConfig(step=3,mode='experiment_quarantine',fragment_line={
        'raw_fragment_families_enabled':True,'source_ledger_enabled':True,
        'raw_fan_families_enabled':True,'fan_joint_enabled':True})
    out,_=engine_evaluate(n,cfg,source)
    baseline,_=engine_evaluate(n,cfg.model_copy(update={'fragment_line':cfg.fragment_line.model_copy(update={'fan_joint_enabled':False})}),source)
    for key in ('RV2_MODEL_ID','RV2_SEGMENT_RESIDUAL_DB','RV2_SEGMENT_FOLD_ID'):
        assert np.array_equal(out[key],baseline[key],equal_nan=True)
    assert out[P+'QUALIFIED_MASK'][tail].all()
    assert out['RV2_ACTION_PROPOSAL_MASK'][tail].all()
    assert out['RV2_GEOMETRY_ACTION_MASK'][tail].all()
    assert not out['RV2_SEGMENT_MATCH_MASK'][tail].any()
    load('radial_revision.validation').validate_revision_fields(out,n.field_available['DBZH'],source,np.zeros(n.shape,bool))
    audit,_=engine_evaluate(n,cfg.model_copy(update={'mode':'audit'}),source)
    assert audit[P+'QUALIFIED_MASK'][tail].all() and not audit['RV2_ACTION_PROPOSAL_MASK'].any()
    config=load('config').SourceReviewConfig(narrow_enabled=False,radial_revision=cfg.model_dump())
    _,arrays,_=load('source').source_additions(n,config,source,np.zeros(n.shape,'float32'))
    arrays['SRC_REVIEW_REFERENCE_FOLD_ID']=source.astype('uint32')
    load('source_validation').validate_source_fields(arrays,n.field_available['DBZH'])
    assert arrays[P+'QUALIFIED_MASK'][tail].all()
    with pytest.raises(ValueError):module.FragmentLineConfig(fan_joint_enabled=True)
