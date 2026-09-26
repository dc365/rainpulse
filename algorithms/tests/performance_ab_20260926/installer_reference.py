#!/usr/bin/env python3
"""Baseline/hash-checked A+B installer; no Git writes, deployment or recomputation.

Generate a complete conventional patch against the user's exact checkout with
--check --write-patch PATH. Source edits are AST-located decorators/functions and
unique checked textual replacements, never runtime monkey-patches.
"""

from __future__ import annotations

import argparse
import ast
import difflib
import hashlib
import json
import os
import subprocess
import tempfile
import uuid
from pathlib import Path, PurePosixPath


def sha(data):
    return hashlib.sha256(data).hexdigest()


def blob(data):
    return hashlib.sha1(b"blob " + str(len(data)).encode() + b"\0" + data).hexdigest()


def safe(root, name):
    p = PurePosixPath(name)
    if not name or p.is_absolute() or ".." in p.parts or "\\" in name or str(p) != name:
        raise ValueError("unsafe relative path")
    target = root.joinpath(*p.parts)
    current = root
    for part in p.parts:
        current /= part
        if current.is_symlink():
            raise ValueError("symlink path refused: " + name)
    return target


def functions(tree):
    result = {}

    def walk(nodes, parents=()):
        for node in nodes:
            if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef)):
                q = ".".join((*parents, node.name))
                if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
                    if q in result:
                        raise ValueError("ambiguous function: " + q)
                    result[q] = node
                walk(node.body, (*parents, node.name))

    walk(tree.body)
    return result


def transform(raw, recipe):
    text = raw.decode("utf-8")
    for item in recipe.get("replacements", []):
        old, new = item[:2]
        expected = item[2] if len(item) == 3 else 1
        if text.count(old) != expected:
            raise ValueError("replacement anchor differs: " + old[:100])
        text = text.replace(old, new)
    for name, replacement in recipe.get("functions", {}).items():
        nodes = functions(ast.parse(text))
        if name not in nodes:
            raise ValueError("function absent: " + name)
        node = nodes[name]
        if node.col_offset or node.decorator_list:
            raise ValueError("whole-function replacement requires undecorated top-level function")
        lines = text.splitlines(keepends=True)
        lines[node.lineno - 1 : node.end_lineno] = [replacement.rstrip() + "\n"]
        text = "".join(lines)
    nodes = functions(ast.parse(text))
    inserts = []
    for name, detail in recipe.get("decorators", {}).items():
        if name not in nodes:
            raise ValueError("instrumentation target absent: " + name)
        node = nodes[name]
        label = json.dumps(detail["label"])
        root = ", root=True" if detail.get("root") else ""
        inserts.append((node.lineno - 1, " " * node.col_offset + f"@_perf_timed({label}{root})\n"))
    lines = text.splitlines(keepends=True)
    for pos, line in sorted(inserts, reverse=True):
        lines.insert(pos, line)
    text = "".join(lines)
    aliases = {
        "timed": "_perf_timed",
        "measure": "_perf_measure",
        "observe": "_perf_observe",
        "identify": "_perf_identify",
        "submit_with_context": "_perf_submit",
    }
    needed = [f"{key} as {value}" for key, value in aliases.items() if value in text]
    if needed:
        tree = ast.parse(text)
        after = 0
        for node in tree.body:
            if (
                isinstance(node, ast.Expr)
                and isinstance(node.value, ast.Constant)
                and isinstance(node.value.value, str)
            ) or (isinstance(node, ast.ImportFrom) and node.module == "__future__"):
                after = node.end_lineno
            else:
                break
        lines = text.splitlines(keepends=True)
        lines.insert(
            after, "\nfrom rainpulse_algo.performance import (" + ", ".join(needed) + ")\n"
        )
        text = "".join(lines)
    compile(text, "<performance-ab-output>", "exec")
    return text.encode()


class Package:
    def __init__(self, root):
        self.root = Path(root).resolve()
        self.manifest = json.loads((self.root / "manifest.json").read_text())
        if self.manifest.get("schema") != "rainpulse.performance-ab-delivery-v1":
            raise ValueError("unsupported delivery manifest")
        raw = (self.root / "recipes/changes.json").read_bytes()
        if sha(raw) != self.manifest["recipes_sha256"]:
            raise ValueError("recipe checksum differs")
        self.recipes = json.loads(raw)

    def plan(self, repo):
        repo = Path(repo).resolve()
        head = subprocess.check_output(
            ["git", "-C", str(repo), "rev-parse", "HEAD"], text=True
        ).strip()
        if head != self.manifest["base_commit"]:
            raise ValueError("base commit differs; use a separate exact-baseline worktree")
        changes = {}
        for name, recipe in self.recipes.items():
            target = safe(repo, name)
            raw = target.read_bytes()
            if blob(raw) != recipe["git_blob"]:
                raise ValueError("base source changed: " + name)
            changes[name] = (raw, transform(raw, recipe))
        for name, expected in self.manifest["payloads"].items():
            target = safe(repo, name)
            if target.exists():
                raise ValueError("new path already exists: " + name)
            value = safe(self.root / "payload", name).read_bytes()
            if sha(value) != expected:
                raise ValueError("payload checksum differs: " + name)
            if name.endswith(".py"):
                compile(value, name, "exec")
            changes[name] = (None, value)
        return changes


def write_atomic(path, data, mode=0o644):
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, temporary = tempfile.mkstemp(prefix=".perf-ab-", dir=path.parent)
    try:
        with os.fdopen(fd, "wb") as stream:
            stream.write(data)
            stream.flush()
            os.fsync(stream.fileno())
        os.chmod(temporary, mode)
        os.replace(temporary, path)
    finally:
        if os.path.exists(temporary):
            os.unlink(temporary)


def apply(repo, changes, package_id):
    repo = Path(repo).resolve()
    lock = safe(repo, ".rainpulse-performance-ab.lock")
    lock.mkdir()
    receipt_path = None
    written = []
    try:
        backup = safe(repo, ".rainpulse-performance-ab-backups/" + uuid.uuid4().hex)
        backup.mkdir(parents=True)
        receipt_path = backup / "receipt.json"
        record = {
            "schema": "rainpulse.performance-ab-receipt-v1",
            "package": package_id,
            "repo": str(repo),
            "status": "applying",
            "files": {},
        }
        # Save every original before the first source write.
        for name, (old, new) in changes.items():
            target = safe(repo, name)
            if old is None:
                if target.exists():
                    raise ValueError("new path appeared: " + name)
                mode = 0o644
            else:
                if target.read_bytes() != old:
                    raise ValueError("source changed after preflight: " + name)
                mode = target.stat().st_mode & 0o777
                dest = safe(backup / "original", name)
                write_atomic(dest, old, mode)
            record["files"][name] = {
                "before": None if old is None else sha(old),
                "after": sha(new),
                "mode": mode,
            }
        write_atomic(receipt_path, json.dumps(record, indent=2).encode())
        try:
            for name, (old, new) in changes.items():
                target = safe(repo, name)
                # Recheck immediately before the write, not only at plan time.
                if (
                    old is None
                    and target.exists()
                    or old is not None
                    and target.read_bytes() != old
                ):
                    raise ValueError("concurrent source edit: " + name)
                write_atomic(target, new, record["files"][name]["mode"])
                written.append(name)
            record["status"] = "applied"
            write_atomic(receipt_path, json.dumps(record, indent=2).encode())
        except BaseException:
            for name in reversed(written):
                old = changes[name][0]
                target = safe(repo, name)
                if old is None:
                    target.unlink()
                else:
                    write_atomic(target, old, record["files"][name]["mode"])
            record["status"] = "rolled_back_after_failure"
            write_atomic(receipt_path, json.dumps(record, indent=2).encode())
            raise
        return receipt_path
    finally:
        lock.rmdir()


def rollback(repo, receipt_path):
    repo = Path(repo).resolve()
    receipt_path = Path(receipt_path).resolve()
    record = json.loads(receipt_path.read_text())
    if (
        record.get("schema") != "rainpulse.performance-ab-receipt-v1"
        or record.get("repo") != str(repo)
        or record.get("status") != "applied"
    ):
        raise ValueError("receipt is not an applied transaction for this repo")
    restore = {}
    for name, entry in record["files"].items():
        target = safe(repo, name)
        if not target.is_file() or sha(target.read_bytes()) != entry["after"]:
            raise ValueError("subsequent source edit prevents rollback: " + name)
        data = (
            None
            if entry["before"] is None
            else safe(receipt_path.parent / "original", name).read_bytes()
        )
        if data is not None and sha(data) != entry["before"]:
            raise ValueError("backup checksum differs: " + name)
        restore[name] = data
    lock = safe(repo, ".rainpulse-performance-ab.lock")
    lock.mkdir()
    try:
        for name, data in restore.items():
            if data is None:
                safe(repo, name).unlink()
            else:
                write_atomic(safe(repo, name), data, record["files"][name]["mode"])
        record["status"] = "rolled_back"
        write_atomic(receipt_path, json.dumps(record, indent=2).encode())
    finally:
        lock.rmdir()


def patch(changes):
    result = []
    for name, (old, new) in sorted(changes.items()):
        result.extend(
            difflib.unified_diff(
                [] if old is None else old.decode().splitlines(keepends=True),
                new.decode().splitlines(keepends=True),
                fromfile="/dev/null" if old is None else "a/" + name,
                tofile="b/" + name,
            )
        )
    return "".join(result)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--repo", required=True, type=Path)
    mode = parser.add_mutually_exclusive_group(required=True)
    mode.add_argument("--check", action="store_true")
    mode.add_argument("--apply", action="store_true")
    mode.add_argument("--rollback", type=Path, metavar="RECEIPT")
    parser.add_argument("--write-patch", type=Path)
    args = parser.parse_args()
    if args.rollback:
        rollback(args.repo, args.rollback)
        print("Rollback complete")
        return
    package = Package(Path(__file__).parent)
    changes = package.plan(args.repo)
    if args.write_patch:
        with args.write_patch.open("x", encoding="utf-8") as output:
            output.write(patch(changes))
    print(
        json.dumps(
            {
                "status": "preflight_passed",
                "paths": len(changes),
                "base_commit": package.manifest["base_commit"],
            }
        )
    )
    if args.apply:
        print(
            "Applied. Rollback receipt: "
            + str(apply(args.repo, changes, package.manifest["package_id"]))
        )


if __name__ == "__main__":
    main()
