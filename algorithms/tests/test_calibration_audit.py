from __future__ import annotations

import json
import sys
from pathlib import Path

import pytest
import yaml

from rainpulse_algo.radar import calibration_audit

REPOSITORY_ROOT = Path(__file__).resolve().parents[2]
PROFILE_PATH = (
    REPOSITORY_ROOT / "configs" / "verification" / "fujian-radar-calibration-shadow-v1.yaml"
)
RADAR_CONFIG_DIR = REPOSITORY_ROOT / "configs" / "radars" / "fujian-20260828"


def _reference_manifest() -> dict[str, object]:
    references = []
    for index in range(3):
        references.append(
            {
                "process_id": f"storm-fit-00{index + 1}",
                "case_id": f"case-fit-00{index + 1}",
                "role": "coefficient_fitting",
                "truth_kind": "gauge_accumulation_1h",
                "radar_ids": ["z9598", "z9593"],
                "start_time_utc": f"2026-08-28T0{index + 2}:00:00Z",
                "end_time_utc": f"2026-08-28T0{index + 3}:00:00Z",
                "reference_uri": f"s3://rainpulse/calibration/fit-00{index + 1}.parquet",
                "source_sha256": f"{index + 1}" * 64,
            }
        )
        references.append(
            {
                "process_id": f"storm-val-10{index + 1}",
                "case_id": f"case-val-10{index + 1}",
                "role": "independent_validation",
                "truth_kind": "reference_qpe_1h",
                "radar_ids": ["z9598", "z9593"],
                "start_time_utc": f"2026-08-29T0{index + 2}:00:00Z",
                "end_time_utc": f"2026-08-29T0{index + 3}:00:00Z",
                "reference_uri": f"s3://rainpulse/calibration/val-10{index + 1}.parquet",
                "source_sha256": f"{index + 4}" * 64,
            }
        )
    return {
        "schema_version": "1.0",
        "manifest_version": "radar-calibration-reference-manifest-v1",
        "source_calibration_profile_version": "fujian-radar-calibration-shadow-v1",
        "generated_at": "2026-09-07T08:00:00Z",
        "radar_band": "S",
        "references": references,
    }


def _coefficient_table() -> dict[str, object]:
    fitted = ["storm-fit-001", "storm-fit-002", "storm-fit-003"]
    validation = ["storm-val-101", "storm-val-102", "storm-val-103"]
    return {
        "schema_version": "1.0",
        "table_version": "fujian-s-band-attenuation-coefficients-v1",
        "artifact_contract_version": "1.0",
        "source_calibration_profile_version": "fujian-radar-calibration-shadow-v1",
        "reference_manifest_version": "fujian-radar-calibration-reference-20260907-v1",
        "radar_band": "S",
        "entries": [
            {
                "radar_id": "z9598",
                "coefficient_state": "verified_shadow_use",
                "coefficient_a": 0.04,
                "exponent_b": 1.0,
                "applicable_temperature_range_c": [5.0, 35.0],
                "fitted_process_ids": fitted,
                "validation_process_ids": validation,
            },
            {
                "radar_id": "z9593",
                "coefficient_state": "verified_shadow_use",
                "coefficient_a": 0.038,
                "exponent_b": 1.02,
                "applicable_temperature_range_c": [5.0, 35.0],
                "fitted_process_ids": fitted,
                "validation_process_ids": validation,
            },
        ],
    }


def _relative_bias_report(status: str = "computed") -> dict[str, object]:
    return {
        "schema_version": "1.0",
        "mode": "relative_bias_shadow_audit",
        "aggregates": [
            {
                "primary_radar_id": "z9598",
                "reference_radar_id": "z9593",
                "comparison_semantics": "primary_minus_reference_dbz",
                "grouping_mode": "process_id",
                "status": status,
                "truth_designation_enabled": False,
                "independent_group_count": 3,
                "comparable_scan_count": 3,
                "comparable_gate_count": 360,
                "median_relative_bias_dbz": 0.8,
                "relative_bias_interval_dbz": {"lower": 0.5, "upper": 1.1},
                "group_medians_dbz": {
                    "storm-fit-001": 0.7,
                    "storm-fit-002": 0.8,
                    "storm-fit-003": 0.9,
                },
            },
            {
                "primary_radar_id": "z9593",
                "reference_radar_id": "z9598",
                "comparison_semantics": "primary_minus_reference_dbz",
                "grouping_mode": "process_id",
                "status": status,
                "truth_designation_enabled": False,
                "independent_group_count": 3,
                "comparable_scan_count": 3,
                "comparable_gate_count": 360,
                "median_relative_bias_dbz": -0.8,
                "relative_bias_interval_dbz": {"lower": -1.1, "upper": -0.5},
                "group_medians_dbz": {
                    "storm-fit-001": -0.7,
                    "storm-fit-002": -0.8,
                    "storm-fit-003": -0.9,
                },
            },
        ],
    }


def test_calibration_audit_reports_ready_shadow_freeze(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    manifest_path = tmp_path / "reference-manifest.yaml"
    manifest_path.write_text(
        yaml.safe_dump(_reference_manifest(), sort_keys=False),
        encoding="utf-8",
    )
    table_path = tmp_path / "coefficient-table.yaml"
    table_path.write_text(
        yaml.safe_dump(_coefficient_table(), sort_keys=False),
        encoding="utf-8",
    )
    relative_bias_path = tmp_path / "relative-bias-report.json"
    relative_bias_path.write_text(
        json.dumps(_relative_bias_report(), ensure_ascii=False),
        encoding="utf-8",
    )
    output = tmp_path / "calibration-audit-report.json"

    monkeypatch.setattr(
        sys,
        "argv",
        [
            "calibration_audit.py",
            "--profile",
            str(PROFILE_PATH),
            "--reference-manifest",
            str(manifest_path),
            "--coefficient-table",
            str(table_path),
            "--relative-bias-report",
            str(relative_bias_path),
            "--radar-config-dir",
            str(RADAR_CONFIG_DIR),
            "--output",
            str(output),
        ],
    )

    exit_code = calibration_audit.main()

    report = json.loads(output.read_text(encoding="utf-8"))
    assert exit_code == 0
    assert report["summary"]["overall_status"] == "ready_for_shadow_coefficients"
    assert report["summary"]["ready_coefficient_count"] == 2
    assert report["summary"]["blocked_coefficient_count"] == 0
    assert report["reference_manifest"]["fitting_process_count"] == 3
    assert report["reference_manifest"]["validation_process_count"] == 3
    assert report["relative_bias_pairs"][0]["status"] == "computed"


def test_calibration_audit_blocks_engineering_only_overlap_evidence(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    manifest_path = tmp_path / "reference-manifest.yaml"
    manifest_path.write_text(
        yaml.safe_dump(_reference_manifest(), sort_keys=False),
        encoding="utf-8",
    )
    table_path = tmp_path / "coefficient-table.yaml"
    table_path.write_text(
        yaml.safe_dump(_coefficient_table(), sort_keys=False),
        encoding="utf-8",
    )
    relative_bias_path = tmp_path / "relative-bias-report.json"
    relative_bias_path.write_text(
        json.dumps(_relative_bias_report(status="engineering_only"), ensure_ascii=False),
        encoding="utf-8",
    )
    output = tmp_path / "calibration-audit-report.json"

    monkeypatch.setattr(
        sys,
        "argv",
        [
            "calibration_audit.py",
            "--profile",
            str(PROFILE_PATH),
            "--reference-manifest",
            str(manifest_path),
            "--coefficient-table",
            str(table_path),
            "--relative-bias-report",
            str(relative_bias_path),
            "--radar-config-dir",
            str(RADAR_CONFIG_DIR),
            "--output",
            str(output),
        ],
    )

    exit_code = calibration_audit.main()

    report = json.loads(output.read_text(encoding="utf-8"))
    assert exit_code == 3
    assert report["summary"]["overall_status"] == "blocked"
    assert report["summary"]["blocked_coefficient_count"] == 2
    assert "relative_bias_status_engineering_only" in report["coefficients"][0]["blocked_reasons"]