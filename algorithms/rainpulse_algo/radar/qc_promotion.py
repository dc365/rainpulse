from __future__ import annotations

import hashlib
from collections.abc import Iterable, Mapping
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import numpy as np
import yaml

from .qc_labels import REQUIRED_QC_LABEL_CATEGORIES


class QCPromotionInputError(ValueError):
    """Raised when QC promotion evidence cannot be summarized safely."""


@dataclass(frozen=True)
class QCPromotionGateConfig:
    anomaly_precision_lower_bound: float
    anomaly_recall_lower_bound: float
    meteorological_retention_lower_bound: float
    qpe_rmse_ratio_upper_bound: float


@dataclass(frozen=True)
class QCPromotionProfile:
    schema_version: str
    profile_version: str
    bootstrap_samples: int
    random_seed: int
    minimum_independent_processes_per_category: int
    strong_echo_dbzh_threshold: float
    gates: QCPromotionGateConfig


_METRIC_SPECS = {
    "anomaly_precision": {
        "candidate_field": "anomaly_precision_candidate",
        "reference_field": "anomaly_precision_reference",
        "comparison": "difference",
        "threshold_field": "anomaly_precision_lower_bound",
    },
    "anomaly_recall": {
        "candidate_field": "anomaly_recall_candidate",
        "reference_field": "anomaly_recall_reference",
        "comparison": "difference",
        "threshold_field": "anomaly_recall_lower_bound",
    },
    "meteorological_retention": {
        "candidate_field": "meteorological_retention_candidate",
        "reference_field": "meteorological_retention_reference",
        "comparison": "difference",
        "threshold_field": "meteorological_retention_lower_bound",
    },
    "qpe_rmse_ratio": {
        "candidate_field": "qpe_rmse_candidate",
        "reference_field": "qpe_rmse_reference",
        "comparison": "ratio",
        "threshold_field": "qpe_rmse_ratio_upper_bound",
    },
}


def load_qc_promotion_profile(path: Path) -> QCPromotionProfile:
    raw = yaml.safe_load(path.read_text())
    try:
        profile = QCPromotionProfile(
            schema_version=str(raw["schema_version"]),
            profile_version=str(raw["profile_version"]),
            bootstrap_samples=int(raw["bootstrap_samples"]),
            random_seed=int(raw["random_seed"]),
            minimum_independent_processes_per_category=int(
                raw["minimum_independent_processes_per_category"]
            ),
            strong_echo_dbzh_threshold=float(raw["strong_echo_dbzh_threshold"]),
            gates=QCPromotionGateConfig(
                anomaly_precision_lower_bound=float(raw["gates"]["anomaly_precision_lower_bound"]),
                anomaly_recall_lower_bound=float(raw["gates"]["anomaly_recall_lower_bound"]),
                meteorological_retention_lower_bound=float(
                    raw["gates"]["meteorological_retention_lower_bound"]
                ),
                qpe_rmse_ratio_upper_bound=float(raw["gates"]["qpe_rmse_ratio_upper_bound"]),
            ),
        )
    except (KeyError, TypeError, ValueError) as error:
        raise QCPromotionInputError(f"invalid QC promotion profile {path}: {error}") from error

    if profile.schema_version != "1.0":
        raise QCPromotionInputError("unsupported QC promotion profile schema")
    if profile.profile_version != "fujian-qc-promotion-v1":
        raise QCPromotionInputError("unsupported QC promotion profile version")
    if profile.bootstrap_samples < 1:
        raise QCPromotionInputError("bootstrap_samples must be positive")
    if profile.minimum_independent_processes_per_category < 1:
        raise QCPromotionInputError("minimum_independent_processes_per_category must be positive")
    return profile


def summarize_qc_promotion(
    rows: Iterable[Mapping[str, object]],
    *,
    profile: QCPromotionProfile,
) -> dict[str, Any]:
    normalized_rows = [_normalize_row(row) for row in rows]
    process_partitions: dict[str, set[str]] = {}
    for row in normalized_rows:
        process_partitions.setdefault(row["process_id"], set()).add(row["partition"])
    if any(len(partitions) != 1 for partitions in process_partitions.values()):
        raise QCPromotionInputError("process must remain in a single partition")
    development_row_count = sum(row["partition"] == "development" for row in normalized_rows)
    normalized_rows = [row for row in normalized_rows if row["partition"] == "holdout"]
    overall_rows = [row for row in normalized_rows if row["subset"] == "overall"]
    scopes = [
        _evaluate_scope(
            "overall",
            overall_rows if overall_rows else normalized_rows,
            profile=profile,
            minimum_process_count=None,
        )
    ]

    subset_names = sorted(
        {f"strong_echo_ge_{profile.strong_echo_dbzh_threshold:g}_dbz"}
        | {str(row["subset"]) for row in normalized_rows if str(row["subset"]) != "overall"}
    )
    for subset_name in subset_names:
        subset_rows = [row for row in normalized_rows if row["subset"] == subset_name]
        scopes.append(
            _evaluate_scope(
                f"subset:{subset_name}",
                subset_rows,
                profile=profile,
                minimum_process_count=profile.minimum_independent_processes_per_category,
            )
        )

    for case_category in REQUIRED_QC_LABEL_CATEGORIES:
        category_rows = [
            row
            for row in normalized_rows
            if row["case_category"] == case_category and row["subset"] == "overall"
        ]
        scopes.append(
            _evaluate_scope(
                f"category:{case_category}",
                category_rows,
                profile=profile,
                minimum_process_count=profile.minimum_independent_processes_per_category,
            )
        )

    statuses = [str(scope["status"]) for scope in scopes]
    if any(status == "failed" for status in statuses):
        overall_status = "failed"
    elif any(status == "insufficient_data" for status in statuses):
        overall_status = "insufficient_data"
    else:
        overall_status = "passed"

    return {
        "schema_version": profile.schema_version,
        "profile_version": profile.profile_version,
        "overall_status": overall_status,
        "evaluated_partition": "holdout",
        "operational_eligible": False,
        "assessment_kind": "engineering_candidate_comparison",
        "holdout_row_count": len(normalized_rows),
        "holdout_process_count": len({row["process_id"] for row in normalized_rows}),
        "excluded_development_row_count": development_row_count,
        "bootstrap_samples": profile.bootstrap_samples,
        "minimum_independent_processes_per_category": (
            profile.minimum_independent_processes_per_category
        ),
        "scopes": scopes,
    }


def _evaluate_scope(
    scope_id: str,
    rows: list[dict[str, Any]],
    *,
    profile: QCPromotionProfile,
    minimum_process_count: int | None,
) -> dict[str, Any]:
    process_count = len({str(row["process_id"]) for row in rows})
    gates = {
        metric_id: _evaluate_metric(
            scope_id,
            metric_id,
            rows,
            profile=profile,
            total_process_count=process_count,
        )
        for metric_id in _METRIC_SPECS
    }

    insufficient_by_policy = (
        minimum_process_count is not None and process_count < minimum_process_count
    )
    if insufficient_by_policy or any(
        gate["status"] == "insufficient_data" for gate in gates.values()
    ):
        status = "insufficient_data"
    elif any(gate["passes"] is False for gate in gates.values()):
        status = "failed"
    else:
        status = "passed"

    return {
        "scope_id": scope_id,
        "status": status,
        "independent_process_count": process_count,
        "gates": gates,
    }


def _evaluate_metric(
    scope_id: str,
    metric_id: str,
    rows: list[dict[str, Any]],
    *,
    profile: QCPromotionProfile,
    total_process_count: int,
) -> dict[str, Any]:
    spec = _METRIC_SPECS[metric_id]
    process_values: dict[str, list[float]] = {}
    missing_pair_count = 0
    for row in rows:
        candidate_value = row.get(spec["candidate_field"])
        reference_value = row.get(spec["reference_field"])
        if candidate_value is None or reference_value is None:
            missing_pair_count += 1
            continue
        candidate = float(candidate_value)
        reference = float(reference_value)
        if not np.isfinite(candidate) or not np.isfinite(reference):
            missing_pair_count += 1
            continue
        if spec["comparison"] == "ratio":
            if reference <= 0.0:
                missing_pair_count += 1
                continue
            metric_value = candidate / reference
        else:
            metric_value = candidate - reference
        process_id = str(row["process_id"])
        process_values.setdefault(process_id, []).append(float(metric_value))

    process_means = {
        process_id: float(np.mean(values))
        for process_id, values in process_values.items()
        if values
    }
    interval: list[float | None]
    mean_value: float | None
    if process_means:
        values = np.asarray(list(process_means.values()), dtype="float64")
        mean_value = float(np.mean(values))
        interval = _bootstrap_interval(
            values,
            bootstrap_samples=profile.bootstrap_samples,
            seed=_metric_seed(profile.random_seed, scope_id, metric_id),
        )
    else:
        mean_value = None
        interval = [None, None]

    threshold = float(getattr(profile.gates, str(spec["threshold_field"])))
    complete_process_coverage = (
        total_process_count > 0 and len(process_means) == total_process_count
    )
    if not complete_process_coverage or missing_pair_count > 0:
        status = "insufficient_data"
        passes: bool | None = None
    else:
        status = "computed"
        if spec["comparison"] == "ratio":
            upper_bound = interval[1]
            passes = upper_bound is not None and upper_bound <= threshold
        else:
            lower_bound = interval[0]
            passes = lower_bound is not None and lower_bound >= threshold

    return {
        "status": status,
        "comparison": str(spec["comparison"]),
        "threshold": threshold,
        "mean_value": mean_value,
        "interval_95": interval,
        "independent_process_count": len(process_means),
        "missing_pair_count": missing_pair_count,
        "passes": passes,
    }


def _bootstrap_interval(
    values: np.ndarray,
    *,
    bootstrap_samples: int,
    seed: int,
) -> list[float]:
    rng = np.random.default_rng(seed)
    means = np.empty(bootstrap_samples, dtype="float64")
    for index in range(bootstrap_samples):
        sampled = rng.choice(values, size=values.size, replace=True)
        means[index] = float(np.mean(sampled))
    return [
        float(np.percentile(means, 2.5)),
        float(np.percentile(means, 97.5)),
    ]


def _metric_seed(base_seed: int, scope_id: str, metric_id: str) -> int:
    digest = hashlib.sha256(f"{scope_id}:{metric_id}".encode()).digest()
    derived = int.from_bytes(digest[:8], "big")
    return (base_seed + derived) % (2**32)


def _normalize_row(row: Mapping[str, object]) -> dict[str, Any]:
    case_category = _require_text(row, "case_category")
    if case_category not in REQUIRED_QC_LABEL_CATEGORIES:
        raise QCPromotionInputError(f"unsupported case_category {case_category}")
    partition = _require_text(row, "partition")
    if partition not in {"development", "holdout"}:
        raise QCPromotionInputError("partition must be development or holdout")
    return {
        "process_id": _require_text(row, "process_id"),
        "case_category": case_category,
        "subset": _require_text(row, "subset"),
        "partition": partition,
        "anomaly_precision_candidate": _maybe_float(row, "anomaly_precision_candidate"),
        "anomaly_precision_reference": _maybe_float(row, "anomaly_precision_reference"),
        "anomaly_recall_candidate": _maybe_float(row, "anomaly_recall_candidate"),
        "anomaly_recall_reference": _maybe_float(row, "anomaly_recall_reference"),
        "meteorological_retention_candidate": _maybe_float(
            row,
            "meteorological_retention_candidate",
        ),
        "meteorological_retention_reference": _maybe_float(
            row,
            "meteorological_retention_reference",
        ),
        "qpe_rmse_candidate": _maybe_float(row, "qpe_rmse_candidate"),
        "qpe_rmse_reference": _maybe_float(row, "qpe_rmse_reference"),
    }


def _require_text(row: Mapping[str, object], key: str) -> str:
    value = row.get(key)
    if not isinstance(value, str) or not value:
        raise QCPromotionInputError(f"{key} must be a non-empty string")
    return value


def _maybe_float(row: Mapping[str, object], key: str) -> float | None:
    value = row.get(key)
    if value is None:
        return None
    try:
        return float(value)
    except (TypeError, ValueError) as error:
        raise QCPromotionInputError(f"{key} must be numeric when provided") from error
