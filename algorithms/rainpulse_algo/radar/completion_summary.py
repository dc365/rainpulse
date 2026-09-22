"""Bounded QC completion metadata; detailed evidence stays in the QC artifact.

This contract is independent of the detector version. It is not a new quality
assessment and must never change a measurement, cause flag, or eligibility bit.
Only Python's standard library is imported so transport tests need no radar stack.
"""
from __future__ import annotations

import json
import math
from collections.abc import Mapping
from typing import Any

MAX_SUMMARY_BYTES = 64 * 1024
MAX_OPTIONAL_BYTES = 8 * 1024
IDENTITY_FIELDS = (
    "schema_version", "engine", "qc_pipeline_version", "qc_profile",
    "decision_version", "flag_definition_version", "parameters_hash", "radar_id",
    "scan_id", "health_state", "review_extension_version", "measured_at",
)
COUNT_FIELDS = (
    "valid_gate_count", "missing_gate_count", "low_quality_gate_count",
    "no_rain_gate_count", "radial_interference_ray_count",
    "radial_interference_gate_count", "ground_clutter_gate_count",
    "sea_clutter_gate_count", "ap_gate_count",
)
NUMBER_FIELDS = ("mean_quality_index", "radial_interference_area_km2")
# Do not put sweeps, nodes, per-gate masks, ray lists or raw reports here.
OPTIONAL_FIELDS = (
    "health_facets", "generalization_summary", "nonprecip_review_summary",
    "module_statuses",
)


class SummaryContractError(ValueError):
    """Invalid core metadata must fail closed, not be silently coerced."""


def encoded_summary(value: Mapping[str, Any]) -> bytes:
    return json.dumps(value, ensure_ascii=True, allow_nan=False, sort_keys=True).encode("utf-8")


def compact_qc_summary(summary: Mapping[str, Any]) -> dict[str, Any]:
    if not isinstance(summary, Mapping):
        raise SummaryContractError("QC summary must be a mapping")
    result: dict[str, Any] = {
        "completion_summary_version": "1.0",
        "summary_object_path": "qc/summary.json",
        "summary_detail_storage": "completed_qc_asset",
    }
    for key in IDENTITY_FIELDS:
        if key not in summary:
            continue
        value = summary[key]
        if not isinstance(value, str) or len(value) > 512:
            raise SummaryContractError(f"{key} must be a string of at most 512 characters")
        result[key] = value
    for key in COUNT_FIELDS:
        if key not in summary:
            continue
        value = summary[key]
        if type(value) is not int or not 0 <= value <= 2**63 - 1:
            raise SummaryContractError(f"{key} must be a non-negative int64")
        result[key] = value
    for key in NUMBER_FIELDS:
        if key not in summary:
            continue
        value = summary[key]
        # An unavailable area stays null; it must not become a fabricated zero.
        if value is not None and (
            type(value) not in (int, float) or not math.isfinite(value) or value < 0
        ):
            raise SummaryContractError(f"{key} must be finite/non-negative or null")
        result[key] = value
    if "operational_eligible" in summary:
        if type(summary["operational_eligible"]) is not bool:
            raise SummaryContractError("operational_eligible must be an actual boolean")
        result["operational_eligible"] = summary["operational_eligible"]

    omitted: list[str] = []
    for key in OPTIONAL_FIELDS:
        if key not in summary:
            continue
        try:
            value = _copy_small_tree(summary[key], depth=0, budget=[256])
            if len(encoded_summary({key: value})) > MAX_OPTIONAL_BYTES:
                raise ValueError("optional summary byte limit")
        except (TypeError, ValueError, OverflowError):
            # Omit a whole optional diagnostic, not selected entries which
            # could turn an incomplete mapping into an apparently complete one.
            omitted.append(key)
        else:
            result[key] = value
    if omitted:
        result["summary_omitted_fields"] = omitted
    if len(encoded_summary(result)) > MAX_SUMMARY_BYTES:
        raise SummaryContractError("QC completion summary exceeds its fixed byte budget")
    return result


def _copy_small_tree(value: Any, *, depth: int, budget: list[int]) -> Any:
    budget[0] -= 1
    if budget[0] < 0 or depth > 4:
        raise ValueError("optional summary exceeds depth/node budget")
    if value is None or type(value) is bool:
        return value
    if type(value) is int:
        if not -(2**63) <= value <= 2**63 - 1:
            raise ValueError("optional integer too large")
        return value
    if type(value) is float:
        if not math.isfinite(value):
            raise ValueError("optional non-finite number")
        return value
    if isinstance(value, str):
        if len(value) > 256:
            raise ValueError("optional string too long")
        return value
    if isinstance(value, Mapping):
        if len(value) > 64:
            raise ValueError("optional mapping too large")
        copied: dict[str, Any] = {}
        for key, item in value.items():
            if not isinstance(key, str) or len(key) > 96:
                raise ValueError("optional key too long")
            copied[key] = _copy_small_tree(item, depth=depth + 1, budget=budget)
        return copied
    if isinstance(value, (list, tuple)):
        if len(value) > 32:
            raise ValueError("optional sequence too large")
        return [_copy_small_tree(item, depth=depth + 1, budget=budget) for item in value]
    raise TypeError("optional summary is not a bounded JSON tree")
