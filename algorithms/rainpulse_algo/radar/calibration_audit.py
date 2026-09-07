from __future__ import annotations

import argparse
import json
import sys
from collections import defaultdict
from pathlib import Path
from typing import Any

from .calibration import (
    CalibrationInputError,
    CalibrationProfile,
    CalibrationReferenceManifest,
    CoefficientTable,
    CoefficientTableEntry,
    load_calibration_profile,
    load_calibration_reference_manifest,
    load_coefficient_table,
)
from .config import RadarConfigError, load_radar_config


class AuditInputError(RuntimeError):
    pass


def main() -> int:
    parser = argparse.ArgumentParser(
        description="Audit offline C2-P3 radar calibration readiness from frozen shadow inputs."
    )
    parser.add_argument("--profile", type=Path, required=True)
    parser.add_argument("--reference-manifest", type=Path, required=True)
    parser.add_argument("--coefficient-table", type=Path, required=True)
    parser.add_argument("--relative-bias-report", type=Path, required=True)
    parser.add_argument("--radar-config-dir", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()

    try:
        profile = load_calibration_profile(args.profile)
        reference_manifest = load_calibration_reference_manifest(
            args.reference_manifest,
            profile=profile,
        )
        coefficient_table = load_coefficient_table(
            args.coefficient_table,
            profile=profile,
            reference_manifest=reference_manifest,
        )
        relative_bias_pairs = _load_relative_bias_pairs(args.relative_bias_report)
        radar_inventory = _load_radar_inventory(
            args.radar_config_dir,
            expected_radar_band=profile.radar_band,
        )
        report = _build_report(
            profile=profile,
            reference_manifest=reference_manifest,
            coefficient_table=coefficient_table,
            relative_bias_pairs=relative_bias_pairs,
            radar_inventory=radar_inventory,
        )
        _write_report(args.output, report)
        if report["summary"]["blocked_coefficient_count"] > 0:
            return 3
        return 0
    except (AuditInputError, CalibrationInputError, RadarConfigError, ValueError) as error:
        print(str(error), file=sys.stderr)
        return 2


def _load_relative_bias_pairs(path: Path) -> list[dict[str, Any]]:
    if not path.is_file():
        raise AuditInputError(f"relative-bias report does not exist: {path}")
    value = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(value, dict):
        raise AuditInputError("relative-bias report must be a JSON object")
    aggregates = value.get("aggregates")
    if not isinstance(aggregates, list):
        raise AuditInputError("relative-bias report must contain an aggregates array")
    normalized: list[dict[str, Any]] = []
    for item in aggregates:
        if not isinstance(item, dict):
            raise AuditInputError("relative-bias aggregate entries must be objects")
        primary_radar_id = str(item.get("primary_radar_id", "")).strip()
        reference_radar_id = str(item.get("reference_radar_id", "")).strip()
        status = str(item.get("status", "")).strip()
        if not primary_radar_id or not reference_radar_id or not status:
            raise AuditInputError(
                "relative-bias aggregate entries require primary_radar_id, "
                "reference_radar_id and status"
            )
        normalized.append(item)
    return normalized


def _load_radar_inventory(
    path: Path,
    *,
    expected_radar_band: str,
) -> dict[str, dict[str, Any]]:
    if not path.is_dir():
        raise AuditInputError(f"radar-config-dir is not a directory: {path}")
    inventory: dict[str, dict[str, Any]] = {}
    for config_path in sorted(path.glob("*.yaml")):
        config = load_radar_config(config_path)
        inventory[config.radar_id.lower()] = {
            "radar_id": config.radar_id,
            "radar_band": str(config.hardware.get("radar_band", "")),
        }
    if not inventory:
        raise AuditInputError("radar-config-dir contains no radar configuration files")
    for radar in inventory.values():
        if radar["radar_band"] != expected_radar_band:
            raise AuditInputError(
                "radar calibration audit requires radar configs that match the profile radar band"
            )
    return inventory


def _build_report(
    *,
    profile: CalibrationProfile,
    reference_manifest: CalibrationReferenceManifest,
    coefficient_table: CoefficientTable,
    relative_bias_pairs: list[dict[str, Any]],
    radar_inventory: dict[str, dict[str, Any]],
) -> dict[str, Any]:
    relative_bias_status_by_radar = _relative_bias_status_by_radar(relative_bias_pairs)
    coefficients = [
        _evaluate_coefficient_entry(
            entry,
            reference_manifest=reference_manifest,
            relative_bias_status_by_radar=relative_bias_status_by_radar,
            radar_inventory=radar_inventory,
        )
        for entry in coefficient_table.entries
    ]
    ready_coefficient_count = sum(1 for item in coefficients if item["status"] == "ready")
    blocked_coefficient_count = len(coefficients) - ready_coefficient_count
    overall_status = (
        "ready_for_shadow_coefficients" if blocked_coefficient_count == 0 else "blocked"
    )
    return {
        "schema_version": "1.0",
        "mode": "radar_calibration_shadow_audit",
        "profile_version": profile.profile_version,
        "coefficient_table_version": coefficient_table.table_version,
        "summary": {
            "overall_status": overall_status,
            "ready_coefficient_count": ready_coefficient_count,
            "blocked_coefficient_count": blocked_coefficient_count,
        },
        "reference_manifest": {
            "manifest_version": reference_manifest.manifest_version,
            "fitting_process_count": len(reference_manifest.fitting_process_ids),
            "validation_process_count": len(reference_manifest.validation_process_ids),
            "fitting_case_count": len(reference_manifest.fitting_case_ids),
            "validation_case_count": len(reference_manifest.validation_case_ids),
        },
        "relative_bias_pairs": relative_bias_pairs,
        "coefficients": coefficients,
    }


def _evaluate_coefficient_entry(
    entry: CoefficientTableEntry,
    *,
    reference_manifest: CalibrationReferenceManifest,
    relative_bias_status_by_radar: dict[str, str],
    radar_inventory: dict[str, dict[str, Any]],
) -> dict[str, Any]:
    blocked_reasons: list[str] = []
    radar_record = radar_inventory.get(entry.radar_id.lower())
    if radar_record is None:
        blocked_reasons.append("radar_config_missing")
    if not set(entry.fitted_process_ids) <= set(reference_manifest.fitting_process_ids):
        blocked_reasons.append("fitted_process_ids_outside_manifest")
    if not set(entry.validation_process_ids) <= set(reference_manifest.validation_process_ids):
        blocked_reasons.append("validation_process_ids_outside_manifest")
    relative_bias_status = relative_bias_status_by_radar.get(entry.radar_id.lower())
    if relative_bias_status is None:
        blocked_reasons.append("relative_bias_pair_missing")
    elif relative_bias_status != "computed":
        blocked_reasons.append(f"relative_bias_status_{relative_bias_status}")
    return {
        "radar_id": entry.radar_id,
        "coefficient_state": entry.coefficient_state,
        "status": "ready" if not blocked_reasons else "blocked",
        "blocked_reasons": blocked_reasons,
        "fitted_process_ids": list(entry.fitted_process_ids),
        "validation_process_ids": list(entry.validation_process_ids),
        "applicable_temperature_range_c": list(entry.applicable_temperature_range_c),
    }


def _relative_bias_status_by_radar(
    relative_bias_pairs: list[dict[str, Any]],
) -> dict[str, str]:
    statuses: dict[str, list[str]] = defaultdict(list)
    for item in relative_bias_pairs:
        statuses[str(item["primary_radar_id"]).lower()].append(str(item["status"]))
    resolved: dict[str, str] = {}
    for radar_id, values in statuses.items():
        if any(value == "computed" for value in values):
            resolved[radar_id] = "computed"
        else:
            resolved[radar_id] = sorted(set(values))[0]
    return resolved


def _write_report(path: Path, report: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
