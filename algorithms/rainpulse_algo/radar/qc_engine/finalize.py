"""Final health/eligibility projection shared by all decision stages.

Does not reinterpret reasons, restore rejected gates or relabel quarantine.
"""
import numpy as np


def finalize_decision(sweep, decision, profile, health, *, baseline_quality=None,
                      v5_quality=None, v7_baseline_quality=None):
    quality = decision.quality.copy()
    if health["health"] == "DEGRADED":
        quality *= profile.health_gate.degraded_quality_multiplier
        if baseline_quality is not None:
            baseline_quality *= profile.health_gate.degraded_quality_multiplier
    if baseline_quality is not None:
        decision.arrays["V5_BASELINE_ELIGIBLE_MASK"] &= (
            baseline_quality >= profile.quality_index.quantitative_minimum
        ).astype("uint8")
    if v5_quality is not None:
        if health["health"] == "DEGRADED":
            v5_quality *= profile.health_gate.degraded_quality_multiplier
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
        if health["health"] == "DEGRADED":
            v7_baseline_quality *= profile.health_gate.degraded_quality_multiplier
        decision.arrays["V7_BASELINE_ELIGIBLE_MASK"] &= (v7_baseline_quality >= profile.quality_index.quantitative_minimum).astype("uint8")
    return quality, observed, low, flags
