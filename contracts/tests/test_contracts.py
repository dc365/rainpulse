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
    "analysis-mosaic-requested-v2",
    "analysis-qpe-requested",
    "nowcast-input-requested",
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
        "/radars",
        "/radars/status",
        "/radars/{radar_id}",
        "/radars/{radar_id}/status",
        "/radar-scans",
        "/radar-scans/{scan_id}",
        "/radar-scans/{scan_id}/qc-summary",
        "/radar-scans/{scan_id}/grid-summary",
        "/analysis-cycles",
        "/analysis-cycles/{analysis_id}",
        "/analysis-cycles/{analysis_id}/mosaic-summary",
        "/analysis-cycles/{analysis_id}/qpe-summary",
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
        "radar-mosaic",
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
    assert "Direct dBZ averaging is forbidden" in contracts["radar-mosaic"]
    assert "Direct dBZ averaging is forbidden" in contracts["radar-analysis"]
    for name in (
        "qc-radar-volume",
        "radar-grid",
        "radar-mosaic",
        "radar-analysis",
        "nowcast-input",
    ):
        assert "uint32" in contracts[name]
        assert "uint16` | Versioned bit set" not in contracts[name]

    assert "An unavailable prerequisite is represented by `NaN`" in contracts["qc-radar-volume"]


def test_phase1_grid_contract_is_equal_lat_lon_end_to_end() -> None:
    contract_names = (
        "radar-grid",
        "radar-analysis",
        "nowcast-input",
        "forecast-output",
    )
    contracts = {
        name: (CONTRACTS_ROOT / "data" / f"{name}.md").read_text()
        for name in contract_names
    }

    assert "fuzhou_118_123_25_27_0p01deg_v1" in contracts["radar-grid"]
    assert "`501`" in contracts["radar-grid"]
    assert "`201`" in contracts["radar-grid"]
    assert "time × lat × lon" in contracts["nowcast-input"]
    assert "member × lead_time × lat × lon" in contracts["forecast-output"]
    assert "resolution_m" not in contracts["nowcast-input"]
    for contract in contracts.values():
        assert "Projected cell-centre" not in contract


def test_radar_grid_requires_polar_blockage_evidence_and_datum_gate() -> None:
    contract = (CONTRACTS_ROOT / "data" / "radar-grid.md").read_text()

    assert "contract_version=1.3" in contract
    assert "`QI_BLOCKAGE`" in contract
    assert "`QI_BEAM_HEIGHT`" in contract
    assert "`SOURCE_SWEEP`" in contract
    assert "per-sweep polar blockage diagnostics" in contract
    assert "operational_eligible=false" in contract
    assert "Velocity-only" in contract


def test_rp010_mosaic_is_separate_from_rp011_qpe() -> None:
    mosaic = (CONTRACTS_ROOT / "data" / "radar-mosaic.md").read_text()
    analysis = (CONTRACTS_ROOT / "data" / "radar-analysis.md").read_text()

    assert "does not contain `RATE_QPE`" in mosaic
    assert "dBZ → linear Z" in mosaic
    assert "`SOURCE_RADAR`" in mosaic
    assert "all `QI_*`" in mosaic
    assert "after RP-011 QPE" in analysis


def test_rp011_qpe_contract_is_versioned_and_never_fabricates_raw_fields() -> None:
    analysis = (CONTRACTS_ROOT / "data" / "radar-analysis.md").read_text()

    assert "contract_version=1.2" in analysis
    assert "Z = a R^b" in analysis
    assert "RATE_QPE=0" in analysis
    assert "must not be fabricated" in analysis
    assert "gauge adjustment is disabled" in analysis


def test_distribution_contracts_preserve_grid_and_missing_semantics() -> None:
    netcdf = (CONTRACTS_ROOT / "data" / "application-rainfall-netcdf.md").read_text()
    rendered = (CONTRACTS_ROOT / "data" / "rendered-rainfall-layer.md").read_text()

    assert "NetCDF classic" in netcdf
    assert "lat × lon" in netcdf
    assert "_FillValue=-9999.0" in netcdf
    assert "valid no-rain is `0.0`" in netcdf
    assert "501 × 201" in rendered
    assert "[117.995, 24.995, 123.005, 27.005]" in rendered
    assert "half-pixel alignment error" in rendered
