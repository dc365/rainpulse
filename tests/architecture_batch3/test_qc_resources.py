"""Real resource-loader code with injected I/O; no radar/GPU readiness claim."""
import hashlib
import importlib.util
from pathlib import Path
import sys
from types import SimpleNamespace as NS

import pytest

ROOT = Path(__file__).resolve().parents[2]
PATH = ROOT / 'algorithms/rainpulse_algo/radar/qc_resources.py'
spec = importlib.util.spec_from_file_location('batch3_geometry', PATH)
module = importlib.util.module_from_spec(spec)
sys.modules[spec.name] = module
spec.loader.exec_module(module)


def setup_io(tmp_path, **kw):
    (tmp_path / 'z1.yaml').write_text('radar')
    ancillary = tmp_path / 'ancillary.yaml'
    ancillary.write_text('ancillary')
    radar = NS(radar_id='z1', config_version='site-v1', ancillary={'dem_asset_version': 'dem-v1'})
    beam = NS(altitude_datum_status='national1985_unconverted')
    terrain = NS(manifest_sha256='d' * 64)
    calls = []
    def build(source, root, **options):
        calls.append((source.config_version, root, options))
        return terrain
    defaults = dict(radar_config=lambda p: radar, beam_context=lambda r: beam,
        ancillary_source=lambda p: NS(config_version='ancillary-v1'), terrain_store=build,
        optional_file=lambda name: ancillary, optional_directory=lambda name: tmp_path)
    defaults.update(kw)
    providers = module.GeometryProviders(**defaults)
    return providers, radar, beam, terrain, calls


def run(providers, profile=None):
    audit = {'preexisting': 'retain'}
    value = module.load_geometry_resources(NS(payload=NS(radar_id='z1')),
        profile or NS(engine='open_source', decision_version='x'), providers=providers, audit=audit)
    return value, audit


@pytest.mark.parametrize('engine,decision,requested', [
    ('open_source','x',True), ('legacy','evidence-v2',True), ('legacy','evidence-v1',False),
    (None,'evidence-v1',False), ('future','x',False),
])
def test_capability_not_algorithm_version(tmp_path, engine, decision, requested):
    io, *_ = setup_io(tmp_path)
    value, audit = run(io, NS(engine=engine, decision_version=decision))
    assert (value[0] is not None) == requested
    assert audit['status'] == ('resources_loaded' if requested else 'not_requested')
    assert audit['preexisting'] == 'retain'


def test_exact_assets_and_no_datum_promotion(tmp_path):
    io, _, beam, terrain, calls = setup_io(tmp_path)
    value, audit = run(io)
    assert value == (beam, terrain, tmp_path, 'dem-v1')
    assert audit['altitude_datum_status'] == 'national1985_unconverted'
    assert audit['resources']['current_radar_config']['sha256'] == hashlib.sha256(b'radar').hexdigest()
    assert audit['resources']['ancillary_config']['sha256'] == hashlib.sha256(b'ancillary').hexdigest()
    assert calls == [('ancillary-v1', tmp_path, {'expected_asset_version':'dem-v1','expected_config_version':'ancillary-v1'})]


def test_missing_directory_is_explicit(tmp_path):
    io, *_ = setup_io(tmp_path, optional_directory=lambda _: None)
    value, audit = run(io)
    assert value == (None,None,None,None)
    assert audit['status'] == 'radar_config_directory_unavailable'


def test_identity_mismatch_retains_directory(tmp_path):
    io, radar, *_ = setup_io(tmp_path)
    radar.radar_id = 'other'
    value, audit = run(io)
    assert value == (None,None,tmp_path,None)
    assert audit['status'] == 'radar_identity_mismatch'


@pytest.mark.parametrize('failure', [OSError, ValueError])
def test_invalid_radar_stays_unavailable(tmp_path, failure):
    def bad(_): raise failure('unavailable')
    io, *_ = setup_io(tmp_path, radar_config=bad)
    value, audit = run(io)
    assert value == (None,None,tmp_path,None)
    assert audit['status'] == 'radar_config_invalid'
    assert audit['error_type'] == failure.__name__


def test_changed_radar_file_rejected(tmp_path):
    def changed(path):
        path.write_text('modified')
        return NS(radar_id='z1', config_version='site-v1')
    io, *_ = setup_io(tmp_path, radar_config=changed)
    value, audit = run(io)
    assert value[0] is None
    assert audit['error_type'] == 'ValueError'


@pytest.mark.parametrize('version', [None, '', 3])
def test_missing_dem_version_does_not_invent_terrain(tmp_path, version):
    io, radar, beam, _, calls = setup_io(tmp_path)
    radar.ancillary['dem_asset_version'] = version
    value, audit = run(io)
    assert value == (beam,None,tmp_path,None)
    assert audit['terrain_status'] == 'not_configured'
    assert calls == []


@pytest.mark.parametrize('failure', [OSError, RuntimeError, ValueError])
def test_invalid_dem_preserves_beam_and_expected_version(tmp_path, failure):
    def bad(*a, **k): raise failure('bad terrain')
    io, _, beam, *_ = setup_io(tmp_path, terrain_store=bad)
    value, audit = run(io)
    assert value == (beam,None,tmp_path,'dem-v1')
    assert audit['terrain_status'] == 'invalid'
    assert audit['error_type'] == failure.__name__


def test_changed_ancillary_file_does_not_load_terrain(tmp_path):
    def changed(path):
        path.write_text('modified')
        return NS(config_version='v2')
    io, _, beam, _, calls = setup_io(tmp_path, ancillary_source=changed)
    value, audit = run(io)
    assert value == (beam,None,tmp_path,'dem-v1')
    assert audit['terrain_status'] == 'invalid'
    assert calls == []


def test_audit_optional_does_not_change_return(tmp_path):
    io, _, beam, terrain, _ = setup_io(tmp_path)
    assert module.load_geometry_resources(NS(payload=NS(radar_id='z1')),
        NS(engine='open_source',decision_version='x'),providers=io) == (beam,terrain,tmp_path,'dem-v1')


def test_provider_never_needs_worker_module(tmp_path, monkeypatch):
    io, *_ = setup_io(tmp_path)
    import builtins
    old = builtins.__import__
    def reject(name, *a, **kw):
        assert 'qc_worker' not in name
        return old(name, *a, **kw)
    monkeypatch.setattr(builtins, '__import__', reject)
    assert run(io)[1]['status'] == 'resources_loaded'
