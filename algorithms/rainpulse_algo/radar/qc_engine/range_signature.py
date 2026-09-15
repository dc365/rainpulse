"""Long-range log-distance measurement hypotheses, with explicit censor semantics.

Not an IQ detector, not SWAN, not a saturation assertion from palette colours.
The shape alone is a quarantine candidate by default, not confirmed weather truth.
"""

from __future__ import annotations

from collections import Counter
from dataclasses import dataclass

import numpy as np

from .crossradar_profile import CrossRadarConfig
from .segments import intervals


@dataclass(frozen=True)
class RangeEvidence:
    arrays: dict[str, np.ndarray]
    summary: dict


def _fit(ranges, values, config, ceiling, *, range_term_db_per_km=0.0):
    """Use actual metres (normalized to 1 km), not gate index. Never fill missing."""
    log_range = 20 * np.log10(ranges / 1000.0)
    x = log_range + range_term_db_per_km * ranges / 1000.0
    mode, cap = 1, None
    if ceiling is not None:
        cap = ceiling.value_dbz
        if np.any(values > cap + config.plateau_tolerance_db):
            return None, "inconsistent_verified_ceiling"
        mode = 2
    else:
        # This is a numeric plateau hypothesis, NEVER a verified encoding statement.
        tail = values[
            -max(3, int(np.ceil(config.minimum_plateau_length_m / np.median(np.diff(ranges))))) :
        ]
        possible = float(np.median(tail))
        flat = np.abs(values - possible) <= config.numeric_plateau_spread_db / 2
        idx = len(values) - 1
        while idx >= 0 and flat[idx]:
            idx -= 1
        if (
            possible >= config.plateau_minimum_dbz
            and np.ptp(tail) <= config.numeric_plateau_spread_db
            and idx + 1 < len(values)
            and ranges[-1] - ranges[idx + 1] >= config.minimum_plateau_length_m
            and np.max(values) <= possible + config.plateau_tolerance_db
        ):
            cap, mode = possible, 3
    censored = (
        np.zeros(len(values), bool)
        if cap is None
        else (values >= cap - config.plateau_tolerance_db)
    )
    fit = ~censored
    if fit.sum() < config.minimum_fit_samples:
        return None, "insufficient_uncensored_samples"
    if np.ptp(ranges[fit]) < config.minimum_fit_span_m or np.ptp(log_range[fit]) < (
        config.minimum_log_range_span_db
    ):
        return None, "insufficient_measured_range_ratio"
    intercept = float(np.median(values[fit] - x[fit]))
    prediction = intercept + x
    residual = np.abs(values - prediction)
    if cap is not None:
        # A censored observation implies latent value >= cap; it is not equal to cap.
        residual[censored] = np.maximum(cap - prediction[censored], 0.0)
    p90 = float(np.percentile(residual, 90))
    n = max(3, len(values) // 5)
    growth = float(np.median(values[-n:]) - np.median(values[:n]))
    if p90 > config.residual_p90_db:
        return None, "shape_residual"
    if growth < config.minimum_growth_db:
        return None, "insufficient_growth"
    if np.mean(values >= config.minimum_high_dbz) < config.minimum_high_fraction:
        return None, "insufficient_strong_measured_fraction"
    return dict(
        mode=mode,
        ceiling=cap,
        intercept=intercept,
        residual=residual,
        p90=p90,
        growth=growth,
        inliers=residual <= config.gate_residual_db,
        uncensored_count=int(fit.sum()),
        censored_count=int(censored.sum()),
    ), None


def range_signatures(
    native, config: CrossRadarConfig, *, association=None, protected=None, range_term_db_per_km=0.0
) -> RangeEvidence:
    if not np.isfinite(range_term_db_per_km) or not 0 <= range_term_db_per_km <= 0.03:
        raise ValueError("invalid measured range term")
    observed = native.field_available["DBZH"] & native.geometry_good[:, None]
    z, ranges, dr = native.fields["DBZH"], native.ranges, native.gate_spacing_m
    echo = observed & (z >= config.minimum_echo_dbz) & (ranges[None, :] >= config.minimum_range_m)
    ceilings = [
        x
        for x in config.verified_ceilings
        if x.radar_config_version == native.attrs.get("radar_config_version")
        and x.sweep == native.name
    ]
    ceiling = ceilings[0] if ceilings else None
    if protected is None:
        protected = np.zeros(native.shape, bool)
    if np.shape(protected) != native.shape:
        raise ValueError("range association weather geometry differs")
    reasons = Counter()
    parts, by_ray = [], [[] for _ in native.azimuth]
    for ray in range(len(z)):
        for lo, hi, count in intervals(
            echo[ray], ~observed[ray], dr, config.maximum_gap_m, config.maximum_gap_fraction
        ):
            if (
                hi - lo
            ) * dr < config.minimum_span_m or count * dr < config.minimum_measured_length_m:
                reasons["short_measured_segment"] += 1
                continue
            idx = np.flatnonzero(echo[ray, lo:hi]) + lo
            fit, why = _fit(
                ranges[idx], z[ray, idx], config, ceiling, range_term_db_per_km=range_term_db_per_km
            )
            if fit is None:
                reasons[why] += 1
                continue
            # Incompatible measured gates are not swallowed by the object.
            good = np.zeros(len(ranges), bool)
            good[idx[fit["inliers"]]] = True
            bridgeable = ~observed[ray]
            gap_m, gap_fraction = config.maximum_gap_m, config.maximum_gap_fraction
            if association is not None:
                # Only identity crosses a bounded UNDECIDED measured outlier.
                # The outlier does not enter the fit/inlier candidate or acquire a value.
                bridgeable = bridgeable.copy()
                bridgeable[idx] |= fit["residual"] <= association.link_maximum_residual_db
                bridgeable &= ~np.asarray(protected[ray], bool)
                gap_m = association.link_maximum_gap_m
                gap_fraction = association.link_maximum_fraction
            for a, b, measured in intervals(good, bridgeable, dr, gap_m, gap_fraction):
                if measured * dr < config.minimum_measured_length_m:
                    reasons["short_inlier_segment"] += 1
                    continue
                if len(parts) >= config.maximum_segments:
                    raise ValueError("V5 range segment budget exceeded; no partial result")
                locations = np.flatnonzero(good[a:b]) + a
                by_ray[ray].append(len(parts))
                part_fit = dict(fit)
                part_fit["linked_locations"] = (
                    np.flatnonzero(observed[ray, a:b] & bridgeable[a:b] & ~good[a:b]) + a
                    if association is not None
                    else np.array([], dtype=int)
                )
                parts.append((ray, a, b, locations, part_fit))
    parent = list(range(len(parts)))

    def find(i):
        while parent[i] != i:
            parent[i] = parent[parent[i]]
            i = parent[i]
        return i

    for ray in range(len(z)):
        other = (ray + 1) % len(z)
        if (
            native.gap_after[ray]
            or not native.geometry_good[ray]
            or not native.geometry_good[other]
        ):
            continue
        if other == 0 and not native.full_ppi:
            continue
        aa, bb, i, j = by_ray[ray], by_ray[other], 0, 0
        while i < len(aa) and j < len(bb):
            a, b = parts[aa[i]], parts[bb[j]]
            if min(a[2], b[2]) > max(a[1], b[1]):
                x, y = find(aa[i]), find(bb[j])
                parent[max(x, y)] = min(x, y)
            if a[2] < b[2]:
                i += 1
            else:
                j += 1
    groups = {}
    for i in range(len(parts)):
        groups.setdefault(find(i), []).append(i)
    ids = np.zeros(native.shape, "uint32")
    mode = np.zeros(native.shape, "uint8")
    linked_ids = np.zeros(native.shape, "uint32")
    fit_quality = np.full(native.shape, np.nan, "float32")
    growth = fit_quality.copy()
    spans = fit_quality.copy()
    records = []
    for group in groups.values():
        angles = np.sort(np.unique([native.azimuth[parts[i][0]] for i in group]))
        width = float(360 - np.max(np.diff(np.r_[angles, angles[0] + 360])))
        width += native.audit["azimuth_spacing_deg"]
        lo = min(parts[i][1] for i in group)
        hi = max(parts[i][2] for i in group)
        span = max((parts[i][2] - parts[i][1]) * dr for i in group)
        transverse = max(dr, (ranges[lo] + ranges[hi - 1]) / 2 * np.deg2rad(width))
        if width > config.maximum_width_deg or span / transverse < config.minimum_aspect:
            reasons["not_a_radial_object"] += 1
            continue
        identity = len(records) + 1
        for i in group:
            ray, a, b, idx, fit = parts[i]
            ids[ray, idx] = identity
            linked_ids[ray, fit["linked_locations"]] = identity
            mode[ray, idx] = fit["mode"]
            fit_quality[ray, idx] = fit["p90"]
            growth[ray, idx] = fit["growth"]
            spans[ray, idx] = (b - a) * dr
        records.append(
            dict(
                object_id=identity,
                azimuth_width_deg=width,
                span_m=float(span),
                range_start_m=float(ranges[lo]),
                range_end_m=float(ranges[hi - 1]),
                ray_count=len(angles),
                observed_gates=sum(len(parts[i][3]) for i in group),
                model_codes=sorted({parts[i][4]["mode"] for i in group}),
                maximum_residual_db=max(parts[i][4]["p90"] for i in group),
            )
        )
    candidate = ids > 0
    return RangeEvidence(
        {
            "V5_RANGE_CANDIDATE_MASK": candidate.astype("uint8"),
            "V5_RANGE_FIT_AVAILABLE_MASK": candidate.astype("uint8"),
            "V5_RANGE_OBJECT_ID": ids,
            "V5_RANGE_MODEL_CODE": mode,
            "V5_RANGE_RESIDUAL_P90_DB": fit_quality,
            "V5_RANGE_GROWTH_DB": growth,
            "V5_RANGE_SPAN_M": spans,
            **(
                {
                    "V6_RANGE_LINKED_REVIEW_MASK": (linked_ids > 0).astype("uint8"),
                    "V6_RANGE_LINK_PARENT_ID": linked_ids,
                }
                if association is not None
                else {}
            ),
        },
        dict(
            method=("range-inlier-association-v6" if association is not None else config.method),
            objects=records,
            object_count=len(records),
            candidate_gates=int(candidate.sum()),
            discarded_segment_reasons=dict(sorted(reasons.items())),
            ceiling_metadata=ceiling.model_dump(mode="json") if ceiling else None,
            interpretation="measurement_hypothesis_not_confirmed_rfi",
            source_relationship="Wen2020_eq1_log_distance_plus_explicit_engineering_extension",
        ),
    )
