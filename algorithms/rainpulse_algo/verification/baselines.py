from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass

import numpy as np
from scipy import ndimage


class VerificationBaselineError(ValueError):
    """Raised when an independent deterministic baseline cannot be constructed."""


@dataclass(frozen=True)
class PhaseCorrelationEstimate:
    """Whole-field displacement estimated without using the LK motion field."""

    dx_pixels_per_source_step: float
    dy_pixels_per_source_step: float
    support_fraction: float
    peak_correlation: float
    fallback_used: bool
    fallback_reason: str | None


@dataclass(frozen=True)
class PhaseCorrelationForecast:
    rate_mm_h: np.ndarray
    valid_mask: np.ndarray
    domain_valid_mask: np.ndarray
    estimate: PhaseCorrelationEstimate


def estimate_phase_correlation_translation(
    previous: np.ndarray,
    current: np.ndarray,
    previous_valid: np.ndarray,
    current_valid: np.ndarray,
    *,
    minimum_support_pixels: int = 256,
    minimum_signal_pixels: int = 64,
    minimum_standard_deviation: float = 1e-4,
) -> PhaseCorrelationEstimate:
    """Estimate one integer-pixel translation using masked FFT phase correlation.

    The estimate is deliberately independent from pySTEPS-LK. Missing cells are
    excluded from the centring statistics, the common support is tapered with a
    Hann window, and low-information scenes fall back to explicit zero motion.
    """

    first = np.asarray(previous, dtype="float64")
    second = np.asarray(current, dtype="float64")
    first_valid = np.asarray(previous_valid) == 1
    second_valid = np.asarray(current_valid) == 1
    if first.ndim != 2 or second.shape != first.shape:
        raise VerificationBaselineError("phase-correlation images must share a 2-D shape")
    if first_valid.shape != first.shape or second_valid.shape != first.shape:
        raise VerificationBaselineError("phase-correlation masks must match the images")

    support = first_valid & second_valid & np.isfinite(first) & np.isfinite(second)
    support_count = int(np.count_nonzero(support))
    support_fraction = float(support_count / support.size) if support.size else 0.0
    if support_count < minimum_support_pixels:
        return PhaseCorrelationEstimate(
            0.0,
            0.0,
            support_fraction,
            0.0,
            True,
            "insufficient_common_support",
        )

    first_values = first[support]
    second_values = second[support]
    if (
        float(np.std(first_values)) < minimum_standard_deviation
        or float(np.std(second_values)) < minimum_standard_deviation
    ):
        return PhaseCorrelationEstimate(
            0.0,
            0.0,
            support_fraction,
            0.0,
            True,
            "insufficient_spatial_variance",
        )
    first_signal_count = int(np.count_nonzero(np.abs(first_values) > 1e-8))
    second_signal_count = int(np.count_nonzero(np.abs(second_values) > 1e-8))
    if min(first_signal_count, second_signal_count) < minimum_signal_pixels:
        return PhaseCorrelationEstimate(
            0.0,
            0.0,
            support_fraction,
            0.0,
            True,
            "insufficient_signal_area",
        )

    first_work = np.zeros(first.shape, dtype="float64")
    second_work = np.zeros(second.shape, dtype="float64")
    first_work[support] = first_values - float(np.mean(first_values))
    second_work[support] = second_values - float(np.mean(second_values))
    taper = np.outer(np.hanning(first.shape[0]), np.hanning(first.shape[1]))
    first_work *= taper
    second_work *= taper

    first_spectrum = np.fft.fft2(first_work)
    second_spectrum = np.fft.fft2(second_work)
    cross_power = second_spectrum * np.conjugate(first_spectrum)
    magnitude = np.abs(cross_power)
    usable = magnitude > np.finfo("float64").eps
    if not np.any(usable):
        return PhaseCorrelationEstimate(
            0.0,
            0.0,
            support_fraction,
            0.0,
            True,
            "empty_cross_power_spectrum",
        )
    normalized = np.zeros_like(cross_power)
    normalized[usable] = cross_power[usable] / magnitude[usable]
    correlation = np.abs(np.fft.ifft2(normalized))
    peak_y, peak_x = np.unravel_index(int(np.argmax(correlation)), correlation.shape)
    dy = float(peak_y if peak_y <= first.shape[0] // 2 else peak_y - first.shape[0])
    dx = float(peak_x if peak_x <= first.shape[1] // 2 else peak_x - first.shape[1])
    peak = float(correlation[peak_y, peak_x])
    return PhaseCorrelationEstimate(dx, dy, support_fraction, peak, False, None)


def build_phase_correlation_forecast(
    previous_rate_mm_h: np.ndarray,
    current_rate_mm_h: np.ndarray,
    previous_valid_mask: np.ndarray,
    current_valid_mask: np.ndarray,
    *,
    lead_minutes: Sequence[int],
    source_interval_minutes: int,
) -> PhaseCorrelationForecast:
    """Advect the latest rate field with an independent whole-field translation."""

    if source_interval_minutes <= 0:
        raise VerificationBaselineError("source interval must be positive")
    leads = tuple(int(value) for value in lead_minutes)
    if not leads or any(value <= 0 for value in leads) or tuple(sorted(leads)) != leads:
        raise VerificationBaselineError("lead minutes must be positive and increasing")

    previous_rate = np.asarray(previous_rate_mm_h, dtype="float32")
    current_rate = np.asarray(current_rate_mm_h, dtype="float32")
    previous_valid = np.asarray(previous_valid_mask) == 1
    current_valid = np.asarray(current_valid_mask) == 1
    if previous_rate.ndim != 2 or current_rate.shape != previous_rate.shape:
        raise VerificationBaselineError("phase-correlation rate fields must share a 2-D shape")
    if previous_valid.shape != previous_rate.shape or current_valid.shape != previous_rate.shape:
        raise VerificationBaselineError("phase-correlation rate masks must match the fields")
    if np.any(~np.isfinite(previous_rate[previous_valid])) or np.any(
        ~np.isfinite(current_rate[current_valid])
    ):
        raise VerificationBaselineError("valid phase-correlation rate cells must be finite")

    previous_image = np.where(previous_valid, np.log1p(previous_rate), 0.0)
    current_image = np.where(current_valid, np.log1p(current_rate), 0.0)
    estimate = estimate_phase_correlation_translation(
        previous_image,
        current_image,
        previous_valid,
        current_valid,
    )

    working_rate = np.where(current_valid, current_rate, 0.0).astype("float32")
    forecast = np.full((len(leads), *current_rate.shape), np.nan, dtype="float32")
    support = np.zeros((len(leads), *current_rate.shape), dtype="uint8")
    domain_support = np.zeros((len(leads), *current_rate.shape), dtype="uint8")
    for index, lead in enumerate(leads):
        factor = float(lead / source_interval_minutes)
        shift = (
            estimate.dy_pixels_per_source_step * factor,
            estimate.dx_pixels_per_source_step * factor,
        )
        shifted_rate = ndimage.shift(
            working_rate,
            shift=shift,
            order=1,
            mode="constant",
            cval=0.0,
            prefilter=False,
        )
        shifted_support = ndimage.shift(
            current_valid.astype("float32"),
            shift=shift,
            order=0,
            mode="constant",
            cval=0.0,
            prefilter=False,
        ) >= 0.5
        shifted_domain_support = ndimage.shift(
            np.ones(current_rate.shape, dtype="float32"),
            shift=shift,
            order=0,
            mode="constant",
            cval=0.0,
            prefilter=False,
        ) >= 0.5
        forecast[index][shifted_support] = np.maximum(
            shifted_rate[shifted_support],
            0.0,
        ).astype("float32")
        support[index] = shifted_support.astype("uint8")
        domain_support[index] = shifted_domain_support.astype("uint8")
    return PhaseCorrelationForecast(forecast, support, domain_support, estimate)
