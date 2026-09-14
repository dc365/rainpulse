"""V6 actions must be additive and traceable independently of the renderer."""

from __future__ import annotations

import numpy as np


def validate_residual_fields(group, observed, reject, quarantine):
    required = {
        "V6_CAPABILITY_CODE": "uint8",
        "V6_DECISION_REASON": "uint32",
        **{
            name: "uint8"
            for name in (
                "V6_CANDIDATE_MASK",
                "V6_WEATHER_PROTECTED_MASK",
                "V6_POL_CORROBORATED_MASK",
                "V6_CONFIRMED_ADDITION_MASK",
                "V6_QUARANTINED_ADDITION_MASK",
                "V6_BASELINE_REJECT_MASK",
                "V6_BASELINE_QUARANTINE_MASK",
                "V6_BASELINE_ELIGIBLE_MASK",
            )
        },
    }
    for name, dtype in required.items():
        if (
            name not in group
            or group[name].shape != observed.shape
            or group[name].dtype != np.dtype(dtype)
        ):
            raise ValueError(f"invalid V6 field {name}")
    for name in group:
        if not name.startswith("V6_"):
            continue
        value = group[name][:]
        if value.shape != observed.shape:
            raise ValueError("V6 diagnostic geometry changed")
        if name.endswith("_MASK") and np.any((value == 1) & ~observed):
            raise ValueError("V6 mask created observations")
        if name.endswith("_OBJECT_ID") and np.any((value > 0) & ~observed):
            raise ValueError("V6 object created observations")

    def mask(n):
        return group[n][:] == 1

    domain = mask("V6_CANDIDATE_MASK")
    add = mask("V6_CONFIRMED_ADDITION_MASK")
    withheld = mask("V6_QUARANTINED_ADDITION_MASK")
    old = mask("V6_BASELINE_REJECT_MASK")
    oldq = mask("V6_BASELINE_QUARANTINE_MASK")
    if np.any((add | withheld) & ~domain) or np.any(add & withheld):
        raise ValueError("V6 additions outside candidates or contradictory")
    if not np.array_equal(reject, old | add) or not np.array_equal(
        quarantine, (oldq | withheld) & ~reject
    ):
        raise ValueError("V6 final actions differ from frozen V5 plus additions")
    if np.any(mask("QPE_ELIGIBLE_MASK") & ~mask("V6_BASELINE_ELIGIBLE_MASK")):
        raise ValueError("V6 restored a withheld V5 measurement")
    if np.any(add & ~mask("V6_POL_CORROBORATED_MASK")):
        raise ValueError("V6 confirmation lacks local reliable measurement evidence")
    if "V6_RANGE_MODEL_CODE" in group and np.any(add & (group["V6_RANGE_MODEL_CODE"][:] == 3)):
        raise ValueError("unverified plateau was confirmed")
    if "V6_PARENT_RAY" in group:
        ray, gate = group["V6_PARENT_RAY"][:], group["V6_PARENT_GATE"][:]
        linked = mask("V6_PERIPHERAL_COMPATIBLE_MASK")
        if (
            np.any(ray[~linked] != -1)
            or np.any(gate[~linked] != -1)
            or np.any(ray[linked] < 0)
            or np.any(ray[linked] >= observed.shape[0])
            or np.any(gate[linked] < 0)
            or np.any(gate[linked] >= observed.shape[1])
        ):
            raise ValueError("V6 parent index outside original sweep")
        if linked.any() and np.any(~observed[ray[linked], gate[linked]]):
            raise ValueError("V6 parent is an absent observation")
    return domain
