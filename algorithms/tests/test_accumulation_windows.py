import numpy as np
import pytest

from rainpulse_algo.products.accumulation import accumulate_windows


def inputs(members=1):
    rate = np.full((members, 24, 2, 3), 6, dtype=np.float32)
    return rate, np.ones_like(rate, dtype=np.uint8), np.full_like(rate, 0.8)


def test_constant_rate_units_and_additivity():
    result = accumulate_windows(*inputs(), lead_minutes=list(range(5, 121, 5)))
    np.testing.assert_allclose(result.amount_mm[:, 0], 6)
    np.testing.assert_allclose(result.amount_mm[:, 1], 6)
    np.testing.assert_allclose(result.amount_mm[:, 2], 12)
    np.testing.assert_allclose(
        result.amount_mm[:, 0] + result.amount_mm[:, 1], result.amount_mm[:, 2]
    )
    np.testing.assert_allclose(result.confidence, 0.8)


def test_missing_is_not_dry_and_does_not_poison_other_hour():
    rate, valid, confidence = inputs()
    valid[0, 0, 0, 0] = 0
    rate[:, :, 1, 1] = 0
    confidence[0, 13, 0, 1] = 0.1
    result = accumulate_windows(rate, valid, confidence, lead_minutes=range(5, 121, 5))
    assert np.isnan(result.amount_mm[0, 0, 0, 0])
    assert result.amount_mm[0, 1, 0, 0] == 6
    assert np.isnan(result.amount_mm[0, 2, 0, 0])
    assert np.all(result.amount_mm[0, :, 1, 1] == 0)
    assert result.confidence[0, 1, 0, 1] == pytest.approx(0.1)


def test_accumulate_members_before_quantile():
    rate, valid, confidence = inputs(3)
    rate[:] = 0
    rate[0, :12] = 10
    rate[1, 12:] = 10
    result = accumulate_windows(rate, valid, confidence, lead_minutes=range(5, 121, 5))
    values, support, _ = result.statistic(quantile=0.5)
    assert np.all(support)
    assert np.all(values[0] == 0)
    assert np.all(values[1] == 0)
    assert np.all(values[2] == 10)
    assert np.all(np.median(rate, axis=0) == 0)


def test_ensemble_statistic_requires_every_member():
    rate, valid, confidence = inputs(3)
    valid[1, 2, 0, 0] = 0
    result = accumulate_windows(rate, valid, confidence, lead_minutes=range(5, 121, 5))
    for quantile in (None, 0.5):
        values, support, quality = result.statistic(quantile=quantile)
        assert np.isnan(values[0, 0, 0])
        assert not support[0, 0, 0]
        assert quality[0, 0, 0] == 0
        assert support[1, 0, 0]


@pytest.mark.parametrize("leads", [range(0, 120, 5), range(10, 250, 10), [5] * 24])
def test_reject_wrong_cadence(leads):
    with pytest.raises(ValueError, match="lead"):
        accumulate_windows(*inputs(), lead_minutes=leads)


@pytest.mark.parametrize("bad", [np.nan, np.inf, -1])
def test_reject_invalid_supported_values(bad):
    rate, valid, confidence = inputs()
    rate[0, 0, 0, 0] = bad
    with pytest.raises(ValueError, match="rain"):
        accumulate_windows(rate, valid, confidence, lead_minutes=range(5, 121, 5))


def test_reject_invalid_confidence():
    rate, valid, confidence = inputs()
    confidence[0, 0, 0, 0] = np.nan
    with pytest.raises(ValueError, match="confidence"):
        accumulate_windows(rate, valid, confidence, lead_minutes=range(5, 121, 5))
