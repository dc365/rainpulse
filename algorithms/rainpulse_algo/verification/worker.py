from __future__ import annotations

import json
import os
from pathlib import Path

import zarr
from minio import Minio
from zarr.storage import MemoryStore

from rainpulse_algo.worker.domain_contracts import ForecastVerificationRequested
from rainpulse_algo.worker.object_store import (
    ArtifactObjectReader,
    artifact_sha256,
    minio_client_from_environment,
)
from rainpulse_algo.worker.runtime import WorkerResult

from .operational import (
    OperationalVerificationInputError,
    build_operational_verification_result,
    load_operational_verification_profile,
)


def execute_forecast_verification(
    request: ForecastVerificationRequested,
) -> WorkerResult:
    return _execute_forecast_verification(request, minio_client_from_environment())


def _execute_forecast_verification(
    request: ForecastVerificationRequested,
    client: Minio,
) -> WorkerResult:
    profile = load_operational_verification_profile(
        _required_file("RAINPULSE_VERIFICATION_CONFIG")
    )
    _validate_request(request, profile.profile_version)
    reader = ArtifactObjectReader(client)
    forecast_objects = reader.load(request.payload.forecast_uri)
    if artifact_sha256(forecast_objects) != request.payload.forecast_sha256:
        raise OperationalVerificationInputError(
            "requested ForecastOutput SHA-256 differs from artifact"
        )
    truth_object_sets: list[dict[str, bytes]] = []
    for frame in request.payload.truth_frames:
        objects = reader.load(frame.input_uri)
        if artifact_sha256(objects) != frame.input_sha256:
            raise OperationalVerificationInputError(
                "requested RadarAnalysis SHA-256 differs from artifact"
            )
        _validate_truth_request_identity(
            objects, str(frame.analysis_id), frame.valid_time.isoformat()
        )
        truth_object_sets.append(objects)
    objects = build_operational_verification_result(
        forecast_objects,
        truth_object_sets,
        profile=profile,
        run_id=request.run_id,
        job_id=request.job_id,
        forecast_uri=request.payload.forecast_uri,
        truth_uris=[frame.input_uri for frame in request.payload.truth_frames],
    )
    summary = json.loads(objects["summary.json"])
    return WorkerResult(
        objects=objects,
        diagnostics={"forecast_verification": summary},
        metrics={
            "output_size_bytes": float(sum(len(value) for value in objects.values())),
            "object_count": float(len(objects)),
            "truth_frame_count": float(summary["truth_frame_count"]),
            "metric_row_count": float(summary["metric_row_count"]),
            "accumulation_metric_row_count": float(
                summary["accumulation_metric_row_count"]
            ),
        },
    )


def _validate_request(
    request: ForecastVerificationRequested,
    profile_version: str,
) -> None:
    if request.payload.verification_config_version != profile_version:
        raise OperationalVerificationInputError(
            "requested verification profile differs from mounted configuration"
        )
    if (
        request.payload.issue_time.utcoffset() is None
        or request.payload.issue_time.timestamp() % 300
    ):
        raise OperationalVerificationInputError(
            "verification issue time is not on a five-minute UTC boundary"
        )
    expected_times = [
        request.payload.issue_time.timestamp() + lead * 60 for lead in range(5, 125, 5)
    ]
    actual_times = [frame.valid_time.timestamp() for frame in request.payload.truth_frames]
    if actual_times != expected_times:
        raise OperationalVerificationInputError(
            "verification truth frames must cover issue+5 through issue+120 in order"
        )


def _validate_truth_request_identity(
    objects: dict[str, bytes], analysis_id: str, valid_time: str
) -> None:
    store = MemoryStore()
    store.update(objects)
    root = zarr.open_group(store=store, mode="r")
    actual_time = str(root.attrs.get("analysis_time"))
    normalized_requested = valid_time.replace("Z", "+00:00")
    if root.attrs.get("analysis_id") != analysis_id or actual_time != normalized_requested:
        raise OperationalVerificationInputError(
            "RadarAnalysis identity differs from verification request"
        )


def _required_file(name: str) -> Path:
    value = os.getenv(name)
    if not value:
        raise OperationalVerificationInputError(f"{name} is required")
    path = Path(value).resolve(strict=True)
    if not path.is_file():
        raise OperationalVerificationInputError(f"{name} must identify a file")
    return path
