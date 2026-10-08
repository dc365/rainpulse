# ruff: noqa: E501, I001
import copy
import io
import json
import hashlib
import base64
import zlib
import os
from pathlib import Path
import numpy as np
import pytest
from types import SimpleNamespace as NS
from rainpulse_algo.multiband.comparison_finish import build
from rainpulse_algo.multiband.composite_sampling import Plan
from .test_provenance import scene
from . import render_reference as render

def results():
    joint = scene()
    shape = joint.arrays['CR_DBZH'].shape
    joint.metadata.update(contract='rainpulse.multiband.composite-v1', method='quality_select_at_height_then_vertical_max_v2', grid_id='g', crs='EPSG:32651', west_m=190000.0, south_m=2870000.0, spacing_m=1000.0, width=3, height=1, row_order='south_to_north', analysis_time='2026-08-28T00:06:00Z', input_cutoff='2026-08-28T00:06:00Z', cadence_seconds=360, operational_eligible=False, qpe_eligible=False, requested_radars=['s1', 'x1'], requested_radars_by_band={'S': ['s1'], 'X': ['x1']})
    a = joint.arrays
    a['CR_UNCERTAIN_DBZH'] = np.full(shape, np.nan)
    a['UNCERTAIN_MASK'] = np.zeros(shape, np.uint8)
    for n in ('WINNER_HEIGHT_MSL_M', 'WINNER_AGE_SECONDS', 'WINNER_RESOLUTION_M', 'WINNER_QUALITY_SCORE', 'OBSERVED_MIN_HEIGHT_MSL_M', 'OBSERVED_MAX_HEIGHT_MSL_M'):
        a[n] = np.where(np.isfinite(a['CR_DBZH']), 100.0, np.nan)
    for n in ('COVERAGE_SOURCE', 'VALID_LAYER_COUNT', 'UNRESOLVED_MASK', 'INPUT_QUALITY_REASON', 'AVAILABLE_BAND_BITS', 'QUALIFIED_BAND_BITS'):
        a[n] = np.zeros(shape, np.int32)
    s, x = (copy.deepcopy(joint), copy.deepcopy(joint))
    for r, idx in [(s, 0), (x, 2)]:
        source = r.metadata['sources'][idx].copy()
        source['index'] = 0
        r.metadata['sources'] = [source]
        r.arrays['WINNER_SOURCE'] = np.array([[0, 0, -1]], np.int32)
    return (joint, {'S': s, 'X': x})

def unpack_tile(objects, index, row, col):
    entry = index['tiles'][f'{row // 64}_{col // 64}']
    raw = objects[entry['path']]
    assert hashlib.sha256(raw).hexdigest() == entry['sha256']
    d = json.loads(raw)
    a = np.frombuffer(zlib.decompress(base64.b64decode(d['data'])), '<f8').reshape(d['height'], d['width'], len(d['fields']))
    return dict(zip(d['fields'], a[row % 64, col % 64].tolist(), strict=True))

def test_three_probes_native_identity_and_no_array_mutation(tmp_path):
    r, bands = results()
    before = copy.deepcopy((r, bands))
    count = 0

    def encode(a):
        nonlocal count
        count += 1
        return render.encode_arrays(a)
    api = NS(**{k: getattr(render, k) for k in ('png', 'quicklook', 'difference_quicklook', 'categorical_preview', 'LEVELS', 'COLORS')}, encode_arrays=encode)
    objects = build(r, bands, render_api=api)
    assert count == 1
    m = json.loads(objects['manifest.json'])
    arrays = np.load(io.BytesIO(objects['arrays.npz']), allow_pickle=False)
    assert m['source_contract'] == 'rainpulse.sx-source-v2'
    assert m['operational_eligible'] is False
    plan = Plan(r.metadata)
    for pid, c in [('s_only', bands['S']), ('x_only', bands['X']), ('sx_composite', r)]:
        entry = next((p for p in m['comparison']['products'] if p['product_id'] == pid))
        assert objects[entry['object_path']] == render.quicklook(c.arrays['CR_DBZH'])
        _, values = plan.sample(c.arrays['CR_DBZH'])
        np.testing.assert_equal(entry['map']['probe']['fields'][-1], 'CR_DBZH') if pid != 'sx_composite' else None
        assert objects[entry['map']['object_path']] == render.quicklook(values)
        idx = entry['map']['probe']
        assert len(idx['fields']) <= 24
        row, col = np.argwhere(np.isfinite(values[::-1]))[0]
        sample = unpack_tile(objects, idx, int(row), int(col))
        assert sample['CR_DBZH'] == values[::-1][row, col]
        si = int(sample['WINNER_SOURCE'])
        src = entry['provenance']['sources'][si]
        assert src['band'] == ('S' if pid == 's_only' else 'X') if pid != 'sx_composite' else src['band'] in ('S', 'X')
        assert src['sweep_number'] == sample['WINNER_SWEEP_NUMBER']
    assert arrays['WINNER_BAND'].dtype == np.uint8
    assert m['source_scope']['counts']['input_station_count'] == 2
    for result, old in [(r, before[0]), (bands['S'], before[1]['S']), (bands['X'], before[1]['X'])]:
        assert result.metadata == old.metadata
        for k in result.arrays:
            np.testing.assert_array_equal(result.arrays[k], old.arrays[k])
    for ref in m['audit'].values():
        assert hashlib.sha256(objects[ref['object_path']]).hexdigest() == ref['sha256']
    dst = Path(os.environ.get('SX_GOLDEN_OUTPUT', str(tmp_path / 'golden.json')))
    entry = next((p for p in m['comparison']['products'] if p['product_id'] == 'x_only'))
    idx = entry['map']['probe']
    _, values = plan.sample(bands['X'].arrays['CR_DBZH'])
    row, col = np.argwhere(np.isfinite(values[::-1]))[0]
    fixture = {'manifest': m, 'index': idx, 'x': (int(col) + 0.5) / idx['width'], 'y': (int(row) + 0.5) / idx['height'], 'tiles': {v['path']: base64.b64encode(objects[v['path']]).decode() for v in idx['tiles'].values()}}
    dst.write_text(json.dumps(fixture))
    assert len(idx['fields']) == 23

@pytest.mark.parametrize('missing', ['S', 'X', 'both'])
def test_missing_band_no_fabricated_source(missing):
    r, b = results()
    if missing == 'both':
        b = {'S': None, 'X': None}
        r.arrays['CR_DBZH'][:] = np.nan
        r.arrays['WINNER_SOURCE'][:] = -1
        r.arrays['NO_ECHO_MASK'][:] = 0
        r.arrays['OBSERVED_MASK'][:] = 0
        r.metadata['sources'] = []
    else:
        b[missing] = None
        r = copy.deepcopy(b['S' if missing == 'X' else 'X'])
    out = build(r, b, render_api=render)
    m = json.loads(out['manifest.json'])
    for band in ['S', 'X']:
        p = next((v for v in m['comparison']['products'] if v['product_id'] == band.lower() + '_only'))
        if b[band] is None:
            assert p['status'] == 'no_inputs' and 'map' not in p
    assert json.loads(out['source-selection-audit.json'])['meteorological_accuracy'] == 'not_evaluated'

@pytest.mark.parametrize('case', ['noecho_and_echo', 'noecho_and_uncertain', 'foreign_winner'])
def test_bad_output_cannot_be_encoded_as_valid(case):
    r, b = results()
    if case == 'noecho_and_echo':
        r.arrays['NO_ECHO_MASK'][0, 0] = 1
    elif case == 'noecho_and_uncertain':
        r.arrays['UNCERTAIN_MASK'][0, 2] = 1
    else:
        r.arrays['WINNER_SOURCE'][0, 0] = 99
    with pytest.raises(ValueError):
        build(r, b, render_api=render)

def test_real_repository_product_entrypoint():
    if os.environ.get('RAINPULSE_SUBSET_TEST') == '1':
        pytest.skip('full current product.py and full dependency environment not mounted; covered on Codex full checkout')
    from rainpulse_algo.multiband import product
    r, bands = results()
    objects = product.sx_comparison_objects(r, bands)
    manifest = json.loads(objects['manifest.json'])
    assert manifest['source_contract'] == 'rainpulse.sx-source-v2'
    assert objects['comparison/s_only.png'] == product.quicklook(bands['S'].arrays['CR_DBZH'])
    assert objects['comparison/x_only.png'] == product.quicklook(bands['X'].arrays['CR_DBZH'])
    assert objects['comparison/sx_composite.png'] == product.quicklook(r.arrays['CR_DBZH'])
