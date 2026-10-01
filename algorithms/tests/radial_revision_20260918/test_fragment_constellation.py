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
