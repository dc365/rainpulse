"""Separate administrative admission from physical measurement health.

Read-only projection of original health; do not relabel source health or heights.
Only the specific, fully identified CONFIG_NOT_READY-only case is exempted.
"""


def health_facets(health, profile):
    cfg = getattr(profile, "generalization", None)
    state = health.get("health", "UNAVAILABLE")
    value = health.get("health_reasons", [])
    reasons = (
        sorted(set(value))
        if isinstance(value, (list, tuple)) and all(isinstance(x, str) for x in value)
        else ["MALFORMED_HEALTH_REASONS"]
    )
    administrative = [r for r in reasons if r == "CONFIG_NOT_READY"]
    physical = [r for r in reasons if r != "CONFIG_NOT_READY"]
    if state == "DEGRADED" and not reasons:
        physical.append("UNEXPLAINED_DEGRADATION")
    channel = health.get("channel_status")
    if state == "DEGRADED" and channel != "OK":
        physical.append("CHANNEL_NOT_CONFIRMED_OK")
    exempt = bool(
        cfg
        and cfg.split_admission_health
        and state == "DEGRADED"
        and administrative
        and not physical
        and channel == "OK"
    )
    factor = (
        profile.health_gate.degraded_quality_multiplier
        if state == "DEGRADED" and not exempt
        else 1.0
    )
    ready = health.get("config_lifecycle") == "ready" and not administrative
    return {
        "raw_health": state,
        "config_lifecycle": health.get("config_lifecycle"),
        "administrative_reasons": administrative,
        "physical_reasons": sorted(set(physical)),
        "measurement_health": "HEALTHY" if exempt else state,
        "physical_quality_multiplier": float(factor),
        "administrative_penalty_removed": exempt,
        "admission_ready": bool(ready and state == "HEALTHY"),
        "operational_eligible": False,
        "interpretation": "measurement_projection_not_configuration_approval",
    }
