# ruff: noqa: E501, I001
from dataclasses import asdict
from copy import deepcopy
import importlib.util
import json
from pathlib import Path
import pytest
from .helpers import station, network, options
ROOT = Path(__file__).resolve().parents[3]
spec = importlib.util.spec_from_file_location('sx_release_builder', ROOT / 'scripts/prepare_sx_quality_release.py')
builder = importlib.util.module_from_spec(spec)
spec.loader.exec_module(builder)

def base():
    st = asdict(station())
    st.pop('radar_id')
    st['x_qc']['enhancement'] = None
    grid = asdict(network(station()).products['sx'])
    return (json.loads(json.dumps({'schema_version': '1.0', 'release_id': 'old', 'stations': {'x1': st}, 'products': {'old': grid}})), json.loads(json.dumps(grid)))

def test_builder_preserves_existing_inputs_and_verification():
    b, g = base()
    b['stations']['x1']['enabled'] = False
    b['stations']['x1']['geometry_verified'] = False
    old = deepcopy(b)
    raw, warnings = builder.prepare(b, g, 'new', 'new-release')
    out = json.loads(raw)
    assert b == old and out['stations'] == old['stations']
    assert out['products']['old'] == json.loads(json.dumps(old['products']['old']))
    assert out['products']['new']['method'] == 'quality_height_v2'
    assert any(('unverified' in text for text in warnings))

def test_builder_refuses_product_replacement_and_non_x_updates():
    b, g = base()
    with pytest.raises(ValueError):
        builder.prepare(b, g, 'old', 'new')
    with pytest.raises(ValueError):
        builder.prepare(b, g, 'new', 'old')
    with pytest.raises(ValueError):
        builder.prepare(b, g, 'new', 'new', {'s1': {'attenuation': 'zphi'}})
    with pytest.raises(ValueError):
        builder.prepare(b, g, 'new', 'new', {'x1': {'enabled': True}})
    with pytest.raises(ValueError):
        builder.prepare(b, {**g, 'method': 'experimental_horizontal_max'}, 'new', 'new')

def test_release_file_is_never_overwritten(tmp_path):
    b, g = base()
    (tmp_path / 'network.json').write_text(json.dumps(b))
    (tmp_path / 'grid.json').write_text(json.dumps(g))
    args = ['--network', str(tmp_path / 'network.json'), '--grid', str(tmp_path / 'grid.json'), '--product-id', 'new', '--release-id', 'new', '--output', str(tmp_path / 'result.json')]
    assert builder.main(args) == 0
    before = (tmp_path / 'result.json').read_bytes()
    assert builder.main(args) == 2
    assert before == (tmp_path / 'result.json').read_bytes()

def test_schema_and_python_require_explicit_zphi_coefficients():
    import jsonschema
    from referencing import Registry, Resource
    folder = ROOT / 'contracts/internal/multiband'
    schema = json.loads((folder / 'network.schema.json').read_text())
    uri = 'https://rainpulse.invalid/network.schema.json'
    schema['$id'] = uri
    zphi = json.loads((folder / 'zphi-options.schema.json').read_text())
    registry = Registry().with_resource('https://rainpulse.invalid/zphi-options.schema.json', Resource.from_contents(zphi))
    validator = jsonschema.Draft202012Validator(schema, registry=registry)
    b, g = base()
    cfg = b['stations']['x1']['x_qc']
    cfg.pop('enhancement')
    cfg.pop('zphi', None)
    cfg['attenuation'] = 'zphi'
    assert list(validator.iter_errors(b))
    cfg['zphi'] = options()
    cfg['alpha_db_per_degree'] = 0.2
    assert not list(validator.iter_errors(json.loads(json.dumps(b))))
    cfg['alpha_db_per_degree'] = None
    assert list(validator.iter_errors(b))
