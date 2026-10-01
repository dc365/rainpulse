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


def test_native_file_shards_are_complete_and_disjoint():
    raw_spec = importlib.util.spec_from_file_location("raw_shards", Path(__file__).parents[1] / "scripts/audit_xqc_raw.py")
    raw_module = importlib.util.module_from_spec(raw_spec)
    raw_spec.loader.exec_module(raw_module)
    identities = [module.digest({"raw": i}) for i in range(5466)]
    groups = [{s for s in identities if raw_module.shard_for_file(s, 8) == i} for i in range(8)]
    assert set.union(*groups) == set(identities)
    assert sum(len(g) for g in groups) == len(identities)
    assert max(map(len, groups)) < 800


def test_corpus_summary_requires_every_native_cut_and_preserves_cross_day():
    summary_spec = importlib.util.spec_from_file_location("raw_summary", Path(__file__).parents[1] / "scripts/summarize_xqc_raw_acceptance.py")
    summary = importlib.util.module_from_spec(summary_spec)
    summary_spec.loader.exec_module(summary)
    source = {"sha256": "a", "radar_id": "zf701", "relative_path": "sample"}
    prefix = {"raw_sha256": "a", "radar_id": "zf701"}
    rows = [(0, {**prefix, "state": "RAW_DECODE_VERIFIED", "ref_sweep_numbers": [0, 1],
                 "decoder_config_sha256": "cfg", "observed_start_utc": "2026-08-28T23:59:00+00:00",
                 "observed_end_utc": "2026-08-29T00:02:00+00:00"}),
            (0, {**prefix, "state": "RAW_FILE_COMPLETE"}),
            (0, {**prefix, "state": "RAW_MECHANICAL_GATES_PASSED", "sweep": 0})]
    incomplete = summary.summarize([source], rows, {"zf701": "cfg"})
    assert not incomplete["mechanical_corpus_complete"]
    assert "REF_CUT_COVERAGE_INCOMPLETE" in incomplete["failure_reasons"]
    rows.append((0, {**prefix, "state": "RAW_MECHANICAL_GATES_PASSED", "sweep": 1}))
    accepted = summary.summarize([source], rows, {"zf701": "cfg"})
    assert accepted["mechanical_corpus_complete"]
    assert accepted["meteorological_acceptance"] == "NOT_COMPLETED"
    assert {(r["clock"], r["date"]) for r in accepted["native_start_coverage"]} == {
        ("UTC", "2026-08-28"), ("UTC+08", "2026-08-29")}
    drift = summary.summarize([source], rows, {"zf701": "changed"})
    assert not drift["mechanical_corpus_complete"]
    assert "DECODER_CONFIG_DRIFT" in drift["failure_reasons"]


def test_unprocessed_files_do_not_inflate_receipt_coverage():
    spec = importlib.util.spec_from_file_location("coverage_summary", Path(__file__).parents[1] / "scripts/summarize_xqc_raw_acceptance.py")
    summary = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(summary)
    sources = [{"sha256": s, "radar_id": "zf701", "relative_path": s} for s in ("a", "b")]
    receipts = [(0, {"raw_sha256": "a", "radar_id": "zf701", "state": "RAW_FILE_COMPLETE"})]
    result = summary.summarize(sources, receipts, {})
    assert result["files_with_receipts"] == 1
    assert result["file_states"]["PENDING"] == 1
    assert not result["mechanical_corpus_complete"]
