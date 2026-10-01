"""Acceptance does not mistake task success or a clean image for completed QC."""
import importlib.util
from pathlib import Path

spec = importlib.util.spec_from_file_location("acceptance", Path(__file__).parents[1] / "scripts/xqc_acceptance.py")
module = importlib.util.module_from_spec(spec)
spec.loader.exec_module(module)


def test_mechanical_gates_require_complete_cut_and_source():
    good = {"status": "EVALUATED", "module_records": {"radial_source": {"status": "EVALUATED", "complete": True}}}
    args = dict(raw_equal=True, geometry_equal=True, held_visible=0, held_admitted=0,
                protected_rejected=0, qpe_enabled=False)
    assert module.cut_failures(good, **args) == []
    for state in ("ACTION_BUDGET_ABSTAINED", "DEGRADED_SOURCE_RESOURCE_LIMIT", None):
        assert module.cut_failures({**good, "status": state}, **args)
    assert "SOURCE_INCOMPLETE" in module.cut_failures({"status": "EVALUATED"}, **args)
    for field in args:
        bad = {**args, field: not args[field]}
        assert module.cut_failures(good, **bad), field


def test_manifest_identity_changes_with_scope_and_release():
    original = {"scans": ["a", "b"], "network_sha256": "old"}
    assert module.digest(original) != module.digest({**original, "scans": ["a"]})
    assert module.digest(original) != module.digest({**original, "network_sha256": "new"})


def test_raw_batch_rejects_decoder_configuration_drift(tmp_path):
    import hashlib
    import pytest
    raw_spec = importlib.util.spec_from_file_location("raw_acceptance", Path(__file__).parents[1] / "scripts/audit_xqc_raw.py")
    raw_module = importlib.util.module_from_spec(raw_spec)
    raw_spec.loader.exec_module(raw_module)
    config = tmp_path / "zf101.yaml"
    config.write_bytes(b"frozen decoder")
    expected = hashlib.sha256(config.read_bytes()).hexdigest()
    assert raw_module.verify_config(config, expected) == b"frozen decoder"
    config.write_bytes(b"changed decoder")
    with pytest.raises(ValueError, match="frozen batch identity"):
        raw_module.verify_config(config, expected)
