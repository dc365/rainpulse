"""V3 structural hypotheses and independent, bounded search envelopes.

Rough candidates use median/IQR and physical segment structure instead of the
V2 axial-standard-deviation veto. Search is not a removal operation. All
features come from the original arrays, never from a cleaned/reconstructed PPI.
"""

from __future__ import annotations

from dataclasses import dataclass

import numpy as np

from .multivariate import joint_moment_evidence
from .objects import ObjectEvidence, _background, _segments, axial_statistics


@dataclass(frozen=True)
class V3ObjectEvidence(ObjectEvidence):
    def summary(self):
        return {
            **super().summary(),
            "method": "native-polar-multivariate-v3",
            "search_gates": int(self.candidate.sum()),
            "structural_seed_gates": int(self.arrays["RFI_STRUCTURAL_SEED_MASK"].sum()),
            "core_seed_gates": int(self.arrays["RFI_CORE_SEED_MASK"].sum()),
            "periphery_gates": int(self.arrays["RFI_PERIPHERY_MASK"].sum()),
            "rough_candidate_gates": int(self.arrays["RFI_ROUGH_CANDIDATE_MASK"].sum()),
        }


def bounded_search(
    native, seed_ids, allowed, range_m, azimuth_deg, maximum_steps, *, domain_ids=None
):
    """Range and angular passes from ORIGINAL seeds, with separate distance caps.

    No convergence loop, no bridging missing gates or topology gaps. Distinct
    objects do not merge. A deterministic lowest-ID tie break labels overlap.
    A cap violation is an explicit failure, never a partial result.
    """
    if seed_ids.shape != native.shape or allowed.shape != native.shape:
        raise ValueError("search geometry differs from native data")
    if domain_ids is not None and domain_ids.shape != native.shape:
        raise ValueError("search identity domain differs from native geometry")
    range_steps = int(np.floor(range_m / native.gate_spacing_m))
    measured_gaps = (np.roll(native.azimuth, -1) - native.azimuth) % 360
    continuous = measured_gaps[(~native.gap_after) & (measured_gaps > 0)]
    angular_steps = min(
        native.shape[0] - 1,
        int(np.floor(azimuth_deg / continuous.min())) if continuous.size else 0,
    )
    if range_steps + angular_steps > maximum_steps:
        raise ValueError("V3 search step budget exceeded; reduce bounds for this resolution")
    base = np.where(allowed, seed_ids, 0).astype("uint32")
    output = base.copy()

    def accept(proposed):
        free = base == 0
        replace = free & (proposed > 0) & ((output == 0) | (proposed < output))
        output[replace] = proposed[replace]

    for direction in (-1, 1):
        front = base.copy()
        for _ in range(range_steps):
            front = np.roll(front, direction, axis=1)
            front[:, 0 if direction > 0 else -1] = 0
            front[~allowed | ((base > 0) & (base != front))] = 0
            if domain_ids is not None:
                front[(domain_ids == 0) | (domain_ids != front)] = 0
            accept(front)
    axial_result = output.copy()
    for direction in (-1, 1):
        front = axial_result.copy()
        distance = np.zeros(native.shape, "float32")
        neighbour = np.roll(np.arange(native.shape[0]), direction)
        gaps = np.abs((native.azimuth - native.azimuth[neighbour] + 180) % 360 - 180)
        good = native.geometry_good & native.geometry_good[neighbour]
        good &= ~(np.roll(native.gap_after, 1) if direction > 0 else native.gap_after)
        if not native.full_ppi:
            good[0 if direction > 0 else -1] = False
        for _ in range(angular_steps):
            distance = distance[neighbour] + gaps[:, None]
            front = front[neighbour].copy()
            front[~allowed | ~good[:, None] | (distance > azimuth_deg + 1e-6)] = 0
            front[(base > 0) & (base != front)] = 0
            if domain_ids is not None:
                front[(domain_ids == 0) | (domain_ids != front)] = 0
            accept(front)
    return output


def multivariate_objects(native, profile):
    cfg, obj = profile.rfi_refinement, profile.rfi_objects
    if cfg is None or obj is None:
        raise ValueError("V3 objects require coordinated configuration")
    pol = joint_moment_evidence(native, profile)
    values = native.fields["DBZH"]
    observed = native.field_available["DBZH"]
    dr = native.gate_spacing_m
    echo = observed & (values >= obj.minimum_echo_dbz)
    echo &= native.ranges[None, :] >= obj.minimum_range_m
    contrast, background = _background(native, obj)
    window = max(3, int(round(obj.local_window_m / dr)) | 1)
    corrected = values - 20 * np.log10(np.maximum(native.ranges, dr / 2))[None, :]
    std, support = axial_statistics(corrected, observed, window)
    axial = observed & (support >= obj.minimum_measured_support)
    anomaly = pol["RFI_POLARIMETRIC_ANOMALY_MASK"] == 1
    barrier = pol["RFI_WEATHER_BARRIER_MASK"] == 1
    # No axial-smoothness prerequisite: large variance alone neither admits nor excludes RFI.
    seed = echo & (contrast | (axial & anomaly))
    weak_echo = observed & (values >= profile.echo.no_rain_below_dbz)
    weak_echo &= native.ranges[None, :] >= obj.minimum_range_m
    bridgeable = (~observed) | (weak_echo & ~barrier & anomaly)
    intervals, by_ray = [], [[] for _ in native.azimuth]
    for ray in range(native.shape[0]):
        for lo, hi, count in _segments(
            seed[ray], bridgeable[ray], int(obj.maximum_gap_m / dr), obj.maximum_gap_fraction
        ):
            if count * dr < obj.minimum_segment_m:
                continue
            gates = np.flatnonzero(seed[ray, lo:hi]) + lo
            measured_contrast = contrast[ray, gates].mean() >= obj.minimum_background_fraction
            span = (hi - lo) * dr
            iqr = float(
                np.percentile(corrected[ray, gates], 75) - np.percentile(corrected[ray, gates], 25)
            )
            tail = max(1, len(gates) // 5)
            growth = float(
                np.median(values[ray, gates[-tail:]]) - np.median(values[ray, gates[:tail]])
            )
            strong_fraction = pol["RFI_POLARIMETRIC_STRONG_MASK"][ray, gates].mean()
            intrinsic = (
                span >= cfg.rough_minimum_span_m
                and anomaly[ray, gates].mean() >= cfg.rough_minimum_anomaly_fraction
                and iqr <= cfg.rough_maximum_corrected_iqr_db
                and (
                    growth >= obj.self_signature_minimum_growth_db
                    or strong_fraction >= cfg.rough_minimum_anomaly_fraction
                )
            )
            if not (measured_contrast or intrinsic):
                continue
            if len(intervals) >= obj.maximum_segments:
                raise ValueError("V3 segment budget exceeded")
            by_ray[ray].append(len(intervals))
            intervals.append((ray, lo, hi, bool(intrinsic)))
    parent = list(range(len(intervals)))

    def find(i):
        while parent[i] != i:
            parent[i] = parent[parent[i]]
            i = parent[i]
        return i

    for ray in range(native.shape[0]):
        other = (ray + 1) % native.shape[0]
        if native.gap_after[ray] or not (native.geometry_good[ray] and native.geometry_good[other]):
            continue
        if other == 0 and not native.full_ppi:
            continue
        a, b = by_ray[ray], by_ray[other]
        i = j = 0
        while i < len(a) and j < len(b):
            x, y = intervals[a[i]], intervals[b[j]]
            if (min(x[2], y[2]) - max(x[1], y[1])) * dr >= obj.minimum_overlap_m:
                pa, pb = find(a[i]), find(b[j])
                parent[max(pa, pb)] = min(pa, pb)
            if x[2] < y[2]:
                i += 1
            else:
                j += 1
    groups = {}
    for i in range(len(intervals)):
        groups.setdefault(find(i), []).append(i)
    ids = np.zeros(native.shape, "uint32")
    structural = np.zeros(native.shape, bool)
    own_mask = np.zeros(native.shape, bool)
    records = []
    for parts in groups.values():
        rays = np.unique([intervals[i][0] for i in parts])
        angles = np.sort(native.azimuth[rays])
        width = float(360 - np.max(np.diff(np.r_[angles, angles[0] + 360])))
        width += native.audit["azimuth_spacing_deg"]
        lo = min(intervals[i][1] for i in parts)
        hi = max(intervals[i][2] for i in parts)
        span = max((intervals[i][2] - intervals[i][1]) * dr for i in parts)
        centre = (native.ranges[lo] + native.ranges[hi - 1]) / 2
        aspect = span / max(dr, centre * np.deg2rad(width))
        if width > obj.maximum_object_width_deg or aspect < obj.minimum_radial_aspect:
            continue
        if len(records) >= obj.maximum_objects:
            raise ValueError("V3 object budget exceeded")
        identity = len(records) + 1
        for i in parts:
            ray, start, end, intrinsic = intervals[i]
            selected = seed[ray, start:end]
            member = selected | (echo[ray, start:end] & ~barrier[ray, start:end])
            ids[ray, start:end][member] = identity
            structural[ray, start:end][selected] = True
            own_mask[ray, start:end][selected] = intrinsic
        records.append(
            {
                "object_id": identity,
                "ray_count": int(len(rays)),
                "original_ray_indices": native.original_indices[rays].astype(int).tolist(),
                "azimuth_width_deg": width,
                "maximum_ray_span_m": float(span),
                "range_start_m": float(native.ranges[lo]),
                "range_end_m": float(native.ranges[hi - 1]),
                "radial_aspect": float(aspect),
                "has_self_signature": any(intervals[i][3] for i in parts),
            }
        )
    original = ids.copy()
    # A normal high-RHOHV, smooth-phase rain gate is a barrier, not a route to the next object.
    search_allowed = weak_echo & ~barrier & (anomaly | contrast)
    # Retain original hypotheses even where protected; they do NOT seed expansion through barriers.
    expanded = bounded_search(
        native,
        ids,
        search_allowed,
        cfg.search_range_m,
        cfg.search_azimuth_deg,
        cfg.maximum_search_steps,
    )
    expanded[original > 0] = original[original > 0]
    ids = expanded
    periphery = (ids > 0) & ~structural
    core = structural & (pol["RFI_POLARIMETRIC_STRONG_MASK"] == 1)
    core &= pol["RFI_SNR_RELIABLE_MASK"] == 1
    size = len(records) + 1
    observed_counts = np.bincount(ids.ravel(), minlength=size)
    core_counts = np.bincount(ids[core], minlength=size)
    periphery_counts = np.bincount(ids[periphery], minlength=size)
    for record in records:
        identity = record["object_id"]
        record.update(
            observed_candidate_gates=int(observed_counts[identity]),
            core_seed_gates=int(core_counts[identity]),
            periphery_gates=int(periphery_counts[identity]),
        )
    return V3ObjectEvidence(
        {
            **pol,
            "RFI_OBJECT_ID": ids,
            "RFI_SEARCH_MASK": (ids > 0).astype("uint8"),
            "RFI_STRUCTURAL_SEED_MASK": structural.astype("uint8"),
            "RFI_CORE_SEED_MASK": core.astype("uint8"),
            "RFI_PERIPHERY_MASK": periphery.astype("uint8"),
            "RFI_ROUGH_CANDIDATE_MASK": (
                (ids > 0) & axial & (std > obj.maximum_axial_std_db)
            ).astype("uint8"),
            "RFI_STRUCTURE_AVAILABLE_MASK": axial.astype("uint8"),
            "RFI_BACKGROUND_AVAILABLE_MASK": background.astype("uint8"),
            "RFI_SELF_SIGNATURE_MASK": own_mask.astype("uint8"),
            "RFI_LINKED_OBSERVATION_MASK": periphery.astype("uint8"),
            "RFI_BOUNDARY_OBSERVATION_MASK": ((ids > 0) & (original == 0)).astype("uint8"),
            "RFI_CONTRAST_MASK": contrast.astype("uint8"),
            "RFI_AXIAL_STD_DB": np.where(axial, std, np.nan).astype("float32"),
            "RFI_AXIAL_SUPPORT": np.where(observed, support, np.nan).astype("float32"),
        },
        tuple(records),
    )
