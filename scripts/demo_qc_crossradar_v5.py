#!/usr/bin/env python3
"""Generate an explicitly SYNTHETIC V4/V5 frozen-task comparison (dev dependencies)."""

from __future__ import annotations

import argparse
import json
import shutil
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path[:0] = [str(ROOT), str(ROOT / "algorithms")]


def main():
    from rainpulse_algo.radar.qc_engine.network_compare import run_network

    from algorithms.tests.test_crossradar_network import build_case, frozen

    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument("--output", required=True, type=Path)
    args = p.parse_args()
    output = args.output.resolve()
    if output.exists():
        p.error("output exists; choose a new directory")
    output.mkdir(parents=True)
    case, spec, _, _, job = build_case(output / "fixture")
    # Make the resulting fixture portable; no private data or external I/O.
    for name in ("baseline_profile", "candidate_profile", "flags"):
        source = Path(spec[name]["path"])
        local = case.parent / source.name
        shutil.copyfile(source, local)
        spec[name] = {**frozen(local), "path": local.name}
    spec["task"]["path"] = "task.json"
    spec["labels"]["sweep_000"]["path"] = "labels.npy"
    case.write_text(json.dumps(spec, ensure_ascii=False, indent=2))
    suite = output / "suite.json"
    suite.write_text(
        json.dumps(
            dict(
                schema_version="rainpulse.qc-network.v1",
                expected_radars=[job.payload.radar_id],
                cases=[{**frozen(case), "path": "fixture/case.json"}],
            ),
            indent=2,
        )
    )
    report = run_network(suite, output / "comparison", inspect_rays=(0,))
    (output / "README.txt").write_text(
        "SYNTHETIC ONLY. Not Z9591 or Z9598 observations, not an IQ simulation.\n"
        "A long high-correlation clipped numeric pattern tests a V4 decision gap.\n"
        "V5's added withholding is quarantine, not confirmed interference recall.\n"
        "Open comparison/report.json in /qc-review. Network gate must not PASS.\n"
        "The fixture is development data and cannot establish operational skill.\n"
    )
    print(
        json.dumps(
            {
                "output": str(output),
                "data_kind": "synthetic",
                "network_gate": report["network_gate"]["status"],
                "operational_eligible": False,
            },
            ensure_ascii=False,
        )
    )


if __name__ == "__main__":
    main()
