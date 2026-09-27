"""Bounded whole-field read sharing for one serialized-sweep validation call.

Every validator still runs. Legacy field mappings resolve to this underlying
read view, sharing reads only for the same physical field of this exact group.
No cache persists after the call, including exceptions. No output statistics are
trusted as substitutes for recalculation.
"""
from __future__ import annotations

from collections import OrderedDict
from collections.abc import Mapping
from contextvars import ContextVar
from functools import wraps

import numpy as np

_ACTIVE = ContextVar("rainpulse_validation_read_scope", default=None)
DEFAULT_BYTES = 64 * 1024**2


def _whole(index):
    return index is Ellipsis or (isinstance(index, slice) and
           index.start is None and index.stop is None and index.step is None)


class ValidationReadGroup(Mapping):
    def __init__(self, group, maximum_bytes=DEFAULT_BYTES):
        if type(maximum_bytes) is not int or maximum_bytes < 0:
            raise ValueError("invalid validation read budget")
        self.group, self.maximum = group, maximum_bytes
        self.cache = OrderedDict()
        self.bytes = self.peak = self.hits = self.reads = self.evictions = 0
        self.closed = False
        self.keys_ = tuple(group)

    def __iter__(self):
        return iter(self.keys_)

    def __len__(self):
        return len(self.keys_)

    def __getattr__(self, key):
        return getattr(self.group, key)

    def __getitem__(self, key):
        if self.closed:
            raise RuntimeError("validation read scope is closed")
        source = self.group[key]  # Preserve missing-field exceptions and attrs.
        return _Array(self, key, source)

    def read(self, key, source):
        if self.closed:
            raise RuntimeError("validation read scope is closed")
        if key in self.cache:
            self.hits += 1
            self.cache.move_to_end(key)
            return self.cache[key]
        self.reads += 1
        value = np.asarray(source[:])
        # Validators get immutable snapshots; callers that need mutation must
        # already make an explicit copy, as the baseline validators do.
        if value.dtype.hasobject:
            raise ValueError("object fields cannot enter the validation read cache")
        value = np.frombuffer(value.tobytes(order="C"), dtype=value.dtype).reshape(value.shape)
        if value.nbytes <= self.maximum:
            while self.cache and self.bytes + value.nbytes > self.maximum:
                _, old = self.cache.popitem(last=False)
                self.bytes -= old.nbytes
                self.evictions += 1
            self.cache[key] = value
            self.bytes += value.nbytes
            self.peak = max(self.peak, self.bytes)
        return value

    def close(self):
        self.cache.clear()
        self.bytes = 0
        self.closed = True


class _Array:
    def __init__(self, group, key, source):
        self.group, self.key, self.source = group, key, source

    def __getattr__(self, key):
        return getattr(self.source, key)

    def __getitem__(self, item):
        if _whole(item):
            return self.group.read(self.key, self.source)
        return self.source[item]

    def __array__(self, dtype=None, copy=None):
        a = self.group.read(self.key, self.source)
        if copy is True:
            return np.array(a, dtype=dtype, copy=True)
        if copy is False and dtype is not None and np.dtype(dtype) != a.dtype:
            raise ValueError("dtype conversion requires a copy")
        return np.asarray(a, dtype=dtype)


def shared_validation_reads(function):
    @wraps(function)
    def wrapped(group, *args, **kwargs):
        if _ACTIVE.get() is not None:
            return function(group, *args, **kwargs)
        view = ValidationReadGroup(group)
        token = _ACTIVE.set(view)
        try:
            return function(view, *args, **kwargs)
        finally:
            try:
                from rainpulse_algo.performance import observe
                observe("s.validation_read_hits", view.hits)
                observe("s.validation_field_reads", view.reads)
                observe("s.validation_read_peak_bytes", view.peak)
            except Exception:
                pass
            _ACTIVE.reset(token)
            view.close()
    return wrapped
