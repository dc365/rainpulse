"""Merged near windows cannot erase or reanchor independent RAW edge history."""
import numpy as np
import pytest
from .conftest import Native,load

P='RV2_VARIABLE_OBJECT_'


def merged(rotation=0.,spacing=1.):
    r=np.arange(80000.,400000.,500.);az=np.arange(-25.,26.,spacing)
    raw=np.full((len(az),len(r)),np.nan)
    raw[np.abs(az)<=3,:]=25.
    raw[np.ix_(np.abs(az)<=18,r<100000)]=25.
    n=Native(raw,start=80000.,dr=500.,fields={'SNR':np.where(np.isfinite(raw),8.,-2.)})
    n.azimuth=(az+180+rotation)%360
    return n


@pytest.mark.parametrize('spacing',[.5,1.])
def test_frozen_branch_survives_short_merge_without_changing_qc_arrays(spacing):
    m=load('radial_revision.variable_morphology');n=merged(spacing=spacing)
    blocked=np.zeros(n.shape,bool)
    base,_=m.detect(n,blocked)
    fields,report=m.detect(n,blocked,boundary_hypotheses=True)
    for k,v in base.items():assert np.array_equal(fields[k],v)
    qualified=[h for h in report['boundary_hypotheses'] if h['geometry_qualified']]
    assert qualified
    assert any(h['original_start_m']==80000 and h['matched_windows']<h['original_windows']
        and any(w['state']=='unmatched' for w in h['history']) for h in qualified)
    assert all(not h['production_eligible'] and not h['recursive_growth'] for h in qualified)
    assert not fields[P+'BOUNDARY_QUALIFIED_RESEARCH_MASK'][~n.field_available['DBZH']].any()
    other,_=m.detect(merged(rotation=53.,spacing=spacing),blocked,boundary_hypotheses=True)
    assert np.array_equal(fields[P+'BOUNDARY_QUALIFIED_RESEARCH_MASK'],
        other[P+'BOUNDARY_QUALIFIED_RESEARCH_MASK'])


@pytest.mark.parametrize('cause',['weather','barrier','unknown'])
def test_failed_near_history_is_not_discarded_to_reanchor_far_branch(cause):
    n=merged();blocked=np.zeros(n.shape,bool);near=n.ranges<100000
    if cause=='weather':
        n.fields['RHOHV']=np.full(n.shape,np.nan)
        n.fields['RHOHV'][np.ix_(np.abs(n.azimuth-180)<=3,near)]=.99
        n.field_available['RHOHV']=np.isfinite(n.fields['RHOHV'])
        n.fields['SNR'][n.field_available['RHOHV']]=20.
    elif cause=='barrier':blocked[np.ix_(np.abs(n.azimuth-180)<=3,near)]=True
    else:
        n.fields['SNR'][:]=np.nan;n.field_available['SNR'][:]=False
    fields,report=load('radial_revision.variable_morphology').detect(n,blocked,boundary_hypotheses=True)
    assert not fields[P+'BOUNDARY_QUALIFIED_RESEARCH_MASK'].any()
    assert all(not h['production_eligible'] for h in report['boundary_hypotheses'])


def test_narrowing_weather_cannot_be_requalified_as_fixed_far_boundaries():
    from .test_variable_morphology import fixture
    n=fixture('constant_km')
    fields,report=load('radial_revision.variable_morphology').detect(n,np.zeros(n.shape,bool),boundary_hypotheses=True)
    assert not fields[P+'BOUNDARY_QUALIFIED_RESEARCH_MASK'].any()
    assert any('complete_parent_narrowing_weather' in h['holds'] for h in report['boundary_hypotheses'])


@pytest.mark.parametrize('enclosed',[False,True])
def test_high_core_cannot_bypass_complete_low_contour_weather_history(enclosed):
    from .test_variable_morphology import fixture
    n=fixture('constant_km');n.fields['DBZH'][n.field_available['DBZH']]=15.
    core=(np.abs(n.azimuth-180)<=1)[:,None]&n.field_available['DBZH']
    n.fields['DBZH'][core]=45.
    fields,report=load('radial_revision.variable_morphology').detect(n,np.zeros(n.shape,bool),
        boundary_hypotheses=True,enclosed_branch_hypotheses=enclosed)
    high=[h for h in report['boundary_hypotheses'] if h['level_dbz']>=35]
    assert high and any('complete_lower_parent_narrowing_weather' in h['holds'] for h in high)
    assert not fields[P+'BOUNDARY_QUALIFIED_RESEARCH_MASK'].any()


def test_curved_chain_never_changes_frozen_seed_boundaries():
    from .test_variable_morphology import fixture
    n=fixture('curved')
    fields,report=load('radial_revision.variable_morphology').detect(n,np.zeros(n.shape,bool),boundary_hypotheses=True)
    assert not fields[P+'BOUNDARY_QUALIFIED_RESEARCH_MASK'].any()
    assert all(not h['recursive_growth'] for h in report['boundary_hypotheses'])


def split(unknown=False,spacing=1.):
    n=merged(spacing=spacing);middle=np.abs(n.azimuth-180)<1;far=n.ranges>=240000
    n.fields['DBZH'][np.ix_(middle,far)]=np.nan
    n.field_available['DBZH']=np.isfinite(n.fields['DBZH'])
    n.fields['SNR'][np.ix_(middle,far)]=np.nan if unknown else -2.
    n.field_available['SNR']=np.isfinite(n.fields['SNR'])
    return n


@pytest.mark.parametrize('spacing',[.5,1.])
def test_measured_internal_split_keeps_original_outer_edges_without_filling(spacing):
    m=load('radial_revision.variable_morphology');n=split(spacing=spacing);blocked=np.zeros(n.shape,bool)
    before,_=m.detect(n,blocked,boundary_hypotheses=True)
    assert not before[P+'BOUNDARY_QUALIFIED_RESEARCH_MASK'].any()
    fields,report=m.detect(n,blocked,boundary_hypotheses=True,enclosed_branch_hypotheses=True)
    assert fields[P+'BOUNDARY_QUALIFIED_RESEARCH_MASK'].any()
    assert any(h['geometry_qualified'] and h['enclosed_measured_windows']>0
               for h in report['boundary_hypotheses'])
    assert not fields[P+'BOUNDARY_HYPOTHESIS_MASK'][~n.field_available['DBZH']].any()
    for k in [P+'MASK',P+'ID',P+'STRONG_MASK']:
        assert np.array_equal(before[k],fields[k])
    rotated=n.clone();rotated.azimuth=(n.azimuth+180)%360
    other,_=m.detect(rotated,blocked,boundary_hypotheses=True,enclosed_branch_hypotheses=True)
    assert np.array_equal(fields[P+'BOUNDARY_QUALIFIED_RESEARCH_MASK'],
                          other[P+'BOUNDARY_QUALIFIED_RESEARCH_MASK'])


def test_unobserved_internal_split_cannot_be_used_as_measured_enclosure():
    n=split(unknown=True)
    fields,report=load('radial_revision.variable_morphology').detect(n,np.zeros(n.shape,bool),
        boundary_hypotheses=True,enclosed_branch_hypotheses=True)
    assert not fields[P+'BOUNDARY_QUALIFIED_RESEARCH_MASK'].any()
    assert any(any(w['state']=='unobserved_enclosed_path' for w in h['history'])
               for h in report['boundary_hypotheses'])


def test_nonquiet_actual_snr_proves_internal_coverage_without_filling_dbzh():
    n=split();middle=np.abs(n.azimuth-180)<1;far=n.ranges>=240000
    n.fields['SNR'][np.ix_(middle,far)]=8.
    fields,report=load('radial_revision.variable_morphology').detect(n,np.zeros(n.shape,bool),
        boundary_hypotheses=True,enclosed_branch_hypotheses=True)
    assert fields[P+'BOUNDARY_QUALIFIED_RESEARCH_MASK'].any()
    assert any(h['enclosed_nonquiet_snr_without_dbzh']>0 and h['internal_snr_is_not_dry_evidence']
               for h in report['boundary_hypotheses'])
    assert not fields[P+'BOUNDARY_HYPOTHESIS_MASK'][~n.field_available['DBZH']].any()


def test_enclosed_branches_do_not_bypass_lower_weather_or_curved_history():
    from .test_variable_morphology import fixture
    m=load('radial_revision.variable_morphology')
    for n in [fixture('constant_km'),fixture('curved')]:
        fields,_=m.detect(n,np.zeros(n.shape,bool),boundary_hypotheses=True,enclosed_branch_hypotheses=True)
        assert not fields[P+'BOUNDARY_QUALIFIED_RESEARCH_MASK'].any()


@pytest.mark.parametrize('spacing',[.5,1.])
def test_variable_edges_measure_original_runs_without_moving_seed(spacing):
    n=merged(spacing=spacing)
    widths=np.where((n.ranges//20000).astype(int)%2,3.,4.)
    n.fields['DBZH'][:]=np.where(np.abs(n.azimuth-180)[:,None]<=widths,25.,np.nan)
    n.field_available['DBZH']=np.isfinite(n.fields['DBZH'])
    n.fields['SNR']=np.where(n.field_available['DBZH'],8.,-2.)
    m=load('radial_revision.variable_morphology');blocked=np.zeros(n.shape,bool)
    base,_=m.detect(n,blocked,boundary_hypotheses=True)
    assert not base[P+'BOUNDARY_QUALIFIED_RESEARCH_MASK'].any()
    fields,report=m.detect(n,blocked,boundary_hypotheses=True,variable_boundary_hypotheses=True)
    assert fields[P+'BOUNDARY_QUALIFIED_RESEARCH_MASK'].any()
    assert any(h['variable_boundary_mode'] and h['geometry_qualified'] for h in report['boundary_hypotheses'])
    for k,v in base.items():
        if 'BOUNDARY' not in k:assert np.array_equal(v,fields[k])
    assert not fields[P+'BOUNDARY_HYPOTHESIS_MASK'][~n.field_available['DBZH']].any()


@pytest.mark.parametrize('kind',['constant_km','curved'])
def test_variable_edges_do_not_turn_weather_or_drifting_chain_into_ray(kind):
    from .test_variable_morphology import fixture
    n=fixture(kind)
    fields,_=load('radial_revision.variable_morphology').detect(n,np.zeros(n.shape,bool),
        boundary_hypotheses=True,variable_boundary_hypotheses=True)
    assert not fields[P+'BOUNDARY_QUALIFIED_RESEARCH_MASK'].any()


@pytest.mark.parametrize('cause',['weather','barrier'])
def test_expanded_variable_corridor_keeps_original_outer_protection(cause):
    n=merged();widths=np.where((n.ranges//20000).astype(int)%2,3.,4.)
    n.fields['DBZH'][:]=np.where(np.abs(n.azimuth-180)[:,None]<=widths,25.,np.nan)
    n.field_available['DBZH']=np.isfinite(n.fields['DBZH'])
    n.fields['SNR']=np.where(n.field_available['DBZH'],8.,-2.)
    blocked=np.zeros(n.shape,bool)
    edge=(np.abs(n.azimuth-180)==4)[:,None]&n.field_available['DBZH']
    if cause=='barrier':blocked[edge]=True
    else:
        n.fields['RHOHV']=np.where(edge,.99,np.nan)
        n.field_available['RHOHV']=np.isfinite(n.fields['RHOHV'])
        n.fields['SNR'][edge]=20.
    fields,report=load('radial_revision.variable_morphology').detect(n,blocked,
        boundary_hypotheses=True,variable_boundary_hypotheses=True)
    assert not fields[P+'BOUNDARY_QUALIFIED_RESEARCH_MASK'].any()
    assert report['boundary_hypotheses']
    assert all(not h['production_eligible'] for h in report['boundary_hypotheses'])
