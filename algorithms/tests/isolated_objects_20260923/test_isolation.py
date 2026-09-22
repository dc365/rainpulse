from dataclasses import replace
import copy
import numpy as np
import pytest
from iso_helpers import scene,config,base,ev,change
from volume_review.data import Sweep
from volume_review.clutter_fusion.engine import evaluate_volume
from volume_review.clutter_fusion.config import ClutterFusionConfig
from volume_review.clutter_fusion.isolation_config import IsolationConfig
from volume_review.clutter_fusion.isolation_geometry import inspect
from volume_review.clutter_fusion.isolated_objects import decision,validate
from volume_review.clutter_fusion.disposition import apply
from volume_review.clutter_fusion.validation import validate_serialized
from volume_review.clutter_fusion.integration import attributes


def test_real_no_echo_support_not_unknown():
    c=config();known=ev(scene(known=True),c);unknown=ev(scene(known=False),c)
    assert known.arrays['CF_ISO_CANDIDATE_MASK'].sum()==12
    assert unknown.arrays['CF_ISO_CANDIDATE_MASK'].sum()==0
    assert not unknown.arrays['CF_ISO_RING_AVAILABLE_MASK'].any()
    assert np.nanmax(unknown.arrays['CF_ISO_POSSIBLE_ECHO_FRACTION'])==1


def test_raw_unchanged_and_no_filling():
    s=scene();digest=s.digest;e=ev(s,config())
    assert s.digest==digest
    for k,v in e.arrays.items():
        if k.startswith('CF_ISO_') and k.endswith('_MASK'):
            assert not ((v==1)&~s.observed).any(), k


@pytest.mark.parametrize('kind',['uniform','long'])
def test_large_or_long_original_object_cannot_shrink_into_candidate(kind):
    s=scene(kind);e=ev(s,config())
    assert not e.arrays['CF_ISO_SMALL_MASK'].any()
    assert not e.arrays['CF_ISO_CANDIDATE_MASK'].any()


@pytest.mark.parametrize('field',['CF_HARD_WEATHER_MASK','CF_LOCAL_WEATHER_MASK','CF_LEGACY_PROTECTED_MASK',
    'CF_WEATHER_PROXY_MASK','CF_BG_ENHANCEMENT_MASK','CF_MIXED_MASK'])
def test_one_weather_member_protects_whole_raw_object(field):
    s=scene();c=config();a=ev(s,c.model_copy(update={'isolated_objects':None})).arrays
    ix=np.transpose(np.nonzero(s.observed))[0];a[field][tuple(ix)]=1
    g=inspect(s,c,a)
    assert np.all(g.arrays['CF_ISO_OBJECT_PROTECTED_MASK'][s.observed]==1)
    assert not g.arrays['CF_ISO_ISOLATED_MASK'].any()


def test_connected_strong_gate_protects_weak_object():
    s=scene();ix=tuple(np.transpose(np.nonzero(s.observed))[0]);s=change(s,'DBZH',ix,55.)
    e=ev(s,config())
    assert np.all(e.arrays['CF_ISO_OBJECT_PROTECTED_MASK'][s.observed]==1)
    assert not e.arrays['CF_ISO_CANDIDATE_MASK'].any()


def test_isolated_strong_gate_is_not_removed():
    e=ev(scene(zvalue=60.),config('quarantine'))
    assert not e.arrays['CF_ISO_CANDIDATE_MASK'].any()


def test_nearby_tiny_protected_weather_guard():
    s=scene();fields={k:v.copy() for k,v in s.fields.items()}
    target=(85,int(20000/s.dr));fields['DBZH'][target]=55.
    fields['RHOHV'][target]=.99;fields['SNR'][target]=25.
    fields['ZDR'][target]=.5;fields['PHIDP'][target]=5.
    s=replace(s,fields=fields,available={k:np.isfinite(v) for k,v in fields.items()},
              no_echo=s.no_echo&~np.isfinite(fields['DBZH']))
    e=ev(s,config());assert not e.arrays['CF_ISO_CANDIDATE_MASK'].any()
    assert e.arrays['CF_ISO_WEATHER_NEARBY_MASK'].any()


@pytest.mark.parametrize('key',['SNR','RHOHV'])
def test_target_needs_own_measurement(key):
    s=scene();s=replace(s,fields={k:v for k,v in s.fields.items() if k!=key},
                        available={k:v for k,v in s.available.items() if k!=key})
    e=ev(s,config());assert e.arrays['CF_ISO_ISOLATED_MASK'].sum()==12
    assert not e.arrays['CF_ISO_CANDIDATE_MASK'].any()


def test_geometry_alone_cannot_remove_weak_rain():
    e=ev(scene(rho=.99),config())
    assert not e.arrays['CF_ISO_CANDIDATE_MASK'].any()


def test_missing_member_is_not_promoted_by_neighbours():
    s=scene();ix=tuple(np.transpose(np.nonzero(s.observed))[0]);s=change(s,'RHOHV',ix,np.nan)
    e=ev(s,config());assert e.arrays['CF_ISO_CANDIDATE_MASK'].sum()==11
    assert e.arrays['CF_ISO_CANDIDATE_MASK'][ix]==0


def test_object_evidence_fraction_is_required():
    s=scene();rr,gg=np.nonzero(s.observed);fields={k:v.copy() for k,v in s.fields.items()}
    fields['RHOHV'][rr[:7],gg[:7]]=np.nan
    s=replace(s,fields=fields,available={k:np.isfinite(v) for k,v in fields.items()})
    e=ev(s,config());assert not e.arrays['CF_ISO_CANDIDATE_MASK'].any()


def test_no_recursive_holes_from_parent_rejections():
    s=scene('long');c=config();a=ev(s,c.model_copy(update={'isolated_objects':None})).arrays
    a['QC_REJECT_MASK']=np.ones(s.shape,'uint8');a['REFLECTIVITY_ELIGIBLE_FOR_CR']=np.zeros(s.shape,'uint8')
    g=inspect(s,c,a);assert not g.arrays['CF_ISO_ISOLATED_MASK'].any()


def test_internal_gap_breaks_adjacency_and_abstains():
    s=scene();g=s.gap_after.copy();g[89]=True;s=replace(s,gap_after=g)
    e=ev(s,config());assert not e.arrays['CF_ISO_CANDIDATE_MASK'].any()
    assert e.arrays['CF_ISO_GEOMETRY_LIMITED_MASK'].any()


def test_true_seam_unions_one_object():
    s=scene();s=replace(s,azimuth=(s.azimuth+270.)%360)
    e=ev(s,config());assert len(np.unique(e.arrays['CF_ISO_OBJECT_ID'][s.observed]))==1
    assert e.arrays['CF_ISO_CANDIDATE_MASK'].sum()==12


@pytest.mark.parametrize('shift',[0.,30.,90.,179.])
def test_rotation_preserves_fully_measured_decision(shift):
    s=scene();s=replace(s,azimuth=(s.azimuth+shift)%360)
    e=ev(s,config());assert e.arrays['CF_ISO_CANDIDATE_MASK'].sum()==12


def test_rotated_row_order_is_equivalent():
    s=scene();order=np.roll(np.arange(s.shape[0]),17)
    t=replace(s,azimuth=s.azimuth[order],elevation=s.elevation[order],good=s.good[order],gap_after=s.gap_after[order],
        fields={k:v[order] for k,v in s.fields.items()},available={k:v[order] for k,v in s.available.items()},
        ray_time_s=s.ray_time_s[order],no_echo=s.no_echo[order])
    a,b=ev(s,config()).arrays,ev(t,config()).arrays
    for key in ('CF_ISO_CANDIDATE_MASK','CF_ISO_OBJECT_ID','CF_ISO_AREA_KM2'):
        assert np.array_equal(a[key][order],b[key],equal_nan=True)


@pytest.mark.parametrize('dr',[125.,250.,500.])
def test_physical_scale_not_fixed_gate_count(dr):
    s=scene(dr=dr,range0=dr/2);e=ev(s,config())
    assert e.arrays['CF_ISO_ISOLATED_MASK'].any()
    area=e.arrays['CF_ISO_AREA_KM2'][s.observed][0]
    assert .85<area<1.25


def test_resolution_larger_than_allowed_abstains():
    e=ev(scene(),config(maximum_gate_footprint_m=250.))
    assert not e.arrays['CF_ISO_CANDIDATE_MASK'].any()


def test_support_tamper_rejected():
    s=scene();c=config();e=ev(s,c);a=copy.deepcopy(e.arrays)
    a['CF_ISO_KNOWN_FRACTION'][s.observed]=.1
    with pytest.raises(ValueError,match='surroundings'):validate(a,c)


def test_count_tamper_rejected():
    s=scene();c=config();e=ev(s,c);a=copy.deepcopy(e.arrays)
    a['CF_ISO_NATIVE_GATE_COUNT'][s.observed]=100
    with pytest.raises(ValueError,match='count mismatch'):validate(a,c)


def test_false_no_echo_cannot_be_constructed():
    s=scene()
    with pytest.raises(ValueError):replace(s,no_echo=np.ones(s.shape,bool))


def test_weak_response_is_diagnostic_not_action():
    s=scene();c=config();e=ev(s,c);a=copy.deepcopy(e.arrays)
    original=decision(a,c)
    for k in a:
        if k.startswith('CF_ISO_WEAK_'):
            a[k][:]=0 if k.endswith('_MASK') else 1e5
    other=decision(a,c)
    for k in original:assert np.array_equal(original[k],other[k],equal_nan=True)


def test_weak_diagnostic_off_keeps_candidates():
    s=scene();a=ev(s,config()).arrays;b=ev(s,config(weak_diagnostic_enabled=False)).arrays
    assert np.array_equal(a['CF_ISO_CANDIDATE_MASK'],b['CF_ISO_CANDIDATE_MASK'])
    assert np.isnan(b['CF_ISO_WEAK_DIAG_A']).all()


def test_uniform_12dbz_low_weak_score_never_sufficient():
    s=scene('uniform',zvalue=12.,rho=.99);e=ev(s,config());a=e.arrays
    assert a['CF_ISO_WEAK_LOW_SCORE_MASK'].any()
    assert not a['CF_ISO_CANDIDATE_MASK'].any()


@pytest.mark.parametrize('mode',['audit','cr_withhold','quarantine'])
def test_projection_modes_no_context(mode):
    s=scene();c=config(mode);b=base(s);e=ev(s,c);out,delta=apply(b,e.arrays,c,low_quality_flag=1024)
    assert delta['isolated_objects_cr_loss_gates']==(0 if mode=='audit' else 12)
    assert delta['qpe_loss_gates']==0
    for key in ('DBZH_RAW','VALID_MASK','QC_FLAGS','REFLECTIVITY_TRUST_MASK','QPE_ELIGIBLE_MASK','QUALITY_INDEX'):
        assert np.array_equal(b[key],out[key],equal_nan=True)
    if mode=='audit':
        for key in b:assert np.array_equal(b[key],out[key],equal_nan=True),key


def test_verified_native_doppler_allows_quantitative_action():
    s=scene();fields=dict(s.fields);fields['VR']=np.where(s.observed,.1,np.nan).astype('float32');fields['SW']=np.where(s.observed,.2,np.nan).astype('float32')
    s=replace(s,fields=fields,available={k:np.isfinite(v) for k,v in fields.items()})
    c=config('quarantine');e=ev(s,c);out,d=apply(base(s),e.arrays,c,low_quality_flag=1024)
    assert d['isolated_objects_qpe_loss_gates']==12
    assert not out['QPE_ELIGIBLE_MASK'].any()
    assert not out['REFLECTIVITY_ELIGIBLE_FOR_CR'].any()
    assert not out['REFLECTIVITY_TRUST_MASK'].any()
    assert out['CF_DERIVED_INVALIDATION_MASK'].sum()==12


def test_background_match_without_current_measurement_not_enough():
    s=scene(rho=.99);c=config('quarantine');e=ev(s,c)
    for key in ('AVAILABLE','MATCH','STABLE','CURRENT_NONMET'):e.arrays['CF_BG_'+key+'_MASK'][s.observed]=1
    d=decision(e.arrays,c);assert not d['CF_ISO_CANDIDATE_MASK'].any()


def test_missing_context_cannot_auto_promote_qpe():
    s=scene();e=ev(s,config('quarantine'))
    assert e.arrays['CF_ISO_CANDIDATE_MASK'].sum()==12
    assert not e.arrays['CF_ISO_QUARANTINE_CANDIDATE_MASK'].any()


def test_parent_exclusion_is_not_revived_or_double_counted():
    s=scene();c=config('cr_withhold');e=ev(s,c);b=base(s);b['REFLECTIVITY_ELIGIBLE_FOR_CR'][:]=0
    out,d=apply(b,e.arrays,c,low_quality_flag=1024)
    assert d['isolated_objects_cr_loss_gates']==0
    assert not out['REFLECTIVITY_ELIGIBLE_FOR_CR'].any()


def test_whole_volume_resource_fallback_preserves_parent():
    s=scene();t=scene(name='sweep_001');c=config(maximum_sample_points=1000)
    results=evaluate_volume([s,t],c)
    assert all(r.summary['isolated_objects']['status']=='RESOURCE_LIMIT_ABSTAINED' for r in results)
    assert all(not r.arrays['CF_ISO_CANDIDATE_MASK'].any() for r in results)
    old=evaluate_volume([s,t],c.model_copy(update={'isolated_objects':None}))
    for a,b in zip(results,old):
        for key,value in b.arrays.items():assert np.array_equal(a.arrays[key],value,equal_nan=True),key


def test_serialized_reconstruction_and_tamper():
    s=scene();c=config('cr_withhold');e=ev(s,c);b=base(s);out,_=apply(b,e.arrays,c,low_quality_flag=1024)
    calls=[]
    def parent(view,attrs):
        calls.append(True)
        for k,v in b.items():assert np.array_equal(view[k],v,equal_nan=True)
    attrs={**attributes(c,1024),'operational_eligible':False}
    validate_serialized(out,attrs,parent);assert calls
    out['REFLECTIVITY_ELIGIBLE_FOR_CR'][s.observed]=1
    with pytest.raises(ValueError):validate_serialized(out,attrs,parent)


def test_absent_config_preserves_digest():
    a=ClutterFusionConfig(depolarization_backend='numpy_reference')
    b=ClutterFusionConfig(depolarization_backend='numpy_reference',isolated_objects=None)
    assert a.digest==b.digest and 'isolated_objects' not in a.model_dump(mode='json')


@pytest.mark.parametrize('kwargs',[{'maximum_area_km2':float('nan')},{'ring_widths_m':(2000.,1000.)},
    {'ring_widths_m':(float('inf'),)},{'minimum_known_fraction':.1},{'extra_fake':True},
    {'maximum_possible_echo_fraction':.9}])
def test_invalid_settings_rejected(kwargs):
    with pytest.raises(ValueError):IsolationConfig(**kwargs)


def test_parent_audit_cannot_silently_activate_actions():
    with pytest.raises(ValueError):ClutterFusionConfig(isolated_objects=IsolationConfig(mode='cr_withhold'))


def test_higher_segmentation_threshold_disallowed_by_low_parent_bound():
    with pytest.raises(ValueError):ClutterFusionConfig(no_rain_below_dbz=0.,
        background={'no_rain_below_dbz':0.},isolated_objects=IsolationConfig(echo_threshold_dbz=-10.))


def test_verified_obstruction_not_clear_support():
    s=scene();fields=dict(s.fields);z=fields['DBZH'].copy();z[85,80]=-20.;fields['DBZH']=z
    s=replace(s,fields=fields,available={k:np.isfinite(v) for k,v in fields.items()},no_echo=s.no_echo&~np.isfinite(z))
    c=config();a=ev(s,c.model_copy(update={'isolated_objects':None})).arrays
    a['CF_NR_DEM_SEVERE_MASK']=np.zeros(s.shape,'uint8');a['CF_NR_DEM_ACTION_AVAILABLE_MASK']=np.zeros(s.shape,'uint8')
    a['CF_NR_DEM_SEVERE_MASK'][85,80]=1;a['CF_NR_DEM_ACTION_AVAILABLE_MASK'][85,80]=1
    g=inspect(s,c,a)
    assert not g.arrays['CF_ISO_ISOLATED_MASK'].any()
    assert g.arrays['CF_ISO_OBSTRUCTION_NEARBY_MASK'].any()


def test_missing_reflectivity_is_explicit_abstention():
    s=scene();fields={k:np.full(s.shape,np.nan,'float32') for k in s.fields}
    s=replace(s,fields=fields,available={k:np.zeros(s.shape,bool) for k in fields},no_echo=np.zeros(s.shape,bool))
    e=ev(s,config());assert e.summary['isolated_objects']['status']=='NO_REFLECTIVITY'
    assert not e.arrays['CF_ISO_CANDIDATE_MASK'].any()
