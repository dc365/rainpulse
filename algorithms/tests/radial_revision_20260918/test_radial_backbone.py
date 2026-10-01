"""Frozen backbone positives and geometry/measurement counterexamples."""
import numpy as np
import pytest
from .conftest import load
from .test_variable_morphology import fixture, nested_branch

P='RV2_BACKBONE_'


def detect(n,blocked=None,**kwargs):
    return load('radial_revision.radial_backbone').detect(n,
        np.zeros(n.shape,bool) if blocked is None else blocked,**kwargs)


def test_fixed_raw_core_recovers_observed_fringe_without_recursive_growth():
    n=nested_branch();raw=n.fields['DBZH'].copy()
    arrays,report=detect(n)
    use=n.field_available['DBZH'][20]
    assert arrays[P+'STRONG_MASK'][20,use].all()
    assert arrays[P+'STRONG_MASK'][19,use].all()
    assert not arrays[P+'STRONG_MASK'][0].any()
    assert not arrays[P+'MASK'][~n.field_available['DBZH']].any()
    assert np.array_equal(raw,n.fields['DBZH'],equal_nan=True)
    assert not report['source_claim'] and not report['recursive_growth'] and report['action_gates']==0
    n.azimuth=(n.azimuth+97)%360
    rotated,_=detect(n)
    assert np.array_equal(arrays[P+'STRONG_MASK'],rotated[P+'STRONG_MASK'])


@pytest.mark.parametrize('kind',['constant_km','curved'])
def test_complete_parent_history_retains_weather_ribbons(kind):
    arrays,_=detect(fixture(kind))
    assert not arrays[P+'STRONG_MASK'].any()


def test_unknown_side_and_actual_weather_do_not_become_quiet():
    n=nested_branch();n.fields['SNR'][:]=np.nan;n.field_available['SNR'][:]=False
    n.fields['DBZH'][18]=np.nan;n.field_available['DBZH'][18]=False
    arrays,_=detect(n);assert not arrays[P+'STRONG_MASK'][20].any()
    n=nested_branch();n.fields['RHOHV']=np.full(n.shape,.99,'float32')
    n.field_available['RHOHV']=n.field_available['DBZH'].copy()
    n.fields['SNR'][n.field_available['DBZH']]=20
    arrays,_=detect(n);assert not arrays[P+'STRONG_MASK'].any()


def test_hard_barrier_splits_actions_but_preserves_original_history():
    n=nested_branch();blocked=np.zeros(n.shape,bool)
    barrier=(n.ranges>=210000)&(n.ranges<220000);blocked[:,barrier]=True
    arrays,report=detect(n,blocked)
    assert not arrays[P+'STRONG_MASK'][:,barrier].any()
    assert arrays[P+'STRONG_MASK'][20,n.ranges<210000].any()
    assert arrays[P+'STRONG_MASK'][20,n.ranges>=220000].any()
    strong=[o for o in report['objects'] if o['strong']]
    assert strong and all(o['start_m']==80000 and o['end_m']==400000 for o in strong)


def test_gap_and_budget_cannot_produce_partial_qualification():
    n=nested_branch();n.gap_after[20]=True
    arrays,_=detect(n);assert not arrays[P+'STRONG_MASK'][20].any()
    with pytest.raises(load('radial_revision.geometry').ResourceLimit):
        detect(nested_branch(),maximum_objects=1)


def test_serialized_proof_is_bound_to_raw_measurements():
    n=nested_branch();blocked=np.zeros(n.shape,bool)
    module=load('radial_revision.radial_backbone')
    arrays,_=module.detect(n,blocked);module.validate(arrays,n,blocked)
    arrays[P+'STRONG_MASK'][0,0]=1
    with pytest.raises(ValueError,match='proof differs'):
        module.validate(arrays,n,blocked)


def test_isolated_protected_column_does_not_invalidate_other_columns():
    n=nested_branch();blocked=np.zeros(n.shape,bool)
    column=int(np.flatnonzero(n.ranges==180000)[0]);blocked[20,column]=True
    arrays,_=detect(n,blocked)
    assert not arrays[P+'STRONG_MASK'][:,column].any()
    assert arrays[P+'STRONG_MASK'][20,column-1]==1
    assert arrays[P+'STRONG_MASK'][20,column+1]==1


def test_high_level_core_cannot_escape_lower_level_weather_width_history():
    n=fixture('constant_km')
    centre=int(np.argmin(np.abs(n.azimuth-180)))
    n.fields['DBZH'][centre,n.field_available['DBZH'][centre]]=35
    arrays,report=detect(n)
    assert not arrays[P+'STRONG_MASK'].any()
    assert any(o['level_dbz']==35 and 'narrowing_physical_width_weather_counterexample' in o['holds']
               for o in report['objects'])


def test_measured_weather_core_does_not_authorize_unmeasured_weak_fringe():
    n=nested_branch();core=n.field_available['DBZH'][20]
    n.fields['RHOHV']=np.full(n.shape,np.nan,'float32')
    n.fields['RHOHV'][20,core]=.99
    n.field_available['RHOHV']=np.isfinite(n.fields['RHOHV'])
    n.fields['SNR'][20,core]=20
    arrays,_=detect(n)
    assert not arrays[P+'STRONG_MASK'][19:22].any()


def test_engine_opt_in_routes_only_strong_shape_and_preserves_original_sources():
    from .conftest import evaluate
    n=nested_branch();cfg=load('radial_revision.config').RadialRevisionConfig(step=3,
        mode='experiment_quarantine',fragment_line={'radial_backbone_enabled':True,
        'source_ledger_enabled':True,'raw_fragment_families_enabled':True})
    arrays,report=evaluate(n,cfg);hit=arrays[P+'STRONG_MASK']==1
    assert hit.any() and arrays['RV2_GEOMETRY_ACTION_MASK'][hit].all()
    assert arrays['RV2_ACTION_PROPOSAL_MASK'][hit].all()
    baseline,_=evaluate(n,cfg.model_copy(update={'fragment_line':cfg.fragment_line.model_copy(
        update={'radial_backbone_enabled':False})}))
    for key in ('RV2_SOURCE_LEDGER_SEED_ID','RV2_SOURCE_LEDGER_KIND'):
        assert np.array_equal(arrays[key],baseline[key])
    audit,_=evaluate(n,cfg.model_copy(update={'mode':'audit'}))
    assert np.array_equal(audit[P+'STRONG_MASK'],arrays[P+'STRONG_MASK'])
    assert not audit['RV2_ACTION_PROPOSAL_MASK'].any()
    assert not report['fragment_line']['radial_backbone']['source_claim']
    load('radial_revision.validation').validate_revision_fields(
        arrays,n.field_available['DBZH'],np.zeros(n.shape,bool),np.zeros(n.shape,bool))


def test_writer_replays_backbone_native_order_and_rejects_forged_raw_or_actions():
    n=nested_branch();source=np.zeros(n.shape,bool)
    cfg=load('config').SourceReviewConfig(narrow_enabled=False,radial_revision={
        'step':3,'mode':'experiment_quarantine','fragment_line':{'radial_backbone_enabled':True}})
    _,arrays,_=load('source').source_additions(n,cfg,source,np.zeros(n.shape,'float32'))
    arrays['SRC_REVIEW_REFERENCE_FOLD_ID']=np.zeros(n.shape,'uint32')
    arrays['DBZH_RAW']=n.fields['DBZH'].copy()
    validator=load('source_validation').validate_source_fields
    validator(arrays,n.field_available['DBZH'])
    order=np.random.default_rng(19).permutation(n.shape[0])
    restored={k:v[order].copy() for k,v in arrays.items()}
    validator(restored,n.field_available['DBZH'][order])
    forged={k:v.copy() for k,v in arrays.items()};forged[P+'STRONG_MASK'][0,0]=1
    with pytest.raises(ValueError):validator(forged,n.field_available['DBZH'])
    forged={k:v.copy() for k,v in arrays.items()};forged[P+'MEASURED_DBZH'][20,180]+=1
    with pytest.raises(ValueError,match='RAW'):validator(forged,n.field_available['DBZH'])
