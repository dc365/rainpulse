from pathlib import Path

import numpy as np
import pytest
from scipy.ndimage import map_coordinates

from rainpulse_algo.nowcast.advection_support import forecast_with_full_support
from rainpulse_algo.nowcast.pysteps_lk import _forecast_with_support, _semilagrangian
from rainpulse_algo.nowcast.pysteps_profile import load_pysteps_lk_profile

ROOT = Path(__file__).resolve().parents[2]


def fractional(field, velocity, leads, order):
    yy, xx = np.indices(field.shape, dtype=float)
    return np.stack(
        [
            map_coordinates(
                field,
                [yy - velocity[1] * step, xx - velocity[0] * step],
                order=order,
                mode="constant",
                cval=np.nan,
            )
            for step in range(1, leads + 1)
        ]
    )


@pytest.mark.parametrize("order", [0, 1])
@pytest.mark.parametrize("leads", [1, 12, 24])
def test_missing_never_dilutes_published_rain(order, leads):
    rate = np.full((40, 40), 10.0, dtype="float32")
    valid = np.ones(rate.shape, dtype=bool)
    valid[10:20, 10:20] = False
    rate[~valid] = np.nan
    velocity = np.full((2, 40, 40), 0.25, dtype="float32")
    values, support = forecast_with_full_support(rate, valid, velocity, leads, order, fractional)
    np.testing.assert_allclose(values[support], 10.0)
    assert np.all(np.isnan(values[~support]))


def test_fractional_counterexample_and_explicit_legacy_replay():
    rate = np.array([[10, 10, np.nan, np.nan]] * 4, dtype="float32")
    valid = np.isfinite(rate)
    velocity = np.zeros((2, 4, 4), dtype="float32")
    velocity[0] = -0.25
    fixed, mask = _forecast_with_support(rate, valid, velocity, 1, 1, fractional)
    legacy, old_mask = _forecast_with_support(
        rate, valid, velocity, 1, 1, fractional, support_policy="legacy_nearest_v1"
    )
    assert np.isnan(fixed[0, 1, 1]) and not mask[0, 1, 1]
    assert legacy[0, 1, 1] == 7.5 and old_mask[0, 1, 1]


def test_real_pysteps_kernel_rejects_partially_observed_interpolation():
    rate = np.full((32, 32), 10.0, dtype="float32")
    rate[8:24, 16:] = np.nan
    valid = np.isfinite(rate)
    velocity = np.zeros((2, 32, 32), dtype="float32")
    velocity[0] = -0.25
    forecast, support = _forecast_with_support(rate, valid, velocity, 2, 1, _semilagrangian)
    assert not support[0, 12, 15]
    np.testing.assert_allclose(forecast[support], 10.0)


def test_zero_and_missing_are_distinct():
    rate = np.zeros((4, 4), dtype="float32")
    velocity = np.zeros((2, 4, 4), dtype="float32")
    forecast, mask = forecast_with_full_support(
        rate, np.ones_like(rate), velocity, 1, 1, fractional
    )
    assert np.all(mask) and np.all(forecast == 0)
    forecast, mask = forecast_with_full_support(
        rate, np.zeros_like(rate), velocity, 1, 1, fractional
    )
    assert not np.any(mask) and np.all(np.isnan(forecast))


def test_new_versioned_profile_uses_strict_support():
    profile = load_pysteps_lk_profile(ROOT / "configs/nowcast/prelaunch-pysteps-lk-v2.yaml")
    assert profile.model_version == "pysteps-lk-2.0.0"
    assert profile.extrapolation.support_policy == "full_kernel_support_v2"
    with pytest.raises(ValueError, match="order"):
        forecast_with_full_support(
            np.ones((2, 2)), np.ones((2, 2)), np.zeros((2, 2, 2)), 1, 3, fractional
        )
