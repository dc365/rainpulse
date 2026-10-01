import numpy as np
import pytest
from .conftest import load, evaluate as engine_evaluate
from .test_fan_joint import fixture, evaluate

P = 'RV2_FAN_STATE_'


def two_states():
    n, source, tail = fixture()
    weak = source & ((n.ranges[None, :] % 40000) >= 26000.)
    n.fields['DBZH'][weak | tail] -= 12.
    n.fields['SNR'] = np.where(weak | tail, 13., np.where(source, 25., np.nan)).astype('float32')
    n.field_available['SNR'] = np.isfinite(n.fields['SNR'])
    return n, source, tail


def diagnose(n, source, blocked=None):
    blocked = np.zeros(n.shape, bool) if blocked is None else blocked
    out, _ = evaluate(n, source, blocked)
    fields, report = load('radial_revision.fan_states').diagnose(n, blocked, out)
    out.update(fields)
    load('radial_revision.fan_states').validate(out, n.field_available['DBZH'], blocked)
    return out, report


def test_original_two_states_explain_weak_tail_without_qc_action():
    n, source, tail = two_states()
    raw = n.fields['DBZH'].copy()
    out, report = diagnose(n, source)
    assert not out['RV2_FAN_JOINT_QUALIFIED_MASK'][tail].any()
    assert out[P+'MATCH_MASK'][tail].all()
    assert (out[P+'STATE_ID'][tail] == 1).all()
    assert report['actions'] == 0 and report['source_claim'] is False
    assert np.array_equal(raw, n.fields['DBZH'], equal_nan=True)


def test_target_never_trains_its_own_weak_state():
    n, source, tail = two_states()
    out, _ = diagnose(n, source)
    n.fields['DBZH'][tail] -= 15.
    bad, _ = diagnose(n, source)
    assert not bad[P+'MATCH_MASK'][tail].any()
    # Perturb the target distribution; source discovery remains original-only.
    module = load('radial_revision.fan_states')
    r = n.ranges
    original = np.flatnonzero(source[8])
    assert module.states(r, 1000., n.fields['DBZH'][8], n.fields['SNR'][8], original, 12)[0][1]['INTERCEPT_DB'] == pytest.approx(8.)


def test_missing_snr_and_protected_gap_abstain():
    n, source, tail = two_states()
    n.fields['SNR'][tail] = np.nan
    n.field_available['SNR'][tail] = False
    out, _ = diagnose(n, source)
    assert not out[P+'MATCH_MASK'][tail].any()
    n, source, tail = two_states()
    blocked = np.zeros(n.shape, bool)
    blocked[:, 210:212] = True
    out, _ = diagnose(n, source, blocked)
    assert not out[P+'MATCH_MASK'][tail].any()


def test_weak_state_requires_independent_blocks_and_support():
    n, source, tail = two_states()
    weak = source & (n.fields['SNR'] < 20.)
    n.fields['SNR'][weak & (n.ranges[None, :] > 100000.)] = np.nan
    n.field_available['SNR'] = np.isfinite(n.fields['SNR'])
    out, _ = diagnose(n, source)
    assert not out[P+'MATCH_MASK'][tail].any()


def test_serialized_states_reject_forged_reference_and_identity():
    n, source, tail = two_states()
    out, _ = diagnose(n, source)
    for field in ('STATE_ID', 'SOURCE_ID', 'INTERCEPT_DB', 'REFERENCE_SUPPORT_M', 'SNR_LOW_DB'):
        bad = {k:v.copy() for k,v in out.items()}
        bad[P+field][tail] += 10
        with pytest.raises(ValueError):
            load('radial_revision.fan_states').validate(bad, n.field_available['DBZH'], np.zeros(n.shape, bool))


def test_engine_diagnostic_does_not_change_candidate_fits_or_actions():
    n, source, tail = two_states()
    for key, value in (('RHOHV', .99), ('ZDR', .5), ('PHIDP', 20.)):
        n.fields[key] = np.where(source, value, np.nan).astype('float32')
        n.field_available[key] = source.copy()
    module = load('radial_revision.config')
    cfg = module.RadialRevisionConfig(step=3, mode='experiment_quarantine', fragment_line={
        'raw_fragment_families_enabled':True, 'source_ledger_enabled':True,
        'raw_fan_families_enabled':True, 'fan_joint_enabled':True, 'fan_power_states_enabled':True})
    out, _ = engine_evaluate(n, cfg, source)
    baseline, _ = engine_evaluate(n, cfg.model_copy(update={'fragment_line':cfg.fragment_line.model_copy(update={'fan_power_states_enabled':False})}), source)
    for key, values in baseline.items():
        assert np.array_equal(out[key], values, equal_nan=True), key
    load('radial_revision.validation').validate_revision_fields(out, n.field_available['DBZH'], source, np.zeros(n.shape, bool))
    config = load('config').SourceReviewConfig(narrow_enabled=False, radial_revision=cfg.model_dump())
    _, arrays, _ = load('source').source_additions(n, config, source, np.zeros(n.shape, 'float32'))
    arrays['SRC_REVIEW_REFERENCE_FOLD_ID'] = source.astype('uint32')
    load('source_validation').validate_source_fields(arrays, n.field_available['DBZH'])
    assert P+'MATCH_MASK' in arrays and P+'ORIGINAL_SOURCE_SNR' in arrays
    with pytest.raises(ValueError):
        module.FragmentLineConfig(fan_power_states_enabled=True)
