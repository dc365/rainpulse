"""Read-only frozen-task replay through the real context/core/serialization path.

No NATS, database, remote object fetch, publication, deployment or model reruns.
The manifest binds a task JSON, the current artifact and every context URI/hash.
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
from rainpulse_algo.worker.object_store import artifact_sha256

from ..qc import apply_basic_qc, load_qc_profile
from ..qc_input import open_qc_input
from ..qc_zarr import build_validated_qc_zarr_store
from .context import prepare_open_source_inputs
from .review import _safe_json, load_local_artifact


class LocalArtifacts:
    def __init__(self, root, definitions):
        self.root = root
        self.definitions = {}
        for item in definitions:
            if item["uri"] in self.definitions:
                raise ValueError("duplicate frozen artifact URI")
            self.definitions[item["uri"]] = item
        self.cache = {}

    def load(self, uri):
        if uri not in self.definitions:
            raise ValueError("task references an artifact absent from the frozen manifest")
        if uri not in self.cache:
            item = self.definitions[uri]
            self.cache[uri] = load_local_artifact(
                (self.root / item["path"]).resolve(), item["sha256"]
            )
        return self.cache[uri]


class NoNetwork:
    def __getattr__(self, name):
        raise ValueError("offline replay prohibits remote asset I/O; supply verified local assets")


def replay_task(manifest_path: Path, output_path: Path):
    manifest_path, output_path = manifest_path.resolve(), output_path.absolute()
    manifest = json.loads(manifest_path.read_text())
    if manifest.get("schema_version") != "rainpulse.qc-task-replay.v1":
        raise ValueError("unsupported task replay manifest")
    if output_path.exists():
        raise ValueError("replay output already exists; choose a new path")
    root = manifest_path.parent
    task_file = (root / manifest["task"]["path"]).resolve()
    if hashlib.sha256(task_file.read_bytes()).hexdigest() != manifest["task"]["sha256"]:
        raise ValueError("task hash differs from manifest")
    request = RadarQCRequested.model_validate_json(task_file.read_text())
    path = (root / manifest["profile"]["path"]).resolve()
    sha = hashlib.sha256(path.read_bytes()).hexdigest()
    if sha != manifest["profile"]["sha256"] or sha != request.payload.qc_profile_sha256:
        raise ValueError("profile hash differs from manifest/task")
    flags = (root / manifest["flags"]["path"]).resolve()
    if hashlib.sha256(flags.read_bytes()).hexdigest() != manifest["flags"]["sha256"]:
        raise ValueError("flag definition hash differs from manifest")
    profile = load_qc_profile(path, flags)
    if profile.rfi_objects is None:
        raise ValueError("this replay requires the coordinated RFI object candidate")
    for name, expected in [
        ("qc_profile", profile.profile_version),
        ("qc_pipeline_version", profile.pipeline_version),
        ("flag_definition_version", profile.flag_definition_version),
    ]:
        if getattr(request.payload, name) != expected:
            raise ValueError(f"task {name} differs from the selected profile")
    definitions = manifest["artifacts"]
    if not 1 <= len(definitions) <= 7:
        raise ValueError("replay supports current + at most six context volumes")
    reader = LocalArtifacts(root, definitions)
    expected = {
        request.payload.input_uri,
        *[x.input_uri for x in request.payload.temporal_context],
        *[x.input_uri for x in request.payload.cross_radar_context],
    }
    if set(reader.definitions) != expected:
        raise ValueError("manifest artifacts must exactly match the frozen task")
    normalized = reader.load(request.payload.input_uri)
    view = open_qc_input(normalized)
    for key in ("scan_id", "radar_id", "radar_config_version"):
        if str(view.root.attrs.get(key)) != str(getattr(request.payload, key)):
            raise ValueError(f"current {key} differs from frozen task")
    prepared, context = prepare_open_source_inputs(
        request, normalized, profile, NoNetwork(), reader=reader
    )
    result = apply_basic_qc(normalized, profile, **prepared, created_at=request.occurred_at)
    result.summary["radial_context"] = context
    objects, validation = build_validated_qc_zarr_store(
        normalized,
        result,
        asset_id=uuid5(NAMESPACE_URL, f"rainpulse:qc-asset:{request.job_id}"),
        normalized_volume_uri=request.payload.input_uri,
        provenance={
            "scan_id": str(request.payload.scan_id),
            "run_id": str(request.run_id),
            "job_id": str(request.job_id),
            "trace_id": str(request.trace_id),
            "context_fingerprint": context["context_fingerprint"],
            "radial_context": json.dumps(context, sort_keys=True),
        },
    )
    receipt = _safe_json(
        {
            "schema_version": "rainpulse.qc-task-replay-receipt.v1",
            "task_sha256": manifest["task"]["sha256"],
            "profile_file_sha256": sha,
            "input_sha256": artifact_sha256(normalized),
            "output_sha256": artifact_sha256(objects),
            "source_revision": manifest.get("source_revision", "not_supplied"),
            "target_cycle_time_utc": manifest.get("target_cycle_time_utc"),
            "actual_observation_end_utc": view.root.attrs.get("volume_end_time_utc"),
            "validation": validation,
            "summary": result.summary,
            "acceptance": "engineering_replay_only_not_weather_skill_or_promotion",
            "published": False,
        }
    )
    if manifest.get("offline_experiment") is not None:
        receipt["offline_experiment"] = manifest["offline_experiment"]
    output_path.parent.mkdir(parents=True, exist_ok=True)
    lock = output_path.with_name(output_path.name + ".lock")
    with lock.open("x"):
        pass
    temporary = Path(tempfile.mkdtemp(prefix="qc-replay-", dir=output_path.parent))
    try:
        if output_path.exists():
            raise ValueError("replay output appeared while acquiring the write lock")
        for key, content in objects.items():
            if key.startswith("/") or ".." in Path(key).parts:
                raise ValueError("unsafe artifact object key")
            file = temporary / "qc.zarr" / key
            file.parent.mkdir(parents=True, exist_ok=True)
            file.write_bytes(content)
        (temporary / "receipt.json").write_text(
            json.dumps(receipt, ensure_ascii=False, allow_nan=False)
        )
        if profile.rfi_refinement is not None:
            # Compact deterministic audit alongside the full immutable numerical artifact.
            audit = {
                "schema_version": "rainpulse.qc-replay-audit.v1",
                "input_sha256": receipt["input_sha256"],
                "output_sha256": receipt["output_sha256"],
                "profile_file_sha256": sha,
                "qc_pipeline_version": profile.pipeline_version,
                "acceptance": "no_labels_no_weather_skill_claim",
                "sweeps": {
                    name: data["rfi_v3_audit"] for name, data in result.summary["sweeps"].items()
                },
            }
            (temporary / "residual-audit.json").write_text(
                json.dumps(_safe_json(audit), ensure_ascii=False, allow_nan=False, indent=2)
            )
        os.rename(temporary, output_path)
    finally:
        if temporary.exists():
            shutil.rmtree(
                temporary
            )  # Only this process's newly-created private temporary directory.
        lock.unlink()
    return receipt


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--manifest", required=True, type=Path)
    parser.add_argument("--output", required=True, type=Path)
    args = parser.parse_args()
    receipt = replay_task(args.manifest, args.output)
    print(
        json.dumps(
            {
                "output": str(args.output),
                "output_sha256": receipt["output_sha256"],
                "published": False,
                "acceptance": receipt["acceptance"],
            }
        )
    )


if __name__ == "__main__":
    main()
