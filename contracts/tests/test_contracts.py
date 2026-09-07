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
    "analysis-diagnostics-requested",
    "nowcast-input-requested",
    "nowcast-input-ready",
    "forecast-run-requested",
    "forecast-pysteps-lk-requested",
    "forecast-nowcastnet-offline-requested",
    "forecast-baseline-ready",
    "product-build-requested",
    "forecast-verification-requested",
    "forecast-nowcastnet-offline-requested",
)
JOB_EVENT_NAMES = (
    "job-requested",
    "job-completed",
    "job-failed",
    "product-published",
    "product-build-requested",
    "forecast-verification-requested",
)


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
        "/analysis-cycles/{analysis_id}/diagnostics",
        "/diagnostics/{job_id}/layers/{layer_id}",
        "/products",
        "/products/{product_id}",
        "/products/{product_id}/assets",
        "/products/{product_id}/assets/{asset_id}/content",
        "/point-forecast",
        "/area-statistics",
        "/alerts",
        "/operations/issues",
        "/verification/summary",
        "/algorithm-verification/runs",
        "/algorithm-verification/runs/{profile_version}/{run_id}",
        "/algorithm-verification/runs/{profile_version}/{run_id}/metrics",
        "/algorithm-verification/runs/{profile_version}/{run_id}/map-frame",
        "/algorithm-verification/runs/{profile_version}/{run_id}/map-assets/{case_id}/{issue_key}/{asset_id}",
        "/algorithm-verification/runs/{profile_version}/{run_id}/probability-map-frame",
        "/algorithm-verification/runs/{profile_version}/{run_id}/probability-map-assets/{case_id}/{issue_key}/{asset_id}",
        "/ensemble-products/latest",
        "/ensemble-products/by-cycle",
        "/ensemble-products/cycles",
        "/ensemble-products/{bundle_id}/assets/{asset_id}",
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


def test_radar_scan_page_contract_exposes_snapshot_pagination() -> None:
    specification = yaml.safe_load((CONTRACTS_ROOT / "openapi.yaml").read_text())
    parameters = specification["paths"]["/radar-scans"]["get"]["parameters"]
    names = {item.get("name") for item in parameters if isinstance(item, dict)}
    page = specification["components"]["schemas"]["RadarScanPage"]["properties"]

    assert {"start_time", "end_time", "cursor", "snapshot_time"} <= names
    assert "next_cursor" in page
    assert "snapshot_time" in page


def test_data_contracts_keep_missing_distinct_from_no_rain() -> None:
    nowcast_input = (CONTRACTS_ROOT / "data" / "nowcast-input.md").read_text()

    assert "Valid no-rain" in nowcast_input
    assert "Missing" in nowcast_input
    assert "LOW_QUALITY_MASK" in nowcast_input
    assert "must never be silently converted to zero rainfall" in nowcast_input


def test_radar_qc_label_manifest_contract_freezes_holdout_and_label_vocabulary() -> None:
    contract = (CONTRACTS_ROOT / "data" / "radar-qc-label-manifest.md").read_text()

    assert "0 = confirmed meteorological" in contract
    assert "1 = confirmed non-meteorological" in contract
    assert "-1 = uncertain or not evaluated" in contract
    assert "holdout" in contract
    assert "same weather process" in contract


def test_radar_qc_replay_manifest_contract_freezes_manifest_only_replay_boundary() -> None:
    contract = (CONTRACTS_ROOT / "data" / "radar-qc-replay-manifest.md").read_text()

    assert "radial_audit.py --manifest" in contract
    assert "mode" in contract
    assert "replay_manifest" in contract
    assert "catalog" in contract
    assert "normalized_uri" in contract
    assert "expected_scan_ids" in contract
    assert "must match `expected_scan_ids` exactly" in contract
    assert "canonical manifest SHA" in contract


def test_radar_phase_processing_artifact_contract_freezes_shadow_only_c1_boundary() -> None:
    contract = (CONTRACTS_ROOT / "data" / "radar-phase-processing-artifact.md").read_text()

    assert "raw `PHIDP`" in contract
    assert "`KDP = 0.5 * dPHIDP/dr`" in contract
    assert "degree/km" in contract
    assert "operational_eligible=false" in contract
    assert "Worker integration remains disabled" in contract


def test_qc_radar_volume_contract_describes_c1_shadow_phase_fields() -> None:
    contract = (CONTRACTS_ROOT / "data" / "qc-radar-volume.md").read_text()

    assert "`PHIDP_SHADOW_UNWRAPPED`" in contract
    assert "`PHIDP_SHADOW_CORRECTED`" in contract
    assert "`KDP_SHADOW`" in contract
    assert "`KDP_SHADOW_AVAILABLE_MASK`" in contract
    assert "`PHIDP_SHADOW_SEGMENT_INDEX`" in contract
    assert "must not change `DBZH_QC`, `QC_FLAGS`, `QUALITY_INDEX`" in contract


def test_qc_radar_volume_contract_describes_c2_shadow_attenuation_fields() -> None:
    contract = (CONTRACTS_ROOT / "data" / "qc-radar-volume.md").read_text()

    assert "`SPECIFIC_ATTENUATION_SHADOW`" in contract
    assert "`ATTENUATION_CORRECTION_SHADOW`" in contract
    assert "`DBZH_ATTENUATION_SHADOW_CORRECTED`" in contract
    assert "`ATTENUATION_SHADOW_AVAILABLE_MASK`" in contract
    assert "`ATTENUATION_SHADOW_SEGMENT_INDEX`" in contract
    assert "`attenuation_shadow` module record" in contract
    assert "must not change `QI_ATTENUATION`, `QI_CALIBRATION`" in contract


def test_radar_attenuation_artifact_contract_freezes_shadow_only_c2_boundary() -> None:
    contract = (CONTRACTS_ROOT / "data" / "radar-attenuation-artifact.md").read_text()

    assert "raw `DBZH`" in contract
    assert "`A_h = a * KDP^b`" in contract
    assert "two-way" in contract
    assert "operational_eligible=false" in contract
    assert "Worker integration remains disabled" in contract


def test_radar_relative_bias_artifact_contract_freezes_c2_shadow_pair_boundary() -> None:
    contract = (CONTRACTS_ROOT / "data" / "radar-relative-bias-artifact.md").read_text()

    assert "primary minus reference" in contract
    assert "time/geometry/blockage-comparable precipitation gates" in contract
    assert "must not designate either radar as truth" in contract
    assert "operational_eligible=false" in contract
    assert "Worker integration remains disabled" in contract


def test_radar_calibration_reference_manifest_contract_freezes_c2_p3_split_boundary() -> None:
    contract = (
        CONTRACTS_ROOT / "data" / "radar-calibration-reference-manifest.md"
    ).read_text()

    assert "coefficient_fitting" in contract
    assert "independent_validation" in contract
    assert "must be disjoint by process_id and case_id" in contract
    assert "must not both fit coefficients and serve as independent truth" in contract
    assert "Worker integration remains disabled" in contract


def test_radar_attenuation_coefficient_table_contract_freezes_shadow_table_boundary() -> None:
    contract = (
        CONTRACTS_ROOT / "data" / "radar-attenuation-coefficient-table.md"
    ).read_text()

    assert "versioned site/band coefficient table" in contract
    assert "verified_shadow_use" in contract
    assert "applicable temperature range" in contract
    assert "must reference a disjoint independent calibration manifest" in contract
    assert "must not enable QI_ATTENUATION or QI_CALIBRATION" in contract


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


def test_rp017_qpe_contract_freezes_stratiform_vpr_outputs_and_overshoot_gate() -> None:
    analysis = (CONTRACTS_ROOT / "data" / "radar-analysis.md").read_text()

    assert "`DBZH_VPR_CORRECTED`" in analysis
    assert "`VPR_CORRECTION_DB`" in analysis
    assert "`VPR_UNCERTAINTY_DB`" in analysis
    assert "`VPR_APPLIED_MASK`" in analysis
    assert "`VPR_OVERSHOOT_MASK`" in analysis
    assert "only in stratiform scenes" in analysis
    assert "far-range overshoot" in analysis
    assert "must remain missing" in analysis
    assert "`BRIGHT_BAND`" in analysis


def test_rp012_diagnostics_are_pre_rendered_and_preserve_three_states() -> None:
    diagnostics = (CONTRACTS_ROOT / "data" / "diagnostic-bundle.md").read_text()

    assert "React and Go must not parse Zarr arrays" in diagnostics
    assert "Missing pixels have alpha 0" in diagnostics
    assert "Valid no-rain is not transparent" in diagnostics
    assert "Low-quality pixels remain visible" in diagnostics
    assert "arbitrary object-store keys" in diagnostics


def test_algorithm_verification_maps_are_pre_rendered_and_not_scientific_inputs() -> None:
    contract = (CONTRACTS_ROOT / "data" / "algorithm-verification-map-bundle.md").read_text()

    assert "presentation-only" in contract
    assert "Valid no-rain cells remain visible" in contract
    assert "React receives manifests and image URLs only" in contract
    assert "SHA-256" in contract


def test_rp014_forecast_output_freezes_deterministic_baseline_diagnostics() -> None:
    forecast = (CONTRACTS_ROOT / "data" / "forecast-output.md").read_text()

    assert "24 lead times" in forecast
    assert "persistence_rain_rate" in forecast
    assert "translation_rain_rate" in forecast
    assert "latitude-aware" in forecast
    assert "must never become zero rainfall" in forecast


def test_distribution_contracts_preserve_grid_and_missing_semantics() -> None:
    bundle = (CONTRACTS_ROOT / "data" / "application-product-bundle.md").read_text()
    netcdf = (CONTRACTS_ROOT / "data" / "application-rainfall-netcdf.md").read_text()
    rendered = (CONTRACTS_ROOT / "data" / "rendered-rainfall-layer.md").read_text()

    assert "NetCDF classic" in netcdf
    assert "lat × lon" in netcdf
    assert "_FillValue=-9999.0" in netcdf
    assert "valid no-rain is `0.0`" in netcdf
    assert "501 × 201" in rendered
    assert "[117.995, 24.995, 123.005, 27.005]" in rendered
    assert "half-pixel alignment error" in rendered
    assert "contract_version=1.0" in bundle
    assert "exactly three immutable product identities" in bundle
    assert "point-query index" in bundle
    assert "must never be converted to valid zero" in bundle
