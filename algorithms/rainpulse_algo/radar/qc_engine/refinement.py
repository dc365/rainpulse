"""V3 type-specific RFI decision. Fixed evidence; bounded confirmation, no cleanup loop."""

from __future__ import annotations

from enum import IntEnum, IntFlag

import numpy as np

from .objects_v3 import bounded_search


class V3Path(IntEnum):
    OUTSIDE_SEARCH = 0
    STRUCTURE_ONLY = 1
    CORE_LOW_RHO = 2
    CORE_JOINT_POL = 3
    TEMPORAL_CORROBORATED = 4
    BOUNDED_PERIPHERY = 5
    QUARANTINED = 6
    WEATHER_BARRIER = 7
    OTHER_CAUSE_REJECTED = 8


class V3Blocker(IntFlag):
    UNOBSERVED = 1
    NO_STRUCTURAL_OBJECT = 2
    INSUFFICIENT_RAW_POL = 4
    SNR_UNAVAILABLE = 8
    LOW_SNR = 16
    PHASE_STENCIL_UNAVAILABLE = 32
    NO_JOINT_PHASE_ZDR = 64
    WEATHER_BARRIER = 128
    NO_STRONG_LOCAL_MEASUREMENT = 256
    OUTSIDE_STRUCTURAL_SEED = 512
    INSUFFICIENT_TEMPORAL_VOTES = 1024
    NOT_REACHED_FROM_CORE = 2048
    QUARANTINED_NOT_CONFIRMED = 4096


def temporal_votes(shape, persistence, samples, profile):
    """Exact integer vote decision; no global 0.67 rounding and no invented samples."""
    count = np.zeros(shape, "uint8")
    fraction = np.full(shape, np.nan, "float32")
    if samples is not None:
        value = np.asarray(samples)
        if (
            value.shape != shape
            or not np.isfinite(value).all()
            or np.any(value < 0)
            or np.any(value > profile.context.max_temporal_scans)
            or np.any(value != np.floor(value))
        ):
            raise ValueError("invalid per-gate V3 temporal sample count")
        count = value.astype("uint8")
    if persistence is not None:
        value = np.asarray(persistence, dtype="float32")
        if value.shape != shape or np.isinf(value).any() or np.any(value < 0) or np.any(value > 1):
            raise ValueError("invalid V3 temporal persistence")
        fraction = np.where(count > 0, value, np.nan).astype("float32")
    if np.any((count > 0) & ~np.isfinite(fraction)):
        raise ValueError("positive temporal count requires a finite vote fraction")
    raw = np.where(count > 0, fraction, 0) * count
    votes = np.rint(raw).astype("uint8")
    if np.any(np.abs(raw - votes) > 1e-5):
        raise ValueError("temporal fraction does not represent an integer number of votes")
    cfg = profile.rfi_refinement
    supported = count >= profile.rfi_objects.temporal_minimum_samples
    supported &= votes >= cfg.temporal_minimum_votes
    supported &= votes.astype(int) * cfg.temporal_vote_denominator >= (
        count.astype(int) * cfg.temporal_vote_numerator
    )
    if not profile.context.enabled:
        supported[:] = False
    return count, fraction, votes, supported


def refine_rfi(native, objects, profile, weather, persistence=None, samples=None):
    cfg, obj = profile.rfi_refinement, profile.rfi_objects
    data = objects.arrays
    shape = native.shape
    observed = native.field_available["DBZH"]
    search = objects.candidate
    if search.shape != shape or weather.shape != shape:
        raise ValueError("V3 refinement geometry mismatch")
    count, fraction, votes, time_support = temporal_votes(shape, persistence, samples, profile)
    absent = np.full(shape, np.nan)
    false = np.zeros(shape, bool)
    rho = native.fields.get("RHOHV", absent)
    rho_ok = native.field_available.get("RHOHV", false)
    raw_count = sum(
        native.field_available.get(k, false).astype("uint8") for k in ("RHOHV", "ZDR", "PHIDP")
    )
    reliable = (raw_count >= obj.minimum_raw_pol_moments) & (data["RFI_SNR_RELIABLE_MASK"] == 1)
    structural = data["RFI_STRUCTURAL_SEED_MASK"] == 1
    strong = data["RFI_POLARIMETRIC_STRONG_MASK"] == 1
    anomalous = data["RFI_POLARIMETRIC_ANOMALY_MASK"] == 1
    phase = data["RFI_PHASE_ANOMALY_MASK"] == 1
    zdr = data["RFI_ZDR_ANOMALY_MASK"] == 1
    joint = phase & zdr
    barrier = data["RFI_WEATHER_BARRIER_MASK"] == 1
    severe = rho_ok & (rho < obj.severe_rhohv)
    moderate = rho_ok & (rho < obj.suspect_rhohv)
    core = structural & reliable & strong & ~barrier
    # Repeated structural hypotheses are auxiliary, not independent physical truth.
    # Time must additionally have a moderate measured rho anomaly AND phase/ZDR corroboration.
    temporal = structural & reliable & moderate & (phase | zdr) & time_support
    temporal &= ~core & ~weather & ~barrier
    confirmed = core | temporal
    allowed = search & reliable & ~barrier
    allowed &= strong | (rho_ok & (rho < obj.residual_maximum_rhohv) & ~weather)
    source_ids = np.where(confirmed, data["RFI_OBJECT_ID"], 0).astype("uint32")
    reached = bounded_search(
        native,
        source_ids,
        allowed,
        cfg.confirm_range_m,
        cfg.confirm_azimuth_deg,
        cfg.maximum_search_steps,
        domain_ids=data["RFI_OBJECT_ID"],
    )
    # A core may never claim a neighbouring object's measurements.
    residual = (reached > 0) & (reached == data["RFI_OBJECT_ID"]) & ~confirmed & allowed
    confirmed |= residual
    quarantine = search & anomalous & ~confirmed & ~barrier
    # No RHOHV or SNR is fabricated. Structure alone never becomes a hard cause.
    quarantine |= search & ~rho_ok & time_support & (phase | zdr) & ~confirmed & ~barrier
    path = np.zeros(shape, "uint8")
    path[search] = V3Path.STRUCTURE_ONLY
    path[search & barrier] = V3Path.WEATHER_BARRIER
    path[quarantine] = V3Path.QUARANTINED
    path[core & severe] = V3Path.CORE_LOW_RHO
    path[core & joint] = V3Path.CORE_JOINT_POL
    path[temporal] = V3Path.TEMPORAL_CORROBORATED
    path[residual] = V3Path.BOUNDED_PERIPHERY
    blockers = np.zeros(shape, "uint16")
    snr_available = native.field_available.get("SNR", false)
    for mask, code in (
        (~observed, V3Blocker.UNOBSERVED),
        (observed & ~search, V3Blocker.NO_STRUCTURAL_OBJECT),
        (observed & (raw_count < obj.minimum_raw_pol_moments), V3Blocker.INSUFFICIENT_RAW_POL),
        (observed & ~snr_available, V3Blocker.SNR_UNAVAILABLE),
        (observed & snr_available & (data["RFI_SNR_RELIABLE_MASK"] == 0), V3Blocker.LOW_SNR),
        (
            observed & (data["RFI_PHASE_LOCAL_AVAILABLE_MASK"] == 0),
            V3Blocker.PHASE_STENCIL_UNAVAILABLE,
        ),
        (observed & ~joint, V3Blocker.NO_JOINT_PHASE_ZDR),
        (observed & barrier, V3Blocker.WEATHER_BARRIER),
        (observed & ~strong, V3Blocker.NO_STRONG_LOCAL_MEASUREMENT),
        (search & ~structural, V3Blocker.OUTSIDE_STRUCTURAL_SEED),
        (observed & ~time_support, V3Blocker.INSUFFICIENT_TEMPORAL_VOTES),
        (search & ~confirmed, V3Blocker.NOT_REACHED_FROM_CORE),
        (quarantine, V3Blocker.QUARANTINED_NOT_CONFIRMED),
    ):
        blockers[mask] |= np.uint16(code)
    return {
        "confirmed": confirmed,
        "quarantine": quarantine,
        "residual": residual,
        "temporal": temporal,
        "count": count,
        "persistence": fraction,
        "arrays": {
            "RFI_V3_DECISION_PATH": path,
            "RFI_V3_BLOCKER_BITS": blockers,
            "RFI_V3_JOINT_CONFIRMED_MASK": (core & joint).astype("uint8"),
            "TEMPORAL_RFI_VOTE_COUNT": votes,
        },
    }
