import copy
import json
from pathlib import Path

import pytest
import yaml
from jsonschema import Draft202012Validator, FormatChecker, ValidationError


REPOSITORY_ROOT = Path(__file__).resolve().parents[2]
CONTRACTS_ROOT = REPOSITORY_ROOT / "contracts"
EVENT_NAMES = (
    "job-requested",
    "job-completed",
    "job-failed",
    "product-published",
    "radar-scan-received",
    "radar-decode-requested",
    "radar-qc-requested",
    "radar-grid-requested",
    "analysis-cycle-opened",
    "analysis-mosaic-requested",
    "nowcast-input-ready",
    "forecast-run-requested",
)
JOB_EVENT_NAMES = ("job-requested", "job-completed", "job-failed", "product-published")


@pytest.mark.parametrize("event_name", EVENT_NAMES)
def test_event_example_conforms_to_schema(event_name: str) -> None:
    schema = json.loads((CONTRACTS_ROOT / "events" / f"{event_name}.schema.json").read_text())
    example = json.loads((CONTRACTS_ROOT / "examples" / f"{event_name}.json").read_text())

    Draft202012Validator.check_schema(schema)
    Draft202012Validator(schema, format_checker=FormatChecker()).validate(example)


@pytest.mark.parametrize("event_name", JOB_EVENT_NAMES)
def test_event_envelope_rejects_missing_job_identity(event_name: str) -> None:
    schema = json.loads((CONTRACTS_ROOT / "events" / f"{event_name}.schema.json").read_text())
    example = json.loads((CONTRACTS_ROOT / "examples" / f"{event_name}.json").read_text())
    invalid_example = copy.deepcopy(example)
    del invalid_example["job_id"]

    with pytest.raises(ValidationError):
        Draft202012Validator(schema, format_checker=FormatChecker()).validate(invalid_example)


def test_openapi_exposes_the_planned_v1_operations() -> None:
    specification = yaml.safe_load((CONTRACTS_ROOT / "openapi.yaml").read_text())
    expected_paths = {
        "/runs/latest",
        "/runs",
        "/runs/{run_id}",
        "/runs/{run_id}/jobs",
        "/products",
        "/products/{product_id}",
        "/products/{product_id}/assets",
        "/point-forecast",
        "/area-statistics",
        "/verification/summary",
        "/system/status",
        "/events/stream",
        "/admin/runs/{run_id}/rerun",
        "/admin/models/{model_id}/enable",
        "/admin/models/{model_id}/disable",
    }

    assert specification["openapi"] == "3.0.3"
    assert set(specification["paths"]) == expected_paths

    operation_ids = [
        operation["operationId"]
        for path_item in specification["paths"].values()
        for method, operation in path_item.items()
        if method in {"get", "post", "put", "patch", "delete"}
    ]
    assert len(operation_ids) == len(set(operation_ids))


def test_data_contracts_keep_missing_distinct_from_no_rain() -> None:
    nowcast_input = (CONTRACTS_ROOT / "data" / "nowcast-input.md").read_text()

    assert "Valid no-rain" in nowcast_input
    assert "Missing" in nowcast_input
    assert "LOW_QUALITY_MASK" in nowcast_input
    assert "must never be silently converted to zero rainfall" in nowcast_input


def test_v11_radar_contract_chain_is_frozen() -> None:
    contract_names = (
        "raw-radar-asset",
        "normalized-radar-volume",
        "qc-radar-volume",
        "radar-grid",
        "radar-analysis",
        "nowcast-input",
        "forecast-output",
    )
    contracts = {
        name: (CONTRACTS_ROOT / "data" / f"{name}.md").read_text()
        for name in contract_names
    }

    assert "immutable" in contracts["raw-radar-asset"]
    assert "original polar sampling geometry" in contracts["normalized-radar-volume"]
    assert "QC runs before" in contracts["qc-radar-volume"]
    assert "Hybrid Scan" in contracts["radar-grid"]
    assert "Direct dBZ averaging is forbidden" in contracts["radar-analysis"]
    for name in ("qc-radar-volume", "radar-grid", "radar-analysis", "nowcast-input"):
        assert "uint32" in contracts[name]
        assert "uint16` | Versioned bit set" not in contracts[name]
