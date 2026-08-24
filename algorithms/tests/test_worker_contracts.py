from datetime import UTC, datetime
from uuid import UUID

import pytest
from pydantic import ValidationError

from rainpulse_algo.worker.contracts import JobRequested, result_event_id


def requested_data() -> dict[str, object]:
    return {
        "schema_version": "1.0",
        "event_id": "d3407132-8f23-49d1-8107-39b45c943760",
        "event_type": "job.requested",
        "occurred_at": "2026-08-24T03:00:01Z",
        "run_id": "f3641335-13a3-4f68-96c0-56a5e0e684d7",
        "job_id": "0894481f-c096-49af-8d32-e9c531a66772",
        "trace_id": "0d049a59-754c-4405-8a31-d789685056c2",
        "payload": {
            "job_type": "model.pysteps_lk",
            "input_uri": "s3://rainpulse/simulations/input.zarr",
            "output_prefix": "s3://rainpulse/simulations/run/",
            "grid_id": "rp003-sim-grid",
            "config_version": "rp003-sim-v1",
            "model_version": "pysteps-lk-sim-v1",
            "issue_time": datetime(2026, 8, 24, 3, tzinfo=UTC).isoformat(),
            "input_asset_ids": [],
            "parameters": {"simulation": True},
        },
    }


def test_requested_contract_forbids_unknown_fields() -> None:
    data = requested_data()
    data["unexpected"] = True

    with pytest.raises(ValidationError):
        JobRequested.model_validate(data)


def test_result_event_id_is_stable_and_event_specific() -> None:
    job_id = UUID("0894481f-c096-49af-8d32-e9c531a66772")

    first = result_event_id(job_id, "job.completed")
    assert first == result_event_id(job_id, "job.completed")
    assert first != result_event_id(job_id, "job.failed")
