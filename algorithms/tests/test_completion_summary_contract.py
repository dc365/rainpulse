from __future__ import annotations

import copy
import json

import pytest

from rainpulse_algo.radar.completion_summary import (
    MAX_SUMMARY_BYTES, SummaryContractError, compact_qc_summary, encoded_summary,
)


@pytest.mark.parametrize("version", ["qc-opensource-7.0.0", "qc-opensource-7.3.9", "qc-future-99.0", "basic-qc-1.0.0"])
def test_every_version_has_bounded_transport_not_full_report(version):
    source = {"qc_pipeline_version": version, "operational_eligible": False,
              "valid_gate_count": 3, "missing_gate_count": 5,
              "sweeps": {"sweep_000": {"nodes": [dict(ray=1)] * 100_000}}}
    result = compact_qc_summary(source)
    assert "sweeps" not in result
    assert result["summary_object_path"] == "qc/summary.json"
    assert result["operational_eligible"] is False
    assert result["missing_gate_count"] == 5
    assert len(encoded_summary(result)) <= MAX_SUMMARY_BYTES


def test_oversized_known_nested_diagnostics_are_not_a_backdoor():
    source = {"generalization_summary": {"nodes": [{}] * 100_000},
              "health_facets": {str(i): "not-ready" for i in range(100_000)},
              "operational_eligible": False}
    result = compact_qc_summary(source)
    assert result["summary_omitted_fields"] == ["health_facets", "generalization_summary"]
    assert len(encoded_summary(result)) < 1024
    assert result["operational_eligible"] is False


def test_small_metadata_and_legacy_metrics_preserved():
    source = {"health_facets": {"hardware": "healthy", "reasons": ["config_not_ready"]},
              "module_statuses": {"near": "enabled"}, "ground_clutter_gate_count": 12,
              "sea_clutter_gate_count": 2, "ap_gate_count": 4,
              "radial_interference_area_km2": None, "mean_quality_index": 0.3}
    before = copy.deepcopy(source)
    result = compact_qc_summary(source)
    for key, value in source.items():
        assert result[key] == value
    result["health_facets"]["reasons"].append("changed-copy")
    assert source == before


@pytest.mark.parametrize("item", [
    {"operational_eligible": "false"}, {"valid_gate_count": True},
    {"valid_gate_count": -1}, {"valid_gate_count": 2**64},
    {"mean_quality_index": float("nan")}, {"qc_profile": "x" * 513},
])
def test_invalid_core_metadata_not_coerced(item):
    with pytest.raises(SummaryContractError):
        compact_qc_summary(item)


def test_cycles_and_deep_optional_trees_fail_bounded():
    tree = {}; tree["self"] = tree
    result = compact_qc_summary({"health_facets": tree})
    assert "health_facets" in result["summary_omitted_fields"]


def test_unicode_budget_is_for_wire_encoding():
    summary = {"qc_profile": "气" * 512,
               "nonprecip_review_summary": {f"x{i}": "气" * 256 for i in range(64)}}
    result = compact_qc_summary(summary)
    assert len(json.dumps(result).encode()) <= MAX_SUMMARY_BYTES


def test_output_matches_checked_in_transport_schema():
    from pathlib import Path
    import jsonschema
    schema_path = Path(__file__).resolve().parents[2] / "contracts/schemas/qc-completion-summary-v1.schema.json"
    schema = json.loads(schema_path.read_text())
    jsonschema.Draft202012Validator.check_schema(schema)
    value = compact_qc_summary({
        "qc_pipeline_version": "future-v99", "qc_profile": "test", "operational_eligible": False,
        "valid_gate_count": 5, "missing_gate_count": 2, "mean_quality_index": 0.6,
        "radial_interference_area_km2": None, "module_statuses": {"near": "enabled"},
    })
    jsonschema.validate(value, schema)
