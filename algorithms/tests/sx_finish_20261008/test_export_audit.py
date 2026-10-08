# ruff: noqa: E501, I001
import importlib.util
import hashlib
from pathlib import Path
import pytest
from .test_encoding import results
from . import render_reference as render
from rainpulse_algo.multiband.comparison_finish import build
SCRIPT = Path(__file__).resolve().parents[3] / 'scripts/audit_sx_finish.py'
spec = importlib.util.spec_from_file_location('sx_audit_cli', SCRIPT)
mod = importlib.util.module_from_spec(spec)
spec.loader.exec_module(mod)

def bundle(tmp_path):
    r, b = results()
    objects = build(r, b, render_api=render)
    for key, data in objects.items():
        p = tmp_path / key
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_bytes(data)
    return hashlib.sha256(objects['manifest.json']).hexdigest()

def test_full_export_counts_and_hashes(tmp_path):
    sha = bundle(tmp_path)
    r = mod.audit(tmp_path, sha)
    assert r['status'] == 'passed'
    assert len(r['products']) == 3
    assert r['verified_object_count'] > 5

@pytest.mark.parametrize('target', ['arrays.npz', 'fusion-readiness.json', 'map/x_only.png'])
def test_tampering_and_missing_files_fail(tmp_path, target):
    sha = bundle(tmp_path)
    (tmp_path / target).write_bytes(b'changed')
    with pytest.raises((ValueError, KeyError)):
        mod.audit(tmp_path, sha)

def test_wrong_manifest_identity_fails(tmp_path):
    bundle(tmp_path)
    with pytest.raises(ValueError):
        mod.audit(tmp_path, '0' * 64)
