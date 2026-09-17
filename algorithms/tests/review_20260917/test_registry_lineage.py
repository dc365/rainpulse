from types import SimpleNamespace
import copy
import hashlib
import json
import numpy as np
import pytest
from .conftest import load, Group
from .test_background import samples, root_for

B = load('background')
R = load('background_registry')
L = load('lineage')


def registry_fixture():
    ss = samples()
    a, m = B.build_background(ss)
    entry = {k: m[k] for k in B.IDENTITY_FIELDS}
    entry.update(asset_uri='s3://synthetic/background.npz', asset_version=B.ASSET_VERSION,
                 asset_content_sha256=m['asset_content_sha256'])
    registry, receipt = R.create_registry([entry])
    return ss, a, registry, receipt


def test_registry_selects_asset_and_records_explicit_missing_hardware_binding():
    ss, a, registry, receipt = registry_fixture()
    root = root_for(ss[0])
    root.attrs.pop('hardware_config_version')
    root.attrs.pop('scan_strategy_id')
    calls = []
    def read(uri):
        calls.append(uri)
        return a
    out = R.load_verified_background(registry, root, receipt['asset_content_sha256'], R.REGISTRY_VERSION, load_asset=read)
    rec = out['sweep_000']['nonprecip_background_receipt']
    assert calls == ['s3://synthetic/background.npz']
    assert rec['verified'] and rec['binding_content_sha256'] == receipt['asset_content_sha256']
    assert rec['asset_content_sha256'] == B.digest(a)


@pytest.mark.parametrize('change', ['unknown_radar', 'unknown_config', 'wrong_hardware', 'content_changed'])
def test_registry_rejects_wrong_binding_or_selected_asset(change):
    ss, a, registry, receipt = registry_fixture()
    root = root_for(ss[0])
    if change == 'unknown_radar': root.attrs['radar_id'] = 'other'
    if change == 'unknown_config': root.attrs['radar_config_version'] = 'cfg-new'
    if change == 'wrong_hardware': root.attrs['hardware_config_version'] = 'hw-new'
    if change == 'content_changed': a['sweep_000__observed_count'][0, 0] += 1
    with pytest.raises(ValueError):
        R.load_verified_background(registry, root, receipt['asset_content_sha256'], R.REGISTRY_VERSION, load_asset=lambda _: a)


def test_registry_rejects_ambiguous_binding():
    _, _, _, receipt = registry_fixture()
    with pytest.raises(ValueError): R.create_registry(receipt['entries'] * 2)


def test_background_rejects_inconsistent_counts_even_with_recomputed_hash():
    ss = samples()
    a, _ = B.build_background(ss)
    a['sweep_000__echo_count'][0, 0] = 999
    with pytest.raises(ValueError): B.verify_background(a, root_for(ss[0]), B.digest(a), B.ASSET_VERSION)


def test_grid_selection_records_actual_indices_only():
    out = {k: np.full((2, 2), -1, 'int32') for k in ('SOURCE_RAY', 'SOURCE_GATE')}
    use = np.array([[True, False], [False, True]])
    mapping = SimpleNamespace(ray_index=np.array([[5, 6], [7, 8]]), gate_index=np.array([[10, 20], [30, 40]]), supported=np.ones((2, 2), bool))
    L.record_grid_selection(out, mapping, use)
    assert out['SOURCE_RAY'].tolist() == [[5, -1], [-1, 8]]
    assert out['SOURCE_GATE'].tolist() == [[10, -1], [-1, 40]]
    mapping.supported[0, 0] = False
    with pytest.raises(ValueError): L.record_grid_selection(out, mapping, use)


def cell_fixture():
    qc = Group({'sweep_000': Group({
        'VALID_MASK': np.ones((2, 3), 'uint8'), 'QPE_ELIGIBLE_MASK': np.ones((2, 3), 'uint8'),
        'DBZH_RAW': np.full((2, 3), 20., 'float32'), 'azimuth': np.array([0., 1.]),
        'range': np.array([1000., 2000., 3000.]), 'QC_ACTION': np.zeros((2, 3), 'uint8'),
    })}, attrs={'asset_id': 'qc', 'qc_parameters_sha256': 'a' * 64})
    grid = Group({
        'VALID_MASK': np.ones((1, 1), 'uint8'), 'SOURCE_SWEEP': np.zeros((1, 1), 'int16'),
        'SOURCE_RAY': np.ones((1, 1), 'int32'), 'SOURCE_GATE': np.full((1, 1), 2, 'int32'),
        'DBZH_QC': np.full((1, 1), 20., 'float32'), 'BEAM_HEIGHT': np.full((1, 1), 200., 'float32'),
        'polar': Group({'sweep_000': Group({'azimuth': np.array([0., 1.]), 'range': np.array([1000., 2000., 3000.])})}),
    }, attrs={'asset_id': 'grid', 'qc_asset_id': 'qc', 'coordinate_sha256': 'grid-sha',
              'qc_parameters_sha256': 'a' * 64, 'qc_review_extension_version': L.VERSION})
    fields = {'VALID_MASK': np.ones((1, 1), 'uint8'), 'CONTRIBUTOR_COUNT': np.ones((1, 1), 'uint8'), 'DBZH_QC': np.full((1, 1), 20., 'float32')}
    details = L.attach_mosaic_sources(fields, [{'radar_id': 'test'}], [SimpleNamespace(radar_id='test')], [grid], np.ones((1, 1, 1), 'float32'), {'test': 1})
    mosaic = Group(fields, attrs={'asset_id': 'mosaic', 'coordinate_sha256': 'grid-sha', 'contributors': details})
    return mosaic, grid, qc


def test_cell_trace_reconstructs_actual_raw_gate():
    mosaic, grid, qc = cell_fixture()
    result = L.trace_mosaic_cell(mosaic, {'grid': grid}, {'qc': qc}, row=0, column=0)
    assert result['status'] == 'verified_recorded_sources'
    assert result['contributors'][0]['ray'] == 1 and result['contributors'][0]['gate'] == 2
    assert result['DBZH_QC'] == result['reconstructed_DBZH_QC'] == 20.


@pytest.mark.parametrize('mutation', ['weight', 'eligible', 'grid_value', 'gate', 'hash', 'missing_index'])
def test_cell_trace_refuses_inconsistent_provenance(mutation):
    mosaic, grid, qc = cell_fixture()
    if mutation == 'weight': mosaic['CONTRIBUTOR_WEIGHT_001'][0, 0] = .5
    if mutation == 'eligible': qc['sweep_000']['QPE_ELIGIBLE_MASK'][1, 2] = 0
    if mutation == 'grid_value': grid['DBZH_QC'][0, 0] = 30.
    if mutation == 'gate': grid['SOURCE_GATE'][0, 0] = 999
    if mutation == 'hash': qc.attrs['qc_parameters_sha256'] = 'b' * 64
    if mutation == 'missing_index': grid['SOURCE_RAY'][0, 0] = -1
    with pytest.raises(ValueError): L.trace_mosaic_cell(mosaic, {'grid': grid}, {'qc': qc}, row=0, column=0)


def test_old_mosaic_without_recorded_weights_not_guessed():
    mosaic, grid, qc = cell_fixture()
    mosaic.attrs['contributors'] = [{'radar_id': 'test'}]
    with pytest.raises(ValueError): L.trace_mosaic_cell(mosaic, {'grid': grid}, {'qc': qc}, row=0, column=0)


def test_mosaic_mixed_extension_generations_rejected():
    mosaic, grid, _ = cell_fixture()
    grid.attrs['qc_review_extension_version'] = 'other'
    with pytest.raises(ValueError):
        L.attach_mosaic_sources({}, [{'radar_id': 'test'}], [SimpleNamespace(radar_id='test')], [grid], np.ones((1, 1, 1)), {'test': 1})


def inventory_fixture(tmp_path):
    t = '2026-09-17T08:24:00+00:00'
    attrs = {
        'raw': {'contract_name': 'rainpulse.normalized-radar-volume', 'asset_id': 'raw', 'input_asset_ids': ['outside-raw-boundary']},
        'qc': {'contract_name': 'rainpulse.qc-radar-volume', 'asset_id': 'qc', 'input_asset_ids': ['raw'], 'radar_id': 'test', 'volume_end_time_utc': t, 'qc_parameters_sha256': 'a'*64},
        'grid': {'contract_name': 'rainpulse.radar-grid', 'asset_id': 'grid', 'qc_asset_id': 'qc', 'volume_end_time_utc': t},
        'mosaic': {'contract_name': 'rainpulse.radar-mosaic', 'asset_id': 'mosaic', 'input_asset_ids': ['grid'], 'analysis_time': t},
        'qpe': {'contract_name': 'rainpulse.radar-qpe', 'asset_id': 'qpe', 'input_asset_ids': ['mosaic'], 'analysis_time': t},
    }
    entries = []
    for name, value in attrs.items():
        p = tmp_path/name
        p.mkdir()
        (p/'.zattrs').write_text(json.dumps(value))
        entries.append({'path': name, 'content_sha256': L.tree_digest(p)})
    return {'schema_version': 'rainpulse.review-lineage-v1', 'target_asset_id': 'qpe', 'analysis_time': t, 'assets': entries,
            'expected_qc': {'test': {'asset_id': 'qc', 'parameters_sha256': 'a'*64}}}


def test_inventory_proves_chain_but_not_unspecified_same_product(tmp_path):
    manifest = inventory_fixture(tmp_path)
    result = L.audit_inventory(manifest, base_dir=tmp_path)
    assert result['lineage_consistent']
    assert not result['same_product_comparison_proven']
    assert result['ancestry'] == ['grid', 'mosaic', 'qc', 'qpe', 'raw']


@pytest.mark.parametrize('mutation', ['stale', 'missing', 'digest', 'no_expected'])
def test_inventory_exposes_stale_missing_unverifiable_inputs(tmp_path, mutation):
    m = inventory_fixture(tmp_path)
    if mutation == 'stale': m['expected_qc']['test']['asset_id'] = 'new-qc'
    if mutation == 'missing': m['assets'] = [x for x in m['assets'] if x['path'] != 'qc']
    if mutation == 'digest': m['assets'][0]['content_sha256'] = '0'*64
    if mutation == 'no_expected': m['expected_qc'] = {}
    assert not L.audit_inventory(m, base_dir=tmp_path)['lineage_consistent']


def test_composite_and_hybrid_are_not_comparable():
    a = dict(field='DBZH_QC', aggregation='hybrid', sampled_level='lowest_usable', units='dBZ', display_threshold_dbz=-10, color_scale_id='same', coordinate_sha256='grid', analysis_time='same')
    b = dict(a, aggregation='vertical_maximum')
    assert not L.compare_definitions(a, b)['comparable']
    assert L.compare_definitions(a, a)['comparable']
