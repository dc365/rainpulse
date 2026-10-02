"""Sparse threshold excursions must use independent, measured RAW references."""
import numpy as np
import pytest

from rainpulse_algo.multiband.xqc_v2.geometry import adapt
from rainpulse_algo.multiband.xqc_v2.source_blocks import detect

from .helpers import config, fixture


def sample(*, rays=360, dr=75.):
    v, row = fixture(rays=rays, gates=int(75000/dr), dr=dr)
    f = v.sweeps[0].fields
    r = v.sweeps[0].range_m
    f['DBZH'][:] = np.nan
    f['SNRH'][:] = -7.
    f['SNRH'][row] = 2.1
    excursions = (np.arange(len(r)) % 23 == 0) & (r >= 15000)
    f['SNRH'][row, excursions] = 3.
    f['DBZH'][row] = f['SNRH'][row] + 20*np.log10(r/1000) - 20
    f['OBSERVED_MASK'][:] = np.isfinite(f['DBZH'])
    f['NO_ECHO_MASK'][:] = 0
    return v, row, excursions


def evaluate(v, *, research=False, protected=None):
    cfg = config(radial_source_enabled=True, noise_censor_snr_db=3.)
    view = adapt(v.sweeps[0], cfg)
    p = np.zeros(view.sweep.shape, bool) if protected is None else protected[view.order]
    kwargs = {'near_floor_references': True} if research else {}
    mask, record = detect(view.sweep, cfg, protected=p, **kwargs)
    return view.restore(mask), record


def test_raw_near_floor_references_qualify_sparse_excursions_only():
    v, row, excursions = sample()
    original = v.sweeps[0].fields['DBZH'].copy()
    assert not evaluate(v)[0][row, excursions].any()
    mask, record = evaluate(v, research=True)
    assert mask[row, excursions].sum() > 20
    assert not mask[row, ~excursions].any()
    assert record['diagnostic_only'] is True
    np.testing.assert_array_equal(v.sweeps[0].fields['DBZH'], original)


@pytest.mark.parametrize('change', ['missing_snr', 'wide', 'weather', 'protected'])
def test_independent_receiver_and_weather_guards_remain(change):
    v, row, _ = sample()
    f = v.sweeps[0].fields
    p = np.zeros(f['DBZH'].shape, bool)
    if change == 'missing_snr':
        f['SNRH'][row-3:row] = np.nan
    elif change == 'wide':
        for other in range(row-4, row+5):
            f['SNRH'][other] = f['SNRH'][row]
            f['DBZH'][other] = f['DBZH'][row]
    elif change == 'weather':
        f['DBZH'][row] = 25.
    else:
        p[row] = True
    assert not evaluate(v, research=True, protected=p)[0].any()


def test_target_and_guard_cannot_train_amplified_echo():
    v, row, _ = sample()
    r = v.sweeps[0].range_m
    target = (r >= 35000) & (r < 40000)
    v.sweeps[0].fields['DBZH'][row, target] += 8.
    mask, _ = evaluate(v, research=True)
    assert mask[row].any()
    assert not mask[row, target].any()


@pytest.mark.parametrize('rays,dr,offset', [(360,75.,0.), (720,150.,239.9)])
def test_angle_resolution_and_distance_spacing_are_not_station_exceptions(rays, dr, offset):
    v, row, excursions = sample(rays=rays, dr=dr)
    v.sweeps[0].azimuth_deg[:] = (v.sweeps[0].azimuth_deg + offset) % 360
    mask, _ = evaluate(v, research=True)
    assert mask[row, excursions].sum() > 10
    assert not mask[row, ~excursions].any()


def test_excursion_outside_independent_raw_bounds_stays_unqualified():
    v, row, excursions = sample()
    f = v.sweeps[0].fields
    f['SNRH'][row, excursions] = 5.
    r = v.sweeps[0].range_m
    f['DBZH'][row, excursions] = 5. + 20*np.log10(r[excursions]/1000) - 20
    assert not evaluate(v, research=True)[0].any()
