import numpy as np
import pytest
from rainpulse_algo.radar.qc_engine.support import weather_support


def test_default_preserves_max_and_missing():
    v = np.array([[.9, np.nan, np.nan]])
    c = np.array([[.2, .8, np.nan]])
    np.testing.assert_equal(weather_support(v, c, np.ones_like(v)), np.fmax(v, c).astype('float32'))


def test_split_only_removes_vertical_protection_for_candidates():
    v = np.array([[.9, .9, .9]])
    c = np.array([[np.nan, .8, np.nan]])
    out = weather_support(v, c, [[True, True, False]], split=True)
    assert np.isnan(out[0, 0])  # unknown, not zero or proof of contamination
    np.testing.assert_allclose(out[0, 1:], [.8, .9])


def test_shape_error():
    with pytest.raises(ValueError):
        weather_support(np.zeros((2, 3)), None, np.zeros((3, 2)))
