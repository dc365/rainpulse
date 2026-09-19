"""A separate versioned extension; never rewrite frozen parent configurations."""

def validate_profile(profile):
    cfg=profile.volume_review
    if cfg is None:return profile
    if profile.pipeline_version not in {"qc-opensource-7.3.6", "qc-opensource-7.3.7", "qc-opensource-7.3.8", "qc-opensource-7.3.9"} or profile.operational_eligible is not False:
        raise ValueError("volume review requires a non-operational 7.3.x baseline")
    if cfg.quarantine_quality>=profile.quality_index.quantitative_minimum:
        raise ValueError("volume isolation quality must be below quantitative eligibility")
    if profile.geometry.phase_period_deg!=360:
        raise ValueError("volume source signatures require an explicit 360-degree phase convention")
    return profile
