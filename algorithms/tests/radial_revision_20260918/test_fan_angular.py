import numpy as np
from .conftest import load
from .test_fan_joint import fixture, evaluate


def angular_case():
    n, source, tail = fixture()
    gain = .5*(np.arange(n.shape[0])-10.)[:,None]
    n.fields['DBZH'] += gain
    n.fields['SNR'] = np.where(source | tail, 25.+gain, np.nan).astype('float32')
    n.field_available['SNR'] = np.isfinite(n.fields['SNR'])
    source[10] = False
    target = tail.copy();target[:10] = False;target[11:] = False
    return n, source, target


def diagnose(n, source, target, blocked=None):
    blocked = np.zeros(n.shape, bool) if blocked is None else blocked
    group, _ = evaluate(n, source, blocked)
    return load('radial_revision.fan_angular').diagnose(n, blocked, group, target_mask=target)


def test_original_references_bracket_and_independently_validate_angular_gain():
    n, source, target = angular_case()
    raw = n.fields['DBZH'].copy()
    out, report = diagnose(n, source, target)
    assert out['MODEL_AVAILABLE_MASK'][target].all()
    assert out['MATCH_MASK'][target].all()
    assert (out['REFERENCE_RAYS'][target] >= 3).all()
    assert (out['VALIDATION_P90_DB'][target] < .01).all()
    assert report['actions'] == 0 and report['source_claim'] is False
    assert np.array_equal(raw,n.fields['DBZH'],equal_nan=True)


def test_target_cannot_fit_or_relay_an_angular_curve():
    n, source, target = angular_case()
    before, _ = diagnose(n, source, target)
    n.fields['DBZH'][target] -= 12.
    after, _ = diagnose(n, source, target)
    assert after['MODEL_AVAILABLE_MASK'][target].all()
    assert not after['MATCH_MASK'][target].any()
    assert np.array_equal(before['INTERCEPT_DB'],after['INTERCEPT_DB'],equal_nan=True)


def test_one_sided_sources_and_two_reference_rays_are_insufficient():
    n, source, target = angular_case()
    source[:10] = False
    out,_ = diagnose(n,source,target)
    assert not out['MODEL_AVAILABLE_MASK'][target].any()
    n, source, target = angular_case()
    source[8] = False;source[12:] = False
    out,_ = diagnose(n,source,target)
    assert not out['MODEL_AVAILABLE_MASK'][target].any()


def test_original_interior_ray_can_veto_curved_or_unrelated_sources():
    n, source, target = angular_case()
    n.fields['DBZH'][9] += 10.
    out,_ = diagnose(n,source,target)
    assert not out['MODEL_AVAILABLE_MASK'][target].any()


def test_native_gap_protected_path_and_unavailable_snr_cannot_supply_evidence():
    n, source, target = angular_case()
    n.gap_after[9] = True
    out,_ = diagnose(n,source,target)
    assert not out['MODEL_AVAILABLE_MASK'][target].any()
    n, source, target = angular_case()
    blocked = np.zeros(n.shape,bool);blocked[9,210:212] = True
    out,_ = diagnose(n,source,target,blocked)
    assert not out['MODEL_AVAILABLE_MASK'][target].any()
    n, source, target = angular_case()
    # Finite SNR with false actual availability is still unavailable.
    n.field_available['SNR'][:] = False
    out,_ = diagnose(n,source,target)
    assert not out['MODEL_AVAILABLE_MASK'][target].any()
