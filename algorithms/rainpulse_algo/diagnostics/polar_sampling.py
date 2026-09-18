"""Shared PNG/pixel-trace inverse sampling. No unmeasured angular/range extrapolation."""

from __future__ import annotations

from dataclasses import dataclass

import numpy as np

SAMPLING_VERSION = "native-footprint-v2"


@dataclass(frozen=True)
class PolarPixels:
    ray: np.ndarray
    gate: np.ndarray
    available: np.ndarray
    radius_m: np.ndarray
    azimuth_deg: np.ndarray


def polar_pixels(azimuth, ranges, size, *, nominal_azimuth_deg=None):
    if not isinstance(size, (int, np.integer)) or not 2 <= size <= 4096:
        raise ValueError("invalid PPI image size")
    xy = np.linspace(-1.0, 1.0, int(size), dtype=np.float64)
    xx, yy = np.meshgrid(xy, -xy)
    if not len(ranges):
        raise ValueError("empty PPI ranges")
    radius = np.hypot(xx, yy) * ranges[-1]
    angle = np.degrees(np.arctan2(xx, yy)) % 360
    return polar_targets(azimuth, ranges, radius, angle, nominal_azimuth_deg=nominal_azimuth_deg)


def polar_targets(azimuth, ranges, radius, angle, *, nominal_azimuth_deg=None):
    """Sample measured polar footprints at arbitrary range/azimuth targets."""
    az = np.asarray(azimuth, float)
    r = np.asarray(ranges, float)
    if (
        az.ndim != 1
        or r.ndim != 1
        or not len(az)
        or len(r) < 2
        or not np.isfinite(az).all()
        or not np.isfinite(r).all()
        or np.any(r < 0)
        or np.any(np.diff(r) <= 0)
    ):
        raise ValueError("invalid PPI source geometry or image size")
    az = az % 360
    order = np.argsort(az, kind="stable")
    a = az[order]
    gap = (np.roll(a, -1) - a) % 360
    dup = gap <= 0.01
    positive = gap[~dup]
    if nominal_azimuth_deg is None:
        # A huge outer sector gap must not become the nominal width.
        small = positive[positive <= np.median(positive)] if len(positive) else []
        nominal = float(np.median(small)) if len(small) else 0.0
    else:
        nominal = float(nominal_azimuth_deg)
        if not np.isfinite(nominal) or not 0 < nominal <= 180:
            raise ValueError("invalid nominal ray footprint")
    good_ray = ~(dup | np.roll(dup, 1))
    if len(a) == 1:
        good_ray[:] = nominal > 0
        gap[:] = 360
    after = np.where(gap > nominal * 1.8, nominal, gap) / 2
    before = np.roll(after, 1)
    radius, angle = np.broadcast_arrays(np.asarray(radius, float), np.asarray(angle, float) % 360)
    pos = np.searchsorted(a, angle)
    li = (pos - 1) % len(a)
    ri = pos % len(a)
    dl = np.abs((angle - a[li] + 180) % 360 - 180)
    dr = np.abs((angle - a[ri] + 180) % 360 - 180)
    nearest = np.where(dl <= dr, li, ri)
    signed = (angle - a[nearest] + 180) % 360 - 180
    within = np.where(
        signed >= 0, signed <= after[nearest] + 1e-9, -signed <= before[nearest] + 1e-9
    )
    rad_idx = np.clip(np.searchsorted(r, radius), 0, len(r) - 1)
    prev = np.maximum(rad_idx - 1, 0)
    rad_idx = np.where(np.abs(radius - r[prev]) <= np.abs(radius - r[rad_idx]), prev, rad_idx)
    spacing = float(np.median(np.diff(r)))
    rgap = np.diff(r)
    side = np.where(rgap > spacing * 1.8, spacing, rgap) / 2
    lo = np.maximum(0, r - np.r_[spacing / 2, side])
    hi = r + np.r_[side, spacing / 2]
    valid = (
        within
        & good_ray[nearest]
        & (nominal > 0)
        & (radius >= lo[rad_idx])
        & (radius <= hi[rad_idx])
        & (radius <= r[-1])
    )
    rays = order[nearest].astype("int32")
    gates = rad_idx.astype("int32")
    return PolarPixels(np.where(valid, rays, -1), np.where(valid, gates, -1), valid, radius, angle)


def project_rgba(polar_rgba, azimuth, ranges, size):
    rgba = np.asarray(polar_rgba)
    if rgba.shape != (len(azimuth), len(ranges), 4):
        raise ValueError("polar RGBA differs from geometry")
    p = polar_pixels(azimuth, ranges, size)
    output = rgba[np.maximum(p.ray, 0), np.maximum(p.gate, 0)].copy()
    output[~p.available] = 0
    return output
