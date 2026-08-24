from __future__ import annotations

import asyncio
import hashlib
import json
import os
import time
from collections.abc import Callable
from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Any

import nats
from nats.errors import TimeoutError as NATSTimeoutError
from nats.js.api import AckPolicy, ConsumerConfig
from pydantic import BaseModel, ValidationError

from .contracts import (
    JOB_COMPLETED_SUBJECT,
    JOB_FAILED_SUBJECT,
    JOB_REQUESTED_SUBJECT,
    JOB_STREAM,
    WORKER_CONSUMER,
    CompletedAsset,
    JobCompleted,
    JobCompletedPayload,
    JobFailed,
    JobFailedPayload,
    JobRequested,
    result_event_id,
)
from .object_store import AtomicObjectPublisher, parse_s3_uri
from .simulation import SimulatedFailure, execute


@dataclass(frozen=True)
class WorkerConfig:
    nats_url: str
    health_host: str
    health_port: int
    worker_id: str
    profile: str = "simulation"

    @classmethod
    def from_environment(cls) -> WorkerConfig:
        health = os.getenv("RAINPULSE_WORKER_HEALTH_ADDR", "0.0.0.0:8091")
        host, separator, raw_port = health.rpartition(":")
        if not separator or not raw_port.isdigit():
            raise ValueError("RAINPULSE_WORKER_HEALTH_ADDR must be host:port")
        return cls(
            nats_url=os.getenv("RAINPULSE_NATS_URL", "nats://127.0.0.1:4222"),
            health_host=host or "0.0.0.0",
            health_port=int(raw_port),
            worker_id=os.getenv("RAINPULSE_WORKER_ID", "pysteps-lk-sim"),
            profile=os.getenv("RAINPULSE_WORKER_PROFILE", "simulation"),
        )


@dataclass(frozen=True)
class WorkerResult:
    data: bytes
    metrics: dict[str, float]


@dataclass(frozen=True)
class TaskHandler:
    profile: str
    subject: str
    consumer: str
    request_model: type[BaseModel]
    executor: Callable[[Any], WorkerResult | tuple[bytes, dict[str, float]]]
    asset_type: str
    artifact_name: str
    media_type: str = "application/vnd+zarr"


class Worker:
    def __init__(
        self,
        config: WorkerConfig,
        publisher: AtomicObjectPublisher,
        executor: Callable[[JobRequested], tuple[bytes, dict[str, float]]] = execute,
        handler: TaskHandler | None = None,
    ) -> None:
        self._config = config
        self._publisher = publisher
        self._handler = handler or TaskHandler(
            profile="simulation",
            subject=JOB_REQUESTED_SUBJECT,
            consumer=WORKER_CONSUMER,
            request_model=JobRequested,
            executor=executor,
            asset_type="forecast_zarr",
            artifact_name="forecast.zarr",
        )
        self._connection: Any = None
        self._ready = False
        self._stop = asyncio.Event()

    async def run(self) -> None:
        self._connection = await nats.connect(
            servers=[self._config.nats_url],
            name=self._config.worker_id,
            connect_timeout=5,
            max_reconnect_attempts=-1,
            reconnect_time_wait=1,
        )
        jetstream = self._connection.jetstream()
        subscription = await jetstream.pull_subscribe(
            self._handler.subject,
            durable=self._handler.consumer,
            stream=JOB_STREAM,
            config=ConsumerConfig(
                ack_policy=AckPolicy.EXPLICIT,
                ack_wait=30,
                max_deliver=10,
            ),
        )
        health_server = await asyncio.start_server(
            self._handle_health,
            self._config.health_host,
            self._config.health_port,
        )
        self._ready = True
        log_event(
            "info",
            "worker.ready",
            worker_id=self._config.worker_id,
            profile=self._handler.profile,
            subject=self._handler.subject,
        )
        try:
            while not self._stop.is_set():
                try:
                    messages = await subscription.fetch(1, timeout=1)
                except NATSTimeoutError:
                    continue
                for message in messages:
                    await self.process_message(message, jetstream)
        finally:
            self._ready = False
            health_server.close()
            await health_server.wait_closed()
            if self._connection is not None:
                await self._connection.drain()

    def stop(self) -> None:
        self._stop.set()

    async def process_message(self, message: Any, jetstream: Any) -> None:
        try:
            request = self._handler.request_model.model_validate_json(message.data)
        except ValidationError as error:
            log_event("error", "job.invalid", error_code="INVALID_JOB_REQUEST", error=str(error))
            await message.term()
            return

        started_at = datetime.now(UTC)
        started_tick = time.perf_counter()
        context = {
            "run_id": str(request.run_id),
            "job_id": str(request.job_id),
            "trace_id": str(request.trace_id),
            "event_type": request.event_type,
            "worker_profile": self._handler.profile,
        }
        log_event("info", "job.started", **context)

        try:
            existing = await asyncio.to_thread(
                self._publisher.load_completion,
                request.payload.output_prefix,
                self._handler.artifact_name,
            )
            if existing is not None:
                self._validate_existing(existing, request)
                await self._publish_result(jetstream, existing)
                await message.ack()
                log_event("info", "job.idempotent_replay", **context)
                return

            raw_result = await asyncio.to_thread(self._handler.executor, request)
            result = self._coerce_result(raw_result)
            completion = self._build_completion(
                request=request,
                started_at=started_at,
                started_tick=started_tick,
                result=result,
            )
            await asyncio.to_thread(
                self._publisher.publish,
                output_prefix=request.payload.output_prefix,
                job_id=request.job_id,
                data=result.data,
                completion=completion,
                artifact_name=self._handler.artifact_name,
            )
            await self._publish_result(jetstream, completion)
            await message.ack()
            log_event(
                "info",
                "job.completed",
                duration_ms=completion.payload.runtime_ms,
                **context,
            )
        except Exception as error:  # noqa: BLE001 - converted to the failure contract below
            failure = self._build_failure(request, started_at, started_tick, error)
            try:
                await self._publish_result(jetstream, failure)
            except Exception as publish_error:  # noqa: BLE001 - NAK must preserve delivery
                log_event(
                    "error",
                    "job.result_publish_failed",
                    error_code="RESULT_PUBLISH_FAILED",
                    error=type(publish_error).__name__,
                    **context,
                )
                await message.nak()
                return
            await message.ack()
            log_event(
                "error",
                "job.failed",
                duration_ms=failure.payload.runtime_ms,
                error_code=failure.payload.error_code,
                **context,
            )

    async def _publish_result(self, jetstream: Any, event: JobCompleted | JobFailed) -> None:
        subject = (
            JOB_COMPLETED_SUBJECT if isinstance(event, JobCompleted) else JOB_FAILED_SUBJECT
        )
        await jetstream.publish(
            subject,
            event.model_dump_json().encode(),
            headers={"Nats-Msg-Id": str(event.event_id)},
        )

    def _build_completion(
        self,
        *,
        request: Any,
        started_at: datetime,
        started_tick: float,
        result: WorkerResult,
    ) -> JobCompleted:
        finished_at = datetime.now(UTC)
        runtime_ms = max(0, round((time.perf_counter() - started_tick) * 1000))
        bucket, prefix = parse_s3_uri(request.payload.output_prefix)
        asset_uri = f"s3://{bucket}/{prefix.rstrip('/')}/{self._handler.artifact_name}"
        return JobCompleted(
            event_id=result_event_id(request.job_id, "job.completed"),
            occurred_at=finished_at,
            run_id=request.run_id,
            job_id=request.job_id,
            trace_id=request.trace_id,
            payload=JobCompletedPayload(
                status="succeeded",
                started_at=started_at,
                finished_at=finished_at,
                runtime_ms=runtime_ms,
                assets=[
                    CompletedAsset(
                        asset_type=self._handler.asset_type,
                        uri=asset_uri,
                        sha256=hashlib.sha256(result.data).hexdigest(),
                        size_bytes=len(result.data),
                        media_type=self._handler.media_type,
                    )
                ],
                metrics=result.metrics,
            ),
        )

    def _build_failure(
        self,
        request: Any,
        started_at: datetime,
        started_tick: float,
        error: Exception,
    ) -> JobFailed:
        finished_at = datetime.now(UTC)
        simulated = isinstance(error, SimulatedFailure)
        return JobFailed(
            event_id=result_event_id(request.job_id, "job.failed"),
            occurred_at=finished_at,
            run_id=request.run_id,
            job_id=request.job_id,
            trace_id=request.trace_id,
            payload=JobFailedPayload(
                started_at=started_at,
                finished_at=finished_at,
                runtime_ms=max(0, round((time.perf_counter() - started_tick) * 1000)),
                error_code="SIMULATED_FAILURE" if simulated else "WORKER_PROCESSING_ERROR",
                error_message=(
                    "RP-005 simulated worker failure" if simulated else "Worker processing failed"
                ),
                retryable=False,
                details={"worker_id": self._config.worker_id, "exception": type(error).__name__},
            ),
        )

    @staticmethod
    def _validate_existing(completion: JobCompleted, request: Any) -> None:
        if (
            completion.run_id != request.run_id
            or completion.job_id != request.job_id
            or completion.trace_id != request.trace_id
        ):
            raise RuntimeError("published completion identity does not match request")

    @staticmethod
    def _coerce_result(
        result: WorkerResult | tuple[bytes, dict[str, float]],
    ) -> WorkerResult:
        if isinstance(result, WorkerResult):
            return result
        data, metrics = result
        return WorkerResult(data=data, metrics=metrics)

    async def _handle_health(
        self,
        reader: asyncio.StreamReader,
        writer: asyncio.StreamWriter,
    ) -> None:
        try:
            request_line = await asyncio.wait_for(reader.readline(), timeout=2)
            path = request_line.decode(errors="replace").split(" ")[1]
        except (TimeoutError, IndexError):
            path = ""
        connected = self._connection is not None and self._connection.is_connected
        healthy = path == "/healthz" and self._ready and connected
        status = "200 OK" if healthy else "503 Service Unavailable"
        body = json.dumps(
            {
                "status": "ready" if healthy else "unavailable",
                "profile": self._handler.profile,
            }
        ).encode()
        writer.write(
            (
                f"HTTP/1.1 {status}\r\nContent-Type: application/json\r\n"
                f"Content-Length: {len(body)}\r\nConnection: close\r\n\r\n"
            ).encode()
            + body
        )
        await writer.drain()
        writer.close()
        await writer.wait_closed()


def log_event(level: str, event: str, **fields: Any) -> None:
    print(
        json.dumps(
            {
                "timestamp": datetime.now(UTC).isoformat(),
                "level": level,
                "service": "rainpulse-worker",
                "event": event,
                **fields,
            },
            separators=(",", ":"),
            sort_keys=True,
        ),
        flush=True,
    )
