"""Preflight independent case identities, then compare sequentially without publication."""

from __future__ import annotations

import argparse
import json
import os
import shutil
import tempfile
from pathlib import Path

from rainpulse_algo.worker.domain_contracts import RadarQCRequested

from .paper_compare import _frozen, run_comparison


def preflight(path: Path):
    path = Path(path).resolve()
    value = json.loads(path.read_text())
    if value.get("schema_version") != "rainpulse.qc-paper-batch.v1":
        raise ValueError("unsupported paper batch manifest")
    entries = value.get("cases", [])
    if not 1 <= len(entries) <= 8:
        raise ValueError("bounded batch requires 1..8 frozen cases")
    seen_scan, seen_input, process_partitions, asset_partitions = set(), set(), {}, {}
    paths = []
    profiles = None
    for item in entries:
        manifest_path = _frozen(path.parent, item)
        manifest = json.loads(manifest_path.read_text())
        if manifest.get("schema_version") != "rainpulse.qc-paper-comparison.v1":
            raise ValueError("batch entry is not a comparison manifest")
        task = RadarQCRequested.model_validate_json(
            _frozen(manifest_path.parent, manifest["task"]).read_text()
        )
        partition, process = manifest["partition"], manifest["process_id"]
        if partition not in {"development", "validation"} or not process:
            raise ValueError("batch needs process identity and partition")
        selected = tuple(
            manifest[key]["sha256"] for key in ("v3_profile", "fusion_profile", "flags")
        )
        if profiles is not None and profiles != selected:
            raise ValueError("a batch cannot mix retuned algorithm configurations")
        profiles = selected
        artifacts = manifest["artifacts"]
        current = [x for x in artifacts if x["uri"] == task.payload.input_uri]
        if len(current) != 1:
            raise ValueError("current task artifact missing or duplicated")
        identity = (task.payload.radar_id, str(task.payload.scan_id))
        if identity in seen_scan or current[0]["sha256"] in seen_input:
            raise ValueError("duplicate physical scan/current artifact is not an independent case")
        seen_scan.add(identity)
        seen_input.add(current[0]["sha256"])
        if process in process_partitions and process_partitions[process] != partition:
            raise ValueError("same weather process cannot be both development and validation")
        process_partitions[process] = partition
        # Current/temporal/neighbor data may not cross a declared evaluation partition.
        for asset in artifacts:
            digest = asset["sha256"]
            if digest in asset_partitions and asset_partitions[digest] != partition:
                raise ValueError("input/context leakage across declared partitions")
            asset_partitions[digest] = partition
        paths.append(manifest_path)
    return paths


def run_batch(path: Path, output: Path, *, inspect_rays=(0,), save_bundles=False):
    paths = preflight(path)
    output = Path(output).absolute()
    if output.exists():
        raise ValueError("batch output already exists")
    output.parent.mkdir(parents=True, exist_ok=True)
    lock = output.with_name(output.name + ".lock")
    with lock.open("x"):
        pass
    temp = Path(tempfile.mkdtemp(prefix="qc-paper-batch-", dir=output.parent))
    try:
        reports = []
        for index, source in enumerate(paths):
            reports.append(
                run_comparison(
                    source,
                    temp / f"case-{index:02d}",
                    inspect_rays=inspect_rays,
                    save_bundles=save_bundles,
                )
            )
        joined = {**reports[0], "cases": [case for r in reports for case in r["cases"]]}
        joined.pop("manifest_sha256", None)
        joined["case_manifest_sha256"] = [r["manifest_sha256"] for r in reports]
        joined["batch_aggregation"] = "per_case_only_no_pooled_skill_or_promotion"
        content = json.dumps(joined, ensure_ascii=False, allow_nan=False)
        if len(content.encode()) > 50 * 1024 * 1024:
            raise ValueError("combined report exceeds 50 MiB; inspect fewer rays/cases")
        (temp / "report.json").write_text(content)
        if output.exists():
            raise ValueError("batch output appeared while running")
        os.rename(temp, output)
        return joined
    finally:
        if temp.exists():
            shutil.rmtree(temp)
        lock.unlink()


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--manifest", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--inspect-ray", type=int, action="append")
    parser.add_argument("--save-bundles", action="store_true")
    args = parser.parse_args()
    run_batch(
        args.manifest,
        args.output,
        inspect_rays=args.inspect_ray or (0,),
        save_bundles=args.save_bundles,
    )
    print(json.dumps({"output": str(args.output), "published": False}))


if __name__ == "__main__":
    main()
