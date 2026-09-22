"""Resource subcommands for rainpulsectl. No independent deployment entrypoint."""
from __future__ import annotations

import json
import os
from pathlib import Path
import re
import tempfile
from datetime import datetime, timezone

from resource_budget import (
    CPU_PROFILES, MIB, ResourceError, build_overlay, digest_json, template,
    validate_container, validate_resolved_pairs,
)


def _read(path: Path, maximum: int = 4 * MIB) -> dict:
    with path.open("rb") as stream:
        raw = stream.read(maximum + 1)
    if len(raw) > maximum:
        raise ResourceError("resource JSON exceeds its size limit")
    result = json.loads(raw)
    if not isinstance(result, dict):
        raise ResourceError("resource JSON must be an object")
    return result


def _hash(path: Path) -> str:
    import hashlib
    with path.open("rb") as stream:
        return hashlib.file_digest(stream, "sha256").hexdigest()


def _write(path: Path, value: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    if path.is_symlink():
        raise ResourceError("resource output must not be a symlink")
    fd, name = tempfile.mkstemp(dir=path.parent, prefix=".resource-")
    try:
        with os.fdopen(fd, "w") as stream:
            json.dump(value, stream, indent=2, sort_keys=True, allow_nan=False)
            stream.write("\n"); stream.flush(); os.fsync(stream.fileno())
        os.chmod(name, 0o640)
        os.replace(name, path)
    finally:
        if os.path.exists(name): os.unlink(name)


def _relative(root: Path, path: Path | str) -> str:
    path = Path(path)
    if not path.is_absolute(): path = root / path
    if path.is_symlink(): raise ResourceError("resource path must not be a symlink")
    try:
        return path.resolve().relative_to(root.resolve()).as_posix()
    except ValueError as error:
        raise ResourceError("resource files must remain under the deployment root") from error


def _source_files(ops, overrides: list[str] | None = None) -> list[str]:
    manifest = ops.manifest()
    if manifest["mode"] != "unified":
        raise ResourceError("resource profiles support the native unified deployment only")
    managed = manifest.get("resource_profile", {}).get("overlay", "")
    if overrides is None:
        overrides = [p for p in manifest["overrides"] if p != managed]
    paths = ["deploy/docker-compose.yaml", "deploy/docker-compose.realtime-shadow.yaml",
             "deploy/docker-compose.unified.yaml", *overrides]
    if len(set(paths)) != len(paths): raise ResourceError("duplicate resource source files")
    for path in paths:
        if _relative(ops.root, path) != path or not (ops.root / path).is_file():
            raise ResourceError("invalid resource source file")
    return paths


def _compose(ops, files: list[str], *args: str) -> list[str]:
    manifest = ops.manifest()
    argv = ["docker", "compose", "--project-name", manifest["project_name"], "--env-file", ops.env_file]
    for path in files: argv += ["-f", str(ops.root / path)]
    return argv + list(args)


def load_plan(ops, path: Path | str) -> dict:
    relative = _relative(ops.root, path)
    plan = _read(ops.root / relative)
    digest = plan.get("profile_sha256")
    if plan.get("schema_version") != 1 or digest != digest_json({k: v for k, v in plan.items() if k != "profile_sha256"}):
        raise ResourceError("resource plan identity differs")
    if plan.get("deployment_root") != str(ops.root) or plan.get("project_name") != ops.manifest()["project_name"]:
        raise ResourceError("resource plan belongs to a different deployment")
    overlay = _relative(ops.root, plan["overlay"])
    if _hash(ops.root / overlay) != plan["overlay_sha256"]:
        raise ResourceError("generated resource overlay changed after planning")
    for path, digest in plan["source_hashes"].items():
        path = _relative(ops.root, path)
        if _hash(ops.root / path) != digest:
            raise ResourceError("resource source changed; regenerate the plan before deployment")
    return plan


def validate_active(ops, manifest: dict) -> dict | None:
    reference = manifest.get("resource_profile")
    if reference is None: return None
    plan = load_plan(ops, reference["plan"])
    if (reference.get("profile_sha256") != plan["profile_sha256"]
            or manifest["overrides"] != plan["source_files"][3:] + [plan["overlay"]]
            or manifest["replicas"] != plan["summary"]["replicas"]):
        raise ResourceError("active resource profile/override/replica inventory differs")
    return plan


def make_template(ops, output: Path) -> None:
    files = _source_files(ops)
    model = json.loads(ops.runner.run(_compose(ops, files, "config", "--format", "json")))
    _write(output, template(model, ops.manifest()["replicas"]))


def make_plan(ops, budget: Path, output: Path, overrides: list[str] | None = None) -> dict:
    files = _source_files(ops, overrides)
    # Both switches are mandatory: never fall back to copying resolved secrets.
    raw = json.loads(ops.runner.run(_compose(
        ops, files, "config", "--format", "json", "--no-interpolate", "--no-env-resolution"
    )))
    before = ops.manifest()
    # Ignore the previous generated background replica names when replanning.
    replicas = dict(before["replicas"])
    previous = before.get("resource_profile")
    if previous:
        old = _read(ops.root / previous["plan"])
        for name, pool in old["summary"]["pools"].items():
            if pool["lane"] == "background": replicas.pop(name, None)
    overlay, summary = build_overlay(raw, _read(budget), replicas)
    output_relative = _relative(ops.root, output)
    output = ops.root / output_relative
    overlay_path = output.with_suffix(".compose.json")
    if output.exists() or overlay_path.exists():
        raise ResourceError("use a new resource plan path; frozen plans are not overwritten")
    _write(overlay_path, overlay)
    try:
        overlay_relative = _relative(ops.root, overlay_path)
        resolved = json.loads(ops.runner.run(_compose(
            ops, files + [overlay_relative], "config", "--format", "json"
        )))
        validate_resolved_pairs(resolved, summary)
        host = json.loads(ops.runner.run(["docker", "info", "--format", "{{json .}}"]))
        if summary["cpu_budget"] > host["NCPU"] or summary["memory_budget_mib"] * MIB > host["MemTotal"]:
            raise ResourceError("declared worker allocation exceeds the Docker host capacity")
        images = {}
        for pool in summary["pools"].values():
            source = pool["source_service"]
            if source in images: continue
            image = resolved["services"][source]["image"]
            identity = ops.runner.run(["docker", "image", "inspect", image, "--format", "{{.Id}}"]).strip()
            if not re.fullmatch(r"sha256:[0-9a-f]{64}", identity):
                raise ResourceError("CPU image must already be built and loaded locally")
            images[source] = identity
        plan = {
            "schema_version": 1, "deployment_root": str(ops.root),
            "project_name": before["project_name"], "recorded_at": datetime.now(timezone.utc).isoformat(),
            "before_manifest_sha256": digest_json(before),
            "source_files": files, "source_hashes": {p: _hash(ops.root / p) for p in files},
            "overlay": overlay_relative, "overlay_sha256": _hash(overlay_path),
            "summary": summary, "image_ids": images,
        }
        plan["profile_sha256"] = digest_json(plan)
        _write(output, plan)
        return plan
    except BaseException:
        overlay_path.unlink(missing_ok=True)
        raise


def qc_inventory(summary: dict) -> dict[str, int]:
    return {name: pool["replicas"] for name, pool in summary["pools"].items() if pool["profile"] == "radar-qc-basic"}


def bind_release_plan(ops, release: dict, resource_path: Path) -> dict:
    resource = load_plan(ops, resource_path)
    groups = qc_inventory(resource["summary"])
    if not groups: raise ResourceError("resource plan has no QC workers")
    if any(resource["image_ids"][pool["source_service"]] != release["image_id"]
           for pool in resource["summary"]["pools"].values() if pool["profile"] == "radar-qc-basic"):
        raise ResourceError("resource and QC release image identities differ")
    return {**release, "qc_services": groups, "qc_replicas": sum(groups.values()),
            "resource_profile_sha256": resource["profile_sha256"]}


def _sql(ops, text: str) -> str:
    return ops.runner.run(ops.compose("exec", "-T", "postgres", "sh", "-c",
        'exec psql -X -v ON_ERROR_STOP=1 -U "$POSTGRES_USER" -d "$POSTGRES_DB" -At'), stdin=text)


def prepare_schema(ops) -> None:
    _sql(ops, (ops.root / "deploy/sql/20260922_resource_lanes.sql").read_text())


def register_plan(ops, path: Path, release: dict) -> None:
    plan = load_plan(ops, path)
    ops.validate_plan(release)
    if release.get("resource_profile_sha256") != plan["profile_sha256"]:
        raise ResourceError("QC release plan must explicitly bind this resource plan")
    with ops.exclusive_gate():
        ops.require_paused(release["release_id"])
        before = ops.manifest()
        if digest_json(before) != plan["before_manifest_sha256"]:
            raise ResourceError("deployment inventory changed after resource planning")
        if ops.qc_backlog()["intents"]:
            raise ResourceError("unmaterialized regeneration intents remain")
        backlog = json.loads(_sql(ops, """SELECT json_build_object(
          'jobs',(SELECT count(*) FROM jobs WHERE status NOT IN ('SUCCEEDED','FAILED','SKIPPED','CANCELLED')),
          'outbox',(SELECT count(*) FROM outbox_events WHERE subject LIKE 'rainpulse.jobs.requested.%' AND status<>'published'))::text;"""))
        if set(backlog) != {"jobs", "outbox"} or any(type(v) is not int or v != 0 for v in backlog.values()):
            raise ResourceError("all outstanding compute must drain before changing container budgets; pause ingest through the existing deployment controls")
        # Inspect all current CPU consumers, not just jobs already recorded as
        # terminal. A pending redelivery must not be missed at the boundary.
        ids = ops.runner.run(["docker", "ps", "-aq", "--filter", f'label=com.docker.compose.project={before["project_name"]}']).split()
        items = json.loads(ops.runner.run(["docker", "inspect", *ids])) if ids else []
        for item in items:
            env = dict(e.split("=", 1) for e in item.get("Config", {}).get("Env", []) if "=" in e)
            if env.get("RAINPULSE_WORKER_PROFILE") not in CPU_PROFILES: continue
            evidence = probe_worker(ops, item["Id"], require_resources=False)
            if evidence["pending"] or evidence["ack_pending"]:
                raise ResourceError("CPU consumer still has pending or unacknowledged messages")
        after = {**before, "overrides": plan["source_files"][3:] + [plan["overlay"]],
                 "replicas": plan["summary"]["replicas"],
                 "resource_profile": {"plan": _relative(ops.root, path), "overlay": plan["overlay"],
                                      "profile_sha256": plan["profile_sha256"]}}
        _write(ops.manifest_path, after)


def probe_worker(ops, container: str, *, require_resources: bool = True) -> dict:
    code = '''import asyncio,json,os,urllib.request
import nats
from rainpulse_algo.worker.contracts import JOB_STREAM
from rainpulse_algo.worker.handlers import HANDLERS
async def main():
 handler=HANDLERS[os.environ["RAINPULSE_WORKER_PROFILE"]]
 try:
  from rainpulse_algo.worker.resources import WorkerResourcePolicy,resource_handler
  handler=resource_handler(handler,WorkerResourcePolicy.from_environment())
 except ModuleNotFoundError:
  if os.environ.get("RAINPULSE_WORKER_LANE","realtime")!="realtime": raise
 port=int(os.environ.get("RAINPULSE_WORKER_HEALTH_ADDR","0.0.0.0:8091").rsplit(":",1)[1])
 with urllib.request.urlopen("http://127.0.0.1:%d/healthz"%port,timeout=5) as response: health=json.load(response)
 nc=await nats.connect(servers=[os.environ["RAINPULSE_NATS_URL"]],connect_timeout=5,max_reconnect_attempts=0)
 try:
  info=await nc.jetstream().consumer_info(JOB_STREAM,handler.consumer)
  print(json.dumps({"health":health,"pending":info.num_pending,"ack_pending":info.num_ack_pending,"subject":handler.subject,"consumer":handler.consumer,"filter_subject":info.config.filter_subject,"max_ack_pending":info.config.max_ack_pending}))
 finally: await nc.close()
asyncio.run(main())
'''
    result = json.loads(ops.runner.run(["docker", "exec", container, "python", "-c", code]))
    if result.get("health", {}).get("status") != "ready":
        raise ResourceError("CPU worker is not ready")
    for key in ("pending", "ack_pending"):
        if type(result.get(key)) is not int or result[key] < 0:
            raise ResourceError("invalid CPU consumer backlog evidence")
    if result.get("subject") != result.get("filter_subject"):
        raise ResourceError("CPU consumer filter differs from the running handler")
    if require_resources:
        health = result["health"]
        if health.get("resources", {}).get("schema_version") != 1:
            raise ResourceError("worker image does not expose resource policy")
        if health.get("subject") != result["subject"] or health.get("consumer") != result["consumer"]:
            raise ResourceError("worker/consumer resource route differs")
    return result


def check_resources(ops, *, expect_routing: str = "enabled") -> dict:
    manifest = ops.manifest()
    plan = validate_active(ops, manifest)
    if plan is None: raise ResourceError("no registered resource profile")
    ready = json.loads(_sql(ops, """SELECT json_build_object('resource_schema_ready',
        EXISTS(SELECT 1 FROM information_schema.columns
          WHERE table_schema=current_schema() AND table_name='outbox_events'
          AND column_name='resource_route_frozen' AND data_type='boolean'
          AND is_nullable='NO' AND column_default='false')
        AND EXISTS(SELECT 1 FROM information_schema.columns
          WHERE table_schema=current_schema() AND table_name='outbox_events'
          AND column_name='resource_route_reason' AND data_type='text'))::text;"""))
    if ready != {"resource_schema_ready": True}:
        raise ResourceError("resource routing schema is not ready; run resources-schema first")
    resolved = json.loads(ops.runner.run(ops.compose("config", "--format", "json")))
    validate_resolved_pairs(resolved, plan["summary"])
    planner = ops.planner_identity()
    routing = planner.get("resource_routing", {})
    if routing.get("schema_version") != 1 or type(routing.get("enabled")) is not bool:
        raise ResourceError("native Go does not expose its resolved resource routing mode")
    if expect_routing != "any" and routing["enabled"] != (expect_routing == "enabled"):
        raise ResourceError("resolved native Go resource routing mode differs")
    ids = ops.runner.run(["docker", "ps", "-aq", "--filter", f'label=com.docker.compose.project={manifest["project_name"]}']).split()
    items = json.loads(ops.runner.run(["docker", "inspect", *ids])) if ids else []
    counts, observations = dict.fromkeys(plan["summary"]["pools"], 0), []
    for item in items:
        config = item.get("Config") or {}
        env = dict(e.split("=", 1) for e in config.get("Env", []) if "=" in e)
        if env.get("RAINPULSE_WORKER_PROFILE") not in CPU_PROFILES: continue
        labels = config.get("Labels") or {}
        if labels.get("com.docker.compose.project") != manifest["project_name"]:
            raise ResourceError("worker belongs to another Compose project")
        name = labels.get("com.docker.compose.service")
        if name not in counts: raise ResourceError("unbudgeted CPU worker exists in this project")
        pool = plan["summary"]["pools"][name]
        validate_container(item, pool, plan["image_ids"][pool["source_service"]])
        evidence = probe_worker(ops, item["Id"])
        health = evidence["health"]
        if (health["resources"].get("lane") != pool["lane"]
                or health["resources"].get("native_threads") != pool["native_threads"]
                or evidence["max_ack_pending"] != pool["max_ack_pending"]
                or health.get("asset_cache", {}).get("limit_bytes") != pool["cache_mib"] * MIB
                or health.get("asset_cache", {}).get("limit_inflight") != pool["read_concurrency"]):
            raise ResourceError("runtime resource policy/budget differs for " + name)
        counts[name] += 1
        observations.append({"service": name, "lane": pool["lane"], "pending": evidence["pending"],
                             "ack_pending": evidence["ack_pending"]})
    if any(counts[name] != pool["replicas"] for name, pool in plan["summary"]["pools"].items()):
        raise ResourceError("actual CPU worker replica inventory differs from the frozen budget")
    return {"profile_sha256": plan["profile_sha256"], "routing_enabled": routing["enabled"],
            "verified_containers": sum(counts.values()), "cpu_limit_total": plan["summary"]["cpu_total"],
            "memory_limit_total_mib": plan["summary"]["memory_total_mib"], "consumers": observations}
