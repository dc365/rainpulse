# ruff: noqa: E501, E701, E702, I001, E402
import json
from pathlib import Path
import pytest
from jsonschema import Draft202012Validator

ROOT = Path(__file__).resolve().parents[2]


def test_network_example_is_valid_but_disabled():
    schema=json.loads((ROOT/'contracts/internal/multiband/network.schema.json').read_text())
    value=json.loads((ROOT/'configs/multiband/network.example.json').read_text())
    Draft202012Validator.check_schema(schema)
    Draft202012Validator(schema).validate(value)
    assert not any(s['enabled'] for s in value['stations'].values())
    value['stations']['x_example']['enabled']=True
    with pytest.raises(Exception):Draft202012Validator(schema).validate(value)


def test_new_contract_schemas_are_valid():
    for path in (ROOT/'contracts/internal/multiband').glob('*.schema.json'):
        Draft202012Validator.check_schema(json.loads(path.read_text()))


def test_management_contract_includes_kind_and_presets():
    d=json.loads((ROOT/'contracts/internal/operations-openapi.json').read_text())
    schema=d['components']['schemas']
    assert {'x_qc','sx_composite'} <= set(schema['Selection']['properties']['preset']['enum'])
    assert 'multiband' in schema['Identity']['properties']['kind']['enum']
    assert 'product_id' in schema['Selection']['properties']


def test_source_wiring_and_no_new_service_launcher():
    adapter=(ROOT/'services/control/internal/controlplane/operations_adapter.go').read_text()
    assert 'return b.buildMultiBand(ctx, s)' in adapter
    assert 'return b.validateMultiBand(ctx, spec)' in adapter
    worker=(ROOT/'algorithms/rainpulse_algo/operations/native.py').read_text()
    assert 'self.multiband.execute(' in worker
    assert 'PinnedClient(self.client, claim["inputs"])' in worker
    assert 'exec.Command' not in (ROOT/'services/control/internal/controlplane/operations_multiband.go').read_text()
