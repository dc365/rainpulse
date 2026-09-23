#!/usr/bin/env python3
"""Host-side inode inspection and explicitly confirmed candidate-only S3 purge.

No filesystem deletion, no Docker prune, no bucket-wide lifecycle changes.
Default commands are read-only. Purge needs its own scoped S3 credentials.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import os
import re
import socket
import time
from datetime import datetime, timezone
from pathlib import Path
from urllib.error import HTTPError
from urllib.parse import urlsplit
from urllib.request import Request, urlopen

UUID = r"[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}"
SHA = re.compile(r"[0-9a-f]{64}\Z")
KINDS = {"qc": "qc.zarr", "render": "review-images", "diagnostics": "diagnostics"}
MAX_OBJECTS = 100_000
MAX_FILE_BYTES = 16 * 1024**2


def write_json(path: Path, value, *, replace=False):
    path = path.resolve()
    if path.exists() and not replace:
        raise ValueError("output already exists; choose a new filename")
    path.parent.mkdir(parents=True, exist_ok=True)
    temp = path.with_name(path.name + ".tmp-" + str(os.getpid()))
    with temp.open("x", encoding="utf-8") as f:
        os.chmod(temp, 0o600)
        json.dump(value, f, ensure_ascii=False, indent=2, allow_nan=False)
        f.write("\n"); f.flush(); os.fsync(f.fileno())
    os.replace(temp, path)


def load_json(path):
    with Path(path).open("rb") as f:
        raw = f.read(MAX_FILE_BYTES + 1)
    if len(raw) > MAX_FILE_BYTES:
        raise ValueError("plan exceeds size limit")
    return json.loads(raw)


class Control:
    def __init__(self):
        self.base = os.environ["RAINPULSE_OPS_CONTROL_URL"].rstrip("/")
        self.token = os.environ["RAINPULSE_ADMIN_TOKEN"]
        parsed = urlsplit(self.base)
        if parsed.scheme not in {"http", "https"} or not parsed.netloc or parsed.username or parsed.query or parsed.fragment:
            raise ValueError("invalid control URL")

    def call(self, path, value=None):
        request = Request(self.base + "/api/v1/admin/ops" + path,
            data=None if value is None else json.dumps(value, ensure_ascii=False, allow_nan=False).encode(),
            headers={"Authorization": "Bearer " + self.token, "Content-Type": "application/json"})
        try:
            with urlopen(request, timeout=130) as r:
                data = r.read(MAX_FILE_BYTES + 1)
        except HTTPError as e:
            raise RuntimeError(f"management request refused: HTTP {e.code}") from None
        if len(data) > MAX_FILE_BYTES:
            raise ValueError("management response exceeds size limit")
        return json.loads(data)


def inspect_filesystem(path: Path, report_id: str, label: str):
    root = path.resolve(strict=True)
    if not root.is_dir():
        raise ValueError("inspect path must be an existing directory on the host")
    st, fs = root.stat(), os.statvfs(root)
    return {"id": report_id, "label": label, "host": socket.gethostname(), "path": str(root),
        "sampled_at": datetime.now(timezone.utc).isoformat(), "device": str(st.st_dev),
        "total_bytes": fs.f_blocks * fs.f_frsize, "available_bytes": fs.f_bavail * fs.f_frsize,
        "inodes_total": fs.f_files, "inodes_free": fs.f_ffree,
        "inode_used_percent": 100 * (1 - fs.f_ffree / fs.f_files) if fs.f_files else None}


def sample_entries(root: Path, max_entries=100_000, seconds=10.0):
    """Optional bounded read-only inventory, not an exact inode census.

    Never follows symlinks or crosses a filesystem. No traversal on page load.
    """
    if not 1 <= max_entries <= 200_000 or not 0 < seconds <= 60:
        raise ValueError("invalid directory scan budget")
    root = root.resolve(strict=True); device = root.stat().st_dev
    deadline = time.monotonic() + seconds; stack = [(root, "(root)")]
    counts = {}; visited = 0; errors = 0
    while stack and visited < max_entries and time.monotonic() < deadline:
        directory, group = stack.pop()
        try:
            with os.scandir(directory) as entries:
                for entry in entries:
                    if visited >= max_entries or time.monotonic() >= deadline:
                        return {"entries": counts, "sampled_entries": visited, "errors": errors, "complete": False}
                    visited += 1
                    counts[group] = counts.get(group, 0) + 1
                    if entry.is_dir(follow_symlinks=False) and entry.stat(follow_symlinks=False).st_dev == device:
                        stack.append((Path(entry.path), entry.name if directory == root else group))
        except OSError:
            errors += 1
    return {"entries": counts, "sampled_entries": visited, "errors": errors,
            "complete": not stack and errors == 0 and visited < max_entries and time.monotonic() < deadline}


def endpoint_identity(value):
    u = urlsplit(value)
    if u.scheme not in {"http", "https"} or not u.netloc or u.username or u.path not in {"", "/"} or u.query or u.fragment:
        raise ValueError("object endpoint must be an HTTP(S) origin without credentials")
    return u.scheme + "://" + u.netloc.lower()


def client_from_env(expected_endpoint, *, deleting=False):
    from minio import Minio
    import urllib3

    endpoint = endpoint_identity(os.environ["RAINPULSE_OBJECT_STORE_ENDPOINT"])
    if endpoint != endpoint_identity(expected_endpoint):
        raise ValueError("object-store endpoint differs from the server's frozen cleanup plan")
    prefix = "RAINPULSE_STORAGE_DELETE_" if deleting else "RAINPULSE_OBJECT_STORE_"
    access, secret = os.environ[prefix + "ACCESS_KEY"], os.environ[prefix + "SECRET_KEY"]
    u = urlsplit(endpoint)
    return Minio(u.netloc, access_key=access, secret_key=secret, secure=u.scheme == "https",
        http_client=urllib3.PoolManager(timeout=urllib3.Timeout(connect=5, read=30), retries=2))


def validate_plan(plan):
    if plan.get("scope") != "managed_candidates_only" or not re.fullmatch(UUID, plan.get("id", "")) or not SHA.fullmatch(plan.get("digest", "")):
        raise ValueError("invalid candidate cleanup plan identity")
    endpoint_identity(plan.get("object_store_endpoint", ""))
    targets = plan.get("targets")
    if not isinstance(targets, list) or not 1 <= len(targets) <= 20:
        raise ValueError("cleanup plan must contain 1..20 runs")
    seen = set(); count = 0
    for run in targets:
        if not re.fullmatch(UUID, run.get("run_id", "")) or not isinstance(run.get("attempts"), list) or not run["attempts"]:
            raise ValueError("invalid run identity or empty attempt inventory")
        for a in run["attempts"]:
            count += 1
            if (a.get("kind") not in KINDS or not re.fullmatch(UUID, a.get("task_id", ""))
                    or not re.fullmatch(UUID, a.get("id", "")) or count > 40960):
                raise ValueError("invalid attempt identity")
            expected = f"s3://rainpulse/operations/{run['run_id']}/{a['task_id']}/attempts/{a['id']}/"
            if a.get("prefix") != expected or expected in seen:
                raise ValueError("cleanup prefix is not the exact registered candidate attempt")
            if a.get("marker_sha256") and not SHA.fullmatch(a["marker_sha256"]):
                raise ValueError("invalid marker identity")
            seen.add(expected)
    return plan


def read_marker(client, key):
    response = client.get_object("rainpulse", key)
    try:
        data = response.read(MAX_FILE_BYTES + 1)
    finally:
        response.close(); response.release_conn()
    if len(data) > MAX_FILE_BYTES:
        raise ValueError("candidate marker too large")
    return data


def inventory_attempt(client, run, a, *, resume=False, budget=MAX_OBJECTS):
    prefix = urlsplit(a["prefix"]).path.lstrip("/")
    artifact = KINDS[a["kind"]]
    marker_key = prefix + artifact + "/_SUCCESS.json"
    mode = getattr(client.get_bucket_versioning("rainpulse"), "status", None)
    if mode not in {None, "", "Enabled", "Suspended"}:
        raise ValueError("unknown bucket versioning mode")
    items = []; current_marker = False
    for o in client.list_objects("rainpulse", prefix=prefix, recursive=True, include_version=True):
        key, version = o.object_name, o.version_id
        # Detect unexpectedly returned keys before issuing any DELETE operation.
        if not isinstance(key, str) or not key.startswith(prefix):
            raise ValueError("S3 listing escaped the frozen attempt prefix")
        suffix = key[len(prefix):]
        if ("\\" in suffix or "\0" in suffix or any(p in {"", ".", ".."} for p in suffix.split("/"))
                or not (suffix == artifact + "/_SUCCESS.json" or re.fullmatch(re.escape(artifact) + r"/_objects/[0-9a-f]{64}/.+", suffix))):
            raise ValueError("unexpected object layout; refusing this entire cleanup plan")
        if mode in {"Enabled", "Suspended"} and not version:
            raise ValueError("versioned listing omitted a version ID; refusing marker-only deletion")
        if key == marker_key and not getattr(o, "is_delete_marker", False) and getattr(o, "is_latest", True):
            current_marker = True
        items.append({"key": key, "version_id": version, "size_bytes": int(o.size or 0),
                      "delete_marker": bool(getattr(o, "is_delete_marker", False))})
        if len(items) > budget:
            raise ValueError("object version inventory exceeds budget; use a smaller cleanup batch")
    if current_marker:
        raw = read_marker(client, marker_key); m = json.loads(raw)
        event = m.get("completion_event", {}); ops = event.get("payload", {}).get("diagnostics", {}).get("operations", {})
        if event.get("run_id") != run["run_id"] or event.get("job_id") != a["task_id"] or ops.get("attempt_id") != a["id"] or ops.get("candidate_only") is not True:
            raise ValueError("marker ownership differs from the frozen candidate attempt")
        if not resume and a.get("marker_sha256") and hashlib.sha256(raw).hexdigest() != a["marker_sha256"]:
            raise ValueError("candidate marker changed; create a new cleanup preview")
    elif a.get("marker_sha256") and not resume:
        raise ValueError("known candidate marker is absent or hidden by a delete marker; investigate first")
    # Immutable data first, success markers last; preserve a recoverable marker
    # until the last step, while logical retirement already prevents new readers.
    items.sort(key=lambda v: (v["key"] == marker_key, v["key"], v["version_id"] or ""))
    return items


def verify_local_remote(local, control):
    validate_plan(local)
    remote = control.call("/storage/cleanup/plans/" + local["id"])
    if remote.get("plan") != local:
        raise ValueError("local plan differs from the authenticated server record")
    if remote.get("state") == "COMPLETE":
        raise ValueError("cleanup is already complete")
    return remote["state"]


def purge(plan, control, client, *, confirm, workers_stopped, receipt_path: Path, max_objects=MAX_OBJECTS):
    """DELETE calls exist only here, after server retirement and explicit consent."""
    if not workers_stopped or confirm != plan.get("digest"):
        raise ValueError("confirm the plan digest and stop managed workers before purge")
    if not 1 <= max_objects <= MAX_OBJECTS:
        raise ValueError("invalid object version budget")
    state = verify_local_remote(plan, control)
    inventories = []; remaining = max_objects
    for run in plan["targets"]:
        for a in run["attempts"]:
            items = inventory_attempt(client, run, a, resume=state in {"ERROR", "DELETING"}, budget=remaining)
            remaining -= len(items); inventories.append((a, items))
    receipt = {"digest": plan["digest"], "prefixes": [
        {"prefix": a["prefix"], "deleted_versions": 0, "empty": False, "error": "not_attempted"}
        for a, _ in inventories]}
    if receipt_path.exists() and state in {"ERROR", "DELETING"}:
        previous = load_json(receipt_path)
        if previous.get("plan_id") != plan["id"] or previous.get("receipt", {}).get("digest") != plan["digest"]:
            raise ValueError("existing receipt belongs to another plan")
        totals = {r["prefix"]: r["deleted_versions"] for r in previous["receipt"]["prefixes"]}
        for row in receipt["prefixes"]:
            count = totals.get(row["prefix"], 0)
            if type(count) is not int or count < 0:
                raise ValueError("invalid previous receipt count")
            row["deleted_versions"] = count
    # Ensure audit storage is writable BEFORE logically retiring any candidate.
    write_json(receipt_path, {"plan_id": plan["id"], "receipt": receipt}, replace=state in {"ERROR", "DELETING"})
    begun = control.call("/storage/cleanup/plans/" + plan["id"] + "/begin", {"digest": confirm})
    if begun != plan:
        raise ValueError("server returned a different retirement plan")
    for index, (a, items) in enumerate(inventories):
        row = receipt["prefixes"][index]
        try:
            for item in items:
                # Exact versions, including delete markers. No recursive bucket
                # deletion and no bypass_governance/retention override.
                client.remove_object("rainpulse", item["key"], version_id=item["version_id"])
                row["deleted_versions"] += 1
            prefix = urlsplit(a["prefix"]).path.lstrip("/")
            row["empty"] = next(iter(client.list_objects("rainpulse", prefix=prefix,
                recursive=True, include_version=True)), None) is None
            row["error"] = "" if row["empty"] else "objects_remain_after_delete"
        except Exception as e:
            # Do not write endpoints or credentials from arbitrary SDK errors.
            row["error"] = type(e).__name__
        write_json(receipt_path, {"plan_id": plan["id"], "receipt": receipt}, replace=True)
    # Error receipts release the global maintenance gate, but targets stay
    # retired and cannot be used by a new plan. Resume the same cleanup ID.
    control.call("/storage/cleanup/plans/" + plan["id"] + "/finish", receipt)
    return receipt


def main():
    p = argparse.ArgumentParser(description=__doc__); sub = p.add_subparsers(dest="command", required=True)
    x = sub.add_parser("inspect"); x.add_argument("--path", type=Path, required=True); x.add_argument("--id", required=True)
    x.add_argument("--label", required=True); x.add_argument("--output", type=Path, required=True); x.add_argument("--upload", action="store_true")
    x.add_argument("--sample-directories", action="store_true")
    x = sub.add_parser("plan"); x.add_argument("--keep-latest", type=int, default=1); x.add_argument("--minimum-age-hours", type=int, default=24)
    x.add_argument("--limit", type=int, default=5); x.add_argument("--output", type=Path, required=True)
    x = sub.add_parser("inventory"); x.add_argument("--plan", type=Path, required=True); x.add_argument("--output", type=Path, required=True)
    x = sub.add_parser("purge"); x.add_argument("--plan", type=Path, required=True); x.add_argument("--confirm", required=True)
    x.add_argument("--workers-stopped", action="store_true"); x.add_argument("--receipt", type=Path, required=True)
    args = p.parse_args()
    if args.command == "inspect":
        report = inspect_filesystem(args.path, args.id, args.label)
        value = {"report": report}
        if args.sample_directories: value["directory_sample"] = sample_entries(args.path)
        write_json(args.output, value, replace=True)
        if args.upload: Control().call("/storage/report", report)
        print(json.dumps(report, ensure_ascii=False)); return
    control = Control()
    if args.command == "plan":
        value = control.call("/storage/cleanup/plans", {"keep_latest": args.keep_latest,
            "minimum_age_hours": args.minimum_age_hours, "limit": args.limit})
        write_json(args.output, value); print(f"Preview only: {len(value['targets'])} runs; digest {value['digest']}"); return
    plan = load_json(args.plan); state = verify_local_remote(plan, control)
    client = client_from_env(plan["object_store_endpoint"], deleting=args.command == "purge")
    if args.command == "inventory":
        rows = []; remaining = MAX_OBJECTS
        for run in plan["targets"]:
            for a in run["attempts"]:
                items = inventory_attempt(client, run, a, resume=state in {"ERROR", "DELETING"}, budget=remaining)
                remaining -= len(items); rows.append({"prefix": a["prefix"], "versions": len(items),
                    "object_bytes": sum(v["size_bytes"] for v in items), "delete_markers": sum(v["delete_marker"] for v in items)})
        write_json(args.output, {"plan_id": plan["id"], "inventory": rows,
            "inode_estimate": None, "note": "Object versions are NOT a physical inode count."}); return
    receipt = purge(plan, control, client, confirm=args.confirm, workers_stopped=args.workers_stopped, receipt_path=args.receipt)
    if any(not x["empty"] or x["error"] for x in receipt["prefixes"]):
        raise RuntimeError("partial purge; retired metadata retained; resume the same plan after investigation")
    print("Candidate versions deleted and prefixes verified empty. Re-sample host inode usage; business data was not touched.")


if __name__ == "__main__":
    try:
        main()
    except Exception as e:
        detail = str(e) if isinstance(e, (ValueError, RuntimeError, KeyError)) else "check local receipt and service logs"
        raise SystemExit(f"storage operation stopped: {type(e).__name__}: {detail}") from None
