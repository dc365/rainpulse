#!/usr/bin/env python3
"""CPU compute benchmark, not a 105/real-X or object-store throughput claim.

Run each mode in a FRESH process. Set RAINPULSE_PERF_REFERENCE_ROOT to the supplied
reference-source subset, or retain the base commit for git-show reference access.
"""

from __future__ import annotations

# Imports follow path setup so this standalone script works from a source checkout.
# ruff: noqa: E402
import argparse
import copy
import hashlib
import json
import platform
import resource
import sys
import tempfile
import time
from dataclasses import replace
from pathlib import Path
import numpy as np

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "algorithms"))
sys.path.insert(0, str(ROOT / "algorithms/tests/performance_batch1_20260925"))
from perf_helpers import station, volume, network, reference
from rainpulse_algo.multiband.model import Volume
from rainpulse_algo.multiband.quality import x_qc
from rainpulse_algo.multiband.execution import ExecutionOptions
from rainpulse_algo.multiband.stream_fusion import build_composite_streaming
from rainpulse_algo.multiband.selection_kernel import warmup


def digest(arrays):
    h = hashlib.sha256()
    for name, a in sorted(arrays.items()):
        a = np.array(a, copy=True, order="C")
        if a.dtype.kind == "f":
            a[np.isnan(a)] = np.nan
        h.update(name.encode())
        h.update(a.dtype.str.encode())
        h.update(str(a.shape).encode())
        h.update(a.tobytes())
    return h.hexdigest()


def main():
    p = argparse.ArgumentParser()
    p.add_argument(
        "--mode", choices=["reference", "stream-numpy", "stream-numba"], required=True
    )
    p.add_argument("--cuts", type=int, default=24)
    p.add_argument("--rays", type=int, default=360)
    p.add_argument("--gates", type=int, default=800)
    p.add_argument("--grid", type=int, default=96)
    p.add_argument("--spill", action="store_true")
    p.add_argument("--output", type=Path)
    args = p.parse_args()
    st = station()
    net = network([st], width=args.grid, height=args.grid, tile_rows=16)
    grid = replace(
        net.products["demo"],
        levels_m_msl=(
            250.0,
            500.0,
            1000.0,
            1500.0,
            2000.0,
            3000.0,
            5000.0,
            8000.0,
            12000.0,
        ),
    )
    net = replace(net, products={"demo": grid})
    base = volume(st, cuts=1, rays=4, gates=4).metadata

    def rawcuts():
        for n in range(args.cuts):
            v = volume(st, cuts=1, rays=args.rays, gates=args.gates, seed=n + 133)
            s = v.sweeps[0]
            s.number = n
            s.elevation_deg[:] = 0.5 + n * 0.5
            yield Volume(copy.deepcopy(base), [s])

    if args.mode == "reference" and (
        args.cuts > 32 or args.cuts * args.rays * args.gates > 8_000_000
    ):
        p.error(
            "the untouched eager contract cannot process this size; do not loosen guards for benchmark"
        )
    backend = "numba" if args.mode.endswith("numba") else "numpy"
    t = time.perf_counter()
    warmup(backend)
    warmup_s = time.perf_counter() - t
    metrics = {}
    t = time.perf_counter()
    if args.mode == "reference":
        q = reference("quality")
        f = reference("fusion")
        parts = list(rawcuts())
        v = Volume(copy.deepcopy(base), [x.sweeps[0] for x in parts])
        del parts
        qv = q.x_qc(v, st, net.sha256)
        result = f.build_composite(
            [qv], net, "demo", "2026-08-28T00:06:00Z", "2026-08-28T00:12:00Z"
        )
    else:
        options = ExecutionOptions(
            streaming=True,
            selection_backend=backend,
            layer_memory_bytes=0 if args.spill else 64 * 1024**2,
        )

        def qc_cuts():
            for raw in rawcuts():
                v = x_qc(raw, st, net.sha256)
                del raw
                yield v
                del v

        with tempfile.TemporaryDirectory(prefix="rp-benchmark-") as tmp:
            result = build_composite_streaming(
                qc_cuts(),
                net,
                "demo",
                "2026-08-28T00:06:00Z",
                "2026-08-28T00:12:00Z",
                options=options,
                directory=tmp,
                metrics=metrics,
            )
    elapsed = time.perf_counter() - t
    record = {
        "kind": "synthetic_compute_only_includes_generation_qc_fusion",
        "mode": args.mode,
        "shape": {
            "cuts": args.cuts,
            "rays": args.rays,
            "gates": args.gates,
            "total_gates": args.cuts * args.rays * args.gates,
            "grid": [args.grid, args.grid],
            "layers": len(grid.levels_m_msl),
        },
        "wall_seconds": elapsed,
        "warmup_seconds": warmup_s,
        "peak_rss_mib": resource.getrusage(resource.RUSAGE_SELF).ru_maxrss / 1024,
        "array_sha256": digest(result.arrays),
        "source_count": len(result.metadata["sources"]),
        "python": sys.version,
        "numpy": np.__version__,
        "platform": platform.platform(),
        "metrics": metrics,
        "limitations": [
            "not real X input",
            "not 105",
            "no network/decompression/publisher benchmark",
            "RSS includes Python/imports and JIT",
            "no meteorological skill claim",
        ],
    }
    raw = json.dumps(record, ensure_ascii=False, indent=2)
    if args.output:
        args.output.write_text(raw + "\n")
    print(raw)


if __name__ == "__main__":
    main()
