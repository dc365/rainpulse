import asyncio
import time
from types import SimpleNamespace
from typing import Any

from rainpulse_algo.worker.contracts import JobCompleted, JobFailed, JobRequested
from rainpulse_algo.worker.runtime import TaskHandler, Worker, WorkerConfig, WorkerResult
from rainpulse_algo.worker.simulation import execute

from .test_worker_contracts import requested_data


class FakeMessage:
    def __init__(self, data: bytes, delivery_attempt: int = 1) -> None:
        self.data = data
        self.metadata = SimpleNamespace(num_delivered=delivery_attempt)
        self.acked = False
        self.terminated = False
        self.nacked = False
        self.nak_delay: float | None = None
        self.in_progress_count = 0

    async def ack(self) -> None:
        self.acked = True

    async def term(self) -> None:
        self.terminated = True

    async def nak(self, delay: float | None = None) -> None:
        self.nacked = True
        self.nak_delay = delay

    async def in_progress(self) -> None:
        self.in_progress_count += 1


class FakeJetStream:
    def __init__(self) -> None:
        self.events: list[tuple[str, bytes, dict[str, str]]] = []

    async def publish(self, subject: str, payload: bytes, headers: dict[str, str]) -> None:
        self.events.append((subject, payload, headers))


class FakePublisher:
    def __init__(self) -> None:
        self.existing: JobCompleted | None = None
        self.publish_count = 0

    def load_completion(self, _: str, _artifact_name: str) -> JobCompleted | None:
        return self.existing

    def publish(self, **values: Any) -> None:
        self.publish_count += 1
        self.existing = values["completion"]


def make_message(
    parameters: dict[str, object] | None = None,
    *,
    delivery_attempt: int = 1,
) -> FakeMessage:
    data = requested_data()
    if parameters is not None:
        data["payload"]["parameters"] = parameters  # type: ignore[index]
    request = JobRequested.model_validate(data)
    return FakeMessage(request.model_dump_json().encode(), delivery_attempt)


def make_handler(
    executor: Any,
    *,
    max_deliveries: int = 3,
    ack_progress_interval_seconds: float | None = None,
) -> TaskHandler:
    return TaskHandler(
        profile="test",
        subject="rainpulse.jobs.requested.test",
        consumer="rainpulse-test-worker",
        request_model=JobRequested,
        executor=executor,
        asset_type="test_artifact",
        artifact_name="result.zarr",
        ack_wait_seconds=30,
        max_deliveries=max_deliveries,
        ack_progress_interval_seconds=ack_progress_interval_seconds,
    )


def test_success_is_published_before_ack_and_replay_reuses_marker() -> None:
    async def scenario() -> None:
        publisher = FakePublisher()
        worker = Worker(
            WorkerConfig("nats://test", "127.0.0.1", 8091, "test-worker"),
            publisher,  # type: ignore[arg-type]
            execute,
        )
        jetstream = FakeJetStream()
        first = make_message()
        await worker.process_message(first, jetstream)
        replay = make_message()
        await worker.process_message(replay, jetstream)

        assert first.acked and replay.acked
        assert publisher.publish_count == 1
        first_result = JobCompleted.model_validate_json(jetstream.events[0][1])
        replay_result = JobCompleted.model_validate_json(jetstream.events[1][1])
        assert first_result.event_id == replay_result.event_id
        assert first_result.payload.diagnostics["worker_delivery"]["attempt"] == 1
        assert first_result.payload.diagnostics["artifact_publication"] == {
            "schema_version": "2.0",
            "data_prefix": f"_objects/{first_result.payload.assets[0].sha256}",
        }

    asyncio.run(scenario())


def test_failure_event_is_published_then_message_is_acked() -> None:
    async def scenario() -> None:
        worker = Worker(
            WorkerConfig("nats://test", "127.0.0.1", 8091, "test-worker"),
            FakePublisher(),  # type: ignore[arg-type]
            execute,
        )
        jetstream = FakeJetStream()
        message = make_message({"simulation": True, "force_failure": True})
        await worker.process_message(message, jetstream)

        assert message.acked and not message.nacked
        failure = JobFailed.model_validate_json(jetstream.events[0][1])
        assert failure.payload.error_code == "SIMULATED_FAILURE"
        assert failure.payload.details["retry_exhausted"] is False

    asyncio.run(scenario())


def test_transient_failure_is_nacked_without_terminal_event() -> None:
    def fail_transiently(_: JobRequested) -> WorkerResult:
        raise TimeoutError("object store timed out")

    async def scenario() -> None:
        worker = Worker(
            WorkerConfig("nats://test", "127.0.0.1", 8091, "test-worker"),
            FakePublisher(),  # type: ignore[arg-type]
            handler=make_handler(fail_transiently),
        )
        jetstream = FakeJetStream()
        message = make_message()
        await worker.process_message(message, jetstream)

        assert message.nacked and not message.acked
        assert message.nak_delay == 1
        assert jetstream.events == []

    asyncio.run(scenario())


def test_exhausted_transient_failure_publishes_terminal_event() -> None:
    def fail_transiently(_: JobRequested) -> WorkerResult:
        raise ConnectionError("dependency unavailable")

    async def scenario() -> None:
        worker = Worker(
            WorkerConfig("nats://test", "127.0.0.1", 8091, "test-worker"),
            FakePublisher(),  # type: ignore[arg-type]
            handler=make_handler(fail_transiently, max_deliveries=3),
        )
        jetstream = FakeJetStream()
        message = make_message(delivery_attempt=3)
        await worker.process_message(message, jetstream)

        assert message.acked and not message.nacked
        failure = JobFailed.model_validate_json(jetstream.events[0][1])
        assert failure.payload.retryable is False
        assert failure.payload.details["delivery_attempt"] == 3
        assert failure.payload.details["retry_exhausted"] is True

    asyncio.run(scenario())


def test_slow_work_refreshes_ack_deadline_until_completion() -> None:
    def execute_slowly(_: JobRequested) -> WorkerResult:
        time.sleep(0.04)
        return WorkerResult(data=b"done")

    async def scenario() -> None:
        worker = Worker(
            WorkerConfig("nats://test", "127.0.0.1", 8091, "test-worker"),
            FakePublisher(),  # type: ignore[arg-type]
            handler=make_handler(execute_slowly, ack_progress_interval_seconds=0.01),
        )
        jetstream = FakeJetStream()
        message = make_message()
        await worker.process_message(message, jetstream)

        assert message.acked
        assert message.in_progress_count >= 2

    asyncio.run(scenario())


def test_ack_progress_failure_does_not_override_successful_job() -> None:
    class FailingProgressMessage(FakeMessage):
        async def in_progress(self) -> None:
            self.in_progress_count += 1
            raise ConnectionError("NATS progress update failed")

    def execute_slowly(_: JobRequested) -> WorkerResult:
        time.sleep(0.03)
        return WorkerResult(data=b"done")

    async def scenario() -> None:
        worker = Worker(
            WorkerConfig("nats://test", "127.0.0.1", 8091, "test-worker"),
            FakePublisher(),  # type: ignore[arg-type]
            handler=make_handler(execute_slowly, ack_progress_interval_seconds=0.01),
        )
        message = FailingProgressMessage(make_message().data)
        await worker.process_message(message, FakeJetStream())

        assert message.acked
        assert message.in_progress_count == 1

    asyncio.run(scenario())


def test_ack_failure_after_completion_does_not_publish_contradictory_failure() -> None:
    class FailingAckMessage(FakeMessage):
        async def ack(self) -> None:
            raise ConnectionError("ack connection failed")

    async def scenario() -> None:
        worker = Worker(
            WorkerConfig("nats://test", "127.0.0.1", 8091, "test-worker"),
            FakePublisher(),  # type: ignore[arg-type]
            execute,
        )
        jetstream = FakeJetStream()
        message = FailingAckMessage(make_message().data)
        await worker.process_message(message, jetstream)

        assert len(jetstream.events) == 1
        assert isinstance(JobCompleted.model_validate_json(jetstream.events[0][1]), JobCompleted)

    asyncio.run(scenario())


def test_invalid_request_is_terminated_without_result() -> None:
    async def scenario() -> None:
        worker = Worker(
            WorkerConfig("nats://test", "127.0.0.1", 8091, "test-worker"),
            FakePublisher(),  # type: ignore[arg-type]
        )
        jetstream = FakeJetStream()
        message = FakeMessage(b'{"schema_version":"1.0"}')
        await worker.process_message(message, jetstream)

        assert message.terminated and not message.acked
        assert jetstream.events == []

    asyncio.run(scenario())
