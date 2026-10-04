"""Exact NumPy block percentiles with bounded call-local batching.

Each row contains one unchanged original block. Empty blocks remain NaN;
different blocks never share samples. Insufficient temporary allowance uses
the original scalar calculation, without changing any resource refusal.
"""

import numpy as np


def measure(values, blocks, quantiles, *, maximum_bytes, offsets=()):
    scalar = np.ndim(quantiles) == 0
    q = np.atleast_1d(quantiles)
    # Include index bookkeeping, output, stacking, NumPy's partition copy and
    # interpolation temporaries before allocating the batched workspace.
    samples = sum(len(block) for block in blocks)
    required = samples * 48 + len(blocks) * (256 + len(q) * 64)

    def native_values(block):
        native = values[block]
        for offset in offsets:
            native = native - offset[block]
        return native

    if required > maximum_bytes:
        return np.asarray(
            [
                np.percentile(native_values(b), quantiles)
                if len(b)
                else (np.nan if scalar else np.full(len(q), np.nan))
                for b in blocks
            ]
        )
    output = [np.nan if scalar else np.full(len(q), np.nan) for _ in blocks]
    groups = {}
    for index, block in enumerate(blocks):
        if len(block):
            groups.setdefault(len(block), []).append(index)
    for indices in groups.values():
        native = np.stack([native_values(blocks[index]) for index in indices])
        # Preserve scalar-q dtype/interpolation too: NumPy treats a scalar
        # percentile differently from a one-element quantile array on float32.
        measured = np.percentile(native, quantiles, axis=1)
        for index, value in zip(indices, measured if scalar else measured.T, strict=True):
            output[index] = value
    return np.asarray(output)
