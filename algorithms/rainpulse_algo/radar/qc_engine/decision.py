"""Type-specific decisions. Weather support cannot resurrect a polluted measurement."""

from __future__ import annotations

from dataclasses import dataclass
from enum import IntEnum, IntFlag

import numpy as np

from .adapters import NativeSweep
from .algorithms import EvidenceSet
from .profile import OpenSourceQCProfile


class Action(IntEnum):
    KEEP = 0
    DOWNWEIGHT = 1
    REJECT = 2
    MISSING = 3


class Reason(IntFlag):
    NONE = 0
    INVALID_OBSERVATION = 1
    INDEPENDENT_NONMET_EVIDENCE = 2
    RADIAL_AND_POLARIMETRIC = 4
    STATIC_CLUTTER_AND_POLARIMETRIC = 8
    WEAK_OBJECT_AND_NOISE = 16
    WEATHER_SUPPORT_CONFLICT = 32
    WEATHER_PROTECTED_WEAK_CANDIDATE = 64
    SINGLE_FAMILY_CANDIDATE = 128
    INCOMPLETE_CAPABILITY = 256
    LOW_SNR = 512


@dataclass(frozen=True)
class Decision:
    arrays: dict[str, np.ndarray]
    flags: np.ndarray
    quality: np.ndarray


def decide(
    native: NativeSweep,
    evidence: EvidenceSet,
    profile: OpenSourceQCProfile,
    *,
    weather_support: np.ndarray | None = None,
    rfi_candidate: np.ndarray | None = None,
    clutter_prior: np.ndarray | None = None,
) -> Decision:
    shape = native.shape
    data = evidence.arrays
    observed = native.field_available["DBZH"]
    dbzh = native.fields["DBZH"]
    rain = observed & (dbzh >= profile.echo.no_rain_below_dbz)
    if weather_support is None:
        weather_support = np.full(shape, np.nan, dtype="float32")
    if weather_support.shape != shape:
        raise ValueError("weather support is not co-registered")
    weather = np.isfinite(weather_support) & (weather_support >= profile.context.strong_support)
    structure = (data["OS_GABELLA_CANDIDATE_MASK"] == 1) | (
        data["OS_DBZH_TEXTURE_CANDIDATE_MASK"] == 1
    )
    low_meteo = (data["METEO_SCORE_AVAILABLE_MASK"] == 1) & (
        data["METEO_SCORE"] <= profile.wradlib.low_meteo_score
    )
    rho = native.fields.get("RHOHV", np.full(shape, np.nan))
    rho_available = native.field_available.get("RHOHV", np.zeros(shape, bool))
    severe_rho = rho_available & (rho < profile.rfi.severe_rhohv)
    snr = native.fields.get("SNR", np.full(shape, np.nan))
    low_snr = (
        native.field_available.get("SNR", np.zeros(shape, bool))
        & (snr < profile.echo.low_snr_db)
        & rain
    )
    small = data["OS_SMALL_OBJECT_CANDIDATE_MASK"] == 1
    # Multiple functions using Z alone count as ONE evidence family.
    generic_reject = structure & low_meteo & ~weather
    radial = np.zeros(shape, bool) if rfi_candidate is None else np.asarray(rfi_candidate, bool)
    if radial.shape != shape:
        raise ValueError("radial candidate geometry mismatch")
    # No global P_METEO threshold is required for this independent anomaly type.
    radial_reject = radial & severe_rho & (data["OS_POL_MOMENT_COUNT"] >= 2)
    ground_reject = np.zeros(shape, bool)
    if clutter_prior is not None:
        if clutter_prior.shape != shape:
            raise ValueError("clutter prior geometry mismatch")
        ground_reject = (
            np.isfinite(clutter_prior)
            & (clutter_prior >= profile.static_ground_clutter.flag_probability)
            & low_meteo
            & ~weather
        )
    weak_noise = small & low_snr & severe_rho & (dbzh < profile.echo.strong_echo_dbz) & ~weather
    reject = rain & (generic_reject | radial_reject | ground_reject | weak_noise)
    uncertain = rain & (structure | low_meteo | radial | severe_rho | small) & ~reject
    capability_incomplete = rain & (data["METEO_SCORE_AVAILABLE_MASK"] == 0)
    action = np.full(shape, Action.KEEP, dtype="uint8")
    action[uncertain | low_snr | capability_incomplete] = Action.DOWNWEIGHT
    action[reject] = Action.REJECT
    action[~observed] = Action.MISSING
    reason = np.zeros(shape, dtype="uint16")
    for mask, code in (
        (~observed, Reason.INVALID_OBSERVATION),
        (generic_reject & rain, Reason.INDEPENDENT_NONMET_EVIDENCE),
        (radial_reject & rain, Reason.RADIAL_AND_POLARIMETRIC),
        (ground_reject & rain, Reason.STATIC_CLUTTER_AND_POLARIMETRIC),
        (weak_noise & rain, Reason.WEAK_OBJECT_AND_NOISE),
        (reject & weather, Reason.WEATHER_SUPPORT_CONFLICT),
        (uncertain & weather, Reason.WEATHER_PROTECTED_WEAK_CANDIDATE),
        (uncertain, Reason.SINGLE_FAMILY_CANDIDATE),
        (capability_incomplete, Reason.INCOMPLETE_CAPABILITY),
        (low_snr, Reason.LOW_SNR),
    ):
        reason[mask] |= np.uint16(code)
    quality = np.ones(shape, dtype="float32")
    quality[capability_incomplete] = profile.quality_index.incomplete_capability_quality
    quality[uncertain] = np.minimum(quality[uncertain], profile.quality_index.suspect_quality)
    quality[low_snr] = np.minimum(quality[low_snr], profile.quality_index.low_snr_quality)
    quality[reject] = 0
    quality[~observed] = np.nan
    flags = np.zeros(shape, dtype="uint32")
    definitions = profile.flag_masks
    if not definitions:
        raise ValueError("QC profile must be loaded with the frozen flag definitions")
    for mask, name in (
        (~observed, "MISSING"),
        (reject, "NON_METEOROLOGICAL"),
        (radial_reject & rain, "RADIAL_INTERFERENCE"),
        (ground_reject & rain, "GROUND_CLUTTER"),
        (low_snr, "LOW_SNR"),
    ):
        flags[mask] |= definitions[name]
    flags[observed & (quality < profile.quality_index.low_quality_threshold)] |= definitions[
        "LOW_QUALITY"
    ]
    trusted = observed & ~reject
    eligible = trusted & (quality >= profile.quality_index.quantitative_minimum)
    arrays = {
        "QC_ACTION": action,
        "QC_DECISION_REASON": reason,
        "REFLECTIVITY_TRUST_MASK": trusted.astype("uint8"),
        # This is QC eligibility only; Hybrid still checks height, blockage and absolute QI.
        "QPE_ELIGIBLE_MASK": eligible.astype("uint8"),
        "WEATHER_SUPPORT_SCORE": np.asarray(weather_support, dtype="float32"),
        "WEATHER_SUPPORTED_MASK": weather.astype("uint8"),
        "RFI_CANDIDATE_MASK": radial.astype("uint8"),
        "DBZH_USABLE": np.where(eligible, dbzh, np.nan).astype("float32"),
    }
    for field in ("RHOHV", "ZDR", "PHIDP", "VR", "SW", "SNR"):
        field_valid = native.field_available.get(field, np.zeros(shape, bool)) & trusted
        if field in ("RHOHV", "ZDR", "PHIDP"):
            field_valid &= data[f"OS_{field}_TEXTURE_CANDIDATE_MASK"] == 0
        arrays[f"{field}_TRUST_MASK"] = field_valid.astype("uint8")
    return Decision(arrays, flags, quality)
