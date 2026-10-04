"""Performance regression: exact original blocks, fewer percentile calls."""

import numpy as np
import pytest

from rainpulse_algo.multiband.xqc_v2.block_percentiles import measure


@pytest.mark.parametrize("dtype", [np.float32, np.float64])
@pytest.mark.parametrize("q", [90, 50, [25, 75], [5, 95]])
def test_exact_ragged_original_blocks(dtype, q):
    rng = np.random.default_rng(725)
    values = rng.normal(size=600).astype(dtype)
    blocks = tuple(
        np.arange(start, start + size)
        for start, size in [(0, 67), (70, 67), (140, 45), (190, 45), (250, 0), (260, 3)]
    )
    expected = np.asarray(
        [
            np.percentile(values[b], q)
            if len(b)
            else (np.nan if np.ndim(q) == 0 else np.full(len(q), np.nan))
            for b in blocks
        ]
    )
    for budget in [0, 1000000]:
        actual = measure(values, blocks, q, maximum_bytes=budget)
        assert actual.dtype == expected.dtype
        np.testing.assert_array_equal(actual, expected)


def test_repeated_equal_native_blocks_use_one_numpy_call(monkeypatch):
    values = np.arange(4000, dtype=np.float32) / 7
    blocks = tuple(np.arange(i * 67, (i + 1) * 67) for i in range(40))
    original = np.percentile
    calls = []

    def counted(*args, **kwargs):
        calls.append(1)
        return original(*args, **kwargs)

    monkeypatch.setattr(np, "percentile", counted)
    result = measure(values, blocks, [5, 95], maximum_bytes=1000000)
    assert len(calls) == 1  # Original scalar implementation needs 40 calls.
    np.testing.assert_array_equal(result[0], original(values[blocks[0]], [5, 95]))


def test_memory_fallback_keeps_scalar_formula(monkeypatch):
    values = np.arange(30, dtype=np.float64)
    blocks = tuple(np.arange(i, i + 3) for i in range(0, 30, 3))
    original = np.percentile
    calls = []

    def counted(*args, **kwargs):
        calls.append(1)
        return original(*args, **kwargs)

    monkeypatch.setattr(np, "percentile", counted)
    output = measure(values, blocks, 90, maximum_bytes=0)
    assert len(calls) == len(blocks)
    np.testing.assert_array_equal(output, [original(values[b], 90) for b in blocks])


@pytest.mark.parametrize("bad", [np.nan, np.inf, -np.inf])
def test_nonfinite_inputs_keep_numpy_semantics(bad):
    values = np.arange(20, dtype=np.float32)
    values[4] = bad
    blocks = (np.arange(10), np.arange(10, 20))
    with np.errstate(invalid="ignore"):
        expected = [np.percentile(values[b], [5, 95]) for b in blocks]
        actual = measure(values, blocks, [5, 95], maximum_bytes=1000000)
    np.testing.assert_array_equal(actual, expected)


def test_response_subtractions_keep_original_order_and_dtype():
    rng = np.random.default_rng(325)
    values = rng.normal(size=180).astype(np.float32)
    law, trend = rng.normal(size=(2, 180)).astype(np.float64)
    blocks = (np.arange(0, 67), np.arange(70, 137), np.arange(140, 180))
    expected = [np.percentile(values[b] - law[b] - trend[b], [5, 95]) for b in blocks]
    for budget in [0, 1000000]:
        actual = measure(values, blocks, [5, 95], maximum_bytes=budget, offsets=(law, trend))
        assert actual.dtype == np.asarray(expected).dtype
        np.testing.assert_array_equal(actual, expected)
