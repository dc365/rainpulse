"""Whole-history positive and weather counterexamples for variable envelopes."""
import numpy as np
import pytest
from .conftest import Native, load

P='RV2_VARIABLE_OBJECT_'


def fixture(kind='variable', rotation=0., spacing=1.):
    dr=500.;r=np.arange(0.,420000.,dr);az=np.arange(-30.,31.,spacing)
    z=np.full((len(az),len(r)),np.nan,'float32')
    for j, radius in enumerate(r):
        if not 80000<=radius<400000:continue
        if kind=='variable':width=5.+1.5*np.sin(radius/25000.)
        elif kind=='constant_km':width=np.rad2deg(20000./radius)/2
        else:width=1.
        centre=(radius-80000)/20000 if kind=='curved' else 0.
        z[np.abs(az-centre)<=width,j]=25.
    n=Native(z,dr=dr,start=0.,fields={'SNR':np.where(np.isfinite(z),8.,-2.)})
    n.azimuth=(az+180.+rotation)%360
    return n


def detect(n,blocked=None,**kwargs):
    if blocked is None:blocked=np.zeros(n.shape,bool)
    return load('radial_revision.variable_morphology').detect(n,blocked,**kwargs)


@pytest.mark.parametrize('spacing',[.5,1.])
def test_variable_fan_keeps_complete_original_envelope(spacing):
    n=fixture(spacing=spacing);raw=n.fields['DBZH'].copy()
    arrays,report=detect(n)
    assert arrays[P+'STRONG_MASK'][n.field_available['DBZH']].any()
    assert not arrays[P+'MASK'][~n.field_available['DBZH']].any()
    assert np.array_equal(raw,n.fields['DBZH'],equal_nan=True)
    assert report['action_gates']==0 and not report['product_writes']
    rotated=fixture(rotation=53.,spacing=spacing)
    other,_=detect(rotated)
    assert np.array_equal(arrays[P+'STRONG_MASK'],other[P+'STRONG_MASK'])


def test_constant_km_weather_does_not_restart_as_qualified_far_tail():
    arrays,report=detect(fixture('constant_km'))
    assert not arrays[P+'STRONG_MASK'].any()
    assert any('narrowing_physical_width_weather_counterexample' in o['holds'] for o in report['objects'])


def test_curved_band_cannot_restart_after_centre_drift():
    arrays,report=detect(fixture('curved'))
    assert not arrays[P+'STRONG_MASK'].any()
    assert any('curved_or_drifting_centre' in o['holds'] for o in report['objects'])


def test_unknown_side_and_weather_barrier_are_not_promoted():
    n=fixture();n.fields['SNR'][:]=np.nan;n.field_available['SNR'][:]=False
    arrays,_=detect(n);assert not arrays[P+'STRONG_MASK'].any()
    n=fixture();blocked=np.zeros(n.shape,bool)
    blocked[:,(n.ranges>=160000)&(n.ranges<165000)]=True
    arrays,_=detect(n,blocked);assert not arrays[P+'STRONG_MASK'].any()


def test_actual_weather_measurements_are_retained():
    n=fixture();n.fields['RHOHV']=np.full(n.shape,.99,'float32')
    n.field_available['RHOHV']=n.field_available['DBZH'].copy()
    n.fields['SNR'][n.field_available['DBZH']]=20
    arrays,_=detect(n);assert not arrays[P+'STRONG_MASK'].any()


def test_resource_exhaustion_has_no_partial_result():
    with pytest.raises(load('radial_revision.geometry').ResourceLimit):
        detect(fixture(),maximum_objects=1)


def test_fork_keeps_early_history_and_does_not_accept_children():
    n=fixture()
    # The central gap divides the already tracked envelope into two branches.
    middle=np.abs(n.azimuth-180)<2
    cols=n.ranges>=240000
    n.fields['DBZH'][np.ix_(middle,cols)]=np.nan
    n.field_available['DBZH']=np.isfinite(n.fields['DBZH'])
    n.fields['SNR'][np.ix_(middle,cols)]=-2
    arrays,report=detect(n)
    assert not arrays[P+'STRONG_MASK'].any()
    assert any('ambiguous_fork_or_merge' in o['holds'] for o in report['objects'])


def test_forged_evidence_is_rejected_by_original_measurement_replay():
    n=fixture();barred=np.zeros(n.shape,bool)
    module=load('radial_revision.variable_morphology')
    arrays,_=module.detect(n,barred);module.validate(arrays,n,barred)
    arrays[P+'STRONG_MASK'][0,0]=1
    with pytest.raises(ValueError,match='proof differs'):
        module.validate(arrays,n,barred)


def test_actual_local_footprints_bound_narrow_centre_jitter():
    r=np.arange(0.,420000.,500.);az=np.arange(41.,dtype=float)
    az[21:]+=0.2
    z=np.full((len(az),len(r)),np.nan,'float32')
    for j,radius in enumerate(r):
        if 80000<=radius<400000:
            rows=[20] if int(radius//10000)%8 else [20,21,22]
            z[rows,j]=25
    n=Native(z,dr=500.,start=0.,fields={'SNR':np.where(np.isfinite(z),8.,-2.)})
    n.azimuth=az
    arrays,report=detect(n)
    assert arrays[P+'STRONG_MASK'][n.field_available['DBZH']].any()
    assert any(o['centre_tolerance_deg']>1 for o in report['objects'])
    # A missing native sector must not become a huge footprint allowance.
    n.azimuth[21:]+=8
    arrays,_=detect(n)
    assert not arrays[P+'STRONG_MASK'].any()


def nested_branch():
    r=np.arange(0.,420000.,500.)
    z=np.full((43,len(r)),np.nan,'float32')
    for j,radius in enumerate(r):
        if 80000<=radius<400000:
            extent=3 if int(radius//20000)%2 else 18
            z[20-extent:24,j]=25
            z[19:22,j]=34
            z[20,j]=35
    return Native(z,dr=500.,start=0.,fields={'SNR':np.where(np.isfinite(z),8.,-2.)})


def test_bounded_exterior_recovers_core_without_growing_parent():
    n=nested_branch();old,_=detect(n)
    new,report=detect(n,branch_shoulders=True)
    core=n.field_available['DBZH'][20]
    assert not old[P+'STRONG_MASK'][20,core].any()
    assert new[P+'STRONG_MASK'][20,core].all()
    # The weak halo belongs to a drifting broad parent: it cannot inherit
    # the qualified high-level branch's identity or deletion eligibility.
    assert not new[P+'STRONG_MASK'][n.fields['DBZH']<35].any()
    assert report['action_gates']==0 and not report['recursive_growth']
    rotated=n.clone();rotated.azimuth=(rotated.azimuth+121)%360
    other,_=detect(rotated,branch_shoulders=True)
    assert np.array_equal(new[P+'STRONG_MASK'],other[P+'STRONG_MASK'])


@pytest.mark.parametrize('kind',['unknown','barrier','outside_stencil'])
def test_branch_cannot_search_past_unknown_barrier_or_frozen_stencil(kind):
    n=nested_branch();blocked=np.zeros(n.shape,bool)
    core=n.field_available['DBZH'][20]
    if kind=='unknown':
        n.fields['DBZH'][19,core]=np.nan;n.field_available['DBZH'][19,core]=False
        n.fields['SNR'][19,core]=np.nan;n.field_available['SNR'][19,core]=False
    elif kind=='barrier':blocked[19,core]=True
    else:
        n.fields['DBZH'][18:23,core]=34;n.fields['DBZH'][20,core]=35
    arrays,_=detect(n,blocked,branch_shoulders=True)
    assert not arrays[P+'STRONG_MASK'][20,core].any()


@pytest.mark.parametrize('kind',['constant_km','curved'])
def test_branch_mode_retains_whole_history_weather_counterexamples(kind):
    arrays,_=detect(fixture(kind),branch_shoulders=True)
    assert not arrays[P+'STRONG_MASK'].any()


def test_branch_mode_preserves_qualified_original_objects_and_weather():
    n=fixture();baseline,_=detect(n);candidate,_=detect(n,branch_shoulders=True)
    assert np.all(candidate[P+'STRONG_MASK'][baseline[P+'STRONG_MASK']==1]==1)
    n=nested_branch();n.fields['RHOHV']=np.full(n.shape,.99,'float32')
    n.field_available['RHOHV']=n.field_available['DBZH'].copy()
    n.fields['SNR'][n.field_available['DBZH']]=20
    candidate,_=detect(n,branch_shoulders=True)
    assert not candidate[P+'STRONG_MASK'].any()


def test_branch_proof_binds_search_mode_and_actual_measurements():
    n=nested_branch();blocked=np.zeros(n.shape,bool)
    module=load('radial_revision.variable_morphology')
    arrays,_=module.detect(n,blocked,branch_shoulders=True)
    module.validate(arrays,n,blocked,branch_shoulders=True)
    with pytest.raises(ValueError,match='proof differs'):
        module.validate(arrays,n,blocked,branch_shoulders=False)
    arrays[P+'STRONG_MASK'][0,0]=1
    with pytest.raises(ValueError,match='proof differs'):
        module.validate(arrays,n,blocked,branch_shoulders=True)
