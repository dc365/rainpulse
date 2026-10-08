import numpy as np

from rainpulse_algo.radar.qc_engine.paired_range_components import calibrate_components

def scene():
    r = np.arange(125., 460000., 250.)
    snr = np.full((20, len(r)), 55.5)
    offsets = np.linspace(-46., -43., len(snr))[:, None]
    z = snr + offsets + 20*np.log10(r/1000) + .011*r/1000
    use = np.ones(z.shape, bool)
    use[10:, r >= 200000] = False
    return r, z, snr, use, r >= 150000



def test_one_bad_ray_cannot_zero_other_processing_cohorts():
    r, z, snr, use, ref = scene()
    use[:] = True
    z[0] += 7 * np.sin(r / 25000)
    c, good, d = calibrate_components(r, z, snr, use, ref)
    assert not good[0] and c[0] == 0
    assert good[1:].all() and np.allclose(c[1:], .011)
    assert d['status'] == 'measured_components' and 0 in d['rejected_reference_rays']


def test_target_guard_and_ineligible_changes_cannot_change_components():
    r, z, snr, use, ref = scene()
    a = calibrate_components(r, z, snr, use, ref)
    z[:, ~ref] += 400
    snr[~use] = -900
    b = calibrate_components(r, z, snr, use, ref)
    assert np.array_equal(a[0], b[0]) and np.array_equal(a[1], b[1]) and a[2] == b[2]


def test_held_intercept_shift_is_not_selected_as_supported_component():
    r, z, snr, use, ref = scene()
    z[:, (r // 50000).astype(int) % 2 == 1] += 2
    c, good, d = calibrate_components(r, z, snr, use, ref)
    assert not good.any() and not c.any() and d['status'] == 'no_supported_component'


def test_nonlinear_or_short_rays_do_not_collectively_create_support():
    r, z, snr, use, ref = scene()
    z += 7 * np.sin(r / 25000)
    assert not calibrate_components(r, z, snr, use, ref)[1].any()
    use[:] = False
    for i in range(len(use)):
        use[i, (r >= 150000 + i * 10000) & (r < 210000 + i * 10000)] = True
    assert not calibrate_components(r, z, snr, use, ref)[1].any()


def test_different_processing_relations_have_separate_authority():
    r, z, snr, use, ref = scene()
    # Give both populations sufficient complete reference support.
    use[:] = True
    z[10:] += .01 * r / 1000
    c, good, d = calibrate_components(r, z, snr, use, ref)
    assert good.all() and np.allclose(c[:10], .011) and np.allclose(c[10:], .021)
    assert len(d['components']) == 2


def test_successful_strict_calibration_is_preserved_exactly():
    from rainpulse_algo.radar.qc_engine.paired_range import calibrate
    r, z, snr, use, ref = scene()
    use[:] = True
    scalar, original = calibrate(r, z, snr, use, ref)
    c, good, report = calibrate_components(r, z, snr, use, ref)
    assert original['status'] == 'measured_consistent'
    assert np.array_equal(c, np.full(len(z), scalar)) and good.all()
    assert report['status'] == 'original_measured_consistent'
