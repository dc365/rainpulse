#!/usr/bin/env python3
"""Prune locally retained NowcastNet checkpoints after a stopped training window."""
from __future__ import annotations

import json
import os
from pathlib import Path
import subprocess

ROOT = Path("/home/yons/hwapp/rainpulse-training/nowcastnet-mrms-v1")


def active(unit: str) -> bool:
    return subprocess.run(
        ["systemctl", "--user", "is-active", "--quiet", unit], check=False
    ).returncode == 0


def latest_file(run_dir: Path) -> Path:
    latest = json.loads((run_dir / "LATEST.json").read_text())
    path = (run_dir / latest["path"]).resolve()
    if run_dir.resolve() not in path.parents or not path.is_file():
        raise RuntimeError(f"unsafe or missing latest checkpoint: {path}")
    return path


def prune(run_dir: Path, keep_recent: int) -> list[tuple[Path, int]]:
    current = latest_file(run_dir)
    checkpoints = sorted((run_dir / "checkpoints").glob("*.pt"), key=lambda p: p.stat().st_mtime)
    retained = {current, *checkpoints[-keep_recent:]}
    removed: list[tuple[Path, int]] = []
    for path in checkpoints:
        if path not in retained:
            size = path.stat().st_size
            path.unlink()
            removed.append((path, size))
    return removed


if active("rainpulse-nowcastnet-generative.service"):
    raise SystemExit("refusing to prune while generative training is active")

removed = prune(ROOT / "foundation-training-v1", keep_recent=1)
removed += prune(ROOT / "generative-training-v1", keep_recent=3)
print(f"removed={len(removed)} bytes={sum(size for _, size in removed)}")
