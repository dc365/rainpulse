"""Raw, strictly-past recurrence; no old QC mask is used as a class label."""
import numpy as np
from .arrays import moment, numeric, mask
from .background import timestamp


def raw_recurrence(current, references, cfg, *, maximum_age_seconds=900):
    z, observed = moment(current, "DBZH")
    shape = current.shape
    count, hits = np.zeros(shape, "uint8"), np.zeros(shape, "uint8")
    current_time = timestamp(current.attrs["volume_end_time_utc"])
    seen = {str(current.attrs["scan_id"])}
    rejected = {"time": 0, "geometry": 0}
    refs = list(references)
    if len(refs) > 3:
        raise ValueError("raw recurrence accepts at most three frozen references")
    for other in refs:
        ident = str(other.attrs["scan_id"])
        if ident in seen:
            raise ValueError("duplicate/target temporal scan")
        seen.add(ident)
        if str(other.attrs["radar_id"]) != str(current.attrs["radar_id"]):
            raise ValueError("temporal recurrence requires the same radar")
        age = (current_time - timestamp(other.attrs["volume_end_time_utc"])).total_seconds()
        if not 0 < age <= maximum_age_seconds:
            rejected["time"] += 1; continue
        if other.name != current.name:
            rejected["geometry"] += 1; continue
        y, valid = moment(other, "DBZH")
        exact = all(np.array_equal(getattr(current,k),getattr(other,k)) for k in ('ranges','azimuth','elevation'))
        if not exact:
            if not cfg.temporal_spatial_matching:
                rejected['geometry'] += 1; continue
            from .observation_match import nearest
            ray,gate,matched=nearest(current,other,cfg)
            y=y[ray[:,None],gate[None,:]]
            valid=valid[ray[:,None],gate[None,:]]&matched
            if not valid.any():
                rejected['geometry'] += 1
        else:
            valid &= other.geometry_good[:,None]
        valid &= observed & current.geometry_good[:, None]
        count += valid
        hits += valid & (abs(z-y) <= cfg.temporal_match_db)
    fraction = np.divide(hits, count, out=np.full(shape, np.nan, "float32"), where=count > 0)
    return {
        "NP_FIXED_MATCH_FRACTION": fraction,
        "NP_FIXED_SAMPLE_COUNT": count,
        # No optical flow / displacement / raw donor contract is available here.
        "NP_ADVECTED_MATCH_FRACTION": np.full(shape, np.nan, "float32"),
        "NP_ADVECTED_SAMPLE_COUNT": np.zeros(shape, "uint8"),
    }, {"raw_reference_scans": len(refs), "abstained": rejected, "motion_compensated_status": "unavailable_not_inferred"}


def recurrence_from_roots(current, temporal_roots, profile):
    from ..adapters import adapt_sweep
    references = [adapt_sweep(root, current.name, profile) for root in temporal_roots if current.name in root]
    return raw_recurrence(current, references, profile.nonprecip_review, maximum_age_seconds=profile.context.max_age_seconds)


def verified_vertical_absence(current_dbzh, upper_observed, upper_no_echo, comparable,
                              upper_detection_limit_dbz, *, margin_db=6.):
    """Caller supplies verified beam/coverage/sensitivity comparability, not proximity.

    No echo is adverse evidence only when the comparable upper measurement would
    have been sensitive enough to see this signal. It is never itself a class.
    """
    z = np.asarray(current_dbzh, dtype="float32")
    obs = mask(upper_observed, z.shape, "upper_observed")
    noecho = mask(upper_no_echo, z.shape, "upper_no_echo")
    same = mask(comparable, z.shape, "vertical_comparable")
    limit = numeric(upper_detection_limit_dbz, z.shape, "upper_detection_limit_dbz")
    if np.any(noecho & ~obs) or not np.isfinite(margin_db) or margin_db <= 0:
        raise ValueError("invalid vertical detection contract")
    available = same & obs & np.isfinite(z) & np.isfinite(limit) & (limit <= z-margin_db)
    return {"NP_VERTICAL_NEGATIVE_AVAILABLE_MASK": available.astype("uint8"), "NP_VERTICAL_NEGATIVE_MASK": (available & noecho).astype("uint8")}
