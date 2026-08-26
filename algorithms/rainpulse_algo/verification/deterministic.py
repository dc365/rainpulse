from __future__ import annotations

from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from typing import Any

import numpy as np
from scipy import ndimage


class VerificationInputError(ValueError):
    """Raised when deterministic forecast fields cannot be compared fairly."""


@dataclass(frozen=True)
class DeterministicForecast:
    rate_mm_h: np.ndarray
    valid_mask: np.ndarray


def _ratio(numerator: int, denominator: int) -> float:
    return float(numerator / denominator) if denominator else float("nan")


def _fractions_skill_score(
    observed_event: np.ndarray,
    forecast_event: np.ndarray,
    common_valid: np.ndarray,
    window_pixels: int,
) -> float:
    support = ndimage.uniform_filter(
        common_valid.astype("float64"), window_pixels, mode="constant", cval=0.0
    )
    observed_count = ndimage.uniform_filter(
        (observed_event & common_valid).astype("float64"),
        window_pixels,
        mode="constant",
        cval=0.0,
    )
    forecast_count = ndimage.uniform_filter(
        (forecast_event & common_valid).astype("float64"),
        window_pixels,
        mode="constant",
        cval=0.0,
    )
    usable = common_valid & (support > 0.0)
    if not np.any(usable):
        return float("nan")
    observed_fraction = observed_count[usable] / support[usable]
    forecast_fraction = forecast_count[usable] / support[usable]
    denominator = np.sum(observed_fraction**2 + forecast_fraction**2)
    if denominator == 0.0:
        return float("nan")
    return float(1.0 - np.sum((forecast_fraction - observed_fraction) ** 2) / denominator)


def score_deterministic_forecasts(
    truth_rate_mm_h: np.ndarray,
    truth_valid_mask: np.ndarray,
    forecasts: Mapping[str, DeterministicForecast],
    *,
    lead_minutes: Sequence[int],
    thresholds_mm_h: Sequence[float],
    windows_pixels: Sequence[int],
    pixel_spacing_km: float,
) -> list[dict[str, Any]]:
    """Score deterministic models on one common validity mask for every lead."""

    truth = np.asarray(truth_rate_mm_h, dtype="float32")
    truth_valid = np.asarray(truth_valid_mask) == 1
    if truth.ndim != 3 or truth_valid.shape != truth.shape:
        raise VerificationInputError("truth fields must be lead x lat x lon with equal shapes")
    if len(lead_minutes) != truth.shape[0] or not forecasts:
        raise VerificationInputError(
            "lead times and forecast collection must be non-empty and aligned"
        )
    if not thresholds_mm_h or any(value < 0.0 for value in thresholds_mm_h):
        raise VerificationInputError("verification thresholds must be non-negative")
    if not windows_pixels or any(value < 1 or value % 2 == 0 for value in windows_pixels):
        raise VerificationInputError("FSS windows must be positive odd pixel counts")
    if pixel_spacing_km <= 0.0:
        raise VerificationInputError("pixel spacing must be positive")

    normalized: dict[str, tuple[np.ndarray, np.ndarray]] = {}
    common_valid = truth_valid.copy()
    for model, forecast in forecasts.items():
        rate = np.asarray(forecast.rate_mm_h, dtype="float32")
        valid = np.asarray(forecast.valid_mask) == 1
        if rate.shape != truth.shape or valid.shape != truth.shape:
            raise VerificationInputError(f"forecast {model} shape differs from truth")
        normalized[model] = rate, valid
        common_valid &= valid

    rows: list[dict[str, Any]] = []
    cell_count = truth.shape[1] * truth.shape[2]
    for lead_index, lead_minute in enumerate(lead_minutes):
        common = common_valid[lead_index]
        truth_values = truth[lead_index][common]
        if np.any(~np.isfinite(truth_values)):
            raise VerificationInputError("truth has non-finite values inside the common mask")
        for model, (forecast_rate, forecast_valid) in normalized.items():
            forecast_values = forecast_rate[lead_index][common]
            if np.any(~np.isfinite(forecast_values)):
                raise VerificationInputError(
                    f"forecast {model} has non-finite values inside the common mask"
                )
            if truth_values.size:
                difference = forecast_values - truth_values
                mae = float(np.mean(np.abs(difference)))
                rmse = float(np.sqrt(np.mean(difference**2)))
                mean_error = float(np.mean(difference))
            else:
                mae = rmse = mean_error = float("nan")

            for threshold in thresholds_mm_h:
                observed_event = truth[lead_index] >= threshold
                forecast_event = forecast_rate[lead_index] >= threshold
                hits = int(np.count_nonzero(common & observed_event & forecast_event))
                misses = int(np.count_nonzero(common & observed_event & ~forecast_event))
                false_alarms = int(np.count_nonzero(common & ~observed_event & forecast_event))
                correct_negatives = int(
                    np.count_nonzero(common & ~observed_event & ~forecast_event)
                )
                for window in windows_pixels:
                    rows.append(
                        {
                            "model": model,
                            "lead_minutes": int(lead_minute),
                            "threshold_mm_h": float(threshold),
                            "window_pixels": int(window),
                            "window_km": float(window * pixel_spacing_km),
                            "hits": hits,
                            "misses": misses,
                            "false_alarms": false_alarms,
                            "correct_negatives": correct_negatives,
                            "csi": _ratio(hits, hits + misses + false_alarms),
                            "pod": _ratio(hits, hits + misses),
                            "far": _ratio(false_alarms, hits + false_alarms),
                            "fss": _fractions_skill_score(
                                observed_event,
                                forecast_event,
                                common,
                                window,
                            ),
                            "mae_mm_h": mae,
                            "rmse_mm_h": rmse,
                            "mean_error_mm_h": mean_error,
                            "truth_coverage": float(
                                np.count_nonzero(truth_valid[lead_index]) / cell_count
                            ),
                            "forecast_coverage": float(
                                np.count_nonzero(forecast_valid[lead_index]) / cell_count
                            ),
                            "common_coverage": float(np.count_nonzero(common) / cell_count),
                        }
                    )
    return rows


def summarize_fss_skill(
    rows: Sequence[Mapping[str, Any]],
    *,
    thresholds_mm_h: Sequence[float] = (1.0, 5.0, 10.0),
    maximum_lead_minutes: int = 60,
    window_pixels: int = 11,
    required_positive_cases: int = 3,
    required_wet_cases: int = 4,
    bootstrap_samples: int = 2000,
    random_seed: int = 16016,
) -> dict[str, Any]:
    """Summarize paired LK FSS skill with case/issue block bootstrap intervals."""

    if bootstrap_samples < 1:
        raise VerificationInputError("bootstrap_samples must be positive")
    wet_case_ids = sorted(
        {str(row["case_id"]) for row in rows if row.get("case_category") == "wet"}
    )
    comparisons: list[dict[str, Any]] = []
    rng = np.random.default_rng(random_seed)
    for baseline in ("persistence", "translation"):
        for threshold in thresholds_mm_h:
            issue_scores: dict[tuple[str, str, str], list[float]] = {}
            for row in rows:
                if (
                    row.get("case_category") != "wet"
                    or int(row["lead_minutes"]) > maximum_lead_minutes
                    or int(row["window_pixels"]) != window_pixels
                    or not np.isclose(float(row["threshold_mm_h"]), threshold)
                    or row.get("model") not in {"lk", baseline}
                ):
                    continue
                fss = float(row["fss"])
                if not np.isfinite(fss):
                    continue
                key = (str(row["case_id"]), str(row["issue_time_utc"]), str(row["model"]))
                issue_scores.setdefault(key, []).append(fss)

            issue_means = {key: float(np.mean(values)) for key, values in issue_scores.items()}
            case_differences: dict[str, list[float]] = {}
            for case_id in wet_case_ids:
                issue_ids = {
                    issue_id
                    for candidate_case, issue_id, model in issue_means
                    if candidate_case == case_id and model == "lk"
                }
                for issue_id in issue_ids:
                    lk_key = (case_id, issue_id, "lk")
                    baseline_key = (case_id, issue_id, baseline)
                    if baseline_key in issue_means:
                        case_differences.setdefault(case_id, []).append(
                            issue_means[lk_key] - issue_means[baseline_key]
                        )

            case_means = {
                case_id: float(np.mean(values))
                for case_id, values in case_differences.items()
                if values
            }
            bootstrap_values: list[float] = []
            evaluable_cases = sorted(case_means)
            if evaluable_cases:
                for _ in range(bootstrap_samples):
                    sampled_cases = rng.choice(
                        evaluable_cases,
                        size=len(evaluable_cases),
                        replace=True,
                    )
                    sampled_case_means: list[float] = []
                    for case_id in sampled_cases:
                        values = np.asarray(case_differences[str(case_id)], dtype="float64")
                        sampled_case_means.append(
                            float(np.mean(rng.choice(values, size=len(values), replace=True)))
                        )
                    bootstrap_values.append(float(np.mean(sampled_case_means)))
                interval: list[float | None] = [
                    float(np.percentile(bootstrap_values, 2.5)),
                    float(np.percentile(bootstrap_values, 97.5)),
                ]
                mean_difference: float | None = float(np.mean(list(case_means.values())))
            else:
                interval = [None, None]
                mean_difference = None

            positive_count = sum(value > 0.0 for value in case_means.values())
            comparisons.append(
                {
                    "baseline": baseline,
                    "threshold_mm_h": float(threshold),
                    "maximum_lead_minutes": maximum_lead_minutes,
                    "window_pixels": window_pixels,
                    "positive_case_count": positive_count,
                    "evaluable_case_count": len(case_means),
                    "total_wet_case_count": len(wet_case_ids),
                    "passes_case_gate": positive_count >= required_positive_cases,
                    "mean_fss_difference": mean_difference,
                    "mean_difference_95pct_interval": interval,
                    "bootstrap_sample_count": bootstrap_samples,
                    "case_mean_differences": case_means,
                }
            )

    if len(wet_case_ids) < required_wet_cases or any(
        comparison["evaluable_case_count"] < len(wet_case_ids)
        for comparison in comparisons
    ):
        status = "insufficient_evidence"
    else:
        persistence_passes = all(
            comparison["passes_case_gate"]
            for comparison in comparisons
            if comparison["baseline"] == "persistence"
        )
        translation_passes = all(
            comparison["passes_case_gate"]
            for comparison in comparisons
            if comparison["baseline"] == "translation"
        )
        if persistence_passes and translation_passes:
            status = "lk_supported"
        elif persistence_passes:
            status = "translation_baseline_retained"
        else:
            status = "skill_not_demonstrated"
    return {
        "status": status,
        "comparison_metric": "FSS",
        "comparisons": comparisons,
    }
