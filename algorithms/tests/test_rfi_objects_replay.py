from __future__ import annotations

import hashlib
import json

import pytest

from rainpulse_algo.radar.qc_engine.replay import replay_task
from rainpulse_algo.radar.qc_worker import _execute_basic_qc
from rainpulse_algo.worker.object_store import artifact_sha256

from .test_object_store import FakeMinio
from .test_rfi_objects_context import mount, request, stamp
from .test_rfi_objects_v2 import FLAGS, PROFILE, scene


def test_frozen_offline_replay_matches_actual_worker_bytes(tmp_path, monkeypatch):
    monkeypatch.setenv("RAINPULSE_RADAR_QC_CONFIG", str(PROFILE))
    monkeypatch.setenv("RAINPULSE_QC_FLAG_DEFINITIONS", str(FLAGS))
    obj, _ = scene()
    current = stamp(obj, "2026-08-28T00:40:00Z")
    client = FakeMinio()
    uri = mount(client, current, "current")
    job = request(current, uri, [])
    task = tmp_path / "task.json"
    task.write_text(job.model_dump_json())
    folder = tmp_path / "source.zarr"
    for k, v in current.items():
        file = folder / k
        file.parent.mkdir(parents=True, exist_ok=True)
        file.write_bytes(v)
    manifest = {
        "schema_version": "rainpulse.qc-task-replay.v1",
        "task": {"path": "task.json", "sha256": hashlib.sha256(task.read_bytes()).hexdigest()},
        "profile": {
            "path": str(PROFILE),
            "sha256": hashlib.sha256(PROFILE.read_bytes()).hexdigest(),
        },
        "flags": {"path": str(FLAGS), "sha256": hashlib.sha256(FLAGS.read_bytes()).hexdigest()},
        "artifacts": [{"uri": uri, "path": "source.zarr", "sha256": artifact_sha256(current)}],
    }
    file = tmp_path / "manifest.json"
    file.write_text(json.dumps(manifest))
    result = replay_task(file, tmp_path / "out")
    actual = _execute_basic_qc(job, client)
    assert result["output_sha256"] == artifact_sha256(actual.objects)
    assert result["published"] is False
    with pytest.raises(ValueError, match="already exists"):
        replay_task(file, tmp_path / "out")
    manifest["profile"]["sha256"] = "0" * 64
    file.write_text(json.dumps(manifest))
    with pytest.raises(ValueError, match="profile hash"):
        replay_task(file, tmp_path / "bad")
