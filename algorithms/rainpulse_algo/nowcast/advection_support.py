"""Conservative observation support using the same interpolation kernel as rain."""

from __future__ import annotations

from collections.abc import Callable

import numpy as np

Extrapolator = Callable[[np.ndarray, np.ndarray, int, int], np.ndarray]


def forecast_with_full_support(
    rate: np.ndarray,
    valid: np.ndarray,
    velocity: np.ndarray,
    lead_count: int,
    interpolation_order: int,
    extrapolate: Extrapolator,
) -> tuple[np.ndarray, np.ndarray]:
    if interpolation_order not in (0, 1):
        raise ValueError("full-kernel support requires interpolation order 0 or 1")
    rate = np.asarray(rate, dtype="float32")
    valid = np.asarray(valid, dtype=bool)
    if rate.ndim != 2 or valid.shape != rate.shape or lead_count < 1:
        raise ValueError("invalid advection input shape or lead count")
    if np.any(~np.isfinite(rate[valid])) or np.any(rate[valid] < 0):
        raise ValueError("observed rainfall must be finite and non-negative")
    working = np.where(valid, rate, 0.0).astype("float32")
    values = np.asarray(
        extrapolate(working, velocity, lead_count, interpolation_order), dtype="float32"
    )
    weights = np.asarray(
        extrapolate(valid.astype("float32"), velocity, lead_count, interpolation_order),
        dtype="float32",
    )
    expected = (lead_count, *rate.shape)
    if values.shape != expected or weights.shape != expected:
        raise ValueError("extrapolator returned an invalid shape")
    # The tolerance accommodates float32 rounding, not deliberately partial support.
    supported = (
        np.isfinite(values)
        & np.isfinite(weights)
        & np.isclose(
            weights,
            1.0,
            rtol=0.0,
            atol=8 * np.finfo("float32").eps,
        )
    )
    values = np.maximum(values, 0.0)
    values[~supported] = np.nan
    return values, supported
