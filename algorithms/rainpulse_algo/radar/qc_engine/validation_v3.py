"""Additional serialized V3 diagnostic and decision invariants."""

from __future__ import annotations

import numpy as np

from .refinement import V3Path


def validate_v3(group, attrs):
    if attrs.get("decision_version") != "rfi-multivariate-v3":
        raise ValueError("V3 artifact has an incompatible decision version")
    shape = group["VALID_MASK"].shape
    fields = {
        "RFI_V3_EVIDENCE_BITS": "uint16",
        "RFI_V3_BLOCKER_BITS": "uint16",
        "RFI_V3_DECISION_PATH": "uint8",
        "TEMPORAL_RFI_VOTE_COUNT": "uint8",
        "RFI_PHASE_CURVATURE_DEG": "float32",
        "RFI_PHASE_NOISE_FRACTION": "float32",
        "RFI_ZDR_OUTLIER_FRACTION": "float32",
    }
    for key in (
        "RFI_SEARCH_MASK",
        "RFI_STRUCTURAL_SEED_MASK",
        "RFI_CORE_SEED_MASK",
        "RFI_PERIPHERY_MASK",
        "RFI_ROUGH_CANDIDATE_MASK",
        "RFI_SNR_RELIABLE_MASK",
        "RFI_WEATHER_BARRIER_MASK",
        "RFI_POLARIMETRIC_STRONG_MASK",
        "RFI_POLARIMETRIC_ANOMALY_MASK",
        "RFI_PHASE_LOCAL_AVAILABLE_MASK",
        "RFI_PHASE_PAIR_AVAILABLE_MASK",
        "RFI_PHASE_ANOMALY_MASK",
        "RFI_ZDR_LOCAL_AVAILABLE_MASK",
        "RFI_ZDR_ANOMALY_MASK",
        "RFI_V3_JOINT_CONFIRMED_MASK",
    ):
        fields[key] = "uint8"
    for name, dtype in fields.items():
        if name not in group or group[name].shape != shape or group[name].dtype != np.dtype(dtype):
            raise ValueError(f"invalid V3 field {name}")
    observed = group["VALID_MASK"][:] == 1
    search = group["RFI_SEARCH_MASK"][:] == 1
    structural = group["RFI_STRUCTURAL_SEED_MASK"][:] == 1
    core = group["RFI_CORE_SEED_MASK"][:] == 1
    periphery = group["RFI_PERIPHERY_MASK"][:] == 1
    if not np.array_equal(search, group["RFI_OBJECT_ID"][:] > 0):
        raise ValueError("V3 search and object identity disagree")
    if np.any(core & ~structural) or np.any(structural & ~search):
        raise ValueError("V3 core/structural/search masks are not nested")
    if not np.array_equal(periphery, search & ~structural) or np.any(search & ~observed):
        raise ValueError("V3 periphery created observations or changed structural identity")
    path = group["RFI_V3_DECISION_PATH"][:]
    if np.any(path > max(V3Path)) or not np.array_equal(path > 0, search):
        raise ValueError("invalid V3 decision path")
    for value, available in (
        ("RFI_PHASE_NOISE_FRACTION", "RFI_PHASE_LOCAL_AVAILABLE_MASK"),
        ("RFI_ZDR_OUTLIER_FRACTION", "RFI_ZDR_LOCAL_AVAILABLE_MASK"),
    ):
        number, supported = group[value][:], group[available][:] == 1
        if (
            np.any(~np.isfinite(number[supported]))
            or np.any(~np.isnan(number[~supported]))
            or np.any(number < 0)
            or np.any(number > 1)
        ):
            raise ValueError(f"V3 measurement availability differs for {value}")
    confirmed = group["RFI_RISK_STATE"][:] == 3
    if np.any(confirmed & (group["RFI_SNR_RELIABLE_MASK"][:] == 0)):
        raise ValueError("V3 confirmed RFI lacks measured reliable SNR")
    if np.any(confirmed & (group["RFI_WEATHER_BARRIER_MASK"][:] == 1)):
        raise ValueError("V3 RFI confirmation crossed a joint weather barrier")
    paths_confirmed = np.isin(path, [2, 3, 4, 5])
    if not np.array_equal(confirmed, paths_confirmed):
        raise ValueError("V3 confirmed paths disagree with RFI risk")
    votes, count = group["TEMPORAL_RFI_VOTE_COUNT"][:], group["TEMPORAL_RFI_SAMPLE_COUNT"][:]
    fraction = group["TEMPORAL_CANDIDATE_PERSISTENCE"][:]
    if np.any(votes > count) or np.any(
        np.abs(votes[count > 0] - fraction[count > 0] * count[count > 0]) > 1e-5
    ):
        raise ValueError("V3 temporal diagnostic does not encode exact votes")

    curvature = group["RFI_PHASE_CURVATURE_DEG"][:]
    pair = group["RFI_PHASE_PAIR_AVAILABLE_MASK"][:] == 1
    if np.any(~np.isfinite(curvature[pair])) or np.any(~np.isnan(curvature[~pair])):
        raise ValueError("V3 phase curvature violates measured stencil availability")
    for label, mask, available in (
        ("phase", "RFI_PHASE_ANOMALY_MASK", "RFI_PHASE_LOCAL_AVAILABLE_MASK"),
        ("ZDR", "RFI_ZDR_ANOMALY_MASK", "RFI_ZDR_LOCAL_AVAILABLE_MASK"),
    ):
        if np.any((group[mask][:] == 1) & (group[available][:] == 0)):
            raise ValueError(f"V3 {label} anomaly has no measured local support")
    if np.any((group["RFI_V3_JOINT_CONFIRMED_MASK"][:] == 1) & ~confirmed):
        raise ValueError("V3 joint confirmation disagrees with final RFI state")

    if not np.array_equal(path == V3Path.QUARANTINED, group["RFI_QUARANTINE_MASK"][:] == 1):
        raise ValueError("V3 quarantine path disagrees with final action")
    other = path == V3Path.OTHER_CAUSE_REJECTED
    if np.any(other & ((group["QC_ACTION"][:] != 2) | confirmed)):
        raise ValueError("V3 other-cause path disagrees with final action")
