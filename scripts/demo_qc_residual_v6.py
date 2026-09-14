#!/usr/bin/env python3
"""Create portable, SYNTHETIC frozen V5/V6 comparisons (requires test dependencies)."""

from __future__ import annotations

import argparse
import json
import shutil
import sys
from dataclasses import replace
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path[:0] = [str(ROOT), str(ROOT / "algorithms")]


def main():
    import numpy as np
    from rainpulse_algo.radar.qc import load_qc_profile
    from rainpulse_algo.radar.qc_engine.network_compare import run_network
    from rainpulse_algo.worker.domain_contracts import RadarQCRequested
    from rainpulse_algo.worker.object_store import artifact_sha256
    from algorithms.tests.test_crossradar_network import frozen
    from algorithms.tests.test_object_store import FakeMinio
    from algorithms.tests.test_radar_qc import synthetic_normalized_fixture
    from algorithms.tests.test_residual_v6 import FLAGS, V5, V6, line_scene, long_scene
    from algorithms.tests.test_rfi_objects_context import mount, request, stamp

    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument("--output", type=Path, required=True)
    a = p.parse_args()
    output = a.output.resolve()
    if output.exists():
        p.error("choose a new output directory")
    output.mkdir(parents=True)
    strong = long_scene(dr=1000)
    z = strong.fields["DBZH"].copy()
    z[:, 25::25] += 5
    strong = replace(strong, fields={**strong.fields, "DBZH": z})
    scenes = [
        ("sparse-measured-outliers", strong),
        ("weak-interrupted-line", line_scene(dr=1000, missing=True)),
    ]
    cases = []
    for i, (name, n) in enumerate(scenes):
        folder = output / name
        folder.mkdir()
        raw = synthetic_normalized_fixture(
            n.fields["DBZH"],
            azimuth_deg=n.azimuth,
            range_m=n.ranges,
            moments={k: v for k, v in n.fields.items() if k != "DBZH"},
        )
        raw = stamp(raw, f"2026-08-28T00:{30 + i * 5}:00Z")
        uri = mount(FakeMinio(), raw, name)
        job = request(raw, uri, [])
        profile = load_qc_profile(V5, FLAGS)
        data = job.model_dump(mode="json")
        data["payload"].update(
            qc_profile=profile.profile_version,
            qc_pipeline_version=profile.pipeline_version,
            qc_profile_sha256=frozen(V5)["sha256"],
        )
        job = RadarQCRequested.model_validate(data)
        task = folder / "task.json"
        task.write_text(job.model_dump_json())
        for key, value in raw.items():
            target = folder / "source.zarr" / key
            target.parent.mkdir(parents=True, exist_ok=True)
            target.write_bytes(value)
        labels = np.where(np.isfinite(n.fields["DBZH"]), 1, 0).astype("uint8")
        labels[np.isfinite(n.fields["DBZH"]) & (n.fields["DBZH"] > 0)] = 2
        np.save(folder / "labels.npy", labels)
        spec = dict(
            schema_version="rainpulse.qc-network-case.v1",
            case_id=name,
            partition="development",
            data_kind="synthetic",
            process_id="synthetic-" + name,
            task={**frozen(task), "path": task.name},
            artifacts=[dict(uri=uri, path="source.zarr", sha256=artifact_sha256(raw))],
            labels={
                "sweep_000": {**frozen(folder / "labels.npy"), "path": "labels.npy"}
            },
        )
        for key, source in [
            ("baseline_profile", V5),
            ("candidate_profile", V6),
            ("flags", FLAGS),
        ]:
            target = folder / source.name
            shutil.copyfile(source, target)
            spec[key] = {**frozen(target), "path": target.name}
        case = folder / "case.json"
        case.write_text(json.dumps(spec, indent=2))
        cases.append({**frozen(case), "path": str(case.relative_to(output))})
    suite = output / "suite.json"
    suite.write_text(
        json.dumps(
            dict(
                schema_version="rainpulse.qc-network.v1",
                cases=cases,
                expected_radars=["z9999"],
            ),
            indent=2,
        )
    )
    report = run_network(
        suite, output / "comparison", inspect_rays=(0, 5), save_bundles=False
    )
    (output / "README.txt").write_text(
        "SYNTHETIC ONLY — NOT Z9591/Z9598 DATA; NOT AN IQ SIMULATION.\n"
        "Open comparison/report.json in /qc-review. This cannot establish real-weather skill.\n"
        "Known injected patterns and V5/V6 apply to the same frozen task/context.\n"
        "Confirmation, quarantine, retained candidates and coverage are separate.\n"
        "The network acceptance gate MUST remain INSUFFICIENT.\n"
    )
    summary = {
        c["case_id"]: {
            name: {
                k: v
                for k, v in info.items()
                if k
                in [
                    "marked_observed_gates",
                    "quarantined_observed_gates",
                    "quantitative_eligible_gates",
                ]
            }
            for name, info in c["sweeps"][0]["methods"].items()
        }
        for c in report["cases"]
    }
    (output / "summary.json").write_text(json.dumps(summary, indent=2))
    print(
        json.dumps(
            dict(
                output=str(output),
                gate=report["network_gate"]["status"],
                synthetic=True,
                published=False,
            )
        )
    )


if __name__ == "__main__":
    main()
