"""Topology-aware labels and approximate horizontal sampling area, not beam volume."""

from __future__ import annotations

import numpy as np
from scipy import ndimage


def label_polar(mask, native):
    if np.shape(mask) != native.shape:
        raise ValueError("polar object geometry differs")
    mask = np.asarray(mask, bool) & native.geometry_good[:, None]
    output = np.zeros(native.shape, "int32")
    boundaries = native.gap_after[:-1] | ~native.geometry_good[:-1] | ~native.geometry_good[1:]
    edges = [0, *list(np.flatnonzero(boundaries) + 1), len(mask)]
    count = 0
    for a, b in zip(edges[:-1], edges[1:], strict=True):
        labels, n = ndimage.label(mask[a:b])  # four-neighbour, no diagonal gap shortcuts
        output[a:b] = np.where(labels, labels + count, 0)
        count += n
    if native.full_ppi and not native.gap_after[-1] and count:
        parent = np.arange(count + 1)

        def find(i):
            while parent[i] != i:
                parent[i] = parent[parent[i]]
                i = parent[i]
            return i

        for a, b in zip(output[0], output[-1], strict=True):
            if a and b:
                a, b = find(a), find(b)
                parent[max(a, b)] = min(a, b)
        mapping = np.array([find(i) for i in range(count + 1)])
        # np.unique may omit zero for a completely occupied image.
        values = mapping[output]
        present = np.unique(values[values > 0])
        lookup = np.zeros(count + 1, "int32")
        lookup[present] = np.arange(1, len(present) + 1)
        output = lookup[values]
        count = len(present)
    return output, count


def angular_width(native, rays):
    angles = np.sort(np.unique(native.azimuth[rays] % 360))
    if not len(angles):
        return 0.0
    return float(
        360 - np.max(np.diff(np.r_[angles, angles[0] + 360])) + native.audit["azimuth_spacing_deg"]
    )


def gate_area_km2(native):
    r = np.asarray(native.ranges, float)
    dr = native.gate_spacing_m
    edges = np.r_[max(0, r[0] - dr / 2), (r[:-1] + r[1:]) / 2, r[-1] + dr / 2]
    nominal = float(native.audit["azimuth_spacing_deg"])
    left = (native.azimuth - np.roll(native.azimuth, 1)) % 360
    right = np.roll(left, -1)
    # Never assign the unobserved gap to the adjacent ray. Outward sector edges
    # use nominal half-footprints; actual gap ownership is handled by support.
    left = np.minimum(left, nominal)
    right = np.minimum(right, nominal)
    width = np.deg2rad((left + right) / 2)
    area = width[:, None] * np.diff(edges**2)[None, :] / 2 / 1e6
    area *= np.cos(np.deg2rad(native.elevation))[:, None] ** 2
    return np.where(native.geometry_good[:, None], area, 0)
