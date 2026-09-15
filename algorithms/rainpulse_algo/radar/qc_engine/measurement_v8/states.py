"""Radial Viterbi proposal; it never makes an action or invents an observation.

Three states: weather/interference/mixed. Missing gates break sequences. A
low-support inferred run becomes undecided. Gate-local evidence is still required
by policy after decoding, so transition penalties cannot delete low-score gates.
This is a 1-D Markov model, NOT a cross-radar/4-D graph or signal reconstruction.
"""

import numpy as np

from .native import spans


def decode(probabilities, observed, ranges, cfg, weather_barrier=None):
    shape = observed.shape
    if probabilities.shape != (*shape, 3) or ranges.shape != (shape[1],):
        raise ValueError("state geometry mismatch")
    if len(ranges) < 2 or not np.isfinite(ranges).all() or np.any(np.diff(ranges) <= 0):
        raise ValueError("increasing finite range geometry required")
    dr = float(np.median(np.diff(ranges)))
    barrier = (
        np.zeros(shape, bool) if weather_barrier is None else np.asarray(weather_barrier, bool)
    )
    if barrier.shape != shape:
        raise ValueError("state barrier geometry mismatch")
    evaluated = observed & np.isfinite(probabilities).all(axis=-1)
    if np.any(probabilities[evaluated] < 0) or not np.allclose(
        probabilities[evaluated].sum(axis=1), 1, atol=1e-5
    ):
        raise ValueError("invalid class probabilities")
    output = np.full(shape, -1, "int8")
    penalty = np.full((3, 3), cfg.transition_cost)
    np.fill_diagonal(penalty, 0)
    for ray in range(shape[0]):
        for lo, hi in spans(evaluated[ray]):
            if not cfg.enabled:
                output[ray, lo:hi] = probabilities[ray, lo:hi].argmax(axis=1)
                output[ray, lo:hi][barrier[ray, lo:hi]] = 0
                continue
            unary = -np.log(np.clip(probabilities[ray, lo:hi], 1e-8, 1)) * dr / 1000
            unary[barrier[ray, lo:hi], 1:] = np.inf
            cost = unary[0].copy()
            back = np.zeros((hi - lo, 3), "int8")
            for j in range(1, hi - lo):
                options = cost[:, None] + penalty
                back[j] = options.argmin(axis=0)
                cost = options.min(axis=0) + unary[j]
            state = int(cost.argmin())
            for j in range(hi - lo - 1, -1, -1):
                output[ray, lo + j] = state
                state = int(back[j, state])
    if cfg.enabled and cfg.minimum_supported_m:
        for ray in range(shape[0]):
            for value in (1, 2):
                for lo, hi in spans(output[ray] == value):
                    if (hi - lo) * dr < cfg.minimum_supported_m:
                        output[ray, lo:hi] = -1
    output[~observed] = -1
    return output
