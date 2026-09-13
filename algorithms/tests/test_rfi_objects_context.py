from __future__ import annotations

import hashlib
import json
from datetime import UTC, datetime
from types import SimpleNamespace
from uuid import uuid4

import numpy as np
import pytest

from rainpulse_algo.radar.qc_engine.adapters import adapt_sweep
from rainpulse_algo.radar.qc_engine.context import validate_context_identity
from rainpulse_algo.radar.qc_engine.temporal import aggregate_temporal_rfi
from rainpulse_algo.radar.qc_input import open_qc_input
from rainpulse_algo.radar.qc_worker import _execute_basic_qc
from rainpulse_algo.worker.domain_contracts import RadarQCRequested
from rainpulse_algo.worker.object_store import artifact_sha256

from .test_object_store import FakeMinio
from .test_rfi_objects_v2 import FLAGS, PROFILE, evaluate, profile, scene


def stamp(obj, when):
    obj = dict(obj)
    attrs = json.loads(obj[".zattrs"])
    attrs.update(scan_id=str(uuid4()), volume_end_time_utc=when, ingest_available_at_utc=when)
    obj[".zattrs"] = json.dumps(attrs).encode()
    return obj


def mount(client, obj, name):
    prefix = "test/" + name
    for key, value in obj.items():
        client.objects[("rainpulse", f"{prefix}/{key}")] = value
    client.objects[("rainpulse", f"{prefix}/_SUCCESS.json")] = json.dumps(
        {
            "schema_version": "1.0",
            "sha256": artifact_sha256(obj),
            "size_bytes": sum(map(len, obj.values())),
            "objects": [
                {"key": k, "size_bytes": len(v), "sha256": hashlib.sha256(v).hexdigest()}
                for k, v in sorted(obj.items())
            ],
        }
    ).encode()
    return f"s3://rainpulse/{prefix}"


def request(obj, uri, contexts):
    attrs = json.loads(obj[".zattrs"])
    return RadarQCRequested.model_validate(
        {
            "schema_version": "1.0",
            "event_type": "radar.qc.requested.v1",
            "event_id": str(uuid4()),
            "run_id": str(uuid4()),
            "job_id": str(uuid4()),
            "trace_id": str(uuid4()),
            "occurred_at": "2026-08-28T00:40:10Z",
            "payload": {
                "scan_id": attrs["scan_id"],
                "radar_id": attrs["radar_id"],
                "radar_config_version": attrs["radar_config_version"],
                "input_uri": uri,
                "output_prefix": "s3://rainpulse/output/",
                "qc_profile": profile().profile_version,
                "qc_pipeline_version": profile().pipeline_version,
                "flag_definition_version": "qc-flags-v2",
                "qc_profile_sha256": hashlib.sha256(PROFILE.read_bytes()).hexdigest(),
                "temporal_context": [
                    {"radar_id": attrs["radar_id"], "input_uri": x} for x in contexts
                ],
            },
        }
    )


def test_temporal_votes_are_per_gate_and_missing_has_no_negative_vote():
    obj, target = scene(missing_background=True)
    native, _, evidence, _ = evaluate(obj)
    current_obj, _ = scene()
    current = adapt_sweep(open_qc_input(current_obj).root, "sweep_000", profile())
    aggregate = aggregate_temporal_rfi(current, [(native, evidence)] * 2)
    assert (aggregate["TEMPORAL_RFI_SAMPLE_COUNT"][target] == 2).all()
    assert (aggregate["TEMPORAL_CANDIDATE_PERSISTENCE"][target] == 1).all()
    assert not aggregate["TEMPORAL_RFI_SAMPLE_COUNT"][50].any()
    assert np.isnan(aggregate["TEMPORAL_CANDIDATE_PERSISTENCE"][50]).all()


def test_future_and_late_context_are_excluded():
    obj, _ = scene()
    current = open_qc_input(stamp(obj, "2026-08-28T00:40:00Z")).root
    future = open_qc_input(stamp(obj, "2026-08-28T00:45:00Z")).root
    entry = SimpleNamespace(radar_id="z9999")
    args = dict(
        role="temporal",
        current_root=current,
        cutoff=datetime(2026, 8, 28, 0, 40, 10, tzinfo=UTC),
        config=profile().context,
    )
    assert validate_context_identity(future, entry, **args) == "future_context_disallowed"
    old = stamp(obj, "2026-08-28T00:35:00Z")
    attrs = json.loads(old[".zattrs"])
    attrs["ingest_available_at_utc"] = "2026-08-28T00:50:00Z"
    old[".zattrs"] = json.dumps(attrs).encode()
    assert (
        validate_context_identity(open_qc_input(old).root, entry, **args)
        == "context_not_available_at_cutoff"
    )


def test_actual_worker_uses_time_support_and_retry_bytes_are_identical(monkeypatch):
    monkeypatch.setenv("RAINPULSE_RADAR_QC_CONFIG", str(PROFILE))
    monkeypatch.setenv("RAINPULSE_QC_FLAG_DEFINITIONS", str(FLAGS))
    current, target = scene(rho=0.88)
    current = stamp(current, "2026-08-28T00:40:00Z")
    client = FakeMinio()
    uri = mount(client, current, "current")
    previous = []
    for when in ("00:25", "00:30", "00:35"):
        previous.append(mount(client, stamp(current, f"2026-08-28T{when}:00Z"), when[-2:]))
    job = request(current, uri, previous)
    first = _execute_basic_qc(job, client)
    second = _execute_basic_qc(job, client)
    assert first.objects == second.objects
    fields = open_qc_input(first.objects).root["sweep_000"]
    assert (fields["QC_ACTION"][:][target] == 2).all()
    assert (fields["TEMPORAL_RFI_SAMPLE_COUNT"][:][target] == 3).all()
    cold = _execute_basic_qc(request(current, uri, []), client)
    fields = open_qc_input(cold.objects).root["sweep_000"]
    assert fields["RFI_QUARANTINE_MASK"][:][target].all()
    assert not (fields["QC_ACTION"][:][target] == 2).any()


def test_same_context_bytes_under_multiple_uris_cannot_supply_multiple_votes(monkeypatch):
    monkeypatch.setenv("RAINPULSE_RADAR_QC_CONFIG", str(PROFILE))
    monkeypatch.setenv("RAINPULSE_QC_FLAG_DEFINITIONS", str(FLAGS))
    obj, _ = scene()
    current = stamp(obj, "2026-08-28T00:40:00Z")
    previous = stamp(obj, "2026-08-28T00:35:00Z")
    client = FakeMinio()
    uri = mount(client, current, "current")
    a, b = mount(client, previous, "alias-a"), mount(client, previous, "alias-b")
    with pytest.raises(RuntimeError, match="duplicate physical"):
        _execute_basic_qc(request(current, uri, [a, b]), client)


def test_mismatched_cut_metadata_cannot_contribute():
    obj, _ = scene()
    native, _, evidence, _ = evaluate(obj)
    from dataclasses import replace

    other = replace(native, audit={**native.audit, "cut_metadata": {"waveform": "other"}})
    result = aggregate_temporal_rfi(native, [(other, evidence)])
    assert not result["TEMPORAL_RFI_SAMPLE_COUNT"].any()
