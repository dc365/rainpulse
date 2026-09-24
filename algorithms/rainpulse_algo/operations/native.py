# ruff: noqa: E501, I001
"""Thin adapters to existing QC, diagnostics and immutable publication contracts."""
from __future__ import annotations

import hashlib
import json
import os
import time
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any
from uuid import UUID

from .protocol import ConfigurationChanged, FrozenInputChanged, PinnedClient

CONFIG_KEYS = {
    "multiband": ("RAINPULSE_MULTIBAND_CONFIG", "RAINPULSE_QC_FLAG_DEFINITIONS"),
    "qc": ("RAINPULSE_RADAR_QC_CONFIG", "RAINPULSE_QC_FLAG_DEFINITIONS"),
    "render": ("RAINPULSE_QC_FLAG_DEFINITIONS",),
    "diagnostics": ("RAINPULSE_DIAGNOSTIC_CONFIG", "RAINPULSE_QC_FLAG_DEFINITIONS"),
}


def capture_identity(kind: str) -> dict[str, Any]:
    import yaml

    if kind not in CONFIG_KEYS:
        raise ValueError("unknown managed worker kind")
    files, configs = {}, {}
    for key in CONFIG_KEYS[kind]:
        name = os.environ.get(key)
        if not name:
            raise ValueError(f"{key} must be configured explicitly")
        data = Path(name).read_bytes()
        files[key] = hashlib.sha256(data).hexdigest()
        configs[key] = yaml.safe_load(data)
    for key in ("RAINPULSE_ANCILLARY_CONFIG", "RAINPULSE_RADAR_PHASE_PROCESSING_PROFILE",
                "RAINPULSE_RADAR_ATTENUATION_PROFILE"):
        if kind != "render" and os.getenv(key):
            files[key] = hashlib.sha256(Path(os.environ[key]).read_bytes()).hexdigest()
    if kind != "render":
        for key in ("RAINPULSE_RADAR_CONFIG_DIR", "RAINPULSE_RADAR_CONTEXT_CONFIG_DIR"):
            if os.getenv(key):
                paths = sorted(Path(os.environ[key]).glob("*.yaml"))
                if len(paths) > 256:
                    raise ValueError("radar configuration inventory exceeds managed identity limit")
                for p in paths:
                    files[key + "/" + p.name] = hashlib.sha256(p.read_bytes()).hexdigest()
    versions = {"flag_definition_version": str(configs["RAINPULSE_QC_FLAG_DEFINITIONS"]["definition_version"])}
    if kind == "multiband":
        from rainpulse_algo.multiband.model import Network
        network = Network.load(os.environ["RAINPULSE_MULTIBAND_CONFIG"])
        versions.update(multiband_contract="rainpulse.multiband.v1", network_release=network.release_id)
    elif kind == "qc":
        qc = configs["RAINPULSE_RADAR_QC_CONFIG"]
        versions.update(qc_profile=str(qc["profile_version"]), qc_pipeline_version=str(qc["pipeline_version"]))
    elif kind == "diagnostics":
        cfg = configs["RAINPULSE_DIAGNOSTIC_CONFIG"]
        versions.update(diagnostic_config_version=str(cfg["profile_version"]), renderer_version=str(cfg["renderer_version"]))
    else:
        versions["preview_version"] = "ops-preview-1"
    code = hashlib.sha256()
    root = Path(__file__).resolve().parents[1]
    for file in sorted(root.rglob("*.py")):
        relative = file.relative_to(root).as_posix().encode()
        data = file.read_bytes()
        code.update(len(relative).to_bytes(8, "big") + relative + len(data).to_bytes(8, "big") + data)
    value = {"kind": kind, "files": files, "versions": versions, "code_sha256": code.hexdigest()}
    value["fingerprint"] = hashlib.sha256(json.dumps(value, sort_keys=True, separators=(",", ":")).encode()).hexdigest()
    return value


class NativeAdapter:
    def __init__(self, kind: str) -> None:
        from rainpulse_algo.worker.object_store import AtomicObjectPublisher, minio_client_from_environment

        self.kind = kind
        self.client = minio_client_from_environment()
        self.publisher = AtomicObjectPublisher(self.client)
        self.startup = capture_identity(kind)
        self.artifact_name = {"qc": "qc.zarr", "render": "review-images", "diagnostics": "diagnostics", "multiband": "multiband"}[kind]
        self.multiband = None
        if kind == "multiband":
            from rainpulse_algo.multiband.managed import existing_runtime_executor
            self.multiband = existing_runtime_executor()

    def check_identity(self, expected) -> None:
        current = capture_identity(self.kind)
        if current != self.startup or current["fingerprint"] != expected["fingerprint"]:
            raise ConfigurationChanged("mounted code/configuration differs from frozen worker identity")

    def existing(self, claim) -> bool:
        from rainpulse_algo.worker.object_store import ArtifactObjectReader

        request = claim["request"]
        completion = self.publisher.load_completion(request["payload"]["output_prefix"], self.artifact_name)
        if completion is None:
            return False
        if (str(completion.job_id) != request["job_id"] or str(completion.run_id) != request["run_id"]
                or str(completion.trace_id) != request["trace_id"]):
            raise FrozenInputChanged("existing completion belongs to another task")
        for asset in completion.payload.assets:
            # Reusing bytes requires full transport checksum validation, not a marker alone.
            ArtifactObjectReader(self.client).load(asset.uri)
        return True

    def execute(self, claim):
        from minio.error import S3Error

        client = PinnedClient(self.client, claim["inputs"])
        try:
            if self.kind == "multiband":
                from rainpulse_algo.worker.object_store import ArtifactObjectReader, artifact_sha256
                from rainpulse_algo.worker.runtime import WorkerResult
                objects, summary, metrics = self.multiband.execute(
                    claim["request"], ArtifactObjectReader(client, max_size_bytes=self.multiband.network.maximum_input_bytes),
                    artifact_digest=artifact_sha256)
                return WorkerResult(objects=objects, diagnostics=summary, metrics={}, observability=metrics)
            if self.kind == "qc":
                from rainpulse_algo.radar.qc_worker import _execute_basic_qc
                from rainpulse_algo.worker.domain_contracts import RadarQCRequested
                return _execute_basic_qc(RadarQCRequested.model_validate(claim["request"]), client)
            if self.kind == "diagnostics":
                from rainpulse_algo.diagnostics.worker import _execute_analysis_diagnostics
                from rainpulse_algo.worker.domain_contracts import AnalysisDiagnosticsRequestedV1
                return _execute_analysis_diagnostics(AnalysisDiagnosticsRequestedV1.model_validate(claim["request"]), client)
            from .preview import render_preview
            return render_preview(claim["request"], client)
        except S3Error as error:
            if error.code in {"NoSuchKey", "NoSuchObject", "NoSuchBucket"}:
                raise FrozenInputChanged("declared input object is no longer available") from error
            raise

    @staticmethod
    def metrics(result) -> dict[str, float]:
        return dict(result.observability)

    def publish(self, claim, result, started_tick: float) -> None:
        from minio.error import S3Error
        from rainpulse_algo.worker.contracts import CompletedAsset, JobCompleted, JobCompletedPayload, result_event_id
        from rainpulse_algo.worker.object_store import artifact_sha256

        request = claim["request"]
        self.check_identity(claim["identity"])
        payloads = result.payloads()
        duration = max(0, time.perf_counter() - started_tick)
        now = datetime.now(UTC)
        uri = request["payload"]["output_prefix"].rstrip("/") + "/" + self.artifact_name
        completion = JobCompleted(event_id=result_event_id(UUID(request["job_id"]), "job.completed"),
            occurred_at=now, job_id=UUID(request["job_id"]), run_id=UUID(request["run_id"]),
            trace_id=UUID(request["trace_id"]), payload=JobCompletedPayload(status="succeeded",
                started_at=now - timedelta(seconds=duration), finished_at=now, runtime_ms=round(duration * 1000),
                assets=[CompletedAsset(asset_type="managed_" + self.kind, uri=uri,
                    sha256=artifact_sha256(payloads), size_bytes=sum(map(len, payloads.values())),
                    media_type="application/vnd+zarr" if self.kind == "qc" else "application/json")],
                metrics=result.metrics, diagnostics={**result.diagnostics, "operations": {
                    "attempt_id": claim["attempt_id"], "candidate_only": True,
                    "worker_fingerprint": claim["identity"]["fingerprint"], "default_publication_changed": False}}))
        try:
            self.publisher.publish(output_prefix=request["payload"]["output_prefix"], job_id=UUID(request["job_id"]),
                data=result.data, objects=result.objects, completion=completion, artifact_name=self.artifact_name)
        except S3Error as error:
            if error.code in {"SlowDown", "InternalError", "ServiceUnavailable", "RequestTimeout"}:
                raise ConnectionError("temporary object publication failure") from error
            raise
