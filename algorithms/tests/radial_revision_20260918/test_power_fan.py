import numpy as np
from .conftest import Native, load


def fan():
    rng = np.random.default_rng(81)
    r = 50000. + np.arange(280)*1000.
    z = np.full((60, len(r)), np.nan, 'float32')
    z[15:35] = 5 + 20*np.log10(r/50000.) + rng.normal(0, 2.5, (20, len(r)))
    z[20, 70:74] = np.nan
    return Native(z)


def run(n, blocked=None):
    return load('radial_revision.power_fan').detect_power_fans(
        n, np.zeros(n.shape, bool) if blocked is None else blocked)


def test_wide_noisy_fan_is_detected_without_creating_observations():
    n = fan(); out = run(n)
    hit = out['RV2_POWER_FAN_MASK'] == 1
    assert hit.sum() > 3500
    assert not np.any(hit & ~n.field_available['DBZH'])
    assert not hit[:15].any() and not hit[35:].any()
    assert np.isfinite(out['RV2_POWER_FAN_RESIDUAL_DB'][hit]).all()


def test_weather_constant_or_curved_range_profile_is_not_a_power_fan():
    for mode in ('constant', 'curved'):
        n = fan()
        n.fields['DBZH'][15:35] = (25 if mode == 'constant' else
                                  10 + 25*np.exp(-((n.ranges-170000)/50000)**2))
        assert not run(n)['RV2_POWER_FAN_MASK'].any()


def test_protection_geometry_and_short_support_are_respected():
    n = fan(); blocked = np.zeros(n.shape, bool); blocked[20:25] = True
    out = run(n, blocked)['RV2_POWER_FAN_MASK'] == 1
    assert not np.any(out & blocked)
    n = fan(); n.geometry_good[14] = False
    assert not run(n)['RV2_POWER_FAN_MASK'].any()
    n = fan(); n.gap_after[14] = True
    assert not run(n)['RV2_POWER_FAN_MASK'].any()
    n = fan(); n.field_available['DBZH'][:,100:] = False
    assert not run(n)['RV2_POWER_FAN_MASK'].any()


def test_uniform_angular_field_cannot_become_fan():
    n = fan(); n.fields['DBZH'][:] = n.fields['DBZH'][16]
    n.field_available['DBZH'][:] = True
    assert not run(n)['RV2_POWER_FAN_MASK'].any()


def test_engine_action_validation_and_audit_mode():
    from .conftest import evaluate
    cfg = load('radial_revision.config').RadialRevisionConfig(step=1, mode='experiment_quarantine',
        fragment_line=dict(version='fragment-line-v3', isolated_quarantine_enabled=True,
            sparse_isolated_enabled=True, group_morphology_enabled=True, power_fan_enabled=True))
    n = fan(); out, _ = evaluate(n, cfg)
    hit = out['RV2_POWER_FAN_MASK'] == 1
    assert hit.sum() > 3500
    assert np.all(out['RV2_ACTION_PROPOSAL_MASK'][hit] == 1)
    validate = load('radial_revision.validation').validate_revision_fields
    validate(out, n.field_available['DBZH'], np.zeros(n.shape, bool), np.zeros(n.shape, bool))
    audit, _ = evaluate(n, cfg.model_copy(update={'mode': 'audit'}))
    assert audit['RV2_POWER_FAN_MASK'].sum() > 3500
    assert not audit['RV2_ACTION_PROPOSAL_MASK'].any()
    out['RV2_POWER_FAN_RESIDUAL_DB'][hit] = np.nan
    import pytest
    with pytest.raises(ValueError, match='power fan'):
        validate(out, n.field_available['DBZH'], np.zeros(n.shape, bool), np.zeros(n.shape, bool))
