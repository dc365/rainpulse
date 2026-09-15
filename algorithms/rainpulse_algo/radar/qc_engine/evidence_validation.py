"""Validate V7 against the recorded pre-graph decisions, preserving older gates."""
import numpy as np


def validate_evidence_fields(group, observed, reject):
    for name in ("V7_BASELINE_REJECT_MASK", "V7_BASELINE_QUARANTINE_MASK",
                 "V7_BASELINE_ELIGIBLE_MASK"):
        if name not in group or group[name].shape != observed.shape or group[name].dtype != np.dtype("uint8"):
            raise ValueError(f"invalid V7 field {name}")
    for name in group:
        if not name.startswith("V7_"):
            continue
        value = group[name][:]
        if value.shape != observed.shape:
            raise ValueError("V7 diagnostic geometry differs")
        if name.endswith("_MASK") and (np.any(value > 1) or np.any((value == 1) & ~observed)):
            raise ValueError("V7 mask created observations")

    def mask(name):
        return group[name][:] == 1

    old = mask("V7_BASELINE_REJECT_MASK")
    oldq = mask("V7_BASELINE_QUARANTINE_MASK")
    quarantine = mask("RFI_QUARANTINE_MASK")
    oc_added = np.zeros(observed.shape, bool)
    if "OC1_ADDED_QUARANTINE_MASK" in group:
        for name in ("OC1_ADDED_QUARANTINE_MASK", "OC1_BASELINE_QUARANTINE_MASK"):
            if name not in group or group[name].shape != observed.shape or group[name].dtype != np.dtype("uint8"):
                raise ValueError("invalid OC1 action provenance")
        oc_added = mask("OC1_ADDED_QUARANTINE_MASK")
        oc_base = mask("OC1_BASELINE_QUARANTINE_MASK")
        if np.any(oc_added & (~observed | reject | oc_base)) or not np.array_equal(quarantine, oc_base | oc_added):
            raise ValueError("OC1 final quarantine differs from baseline plus additions")
        quarantine = oc_base

    domain = np.zeros(observed.shape, bool)
    add = domain.copy()
    withheld = domain.copy()
    if "V7_GRAPH_REVIEW_MASK" in group:
        domain = mask("V7_GRAPH_REVIEW_MASK")
        for name in ("V7_CONFIRMED_ADDITION_MASK", "V7_QUARANTINED_ADDITION_MASK"):
            if name not in group:
                raise ValueError(f"missing V7 action provenance {name}")
        add = mask("V7_CONFIRMED_ADDITION_MASK")
        withheld = mask("V7_QUARANTINED_ADDITION_MASK")
    if np.any((add | withheld) & ~domain) or np.any(add & withheld):
        raise ValueError("V7 additions outside hypotheses or contradictory")
    if not np.array_equal(reject, old | add) or not np.array_equal(quarantine, (oldq | withheld) & ~reject):
        raise ValueError("V7 final actions differ from recorded baseline plus additions")
    if np.any(mask("QPE_ELIGIBLE_MASK") & ~mask("V7_BASELINE_ELIGIBLE_MASK")):
        raise ValueError("V7 restored a withheld baseline measurement")
    return domain | oc_added
