import numpy as np
from .helpers import config, fixture


def run(volume, width=3.):
    from rainpulse_algo.multiband.xqc_v2.geometry import adapt
    from rainpulse_algo.multiband.xqc_v2.radial_source import detect
    cfg = config(radial_source_enabled=True, noise_censor_snr_db=3., radial_source_maximum_width_deg=width)
    s = adapt(volume.sweeps[0], cfg).sweep
    return detect(s, cfg, protected=np.zeros(s.shape, bool))[0]


def narrow_source():
    v, row = fixture('coherent', rays=360, gates=1000)
    f = v.sweeps[0].fields
    f['DBZH'][:] = np.nan
    f['SNRH'][:] = -4.
    f['SNRH'][row] = 14.
    f['DBZH'][row] = 14 + 20*np.log10(v.sweeps[0].range_m / 1000) - 20
    f['RHOHV'][row] = .995
    f['OBSERVED_MASK'][:] = np.isfinite(f['DBZH'])
    f['NO_ECHO_MASK'][:] = 0
    return v, row


def test_high_rho_narrow_receiver_source_is_not_automatically_weather():
    v, row = narrow_source()
    assert run(v)[row].sum() > 600


def test_missing_snr_is_not_a_quiet_receiver():
    v, row = narrow_source()
    v.sweeps[0].fields['SNRH'][row+1:row+4] = np.nan
    assert not run(v).any()


def test_wide_weather_and_narrow_range_variable_echo_are_preserved():
    v, row = narrow_source()
    for other in range(row-5, row+6):
        for field in ('DBZH', 'SNRH'):
            v.sweeps[0].fields[field][other] = v.sweeps[0].fields[field][row]
    assert not run(v).any()
    v, row = narrow_source()
    v.sweeps[0].fields['DBZH'][row] = 25.
    assert not run(v).any()


def test_short_echo_and_protected_gates_are_preserved():
    from rainpulse_algo.multiband.xqc_v2.geometry import adapt
    from rainpulse_algo.multiband.xqc_v2.radial_source import detect
    v, row = narrow_source()
    cfg = config(radial_source_enabled=True, noise_censor_snr_db=3.)
    s = adapt(v.sweeps[0], cfg).sweep
    protected = np.zeros(s.shape, bool); protected[row] = True
    assert not detect(s, cfg, protected=protected)[0].any()
    v.sweeps[0].fields['DBZH'][row, 180:] = np.nan
    assert not run(v).any()


def test_finalizer_rejects_source_and_audit_preserves_it():
    from rainpulse_algo.multiband.quality import x_qc
    from .helpers import station
    v, row = narrow_source()
    original = v.sweeps[0].fields['DBZH'].copy()
    for mode in ('audit', 'quarantine'):
        cfg = config(mode=mode, radial_source_enabled=True, noise_censor_snr_db=3.)
        out = x_qc(v, station(cfg), 'a'*64).sweeps[0].fields
        candidate = out['XQC_RADIAL_SOURCE_MASK'] == 1
        assert candidate[row].sum() > 600
        if mode == 'quarantine':
            assert np.all(out['QC_ACTION'][candidate] == 2)
            assert np.isnan(out['DBZH_QC_DISPLAY'][candidate]).all()
        else:
            assert np.isfinite(out['DBZH_QC_DISPLAY'][candidate]).all()
    np.testing.assert_array_equal(v.sweeps[0].fields['DBZH'], original)


def test_target_block_cannot_train_its_own_amplified_echo():
    v, row = narrow_source()
    r = v.sweeps[0].range_m
    target = (r >= 35000) & (r < 40000)
    v.sweeps[0].fields['DBZH'][row, target] += 8
    out = run(v)
    assert out[row].any()
    assert not out[row, target].any()


def test_five_degree_corridor_accepts_three_rays_but_not_wide_weather():
    v, row = narrow_source()
    for other in (row-1, row+1):
        for field in ('DBZH', 'SNRH'):
            v.sweeps[0].fields[field][other] = v.sweeps[0].fields[field][row]
    assert run(v, width=5.)[row].sum() > 600
    for other in range(row-5, row+6):
        for field in ('DBZH', 'SNRH'):
            v.sweeps[0].fields[field][other] = v.sweeps[0].fields[field][row]
    assert not run(v, width=5.).any()


def test_source_is_still_subject_to_global_exclusion_budget():
    from rainpulse_algo.multiband.xqc_v2.core import evaluate_cut, Reason
    v, row = narrow_source()
    cfg = config(radial_source_enabled=True, noise_censor_snr_db=3., maximum_new_exclusion_fraction=.1)
    ev = evaluate_cut(v.sweeps[0], v.metadata, cfg)
    assert ev.arrays['XQC_RADIAL_SOURCE_MASK'].any()
    assert not ev.arrays['XQC_QUARANTINE_MASK'].any()
    assert ev.record['status'] == 'ACTION_BUDGET_ABSTAINED'
