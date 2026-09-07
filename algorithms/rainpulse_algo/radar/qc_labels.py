from __future__ import annotations

from collections.abc import Iterable, Mapping
from datetime import UTC, datetime
from typing import Any

import numpy as np

REQUIRED_QC_LABEL_CATEGORIES = (
    "clear_sky",
    "sea_ap",
    "shallow_warm_rain",
    "stratiform_rain",
    "deep_convection",
    "typhoon_or_mountain",
    "interference_with_precip",
)

_VALID_PARTITIONS = {"development", "holdout"}
_VALID_LABEL_VALUES = {-1, 0, 1}


class RadarQCLabelInputError(ValueError):
    """Raised when a frozen radar-QC label manifest cannot be built safely."""


def build_label_manifest(
    entries: Iterable[Mapping[str, object]],
    *,
    generated_at: datetime,
    frozen_config_sha256: str | None,
    frozen_code_revision: str | None,
) -> dict[str, Any]:
    normalized_entries = [_normalize_entry(entry) for entry in entries]
    _validate_freeze_reference(frozen_config_sha256, frozen_code_revision)
    _validate_single_partition_per_process(normalized_entries)
    if any(entry["partition"] == "holdout" for entry in normalized_entries):
        if frozen_config_sha256 is None or frozen_code_revision is None:
            raise RadarQCLabelInputError("holdout requires frozen config hash and code revision")
    _validate_context_partitions(normalized_entries)

    scans = [entry["manifest_scan_entry"] for entry in normalized_entries]
    partition_summaries = {
        partition: _partition_summary(normalized_entries, partition)
        for partition in ("development", "holdout")
    }
    scans.sort(key=lambda entry: (entry["partition"], entry["process_id"], entry["scan_id"]))

    return {
        "schema_version": "1.0",
        "manifest_version": "radar-qc-label-manifest-v1",
        "generated_at": _format_utc_timestamp(generated_at),
        "required_case_categories": list(REQUIRED_QC_LABEL_CATEGORIES),
        "holdout_locked_after_freeze": frozen_config_sha256 is not None
        and frozen_code_revision is not None,
        "frozen_config_sha256": frozen_config_sha256,
        "frozen_code_revision": frozen_code_revision,
        "partition_summaries": partition_summaries,
        "scans": scans,
    }


def _normalize_entry(entry: Mapping[str, object]) -> dict[str, Any]:
    partition = _require_partition(entry, "partition")
    case_category = _require_case_category(entry, "case_category")
    label_counts = _label_counts(entry)
    return {
        "process_id": _require_text(entry, "process_id"),
        "partition": partition,
        "case_category": case_category,
        "manifest_scan_entry": {
            "process_id": _require_text(entry, "process_id"),
            "partition": partition,
            "case_category": case_category,
            "radar_id": _require_text(entry, "radar_id"),
            "scan_id": _require_text(entry, "scan_id"),
            "volume_end_time_utc": _normalize_timestamp(
                _require_text(entry, "volume_end_time_utc")
            ),
            "input_uri": _require_text(entry, "input_uri"),
            "label_counts": label_counts,
            "annotator": _require_text(entry, "annotator"),
            "label_source": _require_text(entry, "label_source"),
            "label_version": _require_text(entry, "label_version"),
            "review_status": _require_text(entry, "review_status"),
            "temporal_context": _normalize_context_list(entry, "temporal_context"),
            "cross_radar_context": _normalize_context_list(entry, "cross_radar_context"),
        },
    }


def _validate_freeze_reference(
    frozen_config_sha256: str | None,
    frozen_code_revision: str | None,
) -> None:
    if frozen_config_sha256 is not None:
        if len(frozen_config_sha256) != 64 or any(
            character not in "0123456789abcdef" for character in frozen_config_sha256
        ):
            raise RadarQCLabelInputError("frozen_config_sha256 must be a lowercase SHA-256")
    if frozen_code_revision is not None and not frozen_code_revision:
        raise RadarQCLabelInputError("frozen_code_revision must be non-empty when provided")


def _validate_single_partition_per_process(entries: list[dict[str, Any]]) -> None:
    process_partition: dict[str, str] = {}
    for entry in entries:
        process_id = str(entry["process_id"])
        partition = str(entry["partition"])
        previous_partition = process_partition.get(process_id)
        if previous_partition is None:
            process_partition[process_id] = partition
            continue
        if previous_partition != partition:
            raise RadarQCLabelInputError(f"process {process_id} must remain in a single partition")


def _validate_context_partitions(entries: list[dict[str, Any]]) -> None:
    scans: dict[str, dict[str, Any]] = {}
    for entry in entries:
        scan = entry["manifest_scan_entry"]
        if scan["scan_id"] in scans:
            raise RadarQCLabelInputError("duplicate label scan_id")
        scans[scan["scan_id"]] = scan
    for scan in scans.values():
        for context in scan["temporal_context"] + scan["cross_radar_context"]:
            other = scans.get(context["scan_id"])
            if other is None:
                raise RadarQCLabelInputError(
                    "context partition must be represented in label manifest"
                )
            if other["partition"] != scan["partition"]:
                raise RadarQCLabelInputError("context crosses partition boundary")
            if (
                other["radar_id"] != context["radar_id"]
                or other["input_uri"] != context["input_uri"]
            ):
                raise RadarQCLabelInputError("context identity differs from label manifest")


def _partition_summary(entries: list[dict[str, Any]], partition: str) -> dict[str, Any]:
    partition_entries = [entry for entry in entries if entry["partition"] == partition]
    process_ids = {str(entry["process_id"]) for entry in partition_entries}
    case_categories = {}
    for case_category in REQUIRED_QC_LABEL_CATEGORIES:
        category_processes = {
            str(entry["process_id"])
            for entry in partition_entries
            if entry["case_category"] == case_category
        }
        count = len(category_processes)
        case_categories[case_category] = {
            "independent_process_count": count,
            "status": "ready" if count >= 3 else "insufficient_data",
        }
    return {
        "scan_count": len(partition_entries),
        "process_count": len(process_ids),
        "case_categories": case_categories,
    }


def _require_text(entry: Mapping[str, object], key: str) -> str:
    value = entry.get(key)
    if not isinstance(value, str) or not value:
        raise RadarQCLabelInputError(f"{key} must be a non-empty string")
    return value


def _require_partition(entry: Mapping[str, object], key: str) -> str:
    value = _require_text(entry, key)
    if value not in _VALID_PARTITIONS:
        raise RadarQCLabelInputError(f"{key} must be one of {sorted(_VALID_PARTITIONS)}")
    return value


def _require_case_category(entry: Mapping[str, object], key: str) -> str:
    value = _require_text(entry, key)
    if value not in REQUIRED_QC_LABEL_CATEGORIES:
        raise RadarQCLabelInputError(f"unsupported case_category {value}")
    return value


def _label_counts(entry: Mapping[str, object]) -> dict[str, int]:
    values = np.asarray(entry.get("label_values"))
    if values.size == 0:
        raise RadarQCLabelInputError("label_values must not be empty")
    if not np.isin(values, tuple(_VALID_LABEL_VALUES)).all():
        raise RadarQCLabelInputError("label_values must use {-1, 0, 1}")
    return {
        "meteorological": int(np.count_nonzero(values == 0)),
        "non_meteorological": int(np.count_nonzero(values == 1)),
        "uncertain": int(np.count_nonzero(values == -1)),
    }


def _normalize_context_list(entry: Mapping[str, object], key: str) -> list[dict[str, str]]:
    raw = entry.get(key)
    if raw is None:
        return []
    if not isinstance(raw, list):
        raise RadarQCLabelInputError(f"{key} must be a list")
    return [_normalize_context_item(item, key) for item in raw]


def _normalize_context_item(item: object, key: str) -> dict[str, str]:
    if not isinstance(item, Mapping):
        raise RadarQCLabelInputError(f"{key} entries must be mappings")
    return {
        "radar_id": _require_text(item, "radar_id"),
        "scan_id": _require_text(item, "scan_id"),
        "input_uri": _require_text(item, "input_uri"),
    }


def _normalize_timestamp(value: str) -> str:
    return _format_utc_timestamp(_parse_timestamp(value))


def _parse_timestamp(value: str) -> datetime:
    try:
        parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError as error:
        raise RadarQCLabelInputError(f"invalid timestamp {value}") from error
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=UTC)
    return parsed.astimezone(UTC)


def _format_utc_timestamp(value: datetime) -> str:
    if value.tzinfo is None:
        value = value.replace(tzinfo=UTC)
    return value.astimezone(UTC).isoformat().replace("+00:00", "Z")
