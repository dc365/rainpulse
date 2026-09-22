#!/usr/bin/env python3
"""Unified local operations entry and fail-closed QC release coordination.

No credentials in manifests or command output. This script does not infer the
105 deployment from repository defaults, modify BDP, pull images, or change any
algorithm threshold. Runtime commands must be explicitly invoked by an operator.
"""
from __future__ import annotations

import argparse
from contextlib import contextmanager
from datetime import datetime, timezone
import fcntl
import hashlib
import json
import os
from pathlib import Path
import re
import subprocess
import sys
import tempfile
import time
from typing import Any
import uuid

BASE_FILES = ["deploy/docker-compose.yaml", "deploy/docker-compose.realtime-shadow.yaml"]
UNIFIED_FILE = "deploy/docker-compose.unified.yaml"
QC_SERVICE = "radar-qc-worker"
QC_BACKGROUND_SERVICE = "radar-qc-background-worker"
ID = re.compile(r"^[a-zA-Z0-9][a-zA-Z0-9_.-]{0,95}$")
SHA = re.compile(r"^[0-9a-f]{64}$")


class OpsError(RuntimeError):
    pass


def utc_now() -> str:
    return datetime.now(timezone.utc).isoformat()


def digest(path: Path) -> str:
    with path.open("rb") as stream:
        return hashlib.file_digest(stream, "sha256").hexdigest()


def read_json(path: Path, maximum: int = 1024 * 1024) -> dict[str, Any]:
    with path.open("rb") as stream:
        data = stream.read(maximum + 1)
    if len(data) > maximum:
        raise OpsError(f"JSON file too large: {path.name}")
    result = json.loads(data)
    if not isinstance(result, dict):
        raise OpsError(f"Expected JSON object: {path.name}")
    return result


def write_json(path: Path, value: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True, mode=0o750)
    if path.is_symlink():
        raise OpsError("Refusing to replace a symlink")
    fd, name = tempfile.mkstemp(prefix=".rainpulse-", dir=path.parent)
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as stream:
            json.dump(value, stream, indent=2, sort_keys=True)
            stream.write("\n")
            stream.flush()
            os.fsync(stream.fileno())
        os.chmod(name, 0o640)
        os.replace(name, path)
        fsync_dir(path.parent)
    finally:
        if os.path.exists(name):
            os.unlink(name)


def fsync_dir(path: Path) -> None:
    fd = os.open(path, os.O_RDONLY)
    try:
        os.fsync(fd)
    finally:
        os.close(fd)


def relative_file(root: Path, value: str) -> str:
    path = Path(value)
    path = path if path.is_absolute() else root / path
    resolved = path.resolve()
    try:
        result = resolved.relative_to(root.resolve()).as_posix()
    except ValueError as exc:
        raise OpsError("Compose files must belong to this deployment root") from exc
    if not resolved.is_file() or "," in result or "\n" in result:
        raise OpsError(f"Not a regular deployment file: {result}")
    return result


class Runner:
    def run(self, argv: list[str], *, stdin: str | None = None, timeout: int = 60) -> str:
        try:
            result = subprocess.run(argv, input=stdin, text=True, capture_output=True,
                                    check=False, timeout=timeout)
        except (OSError, subprocess.TimeoutExpired) as exc:
            raise OpsError(f"Command unavailable/timed out: {argv[0]}") from exc
        if result.returncode:
            # Compose/inspect diagnostics can include expanded environment.
            # Do not echo their raw payloads into release logs.
            raise OpsError(f"Command failed: {' '.join(argv[:2])}, exit={result.returncode}; inspect locally")
        return result.stdout


class Operations:
    def __init__(self, root: Path, runner: Runner | None = None, env_file: str = "deploy/.env"):
        self.root = root.resolve()
        self.runner = runner or Runner()
        self.manifest_path = self.root / "runtime/deploy/active-compose.json"
        self.env_file = str((self.root / env_file).resolve())
        raw_gate = os.getenv("RAINPULSE_RELEASE_GATE_FILE", "runtime/control/release-gate.json")
        self.gate = (self.root / raw_gate).resolve()

    def manifest(self) -> dict[str, Any]:
        try:
            result = read_json(self.manifest_path)
        except FileNotFoundError as exc:
            raise OpsError("Initialize once with init --adopt-running (existing server) or --new-install") from exc
        if result.get("schema_version") != 1 or result.get("mode") not in ("unified", "legacy"):
            raise OpsError("Invalid active deployment manifest")
        if not isinstance(result.get("project_name"), str) or not ID.fullmatch(result["project_name"]):
            raise OpsError("Invalid Compose project identity")
        overrides = result.get("overrides")
        if not isinstance(overrides, list) or len(overrides) > 64:
            raise OpsError("Invalid Compose override list")
        for value in overrides:
            if not isinstance(value, str) or value in BASE_FILES + [UNIFIED_FILE]:
                raise OpsError("Base/unified files must not appear as experimental overrides")
            relative_file(self.root, value)
        counts = result.get("replicas", {})
        if not isinstance(counts, dict) or any(not ID.fullmatch(k) or type(v) is not int or not 1 <= v <= 128 for k, v in counts.items()):
            raise OpsError("Invalid replica inventory")
        return result

    def compose(self, *args: str, legacy: bool = False) -> list[str]:
        manifest = self.manifest()
        if (manifest["mode"] == "legacy") != legacy:
            raise OpsError("Legacy deployment requires an explicit --legacy; unified is the default")
        if manifest.get("resource_profile"):
            from resource_operations import validate_active
            validate_active(self, manifest)
        files = BASE_FILES + ([] if legacy else [UNIFIED_FILE]) + manifest["overrides"]
        if len(set(files)) != len(files):
            raise OpsError("Duplicate Compose files")
        argv = ["docker", "compose", "--project-name", manifest["project_name"],
                "--env-file", self.env_file]
        for path in files:
            argv += ["-f", str(self.root / relative_file(self.root, path))]
        return argv + list(args)

    def init(self, *, adopt: bool, new: bool, overrides: list[str], project: str,
             legacy: bool, qc_replicas: int) -> dict[str, Any]:
        if adopt == new:
            raise OpsError("Choose exactly one of --adopt-running or --new-install")
        if self.manifest_path.exists():
            raise OpsError("Active manifest exists; use set-overrides while admission is paused")
        replicas: dict[str, int] = {QC_SERVICE: qc_replicas}
        if adopt:
            ids = self.runner.run(["docker", "ps", "-q", "--filter", f"label=com.docker.compose.project={project}"]).split()
            if not ids:
                raise OpsError("No running containers for that project; do not guess existing overrides")
            containers = json.loads(self.runner.run(["docker", "inspect", *ids]))
            candidates: set[tuple[str, ...]] = set()
            replicas = {}
            for item in containers:
                labels = item.get("Config", {}).get("Labels", {}) or {}
                service = labels.get("com.docker.compose.service", "")
                if service.endswith("-worker"):
                    replicas[service] = replicas.get(service, 0) + 1
                if service == QC_SERVICE:
                    raw = labels.get("com.docker.compose.project.config_files", "")
                    files = tuple(relative_file(self.root, path) for path in raw.split(",") if path)
                    candidates.add(files)
            if len(candidates) != 1 or QC_SERVICE not in replicas:
                raise OpsError("QC replica deployment labels disagree or QC worker is absent")
            files = next(iter(candidates))
            required = BASE_FILES + ([] if legacy else [UNIFIED_FILE])
            if list(files[:len(required)]) != required:
                raise OpsError("Running QC stack does not match the selected base mode; inspect before adopting")
            observed = list(files[len(required):])
            if overrides and overrides != observed:
                raise OpsError("Explicit overrides disagree with running QC labels")
            overrides = observed
        if not ID.fullmatch(project) or not 1 <= qc_replicas <= 128:
            raise OpsError("Invalid project or replica count")
        overrides = [relative_file(self.root, value) for value in overrides]
        required = BASE_FILES + ([] if legacy else [UNIFIED_FILE])
        for file in required:
            relative_file(self.root, file)
        if any(path in required for path in overrides) or len(set(overrides)) != len(overrides):
            raise OpsError("Invalid duplicate/base override")
        manifest = {"schema_version": 1, "mode": "legacy" if legacy else "unified",
                    "project_name": project, "overrides": overrides, "replicas": replicas,
                    "recorded_at": utc_now()}
        write_json(self.manifest_path, manifest)
        return manifest

    @contextmanager
    def exclusive_gate(self, timeout: float = 30):
        self.gate.parent.mkdir(parents=True, exist_ok=True, mode=0o750)
        lock = self.gate.with_name(self.gate.name + ".lock")
        if lock.is_symlink() or self.gate.is_symlink():
            raise OpsError("Release gate must not be a symlink")
        fd = os.open(lock, os.O_CREAT | os.O_RDWR, 0o640)
        deadline = time.monotonic() + timeout
        try:
            while True:
                try:
                    fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
                    break
                except BlockingIOError:
                    if time.monotonic() >= deadline:
                        raise OpsError("Admitted operations have not drained; release pause was not changed")
                    time.sleep(0.02)
            yield
        finally:
            fcntl.flock(fd, fcntl.LOCK_UN)
            os.close(fd)

    def planner_identity(self) -> dict[str, Any]:
        value = read_json(self.gate.parent / "planner-release.json")
        if value.get("schema_version") != 1 or value.get("gate_path") != str(self.gate):
            raise OpsError("Planner is not using this release gate; do not proceed")
        at = datetime.fromisoformat(value["updated_at"].replace("Z", "+00:00"))
        age = (datetime.now(timezone.utc) - at).total_seconds()
        if not 0 <= age <= 30:
            raise OpsError("Planner identity is stale; resolved Go configuration cannot be verified")
        pid = value.get("pid")
        if type(pid) is not int or pid <= 0:
            raise OpsError("Invalid native Go process identity")
        try:
            os.kill(pid, 0)
        except OSError as exc:
            raise OpsError("Native Go process is not available") from exc
        return value

    def require_paused(self, release_id: str | None = None) -> dict[str, Any]:
        value = read_json(self.gate, 16384)
        if value.get("schema_version") != 1 or value.get("state") != "paused" or not value.get("release_id"):
            raise OpsError("Valid release pause is required")
        if release_id is not None and value["release_id"] != release_id:
            raise OpsError("Release plan does not own the pause")
        return value

    def plan(self, config: Path, flags: Path, image: str, execution_mode: str) -> dict[str, Any]:
        if execution_mode not in ("realtime_shadow", "operational"):
            raise OpsError("Declare the existing execution mode explicitly")
        image_id = self.runner.run(["docker", "image", "inspect", image, "--format", "{{.Id}}"]).strip()
        if not re.fullmatch(r"sha256:[0-9a-f]{64}", image_id):
            raise OpsError("Target image must already exist locally with an immutable image ID")
        manifest = self.manifest()
        groups = self.qc_services(manifest)
        result = {"schema_version": 1, "release_id": str(uuid.uuid4()), "created_at": utc_now(),
                "execution_mode": execution_mode, "image_reference": image, "image_id": image_id,
                "qc_config_sha256": digest(config), "qc_flags_sha256": digest(flags),
                "runtime_sha256": digest(self.root / "algorithms/rainpulse_algo/worker/runtime.py"),
                "qc_replicas": sum(groups.values()), "qc_services": groups}
        if manifest.get("resource_profile"):
            result["resource_profile_sha256"] = manifest["resource_profile"]["profile_sha256"]
        return result

    @staticmethod
    def qc_services(manifest: dict[str, Any]) -> dict[str, int]:
        counts = manifest["replicas"]
        result = {QC_SERVICE: counts.get(QC_SERVICE, 1)}
        if QC_BACKGROUND_SERVICE in counts:
            result[QC_BACKGROUND_SERVICE] = counts[QC_BACKGROUND_SERVICE]
        return result

    def validate_plan(self, plan: dict[str, Any]) -> None:
        if plan.get("schema_version") != 1 or not isinstance(plan.get("release_id"), str) or not ID.fullmatch(plan["release_id"]):
            raise OpsError("Invalid release plan")
        for key in ("qc_config_sha256", "qc_flags_sha256", "runtime_sha256"):
            if not isinstance(plan.get(key), str) or not SHA.fullmatch(plan[key]):
                raise OpsError(f"Invalid release hash: {key}")
        if not isinstance(plan.get("image_id"), str) or not re.fullmatch(r"sha256:[0-9a-f]{64}", plan["image_id"]):
            raise OpsError("Invalid immutable image ID")
        if plan.get("execution_mode") not in ("realtime_shadow", "operational") or type(plan.get("qc_replicas")) is not int or not 1 <= plan["qc_replicas"] <= 256:
            raise OpsError("Invalid release mode/replicas")
        groups = plan.get("qc_services")
        if groups is not None:
            if (not isinstance(groups, dict) or QC_SERVICE not in groups
                    or set(groups) - {QC_SERVICE, QC_BACKGROUND_SERVICE}
                    or any(type(v) is not int or not 1 <= v <= 128 for v in groups.values())
                    or sum(groups.values()) != plan["qc_replicas"]):
                raise OpsError("Invalid QC pool inventory")
        resource_hash = plan.get("resource_profile_sha256")
        if resource_hash is not None and (not isinstance(resource_hash, str) or not SHA.fullmatch(resource_hash)):
            raise OpsError("Invalid resource profile identity")

    def pause(self, plan: dict[str, Any]) -> None:
        self.validate_plan(plan)
        if self.manifest()["mode"] != "unified":
            raise OpsError("Coordinated QC release currently supports native unified mode only")
        # Check the actual native Go gate before claiming to have paused it.
        self.planner_identity()
        with self.exclusive_gate():
            if self.gate.exists():
                self.require_paused(plan["release_id"])
                return
            if self.qc_backlog()["intents"]:
                raise OpsError("Unmaterialized QC batch/regeneration requests remain; pause was NOT changed. Let the current planner submit them, or finish/cancel them before release.")
            write_json(self.gate, {"schema_version": 1, "state": "paused",
                                  "release_id": plan["release_id"], "created_at": utc_now()})

    def set_overrides(self, overrides: list[str]) -> None:
        with self.exclusive_gate():
            self.require_paused()
            manifest = self.manifest()
            if manifest.get("resource_profile"):
                raise OpsError("Resource pools are active; use resources-plan --override and resources-register so background configuration cannot drift")
            paths = [relative_file(self.root, p) for p in overrides]
            if any(p in BASE_FILES + [UNIFIED_FILE] for p in paths) or len(set(paths)) != len(paths):
                raise OpsError("Duplicate/base overrides are not allowed")
            manifest["overrides"] = paths
            manifest["recorded_at"] = utc_now()
            write_json(self.manifest_path, manifest)

    def containers(self) -> list[dict[str, Any]]:
        groups = self.qc_services(self.manifest())
        ids = self.runner.run(self.compose("ps", "--all", "-q", *groups)).split()
        if not ids:
            raise OpsError("No QC workers found")
        result = json.loads(self.runner.run(["docker", "inspect", *ids]))
        if not isinstance(result, list):
            raise OpsError("Invalid container inventory")
        manifest = self.manifest()
        for item in result:
            labels = item.get("Config", {}).get("Labels", {}) or {}
            if labels.get("com.docker.compose.project") != manifest["project_name"] or labels.get("com.docker.compose.service") not in groups:
                raise OpsError("Container belongs to another stack/service")
            if item.get("State", {}).get("Running") is not True:
                raise OpsError("Every QC replica must be running")
        return result

    def qc_backlog(self) -> dict[str, int]:
        # Connection settings stay inside the database container. No passwords
        # are assembled on the host command line or written to the manifest.
        sql = """SELECT json_build_object(
          'jobs', (SELECT count(*) FROM jobs WHERE job_type='radar.qc'
                   AND status NOT IN ('SUCCEEDED','FAILED','CANCELLED','SKIPPED')),
          'outbox', (SELECT count(*) FROM outbox_events
                     WHERE subject IN ('rainpulse.jobs.requested.radar_qc','rainpulse.jobs.requested.background.radar_qc') AND status<>'published'),
          'intents', (SELECT count(*) FROM pipeline_regeneration_requests
                      WHERE preset='forecast_all' AND status='PENDING')
                   + (SELECT count(*) FROM qc_batch_items i
                      JOIN pipeline_regeneration_requests r USING(request_id)
                      WHERE r.preset='radar_qc_only' AND r.status NOT IN ('SUCCEEDED','FAILED')
                        AND i.kind='qc' AND i.job_id IS NULL
                        AND i.status NOT IN ('SUCCEEDED','FAILED','SKIPPED')))::text;"""
        argv = self.compose("exec", "-T", "postgres", "sh", "-c",
                            'exec psql -X -v ON_ERROR_STOP=1 -U "$POSTGRES_USER" -d "$POSTGRES_DB" -At')
        value = json.loads(self.runner.run(argv, stdin=sql))
        if set(value) != {"jobs", "outbox", "intents"} or any(type(v) is not int or v < 0 for v in value.values()):
            raise OpsError("Invalid database backlog evidence")
        return value

    def probe(self, container_id: str) -> dict[str, Any]:
        from resource_operations import probe_worker
        value = probe_worker(self, container_id, require_resources=False)
        if value.get("subject") not in {
            "rainpulse.jobs.requested.radar_qc",
            "rainpulse.jobs.requested.background.radar_qc",
        }:
            raise OpsError("Worker is consuming a different QC subject")
        return value

    def verify(self, plan: dict[str, Any] | None = None) -> dict[str, Any]:
        if plan:
            self.validate_plan(plan)
        self.require_paused(plan["release_id"] if plan else None)
        planner = self.planner_identity()
        backlog = self.qc_backlog()
        if any(backlog.values()):
            raise OpsError("QC jobs/outbox/intents have not drained; keep OLD workers and Go result processing running")
        containers = self.containers()
        manifest = self.manifest()
        groups = plan.get("qc_services") if plan else None
        groups = groups or self.qc_services(manifest)
        expected_count = plan["qc_replicas"] if plan else sum(groups.values())
        if len(containers) != expected_count:
            raise OpsError("QC replica count differs from the declared deployment")
        if plan and plan.get("qc_services"):
            observed = dict.fromkeys(groups, 0)
            for item in containers:
                service = (item.get("Config", {}).get("Labels") or {}).get("com.docker.compose.service")
                if service not in observed:
                    raise OpsError("QC service differs from the declared release pool")
                observed[service] += 1
            if observed != groups:
                raise OpsError("QC pool replica distribution differs")
        if plan:
            if planner.get("loaded_qc_sha256") != plan["qc_config_sha256"] or planner.get("current_qc_sha256") != plan["qc_config_sha256"]:
                raise OpsError("Resolved Go QC configuration differs from release; update BDP/local config and restart Go")
            if planner.get("execution_mode") != plan["execution_mode"]:
                raise OpsError("Execution mode drift; release cannot enable/disable publication implicitly")
        for item in containers:
            evidence = self.probe(item["Id"])
            if evidence["pending"] or evidence["ack_pending"]:
                raise OpsError("QC consumer still has queued/in-flight deliveries; do not switch yet")
            if plan:
                if item.get("Image") != plan["image_id"]:
                    raise OpsError("QC replicas do not all use the frozen immutable image")
                identity = evidence["health"].get("release", {})
                if identity.get("schema_version") != 1 or identity.get("profile") != "radar-qc-basic" or identity.get("config_unchanged") is not True:
                    raise OpsError("QC startup identity absent or files changed after startup")
                for key in ("qc_config_sha256", "qc_flags_sha256", "runtime_sha256"):
                    if identity.get(key) != plan[key]:
                        raise OpsError(f"QC replica identity mismatch: {key}")
                for mount in item.get("Mounts", []):
                    target = str(mount.get("Destination", ""))
                    if target == "/opt/rainpulse" or target == "/opt/rainpulse/algorithms" or target.startswith("/opt/rainpulse/algorithms/"):
                        raise OpsError("Live algorithm-code mounts invalidate immutable image verification")
        if plan:
            resource_reference = manifest.get("resource_profile")
            routing_enabled = planner.get("resource_routing", {}).get("enabled") is True
            if resource_reference or routing_enabled or plan.get("resource_profile_sha256"):
                if (not resource_reference or plan.get("resource_profile_sha256")
                        != resource_reference.get("profile_sha256")):
                    raise OpsError("QC release must bind the active resource profile")
                from resource_operations import check_resources
                check_resources(self, expect_routing="enabled")
        return {"qc_jobs": 0, "qc_outbox": 0, "qc_unmaterialized_intents": 0, "qc_pending": 0, "qc_inflight": 0,
                "verified_replicas": len(containers), "verified_at": utc_now()}

    def resume(self, plan: dict[str, Any]) -> dict[str, Any]:
        with self.exclusive_gate():
            evidence = self.verify(plan)
            record = {"release_id": plan["release_id"], "plan": plan, "evidence": evidence}
            write_json(self.gate.parent / "last-verified-release.json", record)
            self.gate.unlink()
            fsync_dir(self.gate.parent)
            return evidence


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--root", type=Path, default=Path(__file__).resolve().parents[1])
    parser.add_argument("--env-file", default="deploy/.env")
    sub = parser.add_subparsers(dest="command", required=True)
    init = sub.add_parser("init")
    choice = init.add_mutually_exclusive_group(required=True)
    choice.add_argument("--adopt-running", action="store_true")
    choice.add_argument("--new-install", action="store_true")
    init.add_argument("--override", action="append", default=[])
    init.add_argument("--project-name", default="rainpulse")
    init.add_argument("--qc-replicas", type=int, default=1)
    init.add_argument("--legacy", action="store_true")
    for name in ("config-check", "status", "up", "down", "indexes"):
        cmd = sub.add_parser(name)
        cmd.add_argument("--legacy", action="store_true")
        if name == "up":
            cmd.add_argument("--service", action="append", default=[])
    overrides = sub.add_parser("set-overrides")
    overrides.add_argument("--override", action="append", default=[])
    plan = sub.add_parser("release-plan")
    plan.add_argument("--qc-config", type=Path, required=True)
    plan.add_argument("--qc-flags", type=Path, required=True)
    plan.add_argument("--image", required=True)
    plan.add_argument("--execution-mode", choices=["realtime_shadow", "operational"], required=True)
    plan.add_argument("--output", type=Path, required=True)
    plan.add_argument("--resource-plan", type=Path)
    resource = sub.add_parser("resources-template")
    resource.add_argument("--output", type=Path, required=True)
    resource = sub.add_parser("resources-plan")
    resource.add_argument("--budget", type=Path, required=True)
    resource.add_argument("--output", type=Path, required=True)
    resource.add_argument("--override", action="append", default=None)
    sub.add_parser("resources-schema")
    resource = sub.add_parser("resources-register")
    resource.add_argument("--plan", type=Path, required=True)
    resource.add_argument("--release-plan", type=Path, required=True)
    resource = sub.add_parser("resources-check")
    resource.add_argument("--expect-routing", choices=["enabled", "disabled", "any"], default="enabled")
    for name in ("release-pause", "release-check", "release-resume"):
        cmd = sub.add_parser(name)
        cmd.add_argument("--plan", type=Path, required=True)
    sub.add_parser("release-drained")
    args = parser.parse_args(argv)
    ops = Operations(args.root, env_file=args.env_file)
    try:
        command = args.command
        if command == "init":
            result = ops.init(adopt=args.adopt_running, new=args.new_install, overrides=args.override,
                              project=args.project_name, legacy=args.legacy, qc_replicas=args.qc_replicas)
            print(json.dumps(result, indent=2))
        elif command == "set-overrides":
            ops.set_overrides(args.override)
            print("Recorded override order; no containers or algorithms were changed.")
        elif command == "release-plan":
            result = ops.plan(args.qc_config, args.qc_flags, args.image, args.execution_mode)
            if args.resource_plan is not None:
                from resource_operations import bind_release_plan
                result = bind_release_plan(ops, result, args.resource_plan)
            ops.validate_plan(result)
            write_json(args.output, result)
            print(f"Release plan frozen: {result['release_id']}")
        elif command.startswith("resources-"):
            from resource_operations import make_template, make_plan, prepare_schema, register_plan, check_resources
            if command == "resources-template":
                make_template(ops, args.output)
                print("Resource budget template written. Fill measured capacities; zeros deliberately prevent activation.")
            elif command == "resources-plan":
                result = make_plan(ops, args.budget, args.output, args.override)
                print("Resource plan frozen: " + result["profile_sha256"])
            elif command == "resources-schema":
                prepare_schema(ops)
                print("Additive routing columns prepared. No routing flag was enabled.")
            elif command == "resources-register":
                register_plan(ops, args.plan, read_json(args.release_plan))
                print("Resource plan registered under release pause. Containers and native Go were not restarted.")
            else:
                print(json.dumps(check_resources(ops, expect_routing=args.expect_routing), indent=2))
        elif command.startswith("release-"):
            plan = read_json(args.plan) if hasattr(args, "plan") else None
            if command == "release-pause":
                ops.pause(plan)
                print("New planner/API/CLI work paused. Existing workers and result processing continue.")
            elif command == "release-resume":
                print(json.dumps(ops.resume(plan), indent=2))
            else:
                print(json.dumps(ops.verify(plan), indent=2))
        elif command == "indexes":
            sql = (ops.root / "deploy/sql/20260922_planner_indexes.sql").read_text()
            cmd = ops.compose("exec", "-T", "postgres", "sh", "-c",
                              'exec psql -X -v ON_ERROR_STOP=1 -U "$POSTGRES_USER" -d "$POSTGRES_DB"', legacy=args.legacy)
            print(ops.runner.run(cmd, stdin=sql, timeout=1800))
        else:
            ops.runner.run(ops.compose("config", "--quiet", legacy=args.legacy))
            if command == "config-check":
                print("Compose configuration valid; resolved secrets were not printed.")
            elif command == "status":
                print(ops.runner.run(ops.compose("ps", legacy=args.legacy)))
            elif command == "down":
                print(ops.runner.run(ops.compose("down", legacy=args.legacy)))
            elif command == "up":
                services = args.service
                if any(not ID.fullmatch(s) or not args.legacy and s in {"api", "web", "orchestrator", "radar-ingest"} for s in services):
                    raise OpsError("Invalid service or legacy Go container selected in unified mode")
                extra = []
                for service, count in ops.manifest()["replicas"].items():
                    if not services or service in services:
                        extra += ["--scale", f"{service}={count}"]
                print(ops.runner.run(ops.compose("up", "-d", "--wait", *extra, *services, legacy=args.legacy), timeout=300))
                print("Container stack updated. The native rainpulse.service is managed separately; verify it before release-resume.")
        return 0
    except (OpsError, OSError, ValueError, KeyError, TypeError) as exc:
        print(f"rainpulsectl: {exc}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
