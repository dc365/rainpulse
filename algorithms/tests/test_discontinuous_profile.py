import copy
import importlib.util
from pathlib import Path
import pytest
import yaml

ROOT = Path(__file__).resolve().parents[2]
spec = importlib.util.spec_from_file_location('discontinuous_profile', ROOT/'scripts/make_discontinuous_radial_profile.py')
module = importlib.util.module_from_spec(spec)
spec.loader.exec_module(module)


def parent():
    return yaml.safe_load((ROOT/'configs/qc/near-joint-20260923-height-audit/near-joint-strong-temporal-quarantine.yaml').read_text())


def test_child_preserves_every_unrelated_active_parent_setting():
    p = parent(); frozen = copy.deepcopy(p)
    child = module.generate(p)
    assert p == frozen
    assert child['profile_version'] != p['profile_version']
    restored = copy.deepcopy(child)
    restored['profile_version'] = p['profile_version']
    actual_line = restored['generalization']['broad_source']['source_review']['radial_revision']['fragment_line']
    parent_line = p['generalization']['broad_source']['source_review']['radial_revision']['fragment_line']
    keys = ('discontinuous_tracks_enabled', 'source_envelope_enabled',
            'raw_fragment_families_enabled', 'source_ledger_enabled',
            'raw_fan_families_enabled', 'source_footprint_enabled')
    assert all(actual_line[k] is True for k in keys)
    for key in keys:
        if key in parent_line:actual_line[key] = parent_line[key]
        else:del actual_line[key]
    assert restored == p


def test_audit_parent_and_repeated_child_are_rejected():
    p = parent()
    with pytest.raises(ValueError):
        module.generate(module.generate(p))
    p['generalization']['broad_source']['source_review']['radial_revision']['mode'] = 'audit'
    with pytest.raises(ValueError):
        module.generate(p)
