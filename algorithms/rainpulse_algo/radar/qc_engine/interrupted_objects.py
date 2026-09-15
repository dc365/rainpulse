"""Bounded same-ray identities from raw echoes and frozen RFI anchors.

Identity is not a rejection vote. Gaps never receive candidates or actions.
"""

import numpy as np
from .segments import runs


def interrupted_objects(
    native,
    anchor,
    *,
    maximum_gap_m=3000.0,
    maximum_span_m=40000.0,
    minimum_span_m=15000.0,
    minimum_occupied_fraction=0.25,
    minimum_anchor_m=2000.0,
    minimum_echo_dbz=-5.0,
):
    if np.shape(anchor) != native.shape:
        raise ValueError("anchor geometry differs")
    if not (0 < maximum_gap_m < minimum_span_m <= maximum_span_m):
        raise ValueError("invalid object distances")
    if not 0 < minimum_occupied_fraction <= 1 or minimum_anchor_m <= 0:
        raise ValueError("invalid object support")
    z = native.fields["DBZH"]
    echo = native.field_available["DBZH"] & np.isfinite(z) & (z >= minimum_echo_dbz)
    echo &= native.geometry_good[:, None]
    ids = np.zeros(native.shape, "uint32")
    candidate = np.zeros(native.shape, bool)
    records = []
    for ray in range(native.shape[0]):
        groups = []
        for lo, hi in runs(echo[ray]):
            # Split long continuous runs to bound all identities, even without gaps.
            step = max(1, int(maximum_span_m / native.gate_spacing_m))
            for start in range(lo, hi, step):
                end = min(start + step, hi)
                if (
                    groups
                    and (start - groups[-1][-1][1]) * native.gate_spacing_m <= maximum_gap_m
                    and (end - groups[-1][0][0]) * native.gate_spacing_m <= maximum_span_m
                ):
                    groups[-1].append((start, end))
                else:
                    groups.append([(start, end)])
        for group in groups:
            lo, hi = group[0][0], group[-1][1]
            actual = echo[ray, lo:hi]
            occupied = int(actual.sum())
            anchors = int((anchor[ray, lo:hi] & actual).sum())
            if (
                len(group) < 2
                or (hi - lo) * native.gate_spacing_m < minimum_span_m
                or occupied / (hi - lo) < minimum_occupied_fraction
                or anchors * native.gate_spacing_m < minimum_anchor_m
            ):
                continue
            identity = len(records) + 1
            ids[ray, lo:hi][actual] = identity
            candidate[ray, lo:hi] = actual
            records.append(
                {
                    "id": identity,
                    "ray": int(native.original_indices[ray]),
                    "start_gate": int(lo),
                    "end_gate": int(hi),
                    "observed_gates": occupied,
                    "anchor_gates": anchors,
                }
            )
    return candidate, ids, records
