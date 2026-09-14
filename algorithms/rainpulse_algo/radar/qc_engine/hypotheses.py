"""Bounded raw-polar hypotheses; structural edges NEVER propagate gate actions.

Not a bRopo/RDD implementation, not a calibrated probability model. Proposal
length is separate from measured group support. Width is local in range, so
an intersection cannot erase the identity of its tails. Missing/clear-air and
known-weather intervals never contribute observation support.
"""
from dataclasses import dataclass
from enum import IntFlag

import numpy as np

from ..qc_geometry import nearest_azimuth_matches
from .crossradar import measurement_capability, pol_corroboration
from .decision import Action, Decision
from .narrow_local import local_widths
from .segments import intervals


class GraphReason(IntFlag):
    PROTECTED = 1
    NO_EVIDENCE = 2
    SHORT_ATOM = 4
    WIDE_INTERSECTION = 8
    SHORT_GROUP = 16
    EXCESS_GAP = 32
    REVIEW = 64
    SHAPE_MODEL = 128
    MEASUREMENT_UNAVAILABLE = 256
    CONFIRMED = 512
    QUARANTINED = 1024
    UNRESOLVED = 2048
    SINGLE_SHOULDER_ONLY = 4096


@dataclass
class Hypotheses:
    arrays: dict
    summary: dict


def _shoulders(native, cfg, frozen_donor_usable):
    """Both shoulders must be observed and usable; use MAX, never a dry fill.

    One-sided support is recorded only; it cannot pass the shape-isolation test.
    Different offsets are alternative observations, not independent votes.
    """
    z, observed = native.fields["DBZH"], native.field_available["DBZH"]
    nr = native.shape[0]
    spacing = native.audit["azimuth_spacing_deg"]
    available = np.zeros(native.shape, bool)
    one = available.copy()
    contrast = np.full(native.shape, np.nan, "float32")
    left_source = np.full(native.shape, -1, "int32")
    right_source = left_source.copy()
    rows = np.arange(nr)
    for angle in cfg.shoulder_offsets_deg:
        left, dl, ol = nearest_azimuth_matches((native.azimuth - angle) % 360, native.azimuth)
        right, dr, ori = nearest_azimuth_matches((native.azimuth + angle) % 360, native.azimuth)
        ok = ol & ori & (dl <= 0.55 * spacing) & (dr <= 0.55 * spacing)
        ok &= native.geometry_good & native.geometry_good[left] & native.geometry_good[right]
        ok &= (left != rows) & (right != rows)
        # Verify every azimuth edge; declared missing rays are not clean flanks.
        for ray in np.flatnonzero(ok):
            for start, end in ((left[ray], ray), (ray, right[ray])):
                steps = (end - start) % nr
                if (not native.full_ppi and end < start) or steps == 0:
                    ok[ray] = False
                    break
                edges = (start + np.arange(steps)) % nr
                if np.any(native.gap_after[edges]):
                    ok[ray] = False
                    break
        a = observed[left] & frozen_donor_usable[left]
        b = observed[right] & frozen_donor_usable[right]
        supported = ok[:, None] & observed & a & b
        one |= ok[:, None] & observed & (a ^ b)
        delta = z - np.maximum(z[left], z[right])
        better = supported & (~available | (delta > contrast))
        contrast[better] = delta[better]
        left_source[better] = np.broadcast_to(native.original_indices[left, None], z.shape)[better]
        right_source[better] = np.broadcast_to(native.original_indices[right, None], z.shape)[better]
        available |= supported
    return contrast, available, one, left_source, right_source


def build_hypotheses(native, baseline, profile, *, weather_support=None):
    cfg, old = profile.evidence_graph, profile.residual
    shape = native.shape
    observed = native.field_available["DBZH"] & native.geometry_good[:, None]
    z, dr = native.fields["DBZH"], native.gate_spacing_m
    pol_bad, _, _ = pol_corroboration(native, profile)
    _, _, _, reliable = measurement_capability(native, old)
    pol_bad &= reliable & observed
    weather = np.zeros(shape, bool)
    if weather_support is not None:
        if np.shape(weather_support) != shape:
            raise ValueError("V7 graph weather geometry differs")
        weather = np.isfinite(weather_support) & (weather_support >= profile.context.strong_support)
    protected = weather & ~pol_bad
    echo = observed & (z >= old.minimum_echo_dbz) & (native.ranges[None, :] >= old.minimum_range_m)
    source = baseline.arrays
    # This mask is frozen BEFORE new graph decisions. No recursive clean-up.
    donor = observed & (source["QC_ACTION"] == Action.KEEP)
    donor &= source["RFI_QUARANTINE_MASK"] == 0
    delta, flank, one, left, right = _shoulders(native, cfg, donor)
    contrast = flank & (delta >= old.narrow_minimum_contrast_db)
    parent = (source.get("RFI_OBJECT_ID", np.zeros(shape)) > 0) | (
        source.get("V5_RANGE_CANDIDATE_MASK", np.zeros(shape)) == 1
    )
    raw_seed = echo & (contrast | parent | pol_bad) & ~protected
    width = local_widths(raw_seed, native)
    shoulder_width = local_widths(raw_seed & contrast, native)
    corridor = contrast & np.isfinite(shoulder_width) & (shoulder_width <= old.narrow_maximum_width_deg)
    allowed = raw_seed & ((width <= cfg.maximum_bundle_width_deg) | corridor)
    effective_width = np.where(corridor, np.minimum(width, shoulder_width), width)
    reasons = np.zeros(shape, "uint32")
    reasons[echo & protected] |= int(GraphReason.PROTECTED)
    reasons[echo & ~raw_seed & ~protected] |= int(GraphReason.NO_EVIDENCE)
    reasons[raw_seed & ~allowed] |= int(GraphReason.WIDE_INTERSECTION)
    reasons[echo & one & ~flank] |= int(GraphReason.SINGLE_SHOULDER_ONLY)
    proposal = np.zeros(shape, bool)
    review = proposal.copy()
    model = proposal.copy()
    ids = np.zeros(shape, "uint32")
    kinds = np.zeros(shape, "uint8")
    nodes, edges, groups = [], [], []
    for ray in range(shape[0]):
        # Atoms do not include excluded intersections. Retain actual measured indices.
        row = allowed[ray]
        bounds = np.diff(np.r_[False, row, False].astype("int8"))
        atoms = []
        for lo, hi in zip(np.flatnonzero(bounds == 1), np.flatnonzero(bounds == -1), strict=True):
            if (hi - lo) * dr < cfg.proposal_minimum_measured_m:
                reasons[ray, lo:hi] |= int(GraphReason.SHORT_ATOM)
                continue
            if len(nodes) >= cfg.maximum_nodes:
                raise ValueError("V7 node budget exceeded; no partial result")
            node = {"node_id": len(nodes) + 1, "ray": int(native.original_indices[ray]),
                    "gate_start": int(lo), "gate_end_exclusive": int(hi),
                    "measured_m": float((hi-lo)*dr)}
            nodes.append(node)
            atoms.append((int(lo), int(hi), node["node_id"]))
            proposal[ray, lo:hi] = True
        batches = []
        for atom in atoms:
            if not batches:
                batches.append([atom])
                continue
            prev = batches[-1][-1]
            lo, hi = prev[1], atom[0]
            gap = (hi - lo) * dr
            span = (atom[1] - batches[-1][0][0]) * dr
            measured = sum((b-a)*dr for a,b,_ in batches[-1]) + (atom[1]-atom[0])*dr
            # Unknown *measured echo* or nodata may link identity. Clear-air and
            # positively supported weather are barriers. No action on the link.
            barrier = protected[ray, lo:hi] | (observed[ray, lo:hi] & ~echo[ray, lo:hi])
            compatible = gap <= cfg.maximum_structure_gap_m and not np.any(barrier)
            compatible &= (span - measured) / max(span, dr) <= cfg.maximum_structure_gap_fraction
            if compatible:
                if len(edges) >= cfg.maximum_edges:
                    raise ValueError("V7 edge budget exceeded; no partial result")
                edges.append({"from": prev[2], "to": atom[2], "type": "structure_link",
                              "gap_m": float(gap), "action_propagation": False})
                batches[-1].append(atom)
            else:
                batches.append([atom])
        for batch in batches:
            idx = np.concatenate([np.arange(lo, hi) for lo, hi, _ in batch])
            span = (idx[-1] - idx[0] + 1) * dr
            measured = len(idx) * dr
            if span < old.narrow_minimum_span_m or measured < old.narrow_minimum_measured_m:
                reasons[ray, idx] |= int(GraphReason.SHORT_GROUP)
                continue
            w = float(np.nanmax(effective_width[ray, idx]))
            middle = float((native.ranges[idx[0]] + native.ranges[idx[-1]]) / 2)
            if span / max(dr, middle*np.deg2rad(w)) < old.narrow_minimum_aspect:
                reasons[ray, idx] |= int(GraphReason.WIDE_INTERSECTION)
                continue
            corrected = z[ray, idx] - 20*np.log10(np.maximum(native.ranges[idx], dr/2)/1000)
            p90 = float(np.percentile(np.abs(corrected - np.median(corrected)), 90))
            shoulder_fraction = float(contrast[ray, idx].mean())
            shape_model = (measured >= old.single_field_minimum_m
                           and w <= old.single_field_maximum_width_deg
                           and p90 <= old.narrow_model_p90_db
                           and shoulder_fraction >= cfg.minimum_shoulder_fraction)
            identity = len(groups) + 1
            ids[ray, idx] = identity
            kind = 2 if w > old.single_field_maximum_width_deg else 1
            if len(batch) > 1:
                kind = 3
            if native.ranges[idx[0]] >= 200000:
                kind = 4
            kinds[ray, idx] = kind
            review[ray, idx] = True
            model[ray, idx] = shape_model
            reasons[ray, idx] |= int(GraphReason.REVIEW)
            if shape_model:
                reasons[ray, idx] |= int(GraphReason.SHAPE_MODEL)
            groups.append({"hypothesis_id": identity, "node_ids": [n for _,_,n in batch],
                           "kind_code": kind, "measured_m": float(measured),
                           "span_m": float(span), "width_deg": w,
                           "model_p90_db": p90, "reliable_shoulder_fraction": shoulder_fraction,
                           "shape_isolation_hypothesis": bool(shape_model)})
    reasons[review & ~reliable] |= int(GraphReason.MEASUREMENT_UNAVAILABLE)
    reasons[~observed] = 0
    return Hypotheses({
        "V7_GRAPH_PROPOSAL_MASK": proposal.astype("uint8"),
        "V7_GRAPH_REVIEW_MASK": review.astype("uint8"),
        "V7_GRAPH_MODEL_MASK": model.astype("uint8"),
        "V7_HYPOTHESIS_ID": ids, "V7_HYPOTHESIS_KIND": kinds,
        "V7_GRAPH_STAGE_REASON": reasons,
        "V7_SHOULDER_AVAILABLE_MASK": flank.astype("uint8"),
        "V7_SHOULDER_LEFT_RAY": left, "V7_SHOULDER_RIGHT_RAY": right,
    }, {"method": cfg.method, "nodes": nodes, "edges": edges, "groups": groups,
        "proposal_gates": int(proposal.sum()), "review_gates": int(review.sum()),
        "reason_counts": {b.name: int(((reasons & int(b)) != 0).sum()) for b in GraphReason},
        "meaning": "structural_hypothesis_not_weather_truth", "operational_eligible": False})


def arbitrate_hypotheses(native, baseline: Decision, graph: Hypotheses, profile, *, weather_support=None):
    cfg = profile.evidence_graph
    observed = native.field_available["DBZH"] & native.geometry_good[:, None]
    _, _, _, reliable = measurement_capability(native, profile.residual)
    pol_bad, _, _ = pol_corroboration(native, profile)
    pol_bad &= reliable & observed
    weather = np.zeros(native.shape, bool)
    if weather_support is not None:
        if np.shape(weather_support) != native.shape:
            raise ValueError("V7 arbitration weather geometry differs")
        weather = np.isfinite(weather_support) & (weather_support >= profile.context.strong_support)
    review = graph.arrays["V7_GRAPH_REVIEW_MASK"] == 1
    unknown_platform = baseline.arrays.get("V5_RANGE_MODEL_CODE", np.zeros(native.shape)) == 3
    confirm = review & pol_bad & ~unknown_platform & observed
    isolate = ((graph.arrays["V7_GRAPH_MODEL_MASK"] == 1) & cfg.quarantine_shape_models
               & ~weather & ~confirm & observed)
    old_reject = baseline.arrays["QC_ACTION"] == Action.REJECT
    old_q = baseline.arrays["RFI_QUARANTINE_MASK"] == 1
    reject = old_reject | confirm
    quarantine = (old_q | isolate) & ~reject
    arrays = {k: v.copy() for k, v in baseline.arrays.items()}
    arrays.update({k: v.copy() for k, v in graph.arrays.items()})
    flags, quality = baseline.flags.copy(), baseline.quality.copy()
    arrays["QC_ACTION"][quarantine] = Action.DOWNWEIGHT
    arrays["QC_ACTION"][reject] = Action.REJECT
    flags[confirm] |= profile.flag_masks["RADIAL_INTERFERENCE"] | profile.flag_masks["NON_METEOROLOGICAL"]
    flags[confirm | isolate] |= profile.flag_masks["LOW_QUALITY"]
    quality[isolate] = np.minimum(quality[isolate], cfg.quarantine_quality)
    quality[confirm] = 0
    allowed = observed & ~reject & ~quarantine
    for name in ("REFLECTIVITY_TRUST_MASK", "QPE_ELIGIBLE_MASK", "RHOHV_TRUST_MASK",
                 "ZDR_TRUST_MASK", "PHIDP_TRUST_MASK", "VR_TRUST_MASK", "SW_TRUST_MASK", "SNR_TRUST_MASK"):
        arrays[name] &= allowed.astype("uint8")
    arrays["DBZH_USABLE"] = np.where(arrays["QPE_ELIGIBLE_MASK"] == 1,
                                    native.fields["DBZH"], np.nan).astype("float32")
    arrays["RFI_QUARANTINE_MASK"] = quarantine.astype("uint8")
    arrays["RFI_RISK_STATE"][quarantine] = 2
    arrays["RFI_RISK_STATE"][confirm] = 3
    arrays["RFI_MIXED_MASK"] |= (weather & confirm).astype("uint8")
    reasons = arrays["V7_GRAPH_STAGE_REASON"]
    reasons[confirm] |= int(GraphReason.CONFIRMED)
    reasons[isolate] |= int(GraphReason.QUARANTINED)
    reasons[review & ~confirm & ~isolate] |= int(GraphReason.UNRESOLVED)
    arrays["V7_CONFIRMED_ADDITION_MASK"] = (confirm & ~old_reject).astype("uint8")
    arrays["V7_QUARANTINED_ADDITION_MASK"] = (isolate & ~old_reject & ~old_q).astype("uint8")
    return Decision(arrays, flags, quality)
