"""Freeze a comparison manifest from an existing, authorized V3 replay manifest."""

from __future__ import annotations

import argparse
import hashlib
import json
from pathlib import Path

from .paper_compare import _frozen


def freeze_comparison(
    replay_path, output_path, fusion_profile, *, process_id, partition="development", case_id=None
):
    source = Path(replay_path).resolve()
    output = Path(output_path).resolve()
    replay = json.loads(source.read_text())
    if replay.get("schema_version") != "rainpulse.qc-task-replay.v1":
        raise ValueError("input must be a frozen V3 task replay manifest")
    if not process_id or partition not in {"development", "validation"}:
        raise ValueError("declare process and partition before comparison")
    result = {
        "schema_version": "rainpulse.qc-paper-comparison.v1",
        "partition": partition,
        "process_id": process_id,
        "source_revision": replay.get("source_revision", "not_supplied"),
        "rdd_references": {},
        "labels": {},
    }
    if case_id:
        result["case_id"] = case_id
    for source_key, key in (("task", "task"), ("profile", "v3_profile"), ("flags", "flags")):
        path = _frozen(source.parent, replay[source_key])
        result[key] = {"path": str(path), "sha256": replay[source_key]["sha256"]}
    p = Path(fusion_profile).resolve()
    result["fusion_profile"] = {
        "path": str(p),
        "sha256": hashlib.sha256(p.read_bytes()).hexdigest(),
    }
    result["artifacts"] = [
        {**item, "path": str((source.parent / item["path"]).resolve())}
        for item in replay["artifacts"]
    ]
    # Do not change task identity or silently replace unavailable RDD with another method.
    # Source artifacts are verified by the comparison command before any computation.
    output.parent.mkdir(parents=True, exist_ok=True)
    with output.open("x") as stream:
        json.dump(result, stream, ensure_ascii=False, indent=2)
        stream.write("\n")
    return result


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--replay", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--fusion-profile", type=Path, required=True)
    parser.add_argument("--process-id", required=True)
    parser.add_argument("--partition", choices=("development", "validation"), default="development")
    parser.add_argument("--case-id")
    args = parser.parse_args()
    freeze_comparison(
        args.replay,
        args.output,
        args.fusion_profile,
        process_id=args.process_id,
        partition=args.partition,
        case_id=args.case_id,
    )
    print(json.dumps({"manifest": str(args.output), "computed": False, "published": False}))


if __name__ == "__main__":
    main()
