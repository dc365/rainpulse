from __future__ import annotations

from datetime import datetime
from typing import Any, Literal
from urllib.parse import urlparse
from uuid import NAMESPACE_URL, UUID, uuid5

from pydantic import BaseModel, ConfigDict, Field, field_validator

SCHEMA_VERSION = "1.0"
JOB_REQUESTED_SUBJECT = "rainpulse.jobs.requested.model_pysteps_lk"
JOB_COMPLETED_SUBJECT = "rainpulse.jobs.completed"
JOB_FAILED_SUBJECT = "rainpulse.jobs.failed"
JOB_STREAM = "RAINPULSE_JOBS"
WORKER_CONSUMER = "rainpulse-sim-worker"


class ContractModel(BaseModel):
    model_config = ConfigDict(extra="forbid")


class JobRequestedPayload(ContractModel):
    job_type: Literal["model.pysteps_lk"]
    input_uri: str
    output_prefix: str
    grid_id: str = Field(min_length=1)
    config_version: str = Field(min_length=1)
    model_version: str = Field(min_length=1)
    issue_time: datetime
    input_asset_ids: list[UUID] = Field(default_factory=list)
    parameters: dict[str, Any] = Field(default_factory=dict)

    @field_validator("input_uri", "output_prefix")
    @classmethod
    def validate_uri(cls, value: str) -> str:
        parsed = urlparse(value)
        if not parsed.scheme:
            raise ValueError("URI scheme is required")
        return value


class JobRequested(ContractModel):
    schema_version: Literal["1.0"]
    event_id: UUID
    event_type: Literal["job.requested"]
    occurred_at: datetime
    run_id: UUID
    job_id: UUID
    trace_id: UUID
    payload: JobRequestedPayload


class CompletedAsset(ContractModel):
    asset_type: str = Field(min_length=1)
    uri: str
    sha256: str = Field(pattern=r"^[a-f0-9]{64}$")
    size_bytes: int = Field(ge=0)
    media_type: str | None = None

    @field_validator("uri")
    @classmethod
    def validate_uri(cls, value: str) -> str:
        if not urlparse(value).scheme:
            raise ValueError("URI scheme is required")
        return value


class JobCompletedPayload(ContractModel):
    status: Literal["succeeded"]
    started_at: datetime
    finished_at: datetime
    runtime_ms: int = Field(ge=0)
    assets: list[CompletedAsset]
    metrics: dict[str, float]
    diagnostics: dict[str, Any] = Field(default_factory=dict)


class JobCompleted(ContractModel):
    schema_version: Literal["1.0"] = SCHEMA_VERSION
    event_id: UUID
    event_type: Literal["job.completed"] = "job.completed"
    occurred_at: datetime
    run_id: UUID
    job_id: UUID
    trace_id: UUID
    payload: JobCompletedPayload


class JobFailedPayload(ContractModel):
    status: Literal["failed"] = "failed"
    started_at: datetime
    finished_at: datetime
    runtime_ms: int = Field(ge=0)
    error_code: str = Field(min_length=1, max_length=128)
    error_message: str = Field(min_length=1, max_length=2048)
    retryable: bool
    details: dict[str, Any]


class JobFailed(ContractModel):
    schema_version: Literal["1.0"] = SCHEMA_VERSION
    event_id: UUID
    event_type: Literal["job.failed"] = "job.failed"
    occurred_at: datetime
    run_id: UUID
    job_id: UUID
    trace_id: UUID
    payload: JobFailedPayload


def result_event_id(job_id: UUID, event_type: str) -> UUID:
    """Return a stable result UUID so JetStream can deduplicate retries."""

    return uuid5(NAMESPACE_URL, f"rainpulse:{job_id}:{event_type}")
