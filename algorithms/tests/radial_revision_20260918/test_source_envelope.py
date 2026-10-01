import numpy as np
import pytest
from .conftest import Native, load, evaluate


def case(dr=1000.):
    r = np.arange(0., 250000., dr)
    z = np.full((11, len(r)), np.nan, 'float32')
    seed = np.zeros(z.shape, bool); nominated = seed.copy()
    seed[5, (r >= 20000.) & (r < 40000.)] = True
    nominated |= seed
    for start, end in ((95000., 120000.), (170000., 175000.), (210000., 215000.)):
        nominated[5, (r >= start) & (r < end)] = True
    z[nominated] = 15.; z[seed] = 35.
    tail = np.zeros(z.shape, bool)
    tail[5, (r >= 125000.) & (r < 130000.)] = True
    z[tail] = 5.
    return Native(z, dr=dr, start=0.), seed, nominated, tail


def detect(n, seed, nominated, blocked=None):
    blocked = np.zeros(n.shape, bool) if blocked is None else blocked
    return load('radial_revision.source_envelope').detect(n, blocked, seed, nominated)


@pytest.mark.parametrize('dr', [250., 500., 1000.])
def test_original_full_extent_recovers_far_weak_tail_without_recursive_growth(dr):
    n, seed, nominated, tail = case(dr)
    raw = n.fields['DBZH'].copy()
    out, report = detect(n, seed, nominated)
    hit = out['RV2_ENVELOPE_MASK'] == 1
    assert hit[tail].all()  # >40 km from old contiguous anchor endpoint
    assert not hit[:, n.ranges >= 170000.].any()  # original seed remains the distance origin
    assert not (hit & seed).any()
    assert not (hit & ~n.field_available['DBZH']).any()
    parent = out['RV2_ENVELOPE_PARENT_ID'][tail]
    assert (parent > 0).all()
    assert np.isin(parent, out['RV2_ENVELOPE_SEED_ID'][seed]).all()
    assert not out['RV2_ENVELOPE_SEED_ID'][tail].any()
    assert np.all(out['RV2_ENVELOPE_END_M'][tail] <= 215000.)
    assert report['recursive_growth'] is False
    assert np.array_equal(raw, n.fields['DBZH'], equal_nan=True)


def test_nomination_extent_is_frozen_not_derived_from_accepted_tail():
    n, seed, nominated, tail = case()
    nominated[:, 160:] = False
    n.fields['DBZH'][5, 134:138] = 5.
    n.field_available['DBZH'] = np.isfinite(n.fields['DBZH'])
    out, _ = detect(n, seed, nominated)
    assert out['RV2_ENVELOPE_MASK'][tail].all()
    assert not out['RV2_ENVELOPE_MASK'][5, 134:138].any()


def test_protected_gap_broad_bridge_and_geometry_break_prevent_link():
    n, seed, nominated, tail = case()
    barrier = np.zeros(n.shape, bool); barrier[:, 70:75] = True
    assert not detect(n, seed, nominated, barrier)[0]['RV2_ENVELOPE_MASK'].any()
    n.gap_after[4] = True
    assert not detect(n, seed, nominated)[0]['RV2_ENVELOPE_MASK'].any()
    n, seed, nominated, tail = case()
    n.fields['DBZH'][2:9, 125:130] = 5.
    n.field_available['DBZH'] = np.isfinite(n.fields['DBZH'])
    assert not detect(n, seed, nominated)[0]['RV2_ENVELOPE_MASK'][tail].any()


def test_unseeded_and_too_short_seed_do_not_acquire_lineage():
    n, seed, nominated, _ = case()
    assert not detect(n, np.zeros(n.shape, bool), nominated)[0]['RV2_ENVELOPE_MASK'].any()
    seed[:, 25:] = False
    assert not detect(n, seed, nominated)[0]['RV2_ENVELOPE_MASK'].any()


def test_strong_core_removed_from_derived_reflectivity_does_not_erase_raw_parent():
    n, seed, nominated, tail = case()
    n.fields['DBZH_QC'] = n.fields['DBZH'].copy()
    n.fields['DBZH_QC'][seed] = np.nan
    n.field_available['DBZH_QC'] = np.isfinite(n.fields['DBZH_QC'])
    out, _ = detect(n, seed, nominated)
    assert out['RV2_ENVELOPE_MASK'][tail].all()
    assert out['RV2_ENVELOPE_SEED_ID'][seed].all()


def test_neighbor_weak_tail_inherits_original_parent_but_cannot_walk_angularly():
    n, seed, nominated, tail = case()
    n.fields['DBZH'][tail] = np.nan
    n.fields['DBZH'][6,125:130] = 5.
    n.fields['DBZH'][7,134:138] = 5.
    n.field_available['DBZH'] = np.isfinite(n.fields['DBZH'])
    out, _ = detect(n, seed, nominated)
    assert out['RV2_ENVELOPE_MASK'][6,125:130].all()
    assert not out['RV2_ENVELOPE_MASK'][7,134:138].any()
    assert np.isin(out['RV2_ENVELOPE_PARENT_ID'][6,125:130], out['RV2_ENVELOPE_SEED_ID'][seed]).all()
    assert not out['RV2_ENVELOPE_SEED_ID'][6].any()


def test_engine_envelope_is_persisted_and_audit_cannot_act(monkeypatch):
    n, seed, nomination, tail = case()
    module = load('radial_revision.fragment_line')
    def frozen_nomination(native, cfg, barred, **kwargs):
        return {'RV2_LINE_MASK': (nomination & ~barred).astype('uint8')}, {'status': 'frozen_fixture'}
    monkeypatch.setattr(module, 'detect', frozen_nomination)
    config = load('radial_revision.config').RadialRevisionConfig(
        mode='experiment_quarantine', fragment_line={'coherent_source_enabled': False,
            'residual_objects_enabled': True, 'source_envelope_enabled': True})
    out, _ = evaluate(n, config, seed)
    assert out['RV2_ENVELOPE_MASK'][tail].all()
    assert out['RV2_ACTION_PROPOSAL_MASK'][tail].all()
    validator = load('radial_revision.validation').validate_revision_fields
    validator(out, n.field_available['DBZH'], seed, np.zeros(n.shape, bool))
    tampered = {k:v.copy() for k,v in out.items()}
    tampered['RV2_ENVELOPE_PARENT_ID'][tail] = 999999
    with pytest.raises(ValueError):
        validator(tampered, n.field_available['DBZH'], seed, np.zeros(n.shape, bool))
    audit, _ = evaluate(n, config.model_copy(update={'mode': 'audit'}), seed)
    assert audit['RV2_ENVELOPE_MASK'][tail].all()
    assert not audit['RV2_ACTION_PROPOSAL_MASK'].any()


def test_weather_availability_is_recorded_without_inventing_absent_support():
    n, seed, _, _ = case()
    config = load('radial_revision.config').RadialRevisionConfig(
        mode='audit', fragment_line={'residual_objects_enabled': True, 'source_envelope_enabled': True})
    available = n.field_available['DBZH']
    out, _ = evaluate(n, config, seed, independent_weather_available=available)
    assert np.array_equal(out['RV2_INDEPENDENT_WEATHER_AVAILABLE_MASK'] == 1, available)
    assert not out['RV2_ACTION_PROPOSAL_MASK'].any()
    unknown, _ = evaluate(n, config, seed)
    assert not unknown['RV2_INDEPENDENT_WEATHER_AVAILABLE_MASK'].any()
