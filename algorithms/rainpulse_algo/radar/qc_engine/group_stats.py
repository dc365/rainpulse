"""Linear-time integer-label sufficient statistics; topology is supplied unchanged."""

from __future__ import annotations

import numpy as np


def grouped_counts(labels, count, *masks):
    a = np.asarray(labels)
    if (
        a.dtype.kind not in "iu"
        or type(count) is not int
        or count < 0
        or count > 100000
        or np.any(a < 0)
        or np.any(a > count)
    ):
        raise ValueError("invalid bounded label table")
    sizes = np.bincount(a.ravel(), minlength=count + 1)
    sizes[0] = 0
    counts = []
    for value in masks:
        m = np.asarray(value)
        if m.shape != a.shape or m.dtype != np.bool_:
            raise ValueError("label support requires a matching boolean mask")
        c = np.bincount(a[m], minlength=count + 1)
        c[0] = 0
        counts.append(c)
    return sizes, counts


def recurrence_tables(labels, count, first, second, minimum, maximum, fraction_min):
    sizes, (one, two) = grouped_counts(labels, count, first, second)
    fraction = np.full(count + 1, np.nan, dtype=np.float64)
    np.divide(np.minimum(one, two), sizes, out=fraction, where=sizes > 0)
    accepted = (sizes >= minimum) & (sizes <= maximum) & (fraction >= fraction_min)
    accepted[0] = False
    return (
        labels.astype(np.int32, copy=False),
        sizes[labels].astype(np.uint32),
        fraction[labels].astype(np.float32),
        accepted[labels],
    )


def seed_tables(labels, count, core, minimum_seeds, minimum_fraction, maximum, propagate):
    sizes, (seeds,) = grouped_counts(labels, count, core)
    fraction = np.full(count + 1, np.nan, dtype=np.float64)
    np.divide(seeds, sizes, out=fraction, where=sizes > 0)
    accepted = (
        (seeds >= minimum_seeds)
        & (fraction >= minimum_fraction)
        & (sizes <= maximum)
        & bool(propagate)
    )
    accepted[0] = False
    return (
        labels.astype(np.int32, copy=False),
        sizes[labels].astype(np.uint32),
        fraction[labels].astype(np.float32),
        accepted[labels] & ~core,
    )
