"""Create a separately identified offline V3 replay task from an immutable V2 capture.

Preserves original event time, input URIs and exact context selection. New task
IDs are deterministic from the source task and candidate hash. No publication,
NATS, database, deployment or data transfer is performed by this command.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import shutil
import tempfile
from pathlib import Path
from uuid import NAMESPACE_URL, uuid5

from rainpulse_algo.worker.domain_contracts import RadarQCRequested

from ..qc import load_qc_profile


def prepare_experiment(manifest_path: Path, profile_path: Path, output_path: Path):
    manifest_path, profile_path = manifest_path.resolve(), profile_path.resolve()
    output_path = output_path.absolute()
    source = json.loads(manifest_path.read_text())
    if source.get("schema_version") != "rainpulse.qc-task-replay.v1":
        raise ValueError("unsupported source replay manifest")
    if output_path.exists():
        raise ValueError("experiment directory already exists")
    root = manifest_path.parent
    for key in ("task", "profile", "flags"):
        path = (root / source[key]["path"]).resolve()
        if hashlib.sha256(path.read_bytes()).hexdigest() != source[key]["sha256"]:
            raise ValueError(f"source {key} hash differs from manifest")
    task = RadarQCRequested.model_validate_json((root / source["task"]["path"]).read_text())
    if task.payload.qc_profile_sha256 != source["profile"]["sha256"]:
        raise ValueError("source task and profile identities differ")
    flags_path = (root / source["flags"]["path"]).resolve()
    original = load_qc_profile((root / source["profile"]["path"]).resolve(), flags_path)
    if (
        task.payload.qc_profile,
        task.payload.qc_pipeline_version,
        task.payload.flag_definition_version,
    ) != (original.profile_version, original.pipeline_version, original.flag_definition_version):
        raise ValueError("source task profile versions do not match the frozen profile")
    candidate = load_qc_profile(profile_path, flags_path)
    if candidate.rfi_refinement is None:
        raise ValueError("experiment preparation requires a V3 candidate profile")
    sha = hashlib.sha256(profile_path.read_bytes()).hexdigest()
    identity = f"rainpulse:offline-qc-v3:{source['task']['sha256']}:{sha}"
    payload = task.model_dump(mode="json")
    for key in ("event_id", "run_id", "job_id", "trace_id"):
        payload[key] = str(uuid5(NAMESPACE_URL, f"{identity}:{key}"))
    payload["payload"].update(
        qc_profile=candidate.profile_version,
        qc_pipeline_version=candidate.pipeline_version,
        flag_definition_version=candidate.flag_definition_version,
        qc_profile_sha256=sha,
        output_prefix=f"s3://rainpulse/offline-qc-experiments/{payload['job_id']}/",
    )
    changed = RadarQCRequested.model_validate(payload)
    text = changed.model_dump_json()
    artifacts = []
    for item in source["artifacts"]:
        path = (root / item["path"]).resolve()
        if output_path.resolve().is_relative_to(path):
            raise ValueError("experiment directory cannot change a source artifact")
        artifacts.append({**item, "path": str(path)})
    manifest = {
        **source,
        "task": {"path": "task.json", "sha256": hashlib.sha256(text.encode()).hexdigest()},
        "profile": {"path": str(profile_path), "sha256": sha},
        "flags": {"path": str(flags_path), "sha256": source["flags"]["sha256"]},
        "artifacts": artifacts,
        "source_revision": "record-executing-checkout-before-replay",
        "offline_experiment": {
            "source_manifest_sha256": hashlib.sha256(manifest_path.read_bytes()).hexdigest(),
            "source_task_sha256": source["task"]["sha256"],
            "source_task_id": str(task.job_id),
            "source_profile_sha256": source["profile"]["sha256"],
            "source_revision": source.get("source_revision"),
            "context_selection": "unchanged_from_original_frozen_task",
            "interpretation": "counterfactual_candidate_not_original_deployed_task",
        },
    }
    output_path.parent.mkdir(parents=True, exist_ok=True)
    lock = output_path.with_name(output_path.name + ".lock")
    with lock.open("x"):
        pass
    temporary = Path(tempfile.mkdtemp(prefix="qc-experiment-", dir=output_path.parent))
    try:
        if output_path.exists():
            raise ValueError("experiment directory appeared while acquiring lock")
        (temporary / "task.json").write_text(text)
        (temporary / "manifest.json").write_text(json.dumps(manifest, ensure_ascii=False, indent=2))
        os.rename(temporary, output_path)
    finally:
        if temporary.exists():
            shutil.rmtree(temporary)
        lock.unlink()
    return manifest


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--manifest", required=True, type=Path)
    parser.add_argument("--profile", required=True, type=Path)
    parser.add_argument("--output", required=True, type=Path)
    args = parser.parse_args()
    prepare_experiment(args.manifest, args.profile, args.output)
    print(json.dumps({"manifest": str(args.output / "manifest.json"), "published": False}))


if __name__ == "__main__":
    main()
