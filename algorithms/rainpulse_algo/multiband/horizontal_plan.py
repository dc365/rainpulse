# ruff: noqa: E501, I001
"""Equivalent, bounded reuse for the existing experimental horizontal policy.

The 1-degree angular tolerance, right-hand range widths and native time limits
are intentionally NOT replaced by the different quality-height footprint policy.
"""
from __future__ import annotations
import numpy as np
from pyproj import Transformer

class HorizontalPlan:

    def __init__(self, grid, maximum_bytes, *, cache_factory=None):
        if cache_factory is None:
            from .stream_fusion import GeometryCache
            cache_factory = GeometryCache
        self.grid = grid
        self.transform = Transformer.from_crs(grid.crs, 'EPSG:4326', always_xy=True)
        self.cache = cache_factory(maximum_bytes)
        self.tiles_seen = self.tiles_skipped = 0

    def coordinates(self, row, stop):
        g = self.grid

        def calculate():
            xx, yy = np.meshgrid(g.west_m + (np.arange(g.width) + 0.5) * g.spacing_m, g.south_m + (np.arange(row, stop) + 0.5) * g.spacing_m)
            return self.transform.transform(xx, yy)
        return self.cache.get(('horizontal_grid', row, stop), calculate)

    def ground(self, lon, lat, row, stop):
        from .fusion import GEOD
        glon, glat = self.coordinates(row, stop)

        def calculate():
            bearing, _, distance = GEOD.inv(np.full(glon.shape, lon), np.full(glat.shape, lat), glon, glat)
            return (np.asarray(bearing) % 360, np.asarray(distance))
        return self.cache.get(('horizontal_station', float(lon), float(lat), row, stop), calculate)

    def metrics(self):
        return dict(geometry_cache_peak_bytes=self.cache.peak, geometry_hits=self.cache.hits, geometry_misses=self.cache.misses, horizontal_tiles_seen=self.tiles_seen, horizontal_tiles_skipped=self.tiles_skipped)

def maximum_ground_reach(sweep, widths):
    from .fusion import EARTH_EFFECTIVE_M as re
    radius = float(sweep.range_m[-1] + np.max(widths) / 2)
    if not 0 <= radius < re / 2 or not np.isfinite(sweep.elevation_deg).all():
        return np.inf
    elevation = np.deg2rad(sweep.elevation_deg)
    reach = re * np.arctan2(radius * np.cos(elevation), re + radius * np.sin(elevation))
    if not np.isfinite(reach).all():
        return np.inf
    return max(0.0, float(np.max(reach))) + 1.0

def fuse_sweep(plan, sweep, station, values, admitted, quality, native_ray, longitude, latitude, target, index, outputs):
    from .fusion import EARTH_EFFECTIVE_M as re, _nearest_ray
    from .experimental import update_samples
    from .fusion_audit import trace_source, trace_samples
    grid = plan.grid
    ranges = sweep.range_m
    if len(ranges) < 2:
        return
    widths = np.r_[np.diff(ranges), np.diff(ranges)[-1]]
    reach = maximum_ground_reach(sweep, widths)
    order = np.argsort(sweep.azimuth_deg)
    prepared = (order, sweep.azimuth_deg[order])
    for band in (station.band, 'S+X'):
        trace_source(outputs[band], sweep, station, index, grid, admitted=admitted)
    for y in range(0, grid.height, grid.tile_rows):
        stop = min(y + grid.tile_rows, grid.height)
        sl = slice(y, stop)
        bearing, distance = plan.ground(longitude, latitude, y, stop)
        plan.tiles_seen += 1
        if np.all(distance > reach):
            plan.tiles_skipped += 1
            false = np.zeros(distance.shape, bool)
            for band in (station.band, 'S+X'):
                trace_samples(outputs[band], index, false, ~false, false, false, false, false)
            continue
        ray, offset = _nearest_ray(sweep.azimuth_deg, bearing, prepared=prepared)
        arc = distance / re
        denominator = np.cos(np.deg2rad(sweep.elevation_deg[ray]) + arc)
        slant = re * np.sin(arc) / np.maximum(denominator, 1e-09)
        pos = np.clip(np.searchsorted(ranges, slant), 1, len(ranges) - 1)
        gate = np.where(abs(slant - ranges[pos - 1]) <= abs(slant - ranges[pos]), pos - 1, pos)
        age = target - sweep.ray_time_epoch[ray]
        support = (offset <= 1.0) & (denominator > 0) & (abs(slant - ranges[gate]) <= widths[gate] / 2) & (age >= 0) & (age <= station.maximum_age_seconds)
        valid = support & admitted[ray, gate]
        observed = support & (sweep.fields['OBSERVED_MASK'][ray, gate] == 1)
        noecho = sweep.fields['NO_ECHO_MASK'][ray, gate] == 1
        for band in (station.band, 'S+X'):
            trace_samples(outputs[band], index, support, np.ones(support.shape, bool), observed, valid, valid, noecho)
            update_samples({k: v[sl] for k, v in outputs[band].items()}, values[ray, gate], valid, native_ray[ray], gate, age, quality[ray, gate], index, sweep.number)
