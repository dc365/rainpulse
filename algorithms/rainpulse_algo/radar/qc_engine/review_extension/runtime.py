"""Additive nonprecip disposition at the real runner's pre-phase boundary.

Existing confirmed flags and RFI identities are immutable here. An experimental
nonprecip quarantine is not a confirmed clutter label and never valid no-rain.
"""
import numpy as np
from . import VERSION
from .arrays import mask
from .nonprecip import classify

TRUST_FIELDS = (
    "REFLECTIVITY_TRUST_MASK", "QPE_ELIGIBLE_MASK", "RHOHV_TRUST_MASK",
    "ZDR_TRUST_MASK", "PHIDP_TRUST_MASK", "VR_TRUST_MASK", "SW_TRUST_MASK", "SNR_TRUST_MASK",
)


def project(native, baseline, classification, cfg, *, low_quality_flag):
    shape = native.shape
    observed = mask(native.field_available["DBZH"], shape, "observed")
    proposal = mask(classification.arrays["NP_PROPOSAL_MASK"], shape, "proposal")
    old_action = baseline.arrays["QC_ACTION"]
    if old_action.shape != shape or not np.array_equal(old_action == 3, ~observed):
        raise ValueError("nonprecip baseline action/support differs")
    old_rfi = mask(baseline.arrays.get("RFI_QUARANTINE_MASK"), shape, "baseline_rfi")
    eligible = mask(baseline.arrays["QPE_ELIGIBLE_MASK"], shape, "baseline_eligible")
    added = proposal & observed & (old_action != 2) & ~old_rfi
    if cfg.mode == "audit":
        added[:] = False
    if cfg.mode not in {"audit", "experiment_quarantine"}:
        raise ValueError("invalid nonprecip action mode")
    out = {k: v.copy() for k, v in baseline.arrays.items()}
    flags, quality = baseline.flags.copy(), baseline.quality.copy()
    out.update(classification.arrays)
    out.update(
        NP_BASELINE_ACTION=old_action.copy(),
        NP_BASELINE_ELIGIBLE_MASK=eligible.astype("uint8"),
        NP_BASELINE_REFLECTIVITY_TRUST_MASK=baseline.arrays["REFLECTIVITY_TRUST_MASK"].copy(),
        NP_BASELINE_RFI_QUARANTINE_MASK=old_rfi.astype("uint8"),
        NP_BASELINE_FLAGS=baseline.flags.copy(),
        NP_BASELINE_QUALITY=baseline.quality.copy(),
        NP_QUARANTINE_MASK=added.astype("uint8"),
        NP_ACTION_MODE=np.full(shape, cfg.mode == "experiment_quarantine", "uint8"),
    )
    out["QC_ACTION"][added] = 1
    quality[added] = np.minimum(quality[added], cfg.quarantine_quality)
    flags[added] |= np.uint32(low_quality_flag)
    for name in TRUST_FIELDS:
        if name not in out or out[name].shape != shape:
            raise ValueError(f"missing nonprecip disposition contract: {name}")
        out[name][added] = 0
    out["DBZH_USABLE"][added] = np.nan
    lost = eligible & added
    fraction = float(lost.sum() / max(1, eligible.sum()))
    review = fraction > cfg.maximum_new_eligible_loss_fraction
    # Never restore doubtful measurements merely to meet an area/coverage budget.
    out["NP_BUDGET_REVIEW_MASK"] = (added & review).astype("uint8")
    return type(baseline)(out, flags, quality), {
        **classification.summary, "extension_version": VERSION,
        "mode": cfg.mode, "operational_eligible": False,
        "added_quarantine_gates": int(added.sum()), "new_confirmed_gates": 0,
        "new_eligible_loss_gates": int(lost.sum()), "new_eligible_loss_fraction": fraction,
        "review_required": review, "budget_overflow_behavior": "retain_isolation_and_require_review",
    }


def apply_nonprecip_review(native, baseline, evidence, profile, *, ancillary=None,
                          context=None, weather_support=None):
    cfg = getattr(profile, "nonprecip_review", None)
    if cfg is None:
        return baseline, {"status": "disabled"}
    ancillary = ancillary or {}
    order = native.original_indices
    background = None
    if "nonprecip_background" in ancillary:
        receipt = ancillary.get("nonprecip_background_receipt", {})
        if receipt.get("binding_content_sha256", receipt.get("asset_content_sha256")) != profile.static_ground_clutter.asset_sha256:
            raise ValueError("nonprecip background differs from the selected profile")
        background = {
            "fields": {k: np.asarray(v)[order] for k, v in ancillary["nonprecip_background"].items()},
            "receipt": receipt,
        }
    # Runner contexts/ancillary maps have original ray order. Algorithms use native order.
    ctx = {k: np.asarray(v)[order] for k, v in (context or {}).items() if k.startswith("NP_")}
    result = classify(native, evidence.arrays, cfg, background=background, context=ctx,
                      weather_support=weather_support,
                      no_rain_below_dbz=profile.echo.no_rain_below_dbz,
                      strong_weather_support=profile.context.strong_support)
    if cfg.near_background is not None:
        from .near_background_runtime import augment
        augment(native,baseline,result,cfg,profile.echo.no_rain_below_dbz)
    result.arrays.update({k:v for k,v in ctx.items() if k.startswith('NP_PAIRED_')})
    return project(native, baseline, result, cfg, low_quality_flag=profile.flag_masks["LOW_QUALITY"])


def validate_nonprecip_fields(group, valid, reject, rfi_quarantine):
    """Validate serialized output; do not weaken the legacy RFI-only validators."""
    shape = valid.shape
    fields = {
        "NP_CLASS": "uint8", "NP_EVIDENCE_BITS": "uint16", "NP_CLASS_BITS": "uint16",
        "NP_EVIDENCE_FAMILY_COUNT": "uint8", "NP_CANDIDATE_MASK": "uint8",
        "NP_PROPOSAL_MASK": "uint8", "NP_QUARANTINE_MASK": "uint8",
        "NP_CONFIRMED_MASK": "uint8", "NP_BASELINE_ACTION": "uint8",
        "NP_BASELINE_ELIGIBLE_MASK": "uint8", "NP_BASELINE_REFLECTIVITY_TRUST_MASK": "uint8",
        "NP_BASELINE_RFI_QUARANTINE_MASK": "uint8", "NP_ACTION_MODE": "uint8",
        "NP_BASELINE_FLAGS": "uint32", "NP_BASELINE_QUALITY": "float32",
        "NP_WEATHER_PROTECTED_MASK": "uint8", "NP_MIXED_MASK": "uint8",
    }
    if not any(k.startswith("NP_") for k in group):
        return np.zeros(shape, bool)
    for key, dtype in fields.items():
        if key not in group or group[key].shape != shape or group[key].dtype != np.dtype(dtype):
            raise ValueError(f"invalid nonprecip field {key}")
    get = lambda key: np.asarray(group[key][:])
    q = mask(get("NP_QUARANTINE_MASK"), shape, "NP_quarantine")
    proposal = mask(get("NP_PROPOSAL_MASK"), shape, "NP_proposal")
    old = get("NP_BASELINE_ACTION")
    mode = get("NP_ACTION_MODE")
    if np.any(mode > 1) or np.unique(mode).size != 1:
        raise ValueError("nonprecip action mode differs within a cut")
    expected = proposal & valid & (old != 2) & ~rfi_quarantine & (mode == 1)
    if not np.array_equal(q, expected):
        raise ValueError("nonprecip action not equal to measured protected proposal")
    if not np.array_equal(get("NP_BASELINE_RFI_QUARANTINE_MASK") == 1, rfi_quarantine):
        raise ValueError("nonprecip modified RFI identity")
    if not np.array_equal(old == 3, ~valid) or np.any(old > 3) or not np.array_equal(reject, old == 2):
        raise ValueError("nonprecip modified missing/confirmed disposition")
    if not np.array_equal(get("QC_ACTION"), np.where(q, 1, old)):
        raise ValueError("nonprecip action provenance differs")
    cls = get("NP_CLASS")
    if np.any(cls > 9) or not np.array_equal(cls == 7, ~valid):
        raise ValueError("nonprecip classes changed original missing support")
    if np.any(proposal & (~valid | ~np.isin(cls, [2, 3, 4, 9]))) or np.any(get("NP_CONFIRMED_MASK")):
        raise ValueError("unsupported nonprecip proposal/confirmation")
    if np.any(cls == 9):
        near = mask(get("NP_NEAR_CANDIDATE_MASK"), shape, "near_candidate")
        available = mask(get("NP_NEAR_AVAILABLE_MASK"), shape, "near_available")
        if np.any((cls == 9) & (~near | ~available)):
            raise ValueError("near nonmet class lacks measured neighbourhood support")
    if np.any(q & ((get("NP_WEATHER_PROTECTED_MASK") == 1) | (get("NP_MIXED_MASK") == 1))):
        raise ValueError("nonprecip action crossed a weather barrier")
    eligible = get("QPE_ELIGIBLE_MASK") == 1
    if np.any(eligible & ((get("NP_BASELINE_ELIGIBLE_MASK") == 0) | q)):
        raise ValueError("nonprecip eligibility restored/leaked measurement")
    if not np.array_equal(get("REFLECTIVITY_TRUST_MASK") == 1, (get("NP_BASELINE_REFLECTIVITY_TRUST_MASK") == 1) & ~q):
        raise ValueError("nonprecip trust projection differs")
    for key in TRUST_FIELDS:
        if np.any(q & (get(key) != 0)):
            raise ValueError(f"nonprecip quarantine leaked into {key}")
    flags = get("QC_FLAGS")
    base_flags = get("NP_BASELINE_FLAGS")
    if np.any((flags & base_flags) != base_flags):
        raise ValueError("nonprecip lost an existing cause flag")
    return q


def review_summary(sweep_records):
    records = [x["nonprecip_review"] for x in sweep_records.values() if "nonprecip_review" in x]
    return {
        "extension_version": VERSION, "evaluated_sweeps": len(records),
        "new_confirmed_gates": 0,
        "added_quarantine_gates": sum(x.get("added_quarantine_gates", 0) for x in records),
        "new_eligible_loss_gates": sum(x.get("new_eligible_loss_gates", 0) for x in records),
        "review_required": any(x.get("review_required", False) for x in records),
        "operational_eligible": False,
    }


def review_attributes(profile):
    version = getattr(profile, "review_extension_version", None)
    if version is None:
        return {}
    broad = profile.generalization.broad_source
    return {
        **({
            "qc_radial_revision_version": broad.source_review.radial_revision.version,
            "qc_radial_revision_step": broad.source_review.radial_revision.step,
            "qc_radial_revision_mode": broad.source_review.radial_revision.mode,
        } if broad is not None and broad.source_review is not None
             and broad.source_review.radial_revision is not None else {}),
        "qc_review_extension_version": version,
        "qc_review_nonprecip_enabled": profile.nonprecip_review is not None,
        "qc_review_source_enabled": broad is not None and broad.source_review is not None,
    }
