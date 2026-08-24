import asyncio
from typing import Any

from rainpulse_algo.worker.contracts import JobCompleted, JobFailed, JobRequested
from rainpulse_algo.worker.runtime import Worker, WorkerConfig
from rainpulse_algo.worker.simulation import execute

from .test_worker_contracts import requested_data


class FakeMessage:
    def __init__(self, data: bytes) -> None:
        self.data = data
        self.acked = False
        self.terminated = False
        self.nacked = False

    async def ack(self) -> None:
        self.acked = True

    async def term(self) -> None:
        self.terminated = True

    async def nak(self) -> None:
        self.nacked = True


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


def make_message(parameters: dict[str, object] | None = None) -> FakeMessage:
    data = requested_data()
    if parameters is not None:
        data["payload"]["parameters"] = parameters  # type: ignore[index]
    request = JobRequested.model_validate(data)
    return FakeMessage(request.model_dump_json().encode())


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
