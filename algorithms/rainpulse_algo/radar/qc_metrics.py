from __future__ import annotations

from typing import Any

import numpy as np


def echo_classification_metrics(
    truth_anomaly: np.ndarray,
    predicted_probability: np.ndarray,
    *,
    threshold: float,
    valid_mask: np.ndarray | None = None,
) -> dict[str, int | float | None]:
    truth_values = np.asarray(truth_anomaly)
    truth = truth_values != 0
    probability = np.asarray(predicted_probability, dtype="float64")
    if truth.shape != probability.shape:
        raise ValueError("truth and predicted anomaly arrays must have equal shape")
    valid = np.isfinite(truth_values) & np.isfinite(probability)
    if valid_mask is not None:
        supplied_values = np.asarray(valid_mask)
        supplied = np.isfinite(supplied_values) & (supplied_values != 0)
        if supplied.shape != truth.shape:
            raise ValueError("classification valid mask must match truth shape")
        valid &= supplied
    predicted = probability >= threshold
    true_positive = int(np.count_nonzero(valid & truth & predicted))
    false_positive = int(np.count_nonzero(valid & ~truth & predicted))
    false_negative = int(np.count_nonzero(valid & truth & ~predicted))
    true_negative = int(np.count_nonzero(valid & ~truth & ~predicted))
    precision = _safe_ratio(true_positive, true_positive + false_positive)
    recall = _safe_ratio(true_positive, true_positive + false_negative)
    f1 = (
        2.0 * precision * recall / (precision + recall)
        if precision is not None and recall is not None and precision + recall > 0
        else None
    )
    return {
        "evaluated_gate_count": int(np.count_nonzero(valid)),
        "true_positive": true_positive,
        "false_positive": false_positive,
        "false_negative": false_negative,
        "true_negative": true_negative,
        "precision": precision,
        "recall": recall,
        "f1": f1,
    }


def real_precipitation_retention_rate(
    truth_meteorological: np.ndarray,
    retained_mask: np.ndarray,
    *,
    valid_mask: np.ndarray | None = None,
) -> float | None:
    truth_values = np.asarray(truth_meteorological)
    retained_values = np.asarray(retained_mask)
    truth = truth_values != 0
    retained = retained_values != 0
    if truth.shape != retained.shape:
        raise ValueError("meteorological truth and retained mask must have equal shape")
    eligible = truth & np.isfinite(truth_values) & np.isfinite(retained_values)
    if valid_mask is not None:
        supplied_values = np.asarray(valid_mask)
        supplied = np.isfinite(supplied_values) & (supplied_values != 0)
        if supplied.shape != truth.shape:
            raise ValueError("retention valid mask must match truth shape")
        eligible &= supplied
    return _safe_ratio(
        int(np.count_nonzero(eligible & retained)),
        int(np.count_nonzero(eligible)),
    )


def polar_mask_area_km2(
    mask: np.ndarray,
    ranges_m: np.ndarray,
    azimuth_deg: np.ndarray,
) -> float:
    selected = np.asarray(mask, dtype=bool)
    ranges = np.asarray(ranges_m, dtype="float64")
    azimuth = np.asarray(azimuth_deg, dtype="float64")
    if selected.ndim != 2 or selected.shape != (azimuth.size, ranges.size):
        raise ValueError("polar mask shape must be [azimuth, range]")
    if ranges.size == 0 or azimuth.size == 0:
        return 0.0
    if not np.all(np.isfinite(ranges)) or np.any(np.diff(ranges) <= 0):
        raise ValueError("polar ranges must be finite and strictly increasing")
    if not np.all(np.isfinite(azimuth)):
        raise ValueError("polar azimuths must be finite")
    range_edges = _coordinate_edges(ranges, floor_zero=True)
    radial_area = 0.5 * (range_edges[1:] ** 2 - range_edges[:-1] ** 2)
    azimuth_width = _cyclic_azimuth_width_radians(azimuth)
    gate_area = azimuth_width[:, None] * radial_area[None, :]
    return float(np.sum(gate_area[selected]) / 1_000_000.0)


def qpe_distribution_metrics(
    rate_mm_h: np.ndarray,
    *,
    valid_mask: np.ndarray | None = None,
) -> dict[str, int | float | None]:
    values = np.asarray(rate_mm_h, dtype="float64")
    valid = np.isfinite(values)
    if valid_mask is not None:
        supplied_values = np.asarray(valid_mask)
        supplied = np.isfinite(supplied_values) & (supplied_values != 0)
        if supplied.shape != values.shape:
            raise ValueError("QPE valid mask must match rate shape")
        valid &= supplied
    sample = values[valid]
    if sample.size == 0:
        return {
            "sample_count": 0,
            "mean_mm_h": None,
            "p95_mm_h": None,
            "maximum_mm_h": None,
        }
    return {
        "sample_count": int(sample.size),
        "mean_mm_h": float(np.mean(sample)),
        "p95_mm_h": float(np.quantile(sample, 0.95)),
        "maximum_mm_h": float(np.max(sample)),
    }


def gauge_verification_metrics(
    qpe_accumulation_mm: np.ndarray,
    gauge_accumulation_mm: np.ndarray,
    *,
    valid_mask: np.ndarray | None = None,
) -> dict[str, int | float | None]:
    qpe = np.asarray(qpe_accumulation_mm, dtype="float64")
    gauge = np.asarray(gauge_accumulation_mm, dtype="float64")
    if qpe.shape != gauge.shape:
        raise ValueError("QPE and gauge arrays must have equal shape")
    valid = np.isfinite(qpe) & np.isfinite(gauge)
    if valid_mask is not None:
        supplied_values = np.asarray(valid_mask)
        supplied = np.isfinite(supplied_values) & (supplied_values != 0)
        if supplied.shape != qpe.shape:
            raise ValueError("gauge valid mask must match input shape")
        valid &= supplied
    qpe = qpe[valid]
    gauge = gauge[valid]
    if qpe.size == 0:
        return {
            "sample_count": 0,
            "bias_ratio": None,
            "correlation": None,
            "mae_mm": None,
            "rmse_mm": None,
        }
    gauge_total = float(np.sum(gauge))
    correlation: float | None = None
    if qpe.size >= 2 and np.std(qpe) > 0 and np.std(gauge) > 0:
        correlation = float(np.corrcoef(qpe, gauge)[0, 1])
    error = qpe - gauge
    return {
        "sample_count": int(qpe.size),
        "bias_ratio": float(np.sum(qpe) / gauge_total) if gauge_total > 0 else None,
        "correlation": correlation,
        "mae_mm": float(np.mean(np.abs(error))),
        "rmse_mm": float(np.sqrt(np.mean(error**2))),
    }


def unavailable_acceptance_metrics(reason: str) -> dict[str, Any]:
    return {"status": "skipped", "reason": reason}


def _safe_ratio(numerator: int, denominator: int) -> float | None:
    return float(numerator / denominator) if denominator else None


def _coordinate_edges(values: np.ndarray, *, floor_zero: bool) -> np.ndarray:
    if values.size == 1:
        spacing = max(float(values[0]), 1.0)
        edges = np.array([values[0] - spacing / 2, values[0] + spacing / 2])
    else:
        midpoint = (values[:-1] + values[1:]) / 2.0
        edges = np.concatenate(
            (
                [values[0] - (midpoint[0] - values[0])],
                midpoint,
                [values[-1] + (values[-1] - midpoint[-1])],
            )
        )
    if floor_zero:
        edges[0] = max(edges[0], 0.0)
    return edges


def _cyclic_azimuth_width_radians(azimuth: np.ndarray) -> np.ndarray:
    if azimuth.size == 1:
        return np.array([2 * np.pi], dtype="float64")
    normalized = np.mod(azimuth, 360.0)
    order = np.argsort(normalized)
    sorted_azimuth = normalized[order]
    previous = np.roll(sorted_azimuth, 1)
    following = np.roll(sorted_azimuth, -1)
    previous_gap = np.mod(sorted_azimuth - previous, 360.0)
    following_gap = np.mod(following - sorted_azimuth, 360.0)
    widths = np.deg2rad((previous_gap + following_gap) / 2.0)
    result = np.empty_like(widths)
    result[order] = widths
    return result
