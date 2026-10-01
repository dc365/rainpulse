import numpy as np
import pytest
from .helpers import fixture, config


def cut_with_velocity(values):
    v, _ = fixture('coherent', rays=12, gates=len(values))
    cut = v.sweeps[0]
    cut.fields['VR'] = np.tile(values, (12, 1)).astype('float32')
    cut.fields['SW'] = np.full((12, len(values)), 2., 'float32')
    cut.fields['SNRH'][:] = 14.
    cut.fields['DBZH'][:] = 20.
    cut.fields['OBSERVED_MASK'][:] = 1
    cut.fields['NO_ECHO_MASK'][:] = 0
    return cut


def run(cut, nyquist=10.):
    from rainpulse_algo.multiband.xqc_v2.native_doppler_diagnostic import summarize
    return summarize(cut, config(), declared_nyquist_mps=nyquist)


def test_folded_smooth_velocity_is_not_reported_as_large_circular_jump():
    cut = cut_with_velocity([8, 9, -10, -9, -8])
    before = {k: a.copy() for k, a in cut.fields.items()}
    report = run(cut)
    row = report['rays'][0]
    assert row['raw_absolute_delta_mps'][2] > 10
    assert row['declared_period_absolute_delta_mps'] == [1., 1., 1.]
    assert report['action_eligible'] is False
    for k, a in before.items(): np.testing.assert_array_equal(a, cut.fields[k])


def test_no_pairs_across_missing_or_unavailable_gate():
    cut = cut_with_velocity([1, 2, 3, 4, 5])
    cut.fields['VR_VALID_MASK'] = np.ones((12, 5), 'uint8')
    cut.fields['VR_VALID_MASK'][:, 2] = 0
    cut.fields['SW_AVAILABLE_MASK'] = np.ones((12, 5), 'uint8')
    cut.fields['SW_AVAILABLE_MASK'][:, 4] = 0
    row = run(cut)['rays'][0]
    assert row['paired_moment_gates'] == 3
    assert row['consecutive_pairs'] == 1


def test_unknown_nyquist_keeps_raw_diagnostic_without_guessing():
    report = run(cut_with_velocity([1, 5, -4, 3, -8]), None)
    assert report['period_status'] == 'UNAVAILABLE'
    assert report['rays'][0]['declared_period_absolute_delta_mps'] is None
    assert report['rays'][0]['raw_absolute_delta_mps'][1] > 4


def test_missing_moment_and_quiet_snr_do_not_become_zero_velocity():
    cut = cut_with_velocity([1, 2, 3, 4, 5])
    del cut.fields['VR']
    assert all(x['paired_moment_gates'] == 0 for x in run(cut)['rays'])
    cut = cut_with_velocity([1, 2, 3, 4, 5])
    cut.fields['SNRH'][:] = 0
    assert all(x['consecutive_pairs'] == 0 for x in run(cut)['rays'])


def test_wide_spectrum_and_weather_edge_are_statistics_never_actions():
    cut = cut_with_velocity([-9, 7, -3, 9, -6])
    cut.fields['SW'][:] = 8
    report = run(cut)
    assert report['rays'][0]['spectrum_width_mps'] == [8., 8., 8.]
    assert report['rays'][0]['declared_period_absolute_delta_mps'][1] >= 4
    assert report['action_eligible'] is False


def test_malformed_mask_and_resource_limit_are_not_silent_diagnostics():
    from rainpulse_algo.multiband.xqc_v2.native_doppler_diagnostic import summarize
    from rainpulse_algo.radar.qc_engine.volume_review.data import ResourceLimit
    cut = cut_with_velocity([1, 2, 3, 4, 5])
    cut.fields['VR_VALID_MASK'] = np.full((12, 5), 2, 'uint8')
    with pytest.raises(ValueError): run(cut)
    with pytest.raises(ResourceLimit):
        summarize(cut, config(maximum_sweep_gates=10), declared_nyquist_mps=10.)


def test_selection_boundary_and_duplicate_geometry_break_support():
    from rainpulse_algo.multiband.xqc_v2.native_doppler_diagnostic import summarize
    cut = cut_with_velocity([1, 2, 3, 4, 5])
    select = np.ones((12, 5), bool); select[:, 2] = False
    report = summarize(cut, config(), gate_selection=select)
    assert report['rays'][0]['consecutive_pairs'] == 2
    cut.azimuth_deg[1] = cut.azimuth_deg[0]
    report = summarize(cut, config(), gate_selection=select)
    assert not report['rays'][0]['geometry_good']
    assert report['rays'][0]['paired_moment_gates'] == 0
    with pytest.raises(ValueError):
        summarize(cut, config(), gate_selection=np.ones((12, 4)))


@pytest.mark.parametrize('nyquist', [False, float('nan'), -999999., 0.])
def test_invalid_period_does_not_hide_supported_raw_moments(nyquist):
    report = run(cut_with_velocity([1, 2, 3, 4, 5]), nyquist)
    assert report['period_status'] == 'UNAVAILABLE'
    assert report['rays'][0]['raw_absolute_delta_mps'] == [1., 1., 1.]
