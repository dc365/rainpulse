from __future__ import annotations

import hashlib
import json
from datetime import UTC, datetime
from pathlib import Path
from uuid import UUID

import numpy as np
import pytest
from pydantic import ValidationError

from rainpulse_algo.nowcast.nowcastnet_offline_zarr import (
    build_nowcastnet_offline_input_zarr_store,
    load_nowcastnet_offline_input,
    validate_nowcastnet_offline_output,
)
from rainpulse_algo.nowcast.nowcastnet_profile import load_nowcastnet_profile
from rainpulse_algo.nowcast.nowcastnet_worker import (
    _execute_nowcastnet_offline,
    _load_runtime,
)
from rainpulse_algo.worker.domain_contracts import NowcastNetOfflineRequested
from rainpulse_algo.worker.object_store import artifact_sha256

from .test_object_store import FakeMinio

REPOSITORY_ROOT = Path(__file__).resolve().parents[2]
PROFILE_PATH = REPOSITORY_ROOT / "configs" / "nowcast" / "rp026-nowcastnet-offline-v1.yaml"
ISSUE_TIME = datetime(2024, 7, 1, 8, 0, tzinfo=UTC)
GRID_ID = "mrms_nowcastnet_512x512_v1"
INPUT_ASSET_IDS = [
    UUID(f"82600000-0000-4000-8000-{index:012d}") for index in range(101, 110)
]


def _profile():
    return load_nowcastnet_profile(PROFILE_PATH)


def _input_objects() -> dict[str, bytes]:
    profile = _profile()
    shape = (
        profile.protocol.input_frames,
        profile.protocol.input_height,
        profile.protocol.input_width,
    )
    rate = np.zeros(shape, dtype="float32")
    rate[:, 240:272, 220:260] = 12.0
    rate[0, 0, 0] = 200.0
    valid = np.ones(shape, dtype="uint8")
    return build_nowcastnet_offline_input_zarr_store(
        rate,
        valid,
        latitude=np.linspace(30.0, 35.11, 512, dtype="float32"),
        longitude=np.linspace(-100.0, -94.89, 512, dtype="float32"),
        issue_time=ISSUE_TIME,
        grid_id=GRID_ID,
        input_asset_ids=INPUT_ASSET_IDS,
        source_group="mrms-development-2024-07",
        profile=profile,
    )


def _request() -> NowcastNetOfflineRequested:
    return NowcastNetOfflineRequested.model_validate(
        {
            "schema_version": "1.0",
            "event_id": "82600000-0000-4000-8000-000000000001",
            "event_type": "forecast.nowcastnet_offline.requested.v1",
            "occurred_at": "2026-08-30T08:00:00Z",
            "run_id": "82600000-0000-4000-8000-000000000002",
            "job_id": "82600000-0000-4000-8000-000000000003",
            "trace_id": "82600000-0000-4000-8000-000000000004",
            "payload": {
                "input_uri": "s3://rainpulse-offline/nowcastnet/inputs/test/input.zarr",
                "output_prefix": "s3://rainpulse-offline/nowcastnet/outputs/test/",
                "issue_time": ISSUE_TIME.isoformat(),
                "grid_id": GRID_ID,
                "input_asset_ids": [str(value) for value in INPUT_ASSET_IDS],
                "model_id": "nowcastnet",
                "model_version": "official-codeocean-v1-cc0",
                "config_version": "rp026-nowcastnet-offline-v1",
                "input_contract_version": "1.0",
                "output_contract_version": "1.0",
                "random_seed": 20260830,
            },
        }
    )


def _published_input(objects: dict[str, bytes]) -> FakeMinio:
    client = FakeMinio()
    prefix = "nowcastnet/inputs/test/input.zarr"
    manifest = []
    for key, value in objects.items():
        client.objects[("rainpulse-offline", f"{prefix}/{key}")] = value
        manifest.append(
            {
                "key": key,
                "sha256": hashlib.sha256(value).hexdigest(),
                "size_bytes": len(value),
            }
        )
    client.objects[("rainpulse-offline", f"{prefix}/_SUCCESS.json")] = json.dumps(
        {
            "schema_version": "2.0",
            "sha256": artifact_sha256(objects),
            "size_bytes": sum(map(len, objects.values())),
            "objects": sorted(manifest, key=lambda item: item["key"]),
        }
    ).encode()
    return client


def test_offline_input_round_trip_preserves_identity_and_three_state_gate() -> None:
    profile = _profile()
    fields = load_nowcastnet_offline_input(_input_objects(), profile=profile)

    assert fields.rain_rate_mm_h.shape == (9, 512, 512)
    assert fields.issue_time == ISSUE_TIME
    assert fields.grid_id == GRID_ID
    assert fields.input_asset_ids == tuple(INPUT_ASSET_IDS)
    assert np.all(fields.valid_mask == 1)


def test_offline_worker_reads_committed_input_and_emits_non_operational_output() -> None:
    profile = _profile()
    input_objects = _input_objects()
    client = _published_input(input_objects)
    calls: list[tuple[int, int]] = []

    def backend(values: np.ndarray, members: int, random_seed: int) -> np.ndarray:
        calls.append((members, random_seed))
        assert values[0, 0, 0, 0] == profile.protocol.rain_rate_cap_mm_h
        output = np.broadcast_to(
            values[-1, ..., 0],
            (members, profile.protocol.output_frames, 512, 512),
        ).copy()
        output[0, 0, 0, 0] = -0.25
        return output

    worker_result = _execute_nowcastnet_offline(
        _request(),
        client,  # type: ignore[arg-type]
        profile=profile,
        backend=backend,
        runtime_info={"device": "injected-test-backend"},
    )
    validation = validate_nowcastnet_offline_output(
        worker_result.objects or {}, profile=profile
    )

    assert calls == [(4, 20260830)]
    assert validation["member_count"] == 4
    assert validation["lead_count"] == 20
    assert worker_result.metrics["clipped_input_pixel_count"] == 1
    assert worker_result.metrics["clipped_negative_output_pixel_count"] == 1
    assert worker_result.metrics["operational_eligible"] == 0
    assert worker_result.metrics["product_publication_enabled"] == 0
    assert worker_result.diagnostics["nowcastnet_offline"]["input_uri"] == (
        _request().payload.input_uri
    )


def test_offline_request_rejects_duplicate_assets_and_non_s3_input() -> None:
    value = json.loads(
        (
            REPOSITORY_ROOT
            / "contracts"
            / "examples"
            / "forecast-nowcastnet-offline-requested.json"
        ).read_text()
    )
    value["payload"]["input_asset_ids"][1] = value["payload"]["input_asset_ids"][0]
    value["payload"]["input_uri"] = "file:///tmp/input.zarr"
    with pytest.raises(ValidationError):
        NowcastNetOfflineRequested.model_validate(value)


def test_worker_runtime_loads_the_official_backend_once(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    import rainpulse_algo.nowcast.nowcastnet_worker as worker_module

    loads: list[tuple[str, str]] = []

    class FakeBackend:
        def __init__(self, capsule_root: str, *, profile: object, device: str) -> None:
            loads.append((capsule_root, device))

    _load_runtime.cache_clear()
    monkeypatch.setattr(worker_module, "OfficialNowcastNetBackend", FakeBackend)
    first = _load_runtime(str(PROFILE_PATH), "/opt/rainpulse/nowcastnet/official-v1", "cuda:0")
    second = _load_runtime(str(PROFILE_PATH), "/opt/rainpulse/nowcastnet/official-v1", "cuda:0")

    assert first is second
    assert loads == [("/opt/rainpulse/nowcastnet/official-v1", "cuda:0")]
    _load_runtime.cache_clear()
