"""Explicit child identity without changing any disabled historical profile hash."""
from . import VERSION


def strip_absent_review_fields(value):
    for key in ("review_extension_version", "nonprecip_review"):
        if value.get(key) is None:
            value.pop(key, None)
    general = value.get("generalization") or {}
    broad = general.get("broad_source") or {}
    if broad.get("source_review") is None:
        broad.pop("source_review", None)
    source = broad.get("source_review") or {}
    if source.get("radial_revision") is None:
        source.pop("radial_revision", None)
    return value


def validate_review_profile(profile):
    general = profile.generalization
    broad = general.broad_source if general is not None else None
    source = broad.source_review if broad is not None else None
    nonprecip = profile.nonprecip_review
    active = source is not None or nonprecip is not None
    if not active:
        if profile.review_extension_version is not None:
            raise ValueError("review extension identity declared without review configuration")
        return profile
    if profile.pipeline_version not in {"qc-opensource-7.3.6", "qc-opensource-7.3.7"} or profile.review_extension_version != VERSION:
        raise ValueError("review requires explicit v1 extension identity on the frozen 7.3.x base")
    if "review-20260917" not in profile.profile_version:
        raise ValueError("a distinct review-20260917 child profile is required")
    if source is not None and profile.geometry.phase_period_deg != 360:
        raise ValueError("review source reference requires the 360-degree phase convention")
    if source is not None and broad.range_residual_db > source.maximum_source_residual_db:
        raise ValueError("held-out reference fit must be at least as strict as the source target residual")
    if source is not None and source.radial_revision is not None:
        if "radial-20260918" not in profile.profile_version:
            raise ValueError("radial revision requires a distinct radial-20260918 child profile")
    if nonprecip is not None:
        if nonprecip.quarantine_quality >= profile.quality_index.quantitative_minimum:
            raise ValueError("nonprecip quarantine cannot be quantitatively eligible")
        if not profile.context.enabled or profile.context.max_temporal_scans < nonprecip.minimum_temporal_samples:
            raise ValueError("nonprecip review requires enabled causal temporal preparation")
    return profile
