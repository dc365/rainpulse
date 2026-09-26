"""One immutable sampling plan per sweep/view, shared by raw/QC/flags rendering.

Native PPI and geographic map remain different plans. Finite-value/action masks
are evaluated on each rendered field, NOT copied from raw. No cross-task cache.
"""

from __future__ import annotations

from dataclasses import dataclass

import numpy as np

from rainpulse_algo.performance import observe, timed

MAX_PPI_INTERPOLATION_GAP_DEG = 3.0


@dataclass(frozen=True)
class PolarSampling:
    rays: np.ndarray
    gates: np.ndarray
    support: np.ndarray
    source_shape: tuple[int, int]
    size: int


@timed("preview.sampling")
def prepare_polar_sampling(
    azimuth_deg, range_m, *, size=720, map_sampling=None, elevation_deg=None
):
    az = np.asarray(azimuth_deg, dtype=float) % 360
    ranges = np.asarray(range_m, dtype=float)
    if len(az) == 0 or len(ranges) == 0:
        raise ValueError("polar quicklook coordinates and field differ")
    radius = max(float(ranges[-1]), 1.0)
    if map_sampling is None:
        axis = (np.arange(size, dtype=float) - (size - 1) / 2) * (2 * radius / size)
        xx, yy = np.meshgrid(axis, axis)
        distance = np.hypot(xx, yy)
        bearing = np.rad2deg(np.arctan2(xx, yy)) % 360
    else:
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
        effective_radius = 6371008.8 * 4 / 3
        angle = distance / effective_radius
        elevation = np.deg2rad(np.broadcast_to(elevation_deg, az.shape)[rays])
        denominator = np.cos(elevation + angle)
        distance = np.where(denominator > 0, effective_radius * np.sin(angle) / denominator, np.inf)
    gates = np.searchsorted(ranges, distance, side="left")
    valid = (distance <= radius) & (gates < len(ranges)) & (angular_distance <= angular_limit)
    gates = np.clip(gates, 0, len(ranges) - 1)
    for value in (rays, gates, valid):
        value.setflags(write=False)
    observe("preview.sampling_plans", 1)
    return PolarSampling(rays, gates, valid, (len(az), len(ranges)), size)


@timed("preview.color_and_png")
def render_polar_sampling(
    plan, values, levels, colors, encode_png, *, uncertain=None, actions=None
):
    data = np.asarray(values)
    if data.shape != plan.source_shape:
        raise ValueError("polar quicklook coordinates and field differ")
    sample = data[plan.rays, plan.gates]
    valid = plan.support & np.isfinite(sample)
    rgba = np.zeros((plan.size, plan.size, 4), np.uint8)
    index = np.clip(
        np.searchsorted(levels, np.nan_to_num(sample, nan=-100), side="right") - 1,
        0,
        len(levels) - 1,
    )
    rgba[:, :, :3] = colors[index]
    rgba[:, :, 3] = np.where(valid, 255, 0)
    if uncertain is not None:
        mask = np.asarray(uncertain, dtype=bool)[plan.rays, plan.gates] & valid
        rgba[mask, :3] = np.array([238, 160, 40], dtype=np.uint8)
    if actions is not None:
        action = np.asarray(actions, dtype=np.uint8)[plan.rays, plan.gates]
        rejected = (action == 2) & valid
        uncertain_gate = (action == 3) & valid
        rgba[:, :, 3] = np.where(rejected | uncertain_gate, 255, 0)
        rgba[rejected, :3] = np.array([191, 57, 48], dtype=np.uint8)
        rgba[uncertain_gate, :3] = np.array([238, 160, 40], dtype=np.uint8)
    observe("preview.png_count", 1)
    return encode_png(rgba[::-1])
