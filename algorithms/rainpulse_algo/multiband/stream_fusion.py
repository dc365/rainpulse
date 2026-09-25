"""Source-major fusion: each QC cut is consumed once, state is bounded/spillable.

Uses the SAME geometry, admission, score and reduction functions as eager fusion.
Only the loop/storage schedule changes. Stage all inputs before atomic publication;
a failure in the last cut cannot publish the preceding partial product.
"""

from __future__ import annotations

import shutil
import time
from collections import OrderedDict
from contextlib import contextmanager
from dataclasses import replace
from pathlib import Path

import numpy as np
from pyproj import Transformer

from .execution import ExecutionOptions
from .fusion import (
    allocate_layers,
    allocate_output,
    finish_composite,
    finish_tile,
    footprint,
    ground_geometry,
    prepare_polar,
    tile_coordinates,
    update_tile,
)
from .model import Network, epoch, json_bytes

FIELDS = ("score", "values", "winner", "wray", "wgate", "h", "age", "resolution")
DTYPE = np.dtype(
    [
        (
            name,
            "float64"
            if name == "score"
            else "int32"
            if name in {"winner", "wray", "wgate"}
            else "float32",
        )
        for name in FIELDS
    ]
)


class GeometryCache:
    def __init__(self, maximum_bytes):
        self.maximum = maximum_bytes
        self.entries = OrderedDict()
        self.bytes = self.peak = self.hits = self.misses = 0

    def get(self, key, calculate):
        if key in self.entries:
            self.hits += 1
            self.entries.move_to_end(key)
            return self.entries[key]
        self.misses += 1
        value = calculate()
        size = sum(a.nbytes for a in value)
        if size <= self.maximum:
            while self.entries and self.bytes + size > self.maximum:
                _, removed = self.entries.popitem(last=False)
                self.bytes -= sum(a.nbytes for a in removed)
            for a in value:
                a.setflags(write=False)
            self.entries[key] = value
            self.bytes += size
            self.peak = max(self.peak, self.bytes)
        return value


class LayerWorkspace:
    """Grid-height state only, never a station-by-grid-by-height array.

    When spilled, one structured mmap tile is open at a time. Page cache and
    filesystem traffic remain real resources: this does not promise an RSS cap.
    """

    def __init__(self, grid, options, root):
        self.grid, self.root = grid, Path(root)
        self.bytes = len(grid.levels_m_msl) * grid.height * grid.width * DTYPE.itemsize
        tile_bytes = (
            len(grid.levels_m_msl) * min(grid.tile_rows, grid.height) * grid.width * DTYPE.itemsize
        )
        if tile_bytes > options.maximum_tile_bytes:
            raise ValueError("height tile exceeds execution memory budget")
        self.in_memory = self.bytes <= options.layer_memory_bytes
        if not self.in_memory and self.bytes > options.maximum_scratch_bytes:
            raise ValueError("height state exceeds scratch budget")
        if not self.in_memory and self.bytes > shutil.disk_usage(root).free:
            raise ValueError("insufficient scratch space for height state")
        self.full = allocate_layers(grid, (grid.height, grid.width)) if self.in_memory else None
        self.paths = {}
        self.peak_tile_bytes = tile_bytes
        self.read_bytes = self.write_bytes = 0

    @contextmanager
    def tile(self, row, stop, *, write=True):
        if self.full is not None:
            yield tuple(a[:, row:stop, :] for a in self.full)
            return
        shape = (len(self.grid.levels_m_msl), stop - row, self.grid.width)
        path = self.root / f"layers-{row:06d}.bin"
        fresh = row not in self.paths
        mode = "w+" if fresh else "r+" if write else "r"
        memory = np.memmap(path, dtype=DTYPE, mode=mode, shape=shape)
        try:
            if fresh:
                for name in FIELDS:
                    memory[name][:] = (
                        -np.inf
                        if name == "score"
                        else -1
                        if name in {"winner", "wray", "wgate"}
                        else np.inf
                        if name in {"age", "resolution"}
                        else np.nan
                    )
                self.paths[row] = path
            else:
                self.read_bytes += memory.nbytes
            arrays = tuple(memory[name] for name in FIELDS)
            yield arrays
            if write or fresh:
                memory.flush()
                self.write_bytes += memory.nbytes
        finally:
            # No views may escape this context; callers never retain layer arrays.
            memory._mmap.close()

    @property
    def scratch_bytes(self):
        return 0 if self.in_memory else self.bytes

    def close(self):
        self.full = None
        for path in self.paths.values():
            path.unlink(missing_ok=True)
        self.paths.clear()


def build_composite_streaming(
    volumes,
    network: Network,
    product: str,
    analysis_time: str,
    cutoff: str,
    *,
    options: ExecutionOptions,
    directory,
    metrics=None,
    comparison=False,
):
    """Consume one-cut Volume objects in stable (station, scan, cut) order."""
    if product not in network.products:
        raise ValueError("unregistered product")
    grid = network.products[product]
    target, deadline = epoch(analysis_time), epoch(cutoff)
    if target > deadline or target % grid.cadence_seconds != 0:
        raise ValueError("target must be a minute boundary no later than input cutoff")
    stats = metrics if metrics is not None else {}
    compute_start = time.perf_counter()
    compute_seconds = 0.0
    out = allocate_output(grid)
    transform = Transformer.from_crs(grid.crs, "EPSG:4326", always_xy=True)
    geometry = GeometryCache(options.geometry_cache_bytes)
    workspace_options = replace(options, layer_memory_bytes=0) if comparison else options
    main_root = Path(directory) / "layers-sx" if comparison else directory
    if comparison:
        main_root.mkdir()
    workspace = LayerWorkspace(grid, workspace_options, main_root)
    band_out = {band: allocate_output(grid) for band in ("S", "X")} if comparison else {}
    band_workspaces = {}
    if comparison:
        if workspace.bytes * 3 > options.maximum_scratch_bytes:
            raise ValueError("S/X comparison height state exceeds aggregate scratch budget")
        if workspace.bytes * 3 > shutil.disk_usage(directory).free:
            raise ValueError("insufficient scratch space for S/X comparison state")
        for band in ("S", "X"):
            band_root = Path(directory) / f"layers-{band.lower()}"
            band_root.mkdir()
            band_workspaces[band] = LayerWorkspace(grid, workspace_options, band_root)
    sources, skipped = [], []
    band_sources = {"S": [], "X": []}
    band_skipped = {"S": [], "X": []}
    seen_bands = set()
    seen, last_order, station_identity = set(), None, None
    station_gates = station_cuts = task_gates = 0
    compute_seconds += time.perf_counter() - compute_start
    try:
        for v in volumes:
            compute_start = time.perf_counter()
            if len(v.sweeps) != 1:
                raise ValueError("streaming input must contain exactly one cut")
            sid = v.metadata["radar_id"]
            station = network.stations[sid]
            v.validate(station)
            if not station.enabled or not station.geometry_verified:
                raise ValueError("disabled or unverified station")
            if v.metadata.get("network_sha256") != network.sha256:
                raise ValueError("volume was not translated through this network release")
            s = v.sweeps[0]
            order = (sid, v.metadata["scan_id"], s.number)
            if last_order is not None and order <= last_order:
                raise ValueError("stream is duplicated or not in canonical source order")
            new_station = last_order is None or sid != last_order[0]
            if new_station:
                if sid in seen or len(seen) >= 16:
                    raise ValueError("one to sixteen unique station observations are required")
                seen.add(sid)
                station_gates = station_cuts = 0
                station_identity = json_bytes(v.metadata)
            elif json_bytes(v.metadata) != station_identity:
                raise ValueError("cut metadata changed inside one streamed volume")
            station_gates += s.fields["DBZH"].size
            task_gates += s.fields["DBZH"].size
            station_cuts += 1
            if (
                station_cuts > options.maximum_sweeps
                or station_gates > options.maximum_volume_gates
                or task_gates > options.maximum_task_gates
            ):
                raise ValueError("streamed source exceeds cumulative cut/gate budget")
            if v.nbytes > options.maximum_qc_cut_bytes:
                raise ValueError("QC cut exceeds execution budget")
            stats["peak_qc_cut_array_bytes"] = max(
                stats.get("peak_qc_cut_array_bytes", 0), v.nbytes
            )
            stats["processed_cuts"] = stats.get("processed_cuts", 0) + 1
            stats["processed_gates"] = task_gates
            last_order = order
            reason = None
            if (
                epoch(v.metadata["volume_end"]) > target
                or epoch(v.metadata["available_at"]) > deadline
            ):
                reason = "future_observation_or_arrival"
            elif target - epoch(v.metadata["volume_end"]) > station.maximum_age_seconds:
                reason = "expired"
            if reason:
                if new_station:
                    item = {"radar_id": sid, "reason": reason}
                    skipped.append(item)
                    if comparison:
                        band_skipped[station.band].append(item)
                del v, s
                compute_seconds += time.perf_counter() - compute_start
                continue
            for key in ("DBZH_QC", "REFLECTIVITY_ELIGIBLE_FOR_CR", "QUALITY_SCORE"):
                if key not in s.fields:
                    raise ValueError("fusion requires the explicit QC output contract: " + key)
            q = s.fields["QUALITY_SCORE"]
            if np.any(~np.isfinite(q)) or np.any((q < 0) | (q > 1)):
                raise ValueError("invalid fusion quality score")
            index = len(sources)
            source_record = {
                    "index": index,
                    "radar_id": sid,
                    "band": station.band,
                    "scan_id": v.metadata["scan_id"],
                    "sweep_number": s.number,
                    "asset_sha256": v.metadata["asset_sha256"],
                    "input_uri": v.metadata.get("input_uri"),
                    "frequency_hz": station.frequency_hz,
                    "site_altitude_m_msl": station.altitude_m_msl,
                    "volume_end": v.metadata["volume_end"],
                    "available_at": v.metadata["available_at"],
                    "scan_type": v.metadata["scan_type"],
                    "qc_version": v.metadata.get(
                        "qc_pipeline_version", v.metadata.get("processing")
                    ),
                    "calibration_id": station.calibration_id,
                    "network_sha256": network.sha256,
                }
            sources.append(source_record)
            if comparison:
                seen_bands.add(station.band)
                band_record = {
                    **source_record,
                    "index": len(band_sources[station.band]),
                }
                band_sources[station.band].append(band_record)
                band_index = band_record["index"]
            prepared = prepare_polar(s)
            for row in range(0, grid.height, grid.tile_rows):
                stop = min(row + grid.tile_rows, grid.height)
                sl = np.s_[row:stop, :]
                lon, lat = geometry.get(
                    ("grid", row), lambda: tile_coordinates(grid, transform, row, stop)
                )
                ground = geometry.get(
                    ("station", sid, row), lambda: ground_geometry(station, lon, lat)
                )
                fp = footprint(s, station, lon, lat, target, ground=ground, prepared=prepared)
                with workspace.tile(row, stop) as layers:
                    update_tile(
                        layers,
                        out,
                        sl,
                        fp,
                        s,
                        station,
                        index,
                        grid,
                        backend=options.selection_backend,
                    )
                if comparison:
                    with band_workspaces[station.band].tile(row, stop) as layers:
                        update_tile(
                            layers,
                            band_out[station.band],
                            sl,
                            fp,
                            s,
                            station,
                            band_index,
                            grid,
                            backend=options.selection_backend,
                        )
            del v, s, prepared, fp, q
            compute_seconds += time.perf_counter() - compute_start
        compute_start = time.perf_counter()
        if not seen:
            raise ValueError("nonempty stream required")
        for row in range(0, grid.height, grid.tile_rows):
            stop = min(row + grid.tile_rows, grid.height)
            with workspace.tile(row, stop, write=False) as layers:
                finish_tile(layers, out, np.s_[row:stop, :], grid)
            if comparison:
                for band in ("S", "X"):
                    with band_workspaces[band].tile(row, stop, write=False) as layers:
                        finish_tile(layers, band_out[band], np.s_[row:stop, :], grid)
        stats.update(
            height_state_bytes=workspace.bytes * (3 if comparison else 1),
            layer_scratch_bytes=workspace.scratch_bytes
            + sum(item.scratch_bytes for item in band_workspaces.values()),
            peak_height_tile_bytes=workspace.peak_tile_bytes,
            layer_file_read_bytes=workspace.read_bytes,
            layer_file_write_bytes=workspace.write_bytes,
            geometry_cache_peak_bytes=geometry.peak,
            geometry_hits=geometry.hits,
            geometry_misses=geometry.misses,
            layer_state_in_memory=int(workspace.in_memory),
        )
        result = finish_composite(
            out, grid, network, product, analysis_time, cutoff, sources, skipped
        )
        if comparison:
            result.band_comparisons = {
                band: finish_composite(
                    band_out[band],
                    grid,
                    network,
                    product,
                    analysis_time,
                    cutoff,
                    band_sources[band],
                    band_skipped[band],
                )
                if band in seen_bands
                else None
                for band in ("S", "X")
            }
        compute_seconds += time.perf_counter() - compute_start
        stats["fusion_ms"] = compute_seconds * 1000
        return result
    finally:
        workspace.close()
        for item in band_workspaces.values():
            item.close()
