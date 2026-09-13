"""Bounded native-polar RFI objects. Hypothesis links are never new observations.

This is a versioned, uncalibrated regional supplement to Py-ART/wradlib, not a
copy or an implementation claim of RADVOL/SPIKE. Library baselines remain intact.
"""

from __future__ import annotations

from dataclasses import dataclass

import numpy as np
from scipy import ndimage

from ..qc_geometry import nearest_azimuth_matches
from .adapters import NativeSweep
from .profile import RFIObjectConfig


@dataclass(frozen=True)
class ObjectEvidence:
    arrays: dict[str, np.ndarray]
    records: tuple[dict, ...]

    @property
    def candidate(self):
        return self.arrays["RFI_OBJECT_ID"] > 0

    def summary(self):
        return {
            "method": "native-polar-objects-v2",
            "status": "applied",
            "role": "structure_evidence_not_standalone_reject",
            "object_count": len(self.records),
            "candidate_gates": int(self.candidate.sum()),
            "objects": list(self.records),
        }


def axial_statistics(values, available, window):
    """Weighted *statistics* only. The zero working copy is never a measurement."""
    weight = ndimage.uniform_filter1d(available.astype(float), window, axis=1, mode="constant")
    work = np.where(available, values, 0.0)
    mean = ndimage.uniform_filter1d(work, window, axis=1, mode="constant")
    square = ndimage.uniform_filter1d(work * work, window, axis=1, mode="constant")
    mean = np.divide(mean, weight, out=np.zeros_like(mean), where=weight > 0)
    variance = np.divide(square, weight, out=np.zeros_like(square), where=weight > 0) - mean**2
    # Truncate stencils at the actual range axis; do not invent off-domain observations.
    domain = ndimage.uniform_filter1d(
        np.ones_like(values, dtype=float), window, axis=1, mode="constant"
    )
    fraction = np.divide(weight, domain, out=np.zeros_like(weight), where=domain > 0)
    return np.sqrt(np.maximum(variance, 0)), np.clip(fraction, 0, 1)


def _runs(row):
    edges = np.diff(np.r_[False, row, False].astype("int8"))
    return list(zip(np.flatnonzero(edges == 1), np.flatnonzero(edges == -1), strict=True))


def _segments(seed, bridgeable, maximum_gap, maximum_fraction):
    """A bounded single pass; long/cumulatively excessive holes split objects."""
    runs = _runs(seed)
    if not runs:
        return []
    result = []
    start, end = runs[0]
    measured = end - start
    for a, b in runs[1:]:
        gap = a - end
        proposed_measured = measured + b - a
        fraction = 1 - proposed_measured / (b - start)
        if gap <= maximum_gap and bridgeable[end:a].all() and fraction <= maximum_fraction:
            end, measured = b, proposed_measured
        else:
            result.append((int(start), int(end), int(measured)))
            start, end, measured = a, b, b - a
    result.append((int(start), int(end), int(measured)))
    return result


def _background(native, config):
    observed = native.field_available["DBZH"]
    values = native.fields["DBZH"]
    contrast = np.zeros(native.shape, bool)
    available = np.zeros(native.shape, bool)
    spacing = native.audit["azimuth_spacing_deg"]
    # Track native topology, not a guessed azimuth array wrap.
    component = np.cumsum(np.r_[0, native.gap_after[:-1] | ~native.geometry_good[:-1]])
    for offset in config.azimuth_offsets_deg:
        left, dl, ol = nearest_azimuth_matches((native.azimuth - offset) % 360, native.azimuth)
        right, dr, ore = nearest_azimuth_matches((native.azimuth + offset) % 360, native.azimuth)
        rows = ol & ore & (dl <= spacing * 0.55) & (dr <= spacing * 0.55)
        rows &= (left != np.arange(native.shape[0])) & (right != np.arange(native.shape[0]))
        if not native.full_ppi:
            rows &= (component[left] == component) & (component[right] == component)
            # For a sector, even small source indexing wraps cannot become a neighbour.
            rows &= (left < np.arange(native.shape[0])) & (right > np.arange(native.shape[0]))
        good = observed & observed[left] & observed[right] & rows[:, None]
        available |= good
        contrast |= good & (
            values - np.maximum(values[left], values[right]) >= config.minimum_contrast_db
        )
    return contrast, available


def radial_objects(native: NativeSweep, config: RFIObjectConfig) -> ObjectEvidence:
    """Segment association + neighbouring-segment graph, with deterministic local IDs."""
    shape = native.shape
    dr = native.gate_spacing_m
    values = native.fields["DBZH"]
    observed = native.field_available["DBZH"]
    echo = observed & (values >= config.minimum_echo_dbz)
    echo &= native.ranges[None, :] >= config.minimum_range_m
    rho = native.fields.get("RHOHV", np.full(shape, np.nan))
    rho_ok = native.field_available.get("RHOHV", np.zeros(shape, bool))
    low = rho_ok & (rho < config.suspect_rhohv)
    protected = rho_ok & (rho >= config.protected_rhohv)
    window = max(3, int(round(config.local_window_m / dr)) | 1)
    corrected = values - 20 * np.log10(np.maximum(native.ranges, dr / 2))[None, :]
    std, support = axial_statistics(corrected, observed, window)
    axial = observed & (support >= config.minimum_measured_support)
    stable = axial & (std <= config.maximum_axial_std_db)
    contrast, background = _background(native, config)
    # The second route uses positive within-object measurements, not missing background.
    seed = echo & stable & (contrast | low)
    maximum_gap = int(np.floor(config.maximum_gap_m / dr))
    intervals = []
    by_ray = [[] for _ in native.azimuth]
    for ray in range(shape[0]):
        for start, end, count in _segments(
            seed[ray], ~protected[ray], maximum_gap, config.maximum_gap_fraction
        ):
            if count * dr < config.minimum_segment_m:
                continue
            idx = np.flatnonzero(seed[ray, start:end]) + start
            has_contrast = bool(contrast[ray, idx].mean() >= config.minimum_background_fraction)
            tail = max(1, len(idx) // 5)
            self_signature = bool(
                (end - start) * dr >= config.self_signature_minimum_span_m
                and np.std(corrected[ray, idx]) <= config.self_signature_maximum_std_db
                and np.median(values[ray, idx[-tail:]]) - np.median(values[ray, idx[:tail]])
                >= config.self_signature_minimum_growth_db
                and low[ray, idx].mean() >= config.self_signature_minimum_low_rho_fraction
            )
            if not (has_contrast or self_signature):
                continue
            if len(intervals) >= config.maximum_segments:
                raise ValueError("RFI object segment budget exceeded; no partial QC is published")
            by_ray[ray].append(len(intervals))
            intervals.append((ray, start, end, count, self_signature, has_contrast))
    parents = list(range(len(intervals)))

    def find(i):
        while parents[i] != i:
            parents[i] = parents[parents[i]]
            i = parents[i]
        return i

    def join(a, b):
        a, b = find(a), find(b)
        parents[max(a, b)] = min(a, b)

    for ray in range(shape[0]):
        other = (ray + 1) % shape[0]
        if (
            native.gap_after[ray]
            or not native.geometry_good[ray]
            or not native.geometry_good[other]
            or (other == 0 and not native.full_ppi)
        ):
            continue
        # The two sorted interval lists are disjoint on each ray: linear sweep, not N^2.
        a, b = by_ray[ray], by_ray[other]
        i = j = 0
        while i < len(a) and j < len(b):
            x, y = intervals[a[i]], intervals[b[j]]
            overlap = min(x[2], y[2]) - max(x[1], y[1])
            if overlap * dr >= config.minimum_overlap_m:
                join(a[i], b[j])
            if x[2] < y[2]:
                i += 1
            else:
                j += 1
    groups = {}
    for i in range(len(intervals)):
        groups.setdefault(find(i), []).append(i)
    ids = np.zeros(shape, "uint32")
    self_mask = np.zeros(shape, bool)
    linked_mask = np.zeros(shape, bool)
    boundary_mask = np.zeros(shape, bool)
    records = []
    for parts in groups.values():
        rays = np.unique([intervals[i][0] for i in parts])
        angles = np.sort(native.azimuth[rays])
        angular = float(360 - np.max(np.diff(np.r_[angles, angles[0] + 360])))
        angular += native.audit["azimuth_spacing_deg"]
        start = min(intervals[i][1] for i in parts)
        end = max(intervals[i][2] for i in parts)
        # Use the longest actual ray segment, not a diagonal chain's bounding-box length.
        span = max((intervals[i][2] - intervals[i][1]) * dr for i in parts)
        centre = float((native.ranges[start] + native.ranges[end - 1]) / 2)
        cross_width = max(dr, centre * np.deg2rad(angular))
        if (
            angular > config.maximum_object_width_deg
            or span / cross_width < config.minimum_radial_aspect
        ):
            continue
        if len(records) >= config.maximum_objects:
            raise ValueError("RFI object budget exceeded; no partial QC is published")
        identity = len(records) + 1
        marked = 0
        maximum_hole = 0
        source_count = total_span_gates = 0
        for i in parts:
            ray, lo, hi, count, own, _ = intervals[i]
            # Object membership never includes a missing gate.
            chosen = seed[ray, lo:hi]
            # Bounded links may contain actual but weaker observations. They may be
            # quarantined; they are NOT immediate hard-rejection seeds. Missing stays ID=0.
            linked = ~chosen & echo[ray, lo:hi] & ~protected[ray, lo:hi]
            members = chosen | linked
            ids[ray, lo:hi][members] = identity
            linked_mask[ray, lo:hi][linked] = True
            self_mask[ray, lo:hi][chosen] = own
            marked += int(members.sum())
            source_count += count
            total_span_gates += hi - lo
            maximum_hole = max(maximum_hole, max((b - a for a, b in _runs(~chosen)), default=0))
        records.append(
            {
                "object_id": identity,
                "ray_count": int(len(rays)),
                "original_ray_indices": [int(native.original_indices[x]) for x in rays],
                "azimuth_width_deg": angular,
                "range_start_m": float(native.ranges[start]),
                "range_end_m": float(native.ranges[end - 1]),
                "maximum_ray_span_m": float(span),
                "radial_aspect": float(span / cross_width),
                "observed_candidate_gates": marked,
                "measured_seed_length_m": float(source_count * dr),
                "linked_observed_gates": int(marked - source_count),
                "link_gap_fraction": 1 - source_count / total_span_gates,
                "maximum_link_gap_m": maximum_hole * dr,
                "has_self_signature": any(intervals[i][4] for i in parts),
                "has_measured_background_contrast": any(intervals[i][5] for i in parts),
            }
        )
    # Recover only a bounded measured edge around accepted objects. Statistics across a
    # sharp signal/clear-air boundary can fail although the measured boundary gate is
    # consistent with the accepted axial shape. This is NOT a new hard-rejection seed.
    # A bounded short missing link changes object identity only; missing stays ID=0.
    # Stop at a long/excessive hole, a healthy value or any existing object.
    limit = min(int(config.boundary_extension_m / dr), window // 2)
    original_ids = ids.copy()
    for ray in range(shape[0]):
        for identity in np.unique(original_ids[ray]):
            if identity == 0:
                continue
            locations = np.flatnonzero(original_ids[ray] == identity)
            for direction, origin in ((-1, locations[0]), (1, locations[-1])):
                anchors = locations[:window] if direction < 0 else locations[-window:]
                expected = float(np.median(corrected[ray, anchors]))
                missing_run = missing_total = 0
                for step in range(1, limit + 1):
                    gate = origin + direction * step
                    if not 0 <= gate < shape[1] or ids[ray, gate] != 0:
                        break
                    if not observed[ray, gate]:
                        missing_run += 1
                        missing_total += 1
                        if (
                            missing_run > maximum_gap
                            or missing_total > limit * config.maximum_gap_fraction
                        ):
                            break
                        continue
                    missing_run = 0
                    if not echo[ray, gate] or not low[ray, gate]:
                        break
                    if abs(corrected[ray, gate] - expected) > config.boundary_maximum_deviation_db:
                        break
                    ids[ray, gate] = identity
                    linked_mask[ray, gate] = boundary_mask[ray, gate] = True
                    record = records[int(identity) - 1]
                    record["observed_candidate_gates"] += 1
                    record["linked_observed_gates"] += 1
                    record["boundary_observed_gates"] = record.get("boundary_observed_gates", 0) + 1
                    record["range_start_m"] = min(
                        record["range_start_m"], float(native.ranges[gate])
                    )
                    record["range_end_m"] = max(record["range_end_m"], float(native.ranges[gate]))
    return ObjectEvidence(
        {
            "RFI_OBJECT_ID": ids,
            "RFI_STRUCTURE_AVAILABLE_MASK": axial.astype("uint8"),
            "RFI_BACKGROUND_AVAILABLE_MASK": background.astype("uint8"),
            "RFI_SELF_SIGNATURE_MASK": self_mask.astype("uint8"),
            "RFI_LINKED_OBSERVATION_MASK": linked_mask.astype("uint8"),
            "RFI_BOUNDARY_OBSERVATION_MASK": boundary_mask.astype("uint8"),
            "RFI_CONTRAST_MASK": contrast.astype("uint8"),
            "RFI_AXIAL_STD_DB": np.where(observed & axial, std, np.nan).astype("float32"),
            "RFI_AXIAL_SUPPORT": np.where(observed, support, np.nan).astype("float32"),
        },
        tuple(records),
    )


def bounded_residual(native, objects, seeds, allowed, config):
    """One range pass, then one angular pass; no convergence loop or gap crossing."""
    ids = objects.arrays["RFI_OBJECT_ID"]
    output = seeds.copy()
    range_limit = int(np.floor(config.residual_range_m / native.gate_spacing_m))
    # Each direction starts at ORIGINAL confirmed seeds, so left then right cannot double radius.
    for direction in (-1, 1):
        front = seeds.copy()
        for _ in range(range_limit):
            moved = np.roll(front, direction, axis=1)
            same = ids == np.roll(ids, direction, axis=1)
            moved[:, 0 if direction > 0 else -1] = False
            front = moved & same & allowed & (ids > 0)
            output |= front
    range_result = output.copy()
    # Adjacent measured rays only, bounded by actual angular distance.
    for direction in (-1, 1):
        other = np.roll(np.arange(native.shape[0]), direction)
        gap = np.abs((native.azimuth - native.azimuth[other] + 180) % 360 - 180)
        rows = gap <= config.residual_azimuth_deg
        if direction == 1:
            rows &= ~np.roll(native.gap_after, 1)
        else:
            rows &= ~native.gap_after
        if not native.full_ppi:
            rows[0 if direction > 0 else -1] = False
        output |= range_result[other] & (ids == ids[other]) & rows[:, None] & allowed & (ids > 0)
    return output & ~seeds
