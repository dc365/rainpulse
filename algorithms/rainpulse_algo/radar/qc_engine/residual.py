"""V6 supplementary decisions: frozen V5 + raw-supported residual review."""

from __future__ import annotations

from enum import IntFlag

import numpy as np

from .crossradar import measurement_capability, pol_corroboration
from .decision import Action, Decision
from .narrow_local import repaired_narrow_candidates
from .narrow_spike import narrow_candidates
from .range_signature import range_signatures
from .residual_association import peripheral_review
from .speckle_review import speckle_candidates


class ResidualReason(IntFlag):
    RANGE_INLIER_ASSOCIATION = 1
    RANGE_LINKED_REVIEW = 2
    NARROW_OBJECT = 4
    RAW_SHOULDER_MODEL = 8
    ORIGINAL_PARENT_REVIEW = 16
    SPECKLE_RAW_NOISE = 32
    POL_CORROBORATION = 64
    WEATHER_PROTECTED = 128
    CONFIRMED_ADDITION = 256
    QUARANTINED_ADDITION = 512
    UNVERIFIED_RANGE_PLATFORM = 1024
    CANDIDATE_ONLY = 2048


def residual_decision(native, baseline, profile, *, weather_support=None):
    cfg = profile.residual
    repair = getattr(profile, "residual_repair", None)
    observed = native.field_available["DBZH"] & native.geometry_good[:, None]
    shape = native.shape
    empty = np.zeros(shape, bool)
    code, _, snr_available, reliable = measurement_capability(native, cfg)
    pol_bad, phase_bad, zdr_bad = pol_corroboration(native, profile)
    pol_bad &= reliable & observed
    rho = native.fields.get("RHOHV", np.full(shape, np.nan))
    rho_ok = native.field_available.get("RHOHV", empty)
    low_rho = rho_ok & (rho < cfg.suspect_rhohv) & reliable
    weather = empty.copy()
    if weather_support is not None:
        if np.shape(weather_support) != shape:
            raise ValueError("V6 weather geometry differs")
        weather = np.isfinite(weather_support) & (weather_support >= profile.context.strong_support)
    # Explicit geometry-vetted weather support protects additional ambiguous work.
    # High RHOHV alone is not used as a universal immunity rule.
    protected = weather & ~pol_bad & observed
    raw_noecho = observed & (native.fields["DBZH"] < cfg.minimum_echo_dbz)
    blocked = protected | raw_noecho
    original = baseline.arrays
    old_reject = original["QC_ACTION"] == Action.REJECT
    old_quarantine = original["RFI_QUARANTINE_MASK"] == 1
    old_eligible = original["QPE_ELIGIBLE_MASK"] == 1
    arrays = {k: v.copy() for k, v in original.items()}
    summary = {"method": cfg.method, "parameters": cfg.model_dump(mode="json"), "modules": {}}
    range_mask = empty.copy()
    range_modes = np.zeros(shape, "uint8")
    linked = empty.copy()
    if cfg.repair_range_links:
        repaired = range_signatures(
            native, profile.cross_radar, association=cfg, protected=protected
        )
        for key, value in repaired.arrays.items():
            arrays[key.replace("V5_", "V6_", 1)] = value.copy()
        range_mask = repaired.arrays["V5_RANGE_CANDIDATE_MASK"] == 1
        range_modes = repaired.arrays["V5_RANGE_MODEL_CODE"]
        linked = repaired.arrays["V6_RANGE_LINKED_REVIEW_MASK"] == 1
        summary["modules"]["range_association"] = repaired.summary
    else:
        summary["modules"]["range_association"] = {"status": "disabled"}
    parent = (original.get("RFI_OBJECT_ID", np.zeros(shape)) > 0) | range_mask
    narrow = empty.copy()
    narrow_model = empty.copy()
    if cfg.narrow_enabled:
        detect_narrow = (
            repaired_narrow_candidates
            if repair and repair.local_narrow_width
            else narrow_candidates
        )
        n = detect_narrow(
            native, cfg, polarimetric_risk=pol_bad | low_rho, protected=blocked, parent_mask=parent
        )
        arrays.update(n.arrays)
        summary["modules"]["narrow"] = n.summary
        narrow = n.arrays["V6_NARROW_CANDIDATE_MASK"] == 1
        narrow_model = n.arrays["V6_NARROW_MODEL_MASK"] == 1
    else:
        summary["modules"]["narrow"] = {"status": "disabled"}
    peripheral = empty.copy()
    model_peripheral = empty.copy()
    if cfg.association_enabled:
        # Do NOT use every quarantined/no-QPE gate as an RFI anchor.
        confirmed_anchor = old_reject & (
            (baseline.flags & profile.flag_masks["RADIAL_INTERFERENCE"]) != 0
        )
        model_anchor = original.get("V5_RANGE_CANDIDATE_MASK", np.zeros(shape)) == 1
        model_anchor &= original.get("V5_RANGE_MODEL_CODE", np.zeros(shape)) != 3
        p = peripheral_review(
            native,
            cfg,
            confirmed=confirmed_anchor,
            model_anchor=model_anchor,
            protected=blocked,
            footprint=bool(repair and repair.footprint_peripheral),
        )
        arrays.update(p)
        peripheral = p["V6_PERIPHERAL_COMPATIBLE_MASK"] == 1
        model_peripheral = peripheral & (p["V6_PERIPHERAL_MODEL_SOURCE_MASK"] == 1)
        summary["modules"]["peripheral"] = {
            "status": "applied",
            "passes": 1,
            "reviewed_gates": int(p["V6_PERIPHERAL_REVIEW_MASK"].sum()),
            "compatible_gates": int(peripheral.sum()),
        }
    else:
        summary["modules"]["peripheral"] = {"status": "disabled"}
    speckle = empty.copy()
    if cfg.speckle_enabled:
        low_snr = snr_available & (
            native.fields.get("SNR", np.full(shape, np.nan)) < profile.echo.low_snr_db
        )
        sp, rec = speckle_candidates(
            native,
            cfg,
            baseline_eligible=old_eligible,
            protected=protected,
            pol_bad=pol_bad,
            low_snr=low_snr,
        )
        arrays.update(sp)
        speckle = sp["V6_SPECKLE_CANDIDATE_MASK"] == 1
        summary["modules"]["speckle"] = rec
    else:
        summary["modules"]["speckle"] = {"status": "disabled"}
    candidate = (range_mask | linked | narrow | peripheral | speckle) & observed
    # Unknown numerical plateaus NEVER become newly confirmed by these extensions.
    unknown_platform = (range_modes == 3) | (
        original.get("V5_RANGE_MODEL_CODE", np.zeros(shape)) == 3
    )
    confirmed = (range_mask | narrow | peripheral) & pol_bad & ~unknown_platform
    uncertain = (
        range_mask
        | narrow_model
        | model_peripheral
        | ((linked | narrow | peripheral) & (pol_bad | low_rho))
        | speckle
    )
    uncertain &= ~protected & ~confirmed & observed
    confirmed &= observed
    add_confirm = confirmed & ~old_reject
    add_quarantine = uncertain & ~old_reject & ~old_quarantine
    reject = old_reject | confirmed
    quarantine = (old_quarantine | uncertain) & ~reject
    quality = baseline.quality.copy()
    flags = baseline.flags.copy()
    arrays["QC_ACTION"][reject] = Action.REJECT
    arrays["QC_ACTION"][quarantine] = Action.DOWNWEIGHT
    quality[reject] = 0
    quality[uncertain & ~old_reject] = np.minimum(
        quality[uncertain & ~old_reject], cfg.quarantine_quality
    )
    flags[confirmed] |= (
        profile.flag_masks["RADIAL_INTERFERENCE"] | profile.flag_masks["NON_METEOROLOGICAL"]
    )
    flags[confirmed | quarantine] |= profile.flag_masks["LOW_QUALITY"]
    trusted = observed & ~reject & ~quarantine
    eligible = old_eligible & trusted
    arrays["REFLECTIVITY_TRUST_MASK"] = trusted.astype("uint8")
    arrays["QPE_ELIGIBLE_MASK"] = eligible.astype("uint8")
    arrays["RFI_QUARANTINE_MASK"] = quarantine.astype("uint8")
    arrays["DBZH_USABLE"] = np.where(eligible, native.fields["DBZH"], np.nan).astype("float32")
    arrays["RFI_RISK_STATE"][confirmed] = 3
    arrays["RFI_RISK_STATE"][quarantine] = 2
    arrays["RFI_MIXED_MASK"] |= (weather & (confirmed | uncertain)).astype("uint8")
    for field in ("RHOHV", "ZDR", "PHIDP", "VR", "SW", "SNR"):
        arrays[field + "_TRUST_MASK"] &= trusted.astype("uint8")
    reason = np.zeros(shape, "uint32")
    for mask, bit in (
        (range_mask, ResidualReason.RANGE_INLIER_ASSOCIATION),
        (linked, ResidualReason.RANGE_LINKED_REVIEW),
        (narrow, ResidualReason.NARROW_OBJECT),
        (narrow_model, ResidualReason.RAW_SHOULDER_MODEL),
        (peripheral, ResidualReason.ORIGINAL_PARENT_REVIEW),
        (speckle, ResidualReason.SPECKLE_RAW_NOISE),
        (pol_bad & candidate, ResidualReason.POL_CORROBORATION),
        (protected & candidate, ResidualReason.WEATHER_PROTECTED),
        (add_confirm, ResidualReason.CONFIRMED_ADDITION),
        (add_quarantine, ResidualReason.QUARANTINED_ADDITION),
        (candidate & unknown_platform, ResidualReason.UNVERIFIED_RANGE_PLATFORM),
        (candidate & ~reject & ~quarantine, ResidualReason.CANDIDATE_ONLY),
    ):
        reason[mask & observed] |= np.uint32(bit)
    arrays.update(
        {
            "V6_CAPABILITY_CODE": code,
            "V6_CANDIDATE_MASK": candidate.astype("uint8"),
            "V6_WEATHER_PROTECTED_MASK": protected.astype("uint8"),
            "V6_POL_CORROBORATED_MASK": pol_bad.astype("uint8"),
            "V6_CONFIRMED_ADDITION_MASK": add_confirm.astype("uint8"),
            "V6_QUARANTINED_ADDITION_MASK": add_quarantine.astype("uint8"),
            "V6_BASELINE_REJECT_MASK": old_reject.astype("uint8"),
            "V6_BASELINE_QUARANTINE_MASK": old_quarantine.astype("uint8"),
            "V6_BASELINE_ELIGIBLE_MASK": old_eligible.astype("uint8"),
            "V6_DECISION_REASON": reason,
        }
    )
    if repair is not None:
        outcome = np.zeros(shape, "uint8")
        outcome[protected] = 1
        outcome[candidate & ~protected] = 2
        outcome[candidate & quarantine] = 3
        outcome[candidate & reject] = 4
        outcome[~observed] = 5
        arrays.update(
            {
                "V61_POL_RELIABLE_MASK": reliable.astype("uint8"),
                "V61_SNR_AVAILABLE_MASK": snr_available.astype("uint8"),
                "V61_PHASE_BAD_MASK": phase_bad.astype("uint8"),
                "V61_ZDR_BAD_MASK": zdr_bad.astype("uint8"),
                "V61_LOW_RHO_MASK": low_rho.astype("uint8"),
                "V61_REVIEW_OUTCOME": outcome,
            }
        )
        summary["repair"] = repair.model_dump(mode="json")
        summary["outcomes"] = {str(i): int((outcome == i).sum()) for i in range(6)}
    summary.update(
        candidate_gates=int(candidate.sum()),
        confirmed_additions=int(add_confirm.sum()),
        quarantined_additions=int(add_quarantine.sum()),
        candidate_still_eligible=int((candidate & eligible).sum()),
        reason_counts={b.name: int(((reason & int(b)) != 0).sum()) for b in ResidualReason},
        interpretation="diagnostic_not_real_weather_skill",
    )
    return Decision(arrays, flags, quality), summary
