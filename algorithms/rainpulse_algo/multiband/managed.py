# ruff: noqa: E501, I001
"""Existing operations Worker integration, with bounded cross-task decoded reuse."""

from __future__ import annotations

from collections import OrderedDict
from pathlib import Path
import os
import time
import json
import hashlib
from collections.abc import Callable

import numpy as np

from .adapters import FIELDS, X_QC_FIELDS, read_volume, read_x_qc_sweep
from .fusion import build_composite
from .model import MAX_SWEEPS, Network, Volume, epoch
from .product import sx_comparison_objects, x_qc_objects
from .quality import x_qc, accept_s_qc

MAX_X_QC_INPUT_BYTES = 512 * 1024**2


class VolumeCache:
    def __init__(
        self,
        max_bytes: int,
        ttl_seconds: int,
        *,
        clock: Callable[[], float] = time.monotonic,
        maximum_entries: int = 32,
    ):
        if (
            max_bytes < 0
            or ttl_seconds <= 0
            or type(maximum_entries) is not int
            or not 1 <= maximum_entries <= 1024
        ):
            raise ValueError("invalid decoded cache budget")
        self.maximum, self.ttl, self.clock = max_bytes, ttl_seconds, clock
        self.maximum_entries = maximum_entries
        self.entries: OrderedDict[tuple, tuple[float, Volume]] = OrderedDict()
        self.bytes = 0
        self.hits = self.misses = 0

    def get(self, key: tuple):
        now = self.clock()
        for old in list(self.entries):
            if self.entries[old][0] <= now:
                self.bytes -= self.entries.pop(old)[1].nbytes
        if key not in self.entries:
            self.misses += 1
            return None
        self.hits += 1
        self.entries.move_to_end(key)
        return self.entries[key][1]

    def put(self, key: tuple, value: Volume):
        if self.maximum == 0 or value.nbytes > self.maximum:
            return
        if key in self.entries:
            self.bytes -= self.entries.pop(key)[1].nbytes
        while self.entries and (
            self.bytes + value.nbytes > self.maximum or len(self.entries) >= self.maximum_entries
        ):
            self.bytes -= self.entries.popitem(last=False)[1][1].nbytes
        for s in value.sweeps:
            for a in [
                s.azimuth_deg,
                s.range_m,
                s.elevation_deg,
                s.ray_time_epoch,
                *s.fields.values(),
            ]:
                a.setflags(write=False)
        self.entries[key] = (self.clock() + self.ttl, value)
        self.bytes += value.nbytes


class Executor:
    def __init__(
        self,
        path: str | Path,
        *,
        reject_mask: int = 0,
        flag_version: str = "",
        execution=None,
        execution_path=None,
    ):
        from .execution import ExecutionOptions
        from .selection_kernel import warmup

        self.execution_path = None if execution_path is None else Path(execution_path)
        if execution is not None and execution_path is not None:
            raise ValueError("supply execution policy or path, not both")
        self.execution = (
            ExecutionOptions.load(execution_path)
            if execution_path is not None
            else execution
            if execution is not None
            else ExecutionOptions()
        )
        self.execution_policy_sha256 = (
            hashlib.sha256(self.execution_path.read_bytes()).hexdigest()
            if self.execution_path is not None
            else self.execution.digest
        )
        warmup(self.execution.selection_backend)
        self.path = Path(path)
        self.network = Network.load(path)
        self.cache = VolumeCache(self.network.cache_max_bytes, self.network.cache_ttl_seconds)
        self.reject_mask, self.flag_version = reject_mask, flag_version
        self.cut_cache = VolumeCache(
            self.network.cache_max_bytes,
            self.network.cache_ttl_seconds,
            maximum_entries=self.execution.maximum_cut_cache_entries,
        )

    def execute(self, request: dict, reader, *, artifact_digest: Callable[[dict], str]):
        started = time.perf_counter()
        p = request["payload"]
        actual = Network.load(self.path)
        if p["network_sha256"] != self.network.sha256 or actual.sha256 != self.network.sha256:
            raise ValueError("network release differs from frozen plan/startup")
        if request.get("event_type") != "ops.multiband.requested.v1" or p["mode"] not in {
            "x_qc",
            "sx_composite",
        }:
            raise ValueError("invalid multi-band task contract")
        inputs = p["sources"]
        if not 1 <= len(inputs) <= 16 or len({s["radar_id"] for s in inputs}) != len(inputs):
            raise ValueError("invalid/duplicate station inputs")
        if epoch(p["input_cutoff"]) > epoch(request["occurred_at"]):
            raise ValueError("input cutoff cannot exceed the frozen task creation time")
        if p["mode"] == "x_qc" and len(inputs) != 1:
            raise ValueError("standalone X QC tasks contain exactly one scan")
        if p["mode"] == "sx_composite" and p["product_id"] not in self.network.products:
            raise ValueError("product is not in frozen network release")
        if self.execution_path is not None:
            from .execution import ExecutionOptions

            current_bytes = self.execution_path.read_bytes()
            if (
                ExecutionOptions.load(self.execution_path).digest != self.execution.digest
                or hashlib.sha256(current_bytes).hexdigest() != self.execution_policy_sha256
            ):
                raise ValueError("execution settings differ from worker startup")
        if p.get("execution_sha256") != self.execution_policy_sha256:
            raise ValueError("execution settings differ from frozen task")
        if p["mode"] == "x_qc" and not self.execution.streaming:
            return self._execute_x_qc(request, reader, artifact_digest, started)
        if self.execution.streaming:
            from .stream_managed import execute

            return execute(self, request, reader, started=started)
        volumes = []
        read_ms = qc_ms = 0.0
        for source in inputs:
            station = self.network.stations[source["radar_id"]]
            available = station.enabled or p["mode"] == "x_qc" and station.x_qc_enabled
            if not available or (p["mode"] == "x_qc" and station.band != "X"):
                raise ValueError("disabled/wrong-band task source")
            mark = time.perf_counter()
            # Read a fresh verified marker even on decoded cache hits. This
            # reuses batch-2 sessions rather than creating a second I/O system.
            # Packed schema 3 still performs full integrity verification on a
            # miss; schema 1/2 can omit unrelated diagnostic arrays.
            session = reader.open(source["input_uri"]) if hasattr(reader, "open") else None
            objects = None
            if session is not None:
                sha = session.index.sha256
            else:
                objects = reader.load(source["input_uri"])
                sha = artifact_digest(objects)
            read_ms += (time.perf_counter() - mark) * 1000
            key = (
                source["input_uri"],
                station.radar_id,
                source["scan_id"],
                sha,
                self.network.sha256,
                source["available_at"],
                source["volume_start"],
                source["volume_end"],
                self.flag_version,
                self.reject_mask,
            )
            v = self.cache.get(key)
            if v is None:
                if session is not None:
                    mark = time.perf_counter()
                    objects = session.load(
                        keys=selected_source_keys(session.index.logical, station.source)
                    )
                    read_ms += (time.perf_counter() - mark) * 1000
                mark = time.perf_counter()
                v = read_volume(
                    objects,
                    station,
                    source,
                    asset_sha256=sha,
                    maximum_bytes=self.network.maximum_input_bytes,
                    s_reject_mask=self.reject_mask,
                    expected_flag_version=self.flag_version,
                )
                v.metadata["input_uri"] = source["input_uri"]
                v = (
                    x_qc(v, station, self.network.sha256)
                    if station.band == "X"
                    else accept_s_qc(v, station, self.network.sha256)
                )
                qc_ms += (time.perf_counter() - mark) * 1000
                self.cache.put(key, v)
            volumes.append(v)
            del objects
            if sum(v.nbytes for v in volumes) > self.network.maximum_input_bytes:
                raise ValueError("selected decoded volumes exceed aggregate resident input budget")
        mark = time.perf_counter()
        composite = build_composite(
            volumes, self.network, p["product_id"], p["analysis_time"], p["input_cutoff"]
        )
        fusion_ms = (time.perf_counter() - mark) * 1000
        comparison_mark = time.perf_counter()
        band_results = {
            band: (
                build_composite(
                    [v for v in volumes if v.metadata["band"] == band],
                    self.network,
                    p["product_id"],
                    p["analysis_time"],
                    p["input_cutoff"],
                )
                if any(v.metadata["band"] == band for v in volumes)
                else None
            )
            for band in ("S", "X")
        }
        fusion_ms += (time.perf_counter() - comparison_mark) * 1000
        objects = sx_comparison_objects(composite, band_results)
        summary = {
            "multiband": {
                k: composite.metadata[k]
                for k in (
                    "product_id",
                    "analysis_time",
                    "network_release",
                    "network_sha256",
                    "valid_echo_cells",
                    "valid_no_echo_cells",
                    "missing_cells",
                )
            },
            "candidate_only": True,
            "operational_eligible": False,
            "qpe_enabled": False,
            "detail": "manifest.json",
            "mode": p["mode"],
        }
        metrics = {
            "input_read_ms": read_ms,
            "station_qc_ms": qc_ms,
            "fusion_ms": fusion_ms,
            "total_ms": (time.perf_counter() - started) * 1000,
            "resident_input_bytes": float(sum(v.nbytes for v in volumes)),
            "decoded_cache_bytes": float(self.cache.bytes),
            "decoded_cache_hits": float(self.cache.hits),
            "decoded_cache_misses": float(self.cache.misses),
            "output_object_count": float(len(objects)),
        }
        return objects, summary, metrics

    def _execute_x_qc(self, request: dict, reader, artifact_digest: Callable[[dict], str], started: float):
        p = request["payload"]
        source = p["sources"][0]
        station = self.network.stations[source["radar_id"]]
        if station.band != "X" or not (station.enabled or station.x_qc_enabled):
            raise ValueError("X station is not enabled for standalone QC")
        if epoch(source["available_at"]) > epoch(p["input_cutoff"]):
            raise ValueError("X input was not available by the frozen task cutoff")
        session = reader.open(source["input_uri"]) if hasattr(reader, "open") else None
        if station.source != "normalized_zarr":
            started_read = time.perf_counter()
            if session is not None:
                objects = session.load(keys=selected_source_keys(session.index.logical, station.source))
                sha = session.index.sha256
            else:
                objects = reader.load(source["input_uri"])
                sha = artifact_digest(objects)
            read_ms = (time.perf_counter()-started_read)*1000
            volume = read_volume(objects, station, source, asset_sha256=sha,
                                 maximum_bytes=min(self.network.maximum_input_bytes, MAX_X_QC_INPUT_BYTES))
            started_qc = time.perf_counter()
            result = x_qc(volume, station, self.network.sha256)
            output = x_qc_objects(result)
            manifest = json.loads(output["manifest.json"])
            summary = {"candidate_only": True, "operational_eligible": False, "qpe_enabled": False,
                       "detail": "manifest.json", "mode": "x_qc", "radar_id": source["radar_id"],
                       "scan_id": source["scan_id"], "sweeps": len(manifest["comparison"]["sweeps"]),
                       "quality_semantics": "candidate-heuristic-not-probability-v1"}
            metrics = {"input_read_ms": read_ms, "station_qc_ms": (time.perf_counter()-started_qc)*1000,
                       "fusion_ms": 0.0, "total_ms": (time.perf_counter()-started)*1000,
                       "resident_input_bytes": float(result.nbytes), "decoded_input_bytes": float(volume.nbytes),
                       "decoded_cache_bytes": float(self.cache.bytes), "decoded_cache_hits": float(self.cache.hits),
                       "decoded_cache_misses": float(self.cache.misses), "output_object_count": float(len(output))}
            return output, summary, metrics
        raw_objects = None
        if session is not None:
            sha = session.index.sha256
        else:
            raw_objects = reader.load(source["input_uri"])
            sha = artifact_digest(raw_objects)

        read_ms = 0.0
        qc_ms = 0.0
        decoded_bytes = 0
        decoded_peak = 0
        gate_count = 0

        def qc_sweeps():
            nonlocal read_ms, qc_ms, decoded_bytes, decoded_peak, gate_count
            for raw, elapsed, decoded in _x_qc_sweeps(
                session, raw_objects, station, source, sha,
                min(self.network.maximum_input_bytes, MAX_X_QC_INPUT_BYTES),
            ):
                read_ms += elapsed
                decoded_bytes += decoded
                gate_count += int(raw.sweeps[0].fields["DBZH"].size)
                if gate_count > 32_000_000:
                    raise ValueError("X QC volume exceeds 32 million gate budget")
                mark = time.perf_counter()
                qc = x_qc(raw, station, self.network.sha256)
                qc_ms += (time.perf_counter()-mark)*1000
                decoded_peak = max(decoded_peak, qc.nbytes)
                yield qc

        objects = x_qc_objects(qc_sweeps())
        manifest = json.loads(objects["manifest.json"])
        summary = {"candidate_only": True, "operational_eligible": False, "qpe_enabled": False,
                   "detail": "manifest.json", "mode": "x_qc", "radar_id": source["radar_id"],
                   "scan_id": source["scan_id"], "sweeps": len(manifest["comparison"]["sweeps"]),
                   "quality_semantics": "candidate-heuristic-not-probability-v1"}
        metrics = {"input_read_ms": read_ms, "station_qc_ms": qc_ms, "fusion_ms": 0.0,
                   "total_ms": (time.perf_counter()-started)*1000, "resident_input_bytes": float(decoded_peak),
                   "decoded_input_bytes": float(decoded_bytes), "decoded_cache_bytes": float(self.cache.bytes),
                   "decoded_cache_hits": float(self.cache.hits), "decoded_cache_misses": float(self.cache.misses),
                   "output_object_count": float(len(objects))}
        return objects, summary, metrics


def selected_source_keys(keys, source_format: str, *, x_qc_only: bool = False) -> list[str]:
    """Select native coordinates, admission masks and numerical fields only."""
    if source_format == "native_bundle":
        selected = ["volume.json", "arrays.npz"]
        if not all(k in keys for k in selected):
            raise ValueError("canonical native bundle is incomplete")
        return selected
    selected = []
    coordinates = {"azimuth", "range", "elevation", "ray_time"}
    root_arrays = {"sweep_number"}
    fields = X_QC_FIELDS if x_qc_only else FIELDS
    for key in keys:
        parts = key.split("/")
        if key in {".zattrs", ".zgroup", ".zmetadata"} or parts[0] in root_arrays:
            selected.append(key)
        elif parts[0].startswith("sweep_"):
            if len(parts) == 2 and parts[1] in {".zattrs", ".zgroup"}:
                selected.append(key)
            elif len(parts) >= 3 and (
                parts[1] in fields | coordinates or (not x_qc_only and parts[1].endswith("CR_WITHHELD_MASK"))
            ):
                selected.append(key)
    if ".zattrs" not in selected or ".zgroup" not in selected:
        raise ValueError("input lacks native Zarr root metadata")
    return sorted(selected)


def _x_qc_sweeps(session, raw_objects: dict[str, bytes] | None, station, source: dict, sha: str, maximum_bytes: int):
    """Yield decoded X sweeps, loading one normalized Zarr sweep at a time."""
    if session is None:
        all_keys = list(raw_objects or {})
        selected = selected_source_keys(all_keys, station.source, x_qc_only=True)
        all_objects = {key: raw_objects[key] for key in selected}
        compressed_size = sum(map(len, all_objects.values()))
        if compressed_size > maximum_bytes:
            raise ValueError("selected compressed X input exceeds network budget")
        root_keys = _x_qc_root_keys(selected)
        numbers = _zarr_sweep_numbers({key: all_objects[key] for key in root_keys})
        def load_sweep(number, selected_objects=all_objects):
            prefix = f"sweep_{number:03d}/"
            return {key: value for key, value in selected_objects.items() if key in root_keys or key.startswith(prefix)}
    else:
        logical_keys = list(session.index.logical)
        selected = selected_source_keys(logical_keys, station.source, x_qc_only=True)
        if session.index.schema == "3.0":
            compressed_size = sum(entry.size for entry in session.index.physical.values())
        else:
            compressed_size = sum(session.index.logical[key][2] for key in selected)
        if compressed_size > maximum_bytes:
            raise ValueError("selected compressed X input exceeds network budget")
        if session.index.schema == "3.0":
            all_objects = session.load(keys=selected)
            root_keys = _x_qc_root_keys(selected)
            root_objects = {key: value for key, value in all_objects.items() if key in root_keys}
            numbers = _zarr_sweep_numbers(root_objects)
            def load_sweep(number):
                prefix = f"sweep_{number:03d}/"
                return {key: value for key, value in all_objects.items() if key in root_keys or key.startswith(prefix)}
        else:
            root_keys = _x_qc_root_keys(selected)
            root_objects = session.load(keys=root_keys)
            numbers = _zarr_sweep_numbers(root_objects)
            def load_sweep(number):
                prefix = f"sweep_{number:03d}/"
                group_keys = [key for key in selected if key.startswith(prefix)]
                return {**root_objects, **session.load(keys=group_keys)}

    if not 1 <= len(numbers) <= MAX_SWEEPS:
        raise ValueError("X volume sweep count exceeds budget")
    decoded_total = 0
    for number in numbers:
        started = time.perf_counter()
        sweep_objects = load_sweep(number)
        volume, decoded = read_x_qc_sweep(sweep_objects, station, source, int(number), asset_sha256=sha,
                                          maximum_bytes=maximum_bytes-decoded_total)
        elapsed = (time.perf_counter()-started)*1000
        decoded_total += decoded
        if decoded_total > maximum_bytes:
            raise ValueError("decoded X sweeps exceed network budget")
        if volume is not None:
            yield volume, elapsed, decoded


def _x_qc_root_keys(keys) -> list[str]:
    return sorted(key for key in keys if key in {".zattrs", ".zgroup", ".zmetadata"} or key.startswith("sweep_number/"))


def _zarr_sweep_numbers(objects: dict[str, bytes]) -> list[int]:
    import zarr
    from zarr.storage import MemoryStore
    store = MemoryStore()
    store.update(objects)
    root = zarr.open_group(store=store, mode="r")
    return _validated_sweep_numbers(root["sweep_number"])


def _validated_sweep_numbers(index) -> list[int]:
    shape = tuple(index.shape)
    dtype = np.dtype(index.dtype)
    if len(shape) != 1 or not 1 <= shape[0] <= MAX_SWEEPS or dtype.kind not in "iu":
        raise ValueError("invalid or oversized X sweep index")
    values = np.asarray(index[:])
    numbers = [int(number) for number in values]
    if len(set(numbers)) != len(numbers) or any(number < 0 or number > 4096 for number in numbers):
        raise ValueError("duplicate or invalid X sweep number")
    return sorted(numbers)


def existing_runtime_executor() -> Executor:
    """Only the legacy import adapter depends on S-specific QC admission rules."""
    import yaml
    from rainpulse_algo.diagnostics.renderer import BUSINESS_HARD_REJECT_FLAG_NAMES

    value = yaml.safe_load(Path(os.environ["RAINPULSE_QC_FLAG_DEFINITIONS"]).read_text())
    mask = 0
    names = set()
    for f in value["flags"]:
        if f["name"] in BUSINESS_HARD_REJECT_FLAG_NAMES:
            mask |= int(f["mask"])
            names.add(f["name"])
    if names != set(BUSINESS_HARD_REJECT_FLAG_NAMES) or mask <= 0:
        raise ValueError("legacy hard-reject flag set is incomplete")
    return Executor(
        os.environ["RAINPULSE_MULTIBAND_CONFIG"],
        reject_mask=mask,
        flag_version=value["definition_version"],
        execution_path=os.getenv("RAINPULSE_MULTIBAND_EXECUTION_CONFIG"),
    )
