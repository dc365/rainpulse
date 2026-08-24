import asyncio
import json
from pathlib import Path
from typing import Any

import pytest
from pydantic import ValidationError

from rainpulse_algo.worker.contracts import JobCompleted
from rainpulse_algo.worker.domain_contracts import NowcastInputRequested
from rainpulse_algo.worker.handlers import HANDLERS, handler_for_profile
from rainpulse_algo.worker.runtime import Worker, WorkerConfig

REPOSITORY_ROOT = Path(__file__).resolve().parents[2]

CASES = (
    (
        "radar-decode-synthetic",
        "radar-decode-requested",
        "normalized_radar_volume",
        "volume.zarr",
    ),
    ("radar-qc-synthetic", "radar-qc-requested", "qc_radar_volume", "volume.zarr"),
    ("radar-grid-synthetic", "radar-grid-requested", "radar_grid", "grid.zarr"),
    (
        "mosaic-qpe-synthetic",
        "analysis-mosaic-requested",
        "radar_analysis",
        "analysis.zarr",
    ),
    (
        "nowcast-input-synthetic",
        "nowcast-input-requested",
        "nowcast_input",
        "input.zarr",
    ),
)


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
        self.data: bytes | None = None
        self.artifact_name: str | None = None
        self.publish_count = 0

    def load_completion(self, _: str, _artifact_name: str) -> JobCompleted | None:
        return self.existing

    def publish(self, **values: Any) -> None:
        self.publish_count += 1
        self.existing = values["completion"]
        self.data = values["data"]
        self.artifact_name = values["artifact_name"]


def example_bytes(name: str) -> bytes:
    return (REPOSITORY_ROOT / "contracts" / "examples" / f"{name}.json").read_bytes()


@pytest.mark.parametrize(("profile", "example", "asset_type", "artifact_name"), CASES)
def test_domain_handler_validates_routes_and_publishes_synthetic_contract_artifact(
    profile: str,
    example: str,
    asset_type: str,
    artifact_name: str,
) -> None:
    async def scenario() -> None:
        handler = handler_for_profile(profile)
        publisher = FakePublisher()
        worker = Worker(
            WorkerConfig("nats://test", "127.0.0.1", 8091, profile, profile),
            publisher,  # type: ignore[arg-type]
            handler=handler,
        )
        message = FakeMessage(example_bytes(example))
        jetstream = FakeJetStream()

        await worker.process_message(message, jetstream)
        replay = FakeMessage(example_bytes(example))
        await worker.process_message(replay, jetstream)

        assert message.acked and replay.acked
        assert not message.terminated and not message.nacked
        assert publisher.publish_count == 1
        assert publisher.artifact_name == artifact_name
        assert publisher.data is not None
        payload = json.loads(publisher.data)
        assert payload["synthetic_contract_fixture"] is True
        assert "No radar array" in payload["notice"]
        completion = JobCompleted.model_validate_json(jetstream.events[0][1])
        replay_completion = JobCompleted.model_validate_json(jetstream.events[1][1])
        assert completion.event_id == replay_completion.event_id
        assert completion.payload.assets[0].asset_type == asset_type
        assert completion.payload.assets[0].uri.endswith("/" + artifact_name)

    asyncio.run(scenario())


def test_handler_registry_has_unique_subjects_and_durable_consumers() -> None:
    assert len({handler.subject for handler in HANDLERS.values()}) == len(HANDLERS)
    assert len({handler.consumer for handler in HANDLERS.values()}) == len(HANDLERS)
    assert all(
        handler.subject.startswith("rainpulse.jobs.requested.")
        for handler in HANDLERS.values()
    )


def test_nowcast_input_contract_rejects_mismatched_frame_identities() -> None:
    value = json.loads(example_bytes("nowcast-input-requested"))
    value["payload"]["input_uris"].pop()

    with pytest.raises(ValidationError):
        NowcastInputRequested.model_validate(value)
