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


def test_short_research_geometry_is_translation_invariant_and_not_action_authority():
    module=load('radial_revision.fragment_constellation')
    history=[dict(angular_width_deg=2.,bearing_deg=35.,bilateral_fraction=1.,
        observed_weather_gates=0,lower_parent_weather_gates=0,protected_gates=0,
        lower_parent_protected_gates=0) for _ in range(4)]
    ranges=300000.+np.concatenate([np.arange(i,i+6)*250 for i in (0,24,48,72)])
    result=module.short_segment_assessment(history,ranges,250,1,False)
    shifted=module.short_segment_assessment(history,ranges+1234,250,1,False)
    assert result==shifted and result['qualified']
    assert result['research_only'] and not result['action_authority']
    history[1]['bilateral_fraction']=.99
    assert not module.short_segment_assessment(history,ranges,250,1,False)['qualified']


def test_short_research_rejects_weather_parent_and_unstable_boundaries():
    module=load('radial_revision.fragment_constellation')
    history=[dict(angular_width_deg=2.+i*.3,bearing_deg=35.+i*.2,bilateral_fraction=1.,
        observed_weather_gates=0,lower_parent_weather_gates=0,protected_gates=0,
        lower_parent_protected_gates=0) for i in range(4)]
    history[0]['lower_parent_weather_gates']=1
    ranges=300000.+np.concatenate([np.arange(i,i+6)*250 for i in (0,24,48,72)])
    result=module.short_segment_assessment(history,ranges,250,1,True)
    assert not result['qualified']
    assert set(result['hold_reasons'])=={'weather_or_protected_original_member',
        'full_parent_geometry_hold','unstable_short_center','unstable_short_width'}


def test_physical_shoulder_windows_tolerate_local_contamination_but_not_unknowns():
    module=load('radial_revision.fragment_constellation')
    r=100000.+np.arange(100)*100
    known=np.ones(100,bool);quiet=known.copy();protected=np.zeros(100,bool)
    quiet[50]=False
    accepted,report=module.measured_shoulder_windows(r,100,np.array([50]),known,quiet,protected,lambda _:None)
    assert accepted.all() and report[0]['maximum_contamination_fraction']>0
    known[46:55]=False;quiet[46:55]=False
    accepted,report=module.measured_shoulder_windows(r,100,np.array([50]),known,quiet,protected,lambda _:None)
    assert not accepted.any() and report[0]['maximum_unknown_fraction']>.5


def test_physical_shoulder_windows_preserve_weather_barriers_and_range_edges():
    module=load('radial_revision.fragment_constellation')
    r=100000.+np.arange(100)*100
    known=np.ones(100,bool);quiet=known.copy();protected=np.zeros(100,bool)
    protected[50]=True
    accepted,_=module.measured_shoulder_windows(r,100,np.array([50]),known,quiet,protected,lambda _:None)
    assert not accepted.any()
    protected[:]=False
    accepted,report=module.measured_shoulder_windows(r,100,np.array([0]),known,quiet,protected,lambda _:None)
    assert not accepted.any() and all(x['incomplete_windows']==1 for x in report)


def test_window_research_does_not_fill_raw_or_expand_original_object():
    n=fixture();n.fields['DBZH'][3,103]=30;n.field_available['DBZH'][3,103]=True
    baseline,_=detect(n,segment_evidence=True)
    original=n.fields['DBZH'].copy()
    arrays,report=detect(n,segment_evidence=True,shoulder_windows=True)
    assert report['shoulder_windows'] and report['action_gates']==0
    assert not arrays[P+'STRONG_MASK'][~n.field_available['DBZH']].any()
    assert np.array_equal(arrays[P+'MASK'],baseline[P+'MASK'])
    assert np.array_equal(n.fields['DBZH'],original,equal_nan=True)


def test_nonquiet_measured_snr_is_not_reported_as_unknown_or_quiet():
    n=fixture();n.fields['SNR'][3,:]=8
    arrays,report=detect(n,segment_evidence=True,shoulder_windows=True)
    assert not arrays[P+'STRONG_MASK'].any()
    side=report['objects'][0]['side_observations'][0][0]
    assert side['unknown_fraction']==0 and side['measured_nonquiet_snr_fraction']==1
    assert side['distance_windows'][0]['minimum_known_fraction']==1
    assert side['distance_windows'][0]['minimum_quiet_fraction']==0


def test_bounded_band_never_skips_a_weather_ray_or_crosses_native_gap():
    n=fixture();n.fields['RHOHV']=np.full(n.shape,np.nan,'float32')
    n.field_available['RHOHV']=np.zeros(n.shape,bool)
    n.fields['DBZH'][2,100:108]=15;n.field_available['DBZH'][2,100:108]=True
    n.fields['RHOHV'][2,100:108]=.99;n.field_available['RHOHV'][2,100:108]=True
    n.fields['SNR'][2,100:108]=20
    arrays,report=detect(n,shoulder_windows=True,shoulder_band=True)
    assert not arrays[P+'STRONG_MASK'].any()
    assert any(w['protected_windows'] for obj in report['objects']
        for pair in obj['side_observations'] for side in pair for w in side.get('distance_windows',[]))
    n=fixture();n.gap_after[2]=True
    arrays,report=detect(n,shoulder_windows=True,shoulder_band=True)
    assert not arrays[P+'STRONG_MASK'].any()
    assert any(side.get('angular_band_incomplete') for obj in report['objects'] for pair in obj['side_observations'] for side in pair)


def test_bounded_band_uses_all_fixed_exterior_rays_without_growing_candidates():
    n=fixture()
    baseline,_=detect(n)
    arrays,report=detect(n,shoulder_windows=True,shoulder_band=True)
    assert np.array_equal(arrays[P+'MASK'],baseline[P+'MASK'])
    assert np.array_equal(arrays[P+'STRONG_MASK'],baseline[P+'STRONG_MASK'])
    assert all(len(side['angular_band_rows'])==2 and side['angular_band_max_offset_deg']<=2
        for obj in report['objects'] for pair in obj['side_observations'] for side in pair)


def test_original_distance_partitions_preserve_shared_lower_weather_bridge():
    module=load('radial_revision.fragment_constellation')
    ranges=np.arange(1000.,201000.,1000.)
    group=[dict(ident=i+1,cols=np.array([c])) for i,c in enumerate((0,1,150,151))]
    history=[dict(range_min_m=ranges[f['cols'][0]],range_max_m=ranges[f['cols'][0]]+1000,
        lower_parent_ids=[1 if i<2 else 2],bearing_deg=30.,angular_width_deg=1.,
        bilateral_fraction=1.,observed_weather_gates=0,lower_parent_weather_gates=0,
        protected_gates=0,lower_parent_protected_gates=0) for i,f in enumerate(group)]
    parents=[dict(component_id=i,range_min_m=lo,range_max_m=hi,mean_range_m=(lo+hi)/2,
        angular_width_deg=1.) for i,lo,hi in ((1,1000.,3000.),(2,151000.,153000.))]
    separate=module.original_distance_partitions(group,history,parents,ranges,1000,1)
    assert [p['original_components'] for p in separate]==[[1,2],[3,4]]
    assert all(not p['action_authority'] and p['gap_is_not_dry_evidence'] for p in separate)
    # The same complete lower contour spans both sets: splitting is forbidden,
    # even if the higher contour has a very long empty-looking interval.
    parents[0].update(range_max_m=153000.,angular_width_deg=8.)
    for member in history:member['lower_parent_ids']=[1]
    joined=module.original_distance_partitions(group,history,parents[:1],ranges,1000,1)
    assert len(joined)==1 and joined[0]['original_lower_parent_geometry_hold']
    assert not joined[0]['assessment']['qualified']


def test_distance_partition_evidence_cannot_change_masks_or_weather_authority():
    n=fixture()
    legacy,base=detect(n,segment_evidence=True)
    evidence,report=detect(n,segment_evidence=True,partition_evidence=True)
    for field in legacy:assert np.array_equal(legacy[field],evidence[field])
    assert all('original_distance_partitions' in record for record in report['objects'])
    assert all('original_distance_partitions' not in record for record in base['objects'])


def test_short_center_uses_original_edges_not_uneven_gate_population():
    module=load('radial_revision.fragment_constellation')
    history=[dict(angular_width_deg=2.,bearing_deg=35.+i*.2,
        original_left_deg=34.,original_right_deg=36.,bilateral_fraction=1.,
        observed_weather_gates=0,lower_parent_weather_gates=0,protected_gates=0,
        lower_parent_protected_gates=0) for i in range(4)]
    ranges=300000.+np.concatenate([np.arange(i,i+6)*250 for i in (0,24,48,72)])
    result=module.short_segment_assessment(history,ranges,250,1,False)
    assert result['qualified'] and result['center_drift_deg']==0
    assert result['center_basis']=='complete_original_edges'
    # Actual boundaries translate; a constant population centroid cannot hide it.
    for i,member in enumerate(history):
        member.update(bearing_deg=35.,original_left_deg=34.+i*.2,original_right_deg=36.+i*.2)
    result=module.short_segment_assessment(history,ranges,250,1,False)
    assert not result['qualified'] and 'unstable_short_center' in result['hold_reasons']


def test_short_partial_or_invalid_original_edge_history_is_rejected():
    module=load('radial_revision.fragment_constellation')
    history=[dict(angular_width_deg=2.,bearing_deg=35.,bilateral_fraction=1.,
        observed_weather_gates=0,lower_parent_weather_gates=0,protected_gates=0,
        lower_parent_protected_gates=0) for _ in range(4)]
    ranges=300000.+np.arange(80)*250
    history[0]['original_left_deg']=34.
    with pytest.raises(ValueError,match='complete finite original edges'):
        module.short_segment_assessment(history,ranges,250,1,False)


def test_shoulder_diagnostics_distinguish_observed_nonquiet_unknown_and_protected():
    module=load('radial_revision.fragment_constellation')
    z=np.array([[25.,25.,25.,25.,25.],[20.,np.nan,np.nan,np.nan,np.nan]])
    observed=np.isfinite(z);snr=np.array([[20.]*5,[20.,4.,np.nan,0.,0.]])
    available=np.isfinite(snr);barred=np.zeros(z.shape,bool);barred[1,3]=True
    result=module.shoulder_failure_samples(1,np.zeros(5,dtype=int),np.arange(5),
        np.arange(2),np.arange(5)*250.,z,observed,snr,available,barred,limit=3)
    assert result['failed_gate_count']==4 and result['samples_truncated']
    assert [s['observation_state'] for s in result['samples']]==[
        'observed_dbzh','measured_nonquiet_snr','unknown']
    assert result['samples'][1]['opposing_dbzh'] is None and result['samples'][1]['opposing_snr']==4.
    assert result['samples'][2]['opposing_snr'] is None
    assert not result['action_authority']


def test_short_measured_subset_retains_complete_geometry_and_weather_members():
    module=load('radial_revision.fragment_constellation')
    ranges=300000.+np.arange(100)*250
    columns=[np.arange(i,i+6) for i in (0,24,48,72)]
    group=[dict(ident=i+1,cols=c) for i,c in enumerate(columns)]
    parents=[dict(component_id=i+1,range_min_m=ranges[c[0]],range_max_m=ranges[c[-1]]+250,
        mean_range_m=float(ranges[c].mean()),angular_width_deg=2.) for i,c in enumerate(columns)]
    history=[dict(range_min_m=p['range_min_m'],range_max_m=p['range_max_m'],lower_parent_ids=[p['component_id']],
        angular_width_deg=2.,bearing_deg=35.,original_left_deg=34.,original_right_deg=36.,
        bilateral_fraction=1.,observed_weather_gates=0,lower_parent_weather_gates=0,
        protected_gates=0,lower_parent_protected_gates=0) for p in parents]
    accepted=[np.ones(len(c),bool) for c in columns];accepted[1][0]=False
    history[1]['bilateral_fraction']=5/6
    part=module.original_distance_partitions(group,history,parents,ranges,250,1,measured_accept=accepted)[0]
    assert not part['assessment']['qualified']
    assert part['measured_subset']['qualified'] and not part['measured_subset']['action_authority']
    assert part['original_components']==[1,2,3,4] and part['measured_subset']['measured_range_support_m']==5750
    # An original weather member cannot be dropped and have its evidence borrowed.
    history[1]['lower_parent_weather_gates']=1;accepted[1][:]=False
    part=module.original_distance_partitions(group,history,parents,ranges,250,1,measured_accept=accepted)[0]
    assert not part['measured_subset']['qualified']
    assert 'weather_or_protected_original_member' in part['measured_subset']['hold_reasons']


def test_short_subset_requires_window_proof_and_keeps_production_arrays_identical():
    n=fixture()
    with pytest.raises(ValueError,match='requires complete partition'):
        detect(n,short_subset_evidence=True)
    base,_=detect(n,partition_evidence=True,shoulder_windows=True)
    proposal,_=detect(n,partition_evidence=True,shoulder_windows=True,short_subset_evidence=True)
    for key in base:assert np.array_equal(base[key],proposal[key])
    assert P+'SHORT_RESEARCH_MASK' in proposal


def test_weak_parent_target_requires_own_contrast_and_measured_windows():
    module=load('radial_revision.fragment_constellation')
    shape=(5,100);z=np.full(shape,np.nan);z[2,20:60]=25.;z[2,30]=10.
    z[1,:]=5.;z[3,:]=5.
    observed=np.isfinite(z);snr=np.zeros(shape);sa=np.ones(shape,bool)
    barred=np.zeros(shape,bool);weather=barred.copy();fc=np.arange(20,60);fr=np.full(len(fc),2)
    original=z.copy()
    good,detail=module.measured_parent_footprint(fr,fc,np.arange(5),np.arange(100)*250.,
        z,observed,snr,sa,barred,weather,250,lambda _:None)
    assert good.sum()==39 and not good[10]
    assert detail['independent_weak_target_contrast']
    assert np.array_equal(z,original,equal_nan=True)
    # DBZH and SNR both unavailable: never infer quiet/dry support.
    observed[1,:]=False;sa[1,:]=False
    good,_=module.measured_parent_footprint(fr,fc,np.arange(5),np.arange(100)*250.,
        z,observed,snr,sa,barred,weather,250,lambda _:None)
    assert not good.any()


def test_parent_footprint_is_optin_and_cannot_change_existing_arrays():
    n=fixture()
    with pytest.raises(ValueError,match='requires original short subset'):
        detect(n,short_parent_footprint=True)
    baseline,_=detect(n,partition_evidence=True,shoulder_windows=True,short_subset_evidence=True)
    extended,_=detect(n,partition_evidence=True,shoulder_windows=True,short_subset_evidence=True,
        short_parent_footprint=True)
    for key in baseline:assert np.array_equal(baseline[key],extended[key])
    assert P+'SHORT_PARENT_RESEARCH_MASK' in extended


def test_parent_extension_uses_only_frozen_original_parent_ids_without_recursive_growth():
    from scipy.ndimage import label
    n=fixture();n.fields['DBZH'][:]=np.nan;n.field_available['DBZH'][:]=False
    n.fields['RHOHV']=np.full(n.shape,np.nan);n.field_available['RHOHV']=np.zeros(n.shape,bool)
    for start in (0,160,800,824,848,872):
        n.fields['DBZH'][4:7,start:start+10]=15
        n.fields['DBZH'][4:7,start:start+6]=25
        n.field_available['DBZH'][4:7,start:start+10]=True
        if start<800:
            n.fields['RHOHV'][4:7,start:start+10]=.99
            n.field_available['RHOHV'][4:7,start:start+10]=True
            n.fields['SNR'][4:7,start:start+10]=20
    # Original orphan in another direction is not authorized by new proposals.
    n.fields['DBZH'][9:12,850:860]=15;n.field_available['DBZH'][9:12,850:860]=True
    arrays,report=detect(n,partition_evidence=True,shoulder_windows=True,
        short_subset_evidence=True,short_parent_footprint=True)
    mask=arrays[P+'SHORT_PARENT_RESEARCH_MASK']==1
    assert mask.any() and not mask[9:12,850:860].any()
    assert not mask[:,0:170].any()
    labels,_=label(n.field_available['DBZH']&(n.fields['DBZH']>=10),np.ones((3,3)))
    ids={p['original_lower_parent_id'] for o in report['objects']
         for part in o['original_distance_partitions'] for p in part.get('frozen_parent_footprints',[])}
    assert not (mask&~np.isin(labels,list(ids))).any()
    assert not report['recursive_growth'] and report['action_gates']==0
