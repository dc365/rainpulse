"""Strict additive v5 provenance/support validation, independent of renderer."""

from __future__ import annotations

import numpy as np

MASKS = (
    "V5_RANGE_CANDIDATE_MASK",
    "V5_RANGE_FIT_AVAILABLE_MASK",
    "V5_SNR_AVAILABLE_MASK",
    "V5_RELIABLE_POL_MASK",
    "V5_POL_CORROBORATED_MASK",
    "V5_PAPER_STRUCTURE_MASK",
    "V5_PAPER_LINKED_MASK",
    "V5_CANDIDATE_MASK",
    "V5_WEATHER_CONFLICT_MASK",
    "V5_BASELINE_REJECT_MASK",
    "V5_BASELINE_QUARANTINE_MASK",
    "V5_BASELINE_ELIGIBLE_MASK",
    "V5_CONFIRMED_ADDITION_MASK",
    "V5_QUARANTINED_ADDITION_MASK",
)
DTYPES = {
    **{name: "uint8" for name in MASKS},
    "V5_CAPABILITY_CODE": "uint8",
    "V5_RAW_POL_COUNT": "uint8",
    "V5_RANGE_MODEL_CODE": "uint8",
    "V5_RANGE_OBJECT_ID": "uint32",
    "V5_DECISION_REASON": "uint32",
    **{
        name: "float32"
        for name in ("V5_RANGE_RESIDUAL_P90_DB", "V5_RANGE_GROWTH_DB", "V5_RANGE_SPAN_M")
    },
}


def validate_crossradar_fields(group, observed, reject, quarantine):
    fields = {}
    for name, dtype in DTYPES.items():
        if (
            name not in group
            or group[name].shape != observed.shape
            or group[name].dtype != np.dtype(dtype)
        ):
            raise ValueError(f"invalid cross-radar field {name}")
        value = group[name][:]
        if name in MASKS and (not np.isin(value, [0, 1]).all() or np.any((value == 1) & ~observed)):
            raise ValueError(f"cross-radar mask {name} creates observations")
        fields[name] = value
    domain = fields["V5_CANDIDATE_MASK"] == 1
    range_candidate = fields["V5_RANGE_CANDIDATE_MASK"] == 1
    for name in ("V5_RANGE_CANDIDATE_MASK", "V5_PAPER_STRUCTURE_MASK", "V5_PAPER_LINKED_MASK"):
        if np.any((fields[name] == 1) & ~domain):
            raise ValueError("V5 evidence outside decision domain")
    if not np.array_equal(range_candidate, fields["V5_RANGE_OBJECT_ID"] > 0):
        raise ValueError("V5 range object identity mismatch")
    if not np.array_equal(range_candidate, fields["V5_RANGE_FIT_AVAILABLE_MASK"] == 1):
        raise ValueError("V5 fit support mismatch")
    mode = fields["V5_RANGE_MODEL_CODE"]
    if np.any(mode > 3) or not np.array_equal(mode > 0, range_candidate):
        raise ValueError("V5 range-model code mismatch")
    for name in ("V5_RANGE_RESIDUAL_P90_DB", "V5_RANGE_GROWTH_DB", "V5_RANGE_SPAN_M"):
        val = fields[name]
        if (
            np.any(~np.isnan(val[~range_candidate]))
            or np.any(~np.isfinite(val[range_candidate]))
            or np.any(val[range_candidate] < 0)
        ):
            raise ValueError("V5 fit statistics missing or outside measured domain")
    cap = fields["V5_CAPABILITY_CODE"]
    if np.any(cap > 3) or not np.array_equal(cap > 0, observed):
        raise ValueError("V5 capability changes observation support")
    if np.any(fields["V5_RAW_POL_COUNT"] > 3):
        raise ValueError("V5 raw polarization count invalid")
    if not np.array_equal(cap == 3, fields["V5_RELIABLE_POL_MASK"] == 1):
        raise ValueError("V5 capability/reliability disagree")
    if np.any(
        (cap == 3) & ((fields["V5_SNR_AVAILABLE_MASK"] == 0) | (fields["V5_RAW_POL_COUNT"] < 2))
    ):
        raise ValueError("V5 reliable measurements lack SNR or raw polarimetric support")
    if np.any(fields["V5_RAW_POL_COUNT"][~observed] != 0) or np.any(
        fields["V5_DECISION_REASON"][~observed] != 0
    ):
        raise ValueError("V5 capability/reasons outside original observations")
    if np.any((fields["V5_POL_CORROBORATED_MASK"] == 1) & (cap != 3)):
        raise ValueError("V5 polarization confirmation lacks reliable measurements")
    confirmed = fields["V5_CONFIRMED_ADDITION_MASK"] == 1
    withheld = fields["V5_QUARANTINED_ADDITION_MASK"] == 1
    old_reject = fields["V5_BASELINE_REJECT_MASK"] == 1
    old_quarantine = fields["V5_BASELINE_QUARANTINE_MASK"] == 1
    if np.any((confirmed | withheld) & ~domain) or np.any(confirmed & withheld):
        raise ValueError("V5 actions outside available candidate domain")
    if np.any(confirmed & (mode == 3)):
        raise ValueError("unverified numerical plateau cannot authorize confirmation")
    if not np.array_equal(reject, old_reject | confirmed):
        raise ValueError("V5 rejection lacks baseline/addition provenance")
    if not np.array_equal(quarantine, (old_quarantine | withheld) & ~reject):
        raise ValueError("V5 quarantine lacks baseline/addition provenance")
    if np.any((group["QPE_ELIGIBLE_MASK"][:] == 1) & (fields["V5_BASELINE_ELIGIBLE_MASK"] == 0)):
        raise ValueError("V5 restored an ineligible baseline measurement")
    return domain
