"""Version-4 additive contract validation; legacy contracts remain unchanged."""

from __future__ import annotations

import numpy as np


def validate_paper_fields(group, observed, reject, quarantine):
    shape = observed.shape
    required = {
        **{
            name: "uint8"
            for name in (
                "AFL_AVAILABLE_MASK",
                "AFL_CANDIDATE_MASK",
                "AFL_LOCAL_AVAILABLE_MASK",
                "AFL_LOCAL_CANDIDATE_MASK",
                "RDD_AVAILABLE_MASK",
                "RDD_CANDIDATE_MASK",
                "PAPER_CANDIDATE_MASK",
                "PAPER_STRUCTURE_MASK",
                "PAPER_PHASE_AVAILABLE_MASK",
                "PAPER_CONFIRMED_ADDITION_MASK",
                "PAPER_QUARANTINED_ADDITION_MASK",
                "PAPER_BASELINE_REJECT_MASK",
                "PAPER_BASELINE_QUARANTINE_MASK",
                "PAPER_TEMPORAL_SUPPORT_MASK",
            )
        },
        "AFL_SCORE": "float32",
        "AFL_LOCAL_SCORE": "float32",
        "PAPER_PHASE_BAD_PAIR_FRACTION": "float32",
        "PAPER_DECISION_REASON": "uint16",
    }
    values = {}
    for name, dtype in required.items():
        if name not in group or group[name].shape != shape or group[name].dtype != np.dtype(dtype):
            raise ValueError(f"invalid paper comparison field {name}")
        values[name] = group[name][:]
        if name.endswith("_MASK") and not np.isin(values[name], [0, 1]).all():
            raise ValueError("nonbinary paper mask")
        if name.endswith("_MASK") and np.any((values[name] == 1) & ~observed):
            raise ValueError(f"paper field {name} creates an observation")
    for prefix in ("AFL", "AFL_LOCAL", "RDD"):
        available = values[prefix + "_AVAILABLE_MASK"] == 1
        candidate = values[prefix + "_CANDIDATE_MASK"] == 1
        if np.any(candidate & ~available):
            raise ValueError("paper candidate without available evidence")
        if prefix != "RDD":
            score = values[prefix + "_SCORE"]
            if (
                np.any(~np.isnan(score[~available]))
                or np.any(~np.isfinite(score[available]))
                or np.any(score[available] < 0)
                or np.any(score[available] > 1)
            ):
                raise ValueError("paper membership score/support mismatch")
    domain = values["PAPER_CANDIDATE_MASK"] == 1
    union = (
        (values["AFL_CANDIDATE_MASK"] == 1)
        | (values["AFL_LOCAL_CANDIDATE_MASK"] == 1)
        | (values["RDD_CANDIDATE_MASK"] == 1)
    )
    confirmed = values["PAPER_CONFIRMED_ADDITION_MASK"] == 1
    withheld = values["PAPER_QUARANTINED_ADDITION_MASK"] == 1
    if np.any((values["PAPER_STRUCTURE_MASK"] == 1) & ~domain):
        raise ValueError("paper structure outside candidate support")
    if np.any(domain & ~union) or np.any((confirmed | withheld) & ~domain):
        raise ValueError("paper decisions outside their native candidate domain")
    if np.any(confirmed & (~reject | withheld)) or np.any(withheld & ~quarantine):
        raise ValueError("paper additions differ from final decisions")
    old_reject = values["PAPER_BASELINE_REJECT_MASK"] == 1
    old_quarantine = values["PAPER_BASELINE_QUARANTINE_MASK"] == 1
    if not np.array_equal(reject, old_reject | confirmed):
        raise ValueError("paper fusion changed V3 rejection without an addition record")
    if not np.array_equal(quarantine, (old_quarantine | withheld) & ~reject):
        raise ValueError("paper fusion restored a withheld baseline measurement")
    phase_available = values["PAPER_PHASE_AVAILABLE_MASK"] == 1
    fraction = values["PAPER_PHASE_BAD_PAIR_FRACTION"]
    if (
        np.any(~np.isnan(fraction[~phase_available]))
        or np.any(~np.isfinite(fraction[phase_available]))
        or np.any(fraction[phase_available] < 0)
        or np.any(fraction[phase_available] > 1)
    ):
        raise ValueError("phase evidence availability mismatch")
    return domain
