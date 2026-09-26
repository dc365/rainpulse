"""Frozen source-derived reference expressions, not full production Worker.
Base: c5c7b47b205e6a487d9e181c896dde59d937184e.
"""

import struct
import zlib

import numpy as np
from pyproj import Geod

PALETTE = [
    [5, "#419bf1"],
    [10, "#64e8ec"],
    [15, "#6efb3d"],
    [20, "#00dc00"],
    [25, "#019000"],
    [30, "#fdfe00"],
    [35, "#e7c000"],
    [40, "#ff9000"],
    [45, "#fe0000"],
    [50, "#d60000"],
    [55, "#c00000"],
    [60, "#ff00f0"],
    [65, "#9500b4"],
    [70, "#ae90f0"],
]

LEVELS = np.array([p[0] for p in PALETTE], float)

COLORS = np.array([[int(c[i : i + 2], 16) for i in (1, 3, 5)] for _, c in PALETTE], np.uint8)

MAX_PPI_INTERPOLATION_GAP_DEG = 3.0


def png(rgba):
    if rgba.ndim != 3 or rgba.shape[2] != 4 or rgba.dtype != np.uint8:
        raise ValueError("RGBA uint8 required")
    height, width = rgba.shape[:2]

    def chunk(name, data):
        return (
            struct.pack(">I", len(data))
            + name
            + data
            + struct.pack(">I", zlib.crc32(name + data) & 0xFFFFFFFF)
        )

    scan = b"".join(b"\x00" + row.tobytes() for row in rgba)
    return (
        b"\x89PNG\r\n\x1a\n"
        + chunk(b"IHDR", struct.pack(">IIBBBBB", width, height, 8, 6, 0, 0, 0))
        + chunk(b"IDAT", zlib.compress(scan, 3))
        + chunk(b"IEND", b"")
    )


def polar_quicklook(
    azimuth_deg,
    range_m,
    values,
    *,
    uncertain=None,
    actions=None,
    size=720,
    map_sampling=None,
    elevation_deg=None,
):
    az = np.asarray(azimuth_deg, dtype=float) % 360
    ranges = np.asarray(range_m, dtype=float)
    data = np.asarray(values)
    if data.shape != (len(az), len(ranges)) or len(az) == 0 or len(ranges) == 0:
        raise ValueError("polar quicklook coordinates and field differ")
    radius = max(float(ranges[-1]), 1.0)
    axis = (np.arange(size, dtype=float) - (size - 1) / 2) * (2 * radius / size)
    xx, yy = np.meshgrid(axis, axis)
    distance = np.hypot(xx, yy)
    bearing = np.rad2deg(np.arctan2(xx, yy)) % 360
    if map_sampling is not None:
        distance, bearing = map_sampling
    order = np.argsort(az)
    ordered = az[order]
    extended = np.concatenate(([ordered[-1] - 360], ordered, [ordered[0] + 360]))
    ray_order = np.concatenate(([order[-1]], order, [order[0]]))
    right = np.searchsorted(extended, bearing, side="left")
    left = np.clip(right - 1, 0, len(extended) - 1)
    right = np.clip(right, 0, len(extended) - 1)
    choose_right = np.abs(extended[right] - bearing) < np.abs(extended[left] - bearing)
    rays = ray_order[np.where(choose_right, right, left)]
    angular_distance = np.minimum(np.abs(az[rays] - bearing), 360 - np.abs(az[rays] - bearing))
    circular_steps = np.diff(np.concatenate((ordered, [ordered[0] + 360])))
    angular_limit = min(
        max(float(np.median(circular_steps[circular_steps > 0])) * 1.5, 0.1)
        if np.any(circular_steps > 0)
        else 0.1,
        MAX_PPI_INTERPOLATION_GAP_DEG,
    )
    if map_sampling is not None:
        re = 6371008.8 * 4 / 3
        angle = distance / re
        elevation = np.deg2rad(np.broadcast_to(elevation_deg, az.shape)[rays])
        denominator = np.cos(elevation + angle)
        distance = np.where(denominator > 0, re * np.sin(angle) / denominator, np.inf)
    gates = np.searchsorted(ranges, distance, side="left")
    valid = (distance <= radius) & (gates < len(ranges)) & (angular_distance <= angular_limit)
    gates = np.clip(gates, 0, len(ranges) - 1)
    sample = data[rays, gates]
    valid &= np.isfinite(sample)
    rgba = np.zeros((size, size, 4), np.uint8)
    index = np.clip(
        np.searchsorted(LEVELS, np.nan_to_num(sample, nan=-100), side="right") - 1,
        0,
        len(LEVELS) - 1,
    )
    rgba[:, :, :3] = COLORS[index]
    rgba[:, :, 3] = np.where(valid, 255, 0)
    if uncertain is not None:
        mask = np.asarray(uncertain, dtype=bool)[rays, gates] & valid
        rgba[mask, :3] = np.array([238, 160, 40], dtype=np.uint8)
    if actions is not None:
        action = np.asarray(actions, dtype=np.uint8)[rays, gates]
        rejected = (action == 2) & valid
        uncertain_gate = (action == 3) & valid
        rgba[:, :, 3] = np.where(rejected | uncertain_gate, 255, 0)
        rgba[rejected, :3] = np.array([191, 57, 48], dtype=np.uint8)
        rgba[uncertain_gate, :3] = np.array([238, 160, 40], dtype=np.uint8)
    return png(rgba[::-1])


def map_sampling(az, el, r, size):
    geod = Geod(ellps="WGS84")
    lon, lat = 119.0, 26.0
    re = 6371008.8 * 4 / 3
    e = np.deg2rad(el)
    radius = float(np.max(re * np.arctan2(r[-1] * np.cos(e), re + r[-1] * np.sin(e))))
    x, y, _ = geod.fwd(np.full(360, lon), np.full(360, lat), np.arange(360.0), np.full(360, radius))
    xs = x.min() + (np.arange(size) + 0.5) * (x.max() - x.min()) / size
    ys = y.min() + (np.arange(size) + 0.5) * (y.max() - y.min()) / size
    xx, yy = np.meshgrid(xs, ys)
    a, _, d = geod.inv(np.full_like(xx, lon), np.full_like(yy, lat), xx, yy)
    return d, a % 360


def loop_validate(
    oid, size, fraction, core, propagated, maximum=5000, min_seeds=3, min_fraction=0.3
):
    for value in np.unique(oid[propagated]):
        component = oid == value
        members = int(component.sum())
        seeds = int((component & core).sum())
        if members != int(size[component][0]) or not np.allclose(
            fraction[component], seeds / members
        ):
            raise ValueError("object size or seed fraction differs")
        if members > maximum or seeds < min_seeds or seeds / members < min_fraction:
            raise ValueError("object lacks bounded support")
    return True
