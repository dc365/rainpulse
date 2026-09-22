"""Bounded two-snapshot temporal recurrence for near-radar CR audit.

This first version intentionally does not change measurement qualification.  It
reports connected native objects whose weak low-RHOHV evidence recurs at the
same physical gate in two prior scans from the same radar and elevation.
"""
from __future__ import annotations

from dataclasses import dataclass

import numpy as np
from scipy import ndimage

from .sampling import polar_targets

NEAR_TEMPORAL_OBJECT_POLICY = {
    "version": "near-temporal-object-20260922-v1",
    "mode": "audit",
    "minimum_range_m": 2_000.0,
    "maximum_range_m": 75_000.0,
    "minimum_dbz": -10.0,
    "prior_minimum_dbz": -15.0,
    "maximum_dbz": 30.0,
    "maximum_rhohv": 0.97,
    "minimum_snr_db": 8.0,
    "minimum_object_gates": 5,
    "maximum_object_gates": 5_000,
    "minimum_support_gates": 3,
    "minimum_recurrence_fraction": 0.5,
    "maximum_objects": 20_000,
    "maximum_sweep_gates": 5_000_000,
    "prior_snapshots": 2,
}


@dataclass(frozen=True)
class NearTemporalObjectEvidence:
    arrays: dict
    summary: dict


def _binary(a: dict, key: str, shape: tuple[int, int], *, default: bool = False) -> np.ndarray:
    if key not in a:
        return np.full(shape, default, dtype=bool)
    value = np.asarray(a[key])
    if value.shape != shape or not np.isin(value, (0, 1)).all():
        raise ValueError(f"invalid near-temporal binary field: {key}")
    return value.astype(bool, copy=False)


def _labels_with_wrap(mask: np.ndarray, azimuth: np.ndarray) -> tuple[np.ndarray, int]:
    """Label connected objects in azimuth-sorted order with a real wrap edge."""
    az = np.asarray(azimuth, float) % 360.0
    order = np.argsort(az, kind="stable")
    sorted_mask = mask[order]
    gaps = np.diff(az[order])
    positive = gaps[gaps > 0.01]
    spacing = float(np.median(positive)) if positive.size else 360.0
    wrap_gap = (az[order][0] - az[order][-1]) % 360.0
    periodic = bool(positive.size and wrap_gap <= max(1.8 * spacing, spacing + 0.02))
    padded = np.vstack((
        (sorted_mask[-1] & periodic)[None, :],
        sorted_mask,
        (sorted_mask[0] & periodic)[None, :],
    ))
    labels = ndimage.label(padded, structure=np.ones((3, 3), dtype=np.uint8))[0][1:-1]
    result = np.zeros(mask.shape, dtype=np.int32)
    result[order] = labels
    return result, int(labels.max())


def _prior_recurrence(current: dict, past: dict, shape: tuple[int, int]) -> np.ndarray:
    for key in ("azimuth", "range", "RHOHV_RAW", "SNR_RAW", "DBZH_RAW"):
        if key not in past:
            return np.zeros(shape, dtype=bool)
    ranges = np.asarray(current["range"], float)
    azimuth = np.asarray(current["azimuth"], float)
    past_rho = np.asarray(past["RHOHV_RAW"], float)
    past_snr = np.asarray(past["SNR_RAW"], float)
    past_dbz = np.asarray(past["DBZH_RAW"], float)
    if (past_rho.shape != shape or past_snr.shape != shape or past_dbz.shape != shape):
        raise ValueError("near-temporal prior moment geometry differs")
    ray, gate, footprint = polar_targets(
        np.asarray(past["azimuth"], float),
        np.asarray(past["range"], float),
        np.broadcast_to(ranges[None, :], shape),
        np.broadcast_to(azimuth[:, None], shape),
    )
    rho = past_rho[ray, gate]
    snr = past_snr[ray, gate]
    dbz = past_dbz[ray, gate]
    selected = footprint & np.isfinite(rho) & np.isfinite(snr) & np.isfinite(dbz)
    selected &= rho <= NEAR_TEMPORAL_OBJECT_POLICY["maximum_rhohv"]
    selected &= snr >= NEAR_TEMPORAL_OBJECT_POLICY["minimum_snr_db"]
    selected &= dbz >= NEAR_TEMPORAL_OBJECT_POLICY["prior_minimum_dbz"]
    selected &= dbz <= NEAR_TEMPORAL_OBJECT_POLICY["maximum_dbz"]
    result = np.zeros(shape, dtype=bool)
    result[ray[selected], gate[selected]] = True
    return result


def evaluate_near_temporal_object(
    current: dict, past: tuple[dict, ...], *, sweep: str,
) -> NearTemporalObjectEvidence:
    """Evaluate one native sweep without mutating any input arrays."""
    if len(past) != NEAR_TEMPORAL_OBJECT_POLICY["prior_snapshots"]:
        raise ValueError("near-temporal audit requires exactly two prior sweeps")
    shape = np.asarray(current["DBZH_RAW"]).shape
    gates = int(np.prod(shape))
    if gates > NEAR_TEMPORAL_OBJECT_POLICY["maximum_sweep_gates"]:
        raise ValueError("near-temporal sweep gate budget exceeded")

    range_mask = (
        np.asarray(current["range"], float)[None, :]
        >= NEAR_TEMPORAL_OBJECT_POLICY["minimum_range_m"]
    ) & (
        np.asarray(current["range"], float)[None, :]
        <= NEAR_TEMPORAL_OBJECT_POLICY["maximum_range_m"]
    )
    observed = _binary(current, "VALID_MASK", shape)
    eligible = _binary(current, "REFLECTIVITY_ELIGIBLE_FOR_CR", shape)
    z = np.asarray(current["DBZH_RAW"], float)
    rho = np.asarray(current["RHOHV_RAW"], float)
    snr = np.asarray(current["SNR_RAW"], float)
    barriers = (
        _binary(current, "CF_HARD_WEATHER_MASK", shape)
        | _binary(current, "CF_LOCAL_WEATHER_MASK", shape)
        | _binary(current, "CF_LEGACY_PROTECTED_MASK", shape)
        | _binary(current, "CF_WEATHER_PROXY_MASK", shape)
    )
    mixed = _binary(current, "CF_MIXED_MASK", shape)
    enhancement = _binary(current, "CF_BG_ENHANCEMENT_MASK", shape)
    barriers |= mixed & ~enhancement
    domain = (
        observed & eligible & range_mask & np.isfinite(z) & np.isfinite(rho) & np.isfinite(snr)
        & (z >= NEAR_TEMPORAL_OBJECT_POLICY["minimum_dbz"])
        & (z <= NEAR_TEMPORAL_OBJECT_POLICY["maximum_dbz"])
        & (rho <= NEAR_TEMPORAL_OBJECT_POLICY["maximum_rhohv"])
        & (snr >= NEAR_TEMPORAL_OBJECT_POLICY["minimum_snr_db"])
        & ~barriers
    )

    recurrence = tuple(_prior_recurrence(current, item, shape) for item in past)
    first = domain & recurrence[0]
    second = domain & recurrence[1]
    support = first & second
    labels, count = _labels_with_wrap(domain, np.asarray(current["azimuth"], float))
    if count > NEAR_TEMPORAL_OBJECT_POLICY["maximum_objects"]:
        raise ValueError("near-temporal object budget exceeded")

    object_mask = np.zeros(shape, dtype=bool)
    object_id = np.zeros(shape, dtype=np.int32)
    object_size = np.zeros(shape, dtype=np.uint32)
    object_fraction = np.full(shape, np.nan, dtype=np.float32)
    accepted_objects = 0
    present_labels = np.unique(labels)
    present_labels = present_labels[present_labels > 0]
    count = int(len(present_labels))
    for label_id in present_labels.tolist():
        component = labels == label_id
        size = int(component.sum())
        fraction = min(
            float(recurrence[0][component].mean()),
            float(recurrence[1][component].mean()),
        )
        support_count = int(support[component].sum())
        accepted = (
            NEAR_TEMPORAL_OBJECT_POLICY["minimum_object_gates"] <= size
            <= NEAR_TEMPORAL_OBJECT_POLICY["maximum_object_gates"]
            and support_count >= NEAR_TEMPORAL_OBJECT_POLICY["minimum_support_gates"]
            and fraction >= NEAR_TEMPORAL_OBJECT_POLICY["minimum_recurrence_fraction"]
        )
        object_id[component] = label_id
        object_size[component] = size
        object_fraction[component] = fraction
        if accepted:
            accepted_objects += 1
            object_mask |= component

    arrays = {
        "NTO_CANDIDATE_MASK": domain.astype("uint8"),
        "NTO_PRIOR1_MASK": first.astype("uint8"),
        "NTO_PRIOR2_MASK": second.astype("uint8"),
        "NTO_SUPPORT_MASK": support.astype("uint8"),
        "NTO_OBJECT_MASK": object_mask.astype("uint8"),
        "NTO_OBJECT_ID": object_id,
        "NTO_OBJECT_SIZE": object_size,
        "NTO_OBJECT_FRACTION": object_fraction,
    }
    summary = {
        "status": "EVALUATED",
        "mode": NEAR_TEMPORAL_OBJECT_POLICY["mode"],
        "sweep": sweep,
        "policy": NEAR_TEMPORAL_OBJECT_POLICY,
        "candidate_gates": int(domain.sum()),
        "prior1_gates": int(first.sum()),
        "prior2_gates": int(second.sum()),
        "support_gates": int(support.sum()),
        "object_gates": int(object_mask.sum()),
        "object_count": accepted_objects,
        "component_count": count,
        "action_gates": 0,
        "changes_measurement_qualification": False,
    }
    return NearTemporalObjectEvidence(arrays, summary)
