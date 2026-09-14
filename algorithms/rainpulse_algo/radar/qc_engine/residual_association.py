"""One bounded review of frozen anchors; new exclusions NEVER seed another pass."""

from __future__ import annotations

import numpy as np


def peripheral_review(native, cfg, *, confirmed, model_anchor, protected, footprint=False):
    shape = native.shape
    observed = native.field_available["DBZH"] & native.geometry_good[:, None]
    z, dr = native.fields["DBZH"], native.gate_spacing_m
    if any(np.shape(a) != shape for a in (confirmed, model_anchor, protected)):
        raise ValueError("peripheral source geometry differs")
    source = observed & (confirmed | model_anchor)
    inspected = np.zeros(shape, bool)
    compatible = np.zeros(shape, bool)
    source_model = np.zeros(shape, bool)
    parent_ray = np.full(shape, -1, "int32")
    parent_gate = np.full(shape, -1, "int32")
    center_distance = np.full(shape, np.nan, "float32")
    footprint_gap = np.full(shape, np.nan, "float32")
    nominal = float(native.audit["azimuth_spacing_deg"])
    gaps = (np.roll(native.azimuth, -1) - native.azimuth) % 360
    # Finite sampling cells: a missing ray never acquires the whole absent wedge.
    after = np.where(native.gap_after, nominal, np.minimum(gaps, nominal * 1.8)) / 2
    before = np.roll(after, 1)
    if not source.any():
        return _result(
            inspected,
            compatible,
            source_model,
            parent_ray,
            parent_gate,
            center_distance if footprint else None,
            footprint_gap,
        )
    nr, ng = shape
    ray_offsets = int(np.floor(cfg.peripheral_angle_deg / native.audit["azimuth_spacing_deg"]))
    gate_offsets = int(np.floor(cfg.peripheral_range_m / dr))
    # Offsets are immutable and evaluated independently from original anchors.
    for ro in sorted(range(-ray_offsets, ray_offsets + 1), key=lambda v: (abs(v), v)):
        rows = np.arange(nr)
        origin = (rows - ro) % nr
        row_ok = native.geometry_good & native.geometry_good[origin]
        if not native.full_ppi:
            row_ok &= (rows - ro >= 0) & (rows - ro < nr)
        delta = np.abs((native.azimuth - native.azimuth[origin] + 180) % 360 - 180)
        row_ok &= delta <= cfg.peripheral_angle_deg + 1e-6
        # Check EACH intermediate edge, including full-PPI seam and missing rays.
        for step in range(abs(ro)):
            edge = (rows - step - 1) % nr if ro > 0 else (rows + step) % nr
            row_ok &= ~native.gap_after[edge]
        angular_path = np.ones(shape, bool)
        if ro:
            for step in range(1, abs(ro) + 1):
                via = (rows - step * np.sign(ro)) % nr
                angular_path &= observed[via] & ~protected[via]
        for go in sorted(range(-gate_offsets, gate_offsets + 1), key=lambda v: (abs(v), v)):
            if ro == 0 and go == 0:
                continue
            a, b = max(0, go), min(ng, ng + go)
            if a >= b:
                continue
            srcg = np.arange(a, b) - go
            targetg = np.arange(a, b)
            valid = (
                row_ok[:, None]
                & source[origin[:, None], srcg]
                & observed[:, a:b]
                & ~protected[:, a:b]
                & angular_path[:, a:b]
                & (z[:, a:b] >= cfg.minimum_echo_dbz)
            )
            # A missing or trusted-weather gate is a barrier on this path.
            for step in range(1, abs(go) + 1):
                via = targetg - step * np.sign(go)
                valid &= observed[:, via] & ~protected[:, via]
            cross = native.ranges[a:b][None, :] * np.deg2rad(delta)[:, None]
            gap_angle = delta
            if footprint:
                signed = (native.azimuth - native.azimuth[origin] + 180) % 360 - 180
                source_half = np.where(signed >= 0, after[origin], before[origin])
                target_half = np.where(signed >= 0, before, after)
                gap_angle = np.maximum(0, delta - source_half - target_half)
            edge_gap = native.ranges[a:b][None, :] * np.deg2rad(gap_angle)[:, None]
            valid &= edge_gap <= cfg.peripheral_cross_range_m + 1e-6
            az = z[origin[:, None], srcg]
            is_model = model_anchor[origin[:, None], srcg]
            adjustment = 20 * np.log10(
                np.maximum(native.ranges[a:b], dr / 2) / np.maximum(native.ranges[srcg], dr / 2)
            )
            prediction = az + np.where(is_model, adjustment[None, :], 0)
            match = valid & (np.abs(z[:, a:b] - prediction) <= cfg.peripheral_difference_db)
            inspected[:, a:b] |= valid
            # Deterministic nearest-first source, prioritise compatible witnesses.
            # In footprint mode keep a model witness if ANY admissible original
            # model supports this gate. A newly reachable non-model witness must
            # not hide the model route previously used by V6.
            chosen = match & (~compatible[:, a:b] | (footprint & is_model & ~source_model[:, a:b]))
            compatible[:, a:b] |= match
            center_distance[:, a:b][chosen] = np.broadcast_to(cross, chosen.shape)[chosen]
            footprint_gap[:, a:b][chosen] = np.broadcast_to(edge_gap, chosen.shape)[chosen]
            source_model[:, a:b][chosen] = is_model[chosen]
            pr = np.broadcast_to(native.original_indices[origin, None], chosen.shape)
            pg = np.broadcast_to(srcg[None, :], chosen.shape)
            parent_ray[:, a:b][chosen] = pr[chosen]
            parent_gate[:, a:b][chosen] = pg[chosen]
    return _result(
        inspected,
        compatible,
        source_model,
        parent_ray,
        parent_gate,
        center_distance if footprint else None,
        footprint_gap,
    )


def _result(
    inspected,
    compatible,
    source_model,
    parent_ray,
    parent_gate,
    center_distance=None,
    footprint_gap=None,
):
    fields = {
        "V6_PERIPHERAL_REVIEW_MASK": inspected.astype("uint8"),
        "V6_PERIPHERAL_COMPATIBLE_MASK": compatible.astype("uint8"),
        "V6_PERIPHERAL_MODEL_SOURCE_MASK": source_model.astype("uint8"),
        "V6_PARENT_RAY": parent_ray,
        "V6_PARENT_GATE": parent_gate,
    }
    if center_distance is not None:
        fields.update(
            {
                "V61_PERIPHERAL_CENTER_DISTANCE_M": center_distance,
                "V61_PERIPHERAL_FOOTPRINT_GAP_M": footprint_gap,
            }
        )
    return fields
