#!/usr/bin/env python3
"""No deployment. Validate runtime dependencies and frozen execution/network files."""

from __future__ import annotations
import argparse
import importlib.metadata
import json
import os
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "algorithms"))
from rainpulse_algo.multiband.execution import ExecutionOptions
from rainpulse_algo.multiband.model import Network
from rainpulse_algo.multiband.selection_kernel import warmup


def main():
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument("--execution", required=True)
    p.add_argument("--network", required=True)
    p.add_argument("--require-zarr", action="store_true")
    a = p.parse_args()
    c = ExecutionOptions.load(a.execution)
    n = Network.load(a.network)
    modules = ["numpy", "scipy", "pyproj"] + (
        ["numba"] if c.selection_backend == "numba" else []
    )
    if a.require_zarr or any(s.source.endswith("zarr") for s in n.stations.values()):
        modules.append("zarr")
    versions = {k: importlib.metadata.version(k) for k in modules}
    if "zarr" in versions and versions["zarr"].split(".")[0] != "2":
        raise RuntimeError("locked Zarr 2 required")
    if c.scratch_parent is not None and (
        not Path(c.scratch_parent).is_dir() or not os.access(c.scratch_parent, os.W_OK)
    ):
        raise RuntimeError("scratch directory unavailable or not writable")
    start = time.perf_counter()
    warmup(c.selection_backend)
    print(
        json.dumps(
            {
                "policy_sha256": c.digest,
                "network_sha256": n.sha256,
                "versions": versions,
                "warmup_seconds": time.perf_counter() - start,
                "maximum_stations": 16,
                "streaming": c.streaming,
                "operational_eligibility_changed": False,
                "limitations": "Imports and common-kernel warmup only; not device geometry, memory capacity, MinIO/NATS or 105 acceptance.",
            },
            indent=2,
        )
    )


if __name__ == "__main__":
    main()
