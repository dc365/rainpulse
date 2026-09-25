#!/usr/bin/env python3
"""Generate a NEW execution policy only. Never edits network/Compose or deploys."""

from __future__ import annotations
import argparse
from dataclasses import asdict, replace
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "algorithms"))
from rainpulse_algo.multiband.execution import ExecutionOptions


def generate(output, *, backend="numpy", scratch_parent=None, limits=None):
    output = Path(output)
    cfg = ExecutionOptions.load(limits) if limits else ExecutionOptions()
    if scratch_parent is not None and (
        not Path(scratch_parent).is_absolute() or not Path(scratch_parent).is_dir()
    ):
        raise ValueError(
            "scratch_parent must be an existing, explicitly selected absolute directory"
        )
    cfg = replace(
        cfg,
        streaming=True,
        selection_backend=backend,
        scratch_parent=scratch_parent
        if scratch_parent is not None
        else cfg.scratch_parent,
    )
    if output.exists():
        raise FileExistsError(output)
    output.parent.mkdir(parents=True, exist_ok=True)
    with output.open("x", encoding="utf-8") as f:
        f.write(json.dumps(asdict(cfg), indent=2, sort_keys=True) + "\n")
    return cfg


def main():
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument("--output", type=Path, required=True)
    p.add_argument("--backend", choices=["numpy", "numba"], default="numpy")
    p.add_argument("--scratch-parent")
    p.add_argument(
        "--limits",
        type=Path,
        help="Optional complete validated execution settings; no hardware guessing",
    )
    a = p.parse_args()
    cfg = generate(
        a.output, backend=a.backend, scratch_parent=a.scratch_parent, limits=a.limits
    )
    print(
        json.dumps(
            {
                "execution_policy": str(a.output),
                "policy_sha256": cfg.digest,
                "streaming": True,
                "activated": False,
                "next": "Review budgets, then explicitly bind RAINPULSE_MULTIBAND_EXECUTION_CONFIG at worker startup.",
            },
            indent=2,
        )
    )


if __name__ == "__main__":
    main()
