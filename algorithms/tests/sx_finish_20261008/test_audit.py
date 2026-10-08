# ruff: noqa: E501, I001
from types import SimpleNamespace as NS
import copy
import numpy as np
import pytest
from rainpulse_algo.multiband.fusion_audit import Collector, audited, complete, paired_diagnostic, _CURRENT, request_context

def grid(rows=3, cols=4):
    return NS(method='quality_height_v2', grid_id='g', crs='EPSG:32651', west_m=0.0, south_m=0.0, spacing_m=1000.0, width=cols, height=rows, levels_m_msl=(100.0, 500.0))

def sweep():
    f = {'DBZH': np.ones((1, 9)), 'OBSERVED_MASK': np.array([[0, 1, 1, 1, 1, 1, 1, 1, 1]]), 'NO_ECHO_MASK': np.zeros((1, 9)), 'FUSION_ELIGIBLE_MASK': np.array([[0, 0, 0, 0, 0, 0, 0, 0, 1]]), 'DBZH_QC': np.ones((1, 9)), 'QUALITY_SCORE': np.ones((1, 9)), 'QC_ACTION': np.array([[0, 2, 3, 1, 1, 1, 1, 1, 1]]), 'FUSION_INPUT_REASON': np.array([[0, 0, 0, 4, 1, 2, 16, 0, 0]], np.uint16)}
    return NS(fields=f, number=3)

def test_partition_units_and_raw_independence():
    s = sweep()
    before = copy.deepcopy(s.fields)
    c = Collector({}, grid())
    c.native(s, NS(radar_id='x', band='X'), 0)
    assert c.records[0]['native'] == {k: 1 for k in c.records[0]['native']}
    c.native(s, NS(radar_id='x', band='X'), 0)
    assert c.records[0]['native_total'] == 9
    for k in before:
        np.testing.assert_array_equal(before[k], s.fields[k])

@pytest.mark.parametrize('bad', [2, np.nan, -1, 0.5])
def test_corrupt_mask_not_silently_reinterpreted(bad):
    s = sweep()
    s.fields['OBSERVED_MASK'] = s.fields['OBSERVED_MASK'].astype(float)
    s.fields['OBSERVED_MASK'][0, 1] = bad
    with pytest.raises(ValueError):
        Collector({}, grid()).native(s, NS(radar_id='x', band='X'), 0)

@pytest.mark.parametrize('rows', [1, 3, 7])
def test_sample_lattice_independent_of_tiles(rows):
    g = grid(7, 5)
    shape = (2, 7, 5)
    values = np.arange(np.prod(shape)).reshape(shape)
    layers = (values * 0.01, values, np.zeros(shape, int), values, values, values + 100, values, values + 1)
    a, b = (Collector({}, g), Collector({}, g))
    a.layers(layers, np.s_[0:7, :])
    for row in range(0, 7, rows):
        b.layers(tuple((v[:, row:min(row + rows, 7)] for v in layers)), np.s_[row:min(row + rows, 7), :])
    for key in ('value', 'source', 'height', 'score'):
        np.testing.assert_array_equal(a.samples[key], b.samples[key])
    assert a.layer_wins == b.layer_wins

@pytest.mark.parametrize('field,bad,bucket', [('age', 100, 'time_incomparable'), ('height', 900, 'height_incomparable'), ('resolution', 3000, 'resolution_incomparable')])
def test_pair_screens_do_not_change_samples(field, bad, bucket):
    c = Collector({}, grid())
    s = copy.deepcopy(c.samples)
    x = copy.deepcopy(s)
    for item in (s, x):
        item.update(source=np.zeros(24, int), age=np.zeros(24), height=np.full(24, 100.0), resolution=np.full(24, 1000.0), value=np.full(24, 25.0))
    x[field][0] = bad
    old = copy.deepcopy(x)
    report = paired_diagnostic(s, x)
    assert report['primary_partition'][bucket] == 1
    assert report['comparable_echo_pairs'] == 23
    for k in ('source', 'value', 'age', 'height', 'resolution'):
        np.testing.assert_array_equal(x[k], old[k])

def test_true_echo_clear_conflict_and_missing_are_distinct():
    c = Collector({}, grid())
    s = copy.deepcopy(c.samples)
    x = copy.deepcopy(s)
    for item in (s, x):
        item.update(source=np.zeros(24, int), age=np.zeros(24), height=np.full(24, 100.0), resolution=np.full(24, 1000.0), value=np.full(24, 25.0))
    x['value'][0] = np.nan
    x['source'][1] = -1
    r = paired_diagnostic(s, x)
    assert r['echo_noecho_conflicts'] == 1
    assert r['primary_partition']['missing_band_winner'] == 1
    x['mode'] = 'experimental_horizontal_max'
    assert paired_diagnostic(s, x)['status'] == 'not_height_aligned'

@pytest.mark.parametrize('failure', [False, True])
def test_context_resets_and_outputs_no_shared_arrays(failure):

    @audited
    def run():
        assert _CURRENT.get() == {}
        if failure:
            raise RuntimeError('cancel')
        return 1
    if failure:
        with pytest.raises(RuntimeError):
            run()
    else:
        assert run() == 1
    assert _CURRENT.get() is None

def test_requested_scope_attached_not_inferred():
    r = NS(arrays={}, metadata={})
    executor = NS(network=NS(stations={'s': NS(band='S'), 'x': NS(band='X')}))

    @request_context
    def run(executor, request):
        return complete(r)
    assert run(executor, {'payload': {'requested_radars': ['s', 'x']}}).metadata['requested_radars_by_band'] == {'S': ['s'], 'X': ['x']}
    assert _CURRENT.get() is None

def test_horizontal_uncertain_admission_is_not_rewritten_by_audit():
    s = sweep()
    s.fields['OBSERVED_MASK'][:] = 1
    s.fields['QC_ACTION'][:] = 3
    actual = np.ones((1, 9), bool)
    g = grid()
    g.method = 'experimental_horizontal_max'
    c = Collector({}, g)
    c.native(s, NS(radar_id='x', band='X'), 0, admitted=actual)
    assert c.records[0]['native']['qualified'] == 9
    assert c.records[0]['native']['withheld'] == 0
    assert c.records[0]['admitted_with_uncertainty_diagnostics'] == 9
