"""Streaming implementation behind the EXISTING multiband Executor.

No second queue, publisher, QC algorithm, or task registration. Source manifests
stay fresh and frozen; private scratch is removed on success and exceptions.
"""

from __future__ import annotations

from rainpulse_algo.performance import (timed as _perf_timed)

import importlib.metadata
import json
import platform
import time
from pathlib import Path
from tempfile import TemporaryDirectory

from .model import MAX_SWEEPS, epoch, json_bytes
from .product import MAX_X_QC_PREVIEW_BYTES, sx_comparison_objects, x_qc_objects
from .quality import accept_s_qc, x_qc
from .stream_fusion import DTYPE, build_composite_streaming
from .stream_io import GroupCuts, NPZCuts


def _scratch_reservation(height_bytes, options, *, comparison):
    # S, X and fused layers all spill in comparison mode, regardless of the
    # single-layer memory threshold. Reserve their aggregate before staging.
    height_scratch = (
        height_bytes
        if comparison or height_bytes > options.layer_memory_bytes
        else 0
    )
    reserved = height_scratch * 3 + options.maximum_object_bytes
    if reserved >= options.maximum_scratch_bytes:
        raise ValueError("execution scratch budget cannot hold selected workspace")
    return reserved


@_perf_timed("multiband.streaming_pipeline")
def execute(executor, request, reader, *, started):
    p, options, network = request["payload"], executor.execution, executor.network
    if not hasattr(reader, "open"):
        raise ValueError("streaming requires the existing verified manifest reader")
    if p["mode"] == "x_qc":
        return _execute_x_qc(executor, request, reader, started=started)
    inputs = sorted(p["sources"], key=lambda x: (x["radar_id"], x["scan_id"]))
    grid = network.products[p["product_id"]]
    height_bytes = grid.width * grid.height * len(grid.levels_m_msl) * DTYPE.itemsize
    # One station's packed source + seekable NPZ + height workspace + output coexist.
    reserved = _scratch_reservation(
        height_bytes, options, comparison=p["mode"] == "sx_composite"
    )
    metrics = {
        "input_read_ms": 0.0,
        "station_qc_ms": 0.0,
        "decode_adapt_ms": 0.0,
        "encode_ms": 0.0,
        "packed_staged_bytes": 0,
        "input_download_bytes": 0,
        "peak_physical_object_bytes": 0,
        "peak_input_cut_array_bytes": 0,
        "metadata_cache_hits": 0,
        "peak_metadata_cache_bytes": 0,
        "qc_executions": 0,
        "decoded_cut_cache_hits": 0,
        "decoded_cut_cache_misses": 0,
    }
    versions = {name: importlib.metadata.version(name) for name in ("numpy", "scipy", "pyproj")}
    if options.selection_backend == "numba":
        versions["numba"] = importlib.metadata.version("numba")
    receipt = {
        "version": options.version,
        "policy_sha256": options.digest,
        "streaming": True,
        "selection_backend": options.selection_backend,
        "runtime_versions": versions,
        "python": platform.python_version(),
    }
    with TemporaryDirectory(prefix="rainpulse-multiband-", dir=options.scratch_parent) as root:
        def cut_stream():
            from .managed import selected_source_keys

            for source in inputs:
                station = network.stations[source["radar_id"]]
                if not station.enabled:
                    raise ValueError("disabled/wrong-band task source")
                mark = time.perf_counter()
                session = reader.open(source["input_uri"])
                if not hasattr(session, "staged"):
                    raise ValueError("verified reader lacks bounded staging support")
                keys = selected_source_keys(
                    session.index.logical, station.source, x_qc_only=station.band == "X"
                )
                with session.staged(
                    directory=root,
                    maximum_disk_bytes=options.maximum_scratch_bytes - reserved,
                    maximum_object_bytes=options.maximum_object_bytes,
                    keys=keys,
                ) as mapping:
                    metrics["input_read_ms"] += (time.perf_counter() - mark) * 1000
                    metrics["packed_staged_bytes"] += mapping.stats["staged_bytes"]
                    npz_path = Path(root) / "input-arrays.npz"
                    cuts = None
                    try:
                        mark = time.perf_counter()
                        if station.source == "native_bundle":
                            mapping.copy_logical_to("arrays.npz", npz_path)
                            cuts = NPZCuts(
                                mapping["volume.json"],
                                npz_path,
                                station,
                                source,
                                sha256=session.index.sha256,
                                options=options,
                                maximum_bytes=network.maximum_input_bytes,
                            )
                        else:
                            import zarr
                            from zarr.storage import KVStore

                            group = zarr.open_group(store=KVStore(mapping), mode="r")
                            cuts = GroupCuts(
                                group,
                                station,
                                source,
                                sha256=session.index.sha256,
                                options=options,
                                maximum_bytes=network.maximum_input_bytes,
                                reject_mask=executor.reject_mask,
                                flag_version=executor.flag_version,
                            )
                        metrics["decode_adapt_ms"] += (time.perf_counter() - mark) * 1000
                        base_key = (
                            source["input_uri"],
                            station.radar_id,
                            source["scan_id"],
                            session.index.sha256,
                            network.sha256,
                            source["available_at"],
                            source["volume_start"],
                            source["volume_end"],
                            executor.flag_version,
                            executor.reject_mask,
                        )
                        for number in cuts.numbers:
                            value = executor.cut_cache.get((*base_key, number))
                            if value is None:
                                metrics["decoded_cut_cache_misses"] += 1
                                mark = time.perf_counter()
                                raw = cuts.read(number)
                                raw.metadata["input_uri"] = source["input_uri"]
                                raw.validate(station, require_geometry=station.enabled)
                                metrics["peak_input_cut_array_bytes"] = max(
                                    metrics["peak_input_cut_array_bytes"], raw.nbytes
                                )
                                if raw.nbytes > min(
                                    network.maximum_input_bytes, options.maximum_cut_bytes
                                ):
                                    raise ValueError("input cut exceeds execution budget")
                                metrics["decode_adapt_ms"] += (time.perf_counter() - mark) * 1000
                                mark = time.perf_counter()
                                value = (
                                    x_qc(raw, station, network.sha256)
                                    if station.band == "X"
                                    else accept_s_qc(raw, station, network.sha256)
                                )
                                metrics["station_qc_ms"] += (time.perf_counter() - mark) * 1000
                                metrics["qc_executions"] += 1
                                del raw
                                if value.nbytes > options.maximum_qc_cut_bytes:
                                    raise ValueError("QC cut exceeds execution output budget")
                                executor.cut_cache.put((*base_key, number), value)
                            else:
                                metrics["decoded_cut_cache_hits"] += 1
                            yield value
                            del value
                    finally:
                        if isinstance(cuts, NPZCuts):
                            cuts.close()
                        npz_path.unlink(missing_ok=True)
                    metrics["metadata_cache_hits"] += mapping.stats["metadata_cache_hits"]
                    metrics["peak_metadata_cache_bytes"] = max(
                        metrics["peak_metadata_cache_bytes"],
                        mapping.stats["metadata_cache_peak_bytes"],
                    )
                    metrics["input_download_bytes"] += session.stats["download_bytes"]
                    metrics["peak_physical_object_bytes"] = max(
                        metrics["peak_physical_object_bytes"], mapping.stats["peak_object_bytes"]
                    )

        mark = time.perf_counter()
        stream = cut_stream()
        try:
            composite = build_composite_streaming(
                stream,
                network,
                p["product_id"],
                p["analysis_time"],
                p["input_cutoff"],
                options=options,
                directory=root,
                metrics=metrics,
                comparison=True,
            )
        finally:
            stream.close()
        # Source-major fuse wall time includes generator reads/QC. Do not call
        # it pure fusion time; pure attribution needs substage instrumentation.
        metrics["stream_pipeline_ms"] = (time.perf_counter() - mark) * 1000
        composite.metadata["execution"] = receipt
        mark = time.perf_counter()
        objects = sx_comparison_objects(composite, composite.band_comparisons)
        metrics["encode_ms"] += (time.perf_counter() - mark) * 1000
        output_bytes = sum(map(len, objects.values()))
        if output_bytes > options.maximum_output_bytes:
            raise ValueError("complete encoded output exceeds execution byte budget")
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
            "execution": receipt,
        }
        metrics.update(
            total_ms=(time.perf_counter() - started) * 1000,
            resident_input_bytes=metrics.get("peak_qc_cut_array_bytes", 0),
            decoded_cache_bytes=executor.cut_cache.bytes,
            output_object_count=len(objects),
            output_encoded_bytes=output_bytes,
            streaming=1,
        )
        return objects, summary, {k: float(v) for k, v in metrics.items()}


@_perf_timed("x.streaming_standalone")
def _execute_x_qc(executor, request, reader, *, started):
    """Run geometry-free X QC inside the same bounded streaming Worker path."""
    p, options, network = request["payload"], executor.execution, executor.network
    source = p["sources"][0]
    station = network.stations[source["radar_id"]]
    if station.band != "X" or not (station.enabled or station.x_qc_enabled):
        raise ValueError("X station is not enabled for standalone QC")
    if epoch(source["available_at"]) > epoch(p["input_cutoff"]):
        raise ValueError("X input was not available by the frozen task cutoff")
    session = reader.open(source["input_uri"])
    if not hasattr(session, "staged"):
        raise ValueError("verified reader lacks bounded staging support")
    output_budget = min(options.maximum_output_bytes, MAX_X_QC_PREVIEW_BYTES)
    reserved = output_budget + options.maximum_object_bytes
    if reserved >= options.maximum_scratch_bytes:
        raise ValueError("execution scratch budget cannot hold X QC preview workspace")
    metrics = {
        "input_read_ms": 0.0,
        "station_qc_ms": 0.0,
        "decode_adapt_ms": 0.0,
        "encode_ms": 0.0,
        "packed_staged_bytes": 0,
        "input_download_bytes": 0,
        "peak_physical_object_bytes": 0,
        "peak_input_cut_array_bytes": 0,
        "metadata_cache_hits": 0,
        "peak_metadata_cache_bytes": 0,
        "qc_executions": 0,
        "decoded_cut_cache_hits": 0,
        "decoded_cut_cache_misses": 0,
        "gate_count": 0,
    }
    versions = {name: importlib.metadata.version(name) for name in ("numpy", "scipy", "pyproj")}
    receipt = {
        "version": options.version,
        "policy_sha256": options.digest,
        "streaming": True,
        "selection_backend": options.selection_backend,
        "runtime_versions": versions,
        "python": platform.python_version(),
    }
    gate_limit = min(options.maximum_volume_gates, options.maximum_task_gates, 32_000_000)
    from .managed import selected_source_keys

    with TemporaryDirectory(prefix="rainpulse-x-qc-", dir=options.scratch_parent) as root:
        keys = selected_source_keys(session.index.logical, station.source, x_qc_only=True)
        mark = time.perf_counter()
        staged = session.staged(
            directory=root,
            maximum_disk_bytes=options.maximum_scratch_bytes - reserved,
            maximum_object_bytes=options.maximum_object_bytes,
            keys=keys,
        )

        def qc_volumes():
            with staged as mapping:
                metrics["input_read_ms"] += (time.perf_counter() - mark) * 1000
                metrics["packed_staged_bytes"] += mapping.stats["staged_bytes"]
                npz_path = Path(root) / "input-arrays.npz"
                cuts = None
                try:
                    adapt_mark = time.perf_counter()
                    if station.source == "native_bundle":
                        mapping.copy_logical_to("arrays.npz", npz_path)
                        cuts = NPZCuts(
                            mapping["volume.json"], npz_path, station, source,
                            sha256=session.index.sha256, options=options,
                            maximum_bytes=network.maximum_input_bytes,
                            sweep_limit=MAX_SWEEPS,
                        )
                    else:
                        import zarr
                        from zarr.storage import KVStore

                        group = zarr.open_group(store=KVStore(mapping), mode="r")
                        cuts = GroupCuts(
                            group, station, source, sha256=session.index.sha256,
                            options=options, maximum_bytes=network.maximum_input_bytes,
                            reject_mask=executor.reject_mask, flag_version=executor.flag_version,
                            sweep_limit=MAX_SWEEPS,
                        )
                    metrics["decode_adapt_ms"] += (time.perf_counter() - adapt_mark) * 1000
                    if len(cuts.numbers) > min(options.maximum_sweeps, MAX_SWEEPS):
                        raise ValueError("X QC volume sweep count exceeds execution policy")
                    gate_total = 0
                    base_key = (
                        source["input_uri"], station.radar_id, source["scan_id"],
                        session.index.sha256, network.sha256, source["available_at"],
                        source["volume_start"], source["volume_end"], executor.flag_version,
                        executor.reject_mask, executor.execution_policy_sha256,
                    )
                    for number in cuts.numbers:
                        value = executor.cut_cache.get((*base_key, number))
                        if value is None:
                            metrics["decoded_cut_cache_misses"] += 1
                            adapt_mark = time.perf_counter()
                            raw = cuts.read(number)
                            raw.metadata["input_uri"] = source["input_uri"]
                            raw.validate(station, require_geometry=False)
                            metrics["peak_input_cut_array_bytes"] = max(
                                metrics["peak_input_cut_array_bytes"], raw.nbytes
                            )
                            if raw.nbytes > min(
                                network.maximum_input_bytes, options.maximum_cut_bytes
                            ):
                                raise ValueError("X input cut exceeds execution budget")
                            metrics["decode_adapt_ms"] += (time.perf_counter() - adapt_mark) * 1000
                            qc_mark = time.perf_counter()
                            value = x_qc(raw, station, network.sha256)
                            metrics["station_qc_ms"] += (time.perf_counter() - qc_mark) * 1000
                            metrics["qc_executions"] += 1
                            del raw
                            if value.nbytes > options.maximum_qc_cut_bytes:
                                raise ValueError("X QC cut exceeds execution output budget")
                            executor.cut_cache.put((*base_key, number), value)
                        else:
                            metrics["decoded_cut_cache_hits"] += 1
                        gate_total += cuts.gates[number]
                        if gate_total > gate_limit:
                            raise ValueError("X QC volume exceeds configured gate budget")
                        metrics["gate_count"] = gate_total
                        yield value
                        del value
                finally:
                    if isinstance(cuts, NPZCuts):
                        cuts.close()
                    npz_path.unlink(missing_ok=True)
                metrics["metadata_cache_hits"] += mapping.stats["metadata_cache_hits"]
                metrics["peak_metadata_cache_bytes"] = max(
                    metrics["peak_metadata_cache_bytes"], mapping.stats["metadata_cache_peak_bytes"]
                )
                metrics["input_download_bytes"] += session.stats["download_bytes"]
                metrics["peak_physical_object_bytes"] = max(
                    metrics["peak_physical_object_bytes"], mapping.stats["peak_object_bytes"]
                )

        mark = time.perf_counter()
        objects = x_qc_objects(qc_volumes())
        metrics["encode_ms"] = max(
            0.0,
            (time.perf_counter() - mark) * 1000
            - metrics["input_read_ms"]
            - metrics["decode_adapt_ms"]
            - metrics["station_qc_ms"],
        )
        if sum(map(len, objects.values())) > output_budget:
            raise ValueError("complete X QC preview exceeds execution output budget")
        manifest = json.loads(objects["manifest.json"])
        manifest["execution"] = receipt
        objects["manifest.json"] = json_bytes(manifest)
        summary = {
            "candidate_only": True,
            "operational_eligible": False,
            "qpe_enabled": False,
            "detail": "manifest.json",
            "mode": "x_qc",
            "radar_id": source["radar_id"],
            "scan_id": source["scan_id"],
            "sweeps": len(manifest["comparison"]["sweeps"]),
            "quality_semantics": "candidate-heuristic-not-probability-v1",
            "execution": receipt,
        }
        metrics.update(
            total_ms=(time.perf_counter() - started) * 1000,
            resident_input_bytes=metrics["peak_input_cut_array_bytes"],
            decoded_cache_bytes=executor.cut_cache.bytes,
            output_object_count=len(objects),
            output_encoded_bytes=sum(map(len, objects.values())),
            streaming=1,
        )
        return objects, summary, {k: float(v) for k, v in metrics.items()}
