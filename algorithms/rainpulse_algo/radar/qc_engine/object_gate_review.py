"""Join geometry and gate evidence without upgrading shape into a deletion rule."""

from enum import IntFlag

import numpy as np


class GateReason(IntFlag):
    GEOMETRY_UNCONFIRMED = 1
    SOURCE_UNAVAILABLE = 2
    POWER_CONFLICT = 4
    TARGET_CONFLICT = 8
    PROTECTED = 16
    PLATEAU = 32
    MISSING = 64
    HYPOTHESIS_ELIGIBLE = 128


def review_object_gates(labels, paired_object_ids, bws, raw):
    raw = np.asarray(raw)
    inputs = [
        np.asarray(labels),
        *[
            np.asarray(bws[k])
            for k in ("BWS_REASON", "BWS_CANDIDATE_MASK", "BWS_RANGE_RESIDUAL_DB")
        ],
    ]
    if any(x.shape != raw.shape for x in inputs):
        raise ValueError("gate review geometry differs")
    labels, why, candidate, residual = inputs
    observed = np.isfinite(raw)
    geometry = np.isin(labels, sorted(paired_object_ids)) & (labels > 0)
    reference = np.isfinite(residual) & ((why & 3) == 3)
    power = reference & (abs(residual) <= 2.5)
    protected = (why & 8) != 0
    plateau = (why & 32) != 0
    # BWS conflict includes externally supplied conflicts; do not erase it.
    conflict = (why & 16) != 0
    matched = (why & 4) != 0
    reasons = np.zeros(raw.shape, "uint16")
    for mask, bit in [
        (~geometry, GateReason.GEOMETRY_UNCONFIRMED),
        (~reference, GateReason.SOURCE_UNAVAILABLE),
        (reference & ~power, GateReason.POWER_CONFLICT),
        (power & (~matched | conflict), GateReason.TARGET_CONFLICT),
        (protected, GateReason.PROTECTED),
        (plateau, GateReason.PLATEAU),
        (~observed, GateReason.MISSING),
    ]:
        reasons[mask] |= int(bit)
    proposal = (
        geometry & observed & power & matched & (candidate == 1) & ~protected & ~conflict & ~plateau
    )
    reasons[proposal] |= int(GateReason.HYPOTHESIS_ELIGIBLE)
    return reasons, proposal
