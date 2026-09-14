#!/usr/bin/env python3
"""Run small SYNTHETIC module probes. Not a real-radar/skill benchmark.

Requires this checkout's development/test environment; does not read online data,
modify assets, run a Worker, publish products, or use the user's screenshots.
"""

import argparse
import importlib.metadata
import json
import sys
import time
from pathlib import Path

import numpy as np

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "algorithms"))

from rainpulse_algo.radar.qc_engine.narrow_local import repaired_narrow_candidates  # noqa: E402
from rainpulse_algo.radar.qc_engine.narrow_spike import narrow_candidates  # noqa: E402
from rainpulse_algo.radar.qc_engine.residual_association import peripheral_review  # noqa: E402
from tests.test_residual_v6 import line_scene  # noqa: E402
from tests.test_residual_v61_repair import crossing_scene, profile  # noqa: E402


def run():
    p = profile()
    report = {
        "data_kind": "synthetic",
        "actual_weather_skills": None,
        "scope": "native module probes; no real radar, Worker, QPE or hardware P95 claim",
        "published": False,
        "library_environment": {
            k: importlib.metadata.version(k)
            for k in ("numpy", "scipy", "arm-pyart", "wradlib")
        },
        "narrow_crossing": [],
        "far_peripheral": [],
    }
    for dr in (250, 500, 1000):
        n, parents, crossed = crossing_scene(dr)
        empty = np.zeros(n.shape, bool)
        domain = (n.ranges > 30000) & (n.ranges < 400000) & ~crossed
        row = {
            "gate_spacing_m": dr,
            "tested_native_shape": list(n.shape),
            "target_measured_gates": int(domain.sum()),
        }
        for name, fn in (
            ("v6", narrow_candidates),
            ("v61", repaired_narrow_candidates),
        ):
            started = time.perf_counter()
            result = fn(
                n,
                p.residual,
                polarimetric_risk=empty,
                protected=empty,
                parent_mask=parents,
            )
            row[name] = {
                "module_ms": (time.perf_counter() - started) * 1000,
                "target_candidate_gates": int(
                    result.arrays["V6_NARROW_CANDIDATE_MASK"][5, domain].sum()
                ),
                "broad_intersection_candidate_gates": int(
                    result.arrays["V6_NARROW_CANDIDATE_MASK"][2:10, crossed].sum()
                ),
            }
        report["narrow_crossing"].append(row)
    for distance in (50000, 100000, 250000, 440000):
        n = line_scene()
        gate = int(np.argmin(abs(n.ranges - distance)))
        n.fields["DBZH"][5:7, gate] = 25
        parent = np.zeros(n.shape, bool)
        parent[5, gate] = True
        empty = np.zeros(n.shape, bool)
        row = {"range_m": distance, "source_ray": 5, "target_ray": 6}
        for name, mode in (("v6", False), ("v61", True)):
            result = peripheral_review(
                n,
                p.residual,
                confirmed=empty,
                model_anchor=parent,
                protected=empty,
                footprint=mode,
            )
            row[name] = {
                "compatible_candidate": bool(
                    result["V6_PERIPHERAL_COMPATIBLE_MASK"][6, gate]
                )
            }
        row["center_distance_m"] = float(distance * np.deg2rad(1))
        row["selected_footprint_gap_m"] = float(
            result["V61_PERIPHERAL_FOOTPRINT_GAP_M"][6, gate]
        )
        report["far_peripheral"].append(row)
    return report


def main():
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument("--output", required=True, type=Path)
    a = p.parse_args()
    if a.output.exists():
        raise ValueError("output exists; no overwrite")
    content = json.dumps(run(), ensure_ascii=False, indent=2, allow_nan=False)
    a.output.parent.mkdir(parents=True, exist_ok=True)
    with a.output.open("x", encoding="utf-8") as f:
        f.write(content + "\n")
    print(
        json.dumps(
            {"output": str(a.output), "data_kind": "synthetic", "published": False}
        )
    )


if __name__ == "__main__":
    main()
