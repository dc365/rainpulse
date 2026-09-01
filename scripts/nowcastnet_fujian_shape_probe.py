#!/usr/bin/env python3
"""GPU-probe an experimental native Fujian NowcastNet spatial shape.

This command never mutates the frozen RP-026 profile. It derives an in-memory
profile with only height/width changed, verifies the reviewed capsule and
weights through the production backend, and runs one deterministic dry field.
"""

from __future__ import annotations

import argparse
import json
import time
from dataclasses import replace
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

import numpy as np

from rainpulse_algo.nowcast.nowcastnet_official_backend import OfficialNowcastNetBackend
from rainpulse_algo.nowcast.nowcastnet_profile import (
    NowcastNetProfile,
    load_nowcastnet_profile,
)


def experimental_profile(
    parent: NowcastNetProfile, *, height: int, width: int
) -> NowcastNetProfile:
    if height < 32 or width < 32 or height % 32 or width % 32:
        raise ValueError("experimental NowcastNet dimensions must be positive multiples of 32")
    return replace(
        parent,
        profile_version=f"{parent.profile_version}-shape-{height}x{width}",
        protocol=replace(parent.protocol, input_height=height, input_width=width),
    )


def run_probe(
    *,
    parent_profile: Path,
    capsule_root: Path,
    device: str,
    height: int,
    width: int,
    member_count: int,
    random_seed: int,
) -> dict[str, Any]:
    if member_count < 1 or member_count > 4:
        raise ValueError("shape probe member count must be between 1 and 4")
    parent = load_nowcastnet_profile(parent_profile)
    profile = experimental_profile(parent, height=height, width=width)
    backend = OfficialNowcastNetBackend(capsule_root, profile=profile, device=device)
    frames = np.zeros(
        (
            profile.protocol.input_frames,
            profile.protocol.input_height,
            profile.protocol.input_width,
            profile.protocol.input_channels,
        ),
        dtype="float32",
    )
    frames[..., 1] = 1.0
    backend.reset_peak_memory_stats()
    started = time.perf_counter()
    output = backend(frames, member_count, random_seed)
    elapsed = time.perf_counter() - started
    expected = (
        member_count,
        profile.protocol.output_frames,
        profile.protocol.input_height,
        profile.protocol.input_width,
    )
    if output.shape != expected or np.any(~np.isfinite(output)):
        raise RuntimeError(
            f"experimental NowcastNet output differs: {output.shape}, expected {expected}"
        )
    return {
        "schema_version": "1.0",
        "status": "passed",
        "validated_at": datetime.now(UTC).isoformat(),
        "parent_profile_version": parent.profile_version,
        "experimental_profile_version": profile.profile_version,
        "height": height,
        "width": width,
        "member_count": member_count,
        "random_seed": random_seed,
        "output_shape": list(output.shape),
        "output_min_mm_h": float(np.min(output)),
        "output_max_mm_h": float(np.max(output)),
        "runtime_seconds": elapsed,
        "runtime": backend.runtime_info(),
        **backend.peak_memory_stats(),
        "product_publication_enabled": False,
        "operational_eligible": False,
    }


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Verify that reviewed RP-026 weights execute on a fixed Fujian ROI shape"
    )
    parser.add_argument("--parent-profile", type=Path, required=True)
    parser.add_argument("--capsule-root", type=Path, required=True)
    parser.add_argument("--device", default="cuda:0")
    parser.add_argument("--height", type=int, default=192)
    parser.add_argument("--width", type=int, default=480)
    parser.add_argument("--members", type=int, default=1)
    parser.add_argument("--random-seed", type=int, default=20260901)
    parser.add_argument("--output", type=Path)
    args = parser.parse_args()

    report = run_probe(
        parent_profile=args.parent_profile,
        capsule_root=args.capsule_root,
        device=args.device,
        height=args.height,
        width=args.width,
        member_count=args.members,
        random_seed=args.random_seed,
    )
    payload = json.dumps(report, ensure_ascii=False, indent=2, sort_keys=True)
    if args.output:
        args.output.parent.mkdir(parents=True, exist_ok=True)
        temporary = args.output.with_suffix(args.output.suffix + ".tmp")
        temporary.write_text(payload + "\n", encoding="utf-8")
        temporary.replace(args.output)
    print(payload)


if __name__ == "__main__":
    main()
