"""Read-only replay must be bounded before loading objects or array headers."""

import importlib.util
import io
from pathlib import Path

import numpy as np
import pytest


def audit():
    path = Path(__file__).resolve().parents[3] / "scripts/audit_x_polar_morphology.py"
    spec = importlib.util.spec_from_file_location("morphology_audit", path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def test_stdin_task_limit_precedes_json_parsing():
    module = audit()
    assert module.read_task(io.BytesIO(b'{"id":"test"}')) == {"id": "test"}
    with pytest.raises(ValueError, match="task JSON"):
        module.read_task(io.BytesIO(b" " * (1024**2 + 1)))


def test_native_array_shapes_checked_for_every_field():
    module = audit()
    fields = {
        "DBZH_RAW": np.zeros((4, 5)),
        "azimuth": np.zeros(4),
        "range_m": np.zeros(5),
        "elevation": np.zeros(4),
        "ray_time_epoch": np.zeros(4),
    }
    module.validate_native_arrays(fields, (4, 5))
    with pytest.raises(ValueError, match="shape"):
        module.validate_native_arrays({**fields, "XQC_PROPOSED_MASK": np.zeros((4, 6))}, (4, 5))
    with pytest.raises(ValueError, match="shape"):
        module.validate_native_arrays({**fields, "elevation": np.zeros((4, 5))}, (4, 5))


def test_declared_size_rejected_before_object_download():
    from types import SimpleNamespace

    class Objects:
        session = SimpleNamespace(
            index=SimpleNamespace(logical={"native.npz": ("pack", 0, 65 * 1024**2)})
        )

        def __getitem__(self, key):
            pytest.fail("oversize object downloaded before preflight")

    with pytest.raises(ValueError, match="byte budget"):
        audit().load_bounded(Objects(), ["native.npz"], 64 * 1024**2)


def test_staging_limits_pack_count_even_when_byte_count_is_zero():
    from types import SimpleNamespace

    session = SimpleNamespace(
        index=SimpleNamespace(schema="3.0", size=0, physical=dict.fromkeys(range(257)))
    )
    with pytest.raises(ValueError, match="pack count"):
        audit().check_staging_budget(session, 512 * 1024**2)


def test_missing_optional_history_mask_is_unknown_not_zero():
    module = audit()
    selected = np.ones((2, 3), bool)
    assert module.optional_mask_count({}, "XQC_CONTEXT_WEATHER_MASK", selected) is None
    assert module.optional_mask_count(
        {"XQC_CONTEXT_WEATHER_MASK": np.zeros((2, 3), "uint8")},
        "XQC_CONTEXT_WEATHER_MASK", selected,
    ) == 0
    assert module.optional_mask_count(
        {"XQC_CONTEXT_WEATHER_MASK": np.ones((2, 3), "uint8")},
        "XQC_CONTEXT_WEATHER_MASK", selected,
    ) == 6


def test_incomplete_legacy_baseline_has_no_fabricated_fraction():
    module = audit()
    shape = (2, 3)
    fields = {"XQC_" + name + "_MASK": np.zeros(shape, "uint8") for name in
              ("RECEIVER", "PARTIAL", "RADIAL_POLAR", "RADIAL_FRAGMENT",
               "RADIAL_SOURCE", "CLUTTER", "ISOLATED")}
    fields["XQC_RECEIVER_MASK"][0, 0] = 1
    baseline, missing = module.baseline_union(fields)
    assert baseline.sum() == 1 and missing == []
    del fields["XQC_RADIAL_SOURCE_MASK"]
    baseline, missing = module.baseline_union(fields)
    assert baseline is None and missing == ["XQC_RADIAL_SOURCE_MASK"]


def test_source_only_binds_one_frozen_input_without_product_arrays():
    module = audit()
    source = {'radar_id': 'generic-x', 'scan_id': 'scan', 'input_uri': 's3://bucket/native'}
    task = {'state': 'SUCCEEDED', 'spec': {'inputs': [{'uri': source['input_uri'],
             'sha256': 'a' * 64}], 'request': {'payload': {'mode': 'x_qc',
             'radar_id': 'generic-x', 'scan_id': 'scan', 'sources': [source]}}}}
    assert module.source_identity(task) == (source, 'a' * 64)
    task['spec']['inputs'][0]['uri'] = 's3://bucket/other'
    with pytest.raises(ValueError, match='frozen'):
        module.source_identity(task)
    task['spec']['inputs'][0]['uri'] = source['input_uri']
    task['spec']['request']['payload']['sources'].append(source)
    with pytest.raises(ValueError, match='one'):
        module.source_identity(task)


def test_source_inventory_preserves_native_numbers_and_bounds_all_cuts():
    module = audit()
    assert module.source_cut_numbers(['sweep_008/DBZH/.zarray',
        'sweep_010/DBZH/.zarray', 'sweep_009/VR/.zarray']) == [8, 10]
    with pytest.raises(ValueError, match='declared'):
        module.source_cut_numbers(['sweep_008/DBZH/.zarray'], [8, 10])
    assert module.source_cut_numbers(['sweep_008/DBZH/.zarray'], [8]) == [8]
    with pytest.raises(ValueError, match='64'):
        module.source_cut_numbers([f'sweep_{i:03d}/VR/.zarray' for i in range(65)])
    with pytest.raises(ValueError, match='reflectivity'):
        module.source_cut_numbers(['sweep_001/VR/.zarray'])
    with pytest.raises(ValueError, match='number'):
        module.source_cut_numbers(['sweep_1000/DBZH/.zarray'])


def test_source_cut_keys_never_load_another_cut_or_unused_fields():
    module = audit()
    keys = ['.zattrs', '.zgroup', 'sweep_008/.zattrs', 'sweep_008/.zgroup',
            'sweep_008/DBZH/.zarray', 'sweep_008/DBZH/0.0',
            'sweep_008/azimuth/0', 'sweep_008/unused/0.0',
            'sweep_010/DBZH/0.0', 'sweep_number/0']
    selected = module.source_cut_keys(keys, 8)
    assert 'sweep_008/DBZH/0.0' in selected and '.zattrs' in selected
    assert 'sweep_010/DBZH/0.0' not in selected
    assert 'sweep_008/unused/0.0' not in selected
    assert 'sweep_number/0' not in selected


def test_source_only_runtime_never_reads_product_or_invents_comparison(monkeypatch, capsys):
    import json
    from contextlib import ExitStack, nullcontext
    from types import SimpleNamespace

    import rainpulse_algo.multiband.adapters as adapters
    import rainpulse_algo.multiband.managed as managed
    import rainpulse_algo.multiband.model as model

    module = audit()
    source = {'radar_id': 'generic-x', 'scan_id': 'scan', 'input_uri': 's3://bucket/native'}
    task = {'id': 'old-task', 'state': 'SUCCEEDED', 'spec': {
        'inputs': [{'uri': source['input_uri'], 'sha256': 'a' * 64}],
        'request': {'payload': {'mode': 'x_qc', 'radar_id': 'generic-x',
                    'scan_id': 'scan', 'sources': [source]}}}}
    class Objects(dict):
        pass
    objects = Objects({'.zattrs': b'{}', '.zgroup': b'{}',
                       'sweep_number/.zarray': b'{}', 'sweep_number/0': b'8',
                       'sweep_008/.zgroup': b'{}', 'sweep_008/DBZH/.zarray': b'{}'})
    index = SimpleNamespace(schema='2.0', logical={k: ('pack', 0, len(v))
                                                 for k, v in objects.items()})
    objects.session = SimpleNamespace(index=index)
    session = SimpleNamespace(index=index, staged=lambda **kwargs: nullcontext(objects))
    opened = []
    class Reader:
        def __init__(self, *args, **kwargs):
            pass
        def open(self, uri, *, expected_sha256):
            opened.append((uri, expected_sha256))
            assert uri == source['input_uri']
            return session
    monkeypatch.setattr(module, 'ArtifactObjectReader', Reader)
    monkeypatch.setattr(module, 'minio_client_from_environment', lambda: None)
    net = SimpleNamespace(maximum_input_bytes=512 * 1024**2,
                          stations={'generic-x': None})
    monkeypatch.setattr(model.Network, 'load', lambda _: net)
    monkeypatch.setenv('RAINPULSE_MULTIBAND_CONFIG', 'test-frozen-network')
    monkeypatch.setattr(managed, '_zarr_sweep_numbers', lambda _: [8])
    shape = (9, 100)
    cut = SimpleNamespace(number=8, azimuth_deg=np.arange(9.), range_m=np.arange(100.) * 1000,
        elevation_deg=np.ones(9), ray_time_epoch=np.arange(9.),
        fields={'DBZH': np.zeros(shape, 'float32'),
                'OBSERVED_MASK': np.ones(shape, 'uint8'),
                'NO_ECHO_MASK': np.ones(shape, 'uint8')})
    monkeypatch.setattr(adapters, 'read_x_qc_sweep',
                        lambda *args, **kwargs: (SimpleNamespace(sweeps=[cut]), 0))
    stream = SimpleNamespace(buffer=io.BytesIO(json.dumps(task).encode()))
    monkeypatch.setattr(module.sys, 'stdin', stream)
    with ExitStack() as stack:
        module.run_source_only(stack)
    report = json.loads(capsys.readouterr().out)
    assert opened == [(source['input_uri'], 'a' * 64)]
    assert report['proof_scope'] == 'verified_source_only'
    assert report['no_publication'] is True and report['actions_executed'] is False
    assert report['cuts'][0]['sweep_number'] == 8
    assert report['cuts'][0]['normal_status'] is None
    record = report['cuts'][0]['morphology']
    assert record['status'] == 'EVALUATED' and record['qualified_gates'] == 0
    assert record['new_visible_selected'] is None
    assert record['selected_known_hard_weather_gates'] is None
    assert record['prospective_action_budget_abstained'] is None
