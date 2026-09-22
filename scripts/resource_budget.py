"""Generate and validate CPU-pool resource overlays from the active Compose model.

This module never copies expanded secrets into generated files. It accepts the
no-interpolate/no-env-resolution Compose model, preserves algorithm settings,
and produces a generated (not hand-maintained) overlay and a non-secret budget.
"""
from __future__ import annotations

import copy
import hashlib
import json
import math
import re
from typing import Any

MIB = 1024**2
CPU_PROFILES = frozenset({
    "radar-decode-fmt", "radar-qc-basic", "radar-grid-hybrid", "analysis-mosaic-qi",
    "analysis-qpe-basic", "analysis-diagnostics", "nowcast-input", "pysteps-lk",
    "pysteps-lk-v2", "product-builder", "forecast-verification",
})
SECRET_NAME = re.compile(r"PASSWORD|SECRET|TOKEN|ACCESS_KEY|API_KEY|CREDENTIAL", re.I)
NAME = re.compile(r"^[A-Za-z0-9][A-Za-z0-9_.-]{0,95}$")
RESOURCE_ENV = frozenset({
    "RAINPULSE_WORKER_LANE", "RAINPULSE_NATIVE_THREADS", "RAINPULSE_WORKER_MAX_ACK_PENDING",
    "RAINPULSE_ASSET_CACHE_BYTES", "RAINPULSE_ASSET_CACHE_ENTRIES",
    "RAINPULSE_ASSET_CACHE_MAX_OBJECT_BYTES", "RAINPULSE_ASSET_CACHE_TTL_SECONDS",
    "RAINPULSE_ASSET_READ_CONCURRENCY", "RAINPULSE_OBJECT_STORE_MAX_WORKERS",
})


class ResourceError(ValueError):
    pass


def digest_json(value: Any) -> str:
    return hashlib.sha256(json.dumps(value, sort_keys=True, separators=(",", ":"), allow_nan=False).encode()).hexdigest()


def background_name(service: str) -> str:
    if not NAME.fullmatch(service) or not service.endswith("-worker"):
        raise ResourceError("CPU service must have a stable -worker name")
    result = service[:-7] + "-background-worker"
    if not NAME.fullmatch(result):
        raise ResourceError("background service name exceeds the identity limit")
    return result


def worker_services(model: dict, replicas: dict) -> dict[str, dict]:
    result = {}
    for name, service in model.get("services", {}).items():
        env = service.get("environment") or {}
        if not isinstance(env, dict):
            raise ResourceError("use docker compose config --format json to normalize environment")
        if env.get("RAINPULSE_WORKER_PROFILE") in CPU_PROFILES:
            if service.get("profiles") and name not in replicas:
                continue
            if env.get("RAINPULSE_WORKER_LANE", "realtime") == "background":
                continue
            background_name(name)
            result[name] = service
    if not result:
        raise ResourceError("no supported active CPU workers were found")
    return result


def _positive_int(value: Any, name: str, maximum: int) -> int:
    if type(value) is not int or not 1 <= value <= maximum:
        raise ResourceError("invalid resource budget: " + name)
    return value


def pool_budget(raw: dict, lane: str) -> dict:
    if not isinstance(raw, dict):
        raise ResourceError("pool budget must be an object")
    if set(raw) - {"replicas", "cpus", "memory_mib", "native_threads", "cache_mib",
                   "read_concurrency", "max_ack_pending"}:
        raise ResourceError("unknown pool budget field")
    count = _positive_int(raw.get("replicas"), "replicas", 128)
    cpus = raw.get("cpus")
    if type(cpus) not in (int, float) or not math.isfinite(cpus) or not 0.1 <= cpus <= 128:
        raise ResourceError("cpus must be a finite value in [0.1,128]")
    memory = _positive_int(raw.get("memory_mib"), "memory_mib", 1024**2)
    threads = _positive_int(raw.get("native_threads", 1), "native_threads", 64)
    if threads > max(1, math.floor(cpus)):
        raise ResourceError("native thread count exceeds the per-container CPU budget")
    cache = raw.get("cache_mib", 128 if lane == "realtime" else 32)
    if type(cache) is not int or not 0 <= cache <= min(8192, memory // 8):
        raise ResourceError("cache_mib must be at most one eighth of the container memory")
    reads = _positive_int(raw.get("read_concurrency", 4 if lane == "realtime" else 1), "read_concurrency", 32)
    pending = _positive_int(raw.get("max_ack_pending", max(count, 32) if lane == "realtime" else count), "max_ack_pending", 1024)
    if pending < count:
        raise ResourceError("max_ack_pending is a shared consumer limit and must cover all replicas")
    return dict(replicas=count, cpus=float(cpus), memory_mib=memory, native_threads=threads,
                cache_mib=cache, read_concurrency=reads, max_ack_pending=pending)


def resource_environment(pool: dict, lane: str) -> dict[str, str]:
    return {
        "RAINPULSE_WORKER_LANE": lane,
        "RAINPULSE_NATIVE_THREADS": str(pool["native_threads"]),
        "RAINPULSE_WORKER_MAX_ACK_PENDING": str(pool["max_ack_pending"]),
        "RAINPULSE_ASSET_CACHE_BYTES": str(pool["cache_mib"] * MIB),
        "RAINPULSE_ASSET_CACHE_ENTRIES": "4096",
        "RAINPULSE_ASSET_CACHE_MAX_OBJECT_BYTES": str(16 * MIB),
        "RAINPULSE_ASSET_CACHE_TTL_SECONDS": "600",
        "RAINPULSE_ASSET_READ_CONCURRENCY": str(pool["read_concurrency"]),
        "RAINPULSE_OBJECT_STORE_MAX_WORKERS": str(pool["read_concurrency"]),
    }


def _limits(pool: dict, lane: str) -> dict:
    return {"cpus": pool["cpus"], "mem_limit": pool["memory_mib"] * MIB,
            "cpu_shares": 1024 if lane == "realtime" else 128,
            "pids_limit": 256 if lane == "realtime" else 128,
            "deploy": {"replicas": pool["replicas"], "resources": {"limits": {
                "cpus": str(pool["cpus"]), "memory": str(pool["memory_mib"] * MIB),
            }}}, "environment": resource_environment(pool, lane)}


def _assert_unexpanded(service: dict) -> None:
    # Literals in source Compose files must not be propagated into an artifact.
    # No env_file contents are read: --no-env-resolution retains file references.
    for name, value in (service.get("environment") or {}).items():
        if SECRET_NAME.search(name) and value not in (None, ""):
            if not isinstance(value, str) or not re.fullmatch(r"\$\{[^{}]+\}", value):
                raise ResourceError("literal secret in source Compose environment: " + name)
        if isinstance(value, str) and "://" in value and "@" in value and "${" not in value:
            raise ResourceError("literal credential-bearing URL in source environment")
    for key in ("command", "entrypoint", "labels"):
        value = json.dumps(service.get(key, ""))
        if re.search(r"(?:password|secret|token)[=:]", value, re.I) and "${" not in value:
            raise ResourceError("possible literal secret in source " + key)


def build_overlay(model: dict, spec: dict, replicas: dict) -> tuple[dict, dict]:
    if spec.get("schema_version") != 1 or set(spec) - {"schema_version", "cpu_budget", "memory_budget_mib", "services"}:
        raise ResourceError("invalid resource budget contract")
    cpu_budget = spec.get("cpu_budget")
    if type(cpu_budget) not in (int, float) or not math.isfinite(cpu_budget) or cpu_budget <= 0:
        raise ResourceError("cpu_budget must be an explicitly allocated worker budget")
    mem_budget = _positive_int(spec.get("memory_budget_mib"), "memory_budget_mib", 1024**2)
    services = worker_services(model, replicas)
    if not isinstance(spec.get("services"), dict) or set(spec["services"]) != set(services):
        raise ResourceError("budget must cover every active CPU worker, with no unknown services")
    overlay, pools, expected = {"services": {}}, {}, dict(replicas)
    cpu_total, memory_total = 0.0, 0
    for name, source in sorted(services.items()):
        _assert_unexpanded(source)
        entry = spec["services"][name]
        if not isinstance(entry, dict) or set(entry) != {"realtime", "background"}:
            raise ResourceError("each CPU service needs realtime and background budgets")
        if source.get("network_mode") == "host" or source.get("container_name"):
            raise ResourceError("resource pools require bridge networking and scalable service identities")
        if source.get("cpuset") or source.get("devices") or source.get("gpus"):
            raise ResourceError("special device/CPU pinning requires a separate explicit resource design")
        for lane in ("realtime", "background"):
            pool = pool_budget(entry[lane], lane)
            target = name if lane == "realtime" else background_name(name)
            if target != name and target in model["services"]:
                raise ResourceError("background service already exists in the source model")
            if lane == "realtime":
                config = _limits(pool, lane)
            else:
                config = copy.deepcopy(source)
                for key in ("build", "ports", "hostname", "container_name", "profiles", "scale"):
                    config.pop(key, None)
                # Never register the clone under a realtime network alias.
                for network in (config.get("networks") or {}).values():
                    if isinstance(network, dict):
                        network.pop("aliases", None)
                        network.pop("ipv4_address", None)
                        network.pop("ipv6_address", None)
                limits = _limits(pool, lane)
                env = dict(config.get("environment") or {})
                env.update(limits.pop("environment"))
                env["RAINPULSE_WORKER_ID"] = target
                config.update(limits)
                config["environment"] = env
                config.setdefault("labels", {})["org.rainpulse.resource-source"] = name
            overlay["services"][target] = config
            pools[target] = {**pool, "lane": lane, "source_service": name,
                             "profile": source["environment"]["RAINPULSE_WORKER_PROFILE"]}
            expected[target] = pool["replicas"]
            cpu_total += pool["cpus"] * pool["replicas"]
            memory_total += pool["memory_mib"] * pool["replicas"]
    if cpu_total > cpu_budget + 1e-9 or memory_total > mem_budget:
        raise ResourceError("aggregate worker CPU/memory limits exceed the declared host allocation")
    summary = {"schema_version": 1, "pools": pools, "replicas": expected,
               "cpu_total": round(cpu_total, 6), "memory_total_mib": memory_total,
               "cpu_budget": cpu_budget, "memory_budget_mib": mem_budget}
    return overlay, summary


def validate_resolved_pairs(model: dict, summary: dict) -> None:
    """Run on actual docker compose config output before a plan can be activated."""
    for target, pool in summary["pools"].items():
        service = model["services"][target]
        env = service.get("environment") or {}
        if any(str(env.get(k)) != v for k, v in resource_environment(pool, pool["lane"]).items()):
            raise ResourceError("resolved resource environment differs for " + target)
        if float(service.get("cpus", 0)) != pool["cpus"] or int(service.get("mem_limit", 0)) != pool["memory_mib"] * MIB:
            raise ResourceError("resolved CPU/memory limits differ for " + target)
        if pool["lane"] != "background":
            continue
        original = model["services"][pool["source_service"]]
        if service.get("image") != original.get("image") or service.get("volumes", []) != original.get("volumes", []):
            raise ResourceError("background image/mounts differ from the selected realtime source")
        if service.get("ports"):
            raise ResourceError("background pool must not publish duplicate host ports")
        for key in ("entrypoint", "command", "working_dir", "user"):
            if service.get(key) != original.get(key):
                raise ResourceError("background execution identity differs: " + key)
        for key in set(env) | set(original.get("environment") or {}):
            if key in RESOURCE_ENV or key == "RAINPULSE_WORKER_ID":
                continue
            if env.get(key) != original.get("environment", {}).get(key):
                raise ResourceError("background algorithm/environment differs: " + key)


def validate_container(item: dict, pool: dict, image_id: str) -> None:
    if item.get("State", {}).get("Running") is not True or item.get("Image") != image_id:
        raise ResourceError("worker is stopped or uses an unexpected image identity")
    host = item.get("HostConfig") or {}
    nano = host.get("NanoCpus", 0)
    if not nano and host.get("CpuPeriod", 0) > 0 and host.get("CpuQuota", 0) > 0:
        nano = 1e9 * host["CpuQuota"] / host["CpuPeriod"]
    if abs(float(nano) - pool["cpus"] * 1e9) > 1:
        raise ResourceError("Docker CPU quota was not applied")
    if host.get("Memory") != pool["memory_mib"] * MIB:
        raise ResourceError("Docker memory limit was not applied")
    if host.get("CpuShares") != (128 if pool["lane"] == "background" else 1024):
        raise ResourceError("Docker CPU weight differs")
    if host.get("PidsLimit") != (128 if pool["lane"] == "background" else 256):
        raise ResourceError("Docker process/thread count limit differs")
    actual = dict(value.split("=", 1) for value in item.get("Config", {}).get("Env", []) if "=" in value)
    for key, value in resource_environment(pool, pool["lane"]).items():
        if actual.get(key) != value:
            raise ResourceError("running worker resource setting differs: " + key)


def template(model: dict, replicas: dict) -> dict:
    """Use zero for unknown capacity rather than fabricating a deployment budget."""
    services = {}
    for name, service in worker_services(model, replicas).items():
        real = {"replicas": replicas.get(name, 1), "cpus": float(service.get("cpus", 0)),
                "memory_mib": int(service.get("mem_limit", 0)) // MIB,
                "native_threads": 1, "cache_mib": 128, "read_concurrency": 4}
        background = {"replicas": 1, "cpus": 0, "memory_mib": 0,
                      "native_threads": 1, "cache_mib": 32, "read_concurrency": 1}
        services[name] = {"realtime": real, "background": background}
    return {"schema_version": 1, "cpu_budget": 0, "memory_budget_mib": 0, "services": services}
