"""Rescue locally narrow branches without widening the frozen V6 detector.

A broad parent is context, not permission to erase a narrow branch. Wide
intersections are left out of this route unless independent measured shoulders
still establish a locally narrow structure. All downstream measurement gates
remain unchanged. This is a project supplement, not a bRopo implementation.
"""

from enum import IntFlag

import numpy as np
from scipy import ndimage

from .narrow_spike import NarrowEvidence, narrow_candidates, shoulder_contrast
from .polar_objects import label_polar
from .segments import intervals


class NarrowStage(IntFlag):
    PROTECTED = 1
    NO_EVIDENCE = 2
    SHORT_SPAN = 4
    SHORT_MEASURED = 8
    LOCAL_WIDTH = 16
    ASPECT = 32
    EVIDENCE_FRACTION = 64
    CANDIDATE = 128
    NO_SHAPE_MODEL = 256


def local_widths(mask, native):
    """Per-range connected angular widths; native gaps and invalid rays split runs.

    Two O(ray*gate) passes, rather than a full-volume scan for every object.
    Full-PPI seam joining never crosses a declared missing-azimuth edge.
    """
    m = np.asarray(mask, bool) & native.geometry_good[:, None]
    if m.shape != native.shape:
        raise ValueError("local width geometry differs")
    nr, ng = m.shape
    start = np.empty(m.shape, "int32")
    end = np.empty(m.shape, "int32")
    cur = np.zeros(ng, "int32")
    for ray in range(nr):
        reset = ~m[ray] if ray == 0 else (~m[ray] | ~m[ray - 1] | native.gap_after[ray - 1])
        if ray == 0:
            reset[:] = True
        cur = np.where(reset, ray, cur)
        start[ray] = cur
    cur = np.full(ng, nr - 1, "int32")
    for ray in range(nr - 1, -1, -1):
        reset = ~m[ray] if ray == nr - 1 else (~m[ray] | ~m[ray + 1] | native.gap_after[ray])
        if ray == nr - 1:
            reset[:] = True
        cur = np.where(reset, ray, cur)
        end[ray] = cur
    nominal = float(native.audit["azimuth_spacing_deg"])
    angles = np.rad2deg(np.unwrap(np.deg2rad(native.azimuth)))
    width = angles[end] - angles[start] + nominal
    if native.full_ppi and nr > 1 and not native.gap_after[-1]:
        seam = m[0] & m[-1] & (start[-1] > 0)
        seam_gap = (native.azimuth[0] - native.azimuth[-1]) % 360
        joined = width[0] + width[-1] + seam_gap - nominal
        touch = (start == 0) | (end == nr - 1)
        width = np.where(touch & seam[None, :], joined[None, :], width)
    return np.where(m, np.minimum(width, 360), np.nan).astype("float32")


def repaired_narrow_candidates(native, cfg, *, polarimetric_risk, protected, parent_mask=None):
    old = narrow_candidates(
        native,
        cfg,
        polarimetric_risk=polarimetric_risk,
        protected=protected,
        parent_mask=parent_mask,
    )
    # Preserve every original candidate/model result; the repair only adds routes.
    arrays = {key: value.copy() for key, value in old.arrays.items()}
    z, dr = native.fields["DBZH"], native.gate_spacing_m
    observed = native.field_available["DBZH"] & native.geometry_good[:, None]
    echo = observed & (z >= cfg.minimum_echo_dbz) & (native.ranges[None, :] >= cfg.minimum_range_m)
    parents = np.zeros(native.shape, bool) if parent_mask is None else np.asarray(parent_mask, bool)
    contrast, _, _ = shoulder_contrast(native, cfg)
    evidence = contrast | polarimetric_risk | parents
    seed = echo & evidence & ~protected
    reason = np.zeros(native.shape, "uint32")
    reason[echo & protected] |= int(NarrowStage.PROTECTED)
    reason[echo & ~evidence & ~protected] |= int(NarrowStage.NO_EVIDENCE)
    stage = np.zeros(native.shape, bool)
    for ray in range(len(z)):
        for lo, hi, measured in intervals(
            seed[ray],
            ~observed[ray] | (echo[ray] & ~protected[ray]),
            dr,
            cfg.narrow_maximum_gap_m,
            cfg.narrow_maximum_gap_fraction,
        ):
            if (hi - lo) * dr < cfg.narrow_minimum_span_m:
                reason[ray, lo:hi] |= int(NarrowStage.SHORT_SPAN)
                continue
            if measured * dr < cfg.narrow_minimum_measured_m:
                reason[ray, lo:hi] |= int(NarrowStage.SHORT_MEASURED)
                continue
            stage[ray, lo:hi] = True
    width = local_widths(stage, native)
    shoulder_width = local_widths(stage & contrast, native)
    small = width <= cfg.narrow_maximum_width_deg
    corridor = shoulder_width <= cfg.narrow_maximum_width_deg
    allowed = stage & (small | corridor)
    effective_width = np.where(small, width, shoulder_width)
    reason[stage & ~allowed & observed] |= int(NarrowStage.LOCAL_WIDTH)
    # Requalify physical support after cutting a wide intersection. A wide or
    # protected interval cannot be silently bridged by the second segmentation.
    connected = np.zeros(native.shape, bool)
    seeds = seed & allowed
    segment_count = 0
    for ray in range(len(z)):
        for lo, hi, count in intervals(
            seeds[ray],
            allowed[ray] & (~observed[ray] | (echo[ray] & ~protected[ray])),
            dr,
            cfg.narrow_maximum_gap_m,
            cfg.narrow_maximum_gap_fraction,
        ):
            if (hi - lo) * dr < cfg.narrow_minimum_span_m:
                reason[ray, lo:hi] |= int(NarrowStage.SHORT_SPAN)
                continue
            if count * dr < cfg.narrow_minimum_measured_m:
                reason[ray, lo:hi] |= int(NarrowStage.SHORT_MEASURED)
                continue
            segment_count += 1
            if segment_count > cfg.maximum_segments:
                raise ValueError("6.1 local narrow segment budget exceeded")
            connected[ray, lo:hi] = True
    labels, count = label_polar(connected, native)
    if count > cfg.maximum_objects:
        raise ValueError("6.1 local narrow object budget exceeded")
    extra = np.zeros(native.shape, bool)
    records = list(old.summary["objects"])
    for label, box in enumerate(ndimage.find_objects(labels), 1):
        if box is None:
            continue
        region = labels[box] == label
        chosen = region & echo[box] & ~protected[box]
        supported = region & seeds[box]
        if not supported.any():
            continue
        rr, gg = np.where(region)
        rays, gates = rr + box[0].start, gg + box[1].start
        w = float(np.nanmax(effective_width[box][region]))
        longest = max(
            (gates[rays == ray].max() - gates[rays == ray].min() + 1) * dr
            for ray in np.unique(rays)
        )
        middle = (native.ranges[gates.min()] + native.ranges[gates.max()]) / 2
        if longest / max(dr, middle * np.deg2rad(w)) < cfg.narrow_minimum_aspect:
            reason[box][chosen] |= int(NarrowStage.ASPECT)
            continue
        if supported.sum() / max(1, chosen.sum()) < cfg.narrow_minimum_evidence_fraction:
            reason[box][chosen] |= int(NarrowStage.EVIDENCE_FRACTION)
            continue
        deviations = []
        support_length = 0.0
        for ray in np.unique(rays):
            idx = gates[rays == ray]
            idx = idx[seeds[ray, idx]]
            support_length = max(support_length, len(idx) * dr)
            if len(idx):
                value = z[ray, idx] - 20 * np.log10(np.maximum(native.ranges[idx], dr / 2) / 1000)
                deviations.extend(np.abs(value - np.median(value)).tolist())
        p90 = float(np.percentile(deviations, 90))
        model = (
            support_length >= cfg.single_field_minimum_m
            and w <= cfg.single_field_maximum_width_deg
            and p90 <= cfg.narrow_model_p90_db
            and (chosen & contrast[box]).sum() / max(1, chosen.sum()) >= 0.8
        )
        reason[box][chosen] |= int(NarrowStage.CANDIDATE)
        if not model:
            reason[box][chosen] |= int(NarrowStage.NO_SHAPE_MODEL)
        added = chosen & (arrays["V6_NARROW_OBJECT_ID"][box] == 0)
        if not added.any():
            continue
        if len(records) >= cfg.maximum_objects:
            raise ValueError("6.1 combined narrow object budget exceeded")
        identity = len(records) + 1
        code = 2 if (region & ~seeds[box]).any() else 1
        if longest < 30000:
            code = 3
        if native.ranges[gates.min()] >= 200000:
            code = 4
        for key, value in (
            ("V6_NARROW_OBJECT_ID", identity),
            ("V6_NARROW_TYPE", code),
            ("V6_NARROW_SPAN_M", longest),
            ("V6_NARROW_MEASURED_M", support_length),
            ("V6_NARROW_MODEL_P90_DB", p90),
        ):
            arrays[key][box][added] = value
        arrays["V6_NARROW_LINKED_MASK"][box][added & ~supported] = 1
        if model:
            arrays["V6_NARROW_MODEL_MASK"][box][added & supported] = 1
        extra[box] |= added
        records.append(
            dict(
                object_id=identity,
                type_code=code,
                angle_width_deg=w,
                longest_span_m=float(longest),
                maximum_measured_length_m=float(support_length),
                observed_gates=int(added.sum()),
                seed_gates=int((added & supported).sum()),
                model_residual_p90_db=p90,
                reflectivity_only_hypothesis=bool(model),
                origin="local_width_branch_repair",
                geometric_domain_gates=int(chosen.sum()),
            )
        )
    arrays["V6_NARROW_CANDIDATE_MASK"] = (arrays["V6_NARROW_OBJECT_ID"] > 0).astype("uint8")
    reason[~observed] = 0
    arrays.update(
        {
            "V61_NARROW_LOCAL_ADDITION_MASK": extra.astype("uint8"),
            "V61_NARROW_LOCAL_WIDTH_DEG": effective_width,
            "V61_NARROW_STAGE_REASON": reason,
        }
    )
    return NarrowEvidence(
        arrays,
        dict(
            method="native-narrow-v6.1",
            status="applied",
            objects=records,
            object_count=len(records),
            candidate_gates=int(arrays["V6_NARROW_CANDIDATE_MASK"].sum()),
            original_candidate_gates=old.summary["candidate_gates"],
            rescued_gates=int(extra.sum()),
            local_stage_counts={b.name: int(((reason & int(b)) != 0).sum()) for b in NarrowStage},
            semantics="local_width_repair_not_automatic_rejection",
        ),
    )
