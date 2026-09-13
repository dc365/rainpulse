"""V3 genuine-library, Worker, offline-replay, topology and diagnostic contracts."""

from __future__ import annotations

import hashlib
import json
from dataclasses import replace

import numpy as np
import pytest
import yaml
import zarr
from jsonschema import Draft202012Validator
from zarr.storage import MemoryStore

from rainpulse_algo.radar.qc import apply_basic_qc
from rainpulse_algo.radar.qc_engine.audit import audit_artifact
from rainpulse_algo.radar.qc_engine.multivariate import joint_moment_evidence
from rainpulse_algo.radar.qc_engine.objects_v3 import bounded_search
from rainpulse_algo.radar.qc_engine.replay import replay_task
from rainpulse_algo.radar.qc_engine.review import run_manifest
from rainpulse_algo.radar.qc_input import open_qc_input
from rainpulse_algo.radar.qc_worker import _execute_basic_qc
from rainpulse_algo.radar.qc_zarr import build_qc_zarr_store, validate_qc_zarr_store
from rainpulse_algo.worker.object_store import artifact_sha256

from .test_object_store import FakeMinio
from .test_qc_opensource import field_case
from .test_radar_qc import synthetic_normalized_fixture
from .test_rfi_multivariate_v3 import FLAGS, PROFILE, ROOT, make_native, profile, run_native
from .test_rfi_objects_context import mount, request, stamp


def v3_request(obj, uri, contexts):
    job = request(obj, uri, contexts)
    return job.model_copy(
        update={
            "payload": job.payload.model_copy(
                update={
                    "qc_profile": profile().profile_version,
                    "qc_pipeline_version": profile().pipeline_version,
                    "qc_profile_sha256": hashlib.sha256(PROFILE.read_bytes()).hexdigest(),
                }
            )
        }
    )


def native_objects(**kwargs):
    native, domain = make_native(**kwargs)
    obj = synthetic_normalized_fixture(
        native.fields["DBZH"],
        range_m=native.ranges,
        moments={k: v for k, v in native.fields.items() if k != "DBZH"},
    )
    return stamp(obj, "2026-08-28T00:40:00Z"), domain


def save_objects(folder, objects):
    for key, value in objects.items():
        path = folder / key
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(value)


@pytest.fixture
def runtime(monkeypatch):
    monkeypatch.setenv("RAINPULSE_RADAR_QC_CONFIG", str(PROFILE))
    monkeypatch.setenv("RAINPULSE_QC_FLAG_DEFINITIONS", str(FLAGS))
    monkeypatch.setenv("RAINPULSE_RADAR_PHASE_PROCESSING_PROFILE", "")
    monkeypatch.setenv("RAINPULSE_RADAR_ATTENUATION_PROFILE", "")
    return FakeMinio()


def test_actual_worker_and_offline_replay_have_identical_artifact_bytes(tmp_path, runtime):
    obj, domain = native_objects(rho=0.97, phase_noise=True, zdr=8)
    before = dict(obj)
    uri = mount(runtime, obj, "current-v3")
    job = v3_request(obj, uri, [])
    one = _execute_basic_qc(job, runtime)
    two = _execute_basic_qc(job, runtime)
    assert one.objects == two.objects and obj == before
    fields = open_qc_input(one.objects).root["sweep_000"]
    assert fields["RFI_V3_JOINT_CONFIRMED_MASK"][:][domain].any()
    assert fields["PHIDP_TRUST_MASK"][:][domain].mean() < 0.1
    task = tmp_path / "task.json"
    task.write_text(job.model_dump_json())
    save_objects(tmp_path / "source.zarr", obj)
    manifest = {
        "schema_version": "rainpulse.qc-task-replay.v1",
        "task": {"path": "task.json", "sha256": hashlib.sha256(task.read_bytes()).hexdigest()},
        "profile": {
            "path": str(PROFILE),
            "sha256": hashlib.sha256(PROFILE.read_bytes()).hexdigest(),
        },
        "flags": {"path": str(FLAGS), "sha256": hashlib.sha256(FLAGS.read_bytes()).hexdigest()},
        "artifacts": [{"uri": uri, "path": "source.zarr", "sha256": artifact_sha256(obj)}],
    }
    file = tmp_path / "manifest.json"
    file.write_text(json.dumps(manifest))
    receipt = replay_task(file, tmp_path / "out")
    assert receipt["output_sha256"] == artifact_sha256(one.objects)
    assert receipt["published"] is False
    assert (tmp_path / "out/residual-audit.json").exists()
    bad = job.model_copy(
        update={"payload": job.payload.model_copy(update={"qc_profile_sha256": "0" * 64})}
    )
    with pytest.raises(ValueError, match="SHA256"):
        _execute_basic_qc(bad, runtime)


def test_actual_worker_uses_causal_gate_level_context_with_measurement_corroboration(runtime):
    obj, target = native_objects(rho=0.88, zdr=8)
    uri = mount(runtime, obj, "current-v3")
    previous = [
        mount(runtime, stamp(obj, f"2026-08-28T{t}:00Z"), t[-2:])
        for t in ("00:25", "00:30", "00:35")
    ]
    warm = _execute_basic_qc(v3_request(obj, uri, previous), runtime)
    fields = open_qc_input(warm.objects).root["sweep_000"]
    assert fields["RFI_TEMPORAL_USED_MASK"][:][target].any()
    assert (fields["TEMPORAL_RFI_VOTE_COUNT"][:][target] == 3).all()
    cold = _execute_basic_qc(v3_request(obj, uri, []), runtime)
    assert not open_qc_input(cold.objects).root["sweep_000"]["RFI_TEMPORAL_USED_MASK"][:].any()
    duplicate = stamp(obj, "2026-08-28T00:35:00Z")
    a = mount(runtime, duplicate, "duplicate-a")
    b = mount(runtime, duplicate, "duplicate-b")
    with pytest.raises(RuntimeError, match="duplicate physical"):
        _execute_basic_qc(v3_request(obj, uri, [a, b]), runtime)


def test_schema_accepts_coordinated_v3_configuration():
    schema = json.loads((ROOT / "configs/schemas/radar-qc-open-source.schema.json").read_text())
    Draft202012Validator.check_schema(schema)
    Draft202012Validator(schema).validate(yaml.safe_load(PROFILE.read_text()))
    p = profile()
    assert p.operational_eligible is False
    assert p.parameters_hash != "357d3085e3cfa40c0577fc9538f3c9e562ba23fa22bc4029751747399e809720"


def test_bounded_residual_cannot_tunnel_through_a_different_object():
    native, _ = make_native()
    ids = np.zeros(native.shape, "uint32")
    ids[10, 100] = 1
    allowed = np.zeros(native.shape, bool)
    allowed[10, 100:108] = True
    domain = np.zeros_like(ids)
    domain[10, 100:108] = 1
    domain[10, 103] = 2
    found = bounded_search(native, ids, allowed, 2000, 0, 32, domain_ids=domain)
    assert found[10, 102] == 1
    assert not found[10, 103:].any()


def test_full_ppi_north_wrap_and_sector_edge_are_different():
    native, _ = make_native()
    ids = np.zeros(native.shape, "uint32")
    ids[-1, 200] = 1
    allowed = np.ones(native.shape, bool)
    found = bounded_search(native, ids, allowed, 0, 2, 32)
    assert found[0, 200] == 1
    sector = replace(native, full_ppi=False)
    found = bounded_search(sector, ids, allowed, 0, 2, 32)
    assert found[0, 200] == 0
    gaps = native.gap_after.copy()
    gaps[100] = True
    ids[:] = 0
    ids[100, 200] = 1
    found = bounded_search(replace(native, gap_after=gaps), ids, allowed, 0, 2, 32)
    assert found[101, 200] == 0


@pytest.mark.parametrize("spacing", [250, 500, 1000])
def test_phase_ramp_with_fixed_physical_gradient_survives_different_gate_spacings(spacing):
    native, _ = make_native(rho=0.99)
    ranges = (np.arange(native.shape[1]) + 1) * spacing
    fields = dict(native.fields)
    fields["PHIDP"] = np.broadcast_to((358 + ranges / 1000 * 15) % 360, native.shape).astype(
        "float32"
    )
    evidence = joint_moment_evidence(replace(native, ranges=ranges, fields=fields), profile())
    assert evidence["RFI_PHASE_LOCAL_AVAILABLE_MASK"].any()
    assert not evidence["RFI_PHASE_ANOMALY_MASK"].any()


@pytest.fixture(scope="module")
def serialized():
    native, _ = make_native()
    obj = synthetic_normalized_fixture(
        native.fields["DBZH"],
        range_m=native.ranges,
        moments={k: v for k, v in native.fields.items() if k != "DBZH"},
    )
    return build_qc_zarr_store(
        obj,
        apply_basic_qc(obj, profile()),
        asset_id="v3-integration",
        normalized_volume_uri="local",
    )


@pytest.mark.parametrize(
    "field,location,value",
    [
        ("RFI_SEARCH_MASK", (0, 0), 1),
        ("RFI_SNR_RELIABLE_MASK", (110, 200), 0),
        ("RFI_WEATHER_BARRIER_MASK", (110, 200), 1),
        ("RFI_PHASE_NOISE_FRACTION", (0, 0), 0.5),
        ("RFI_PHASE_CURVATURE_DEG", (0, 0), 1),
        ("RFI_V3_DECISION_PATH", (110, 200), 0),
    ],
)
def test_serialization_rejects_inconsistent_v3_evidence(serialized, field, location, value):
    store = MemoryStore()
    store.update(serialized)
    root = zarr.open_group(store, mode="a")
    root["sweep_000"][field][location] = value
    with pytest.raises(ValueError):
        validate_qc_zarr_store({str(k): bytes(v) for k, v in store.items()})


def test_audit_cli_preserves_hashes_roi_and_does_not_overwrite(serialized, tmp_path):
    directory = tmp_path / "qc.zarr"
    save_objects(directory, serialized)
    output = tmp_path / "audit.json"
    report = audit_artifact(directory, artifact_sha256(serialized), tmp_path / "direct.json")
    assert report["published"] is False
    assert report["identity"]["qc_pipeline_version"] == "qc-opensource-3.0.0"
    assert report["sweeps"]["sweep_000"]["original_domain_gates"] > 0
    with pytest.raises(ValueError, match="hash"):
        audit_artifact(directory, "0" * 64, tmp_path / "bad.json")
    # CLI atomic publication is independently checked without starting any server.
    import subprocess
    import sys

    command = [
        sys.executable,
        "-m",
        "rainpulse_algo.radar.qc_engine.audit",
        "--qc-zarr",
        str(directory),
        "--sha256",
        artifact_sha256(serialized),
        "--output",
        str(output),
    ]
    first = subprocess.run(command, capture_output=True, text=True)
    assert first.returncode == 0, first.stderr
    second = subprocess.run(command, capture_output=True, text=True)
    assert second.returncode != 0
    assert json.loads(output.read_text())["qc_artifact_sha256"] == artifact_sha256(serialized)


def test_review_exports_separate_v2_v3_arrays_and_no_unlabeled_skill(tmp_path):
    dbzh, moments = field_case()
    obj = synthetic_normalized_fixture(dbzh, moments=moments)
    save_objects(tmp_path / "source.zarr", obj)
    manifest = {
        "schema_version": "1.0",
        "cases": [
            {
                "case_id": "synthetic-v3-io",
                "partition": "development",
                "process_id": "synthetic",
                "normalized_zarr": "source.zarr",
                "input_sha256": artifact_sha256(obj),
                "rfi_objects": True,
                "rfi_multivariate_v3": True,
            }
        ],
    }
    file = tmp_path / "manifest.json"
    file.write_text(json.dumps(manifest))
    report = run_manifest(file, tmp_path / "report.json", inspect_rays=(50,))
    sweep = report["cases"][0]["sweeps"][0]
    assert "rfi_multivariate_v3" in sweep["methods"]
    assert "measurement_metrics" not in sweep["methods"]["rfi_multivariate_v3"]
    variants = sweep["radials"][0]["variants"]
    assert "RFI_V3_BLOCKER_BITS" in variants["rfi_multivariate_v3"]
    assert "RFI_V3_BLOCKER_BITS" not in variants["rfi_objects_v2"]
    assert sweep["methods"]["rfi_multivariate_v3"]["residual_audit"] is not None


def test_offline_candidate_preparation_preserves_causal_selection_and_original_files(tmp_path):
    from rainpulse_algo.radar.qc_engine.experiment import prepare_experiment

    from .test_rfi_objects_v2 import PROFILE as V2_PROFILE

    obj, _ = native_objects()
    client = FakeMinio()
    uri = mount(client, obj, "current")
    original = request(obj, uri, [])
    task = tmp_path / "v2-task.json"
    task.write_text(original.model_dump_json())
    before = task.read_bytes()
    manifest = {
        "schema_version": "rainpulse.qc-task-replay.v1",
        "task": {"path": str(task), "sha256": hashlib.sha256(before).hexdigest()},
        "profile": {
            "path": str(V2_PROFILE),
            "sha256": hashlib.sha256(V2_PROFILE.read_bytes()).hexdigest(),
        },
        "flags": {"path": str(FLAGS), "sha256": hashlib.sha256(FLAGS.read_bytes()).hexdigest()},
        "artifacts": [{"uri": uri, "path": "source.zarr", "sha256": artifact_sha256(obj)}],
    }
    file = tmp_path / "v2.json"
    file.write_text(json.dumps(manifest))
    first = prepare_experiment(file, PROFILE, tmp_path / "v3-a")
    second = prepare_experiment(file, PROFILE, tmp_path / "v3-b")
    assert first == second
    changed = json.loads((tmp_path / "v3-a/task.json").read_text())
    assert changed["occurred_at"] == original.model_dump(mode="json")["occurred_at"]
    assert changed["payload"]["temporal_context"] == []
    assert changed["payload"]["input_uri"] == uri
    assert changed["job_id"] != str(original.job_id)
    assert changed["payload"]["qc_profile"] == profile().profile_version
    assert task.read_bytes() == before
    with pytest.raises(ValueError, match="already exists"):
        prepare_experiment(file, PROFILE, tmp_path / "v3-a")
    save_objects(tmp_path / "source.zarr", obj)
    receipt = replay_task(tmp_path / "v3-a/manifest.json", tmp_path / "replay-v3")
    assert receipt["offline_experiment"]["source_task_id"] == str(original.job_id)
    assert (tmp_path / "replay-v3/residual-audit.json").exists()


def test_audit_refuses_to_modify_input_artifact_and_requires_explicit_roi(serialized, tmp_path):
    folder = tmp_path / "source.zarr"
    save_objects(folder, serialized)
    sha = artifact_sha256(serialized)
    with pytest.raises(ValueError, match="cannot modify"):
        audit_artifact(folder, sha, folder / "new-report.json")
    with pytest.raises(ValueError, match="together"):
        audit_artifact(folder, sha, tmp_path / "report.json", roi_sha256="a" * 64)
    empty = np.zeros((360, 640), "uint8")
    roi = tmp_path / "roi.npz"
    np.savez(roi, sweep_000=empty)
    report = audit_artifact(
        folder,
        sha,
        tmp_path / "empty-roi.json",
        roi_path=roi,
        roi_sha256=hashlib.sha256(roi.read_bytes()).hexdigest(),
    )
    assert report["sweeps"]["sweep_000"]["original_domain_gates"] == 0
    assert report["roi_sha256"] == hashlib.sha256(roi.read_bytes()).hexdigest()


def test_embedded_trusted_weather_island_is_not_consumed_by_rfi_periphery():
    native, _ = make_native()
    fields = {key: value.copy() for key, value in native.fields.items()}
    weather = np.zeros(native.shape, bool)
    weather[110:120, 200:250] = True
    fields["RHOHV"][weather] = 0.99
    fields["PHIDP"][weather] = 20
    fields["ZDR"][weather] = 1
    fields["DBZH"][weather] = 55
    changed = replace(native, fields=fields)
    _, result = run_native(changed)
    interior = np.zeros_like(weather)
    interior[112:118, 210:240] = True
    assert not (result.arrays["RFI_RISK_STATE"][interior] == 3).any()
    assert not result.arrays["RFI_QUARANTINE_MASK"][interior].any()
    assert result.arrays["QPE_ELIGIBLE_MASK"][interior].all()
