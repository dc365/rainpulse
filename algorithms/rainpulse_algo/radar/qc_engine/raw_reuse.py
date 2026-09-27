"""One extension-call raw snapshot arena; no cross-task identity-only caching.

Only explicitly registered native inputs can share frozen snapshots. Writable
native owners are checked against the snapshots before leaving the scope. The
snapshots are backed by Python bytes, so NumPy setflags cannot re-enable writes.
"""
from __future__ import annotations

import hashlib
import json
from contextvars import ContextVar
from functools import wraps

import numpy as np

_CURRENT = ContextVar("rainpulse_native_snapshot_scope", default=None)


def immutable_array(value, dtype=None):
    a = np.asarray(value, dtype=dtype)
    if a.dtype.hasobject:
        raise ValueError("object arrays forbidden")
    arena = _CURRENT.get()
    if arena is not None:
        cached = arena.frozen.get(id(value))
        if cached is not None and cached[0] is value and cached[1].dtype == a.dtype:
            arena.hits += 1
            return cached[1]
    # Returning a readonly view over a writable owner is NOT an immutable copy.
    owner = a
    while isinstance(getattr(owner, "base", None), np.ndarray):
        owner = owner.base
    if isinstance(getattr(owner, "base", None), bytes) and not a.flags.writeable:
        result = a
    else:
        result = np.frombuffer(a.tobytes(order="C"), dtype=a.dtype).reshape(a.shape)
    if arena is not None and arena.registered.get(id(value)) is value:
        arena.frozen[id(value)] = (value, result)
        arena.snapshot_bytes += result.nbytes
    return result


class NativeArena:
    def __init__(self, native):
        self.registered = {}
        self.frozen = {}
        self.views = {}
        self.hits = self.snapshot_bytes = 0
        self.native = tuple(native)
        for n in self.native:
            arrays = [*n.fields.values(), *n.field_available.values()]
            arrays += [getattr(n, k, None) for k in
                       ("azimuth", "elevation", "ranges", "geometry_good", "gap_after",
                        "ray_time", "original_indices")]
            for a in arrays:
                if isinstance(a, np.ndarray):
                    self.registered[id(a)] = a

    def check_owners(self):
        for source, snapshot in self.frozen.values():
            if source.shape != snapshot.shape or not np.array_equal(
                    source, snapshot, equal_nan=True):
                raise RuntimeError("native raw owner changed inside shared review scope")

    def clear(self):
        self.views.clear()
        self.frozen.clear()
        self.registered.clear()
        self.native = ()


def shared_native_scope(function):
    @wraps(function)
    def wrapped(result, native, *args, **kwargs):
        # Nested calls should not replace the parent arena. Separate tasks use
        # a new context; the production Worker has one numerical lane.
        if _CURRENT.get() is not None:
            return function(result, native, *args, **kwargs)
        arena = NativeArena(native)
        token = _CURRENT.set(arena)
        try:
            value = function(result, native, *args, **kwargs)
            arena.check_owners()
            return value
        finally:
            # Never replace the original numerical exception with telemetry.
            try:
                from rainpulse_algo.performance import observe
                observe("s.raw_view_cache_hits", arena.hits)
                observe("s.raw_snapshot_bytes", arena.snapshot_bytes)
            except Exception:
                pass
            _CURRENT.reset(token)
            arena.clear()
    return wrapped


def shared_native_view(policy):
    def decorate(function):
        @wraps(function)
        def wrapped(native):
            arena = _CURRENT.get()
            if arena is None:
                return function(native)
            key = (policy, id(native))
            hit = arena.views.get(key)
            if hit is not None and hit[0] is native:
                arena.hits += 1
                return hit[1]
            value = function(native)
            arena.views[key] = (native, value)
            return value
        return wrapped
    return decorate


def array_digest(arrays, *, block_bytes=1024 * 1024):
    """Exact old canonical little-endian/C-order/NaN digest, in bounded blocks."""
    if type(block_bytes) is not int or block_bytes <= 0:
        raise ValueError("positive digest block size required")
    h = hashlib.sha256()
    for key in sorted(arrays):
        a = np.asarray(arrays[key])
        if a.dtype.hasobject:
            raise ValueError("object arrays are not allowed")
        dtype = a.dtype.newbyteorder("<")
        header = json.dumps([key, dtype.str, list(a.shape)], sort_keys=True,
                            separators=(",", ":"), ensure_ascii=False,
                            allow_nan=False).encode("utf-8")
        h.update(len(header).to_bytes(8, "little"))
        h.update(header)
        if not a.size:
            continue
        count = max(1, block_bytes // max(dtype.itemsize, 1))
        with np.nditer(a, flags=["external_loop", "buffered", "zerosize_ok"],
                       op_flags=["readonly"], op_dtypes=[dtype], order="C",
                       buffersize=count) as blocks:
            for source in blocks:
                block = np.array(source, dtype=dtype, order="C", copy=True)
                if block.dtype.kind in "fc":
                    block[np.isnan(block)] = np.nan
                h.update(memoryview(block.view(np.uint8)).cast("B"))
    return h.hexdigest()
