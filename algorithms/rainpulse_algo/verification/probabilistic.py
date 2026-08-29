from __future__ import annotations

from collections.abc import Sequence
from typing import Any

import numpy as np

from .deterministic import VerificationInputError


def score_probabilistic_forecast(
    truth_rate_mm_h: np.ndarray,
    truth_valid_mask: np.ndarray,
    ensemble_rate_mm_h: np.ndarray,
    forecast_valid_mask: np.ndarray,
    *,
    lead_minutes: Sequence[int],
    thresholds_mm_h: Sequence[float],
    reliability_bin_edges: Sequence[float] = tuple(np.linspace(0.0, 1.0, 11)),
) -> list[dict[str, Any]]:
    """Score a raw ensemble on the fixed truth domain where its support is valid."""

    truth = np.asarray(truth_rate_mm_h, dtype="float32")
    truth_valid = np.asarray(truth_valid_mask) == 1
    members = np.asarray(ensemble_rate_mm_h, dtype="float32")
    forecast_valid = np.asarray(forecast_valid_mask) == 1
    leads = tuple(int(value) for value in lead_minutes)
    thresholds = tuple(float(value) for value in thresholds_mm_h)
    edges = np.asarray(tuple(float(value) for value in reliability_bin_edges), dtype="float64")
    if truth.ndim != 3 or truth_valid.shape != truth.shape:
        raise VerificationInputError("probabilistic truth must be lead x lat x lon")
    if members.ndim != 4 or members.shape[1:] != truth.shape or members.shape[0] < 2:
        raise VerificationInputError("ensemble must be member x lead x lat x lon")
    if forecast_valid.shape != truth.shape or len(leads) != truth.shape[0]:
        raise VerificationInputError("probabilistic forecast dimensions are not aligned")
    if tuple(sorted(leads)) != leads or any(value <= 0 for value in leads):
        raise VerificationInputError("probabilistic lead times must be positive and increasing")
    if not thresholds or any(value < 0.0 for value in thresholds):
        raise VerificationInputError("probabilistic thresholds must be non-negative")
    if (
        edges.ndim != 1
        or len(edges) < 2
        or edges[0] != 0.0
        or edges[-1] != 1.0
        or np.any(np.diff(edges) <= 0.0)
    ):
        raise VerificationInputError("reliability bins must increase from zero to one")
    evaluation = truth_valid & forecast_valid
    if np.any(~np.isfinite(truth[truth_valid])) or np.any(truth[truth_valid] < 0.0):
        raise VerificationInputError("probabilistic truth is invalid inside its mask")
    broadcast_valid = np.broadcast_to(forecast_valid, members.shape)
    if np.any(~np.isfinite(members[broadcast_valid])) or np.any(members[broadcast_valid] < 0.0):
        raise VerificationInputError("ensemble is invalid inside forecast support")

    rows: list[dict[str, Any]] = []
    for lead_index, lead in enumerate(leads):
        mask = evaluation[lead_index]
        truth_values = truth[lead_index][mask]
        member_values = members[:, lead_index][:, mask]
        crps = _ensemble_crps(member_values, truth_values)
        ensemble_mean = np.mean(member_values, axis=0) if truth_values.size else np.asarray([])
        spread = (
            float(np.mean(np.std(member_values, axis=0, ddof=0)))
            if truth_values.size
            else float("nan")
        )
        mean_rmse = (
            float(np.sqrt(np.mean((ensemble_mean - truth_values) ** 2)))
            if truth_values.size
            else float("nan")
        )
        for threshold in thresholds:
            probability = (
                np.mean(member_values > threshold, axis=0)
                if truth_values.size
                else np.asarray([], dtype="float32")
            )
            observed = truth_values > threshold
            brier = (
                float(np.mean((probability - observed.astype("float32")) ** 2))
                if truth_values.size
                else float("nan")
            )
            rows.append(
                {
                    "lead_minutes": lead,
                    "threshold_mm_h": threshold,
                    "member_count": int(members.shape[0]),
                    "evaluation_cell_count": int(truth_values.size),
                    "evaluation_coverage": float(np.mean(mask)),
                    "brier_score": brier,
                    "crps_mm_h": crps,
                    "ensemble_mean_rmse_mm_h": mean_rmse,
                    "mean_ensemble_spread_mm_h": spread,
                    "reliability": _reliability(probability, observed, edges),
                }
            )
    return rows


def _ensemble_crps(members: np.ndarray, truth: np.ndarray) -> float:
    if truth.size == 0:
        return float("nan")
    first = np.mean(np.abs(members - truth[np.newaxis, :]), axis=0)
    ordered = np.sort(members, axis=0)
    member_count = members.shape[0]
    coefficients = 2 * np.arange(1, member_count + 1) - member_count - 1
    second = np.sum(coefficients[:, np.newaxis] * ordered, axis=0) / member_count**2
    return float(np.mean(first - second))


def _reliability(
    probability: np.ndarray,
    observed: np.ndarray,
    edges: np.ndarray,
) -> list[dict[str, Any]]:
    bins: list[dict[str, Any]] = []
    for index, (lower, upper) in enumerate(zip(edges[:-1], edges[1:], strict=True)):
        selected = (probability >= lower) & (
            probability <= upper if index == len(edges) - 2 else probability < upper
        )
        count = int(np.count_nonzero(selected))
        bins.append(
            {
                "lower_probability": float(lower),
                "upper_probability": float(upper),
                "includes_upper": index == len(edges) - 2,
                "forecast_count": count,
                "mean_forecast_probability": (
                    float(np.mean(probability[selected])) if count else None
                ),
                "observed_frequency": float(np.mean(observed[selected])) if count else None,
            }
        )
    return bins
