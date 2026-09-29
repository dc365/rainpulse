# ruff: noqa: E501, I001
from copy import deepcopy
from dataclasses import replace
import numpy as np
import pytest
from rainpulse_algo.multiband.attenuation import PathState, PathReason, ZPhiOptions, zphi_segment, correct_sweep
from rainpulse_algo.multiband.model import XProfile
from rainpulse_algo.multiband.quality import x_qc, phase_linear
from .helpers import volume, station, options, SHA

def run(v=None, st=None):
    st = st or station()
    v = v or volume(st)
    return x_qc(v, st, SHA).sweeps[0]

def test_zphi_constant_true_reflectivity_forward_model():
    r = np.linspace(0, 40, 401)
    observed = 40 - 0.2 * r
    phi = r
    pia, ah = zphi_segment(observed, phi, r * 1000, alpha=0.2, beta=0.62)
    np.testing.assert_allclose(observed + pia, 40, atol=2e-05)
    np.testing.assert_allclose(ah, 0.1, rtol=2e-05)
    assert pia[-1] == pytest.approx(8.0, abs=1e-09)

def test_zphi_uses_z_profile_not_linear_phi_copy():
    r = np.arange(101) * 200.0
    phi = np.linspace(0, 50, 101)
    z = np.r_[np.full(51, 20.0), np.full(50, 50.0)]
    pia, ah = zphi_segment(z, phi, r, alpha=0.2, beta=0.62, initial_pia_db=1.5)
    assert pia[-1] == pytest.approx(11.5)
    assert pia[50] < 3
    assert pia[50] != pytest.approx(1.5 + 0.2 * phi[50])
    assert np.all(np.diff(pia) >= 0) and np.all(ah > 0)

def test_input_offset_and_sampling_do_not_change_endpoint_constraint():
    r = np.array([0, 150, 500, 900, 2500.0])
    a, _ = zphi_segment(np.full(5, 30.0), np.arange(5) * 5, r, alpha=0.2, beta=0.62)
    b, _ = zphi_segment(np.full(5, 45.0), 100 + np.arange(5) * 5, r, alpha=0.2, beta=0.62)
    np.testing.assert_allclose(a, b)
    assert a[-1] == pytest.approx(4.0)

@pytest.mark.parametrize('changes', [{'beta': True}, {'beta': 0}, {'frequency_min_hz': 2000000000.0}, {'phase_period_deg': 90}, {'minimum_delta_phase_deg': 0}, {'coefficient_source_sha256': 'unknown'}])
def test_invalid_explicit_options_rejected(changes):
    with pytest.raises(ValueError):
        ZPhiOptions(**options(**changes))

def test_zphi_requires_coefficients_and_explicit_alpha():
    with pytest.raises(ValueError):
        XProfile(attenuation='zphi', alpha_db_per_degree=0.2)
    with pytest.raises(ValueError):
        XProfile(attenuation='zphi', zphi=options())

def test_standalone_path_output_and_raw_immutability():
    st = station()
    v = volume(st)
    before = deepcopy(v)
    f = run(v, st).fields
    assert f['PATH_VALID_MASK'].all()
    assert np.all(f['PATH_STATE'] == PathState.ZPHI)
    assert np.all(f['RADOME_QUALIFIED_MASK'] == 1)
    assert not f['QPE_ELIGIBLE_MASK'].any()
    np.testing.assert_allclose(f['DBZH_ATTENUATION_CORRECTED'], f['DBZH_RAW'] + f['PIA_DB'])
    for k in v.sweeps[0].fields:
        np.testing.assert_array_equal(v.sweeps[0].fields[k], before.sweeps[0].fields[k])

@pytest.mark.parametrize('mode', ['unknown', 'corrected'])
def test_no_double_or_unknown_upstream_correction(mode):
    v = volume()
    v.metadata['attenuation_status'] = mode
    with pytest.raises(ValueError):
        run(v)

@pytest.mark.parametrize('key', ['phase_anchor_verified', 'pia_at_first_gate_db'])
def test_unknown_initial_loss_does_not_restart_farther_away(key):
    v = volume()
    v.metadata.pop(key)
    f = run(v).fields
    assert not f['PATH_VALID_MASK'].any()
    assert np.isnan(f['DBZH_ATTENUATION_CORRECTED']).all()

@pytest.mark.parametrize('key', ['PHIDP_VALID_MASK', 'PHIDP_AVAILABLE_MASK', 'SNR_VALID_MASK', 'RHOHV_AVAILABLE_MASK'])
def test_supplied_moment_validity_blocks_entire_downstream_path(key):
    v = volume()
    shape = v.sweeps[0].fields['DBZH'].shape
    v.sweeps[0].fields[key] = np.ones(shape, 'uint8')
    v.sweeps[0].fields[key][:, 40] = 0
    f = run(v).fields
    assert f['PATH_VALID_MASK'][:, :40].all()
    assert not f['PATH_VALID_MASK'][:, 40:].any()

def test_explicit_known_anchor_may_resume_but_unknown_gap_never_does():
    v = volume()
    f = v.sweeps[0].fields
    shape = f['DBZH'].shape
    f['PHASE_VALID_MASK'][:, 40:50] = 0
    f['PATH_ANCHOR_VALID_MASK'] = np.zeros(shape, 'uint8')
    f['PATH_ANCHOR_VALID_MASK'][:, 50] = 1
    f['PATH_ANCHOR_PIA_DB'] = np.full(shape, np.nan, 'float32')
    f['PATH_ANCHOR_PIA_DB'][:, 50] = 4.0
    with pytest.raises(ValueError):
        run(v)
    v.metadata['path_anchor_evidence_sha256'] = 'd' * 64
    out = run(v).fields
    assert not out['PATH_VALID_MASK'][:, 40:50].any()
    assert out['PATH_VALID_MASK'][:, 50:].all()
    assert out['PIA_DB'][0, 50] == pytest.approx(4.0)

def test_verified_clear_gap_preserves_prior_pia_no_invented_phase():
    v = volume()
    f = v.sweeps[0].fields
    shape = f['DBZH'].shape
    f['PHASE_VALID_MASK'][:, 40:50] = 0
    f['NO_ECHO_MASK'][:, 40:50] = 1
    f['DBZH'][:, 40:50] = np.nan
    f['CLEAR_PATH_MASK'] = np.zeros(shape, 'uint8')
    f['CLEAR_PATH_MASK'][:, 40:50] = 1
    no_proof = run(v).fields
    assert not no_proof['PATH_VALID_MASK'][:, 40:].any()
    v.metadata['clear_path_evidence_sha256'] = 'd' * 64
    out = run(v).fields
    assert out['PATH_VALID_MASK'].all()
    assert np.all(out['PATH_STATE'][:, 40:50] == PathState.VERIFIED_CLEAR_PATH)
    assert np.isnan(out['DBZH_ATTENUATION_CORRECTED'][:, 40:50]).all()
    assert out['PIA_DB'][0, 50] == pytest.approx(out['PIA_DB'][0, 39])

def test_small_phase_not_assumed_zero_loss():
    v = volume()
    v.sweeps[0].fields['PHIDP'] /= 100
    f = run(v).fields
    assert not f['PATH_VALID_MASK'].any()
    assert np.all(f['PATH_REASON'] & int(PathReason.PHASE_SPAN_TOO_SMALL))

def test_limit_is_not_clipped_to_become_trusted():
    st = station(x_qc=XProfile(attenuation='zphi', alpha_db_per_degree=0.2, max_pia_db=2, zphi=options()))
    f = run(volume(st), st).fields
    assert not f['PATH_VALID_MASK'].any()
    assert np.all(f['PATH_REASON'] & int(PathReason.CORRECTION_LIMIT))

@pytest.mark.parametrize('field,value', [('LIQUID_MASK', 0), ('RHOHV', 0.5), ('SNRH', -5)])
def test_liquid_polarimetry_and_signal_quality_required(field, value):
    v = volume()
    v.sweeps[0].fields[field][:, 40] = value
    out = run(v).fields
    assert not out['PATH_VALID_MASK'][:, 40:].any()
    assert out['PATH_VALID_MASK'][:, :40].all()

def test_radome_is_not_fixed_by_path_correction():
    v = volume()
    v.metadata.pop('radome_evidence_sha256')
    f = run(v).fields
    assert f['PATH_VALID_MASK'].all()
    assert not f['RADOME_QUALIFIED_MASK'].any()
    assert np.all(f['PATH_REASON'] & int(PathReason.RADOME_UNVERIFIED))

def test_known_negligible_without_correction_does_not_report_measured_zero_pia():
    st = station(x_qc=XProfile())
    v = volume(st)
    v.sweeps[0].fields['ATTENUATION_NEGLIGIBLE_MASK'] = np.ones((2, 101), 'uint8')
    assert not run(v, st).fields['PATH_VALID_MASK'].any()
    v.metadata['negligible_attenuation_evidence_sha256'] = 'e' * 64
    out = run(v, st).fields
    assert out['PATH_VALID_MASK'].all()
    assert np.isnan(out['PIA_DB']).all()
    np.testing.assert_array_equal(out['DBZH_ATTENUATION_CORRECTED'], v.sweeps[0].fields['DBZH'])

def test_vendor_correction_retains_unknown_magnitude_and_does_not_double_add():
    st = station(x_qc=XProfile(attenuation='upstream_verified'))
    v = volume(st)
    v.metadata['attenuation_status'] = 'corrected'
    v.sweeps[0].fields['ATTENUATION_VALID_MASK'] = np.ones((2, 101), 'uint8')
    f = run(v, st).fields
    np.testing.assert_array_equal(f['DBZH_ATTENUATION_CORRECTED'], v.sweeps[0].fields['DBZH'])
    assert np.isnan(f['PIA_DB']).all()
    assert np.all(f['PATH_STATE'] == PathState.UPSTREAM_VERIFIED)

def test_legacy_linear_baseline_uses_two_way_phi_once():
    st = station(x_qc=XProfile(attenuation='phidp_linear', alpha_db_per_degree=0.2, phase_window_m=100))
    v = volume(st)
    pia, kdp, _ = phase_linear(v.sweeps[0], st.x_qc, anchor_verified=True, initial_pia_db=0)
    f = run(v, st).fields
    np.testing.assert_array_equal(f['PIA_DB'], pia)
    assert pia[0, -1] == pytest.approx(8.0)

def test_linear_dispatch_honors_explicit_unavailable_phase_alias():
    st = station(x_qc=XProfile(attenuation='phidp_linear', alpha_db_per_degree=0.2, phase_window_m=100))
    v = volume(st)
    v.sweeps[0].fields['PHIDP_AVAILABLE_MASK'] = np.ones((2, 101), 'uint8')
    v.sweeps[0].fields['PHIDP_AVAILABLE_MASK'][:, 30] = 0
    result = run(v, st)
    assert result.fields['PATH_VALID_MASK'][:, :30].all()
    assert not result.fields['PATH_VALID_MASK'][:, 30:].any()

def test_correction_bound_is_not_bypassed_by_linear_method():
    st = station(x_qc=XProfile(attenuation='phidp_linear', alpha_db_per_degree=0.2, phase_window_m=100))
    out = run(volume(st, value=99.0), st).fields
    assert not out['PATH_VALID_MASK'][:, 50:].any()
    assert np.all(out['PATH_REASON'][:, 50:] & int(PathReason.CORRECTION_LIMIT))
