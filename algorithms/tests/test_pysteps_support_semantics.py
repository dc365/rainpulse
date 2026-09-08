from __future__ import annotations

import numpy as np

from rainpulse_algo.nowcast.pysteps_lk import _forecast_with_support


def test_bilinear_forecast_rejects_partial_missing_interpolation_footprint() -> None:
    latest_rate = np.asarray([[10.0, 10.0, np.nan, np.nan]], dtype="float32")
    latest_valid = np.asarray([[1, 1, 0, 0]], dtype=bool)
    velocity = np.zeros((2, 1, 4), dtype="float32")

    def fake_extrapolator(field, _velocity, lead_count, interpolation_order):
        assert lead_count == 1
        if np.max(field) > 1.0:
            values = np.asarray([[10.0, 7.5, 0.0, 0.0]], dtype="float32")
        elif interpolation_order == 1:
            # The second destination cell draws 25% of its interpolation
            # footprint from a missing source cell.
            values = np.asarray([[1.0, 0.75, 0.0, 0.0]], dtype="float32")
        else:
            # This branch models the old nearest-neighbour validity mask,
            # which incorrectly called the mixed second cell fully valid.
            values = np.asarray([[1.0, 1.0, 0.0, 0.0]], dtype="float32")
        return np.repeat(values[np.newaxis, ...], lead_count, axis=0)

    forecast, valid = _forecast_with_support(
        latest_rate,
        latest_valid,
        velocity,
        lead_count=1,
        interpolation_order=1,
        extrapolate=fake_extrapolator,
    )

    np.testing.assert_array_equal(
        valid,
        np.asarray([[[True, False, False, False]]]),
    )
    assert forecast[0, 0, 0] == 10.0
    assert np.isnan(forecast[0, 0, 1])
    assert np.all(np.isnan(forecast[0, 0, 2:]))
