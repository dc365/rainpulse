"""A small, opt-in compiled kernel. Scientific scores remain NumPy float64.

No fastmath, parallel writes, changes to source order, or silent backend fallback.
The reference expression is the existing candidate/age/resolution tie rule.
"""

from __future__ import annotations

import numpy as np

_COMPILED = None


def _select_loop(
    admitted,
    candidate,
    noecho,
    sample,
    ray,
    gate,
    height,
    new_age,
    new_resolution,
    source,
    score,
    values,
    winner,
    wray,
    wgate,
    h,
    age,
    resolution,
):
    for i in range(admitted.shape[0]):
        for j in range(admitted.shape[1]):
            if not admitted[i, j]:
                continue
            c, previous = candidate[i, j], score[i, j]
            tied = (c == previous) or (
                np.isfinite(c) and np.isfinite(previous) and abs(c - previous) <= 1e-12
            )
            better = (c > previous + 1e-12) or (
                tied
                and (
                    (new_age[i, j] < age[i, j])
                    or ((new_age[i, j] == age[i, j]) and (new_resolution[i, j] < resolution[i, j]))
                )
            )
            if better:
                score[i, j] = c
                values[i, j] = np.nan if noecho[i, j] else sample[i, j]
                winner[i, j], wray[i, j], wgate[i, j] = source, ray[i, j], gate[i, j]
                h[i, j], age[i, j] = height[i, j], new_age[i, j]
                resolution[i, j] = new_resolution[i, j]


def require_numba():
    global _COMPILED
    if _COMPILED is None:
        try:
            from numba import njit
        except ImportError as error:
            raise RuntimeError("numba backend requested but Numba is unavailable") from error
        _COMPILED = njit(cache=True, fastmath=False, nogil=True)(_select_loop)
    return _COMPILED


def select_winners(
    admitted,
    candidate,
    noecho,
    sample,
    ray,
    gate,
    height,
    new_age,
    new_resolution,
    source,
    score,
    values,
    winner,
    wray,
    wgate,
    h,
    age,
    resolution,
    *,
    backend="numpy",
):
    args = (
        admitted,
        candidate,
        noecho,
        sample,
        ray,
        gate,
        height,
        new_age,
        new_resolution,
        source,
        score,
        values,
        winner,
        wray,
        wgate,
        h,
        age,
        resolution,
    )
    if backend == "numba":
        require_numba()(*args)
        return
    if backend != "numpy":
        raise ValueError("unknown selection backend")
    tied = np.isclose(candidate, score, rtol=0, atol=1e-12)
    better = admitted & (
        (candidate > score + 1e-12)
        | (tied & ((new_age < age) | ((new_age == age) & (new_resolution < resolution))))
    )
    score[better] = candidate[better]
    values[better] = np.where(noecho[better], np.nan, sample[better])
    winner[better], wray[better], wgate[better] = source, ray[better], gate[better]
    h[better], age[better], resolution[better] = (
        height[better],
        new_age[better],
        new_resolution[better],
    )


def warmup(backend):
    """Warm the common float32-observation signature before taking tasks."""
    if backend == "numpy":
        return
    if backend != "numba":
        raise ValueError("unknown selection backend")
    b = np.ones((2, 2), bool)
    f = np.ones((2, 2), np.float64)
    z = f.astype(np.float32)
    i = np.zeros((2, 2), np.int64)
    state = (
        np.full((2, 2), -np.inf),
        z.copy(),
        i.astype(np.int32),
        i.astype(np.int32),
        i.astype(np.int32),
        z.copy(),
        z.copy(),
        z.copy(),
    )
    select_winners(b, f, ~b, z, i, i, f, f, f, 0, *state, backend=backend)
