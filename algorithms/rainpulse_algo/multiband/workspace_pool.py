"""Task-local aggregate S/X height-state budget with write-back tile reuse.

Only intermediate state is stored here. No files are publication assets and
close() discards them even on success. Views borrowed by tile() must not escape.
The memory budget covers ALL workspaces; when smaller than one tile the only
allocation is that explicit transient tile (bounded by maximum_tile_bytes).
"""
from __future__ import annotations

import os
import shutil
from collections import OrderedDict
from contextlib import contextmanager
from dataclasses import dataclass
from pathlib import Path
from tempfile import TemporaryDirectory

import numpy as np

FIELDS = ("score", "values", "winner", "wray", "wgate", "h", "age", "resolution")
DTYPE = np.dtype([(name, "float64" if name == "score" else
                   "int32" if name in {"winner", "wray", "wgate"} else "float32")
                  for name in FIELDS])


def workspace_plan(grid, options, *, comparison):
    """Calculate aggregate reservations before opening any input artifact."""
    count = 3 if comparison else 1
    state = int(grid.width * grid.height * len(grid.levels_m_msl) * DTYPE.itemsize)
    tile = int(grid.width * min(grid.tile_rows, grid.height) *
               len(grid.levels_m_msl) * DTYPE.itemsize)
    total = state * count
    if tile > options.maximum_tile_bytes:
        raise ValueError("height tile exceeds execution memory budget")
    spill = total > options.layer_memory_bytes
    # A dirty replacement is written to a new temporary file before rename.
    disk = total + tile if spill else 0
    if disk > options.maximum_scratch_bytes:
        raise ValueError("aggregate height state exceeds scratch budget")
    return dict(state_bytes=state, total_bytes=total, tile_bytes=tile,
                spill=spill, disk_reservation=disk, count=count)


def _empty(shape):
    a = np.empty(shape, DTYPE)
    for name in FIELDS:
        a[name] = (-np.inf if name == "score" else
                   -1 if name in {"winner", "wray", "wgate"} else
                   np.inf if name in {"age", "resolution"} else np.nan)
    return a


@dataclass
class _Entry:
    array: np.ndarray
    dirty: bool = False


class WorkspacePool:
    """One shared LRU budget, not one independent budget per S/X/SX state."""
    def __init__(self, grid, options, directory, *, comparison=False):
        self.grid, self.options = grid, options
        self.plan = workspace_plan(grid, options, comparison=comparison)
        if self.plan["disk_reservation"] > shutil.disk_usage(directory).free:
            raise ValueError("insufficient scratch space for aggregate height state")
        self._temp = TemporaryDirectory(prefix="height-pool-", dir=directory)
        self.directory = Path(self._temp.name)
        self.maximum = int(options.layer_memory_bytes)
        self.resident = self.peak = 0
        self._cache = OrderedDict()
        self._files = {}
        self._open = False
        self.closed = self.failed = False
        self._names = ("SX", "S", "X") if comparison else ("SX",)
        self.stats = {name: dict(tile_gets=0, tile_hits=0, evictions=0,
                                logical_read_bytes=0, logical_write_bytes=0,
                                file_read_bytes=0, file_write_bytes=0,
                                file_writes=0, discarded_dirty_tiles=0)
                      for name in self._names}

    def workspace(self, name):
        if name not in self.stats:
            raise ValueError("workspace not in aggregate plan")
        return _Workspace(self, name)

    def _path(self, key):
        name, row, stop = key
        return self.directory / f"{name}-{row:06d}-{stop:06d}.bin"

    def _write(self, key, entry):
        if not entry.dirty:
            return
        if not self.plan["spill"]:
            raise RuntimeError("in-memory height plan attempted a spill")
        path = self._path(key)
        temporary = path.with_suffix(".tmp")
        try:
            # No .tobytes() copy of a complete height tile.
            with temporary.open("xb") as stream:
                view = memoryview(entry.array).cast("B")
                for offset in range(0, len(view), 1024 * 1024):
                    part = view[offset:offset + 1024 * 1024]
                    if stream.write(part) != len(part):
                        raise OSError("short height-state write")
            os.replace(temporary, path)
        except BaseException:
            temporary.unlink(missing_ok=True)
            self.failed = True
            raise
        self._files[key] = path
        entry.dirty = False
        s = self.stats[key[0]]
        s["file_write_bytes"] += entry.array.nbytes
        s["file_writes"] += 1

    def _evict(self):
        key, entry = self._cache.popitem(last=False)
        try:
            self._write(key, entry)
        finally:
            self.resident -= entry.array.nbytes
        self.stats[key[0]]["evictions"] += 1

    @contextmanager
    def tile(self, name, row, stop, *, write=True):
        if self.closed or self.failed:
            raise RuntimeError("height workspace is closed or failed")
        if self._open:
            raise RuntimeError("height tile borrow cannot be nested")
        if (name not in self.stats or type(row) is not int or type(stop) is not int
                or row < 0 or row % self.grid.tile_rows or stop != min(
                    row + self.grid.tile_rows, self.grid.height) or stop <= row):
            raise ValueError("invalid height tile identity")
        key = (name, row, stop)
        shape = (len(self.grid.levels_m_msl), stop-row, self.grid.width)
        size = int(np.prod(shape)) * DTYPE.itemsize
        stat = self.stats[name]
        stat["tile_gets"] += 1
        stat["logical_read_bytes"] += size
        entry = self._cache.pop(key, None)
        was_cached = entry is not None
        if was_cached:
            stat["tile_hits"] += 1
        else:
            # Reserve the active tile before allocation, including cache entries.
            bound = max(self.maximum, size)
            while self._cache and self.resident + size > bound:
                self._evict()
            if key in self._files:
                path = self._files[key]
                if path.stat().st_size != size:
                    self.failed = True
                    raise RuntimeError("height-state file length changed")
                a = np.fromfile(path, dtype=DTYPE, count=int(np.prod(shape)))
                if a.nbytes != size:
                    self.failed = True
                    raise RuntimeError("truncated height-state file")
                entry = _Entry(a.reshape(shape))
                stat["file_read_bytes"] += size
            else:
                entry = _Entry(_empty(shape))
            self.resident += size
            self.peak = max(self.peak, self.resident)
        self._open = True
        succeeded = False
        try:
            arrays = tuple(entry.array[field] for field in FIELDS)
            if not write:
                arrays = tuple(a.view() for a in arrays)
                for a in arrays:
                    a.setflags(write=False)
            yield arrays
            succeeded = True
            if write:
                entry.dirty = True
                stat["logical_write_bytes"] += size
        except BaseException:
            self.failed = True
            raise
        finally:
            self._open = False
            if not succeeded:
                self.resident -= size
            elif size <= self.maximum:
                self._cache[key] = entry
            else:
                try:
                    self._write(key, entry)
                finally:
                    self.resident -= size

    def metrics(self):
        result = dict(height_state_bytes=self.plan["total_bytes"],
                      height_memory_budget_bytes=self.maximum,
                      height_resident_array_peak_bytes=self.peak,
                      peak_height_tile_bytes=self.plan["tile_bytes"],
                      layer_scratch_reserved_bytes=self.plan["disk_reservation"],
                      layer_scratch_bytes=sum(p.stat().st_size for p in self._files.values()),
                      layer_state_in_memory=int(not self.plan["spill"]))
        for field in next(iter(self.stats.values())):
            result["layer_" + field] = sum(s[field] for s in self.stats.values())
            for name, s in self.stats.items():
                result[f"layer_{name.lower()}_{field}"] = s[field]
        # Compatibility aliases now cover all workspaces, not only SX.
        result["layer_file_read_bytes"] = sum(s["file_read_bytes"] for s in self.stats.values())
        result["layer_file_write_bytes"] = sum(s["file_write_bytes"] for s in self.stats.values())
        return result

    def close(self):
        if self.closed:
            return
        if self._open:
            raise RuntimeError("cannot close while a height tile is borrowed")
        for key, entry in self._cache.items():
            if entry.dirty:
                self.stats[key[0]]["discarded_dirty_tiles"] += 1
        self._cache.clear()
        self.resident = 0
        self._files.clear()
        self.closed = True
        self._temp.cleanup()

    def __enter__(self):
        return self

    def __exit__(self, typ, value, traceback):
        self.close()


class _Workspace:
    def __init__(self, pool, name):
        self.pool, self.name = pool, name
        self.bytes = pool.plan["state_bytes"]
        self.in_memory = not pool.plan["spill"]
        self.peak_tile_bytes = pool.plan["tile_bytes"]

    def tile(self, row, stop, *, write=True):
        return self.pool.tile(self.name, row, stop, write=write)

    @property
    def scratch_bytes(self):
        return sum(p.stat().st_size for k, p in self.pool._files.items() if k[0] == self.name)

    @property
    def read_bytes(self):
        return self.pool.stats[self.name]["file_read_bytes"]

    @property
    def write_bytes(self):
        return self.pool.stats[self.name]["file_write_bytes"]


def report_workspace_metrics(values):
    """Mirror complete workspace counters into A/B logs, not capped UI receipts."""
    try:
        from rainpulse_algo.performance import observe
        for key, value in values.items():
            if key.startswith(('layer_', 'height_', 'peak_height_')):
                observe('cd.' + key, value, maximum=('peak' in key or 'budget' in key))
    except Exception:
        # Instrumentation failure must never replace a numerical failure/result.
        pass
