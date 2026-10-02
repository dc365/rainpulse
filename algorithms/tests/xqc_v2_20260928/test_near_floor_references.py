"""Sparse threshold excursions must use independent, measured RAW references."""
import numpy as np
import pytest

from rainpulse_algo.multiband.xqc_v2.geometry import adapt
from rainpulse_algo.multiband.xqc_v2.source_blocks import detect

from .helpers import config, fixture


def sample(*, rays=360, dr=75.):
    v, row = fixture(rays=rays, gates=int(75000/dr), dr=dr)
    f = v.sweeps[0].fields
    r = v.sweeps[0].range_m
    f['DBZH'][:] = np.nan
    f['SNRH'][:] = -7.
    f['SNRH'][row] = 2.1
    excursions = (np.arange(len(r)) % 23 == 0) & (r >= 15000)
    f['SNRH'][row, excursions] = 3.
    f['DBZH'][row] = f['SNRH'][row] + 20*np.log10(r/1000) - 20
    f['OBSERVED_MASK'][:] = np.isfinite(f['DBZH'])
    f['NO_ECHO_MASK'][:] = 0
    return v, row, excursions


def evaluate(v, *, research=False, protected=None):
    cfg = config(radial_source_enabled=True, noise_censor_snr_db=3.)
    view = adapt(v.sweeps[0], cfg)
    p = np.zeros(view.sweep.shape, bool) if protected is None else protected[view.order]
    kwargs = {'near_floor_references': True} if research else {}
    mask, record = detect(view.sweep, cfg, protected=p, **kwargs)
    return view.restore(mask), record


def test_raw_near_floor_references_qualify_sparse_excursions_only():
    v, row, excursions = sample()
    original = v.sweeps[0].fields['DBZH'].copy()
    assert not evaluate(v)[0][row, excursions].any()
    mask, record = evaluate(v, research=True)
    assert mask[row, excursions].sum() > 20
    assert not mask[row, ~excursions].any()
    assert record['diagnostic_only'] is True
    np.testing.assert_array_equal(v.sweeps[0].fields['DBZH'], original)


@pytest.mark.parametrize('change', ['missing_snr', 'wide', 'weather', 'protected'])
def test_independent_receiver_and_weather_guards_remain(change):
    v, row, _ = sample()
    f = v.sweeps[0].fields
    p = np.zeros(f['DBZH'].shape, bool)
    if change == 'missing_snr':
        f['SNRH'][row-3:row] = np.nan
    elif change == 'wide':
        for other in range(row-4, row+5):
            f['SNRH'][other] = f['SNRH'][row]
            f['DBZH'][other] = f['DBZH'][row]
    elif change == 'weather':
        f['DBZH'][row] = 25.
    else:
        p[row] = True
    assert not evaluate(v, research=True, protected=p)[0].any()


def test_target_and_guard_cannot_train_amplified_echo():
    v, row, _ = sample()
    r = v.sweeps[0].range_m
    target = (r >= 35000) & (r < 40000)
    v.sweeps[0].fields['DBZH'][row, target] += 8.
    mask, _ = evaluate(v, research=True)
    assert mask[row].any()
    assert not mask[row, target].any()


@pytest.mark.parametrize('rays,dr,offset', [(360,75.,0.), (720,150.,239.9)])
def test_angle_resolution_and_distance_spacing_are_not_station_exceptions(rays, dr, offset):
    v, row, excursions = sample(rays=rays, dr=dr)
    v.sweeps[0].azimuth_deg[:] = (v.sweeps[0].azimuth_deg + offset) % 360
    mask, _ = evaluate(v, research=True)
    assert mask[row, excursions].sum() > 10
    assert not mask[row, ~excursions].any()


def test_excursion_outside_independent_raw_bounds_stays_unqualified():
    v, row, excursions = sample()
    f = v.sweeps[0].fields
    f['SNRH'][row, excursions] = 5.
    r = v.sweeps[0].range_m
    f['DBZH'][row, excursions] = 5. + 20*np.log10(r[excursions]/1000) - 20
    assert not evaluate(v, research=True)[0].any()


def weak_family():
    v, row, excursions = sample()
    f = v.sweeps[0].fields
    for other in range(row-10, row+11):
        f['SNRH'][other] = f['SNRH'][row]
        f['DBZH'][other] = f['DBZH'][row]
    f['OBSERVED_MASK'][:] = np.isfinite(f['DBZH'])
    return v, row, excursions


def family(v, protected=None, *, research=True):
    from rainpulse_algo.multiband.xqc_v2.source_fans import detect as detect_fans
    cfg = config(radial_source_enabled=True, noise_censor_snr_db=3.)
    view = adapt(v.sweeps[0], cfg)
    p = np.zeros(view.sweep.shape, bool) if protected is None else protected[view.order]
    kwargs = {'near_floor_references': True} if research else {}
    mask, record = detect_fans(view.sweep, cfg, protected=p, **kwargs)
    return view.restore(mask), record


@pytest.mark.parametrize('shift,elevation', [(0,.47), (239,3.36), (45,14.55)])
def test_complete_weak_family_uses_family_shoulders_and_heldout_models(shift, elevation):
    v, row, excursions = weak_family()
    for name, values in v.sweeps[0].fields.items():
        v.sweeps[0].fields[name] = np.roll(values, shift, axis=0)
    row = (row+shift) % len(v.sweeps[0].azimuth_deg)
    v.sweeps[0].elevation_deg[:] = elevation
    assert not evaluate(v, research=True)[0].any()  # narrow width remains unchanged
    assert not family(v, research=False)[0][row, excursions].any()
    mask, record = family(v)
    assert mask[row, excursions].sum() > 20
    assert not mask[row, ~excursions].any()
    assert record['maximum_family_width_deg'] == 45.
    assert record['diagnostic_only'] is True


@pytest.mark.parametrize('kind', ['range_weather', 'unknown', 'protected'])
def test_weak_family_keeps_weather_unknown_and_original_object_protection(kind):
    v, row, _ = weak_family()
    f = v.sweeps[0].fields
    p = np.zeros(f['DBZH'].shape, bool)
    if kind == 'range_weather':
        f['DBZH'][row-10:row+11] = 25.
    elif kind == 'unknown':
        f['SNRH'][:] = np.nan
    else:
        p[row-10:row+11] = True
    assert not family(v, p)[0].any()


def candidate_config(**changes):
    from .test_centered_pulsing_morphology import policy
    return config(
        mode='quarantine', receiver_enabled=False, radial_objects_enabled=False,
        clutter_enabled=False, isolation_enabled=False,
        morphology=policy().model_dump(mode='json'), noise_censor_snr_db=3.,
        near_floor_source_candidates_enabled=True, **changes,
    )


def test_normal_candidate_owner_withholds_without_confirming_pollution():
    from rainpulse_algo.multiband.quality import x_qc

    from .helpers import station
    v, row, excursions = weak_family()
    original = v.sweeps[0].fields['DBZH'].copy()
    out = x_qc(v, station(candidate_config()), 'a'*64).sweeps[0].fields
    selected = out['XQC_NEAR_FLOOR_SOURCE_MASK'].astype(bool)
    assert selected[row, excursions & (v.sweeps[0].range_m < 40000)].sum() > 10
    assert not (selected & out['XQC_MORPHOLOGY_COUNTEREXAMPLE_MASK'].astype(bool)).any()
    assert np.all(out['QC_ACTION'][selected] == 3)
    assert np.isnan(out['DBZH_QC'][selected]).all()
    assert not out['REFLECTIVITY_ELIGIBLE_FOR_CR'][selected].any()
    np.testing.assert_array_equal(v.sweeps[0].fields['DBZH'], original)


def test_compact_protection_failure_abstains_new_module_only():
    from rainpulse_algo.multiband.xqc_v2.core import evaluate_cut
    cfg = candidate_config()
    cfg = cfg.model_copy(update={'morphology':cfg.morphology.model_copy(update={'maximum_work':1})})
    v, _, _ = weak_family()
    ev = evaluate_cut(v.sweeps[0], v.metadata, cfg)
    assert not ev.arrays['XQC_NEAR_FLOOR_SOURCE_MASK'].any()
    status = ev.record['module_records']['near_floor_source']['status']
    assert status == 'UNAVAILABLE_COMPACT_PROTECTION'


def test_candidate_action_budget_never_restores_visible_source():
    from rainpulse_algo.multiband.quality import x_qc

    from .helpers import station
    v, _, _ = weak_family()
    profile = station(candidate_config(maximum_new_exclusion_fraction=.001))
    out = x_qc(v, profile, 'a'*64).sweeps[0].fields
    selected = out['XQC_NEAR_FLOOR_SOURCE_MASK'].astype(bool)
    assert selected.any()
    assert out['XQC_BUDGET_WITHHELD_MASK'][selected].all()
    assert np.isnan(out['DBZH_QC'][selected]).all()
    assert not out['REFLECTIVITY_ELIGIBLE_FOR_CR'][selected].any()


def test_activation_requires_explicit_receiver_and_compact_contract():
    from pydantic import ValidationError
    assert not config().near_floor_source_candidates_enabled
    with pytest.raises(ValidationError):
        config(near_floor_source_candidates_enabled=True, noise_censor_snr_db=3.)


def test_new_summary_resource_abstention_keeps_existing_evidence():
    from rainpulse_algo.multiband.xqc_v2.core import evaluate_cut
    v, _, _ = weak_family()
    ev = evaluate_cut(v.sweeps[0], v.metadata, candidate_config(source_maximum_summary_bytes=4096))
    assert not ev.arrays['XQC_NEAR_FLOOR_SOURCE_MASK'].any()
    assert ev.record['module_records']['near_floor_source']['status'] == 'RESOURCE_LIMIT_ABSTAINED'
    assert ev.record['module_records']['morphology']['status'] == 'EVALUATED'


def test_audit_mode_preserves_existing_display_and_admission():
    from rainpulse_algo.multiband.quality import x_qc

    from .helpers import station
    v, _, _ = weak_family()
    cfg = candidate_config().model_copy(update={'mode':'audit'})
    old_cfg = cfg.model_copy(update={'near_floor_source_candidates_enabled':False})
    old = x_qc(v, station(old_cfg), 'a'*64).sweeps[0].fields
    new = x_qc(v, station(cfg), 'a'*64).sweeps[0].fields
    assert new['XQC_NEAR_FLOOR_SOURCE_MASK'].any()
    for name in ['DBZH_QC','DBZH_QC_DISPLAY','QC_ACTION','REFLECTIVITY_ELIGIBLE_FOR_CR']:
        np.testing.assert_array_equal(old[name], new[name])


def test_json_contract_preserves_existing_guards_and_new_dependencies():
    import json
    from pathlib import Path

    import jsonschema
    schema = json.loads((Path(__file__).resolve().parents[3]/
                         'contracts/internal/multiband/x-qc-v2.schema.json').read_text())
    data = candidate_config().model_dump(mode='json')
    jsonschema.validate(data, schema)
    for change in [{'noise_censor_snr_db':None}, {'morphology':None},
                   {'near_floor_source_candidates_enabled':'true'}]:
        with pytest.raises(jsonschema.ValidationError):
            jsonschema.validate(data|change, schema)


def test_new_pass_cannot_restart_source_model_allowance(monkeypatch):
    from rainpulse_algo.multiband.xqc_v2 import radial_source
    from rainpulse_algo.multiband.xqc_v2.core import evaluate_cut
    v, _, _ = weak_family()
    def exhausted(s, cfg, **kwargs):
        return np.zeros(s.shape, bool), {'status':'EVALUATED',
            'work':{'model_trials':cfg.source_maximum_trials, 'model_records':0}}
    monkeypatch.setattr(radial_source, 'detect', exhausted)
    ev = evaluate_cut(v.sweeps[0], v.metadata, candidate_config())
    assert not ev.arrays['XQC_NEAR_FLOOR_SOURCE_MASK'].any()
    record = ev.record['module_records']['near_floor_source']
    assert record['status'] == 'RESOURCE_LIMIT_ABSTAINED'
    assert record['work'] == {}


def test_pipeline_resource_abstention_is_reported_instead_of_crashing(monkeypatch):
    from rainpulse_algo.multiband.quality import x_qc
    from rainpulse_algo.multiband.xqc_v2 import pipeline
    from rainpulse_algo.multiband.xqc_v2.core import empty

    from .helpers import station
    v, _, _ = weak_family()
    monkeypatch.setattr(pipeline, 'evaluate_cut', lambda cut, *a, **kw:
                        empty(cut, 'RESOURCE_OR_GEOMETRY_ABSTAINED', 'frozen evidence limit'))
    result = x_qc(v, station(candidate_config()), 'a'*64).sweeps[0]
    assert result.xqc_diagnostics['status'] == 'RESOURCE_OR_GEOMETRY_ABSTAINED'
    assert result.xqc_diagnostics['detail'] == 'frozen evidence limit'
    assert not result.fields['XQC_NEAR_FLOOR_SOURCE_MASK'].any()


def test_new_record_overflow_keeps_completed_legacy_evidence(monkeypatch):
    from rainpulse_algo.multiband.xqc_v2 import core
    from rainpulse_algo.multiband.xqc_v2.core import evaluate_cut
    v, _, _ = weak_family()
    cfg = candidate_config()
    old = evaluate_cut(v.sweeps[0], v.metadata,
                       cfg.model_copy(update={'near_floor_source_candidates_enabled':False}))
    original = core.json.dumps
    def overflow(value, *args, **kwargs):
        near = (value.get('module_records', {}).get('near_floor_source', {})
                if isinstance(value, dict) else {})
        if near.get('status') == 'EVALUATED':
            return 'x' * (cfg.maximum_evidence_bytes+1)
        return original(value, *args, **kwargs)
    monkeypatch.setattr(core.json, 'dumps', overflow)
    new = evaluate_cut(v.sweeps[0], v.metadata, cfg)
    for name in old.arrays:
        np.testing.assert_array_equal(old.arrays[name], new.arrays[name])
    assert not new.arrays['XQC_NEAR_FLOOR_SOURCE_MASK'].any()
    status = new.record['module_records']['near_floor_source']['status']
    assert status == 'EVIDENCE_BUDGET_ABSTAINED'
    assert new.record['module_records']['morphology']['status'] == 'EVALUATED'


def test_reference_only_blocks_do_not_consume_target_fit_budget():
    from rainpulse_algo.multiband.xqc_v2.source_summary import SourceStatistics
    v, row, _ = sample()
    v.sweeps[0].fields['SNRH'][row] = 2.1
    cfg = config(radial_source_enabled=True, noise_censor_snr_db=3.)
    sweep = adapt(v.sweeps[0], cfg).sweep
    stats = SourceStatistics.build(sweep, cfg)
    mask, record = detect(sweep, cfg, protected=np.zeros(sweep.shape, bool),
                          prepared=stats, near_floor_references=True)
    assert not mask.any() and record['models'] == []
    assert stats.trials == 0


def test_proven_target_is_not_refitted_against_other_reference_families(monkeypatch):
    from rainpulse_algo.multiband.xqc_v2 import source_blocks
    from rainpulse_algo.multiband.xqc_v2.reference_fit import ReferenceFit
    v, row, _ = sample()
    fields = v.sweeps[0].fields
    ranges = v.sweeps[0].range_m
    # Multiple distinct compatible reference families; each successful proof
    # covers every eligible gate of its held-out target block.
    fields['SNRH'][row] = 3. + ((ranges // 5000).astype(int) % 3) * .7
    fields['DBZH'][row] = fields['SNRH'][row] + 20*np.log10(ranges/1000) - 20
    cfg = config(radial_source_enabled=True, noise_censor_snr_db=3.)
    sweep = adapt(v.sweeps[0], cfg).sweep
    calls = []
    def proven(self, key, factory):
        calls.append(key)
        self.stats.trial()
        return ReferenceFit(0., 3.7, 100, 60000., (-100., 100., 0., 10.))
    monkeypatch.setattr(source_blocks.ReferenceFits, 'get', proven)
    mask, record = detect(sweep, cfg, protected=np.zeros(sweep.shape, bool),
                          fan=True, near_floor_references=True)
    assert mask.any() and len(record['models']) > 3
    assert len(calls) == len(record['models'])


@pytest.mark.parametrize('fan,quantile', [(False,50), (True,50), (True,90)])
def test_fit_order_optimization_preserves_frozen_near_floor_proofs(fan, quantile):
    import subprocess
    import types
    source = subprocess.check_output([
        'git', 'show', 'f49cedac0775eacac3a42c96215f1d4cce1c2e84:'
        'algorithms/rainpulse_algo/multiband/xqc_v2/source_blocks.py'], text=True)
    module = types.ModuleType('rainpulse_algo.multiband.xqc_v2._prior_fit_order')
    module.__package__ = 'rainpulse_algo.multiband.xqc_v2'
    exec(compile(source, '<prior-fit-order>', 'exec'), module.__dict__)
    v, row, _ = sample()
    cfg = config(radial_source_enabled=True, noise_censor_snr_db=3.)
    sweep = adapt(v.sweeps[0], cfg).sweep
    protected = np.zeros(sweep.shape, bool)
    protected[row, 500:510] = True
    before = sweep.digest
    kwargs = dict(protected=protected, fan=fan, response_quantile=quantile,
                  near_floor_references=True)
    old_mask, old_record = module.detect(sweep, cfg, **kwargs)
    new_mask, new_record = detect(sweep, cfg, **kwargs)
    np.testing.assert_array_equal(new_mask, old_mask)
    assert new_record == old_record
    assert sweep.digest == before


def test_prior_proven_targets_stay_available_as_raw_training_references():
    v, row, excursions = sample()
    cfg = config(radial_source_enabled=True, noise_censor_snr_db=3.)
    view = adapt(v.sweeps[0], cfg)
    sweep = view.sweep
    kwargs = dict(protected=np.zeros(sweep.shape, bool), fan=True,
                  near_floor_references=True)
    old_mask, _ = detect(sweep, cfg, **kwargs)
    excluded = old_mask.copy()
    # Retain only one held-out action block. All other previously proved gates
    # must still train it; their deletion from signal would destroy the proof.
    excluded[:, (sweep.ranges>=35000)&(sweep.ranges<40000)] = False
    assert (old_mask&~excluded).any()
    actual, record = detect(sweep, cfg, target_exclusion=excluded, **kwargs)
    np.testing.assert_array_equal(actual, old_mask&~excluded)
    assert not (actual&excluded).any()
    assert sum(m['target_gates'] for m in record['models']) == int(actual.sum())


def test_primary_mode_keeps_same_family_mask_without_duplicate_action_proofs(monkeypatch):
    from rainpulse_algo.multiband.xqc_v2 import source_fans
    v, row, _ = weak_family()
    cfg = config(radial_source_enabled=True, noise_censor_snr_db=3.)
    sweep = adapt(v.sweeps[0], cfg).sweep
    kwargs = dict(protected=np.zeros(sweep.shape, bool), near_floor_references=True)
    before = sweep.digest
    actual, record = source_fans.detect(sweep, cfg, **kwargs)
    assert actual.any()
    # Reproduce the prior two complete action passes with identical RAW fits.
    original = source_fans.detect_blocks
    def repeated(*args, **kw):
        kw.pop('target_exclusion', None)
        return original(*args, **kw)
    monkeypatch.setattr(source_fans, 'detect_blocks', repeated)
    expected, prior = source_fans.detect(sweep, cfg, **kwargs)
    np.testing.assert_array_equal(actual, expected)
    assert record['primary_mode']['source_gates'] == 0
    assert prior['primary_mode']['source_gates'] > 0
    assert record['models'] == prior['models']
    assert sweep.digest == before


@pytest.mark.parametrize('wrong', ['legacy', 'shape', 'dtype'])
def test_prior_target_exclusion_is_strictly_scoped(wrong):
    v, _, _ = sample()
    cfg = config(radial_source_enabled=True, noise_censor_snr_db=3.)
    sweep = adapt(v.sweeps[0], cfg).sweep
    mask = np.zeros(sweep.shape, bool)
    if wrong == 'shape':
        mask = mask[:1]
    if wrong == 'dtype':
        mask = mask.astype('uint8')
    with pytest.raises(ValueError, match='prior proven target exclusion'):
        detect(sweep, cfg, protected=np.zeros(sweep.shape, bool),
               near_floor_references=wrong!='legacy', target_exclusion=mask)
