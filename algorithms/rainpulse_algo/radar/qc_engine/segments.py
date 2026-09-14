"""Native radial intervals. Linking an identity never creates a measurement."""

from __future__ import annotations

import numpy as np


def runs(mask):
    edges = np.diff(np.r_[False, mask, False].astype("int8"))
    return list(zip(np.flatnonzero(edges == 1), np.flatnonzero(edges == -1), strict=True))


def intervals(seed, bridgeable, dr, max_gap_m, max_fraction):
    items = runs(seed)
    if not items:
        return []
    output = []
    lo, hi = items[0]
    count = hi - lo
    for a, b in items[1:]:
        proposed = count + b - a
        if (
            (a - hi) * dr <= max_gap_m
            and bridgeable[hi:a].all()
            and (1 - proposed / (b - lo) <= max_fraction + 1e-12)
        ):
            hi, count = b, proposed
        else:
            output.append((int(lo), int(hi), int(count)))
            lo, hi, count = a, b, b - a
    output.append((int(lo), int(hi), int(count)))
    return output


def associated_support(seed, observed, bridgeable, dr, minimum_m, max_gap_m, max_fraction):
    support = np.zeros(seed.shape, bool)
    linked = np.zeros(seed.shape, bool)
    for ray in range(len(seed)):
        for lo, hi, count in intervals(seed[ray], bridgeable[ray], dr, max_gap_m, max_fraction):
            if count * dr + 1e-6 < minimum_m:
                continue
            support[ray, lo:hi] = seed[ray, lo:hi]
            linked[ray, lo:hi] = observed[ray, lo:hi] & bridgeable[ray, lo:hi] & ~seed[ray, lo:hi]
    return support, linked
