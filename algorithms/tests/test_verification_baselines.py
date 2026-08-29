from __future__ import annotations

import numpy as np
import pytest

from rainpulse_algo.verification.baselines import (
    build_phase_correlation_forecast,
    estimate_phase_correlation_translation,
)


def test_phase_correlation_recovers_known_integer_translation() -> None:
    previous = np.zeros((64, 64), dtype="float32")
    previous[20:30, 18:34] = 4.0
    current = np.roll(previous, shift=(3, 5), axis=(0, 1))
    valid = np.ones(previous.shape, dtype="uint8")

    estimate = estimate_phase_correlation_translation(
        previous,
        current,
        valid,
        valid,
        minimum_support_pixels=64,
    )

    assert not estimate.fallback_used
    assert estimate.dx_pixels_per_source_step == pytest.approx(5.0)
    assert estimate.dy_pixels_per_source_step == pytest.approx(3.0)
    assert estimate.peak_correlation > 0.0


def test_phase_correlation_forecast_advects_rate_and_support_without_wraparound() -> None:
    previous = np.zeros((32, 32), dtype="float32")
    previous[8:16, 8:16] = 2.0
    current = np.roll(previous, shift=(0, 2), axis=(0, 1))
    valid = np.ones(previous.shape, dtype="uint8")

    forecast = build_phase_correlation_forecast(
        previous,
        current,
        valid,
        valid,
        lead_minutes=(10, 20),
        source_interval_minutes=10,
    )

    assert forecast.rate_mm_h.shape == (2, 32, 32)
    assert forecast.valid_mask.shape == (2, 32, 32)
    assert forecast.domain_valid_mask.shape == (2, 32, 32)
    assert np.nanargmax(forecast.rate_mm_h[0]) % 32 > np.nanargmax(current) % 32
    assert np.all(forecast.valid_mask[:, :, :2] == 0)
    assert np.all(forecast.domain_valid_mask[:, :, :2] == 0)
    assert np.all(forecast.valid_mask <= forecast.domain_valid_mask)


def test_phase_correlation_has_explicit_zero_motion_fallback_for_dry_scene() -> None:
    dry = np.zeros((32, 32), dtype="float32")
    valid = np.ones(dry.shape, dtype="uint8")

    forecast = build_phase_correlation_forecast(
        dry,
        dry,
        valid,
        valid,
        lead_minutes=(10,),
        source_interval_minutes=10,
    )

    assert forecast.estimate.fallback_used
    assert forecast.estimate.fallback_reason == "insufficient_spatial_variance"
    assert np.allclose(forecast.rate_mm_h[0], 0.0)


def test_phase_correlation_rejects_sparse_noise_as_motion_signal() -> None:
    previous = np.zeros((64, 64), dtype="float32")
    current = np.zeros((64, 64), dtype="float32")
    previous[20, 20] = 0.1
    current[40, 40] = 0.1
    valid = np.ones(previous.shape, dtype="uint8")

    estimate = estimate_phase_correlation_translation(previous, current, valid, valid)

    assert estimate.fallback_used
    assert estimate.fallback_reason == "insufficient_signal_area"
    assert estimate.dx_pixels_per_source_step == 0.0
    assert estimate.dy_pixels_per_source_step == 0.0
