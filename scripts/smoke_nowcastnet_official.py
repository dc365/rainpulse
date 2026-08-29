from __future__ import annotations

import argparse
import json
import time
from pathlib import Path

import numpy as np

from rainpulse_algo.nowcast.nowcastnet_official_backend import OfficialNowcastNetBackend
from rainpulse_algo.nowcast.nowcastnet_profile import load_nowcastnet_profile


def main() -> None:
    parser = argparse.ArgumentParser(description="Smoke-test reviewed official NowcastNet backend")
    parser.add_argument("--profile", required=True)
    parser.add_argument("--capsule-root", required=True)
    parser.add_argument("--input-npy", required=True)
    parser.add_argument("--output-npy")
    parser.add_argument("--device", default="cuda:0")
    parser.add_argument("--allow-cpu", action="store_true")
    parser.add_argument("--members", type=int, default=1)
    parser.add_argument("--seed", type=int, default=20260830)
    parser.add_argument("--repeat", type=int, default=1)
    args = parser.parse_args()
    if args.repeat <= 0:
        parser.error("--repeat must be positive")

    profile = load_nowcastnet_profile(args.profile)
    model_frames = np.load(args.input_npy, allow_pickle=False)
    started = time.perf_counter()
    backend = OfficialNowcastNetBackend(
        args.capsule_root,
        profile=profile,
        device=args.device,
        allow_cpu_for_smoke=args.allow_cpu,
    )
    load_seconds = time.perf_counter() - started

    torch = backend._torch
    if args.device.startswith("cuda:"):
        torch.cuda.reset_peak_memory_stats()
    outputs: list[np.ndarray] = []
    inference_seconds: list[float] = []
    for _ in range(args.repeat):
        started = time.perf_counter()
        outputs.append(backend(model_frames, args.members, args.seed))
        if args.device.startswith("cuda:"):
            torch.cuda.synchronize()
        inference_seconds.append(time.perf_counter() - started)

    output = outputs[0]
    if args.output_npy:
        destination = Path(args.output_npy)
        destination.parent.mkdir(parents=True, exist_ok=True)
        np.save(destination, output, allow_pickle=False)
    repeat_delta = max(
        (float(np.max(np.abs(candidate - output))) for candidate in outputs[1:]),
        default=0.0,
    )
    result = {
        "runtime": backend.runtime_info(),
        "input": {
            "path": str(Path(args.input_npy).resolve()),
            "shape": list(model_frames.shape),
            "minimum": float(np.min(model_frames[..., 0])),
            "maximum": float(np.max(model_frames[..., 0])),
            "valid_fraction": float(np.mean(model_frames[..., 1])),
        },
        "output": {
            "shape": list(output.shape),
            "minimum": float(np.min(output)),
            "maximum": float(np.max(output)),
            "mean": float(np.mean(output)),
            "negative_count": int(np.count_nonzero(output < 0.0)),
            "non_finite_count": int(np.count_nonzero(~np.isfinite(output))),
        },
        "model_load_seconds": load_seconds,
        "inference_seconds": inference_seconds,
        "repeat_max_abs_delta": repeat_delta,
        "gpu_peak_memory_bytes": (
            int(torch.cuda.max_memory_allocated()) if args.device.startswith("cuda:") else None
        ),
    }
    print(json.dumps(result, ensure_ascii=False, indent=2, sort_keys=True))


if __name__ == "__main__":
    main()
