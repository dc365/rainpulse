"""Operations tests use fake Docker/SQL responses. They never contact a server."""
import copy
import importlib.util
import json
import os
from pathlib import Path
import sys
import time

import pytest

ROOT = Path(__file__).resolve().parents[2]
spec = importlib.util.spec_from_file_location("rainpulsectl", ROOT / "scripts/rainpulsectl.py")
opsmod = importlib.util.module_from_spec(spec)
sys.modules[spec.name] = opsmod
spec.loader.exec_module(opsmod)


class Runner:
    def __init__(self):
        self.calls = []
        self.result = '{"jobs":0,"outbox":0,"intents":0}'
    def run(self, argv, *, stdin=None, timeout=60):
        self.calls.append((argv, stdin))
        if callable(self.result):
            return self.result(argv, stdin)
        return self.result


@pytest.fixture
def setup(tmp_path, monkeypatch):
    monkeypatch.delenv("RAINPULSE_RELEASE_GATE_FILE", raising=False)
    for relative in opsmod.BASE_FILES + [opsmod.UNIFIED_FILE, "deploy/exp.yaml", "deploy/next.yaml"]:
        path = tmp_path / relative
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text("services: {}\n")
    runner = Runner()
    operations = opsmod.Operations(tmp_path, runner)
    operations.init(adopt=False, new=True, overrides=["deploy/exp.yaml"], project="rainpulse-test", legacy=False, qc_replicas=2)
    return operations, runner


def plan():
    return {"schema_version": 1, "release_id": "release-001", "qc_config_sha256": "a"*64,
            "qc_flags_sha256": "b"*64, "runtime_sha256": "c"*64,
            "image_id": "sha256:"+"d"*64, "execution_mode": "realtime_shadow", "qc_replicas": 2}


def publish_identity(operations, **changes):
    item = {"schema_version": 1, "gate_path": str(operations.gate), "pid": os.getpid(),
            "updated_at": opsmod.utc_now(), "loaded_qc_sha256": "a"*64,
            "current_qc_sha256": "a"*64, "execution_mode": "realtime_shadow"}
    item.update(changes)
    opsmod.write_json(operations.gate.parent/"planner-release.json", item)


def test_default_stack_order_and_replicas(setup):
    operations, _ = setup
    argv = operations.compose("config", "--quiet")
    files = [argv[i+1] for i, value in enumerate(argv[:-1]) if value == "-f"]
    assert files == [str(operations.root/p) for p in opsmod.BASE_FILES+[opsmod.UNIFIED_FILE, "deploy/exp.yaml"]]
    assert operations.manifest()["replicas"][opsmod.QC_SERVICE] == 2
    with pytest.raises(opsmod.OpsError):
        operations.compose("up", legacy=True)


def test_legacy_is_explicit(setup):
    operations, _ = setup
    manifest = operations.manifest()
    manifest["mode"] = "legacy"
    opsmod.write_json(operations.manifest_path, manifest)
    with pytest.raises(opsmod.OpsError):
        operations.compose("up")
    assert not any("unified" in arg for arg in operations.compose("config", "--quiet", legacy=True))


def test_cannot_reset_adopted_manifest(setup):
    operations, _ = setup
    with pytest.raises(opsmod.OpsError, match="exists"):
        operations.init(adopt=False, new=True, overrides=[], project="rainpulse", legacy=False, qc_replicas=1)


def test_override_change_needs_pause_and_rejects_escape(setup):
    operations, _ = setup
    with pytest.raises(FileNotFoundError):
        operations.set_overrides(["deploy/next.yaml"])
    publish_identity(operations)
    operations.pause(plan())
    operations.set_overrides(["deploy/next.yaml"])
    assert operations.manifest()["overrides"] == ["deploy/next.yaml"]
    with pytest.raises(opsmod.OpsError):
        operations.set_overrides([opsmod.UNIFIED_FILE])
    with pytest.raises(opsmod.OpsError):
        operations.set_overrides(["../../outside.yaml"])


def test_pause_verifies_actual_go_gate_and_is_idempotent(setup):
    operations, _ = setup
    publish_identity(operations, gate_path="/wrong/gate.json")
    with pytest.raises(opsmod.OpsError):
        operations.pause(plan())
    assert not operations.gate.exists()
    publish_identity(operations)
    operations.pause(plan())
    original = operations.gate.read_bytes()
    operations.pause(plan())
    assert operations.gate.read_bytes() == original
    other = plan(); other["release_id"] = "different"
    with pytest.raises(opsmod.OpsError):
        operations.pause(other)


@pytest.mark.parametrize("changes", [
    {"updated_at": "2000-01-01T00:00:00Z"}, {"pid": 0}, {"schema_version": 2},
])
def test_bad_identity_never_pauses(setup, changes):
    operations, _ = setup
    publish_identity(operations, **changes)
    with pytest.raises(opsmod.OpsError):
        operations.pause(plan())
    assert not operations.gate.exists()


def ready_mock(operations, monkeypatch):
    publish_identity(operations)
    operations.pause(plan())
    containers = [{"Id": f"worker-{i}", "Image": plan()["image_id"], "Mounts": []} for i in range(2)]
    backlog = {"jobs": 0, "outbox": 0, "intents": 0}
    identity = {"schema_version": 1, "profile": "radar-qc-basic", "config_unchanged": True,
                **{key: plan()[key] for key in ("qc_config_sha256", "qc_flags_sha256", "runtime_sha256")}}
    evidence = {"pending": 0, "ack_pending": 0, "health": {"status": "ready", "release": identity}}
    monkeypatch.setattr(operations, "containers", lambda: containers)
    monkeypatch.setattr(operations, "qc_backlog", lambda: backlog)
    monkeypatch.setattr(operations, "probe", lambda _: evidence)
    return containers, backlog, evidence


def test_resume_only_after_all_evidence_matches(setup, monkeypatch):
    operations, _ = setup
    ready_mock(operations, monkeypatch)
    result = operations.resume(plan())
    assert result["verified_replicas"] == 2
    assert not operations.gate.exists()
    receipt = json.loads((operations.gate.parent/"last-verified-release.json").read_text())
    assert receipt["plan"]["qc_config_sha256"] == "a"*64


@pytest.mark.parametrize("failure", ["job", "outbox", "pending", "inflight", "replicas", "image", "config", "flags", "runtime", "changed", "source_mount", "go_config", "mode"])
def test_failed_release_keeps_pause(setup, monkeypatch, failure):
    operations, _ = setup
    containers, backlog, evidence = ready_mock(operations, monkeypatch)
    identity = evidence["health"]["release"]
    if failure == "job": backlog["jobs"] = 1
    elif failure == "outbox": backlog["outbox"] = 1
    elif failure == "pending": evidence["pending"] = 1
    elif failure == "inflight": evidence["ack_pending"] = 1
    elif failure == "replicas": containers.pop()
    elif failure == "image": containers[1]["Image"] = "sha256:"+"0"*64
    elif failure in ("config", "flags", "runtime"):
        key = {"config": "qc_config_sha256", "flags": "qc_flags_sha256", "runtime": "runtime_sha256"}[failure]
        identity[key] = "0"*64
    elif failure == "changed": identity["config_unchanged"] = False
    elif failure == "source_mount": containers[1]["Mounts"] = [{"Destination": "/opt/rainpulse/algorithms"}]
    elif failure == "go_config": publish_identity(operations, current_qc_sha256="0"*64)
    elif failure == "mode": publish_identity(operations, execution_mode="operational")
    with pytest.raises(opsmod.OpsError):
        operations.resume(plan())
    assert operations.require_paused()["release_id"] == plan()["release_id"]
    assert not (operations.gate.parent/"last-verified-release.json").exists()


def test_backlog_query_uses_domain_job_type_not_subject_suffix(setup):
    operations, runner = setup
    runner.result = '{"jobs":0,"outbox":0,"intents":0}'
    assert operations.qc_backlog() == {"jobs": 0, "outbox": 0, "intents": 0}
    argv, sql = runner.calls[-1]
    assert "job_type='radar.qc'" in sql
    assert "rainpulse.jobs.requested.radar_qc" in sql
    assert "PGPASSWORD=" not in " ".join(argv)
    assert sql.lstrip().startswith("SELECT")


def test_adoption_preserves_actual_overlay_order_and_scale(setup):
    operations, runner = setup
    operations.manifest_path.unlink()
    files = ",".join(str(operations.root/p) for p in opsmod.BASE_FILES+[opsmod.UNIFIED_FILE,"deploy/exp.yaml","deploy/next.yaml"])
    items = [{"Config": {"Labels": {"com.docker.compose.service": opsmod.QC_SERVICE,
              "com.docker.compose.project.config_files": files}}} for _ in range(4)]
    runner.result = lambda argv, _: "a b c d" if argv[1] == "ps" else json.dumps(items)
    manifest = operations.init(adopt=True,new=False,overrides=[],project="rainpulse-test",legacy=False,qc_replicas=1)
    assert manifest["replicas"][opsmod.QC_SERVICE] == 4
    assert manifest["overrides"] == ["deploy/exp.yaml", "deploy/next.yaml"]


def test_adoption_rejects_mixed_labels(setup):
    operations, runner = setup
    operations.manifest_path.unlink()
    files = ",".join(str(operations.root/p) for p in opsmod.BASE_FILES+[opsmod.UNIFIED_FILE])
    items = [{"Config": {"Labels": {"com.docker.compose.service": opsmod.QC_SERVICE,
              "com.docker.compose.project.config_files": files+suffix}}} for suffix in ("",",deploy/next.yaml")]
    runner.result = lambda argv, _: "a b" if argv[1] == "ps" else json.dumps(items)
    with pytest.raises(opsmod.OpsError):
        operations.init(adopt=True,new=False,overrides=[],project="rainpulse-test",legacy=False,qc_replicas=1)
    assert not operations.manifest_path.exists()


def test_container_inventory_rejects_wrong_project(setup):
    operations, runner = setup
    items=[{"State":{"Running":True}, "Config":{"Labels":{
        "com.docker.compose.project":"another", "com.docker.compose.service":opsmod.QC_SERVICE}}}]
    runner.result=lambda argv,_: json.dumps(items) if argv[1]=="inspect" else "container-1"
    with pytest.raises(opsmod.OpsError):
        operations.containers()


def test_up_preserves_replicas_and_refuses_old_go(setup, monkeypatch):
    operations, runner = setup
    monkeypatch.setattr(opsmod, "Runner", lambda: runner)
    assert opsmod.main(["--root",str(operations.root),"up","--service",opsmod.QC_SERVICE]) == 0
    argv, _ = runner.calls[-1]
    assert "radar-qc-worker=2" in argv and "--wait" in argv
    assert opsmod.main(["--root",str(operations.root),"up","--service","orchestrator"]) == 1


def test_invalid_plan_is_rejected(setup):
    operations, _ = setup
    p=plan(); p["qc_replicas"]=True
    with pytest.raises(opsmod.OpsError): operations.validate_plan(p)


def test_unmaterialized_qc_requests_prevent_pause(setup):
    operations, runner = setup
    publish_identity(operations)
    runner.result = '{"jobs":0,"outbox":0,"intents":1}'
    with pytest.raises(opsmod.OpsError, match="Unmaterialized"):
        operations.pause(plan())
    assert not operations.gate.exists()
    assert "pipeline_regeneration_requests" in runner.calls[-1][1]
    assert "i.job_id IS NULL" in runner.calls[-1][1]
