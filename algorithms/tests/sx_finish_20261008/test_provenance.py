# ruff: noqa: E501, I001
from types import SimpleNamespace
import copy
import numpy as np
import pytest
from rainpulse_algo.multiband.comparison_provenance import scope, comparison_arrays

def scene(volume=False):
    sources = [dict(radar_id='s1', band='S', scan_id='scan-s', asset_sha256='a' * 64), dict(radar_id='s1', band='S', scan_id='scan-s', asset_sha256='a' * 64), dict(radar_id='x1', band='X', scan_id='scan-x', asset_sha256='b' * 64)]
    if not volume:
        for i, s in enumerate(sources):
            s.update(index=i, sweep_number=i)
    a = dict(CR_DBZH=np.array([[10.0, 30.0, np.nan]], 'float32'), WINNER_SOURCE=np.array([[0, 2, -1]], 'int32'), WINNER_RAY=np.array([[2, 3, -1]], 'int32'), WINNER_GATE=np.array([[4, 5, -1]], 'int32'), OBSERVED_MASK=np.ones((1, 3), 'uint8'), NO_ECHO_MASK=np.array([[0, 0, 1]], 'uint8'))
    if volume:
        a['WINNER_SWEEP_NUMBER'] = np.array([[7, 8, -1]], 'int32')
    return SimpleNamespace(arrays=a, metadata=dict(sources=sources, skipped=[], method='test'))

def test_station_identity_is_not_cut_index():
    r = scene()
    before = copy.deepcopy(r)
    s = scope(r)
    assert len(s['stations']) == 2
    assert s['counts']['input_station_count'] == 2
    assert s['counts']['source_cut_count'] == 3
    assert s['counts']['winner_station_count'] == 2
    assert s['counts']['requested_station_count'] is None
    a, all_scopes = comparison_arrays(r, {'S': None, 'X': None})
    assert a['WINNER_STATION_INDEX'].tolist() == [[0, 1, -1]]
    assert a['WINNER_BAND'].dtype == np.uint8
    assert a['WINNER_BAND'].tolist() == [[1, 2, 0]]
    for k in r.arrays:
        assert np.array_equal(r.arrays[k], before.arrays[k], equal_nan=True)
    assert r.metadata == before.metadata

def test_global_indices_not_filtered_or_reassigned():
    r = scene()
    r.arrays['CR_DBZH'][0, 1] = np.nan
    r.arrays['WINNER_SOURCE'][0, 1] = -1
    s = scope(r, band='S')
    assert len(s['sources']) == 3
    assert s['source_indices'] == [0, 1]
    assert [x['index'] for x in s['sources']] == [0, 1, 2]
    assert s['counts']['input_station_count'] == 1

def test_volume_winner_keeps_native_cut():
    r = scene(True)
    a, scopes = comparison_arrays(r, {'S': None, 'X': None})
    assert a['WINNER_SWEEP_NUMBER'].tolist() == [[7, 8, -1]]
    assert scopes['sx_composite']['counts']['source_cut_count'] is None

@pytest.mark.parametrize('bad', [0.5, 99, np.nan, -2])
def test_bad_source_rejected(bad):
    r = scene()
    r.arrays['WINNER_SOURCE'] = r.arrays['WINNER_SOURCE'].astype(float)
    r.arrays['WINNER_SOURCE'][0, 0] = bad
    with pytest.raises(ValueError):
        scope(r)

def test_wrong_cut_rejected():
    r = scene()
    r.arrays['WINNER_SWEEP_NUMBER'] = np.array([[9, 2, -1]])
    with pytest.raises(ValueError):
        scope(r)

def test_foreign_band_winner_rejected():
    with pytest.raises(ValueError):
        scope(scene(), band='S')

def test_components_have_independent_source_namespaces():
    joint = scene()
    s = scene()
    x = scene()
    for r, i in [(s, 0), (x, 2)]:
        src = r.metadata['sources'][i].copy()
        src['index'] = 0
        r.metadata['sources'] = [src]
        r.arrays['WINNER_SOURCE'] = np.array([[0, 0, -1]], 'int32')
    a, scopes = comparison_arrays(joint, {'S': s, 'X': x})
    assert a['WINNER_SOURCE_X_ONLY'].tolist() == [[0, 0, -1]]
    assert scopes['x_only']['sources'][0]['radar_id'] == 'x1'
    assert scopes['s_only']['sources'][0]['radar_id'] == 's1'
