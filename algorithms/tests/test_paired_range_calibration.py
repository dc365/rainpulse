import numpy as np

from rainpulse_algo.radar.qc_engine.paired_range import calibrate


def scene():
    r = np.arange(125., 460000., 250.)
    snr = np.full((20, len(r)), 55.5)
    offsets = np.linspace(-46., -43., len(snr))[:, None]
    z = snr + offsets + 20*np.log10(r/1000) + .011*r/1000
    use = np.ones(z.shape, bool)
    # Different offset populations disappear with range: a pooled median is biased.
    use[10:, r >= 200000] = False
    return r, z, snr, use, r >= 150000


def test_ray_population_changes_do_not_bias_slope():
    c, d = calibrate(*scene())
    assert d['status'] == 'measured_consistent'
    assert abs(c-.011) < 1e-10
    assert all(g['residual_p90_db'] < 1e-10 for g in d['groups'])


def test_target_and_guard_mutations_do_not_change_fit_or_provenance():
    r, z, sn, use, ref = scene()
    expected = calibrate(r, z, sn, use, ref)
    z[:, ~ref] += 400
    sn[:, ~ref] -= 100
    assert calibrate(r, z, sn, use, ref) == expected


def test_protected_observations_cannot_train():
    r, z, sn, use, ref = scene()
    expected = calibrate(r, z, sn, use, ref)
    z[~use] = 9000
    assert calibrate(r, z, sn, use, ref) == expected


def test_independent_ray_groups_disagree():
    r, z, sn, use, ref = scene()
    z[::2] += .006*r/1000
    c, d = calibrate(r, z, sn, use, ref)
    assert c == 0 and d['status'] == 'inconsistent_ray_groups'


def test_short_rays_cannot_collectively_invent_long_support():
    r, z, sn, use, ref = scene()
    use[:] = False
    for i in range(len(use)):
        use[i, (r >= 150000+i*10000) & (r < 210000+i*10000)] = True
    c, d = calibrate(r, z, sn, use, ref)
    assert c == 0 and d['status'] == 'insufficient_paired_support'


def test_nonlinear_response_is_not_repaired_by_wider_threshold():
    r, z, sn, use, ref = scene()
    z += 7*np.sin(r/25000)
    c, d = calibrate(r, z, sn, use, ref)
    assert c == 0 and d['status'] == 'unsupported_range_relation'


def test_validation_does_not_refit_its_offset():
    r, z, sn, use, ref = scene()
    z[:, (r//50000).astype(int) % 2 == 1] += 2
    c, d = calibrate(r, z, sn, use, ref)
    assert c == 0 and d['status'] == 'unsupported_range_relation'


def test_a_single_contradicting_long_ray_is_not_hidden_by_pooled_p90():
    r, z, sn, use, ref = scene()
    z[0] += 7*np.sin(r/25000)
    c, d = calibrate(r, z, sn, use, ref)
    assert c == 0 and d['status'] == 'unsupported_range_relation'
