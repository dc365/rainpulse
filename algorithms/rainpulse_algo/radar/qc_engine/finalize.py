"""Final health/eligibility projection shared by all decision stages.

Does not reinterpret reasons, restore rejected gates or relabel quarantine.
"""

import numpy as np

from .quality_policy import health_facets


def finalize_decision(
    sweep,
    decision,
    profile,
    health,
    *,
    baseline_quality=None,
    v5_quality=None,
    v7_baseline_quality=None,
):
    quality = decision.quality.copy()
    cfg = getattr(profile, "generalization", None)
    facets = health_facets(health, profile)
    factor = facets["physical_quality_multiplier"]
    pre_eligible = decision.arrays["QPE_ELIGIBLE_MASK"] == 1
    legacy_factor = (
        profile.health_gate.degraded_quality_multiplier if health["health"] == "DEGRADED" else 1.0
    )
    legacy_eligible = pre_eligible & (
        quality * legacy_factor >= profile.quality_index.quantitative_minimum
    )
    if cfg is not None:
        decision.arrays["P2_QUALITY_PRE_HEALTH"] = quality.copy()
        decision.arrays["P2_LEGACY_HEALTH_ELIGIBLE_MASK"] = legacy_eligible.astype("uint8")
    if factor != 1.0:
        quality *= factor
        if baseline_quality is not None:
            baseline_quality *= factor
    if baseline_quality is not None:
        decision.arrays["V5_BASELINE_ELIGIBLE_MASK"] &= (
            baseline_quality >= profile.quality_index.quantitative_minimum
        ).astype("uint8")
    if v5_quality is not None:
        if factor != 1.0:
            v5_quality *= factor
        decision.arrays["V6_BASELINE_ELIGIBLE_MASK"] &= (
            v5_quality >= profile.quality_index.quantitative_minimum
        ).astype("uint8")
    observed = sweep.field_available["DBZH"]
    low = observed & (quality < profile.quality_index.low_quality_threshold)
    flags = decision.flags.copy()
    flags[low] |= profile.flag_masks["LOW_QUALITY"]
    # Health may lower quantitative eligibility even when a local field was trusted.
    eligible = (decision.arrays["QPE_ELIGIBLE_MASK"] == 1) & (
        quality >= profile.quality_index.quantitative_minimum
    )
    decision.arrays["QPE_ELIGIBLE_MASK"] = eligible.astype("uint8")
    decision.arrays["DBZH_USABLE"] = np.where(eligible, sweep.fields["DBZH"], np.nan).astype(
        "float32"
    )
    if v7_baseline_quality is not None:
        if factor != 1.0:
            v7_baseline_quality *= factor
        decision.arrays["V7_BASELINE_ELIGIBLE_MASK"] &= (
            v7_baseline_quality >= profile.quality_index.quantitative_minimum
        ).astype("uint8")
    if cfg is not None:
        decision.arrays["P2_ADMIN_PENALTY_REMOVED_MASK"] = (
            eligible & ~legacy_eligible & facets["administrative_penalty_removed"]
        ).astype("uint8")
        decision.arrays["P2_PHYSICAL_HEALTH_FACTOR"] = np.where(observed, factor, np.nan).astype(
            "float32"
        )
    return quality, observed, low, flags
