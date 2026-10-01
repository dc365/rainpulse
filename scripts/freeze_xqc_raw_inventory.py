#!/usr/bin/env python3
"""Freeze every supplied X file identity; do not infer timestamp units or decode."""
import argparse
from concurrent.futures import ThreadPoolExecutor
import hashlib
import json
import os
from pathlib import Path
import re
import stat
from datetime import datetime, timezone


def identify(root, path):
    before = path.stat()
    if not stat.S_ISREG(before.st_mode):
        raise ValueError("source is not a regular file")
    value = hashlib.sha256()
    with path.open("rb") as source:
        for chunk in iter(lambda: source.read(1024 * 1024), b""):
            value.update(chunk)
    after = path.stat()
    identity = lambda s: (s.st_dev, s.st_ino, s.st_size, s.st_mtime_ns, s.st_ctime_ns)
    if identity(before) != identity(after):
        raise ValueError("source changed while hashing")
    site = re.search(r"(?:^|_)(ZF[0-9]+)(?:_|\.)", path.name, re.IGNORECASE)
    return {"relative_path": str(path.relative_to(root)),
            "radar_id": site.group(1).lower() if site else "unclassified",
            "sha256": value.hexdigest(), "size_bytes": before.st_size,
            "filename_date_is_verified_observation_time": False}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("root", type=Path)
    parser.add_argument("output", type=Path)
    args = parser.parse_args()
    args.output.mkdir(parents=True, exist_ok=False)
    paths = sorted(p for p in args.root.rglob("*20260828*") if p.is_file())
    state = {"status": "HASHING", "pid": os.getpid(), "files_expected": len(paths),
             "files_hashed": 0, "bytes_hashed": 0,
             "started_at": datetime.now(timezone.utc).isoformat(),
             "decode_and_cadence_acceptance": "PENDING"}

    def persist():
        state["updated_at"] = datetime.now(timezone.utc).isoformat()
        temp = args.output / "state.json.tmp"
        temp.write_text(json.dumps(state))
        temp.replace(args.output / "state.json")

    persist()
    aggregate = hashlib.sha256()
    try:
        with (args.output / "raw-files.jsonl").open("x") as target, ThreadPoolExecutor(max_workers=4) as executor:
            for record in executor.map(lambda path: identify(args.root, path), paths):
                line = json.dumps(record, sort_keys=True, separators=(",", ":")) + "\n"
                target.write(line)
                aggregate.update(line.encode())
                state["files_hashed"] += 1
                state["bytes_hashed"] += record["size_bytes"]
                if state["files_hashed"] % 25 == 0:
                    target.flush()
                    persist()
        current = sorted(p for p in args.root.rglob("*20260828*") if p.is_file())
        if current != paths:
            raise ValueError("source directory membership changed while freezing")
        state.update(status="FROZEN", manifest_sha256=aggregate.hexdigest())
    except Exception as error:
        state.update(status="FAILED", error_type=type(error).__name__, detail=str(error))
        raise
    finally:
        persist()


if __name__ == "__main__":
    main()
