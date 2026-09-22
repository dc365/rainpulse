"""Object-level near-radar CR admission.

Proximity and weakness alone do not identify non-meteorological echo.  This
module aggregates native polar evidence over connected objects so incomplete
moments on individual gates can be supported by neighbouring measurements.
"""
from __future__ import annotations

import numpy as np
from scipy import ndimage

NEAR_OBJECT_POLICY_VERSION = "near-object-cr-20260922-v1"
NEAR_OBJECT_POLICY = {
    "version": NEAR_OBJECT_POLICY_VERSION,
    "action": "cr_withhold_before_maximum",
    "minimum_range_m": 2_000.0,
    "maximum_range_m": 75_000.0,
    "maximum_dbz": 30.0,
    "maximum_nonmet_rhohv": 0.85,
    "minimum_polarization_snr_db": 8.0,
    "minimum_object_gates": 8,
    "minimum_polarization_samples": 4,
    "minimum_background_samples": 3,
    "minimum_neighborhood_nonmet_fraction": 0.5,
    "minimum_neighborhood_nonmet_samples": 3,
    "minimum_uncertainty_samples": 3,
    "strong_polarization_samples": 12,
    "minimum_evidence_families": 2,
    "maximum_background_departure_db": 6.0,
    "maximum_weather_rhohv": 0.97,
    "minimum_weather_snr_db": 12.0,
    "growth_rays": 2,
    "growth_gates": 2,
}


def _mask(a: dict, key: str, shape: tuple[int, int]) -> np.ndarray:
    if key not in a:
        return np.zeros(shape, dtype=bool)
    value = np.asarray(a[key])
    if value.shape != shape or not np.isin(value, (0, 1)).all():
        raise ValueError(f"invalid near-object binary field: {key}")
    return value.astype(bool, copy=False)


def _labels_with_azimuth_wrap(mask: np.ndarray, azimuth: np.ndarray) -> np.ndarray:
    """Label connected objects without inventing adjacency across declared gaps."""
    az = np.asarray(azimuth, float) % 360.0
    order = np.argsort(az, kind="stable")
    sorted_mask = mask[order]
    gaps = np.diff(az[order])
    positive = gaps[gaps > 0.01]
    spacing = float(np.median(positive)) if len(positive) else 360.0
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
    return result


def evaluate_near_object(a: dict, *, near_active: bool) -> np.ndarray:
    """Return native gates whose CR admission is withheld by object evidence."""
    shape = np.asarray(a["DBZH_QC"]).shape
    if not near_active:
        return np.zeros(shape, dtype=bool)

    value = np.asarray(a["DBZH_QC"], float)
    ranges = np.asarray(a["range"], float)
    observed = _mask(a, "VALID_MASK", shape)
    eligible = _mask(a, "REFLECTIVITY_ELIGIBLE_FOR_CR", shape)
    range_mask = (ranges[None, :] >= NEAR_OBJECT_POLICY["minimum_range_m"]) & (
        ranges[None, :] <= NEAR_OBJECT_POLICY["maximum_range_m"]
    )
    domain = observed & eligible & range_mask & np.isfinite(value) & (
        value < NEAR_OBJECT_POLICY["maximum_dbz"]
    )

    rho = np.asarray(a.get("RHOHV_RAW", np.full(shape, np.nan)), float)
    snr = np.asarray(a.get("SNR_RAW", np.full(shape, np.nan)), float)
    rho_available = np.isfinite(rho) & (rho >= 0.0) & (rho <= 1.0)
    snr_available = np.isfinite(snr)
    polar_reliable = rho_available & snr_available & (
        snr >= NEAR_OBJECT_POLICY["minimum_polarization_snr_db"]
    )
    weather_proxy = rho_available & snr_available & (
        rho >= NEAR_OBJECT_POLICY["maximum_weather_rhohv"]
    ) & (snr >= NEAR_OBJECT_POLICY["minimum_weather_snr_db"])
    protected = (
        _mask(a, "NMR_PROTECTED_MASK", shape)
        | _mask(a, "CF_HARD_WEATHER_MASK", shape)
        | _mask(a, "CF_LOCAL_WEATHER_MASK", shape)
        | weather_proxy
    )
    admissible = domain & ~protected

    low_rho = admissible & polar_reliable & (
        rho <= NEAR_OBJECT_POLICY["maximum_nonmet_rhohv"]
    )
    background_mask = _mask(a, "CF_BG_MATCH_MASK", shape)
    stable = _mask(a, "CF_BG_STABLE_MASK", shape)
    departure = np.asarray(a.get("CF_BG_DBZH_DEPARTURE_DB", np.full(shape, np.nan)), float)
    stable_compatible = stable & np.isfinite(departure) & (
        np.abs(departure) <= NEAR_OBJECT_POLICY["maximum_background_departure_db"]
    )
    background = admissible & (background_mask | stable_compatible)
    low_snr = admissible & _mask(a, "NMR_LOW_SNR_UNCERTAIN_MASK", shape)
    nmr_nonmet = admissible & _mask(a, "NMR_NONMET_CANDIDATE_MASK", shape)
    neighborhood_fraction = np.asarray(
        a.get("NMR_NONMET_FRACTION", np.full(shape, np.nan)), float
    )
    neighborhood_samples = np.asarray(
        a.get("NMR_POL_SAMPLE_COUNT", np.zeros(shape, dtype=np.uint16)), float
    )
    polar_neighborhood = admissible & np.isfinite(neighborhood_fraction) & (
        neighborhood_fraction >= NEAR_OBJECT_POLICY["minimum_neighborhood_nonmet_fraction"]
    ) & (
        neighborhood_samples >= NEAR_OBJECT_POLICY["minimum_neighborhood_nonmet_samples"]
    )

    seed = low_rho | background | low_snr | nmr_nonmet | polar_neighborhood
    labels = _labels_with_azimuth_wrap(seed, np.asarray(a["azimuth"], float))
    count = int(labels.max())
    if count == 0:
        return np.zeros(shape, dtype=bool)

    object_ids = np.arange(1, count + 1)
    total = ndimage.sum(seed, labels, object_ids)
    polarization_count = ndimage.sum(low_rho, labels, object_ids)
    background_count = ndimage.sum(background, labels, object_ids)
    uncertainty_count = ndimage.sum(low_snr, labels, object_ids)
    neighborhood_count = ndimage.sum(polar_neighborhood, labels, object_ids)
    nmr_count = ndimage.sum(nmr_nonmet, labels, object_ids)

    families = (
        polarization_count >= NEAR_OBJECT_POLICY["minimum_polarization_samples"],
        background_count >= NEAR_OBJECT_POLICY["minimum_background_samples"],
        uncertainty_count >= NEAR_OBJECT_POLICY["minimum_uncertainty_samples"],
        nmr_count >= NEAR_OBJECT_POLICY["minimum_uncertainty_samples"],
        neighborhood_count >= NEAR_OBJECT_POLICY["minimum_neighborhood_nonmet_samples"],
    )
    family_count = np.sum(families, axis=0).astype(int)
    fraction = np.divide(
        polarization_count,
        total,
        out=np.zeros_like(total, dtype=float),
        where=total > 0,
    )
    strong_polarization = (
        polarization_count >= NEAR_OBJECT_POLICY["strong_polarization_samples"]
    ) & (fraction >= 0.5)
    accepted_object = (
        (total >= NEAR_OBJECT_POLICY["minimum_object_gates"])
        & ((family_count >= NEAR_OBJECT_POLICY["minimum_evidence_families"]) | strong_polarization)
    )
    accepted = np.isin(labels, object_ids[accepted_object])

    # Grow only around accepted seeds, through gates still eligible for CR and
    # without a weather proxy.  This fills missing-moment neighbours without
    # turning the whole near-radar domain into a spatial hole.
    structure = np.ones(
        (
            2 * NEAR_OBJECT_POLICY["growth_rays"] + 1,
            2 * NEAR_OBJECT_POLICY["growth_gates"] + 1,
        ),
        dtype=np.uint8,
    )
    growth = ndimage.binary_dilation(
        accepted,
        structure=structure,
        iterations=1,
    )
    return admissible & growth
