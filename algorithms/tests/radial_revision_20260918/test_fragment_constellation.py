import types
import numpy as np
import pytest
from .conftest import load

P = 'RV2_CONSTELLATION_'


def fixture():
    shape = (12, 1200)
    z = np.full(shape, np.nan, 'float32'); available = np.zeros(shape, bool)
    for start in (100, 250, 400, 550, 700):
        z[4:7, start:start+8] = 25; available[4:7, start:start+8] = True
    return types.SimpleNamespace(shape=shape, ranges=100000.+np.arange(shape[1])*250,
        azimuth=np.arange(shape[0], dtype=float)+30, geometry_good=np.ones(shape[0], bool),
        gap_after=np.zeros(shape[0], bool), fields={'DBZH':z, 'SNR':np.zeros(shape,'float32')},
        field_available={'DBZH':available, 'SNR':np.ones(shape,bool)})


def detect(native, blocked=None, **options):
    return load('radial_revision.fragment_constellation').detect(native,
        np.zeros(native.shape,bool) if blocked is None else blocked, **options)


def test_original_transverse_fragments_group_without_residual_selection_or_fill():
    n = fixture(); original = n.fields['DBZH'].copy()
    arrays, report = detect(n)
    assert arrays[P+'STRONG_MASK'].sum() == n.field_available['DBZH'].sum()
    assert not arrays[P+'MASK'][~n.field_available['DBZH']].any()
    assert all(obj['actual_range_support_m'] == 10000 for obj in report['objects'])
    assert not report['recursive_growth'] and report['action_gates'] == 0
    assert np.array_equal(original, n.fields['DBZH'], equal_nan=True)
    n.azimuth = (n.azimuth+317) % 360
    rotated, _ = detect(n)
    assert np.array_equal(rotated[P+'STRONG_MASK'], arrays[P+'STRONG_MASK'])


def test_unknown_sides_never_authorize_fragment_group():
    n = fixture(); n.field_available['SNR'][:] = False
    arrays, report = detect(n)
    assert arrays[P+'MASK'].any() and not arrays[P+'STRONG_MASK'].any()
    assert all('incomplete_measured_bilateral_boundaries' in obj['hold_reasons'] for obj in report['objects'])


def test_original_weather_member_and_barrier_cannot_be_dropped_to_restart_group():
    n = fixture(); n.fields['RHOHV'] = np.full(n.shape,.99,'float32')
    n.field_available['RHOHV'] = n.field_available['DBZH'].copy()
    n.fields['SNR'][4:7,100:108] = 20
    arrays, _ = detect(n); assert not arrays[P+'STRONG_MASK'].any()
    n = fixture(); barrier = np.zeros(n.shape,bool); barrier[5,101] = True
    arrays, _ = detect(n,barrier); assert not arrays[P+'STRONG_MASK'].any()


def test_gap_and_curved_fragment_chain_do_not_become_fixed_object():
    n = fixture(); n.gap_after[4] = True
    arrays, _ = detect(n); assert not arrays[P+'STRONG_MASK'].any()
    n = fixture(); n.fields['DBZH'][:] = np.nan; n.field_available['DBZH'][:] = False
    for i,start in enumerate((100,250,400,550,700)):
        n.fields['DBZH'][i:i+3,start:start+8] = 25
        n.field_available['DBZH'][i:i+3,start:start+8] = True
    arrays, _ = detect(n); assert not arrays[P+'MASK'].any()


def test_budget_and_forged_proof_rejected():
    n = fixture(); module = load('radial_revision.fragment_constellation')
    with pytest.raises(load('radial_revision.geometry').ResourceLimit):
        detect(n, maximum_objects=1)
    arrays, _ = detect(n); arrays[P+'STRONG_MASK'][0,0] = 1
    with pytest.raises(ValueError,match='proof mismatch'):
        module.validate(arrays,n,np.zeros(n.shape,bool))


def test_optional_segments_qualify_independently_without_weather_member_authority():
    n = fixture(); barrier = np.zeros(n.shape,bool); barrier[5,101] = True
    baseline, _ = detect(n,barrier)
    arrays, report = detect(n,barrier,segment_evidence=True)
    assert not baseline[P+'STRONG_MASK'].any()
    assert not arrays[P+'STRONG_MASK'][:,100:108].any()
    assert arrays[P+'STRONG_MASK'].sum() == 4*3*8
    assert all(len(obj['member_history']) == 5 for obj in report['objects'])
    # Two failed original members leave only three; their evidence is not borrowed.
    barrier[5,251] = True
    arrays, _ = detect(n,barrier,segment_evidence=True)
    assert not arrays[P+'STRONG_MASK'].any()


def test_full_original_fixed_km_width_history_cannot_be_evaded_by_segmentation():
    n = fixture(); n.fields['DBZH'][:] = np.nan; n.field_available['DBZH'][:] = False
    # Resolved transverse shards narrow in angle with distance like a weather ribbon.
    for start,width in zip((0,200,400,600,800,1000),(4,3,3,2,2,1),strict=True):
        n.fields['DBZH'][5-width:6+width,start:start+8] = 25
        n.field_available['DBZH'][5-width:6+width,start:start+8] = True
    arrays, report = detect(n,beam_width=2,segment_evidence=True)
    assert report['objects']
    assert any(obj['full_parent_narrowing_weather_hold'] for obj in report['objects'])
    assert not arrays[P+'STRONG_MASK'].any()


def native_fixture():
    from .conftest import Native
    measured=fixture()
    native=Native(measured.fields['DBZH'],dr=250,start=100000,
        fields={'SNR':measured.fields['SNR']})
    native.azimuth=measured.azimuth.copy()
    return native


def test_high_intensity_core_cannot_escape_lower_contour_weather_parent():
    n=fixture();n.fields['DBZH'][:]=np.nan;n.field_available['DBZH'][:]=False
    for start,width in zip((0,200,400,600,800,1000),(4,3,3,2,2,1),strict=True):
        n.fields['DBZH'][5-width:6+width,start:start+8]=15
        n.fields['DBZH'][4:7,start:start+8]=45
        n.field_available['DBZH'][5-width:6+width,start:start+8]=True
    arrays,report=detect(n,beam_width=2,segment_evidence=True)
    assert any(obj['contour_dbz']==35 for obj in report['objects'])
    assert not arrays[P+'STRONG_MASK'].any()


def test_measured_lower_halo_weather_protects_polarization_missing_core():
    n=fixture();n.fields['RHOHV']=np.full(n.shape,np.nan,'float32')
    n.field_available['RHOHV']=np.zeros(n.shape,bool)
    for start in (100,250,400,550,700):
        n.fields['DBZH'][4:7,start:start+8]=45
        for row in (3,7):
            n.fields['DBZH'][row,start:start+8]=15;n.field_available['DBZH'][row,start:start+8]=True
            n.fields['RHOHV'][row,start:start+8]=.99;n.field_available['RHOHV'][row,start:start+8]=True
            n.fields['SNR'][row,start:start+8]=20
    arrays,report=detect(n,beam_width=2,segment_evidence=True)
    assert any(obj['contour_dbz']==35 for obj in report['objects'])
    assert not arrays[P+'STRONG_MASK'].any()


def test_engine_opt_in_strong_only_and_original_sources_preserved():
    from .conftest import evaluate
    n=native_fixture()
    cfg=load('radial_revision.config').RadialRevisionConfig(step=3,
        mode='experiment_quarantine',fragment_line={'fragment_constellation_enabled':True,
        'source_ledger_enabled':True,'raw_fragment_families_enabled':True})
    arrays,_=evaluate(n,cfg);hit=arrays[P+'STRONG_MASK']==1
    assert hit.any() and arrays['RV2_GEOMETRY_ACTION_MASK'][hit].all()
    assert arrays['RV2_ACTION_PROPOSAL_MASK'][hit].all()
    baseline,_=evaluate(n,cfg.model_copy(update={'fragment_line':cfg.fragment_line.model_copy(
        update={'fragment_constellation_enabled':False})}))
    assert P+'STRONG_MASK' not in baseline
    for key in ('RV2_SOURCE_LEDGER_SEED_ID','RV2_SOURCE_LEDGER_KIND'):
        assert np.array_equal(arrays[key],baseline[key])
    audit,_=evaluate(n,cfg.model_copy(update={'mode':'audit'}))
    assert not audit['RV2_ACTION_PROPOSAL_MASK'].any()
    load('radial_revision.validation').validate_revision_fields(arrays,n.field_available['DBZH'],
        np.zeros(n.shape,bool),np.zeros(n.shape,bool))


def test_writer_native_order_raw_and_segment_contract_bound():
    n=native_fixture();source=np.zeros(n.shape,bool)
    cfg=load('config').SourceReviewConfig(narrow_enabled=False,radial_revision={
        'step':3,'mode':'experiment_quarantine','fragment_line':{'fragment_constellation_enabled':True}})
    _,arrays,_=load('source').source_additions(n,cfg,source,np.zeros(n.shape,'float32'))
    arrays['SRC_REVIEW_REFERENCE_FOLD_ID']=np.zeros(n.shape,'uint32')
    arrays['DBZH_RAW']=n.fields['DBZH'].copy()
    validator=load('source_validation').validate_source_fields
    validator(arrays,n.field_available['DBZH'])
    order=np.random.default_rng(29).permutation(n.shape[0])
    restored={key:value[order].copy() for key,value in arrays.items()}
    validator(restored,n.field_available['DBZH'][order])
    for key,index in ((P+'STRONG_MASK',(0,0)),(P+'SEGMENT_MODE',(0,0)),
                      (P+'MEASURED_DBZH',(5,100))):
        forged={k:v.copy() for k,v in arrays.items()}
        forged[key][index]=0 if key.endswith('MODE') else forged[key][index]+1
        with pytest.raises(ValueError):validator(forged,n.field_available['DBZH'])
