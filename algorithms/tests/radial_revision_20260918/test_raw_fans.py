import numpy as np
import pytest
from .conftest import Native, load, evaluate

P = 'RV2_RAW_FAN_'


def fixture(dr=1000.):
    r = np.arange(0., 300000., dr)
    z = np.full((30, len(r)), np.nan, 'float32')
    for a in range(8, 16):
        for start in (60000., 100000., 140000., 180000.):
            z[a, (r >= start) & (r < start + 2000.)] = 20.
    z[11, (r >= 181000.) & (r < 181000. + dr)] = 5.
    return Native(z, dr=dr, start=0.)


def detect(n, blocked=None, seed=None):
    blocked = np.zeros(n.shape, bool) if blocked is None else blocked
    seed = np.zeros(n.shape, 'uint32') if seed is None else seed
    module = load('radial_revision.raw_fans')
    out, report = module.detect(n, blocked, seed)
    module.validate(out, n.field_available['DBZH'], blocked, seed)
    return out, report


@pytest.mark.parametrize('dr', [250., 500., 1000.])
def test_sparse_broad_family_retains_original_sources_without_actions(dr):
    n = fixture(dr); source = np.zeros(n.shape, 'uint32')
    source[10, np.isfinite(n.fields['DBZH'][10])] = 9
    raw = n.fields['DBZH'].copy()
    out, report = detect(n, seed=source)
    measured = np.isfinite(raw)
    assert out[P+'MASK'][measured].all()
    assert len(np.unique(out[P+'ID'][measured])) == 1
    assert np.array_equal(out[P+'ORIGINAL_SEED_ID'], source)
    assert (out[P+'HOLD_REASON'][measured] == 1).all()
    assert report['action_gates'] == 0 and not report['source_claim']
    assert np.array_equal(n.fields['DBZH'], raw, equal_nan=True)


def test_protected_empty_gap_splits_identity_and_missing_remains_missing():
    n = fixture(); barrier = np.zeros(n.shape, bool); barrier[12, 120:122] = True
    out, _ = detect(n, barrier)
    assert out[P+'ID'][10, 100] != out[P+'ID'][10, 140]
    assert not out[P+'MASK'][~n.field_available['DBZH']].any()
    assert not out[P+'MASK'][barrier].any()
    assert (out[P+'LEFT_AVAILABLE_FRACTION'][out[P+'MASK'] == 1] == 0).all()


def test_no_recursive_angular_drift_or_scan_gap_bridge():
    n = fixture(); n.fields['DBZH'][:] = np.nan
    for a, start in ((8, 60000.), (10, 100000.), (12, 140000.), (14, 180000.)):
        n.fields['DBZH'][a:a+8, int(start/1000):int(start/1000)+2] = 20.
    n.field_available['DBZH'] = np.isfinite(n.fields['DBZH'])
    out, _ = detect(n)
    assert out[P+'ID'][8, 60] == out[P+'ID'][10, 100]
    assert out[P+'ID'][12, 140] != out[P+'ID'][8, 60]
    n.gap_after[11] = True
    out, _ = detect(n)
    assert out[P+'ID'][10, 100] != out[P+'ID'][12, 100]


def test_validator_rejects_forged_parent_extent_and_source_promotion():
    n = fixture(); out, _ = detect(n); module = load('radial_revision.raw_fans')
    source = np.zeros(n.shape, 'uint32'); barrier = np.zeros(n.shape, bool)
    wrong = {k: v.copy() for k, v in out.items()}
    wrong[P+'ORIGINAL_SEED_ID'][10, 60] = 42
    with pytest.raises(ValueError): module.validate(wrong, n.field_available['DBZH'], barrier, source)
    wrong = {k: v.copy() for k, v in out.items()}
    wrong[P+'REFERENCE_LEFT_DEG'][10, 60] -= 10.
    with pytest.raises(ValueError): module.validate(wrong, n.field_available['DBZH'], barrier, source)


def test_engine_diagnostic_does_not_modify_old_fits_or_actions_and_persists():
    n = fixture(); sources = np.isfinite(n.fields['DBZH'])
    for key, value in (('SNR', 25.), ('RHOHV', .99), ('ZDR', .5), ('PHIDP', 20.)):
        n.fields[key] = np.where(sources, value, np.nan).astype('float32')
        n.field_available[key] = sources.copy()
    module = load('radial_revision.config')
    cfg = module.RadialRevisionConfig(mode='experiment_quarantine', fragment_line={
        'raw_fragment_families_enabled': True, 'source_ledger_enabled': True,
        'raw_fan_families_enabled': True})
    out, _ = evaluate(n, cfg, sources)
    baseline, _ = evaluate(n, cfg.model_copy(update={'fragment_line':
        cfg.fragment_line.model_copy(update={'raw_fan_families_enabled': False})}), sources)
    for key, value in baseline.items():
        assert np.array_equal(out[key], value, equal_nan=True)
    assert out[P+'MASK'][sources].all()
    load('radial_revision.validation').validate_revision_fields(
        out, n.field_available['DBZH'], sources, np.zeros(n.shape, bool))
    config = load('config').SourceReviewConfig(narrow_enabled=False, radial_revision={
        'step': 3, 'mode': 'experiment_quarantine', 'fragment_line': cfg.fragment_line.model_dump()})
    _, arrays, _ = load('source').source_additions(n, config, sources, np.zeros(n.shape, 'float32'))
    arrays['SRC_REVIEW_REFERENCE_FOLD_ID'] = sources.astype('uint32')
    load('source_validation').validate_source_fields(arrays, n.field_available['DBZH'])
    assert np.array_equal(arrays[P+'MASK'], out[P+'MASK'])
    with pytest.raises(ValueError): module.FragmentLineConfig(raw_fan_families_enabled=True)


def test_single_measured_interior_fragment_is_retained_but_never_a_new_source():
    n = fixture(); n.fields['DBZH'][11] = np.nan; n.fields['DBZH'][11, 61] = 5.
    n.field_available['DBZH'] = np.isfinite(n.fields['DBZH'])
    out, _ = detect(n)
    assert out[P+'MASK'][11, 61]
    assert not out[P+'ORIGINAL_SEED_ID'][11, 61]
    assert out[P+'HOLD_REASON'][11, 61] == 1
