"""Per-gate causal RFI support, never a whole-ray generic-quality vote."""

from __future__ import annotations

import numpy as np

from ..qc_geometry import nearest_azimuth_matches
from .adapters import NativeSweep
from .objects import ObjectEvidence


def aggregate_temporal_rfi(current: NativeSweep, past: list[tuple[NativeSweep, ObjectEvidence]]):
    if len(past) > 3:
        raise ValueError("at most three independent past volumes are supported")
    count = np.zeros(current.shape, "uint8")
    votes = np.zeros(current.shape, "uint8")
    for native, evidence in past:
        # Only compare the SAME source cut and exact range bins. Azimuth jitter is matched,
        # not resampled. Native adapters independently exclude missing/duplicate rays.
        if current.name != native.name or not np.array_equal(current.ranges, native.ranges):
            continue
        current_meta = current.audit.get("cut_metadata", {})
        past_meta = native.audit.get("cut_metadata", {})
        if current_meta != past_meta:
            continue
        index, delta, rows = nearest_azimuth_matches(current.azimuth, native.azimuth)
        tolerance = (
            min(current.audit["azimuth_spacing_deg"], native.audit["azimuth_spacing_deg"]) * 0.45
        )
        rows &= delta <= tolerance
        rows &= current.geometry_good & native.geometry_good[index]
        rows &= np.abs(current.elevation - native.elevation[index]) <= 0.1
        available = evidence.arrays["RFI_STRUCTURE_AVAILABLE_MASK"] == 1
        # No polarimetry + no background is insufficient even to issue a negative vote.
        available &= (evidence.arrays["RFI_BACKGROUND_AVAILABLE_MASK"] == 1) | (
            native.field_available.get("RHOHV", np.zeros(native.shape, bool))
        )
        available = available[index] & rows[:, None] & current.field_available["DBZH"]
        count += available.astype("uint8")
        votes += (evidence.candidate[index] & available).astype("uint8")
    persistence = np.divide(
        votes, count, out=np.full(current.shape, np.nan, "float32"), where=count > 0
    )
    return {"TEMPORAL_CANDIDATE_PERSISTENCE": persistence, "TEMPORAL_RFI_SAMPLE_COUNT": count}
