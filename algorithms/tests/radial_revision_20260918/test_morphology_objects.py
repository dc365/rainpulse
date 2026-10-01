"""Whole RAW objects, rather than independently qualified surviving fragments."""
import numpy as np
import pytest
from .conftest import Native, load

P = 'RV2_MORPH_OBJECT_'


def fixture(dr=500., spacing=1., *, short=False, fan=False, noise=True):
    r = np.arange(0., 300000., dr)
    z = np.full((41, len(r)), np.nan, 'float32')
    lo, hi = (100000., 130000.) if short else (60000., 260000.)
    rows = np.abs((np.arange(41)-20)*spacing) <= (5. if fan else .5)
    use = (r >= lo) & (r < hi)
    if not short:
        use &= (r % 10000.) < 2500.
    z[np.ix_(rows, use)] = (20.+8.*np.sin(r[use]/9000.))[None, :]
    snr = np.where(np.isfinite(z), 8., -2.) if noise else np.full(z.shape, np.nan)
    n = Native(z, dr=dr, start=0., fields={'SNR': snr})
    n.azimuth = 350.+np.arange(41)*spacing
    return n


def detect(n, blocked=None):
    blocked = np.zeros(n.shape, bool) if blocked is None else blocked
    return load('radial_revision.morphology_objects').detect(n, blocked)


@pytest.mark.parametrize('dr', [250., 500., 1000.])
def test_disjoint_nonsteady_raw_line_has_whole_object_evidence(dr):
    n = fixture(dr); raw = n.fields['DBZH'].copy()
    arrays, report = detect(n)
    hit = np.isfinite(raw)
    assert arrays[P+'STRONG_MASK'][hit].all()
    assert not arrays[P+'MASK'][~hit].any()
    assert np.array_equal(raw, n.fields['DBZH'], equal_nan=True)
    assert report['action_gates'] == 0 and not report['source_claim']
    assert any(o['span_m'] >= 190000. for o in report['objects'])


def test_unknown_sides_never_become_empty_echo_evidence():
    n = fixture(noise=False)
    arrays, report = detect(n)
    assert arrays[P+'MASK'].any()
    assert not arrays[P+'STRONG_MASK'].any()
    assert any('unknown_shoulders' in o['holds'] for o in report['objects'])


def test_discontinuous_wide_fan_does_not_need_power_fit_or_fill_interior():
    n = fixture(fan=True)
    arrays, report = detect(n)
    observed = n.field_available['DBZH']
    assert arrays[P+'STRONG_MASK'][observed].all()
    assert not arrays[P+'MASK'][~observed].any()
    assert any(o['kind'] == 'fan' and o['strong'] for o in report['objects'])


def test_near_short_obvious_line_uses_short_scale_with_strong_boundaries():
    n = fixture(short=True)
    arrays, report = detect(n)
    assert arrays[P+'STRONG_MASK'][n.field_available['DBZH']].all()
    assert any(o['scale_m'] == 5000. and o['strong'] for o in report['objects'])
    n.fields['SNR'][:] = np.nan
    n.field_available['SNR'][:] = False
    arrays, _ = detect(n)
    assert not arrays[P+'STRONG_MASK'].any()


def test_frozen_angular_template_does_not_follow_curved_rain_band():
    n = fixture(); n.fields['DBZH'][:] = np.nan
    for j, start in enumerate(range(60000, 200000, 10000)):
        use = (n.ranges >= start) & (n.ranges < start+2500.)
        n.fields['DBZH'][5+j*2, use] = 25.
    n.field_available['DBZH'] = np.isfinite(n.fields['DBZH'])
    n.fields['SNR'] = np.where(n.field_available['DBZH'], 8., -2.).astype('float32')
    arrays, _ = detect(n)
    assert not arrays[P+'STRONG_MASK'].any()


def test_weather_veto_and_protected_gap_stop_membership():
    n = fixture()
    n.fields['RHOHV'] = np.full(n.shape, .99, 'float32')
    n.field_available['RHOHV'] = n.field_available['DBZH'].copy()
    n.fields['SNR'][n.field_available['DBZH']] = 20.
    arrays, _ = detect(n)
    assert not arrays[P+'STRONG_MASK'].any()
    assert arrays[P+'WEATHER_VETO_MASK'][n.field_available['DBZH']].all()
    n = fixture(); blocked = np.zeros(n.shape, bool)
    blocked[:, (n.ranges >= 140000.) & (n.ranges < 160000.)] = True
    arrays, report = detect(n, blocked)
    assert not arrays[P+'MASK'][blocked].any()
    assert all(not (o['start_m'] < 140000. and o['end_m'] > 160000.) for o in report['objects'])


def test_cross_north_and_native_resolution_preserve_physical_object():
    for spacing in (.5, 1.):
        n = fixture(spacing=spacing, fan=True); n.azimuth %= 360.
        arrays, report = detect(n)
        assert arrays[P+'STRONG_MASK'][n.field_available['DBZH']].all()
        assert any(abs(o['width_deg']-11.) < 1. for o in report['objects'])


def test_bad_geometry_and_scan_gaps_are_not_bridged():
    n = fixture(fan=True); n.gap_after[20] = True
    arrays, report = detect(n)
    assert not arrays[P+'STRONG_MASK'].any()  # each half lacks its other measured shoulder
    n.geometry_good[20] = False
    arrays, _ = detect(n)
    assert not arrays[P+'MASK'][20].any()


def test_aggregate_object_never_admits_unknown_or_contaminated_target_window():
    n = fixture()
    unknown = (n.ranges >= 100000.) & (n.ranges < 110000.)
    n.fields['SNR'][19, unknown] = np.nan
    n.field_available['SNR'] = np.isfinite(n.fields['SNR'])
    arrays, _ = detect(n)
    assert arrays[P+'STRONG_MASK'].any()
    assert not arrays[P+'STRONG_MASK'][:, unknown].any()
    # A nearby stronger observation is not a quiet side for a weak target.
    n.fields['DBZH'][19, unknown] = 35.
    n.field_available['DBZH'] = np.isfinite(n.fields['DBZH'])
    arrays, _ = detect(n)
    assert not arrays[P+'STRONG_MASK'][20, unknown].any()


def test_original_evidence_replay_rejects_forgery_and_exhaustion():
    n = fixture(); arrays, _ = detect(n)
    module = load('radial_revision.morphology_objects')
    blocked = np.zeros(n.shape, bool)
    module.validate(arrays, n, blocked)
    arrays[P+'STRONG_MASK'][0, 0] = 1
    with pytest.raises(ValueError, match='original measurements'):
        module.validate(arrays, n, blocked)
    with pytest.raises(load('radial_revision.geometry').ResourceLimit):
        module.detect(n, blocked, maximum_objects=1)
    with pytest.raises(ValueError):
        module.detect(n, blocked, beam_width=float('nan'))


def test_fixed_kilometre_weather_band_is_not_a_fixed_angle_fan():
    r = np.arange(0., 300000., 500.)
    angles = (np.arange(121)-60)*.25
    # A resolved straight 8km-wide precipitation band: its angular shoulders
    # converge with range, unlike a fixed-angle instrument-origin fan.
    body = (np.abs(r[None, :]*np.sin(np.deg2rad(angles[:, None]))) < 4000.)
    body &= (r[None, :] >= 60000.) & (r[None, :] < 260000.)
    z = np.where(body, 25., np.nan).astype('float32')
    n = Native(z, dr=500., start=0., fields={'SNR': np.where(body, 8., -2.)})
    n.azimuth = angles % 360.
    arrays, _ = detect(n)
    assert not arrays[P+'STRONG_MASK'].any()


def test_full_ppi_native_array_seam_has_real_shoulders_but_sector_does_not_wrap():
    r = np.arange(0., 300000., 500.)
    z = np.full((360, len(r)), np.nan, 'float32')
    rows = ((np.arange(360)+180) % 360-180) <= 3
    rows &= ((np.arange(360)+180) % 360-180) >= -3
    columns = (r >= 60000.) & (r < 260000.) & ((r % 10000.) < 2500.)
    z[np.ix_(rows, columns)] = 25.
    n = Native(z, dr=500., start=0., fields={'SNR': np.where(np.isfinite(z), 8., -2.)})
    n.gap_after[-1] = False; n.full_ppi = True
    arrays, _ = detect(n)
    assert arrays[P+'STRONG_MASK'][n.field_available['DBZH']].all()
    n.gap_after[-1] = True
    arrays, _ = detect(n)
    assert not arrays[P+'STRONG_MASK'][n.field_available['DBZH']].any()
