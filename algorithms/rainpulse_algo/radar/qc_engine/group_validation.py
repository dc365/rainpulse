"""Recompute grouped facts, preserving the existing validator's accept/reject rules.

This is independent of the producer's saved counts/fractions. Sparse, negative,
or corrupted huge labels use compact indices rather than label-sized allocation.
No gate-to-object association, threshold, tolerance, or action is changed.
"""

from __future__ import annotations

import numpy as np

from rainpulse_algo.performance import observe, timed


def _groups(oid, selected):
    flat = np.asarray(oid).ravel()
    # Dense native integer labels need no sort or full inverse reconstruction.
    # Cap the table before allocation; malformed huge IDs take the compact path.
    if (
        flat.dtype.kind in "iu"
        and flat.size
        and flat.min() >= 0
        and int(flat.max()) <= min(flat.size, 1_000_000)
    ):
        inverse = flat.astype(np.intp, copy=False)
        length = int(inverse.max()) + 1
        counts = np.bincount(inverse, minlength=length)
        first = np.full(length, flat.size, dtype=np.intp)
        np.minimum.at(first, inverse, np.arange(flat.size, dtype=np.intp))
        first[counts == 0] = 0  # never selected; avoids reading out of bounds
        wanted = np.zeros(length, bool)
        wanted[inverse[np.asarray(selected, dtype=bool).ravel()]] = True
        return np.arange(length), first, inverse, counts, wanted
    keys, first, inverse, counts = np.unique(
        flat, return_index=True, return_inverse=True, return_counts=True
    )
    selected_keys = np.unique(np.asarray(oid)[np.asarray(selected, dtype=bool)])
    # NaN is not equal to itself: isin would otherwise silently drop a selected
    # object that the legacy per-object loop rejected as an empty component.
    if selected_keys.dtype.kind == "f" and np.isnan(selected_keys).any():
        raise ValueError("selected object identity is NaN")
    wanted = np.isin(keys, selected_keys)
    return keys, first, inverse, counts, wanted


@timed("s.group_validation.seed")
def validate_seed_objects(
    oid,
    sizes,
    fractions,
    core,
    propagated,
    *,
    maximum_object_gates,
    minimum_seed_gates,
    minimum_seed_fraction,
):
    """Equivalent to the original per-selected-object size/allclose check.

    The legacy contract checks the FIRST stored size and ALL stored fractions.
    Preserve that exact rule here; tightening unrelated rules is not perf work.
    """
    if not np.any(propagated):
        return
    keys, first, inverse, counts, wanted = _groups(oid, propagated)
    seeds = np.bincount(inverse[np.asarray(core, bool).ravel()], minlength=len(keys))
    observe("s.validated_seed_objects", int(wanted.sum()))
    expected = np.divide(seeds, counts, out=np.zeros(len(counts), float), where=counts > 0)
    mismatch = (
        np.bincount(
            inverse[
                ~np.isclose(
                    np.asarray(fractions).ravel(),
                    expected[inverse],
                    rtol=1e-5,
                    atol=1e-8,
                    equal_nan=False,
                )
            ],
            minlength=len(keys),
        )
        > 0
    )
    saved = np.asarray(sizes).ravel()[first]
    # int(saved_first) was the legacy scalar operation. Preserve truncation for
    # unusual float-valued inputs as well as the normal uint32 contract.
    if saved.dtype.kind == "f":
        saved = np.trunc(saved)
    invalid_value = (counts != saved) | mismatch
    if np.any(wanted & invalid_value):
        raise ValueError("strong near object size or seed fraction differs")
    if np.any(
        wanted
        & (
            (counts > maximum_object_gates)
            | (seeds < minimum_seed_gates)
            | (expected < minimum_seed_fraction)
        )
    ):
        raise ValueError("strong near object lacks sufficient bounded seed support")


@timed("s.group_validation.temporal")
def validate_temporal_objects(
    oid, sizes, fractions, selected, *, minimum_gates, maximum_gates, minimum_recurrence_fraction
):
    """Equivalent to legacy selected-object count/first-fraction validation."""
    if not np.any(selected):
        return
    _, first, _, counts, wanted = _groups(oid, selected)
    observe("s.validated_temporal_objects", int(wanted.sum()))
    saved = np.asarray(sizes).ravel()[first]
    if saved.dtype.kind == "f":
        saved = np.trunc(saved)
    if np.any(wanted & (counts != saved)):
        raise ValueError("temporal low-rho object size differs")
    f = np.asarray(fractions).ravel()[first].astype(float)
    if np.any(
        wanted
        & (
            (counts < minimum_gates)
            | (counts > maximum_gates)
            | (f + 1e-6 < minimum_recurrence_fraction)
        )
    ):
        raise ValueError("temporal low-rho object lacks recurrent support")
