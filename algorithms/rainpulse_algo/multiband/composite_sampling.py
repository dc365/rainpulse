# ruff: noqa: E501, I001
"""Reuse one byte-bounded geographic display sampling plan per encoding call.

The original nearest-cell projection and float32 display samples are retained.
Only index preparation is shared. Every field still has its own finite mask.
"""
from __future__ import annotations
from contextvars import ContextVar
from functools import wraps
import numpy as np
from pyproj import Transformer
_CACHE = ContextVar('rainpulse_sx_display_sampling', default=None)
MAX_PLAN_BYTES = 32 * 1024 ** 2

def batch(function):

    @wraps(function)
    def call(*args, **kwargs):
        cache = {'plan': None, 'key': None, 'builds': 0, 'hits': 0, 'peak': 0}
        token = _CACHE.set(cache)
        try:
            return function(*args, **kwargs)
        finally:
            try:
                from rainpulse_algo.performance import observe
                for name in ('builds', 'hits', 'peak'):
                    observe('sx.display_sampling.' + name, cache[name], maximum=name == 'peak')
            except Exception:
                pass
            finally:
                _CACHE.reset(token)
    return call

class Plan:

    def __init__(self, metadata):
        width, height = (int(metadata['width']), int(metadata['height']))
        spacing = float(metadata['spacing_m'])
        west, south = (float(metadata['west_m']), float(metadata['south_m']))
        if width < 1 or height < 1 or width > 2048 or (height > 2048) or (spacing <= 0) or (metadata['row_order'] != 'south_to_north'):
            raise ValueError('unsupported composite grid')
        self.shape = (height, width)
        forward = Transformer.from_crs(metadata['crs'], 'EPSG:4326', always_xy=True)
        bounds = forward.transform_bounds(west, south, west + width * spacing, south + height * spacing, densify_pts=41)
        if not np.all(np.isfinite(bounds)) or bounds[0] >= bounds[2] or bounds[1] >= bounds[3]:
            raise ValueError('invalid map bounds')
        size = min(1024, max(width, height, 256))
        lon = bounds[0] + (np.arange(size) + 0.5) * (bounds[2] - bounds[0]) / size
        lat = bounds[1] + (np.arange(size) + 0.5) * (bounds[3] - bounds[1]) / size
        xx, yy = Transformer.from_crs('EPSG:4326', metadata['crs'], always_xy=True).transform(*np.meshgrid(lon, lat))
        self.cols = np.floor((xx - west) / spacing).astype(np.int64)
        self.rows = np.floor((yy - south) / spacing).astype(np.int64)
        self.valid = (self.cols >= 0) & (self.cols < width) & (self.rows >= 0) & (self.rows < height)
        self.geometry = dict(crs='EPSG:4326', bounds=list(bounds), resampling='nearest', source_grid_id=metadata['grid_id'])
        self.nbytes = self.cols.nbytes + self.rows.nbytes + self.valid.nbytes
        for a in (self.cols, self.rows, self.valid):
            a.setflags(write=False)

    def sample(self, values):
        if np.shape(values) != self.shape:
            raise ValueError('composite shape does not match grid')
        sampled = np.full(self.valid.shape, np.nan, np.float32)
        sampled[self.valid] = np.asarray(values)[self.rows[self.valid], self.cols[self.valid]]
        return ({**self.geometry, 'bounds': list(self.geometry['bounds'])}, sampled)

def sample(values, metadata):
    key = tuple((metadata[n] for n in ('grid_id', 'crs', 'width', 'height', 'spacing_m', 'west_m', 'south_m', 'row_order')))
    if np.shape(values) != (int(metadata['height']), int(metadata['width'])):
        raise ValueError('composite shape does not match grid')
    cache = _CACHE.get()
    if cache is not None and cache['key'] == key and (cache['plan'] is not None):
        cache['hits'] += 1
        return cache['plan'].sample(values)
    plan = Plan(metadata)
    if cache is not None:
        cache['builds'] += 1
        cache['plan'], cache['key'] = (plan, key) if plan.nbytes <= MAX_PLAN_BYTES else (None, None)
        cache['peak'] = max(cache['peak'], plan.nbytes if plan.nbytes <= MAX_PLAN_BYTES else 0)
    return plan.sample(values)
