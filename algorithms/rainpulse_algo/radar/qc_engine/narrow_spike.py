"""Native polar narrow/intermittent candidates, NOT a bRopo/SPIKE reproduction.

Shoulders are measured samples, not missing or newly blanked QC pixels. Each
candidate needs independent local measurement evidence before any confirmation.
"""

from __future__ import annotations

from dataclasses import dataclass

import numpy as np

from ..qc_geometry import nearest_azimuth_matches
from .polar_objects import angular_width, label_polar
from .segments import intervals


@dataclass(frozen=True)
class NarrowEvidence:
    arrays: dict[str, np.ndarray]
    summary: dict


def shoulder_contrast(native, cfg):
    observed, z = native.field_available["DBZH"], native.fields["DBZH"]
    contrast = np.zeros(native.shape, bool)
    available = np.zeros(native.shape, bool)
    maximum = np.full(native.shape, np.nan, "float32")
    component = np.cumsum(np.r_[0, native.gap_after[:-1] | ~native.geometry_good[:-1]])
    rows = np.arange(native.shape[0])
    spacing = native.audit["azimuth_spacing_deg"]
    for offset in cfg.narrow_offsets_deg:
        a, da, oka = nearest_azimuth_matches((native.azimuth - offset) % 360, native.azimuth)
        b, db, okb = nearest_azimuth_matches((native.azimuth + offset) % 360, native.azimuth)
        good_rows = oka & okb & (da <= spacing * 0.55) & (db <= spacing * 0.55)
        good_rows &= (
            (a != rows)
            & (b != rows)
            & native.geometry_good
            & native.geometry_good[a]
            & native.geometry_good[b]
        )
        if not native.full_ppi:
            good_rows &= (component[a] == component) & (component[b] == component)
            good_rows &= (a < rows) & (b > rows)
        good = good_rows[:, None] & observed & observed[a] & observed[b]
        delta = z - np.maximum(z[a], z[b])
        maximum = np.fmax(maximum, np.where(good, delta, np.nan))
        available |= good
        contrast |= good & (delta >= cfg.narrow_minimum_contrast_db)
    return contrast, available, maximum


def narrow_candidates(native, cfg, *, polarimetric_risk, protected, parent_mask=None):
    observed = native.field_available["DBZH"] & native.geometry_good[:, None]
    z, dr = native.fields["DBZH"], native.gate_spacing_m
    if np.shape(polarimetric_risk) != native.shape or np.shape(protected) != native.shape:
        raise ValueError("narrow evidence geometry differs")
    contrast, bg, delta = shoulder_contrast(native, cfg)
    echo = observed & (z >= cfg.minimum_echo_dbz) & (native.ranges[None, :] >= cfg.minimum_range_m)
    parents = np.zeros(native.shape, bool) if parent_mask is None else np.asarray(parent_mask, bool)
    if parents.shape != native.shape:
        raise ValueError("narrow parent geometry differs")
    # Parent membership gives context, not an independent physical vote or truth.
    evidence = contrast | polarimetric_risk | parents
    seed = echo & evidence & ~protected
    connected = np.zeros(native.shape, bool)
    measured_seed = np.zeros(native.shape, bool)
    count = 0
    for ray in range(len(z)):
        for lo, hi, measured in intervals(
            seed[ray],
            (~observed[ray] | (echo[ray] & ~protected[ray])),
            dr,
            cfg.narrow_maximum_gap_m,
            cfg.narrow_maximum_gap_fraction,
        ):
            if (
                hi - lo
            ) * dr < cfg.narrow_minimum_span_m or measured * dr < cfg.narrow_minimum_measured_m:
                continue
            count += 1
            if count > cfg.maximum_segments:
                raise ValueError("V6 narrow segment budget exceeded; no partial QC")
            # Hypothesis links may connect missing indices for labelling only.
            # No output IDs or actions are assigned to these missing samples.
            connected[ray, lo:hi] = True
            measured_seed[ray, lo:hi] = seed[ray, lo:hi]
    labels, nlabels = label_polar(connected, native)
    if nlabels > cfg.maximum_objects:
        raise ValueError("V6 narrow object budget exceeded; no partial QC")
    ids = np.zeros(native.shape, "uint32")
    kind = np.zeros(native.shape, "uint8")
    model = np.zeros(native.shape, bool)
    linked = np.zeros(native.shape, bool)
    spans = np.full(native.shape, np.nan, "float32")
    measured_m = spans.copy()
    residuals = spans.copy()
    records = []
    # find_objects gives bounded boxes rather than scanning the whole volume per label.
    from scipy import ndimage

    for identity, box in enumerate(ndimage.find_objects(labels), 1):
        if box is None:
            continue
        region = labels[box] == identity
        rr, gg = np.where(region)
        rays = rr + box[0].start
        gates = gg + box[1].start
        width = angular_width(native, np.unique(rays))
        longest = max(
            (gates[rays == ray].max() - gates[rays == ray].min() + 1) * dr
            for ray in np.unique(rays)
        )
        middle_r = float((native.ranges[gates.min()] + native.ranges[gates.max()]) / 2)
        aspect = longest / max(dr, middle_r * np.deg2rad(width))
        if width > cfg.narrow_maximum_width_deg or aspect < cfg.narrow_minimum_aspect:
            continue
        chosen = region & echo[box] & ~protected[box]
        seeds = region & measured_seed[box]
        if (
            not chosen.any()
            or seeds.sum() / max(1, chosen.sum()) < cfg.narrow_minimum_evidence_fraction
        ):
            continue
        corrected_parts = []
        shoulder_count = int((chosen & contrast[box]).sum())
        support_length = 0.0
        for ray in np.unique(rays):
            idx = gates[rays == ray]
            idx = idx[seed[ray, idx]]
            support_length = max(support_length, len(idx) * dr)
            if len(idx):
                v = z[ray, idx] - 20 * np.log10(np.maximum(native.ranges[idx], dr / 2) / 1000)
                corrected_parts.extend(np.abs(v - np.median(v)).tolist())
        p90 = float(np.percentile(corrected_parts, 90)) if corrected_parts else float("inf")
        has_gap = bool((region & ~measured_seed[box]).any())
        code = 2 if has_gap else 1
        if longest < 30000:
            code = 3
        if native.ranges[gates.min()] >= 200000:
            code = 4
        ident = len(records) + 1
        ids[box][chosen] = ident
        kind[box][chosen] = code
        linked[box][chosen & ~seeds] = True
        spans[box][chosen] = longest
        measured_m[box][chosen] = support_length
        residuals[box][chosen] = p90
        # A stringent DBZH-only hypothesis may withhold, NEVER declare confirmed.
        hypothesis = (
            support_length >= cfg.single_field_minimum_m
            and width <= cfg.single_field_maximum_width_deg
            and p90 <= cfg.narrow_model_p90_db
            and shoulder_count / max(1, int(chosen.sum())) >= 0.8
        )
        if hypothesis:
            model[box][chosen & seeds] = True
        records.append(
            dict(
                object_id=ident,
                type_code=code,
                angle_width_deg=width,
                longest_span_m=float(longest),
                maximum_measured_length_m=float(support_length),
                observed_gates=int(chosen.sum()),
                seed_gates=int(seeds.sum()),
                model_residual_p90_db=p90,
                reflectivity_only_hypothesis=bool(hypothesis),
            )
        )
    return NarrowEvidence(
        {
            "V6_NARROW_OBJECT_ID": ids,
            "V6_NARROW_CANDIDATE_MASK": (ids > 0).astype("uint8"),
            "V6_NARROW_LINKED_MASK": linked.astype("uint8"),
            "V6_NARROW_TYPE": kind,
            "V6_NARROW_MODEL_MASK": model.astype("uint8"),
            "V6_NARROW_SPAN_M": spans,
            "V6_NARROW_MEASURED_M": measured_m,
            "V6_NARROW_MODEL_P90_DB": residuals,
            "V6_SHOULDER_AVAILABLE_MASK": bg.astype("uint8"),
            "V6_SHOULDER_CONTRAST_DB": delta,
        },
        dict(
            method="native-narrow-v6",
            status="applied",
            objects=records,
            candidate_gates=int((ids > 0).sum()),
            object_count=len(records),
        ),
    )
