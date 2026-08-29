from __future__ import annotations

from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from typing import Any, Literal

import numpy as np
from scipy import ndimage


class VerificationInputError(ValueError):
    """Raised when deterministic forecast fields cannot be compared fairly."""


@dataclass(frozen=True)
class DeterministicForecast:
    rate_mm_h: np.ndarray
    valid_mask: np.ndarray
    domain_valid_mask: np.ndarray | None = None


ValidityDomain = Literal["common", "truth"]


def _ratio(numerator: int, denominator: int) -> float:
    return float(numerator / denominator) if denominator else float("nan")


def _fractions_skill_score(
    observed_event: np.ndarray,
    forecast_event: np.ndarray,
    evaluation_mask: np.ndarray,
    window_pixels: int,
) -> float:
    support = ndimage.uniform_filter(
        evaluation_mask.astype("float64"), window_pixels, mode="constant", cval=0.0
    )
    observed_count = ndimage.uniform_filter(
        (observed_event & evaluation_mask).astype("float64"),
        window_pixels,
        mode="constant",
        cval=0.0,
    )
    forecast_count = ndimage.uniform_filter(
        (forecast_event & evaluation_mask).astype("float64"),
        window_pixels,
        mode="constant",
        cval=0.0,
    )
    usable = evaluation_mask & (support > 0.0)
    if not np.any(usable):
        return float("nan")
    observed_fraction = observed_count[usable] / support[usable]
    forecast_fraction = forecast_count[usable] / support[usable]
    denominator = np.sum(observed_fraction**2 + forecast_fraction**2)
    if denominator == 0.0:
        return float("nan")
    return float(1.0 - np.sum((forecast_fraction - observed_fraction) ** 2) / denominator)


def _window_specs(
    windows_pixels: Sequence[int],
    windows_km: Sequence[float],
    pixel_spacing_km: float,
) -> tuple[tuple[int, float, float], ...]:
    if pixel_spacing_km <= 0.0:
        raise VerificationInputError("pixel spacing must be positive")
    specs: list[tuple[int, float, float]] = []
    for value in windows_pixels:
        pixels = int(value)
        if pixels < 1 or pixels % 2 == 0:
            raise VerificationInputError("FSS pixel windows must be positive odd counts")
        specs.append((pixels, float(pixels * pixel_spacing_km), float(pixels * pixel_spacing_km)))
    for value in windows_km:
        target = float(value)
        if target <= 0.0:
            raise VerificationInputError("FSS physical windows must be positive")
        pixels = max(1, int(round(target / pixel_spacing_km)))
        if pixels % 2 == 0:
            candidates = (max(1, pixels - 1), pixels + 1)
            pixels = min(
                candidates,
                key=lambda candidate: (
                    abs(candidate * pixel_spacing_km - target),
                    candidate,
                ),
            )
        actual = float(pixels * pixel_spacing_km)
        specs.append((pixels, actual, target))
    if not specs:
        raise VerificationInputError("at least one FSS window is required")
    unique: dict[tuple[int, float], tuple[int, float, float]] = {}
    for item in specs:
        unique[(item[0], item[2])] = item
    return tuple(unique.values())


def score_deterministic_forecasts(
    truth_rate_mm_h: np.ndarray,
    truth_valid_mask: np.ndarray,
    forecasts: Mapping[str, DeterministicForecast],
    *,
    lead_minutes: Sequence[int],
    thresholds_mm_h: Sequence[float],
    windows_pixels: Sequence[int],
    pixel_spacing_km: float,
    validity_domain: ValidityDomain = "common",
    windows_km: Sequence[float] = (),
) -> list[dict[str, Any]]:
    """Score deterministic forecasts on either a common or fixed truth domain.

    ``common`` preserves the original like-for-like comparison. ``truth`` keeps
    the observed domain fixed and treats unavailable model cells as no forecast,
    so a model cannot improve its score by silently losing spatial coverage.
    """

    truth = np.asarray(truth_rate_mm_h, dtype="float32")
    truth_valid = np.asarray(truth_valid_mask) == 1
    if truth.ndim != 3 or truth_valid.shape != truth.shape:
        raise VerificationInputError("truth fields must be lead x lat x lon with equal shapes")
    leads = tuple(int(value) for value in lead_minutes)
    if len(leads) != truth.shape[0] or not forecasts:
        raise VerificationInputError(
            "lead times and forecast collection must be non-empty and aligned"
        )
    if tuple(sorted(leads)) != leads or any(value <= 0 for value in leads):
        raise VerificationInputError("lead minutes must be positive and increasing")
    if not thresholds_mm_h or any(value < 0.0 for value in thresholds_mm_h):
        raise VerificationInputError("verification thresholds must be non-negative")
    if validity_domain not in {"common", "truth"}:
        raise VerificationInputError("validity_domain must be common or truth")
    window_specs = _window_specs(windows_pixels, windows_km, pixel_spacing_km)

    normalized: dict[str, tuple[np.ndarray, np.ndarray, np.ndarray | None]] = {}
    common_valid = truth_valid.copy()
    for model, forecast in forecasts.items():
        rate = np.asarray(forecast.rate_mm_h, dtype="float32")
        valid = np.asarray(forecast.valid_mask) == 1
        if rate.shape != truth.shape or valid.shape != truth.shape:
            raise VerificationInputError(f"forecast {model} shape differs from truth")
        if np.any(~np.isfinite(rate[valid])) or np.any(rate[valid] < 0.0):
            raise VerificationInputError(f"forecast {model} has invalid values in valid cells")
        domain_valid: np.ndarray | None = None
        if forecast.domain_valid_mask is not None:
            raw_domain = np.asarray(forecast.domain_valid_mask)
            if raw_domain.shape != truth.shape or np.any(~np.isin(raw_domain, (0, 1))):
                raise VerificationInputError(f"forecast {model} domain mask is invalid")
            domain_valid = raw_domain == 1
            if np.any(valid & ~domain_valid):
                raise VerificationInputError(
                    f"forecast {model} is valid outside its advection domain"
                )
        normalized[model] = rate, valid, domain_valid
        common_valid &= valid
    if np.any(~np.isfinite(truth[truth_valid])) or np.any(truth[truth_valid] < 0.0):
        raise VerificationInputError("truth has invalid values inside its validity mask")

    rows: list[dict[str, Any]] = []
    cell_count = truth.shape[1] * truth.shape[2]
    for lead_index, lead_minute in enumerate(leads):
        truth_domain = truth_valid[lead_index]
        all_model_common = common_valid[lead_index]
        truth_count = int(np.count_nonzero(truth_domain))
        for model, (forecast_rate, forecast_valid, forecast_domain_valid) in normalized.items():
            model_valid = forecast_valid[lead_index]
            evaluation = all_model_common if validity_domain == "common" else truth_domain
            observed_values = truth[lead_index][evaluation]
            if validity_domain == "common":
                forecast_values = forecast_rate[lead_index][evaluation]
            else:
                forecast_work = np.where(model_valid, forecast_rate[lead_index], 0.0)
                forecast_values = forecast_work[evaluation]
            if observed_values.size:
                difference = forecast_values - observed_values
                mae = float(np.mean(np.abs(difference)))
                rmse = float(np.sqrt(np.mean(difference**2)))
                mean_error = float(np.mean(difference))
            else:
                mae = rmse = mean_error = float("nan")

            forecast_inside_truth = truth_domain & model_valid
            forecast_inside_truth_count = int(np.count_nonzero(forecast_inside_truth))
            forecast_to_truth_coverage = _ratio(
                forecast_inside_truth_count,
                truth_count,
            )
            if forecast_domain_valid is None:
                advection_domain_to_truth_coverage = float("nan")
                advection_boundary_loss_ratio = float("nan")
                interior_missing_loss_ratio = float("nan")
                boundary_adjusted_coverage = float("nan")
                coverage_closure_error = float("nan")
                coverage_provenance_available = False
            else:
                domain_inside_truth = truth_domain & forecast_domain_valid[lead_index]
                domain_inside_truth_count = int(np.count_nonzero(domain_inside_truth))
                boundary_loss_count = truth_count - domain_inside_truth_count
                interior_missing_count = int(
                    np.count_nonzero(domain_inside_truth & ~model_valid)
                )
                advection_domain_to_truth_coverage = _ratio(
                    domain_inside_truth_count,
                    truth_count,
                )
                advection_boundary_loss_ratio = _ratio(boundary_loss_count, truth_count)
                interior_missing_loss_ratio = _ratio(interior_missing_count, truth_count)
                boundary_adjusted_coverage = _ratio(
                    forecast_inside_truth_count,
                    domain_inside_truth_count,
                )
                closure = _ratio(
                    forecast_inside_truth_count
                    + boundary_loss_count
                    + interior_missing_count,
                    truth_count,
                )
                coverage_closure_error = abs(closure - 1.0)
                coverage_provenance_available = True
            for threshold in thresholds_mm_h:
                observed_event = (truth[lead_index] >= threshold) & truth_domain
                forecast_event = (forecast_rate[lead_index] >= threshold) & model_valid
                hits = int(np.count_nonzero(evaluation & observed_event & forecast_event))
                misses = int(np.count_nonzero(evaluation & observed_event & ~forecast_event))
                false_alarms = int(
                    np.count_nonzero(evaluation & ~observed_event & forecast_event)
                )
                correct_negatives = int(
                    np.count_nonzero(evaluation & ~observed_event & ~forecast_event)
                )
                for window, actual_window_km, target_window_km in window_specs:
                    rows.append(
                        {
                            "model": model,
                            "lead_minutes": int(lead_minute),
                            "threshold_mm_h": float(threshold),
                            "window_pixels": int(window),
                            "window_km": actual_window_km,
                            "window_target_km": target_window_km,
                            "validity_domain": validity_domain,
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
                                evaluation,
                                window,
                            ),
                            "mae_mm_h": mae,
                            "rmse_mm_h": rmse,
                            "mean_error_mm_h": mean_error,
                            "truth_coverage": float(truth_count / cell_count),
                            "forecast_coverage": float(
                                np.count_nonzero(model_valid) / cell_count
                            ),
                            "common_coverage": float(
                                np.count_nonzero(all_model_common) / cell_count
                            ),
                            "forecast_to_truth_coverage": forecast_to_truth_coverage,
                            "coverage_provenance_available": coverage_provenance_available,
                            "advection_domain_to_truth_coverage": (
                                advection_domain_to_truth_coverage
                            ),
                            "advection_boundary_loss_ratio": advection_boundary_loss_ratio,
                            "interior_missing_loss_ratio": interior_missing_loss_ratio,
                            "boundary_adjusted_forecast_to_truth_coverage": (
                                boundary_adjusted_coverage
                            ),
                            "coverage_decomposition_closure_error": coverage_closure_error,
                            "evaluation_coverage": float(
                                np.count_nonzero(evaluation) / cell_count
                            ),
                        }
                    )
    return rows


def _accumulate_rates(
    rates: np.ndarray,
    valid_mask: np.ndarray,
    lead_minutes: Sequence[int],
    maximum_minutes: int,
) -> tuple[np.ndarray, np.ndarray]:
    leads = np.asarray(lead_minutes, dtype="int32")
    selected = leads <= maximum_minutes
    if not np.any(selected) or int(leads[selected][-1]) != maximum_minutes:
        raise VerificationInputError(
            f"accumulation window {maximum_minutes} minutes is not represented by leads"
        )
    selected_leads = leads[selected]
    intervals = np.diff(np.concatenate(([0], selected_leads))).astype("float32") / 60.0
    selected_rates = np.asarray(rates, dtype="float32")[selected]
    selected_valid = (np.asarray(valid_mask) == 1)[selected]
    valid = np.all(selected_valid, axis=0)
    values = np.sum(
        np.where(selected_valid, selected_rates, 0.0)
        * intervals[:, np.newaxis, np.newaxis],
        axis=0,
    ).astype("float32")
    values[~valid] = np.nan
    return values, valid.astype("uint8")


def score_accumulation_forecasts(
    truth_rate_mm_h: np.ndarray,
    truth_valid_mask: np.ndarray,
    forecasts: Mapping[str, DeterministicForecast],
    *,
    lead_minutes: Sequence[int],
    accumulation_windows_minutes: Sequence[int],
    thresholds_mm: Sequence[float],
    windows_pixels: Sequence[int],
    pixel_spacing_km: float,
    validity_domain: ValidityDomain = "truth",
    windows_km: Sequence[float] = (),
) -> list[dict[str, Any]]:
    """Score 0–1 h and 0–2 h accumulations with the same domain semantics."""

    rows: list[dict[str, Any]] = []
    for accumulation_minutes in accumulation_windows_minutes:
        truth_accum, truth_accum_valid = _accumulate_rates(
            truth_rate_mm_h,
            truth_valid_mask,
            lead_minutes,
            int(accumulation_minutes),
        )
        accumulated_forecasts: dict[str, DeterministicForecast] = {}
        for model, forecast in forecasts.items():
            values, valid = _accumulate_rates(
                forecast.rate_mm_h,
                forecast.valid_mask,
                lead_minutes,
                int(accumulation_minutes),
            )
            accumulated_forecasts[model] = DeterministicForecast(
                values[np.newaxis, ...],
                valid[np.newaxis, ...],
                (
                    _accumulate_rates(
                        np.zeros_like(forecast.rate_mm_h, dtype="float32"),
                        forecast.domain_valid_mask,
                        lead_minutes,
                        int(accumulation_minutes),
                    )[1][np.newaxis, ...]
                    if forecast.domain_valid_mask is not None
                    else None
                ),
            )
        scored = score_deterministic_forecasts(
            truth_accum[np.newaxis, ...],
            truth_accum_valid[np.newaxis, ...],
            accumulated_forecasts,
            lead_minutes=(int(accumulation_minutes),),
            thresholds_mm_h=thresholds_mm,
            windows_pixels=windows_pixels,
            windows_km=windows_km,
            pixel_spacing_km=pixel_spacing_km,
            validity_domain=validity_domain,
        )
        for row in scored:
            row["accumulation_minutes"] = int(accumulation_minutes)
            row["threshold_mm"] = row.pop("threshold_mm_h")
            row["mae_mm"] = row.pop("mae_mm_h")
            row["rmse_mm"] = row.pop("rmse_mm_h")
            row["mean_error_mm"] = row.pop("mean_error_mm_h")
        rows.extend(scored)
    return rows


def summarize_coverage(
    rows: Sequence[Mapping[str, Any]],
    *,
    models: Sequence[str],
    minimum_ratio: float,
    maximum_lead_minutes: int = 120,
    coverage_field: str = "forecast_to_truth_coverage",
) -> dict[str, Any]:
    if not 0.0 <= minimum_ratio <= 1.0:
        raise VerificationInputError("coverage threshold must be within [0, 1]")
    summaries: dict[str, Any] = {}
    for model in models:
        unique: dict[tuple[str, str, int], float] = {}
        for row in rows:
            if row.get("model") != model or int(row["lead_minutes"]) > maximum_lead_minutes:
                continue
            value = row.get(coverage_field)
            if value is None or not np.isfinite(float(value)):
                continue
            key = (
                str(row.get("case_id", "")),
                str(row.get("issue_time_utc", "")),
                int(row["lead_minutes"]),
            )
            unique[key] = float(value)
        values = list(unique.values())
        summaries[model] = {
            "minimum_required_ratio": minimum_ratio,
            "evaluated_slice_count": len(values),
            "minimum_ratio": min(values) if values else None,
            "mean_ratio": float(np.mean(values)) if values else None,
            "failed_slice_count": sum(value < minimum_ratio for value in values),
            "passes": bool(values) and all(value >= minimum_ratio for value in values),
        }
    return {
        "coverage_metric": coverage_field,
        "minimum_required_ratio": minimum_ratio,
        "models": summaries,
        "all_models_pass": bool(summaries) and all(
            item["passes"] for item in summaries.values()
        ),
    }


def summarize_coverage_provenance(
    rows: Sequence[Mapping[str, Any]],
    *,
    models: Sequence[str],
    maximum_lead_minutes: int = 120,
) -> dict[str, Any]:
    """Summarize geometric-boundary and in-domain forecast coverage losses."""

    summaries: dict[str, Any] = {}
    for model in models:
        unique: dict[tuple[str, str, int], tuple[float, float, float, float, float]] = {}
        for row in rows:
            if row.get("model") != model or int(row["lead_minutes"]) > maximum_lead_minutes:
                continue
            values = tuple(
                float(row[name])
                for name in (
                    "forecast_to_truth_coverage",
                    "advection_boundary_loss_ratio",
                    "interior_missing_loss_ratio",
                    "boundary_adjusted_forecast_to_truth_coverage",
                    "coverage_decomposition_closure_error",
                )
            )
            if not all(np.isfinite(value) for value in values):
                continue
            key = (
                str(row.get("case_id", "")),
                str(row.get("issue_time_utc", "")),
                int(row["lead_minutes"]),
            )
            unique[key] = values
        slices = list(unique.values())
        summaries[model] = {
            "evaluated_slice_count": len(slices),
            "mean_forecast_to_truth_coverage": (
                float(np.mean([item[0] for item in slices])) if slices else None
            ),
            "minimum_forecast_to_truth_coverage": (
                min(item[0] for item in slices) if slices else None
            ),
            "mean_advection_boundary_loss_ratio": (
                float(np.mean([item[1] for item in slices])) if slices else None
            ),
            "maximum_advection_boundary_loss_ratio": (
                max(item[1] for item in slices) if slices else None
            ),
            "mean_interior_missing_loss_ratio": (
                float(np.mean([item[2] for item in slices])) if slices else None
            ),
            "maximum_interior_missing_loss_ratio": (
                max(item[2] for item in slices) if slices else None
            ),
            "minimum_boundary_adjusted_forecast_to_truth_coverage": (
                min(item[3] for item in slices) if slices else None
            ),
            "interior_missing_slice_count": sum(item[2] > 0.0 for item in slices),
            "maximum_closure_error": max(item[4] for item in slices) if slices else None,
        }
    return {
        "method": "advected_all_ones_domain_support_v1",
        "models": summaries,
        "all_models_have_provenance": bool(summaries)
        and all(item["evaluated_slice_count"] > 0 for item in summaries.values()),
    }


def summarize_model_fss_difference(
    rows: Sequence[Mapping[str, Any]],
    *,
    candidate: str,
    reference: str,
    maximum_lead_minutes: int = 120,
) -> dict[str, Any]:
    grouped: dict[tuple[str, str, int, float, float], dict[str, float]] = {}
    for row in rows:
        if row.get("model") not in {candidate, reference}:
            continue
        if int(row["lead_minutes"]) > maximum_lead_minutes:
            continue
        value = row.get("fss")
        if value is None or not np.isfinite(float(value)):
            continue
        key = (
            str(row.get("case_id", "")),
            str(row.get("issue_time_utc", "")),
            int(row["lead_minutes"]),
            float(row.get("threshold_mm_h", row.get("threshold_mm", 0.0))),
            float(row.get("window_target_km", row.get("window_km", 0.0))),
        )
        grouped.setdefault(key, {})[str(row["model"])] = float(value)
    differences = [
        values[candidate] - values[reference]
        for values in grouped.values()
        if candidate in values and reference in values
    ]
    return {
        "candidate": candidate,
        "reference": reference,
        "paired_slice_count": len(differences),
        "mean_fss_difference": float(np.mean(differences)) if differences else None,
        "median_fss_difference": float(np.median(differences)) if differences else None,
        "positive_slice_count": sum(value > 0.0 for value in differences),
        "negative_slice_count": sum(value < 0.0 for value in differences),
    }


def summarize_fss_skill(
    rows: Sequence[Mapping[str, Any]],
    *,
    thresholds_mm_h: Sequence[float] = (1.0, 5.0, 10.0),
    minimum_lead_minutes: int = 0,
    maximum_lead_minutes: int = 60,
    window_pixels: int = 11,
    required_positive_cases: int = 3,
    required_wet_cases: int = 4,
    bootstrap_samples: int = 2000,
    random_seed: int = 16016,
    candidate_model: str = "lk",
    baselines: Sequence[str] = ("persistence", "translation"),
    minimum_forecast_to_truth_coverage: float | None = None,
    coverage_field: str = "forecast_to_truth_coverage",
) -> dict[str, Any]:
    """Summarize paired candidate FSS skill with case/issue block bootstrap intervals."""

    if bootstrap_samples < 1:
        raise VerificationInputError("bootstrap_samples must be positive")
    if minimum_lead_minutes < 0 or maximum_lead_minutes < minimum_lead_minutes:
        raise VerificationInputError("skill lead interval is invalid")
    if minimum_forecast_to_truth_coverage is not None and not (
        0.0 <= minimum_forecast_to_truth_coverage <= 1.0
    ):
        raise VerificationInputError("skill coverage gate must be within [0, 1]")
    wet_case_ids = sorted(
        {str(row["case_id"]) for row in rows if row.get("case_category") == "wet"}
    )
    comparisons: list[dict[str, Any]] = []
    rng = np.random.default_rng(random_seed)
    for baseline in baselines:
        for threshold in thresholds_mm_h:
            issue_scores: dict[tuple[str, str, str], list[float]] = {}
            coverage_values: dict[tuple[str, str, int], float] = {}
            for row in rows:
                lead = int(row["lead_minutes"])
                if (
                    row.get("case_category") != "wet"
                    or lead < minimum_lead_minutes
                    or lead > maximum_lead_minutes
                    or int(row["window_pixels"]) != window_pixels
                    or not np.isclose(float(row["threshold_mm_h"]), threshold)
                    or row.get("model") not in {candidate_model, baseline}
                ):
                    continue
                fss = float(row["fss"])
                if np.isfinite(fss):
                    key = (
                        str(row["case_id"]),
                        str(row["issue_time_utc"]),
                        str(row["model"]),
                    )
                    issue_scores.setdefault(key, []).append(fss)
                if row.get("model") == candidate_model:
                    coverage = row.get(coverage_field)
                    if coverage is not None and np.isfinite(float(coverage)):
                        coverage_values[(
                            str(row["case_id"]),
                            str(row["issue_time_utc"]),
                            lead,
                        )] = float(coverage)

            issue_means = {key: float(np.mean(values)) for key, values in issue_scores.items()}
            case_differences: dict[str, list[float]] = {}
            for case_id in wet_case_ids:
                issue_ids = {
                    issue_id
                    for candidate_case, issue_id, model in issue_means
                    if candidate_case == case_id and model == candidate_model
                }
                for issue_id in issue_ids:
                    candidate_key = (case_id, issue_id, candidate_model)
                    baseline_key = (case_id, issue_id, baseline)
                    if baseline_key in issue_means:
                        case_differences.setdefault(case_id, []).append(
                            issue_means[candidate_key] - issue_means[baseline_key]
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
            if minimum_forecast_to_truth_coverage is None:
                coverage_passes = True
                minimum_coverage = None
            else:
                coverage_passes = bool(coverage_values) and all(
                    value >= minimum_forecast_to_truth_coverage
                    for value in coverage_values.values()
                )
                minimum_coverage = min(coverage_values.values()) if coverage_values else None
            comparisons.append(
                {
                    "baseline": baseline,
                    "threshold_mm_h": float(threshold),
                    "minimum_lead_minutes": minimum_lead_minutes,
                    "maximum_lead_minutes": maximum_lead_minutes,
                    "window_pixels": window_pixels,
                    "positive_case_count": positive_count,
                    "evaluable_case_count": len(case_means),
                    "total_wet_case_count": len(wet_case_ids),
                    "passes_case_gate": (
                        positive_count >= required_positive_cases and coverage_passes
                    ),
                    "coverage_gate_passes": coverage_passes,
                    "coverage_metric": coverage_field,
                    "minimum_coverage_ratio": minimum_coverage,
                    "required_coverage_ratio": minimum_forecast_to_truth_coverage,
                    "minimum_forecast_to_truth_coverage": minimum_coverage,
                    "required_forecast_to_truth_coverage": (
                        minimum_forecast_to_truth_coverage
                    ),
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
        strong_baselines = [
            comparison
            for comparison in comparisons
            if comparison["baseline"] != "persistence"
        ]
        strong_passes = bool(strong_baselines) and all(
            comparison["passes_case_gate"] for comparison in strong_baselines
        )
        translation_comparisons = [
            comparison
            for comparison in comparisons
            if comparison["baseline"] == "translation"
        ]
        translation_passes = bool(translation_comparisons) and all(
            comparison["passes_case_gate"] for comparison in translation_comparisons
        )
        if persistence_passes and strong_passes:
            status = "lk_supported"
        elif persistence_passes and translation_comparisons and not translation_passes:
            status = "translation_baseline_retained"
        else:
            status = "skill_not_demonstrated"
    return {
        "status": status,
        "comparison_metric": "FSS",
        "candidate_model": candidate_model,
        "coverage_metric": coverage_field,
        "comparisons": comparisons,
    }
