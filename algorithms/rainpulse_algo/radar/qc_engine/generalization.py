"""Measurement-supported broad source review, separate from admission/geometry.

No new confirmations. A coherent OC1 source + actual range fit is stronger than
shape alone, but both share a receiver chain: mark uncertain/isolated, not truth.
Positive comparable weather and measured enhancement conflicts remain barriers.
"""

from enum import IntFlag

import numpy as np

from .decision import Action, Decision
from .object_consensus.engine import Reason as OCReason
from .range_signature import range_signatures


class ReviewReason(IntFlag):
    RANGE_SUPPORTED = 1
    BROAD_SHAPE = 2
    COHERENT_TARGET_MATCH = 4
    SAME_SOURCE_LOCAL_SMOOTHNESS = 8
    INDEPENDENT_WEATHER = 16
    TARGET_MEASUREMENT_CONFLICT = 32
    REFERENCE_NOT_BRACKETED = 64
    UNVERIFIED_NUMERIC_PLATEAU = 128
    QUARANTINED = 256
    BUDGET_REVIEW_REQUIRED = 512
    MEASUREMENT_EVIDENCE_INSUFFICIENT = 1024
    LEGACY_BUDGET_WITHHOLD_RECOVERED = 2048


def broad_source_review(
    native,
    baseline,
    profile,
    oc_evidence,
    oc_outcome,
    *,
    weather_support=None,
    eligible_before_oc1=None,
):
    cfg = profile.generalization
    if cfg is None:
        return baseline, {"status": "disabled"}
    shape = native.shape
    observed = native.field_available["DBZH"] & native.geometry_good[:, None]
    range_ev = range_signatures(native, profile.cross_radar, route_all_shapes=True)
    r = range_ev.arrays
    supported = r["P2_RANGE_MEASUREMENT_MASK"] == 1
    if not cfg.retain_broad_measurements:
        supported &= r["V5_RANGE_CANDIDATE_MASK"] == 1
    a = oc_evidence.arrays
    for key in ("family_code", "reason", "state", "bracketed_reference_mask"):
        if a[key].shape != shape:
            raise ValueError("P0-P2 OC1 geometry differs")
    reason = np.zeros(shape, "uint32")
    source_match = (a["family_code"] == 2) & (
        (a["reason"] & int(OCReason.SOURCE_MODEL_COMPATIBLE)) != 0
    )
    # Only the explicitly identified same-source broad smoothness conflict is
    # reconsidered. Low-SNR/missing/unmatched targets never inherit an action.
    same_source = source_match & ((a["reason"] & int(OCReason.BROAD_WEATHER_CONTINUITY)) != 0)
    blocked_bits = int(
        OCReason.LOCAL_POWER_ENHANCEMENT
        | OCReason.EXTERNAL_WEATHER_SUPPORT
        | OCReason.TARGET_POWER_MISMATCH
        | OCReason.TARGET_POL_MISSING
        | OCReason.TARGET_COHERENCE_MISMATCH
        | OCReason.GEOMETRY_UNAVAILABLE
    )
    conflict = (a["reason"] & blocked_bits) != 0
    independent = np.zeros(shape, bool)
    weather_available = np.zeros(shape, bool)
    if weather_support is not None:
        score = np.asarray(weather_support)
        if score.shape != shape:
            raise ValueError("independent weather geometry differs")
        weather_available = np.isfinite(score)
        if np.any(weather_available & ((score < 0) | (score > 1))):
            raise ValueError("invalid independent weather score")
        independent = weather_available & (score >= profile.context.strong_support)
    model_verified = r["P2_RANGE_MODEL_CODE"] != 3
    bracketed = a["bracketed_reference_mask"] == 1
    proposal = supported & source_match & ~conflict & ~independent & model_verified & observed
    if cfg.require_bracketed_reference:
        proposal &= bracketed
    if not cfg.resolve_coherent_self_protection:
        proposal[:] = False
    # Old OC1 may have declined its entire output due to the experiment budget.
    # Preserve its exact qualified proposals as review/isolation, not normal QPE.
    old_budget = oc_outcome.summary.get("status") == "blocked_budget_whole_cut_reverted"
    budget_recovered = (
        np.asarray(oc_outcome.proposed_quarantine, bool) & observed & ~independent
        if old_budget and cfg.prevent_budget_reversion
        else np.zeros(shape, bool)
    )
    proposal |= budget_recovered
    old_reject = baseline.arrays["QC_ACTION"] == Action.REJECT
    oldq = baseline.arrays["RFI_QUARANTINE_MASK"] == 1
    added = proposal & ~old_reject & ~oldq
    q = oldq | added
    original_eligible = (
        baseline.arrays["QPE_ELIGIBLE_MASK"] == 1
        if eligible_before_oc1 is None
        else np.asarray(eligible_before_oc1, bool)
    )
    if original_eligible.shape != shape:
        raise ValueError("budget denominator geometry differs")
    eligible = (baseline.arrays["QPE_ELIGIBLE_MASK"] == 1) & ~added
    cumulative_loss = original_eligible & ~eligible
    fraction = float(cumulative_loss.sum() / max(1, original_eligible.sum()))
    review_required = bool(old_budget or fraction > cfg.maximum_new_eligible_loss_fraction)
    out = {k: v.copy() for k, v in baseline.arrays.items()}
    flags, quality = baseline.flags.copy(), baseline.quality.copy()
    out["P2_BASELINE_QUARANTINE_MASK"] = oldq.astype("uint8")
    out["P2_BASELINE_ELIGIBLE_MASK"] = baseline.arrays["QPE_ELIGIBLE_MASK"].copy()
    out["P2_ELIGIBLE_BEFORE_OC1_MASK"] = original_eligible.astype("uint8")
    out["QC_ACTION"][added] = Action.DOWNWEIGHT
    out["RFI_QUARANTINE_MASK"] = q.astype("uint8")
    out["RFI_RISK_STATE"][added] = 2
    quality[added] = np.minimum(quality[added], cfg.quarantine_quality)
    flags[added] |= profile.flag_masks["LOW_QUALITY"]
    for field in (
        "REFLECTIVITY_TRUST_MASK",
        "QPE_ELIGIBLE_MASK",
        "RHOHV_TRUST_MASK",
        "ZDR_TRUST_MASK",
        "PHIDP_TRUST_MASK",
        "VR_TRUST_MASK",
        "SW_TRUST_MASK",
        "SNR_TRUST_MASK",
    ):
        out[field][added] = 0
    out["DBZH_USABLE"][added] = np.nan
    for mask, bit in (
        (supported, ReviewReason.RANGE_SUPPORTED),
        (r["P2_RANGE_ROUTE_CODE"] > 1, ReviewReason.BROAD_SHAPE),
        (source_match, ReviewReason.COHERENT_TARGET_MATCH),
        (same_source, ReviewReason.SAME_SOURCE_LOCAL_SMOOTHNESS),
        (independent, ReviewReason.INDEPENDENT_WEATHER),
        (conflict, ReviewReason.TARGET_MEASUREMENT_CONFLICT),
        (~bracketed & supported, ReviewReason.REFERENCE_NOT_BRACKETED),
        (~model_verified & supported, ReviewReason.UNVERIFIED_NUMERIC_PLATEAU),
        (added, ReviewReason.QUARANTINED),
        (supported & ~source_match, ReviewReason.MEASUREMENT_EVIDENCE_INSUFFICIENT),
        (budget_recovered, ReviewReason.LEGACY_BUDGET_WITHHOLD_RECOVERED),
    ):
        reason[mask & observed] |= int(bit)
    if review_required:
        reason[(proposal | cumulative_loss) & observed] |= int(ReviewReason.BUDGET_REVIEW_REQUIRED)
    out.update({k: v for k, v in r.items() if k.startswith("P2_")})
    out.update(
        P2_PROPOSAL_MASK=proposal.astype("uint8"),
        P2_ADDED_QUARANTINE_MASK=added.astype("uint8"),
        P2_REVIEW_REASON=reason,
        P2_INDEPENDENT_WEATHER_AVAILABLE_MASK=(weather_available & observed).astype("uint8"),
    )
    summary = {
        "status": "isolated_review_required" if review_required else "applied_candidate",
        "method": cfg.method,
        "review_required": review_required,
        "budget_overflow_behavior": cfg.budget_overflow,
        "independent_weather_available_gates": int((weather_available & observed).sum()),
        "independent_weather_supported_gates": int((independent & observed).sum()),
        "same_source_smoothness_gates": int((same_source & supported & observed).sum()),
        "proposed_gates": int(proposal.sum()),
        "added_quarantine_gates": int(added.sum()),
        "new_confirmed_gates": 0,
        "cumulative_new_eligible_loss_gates": int(cumulative_loss.sum()),
        "cumulative_new_eligible_loss_fraction": fraction,
        "legacy_budget_recovered": old_budget,
        "operational_eligible": False,
        "measurement_routes": range_ev.summary.get("routed_objects", []),
    }
    return Decision(out, flags, quality), summary
