from __future__ import annotations

import argparse
import hashlib
import json
import os
import platform
import random
import tempfile
import time
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

import numpy as np

from .data import MRMSZarrTrainingDataset
from .profile import load_nowcastnet_training_run_profile


class EvolutionSmokeError(RuntimeError):
    """Raised when the hardware smoke run cannot produce a verified checkpoint."""


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(8 * 1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _seed_everything(torch: Any, seed: int) -> None:
    random.seed(seed)
    np.random.seed(seed)
    torch.manual_seed(seed)
    if torch.cuda.is_available():
        torch.cuda.manual_seed_all(seed)


def _save_checkpoint_atomic(
    torch: Any,
    path: Path,
    state: dict[str, Any],
) -> str:
    path.parent.mkdir(parents=True, exist_ok=True)
    if path.exists():
        raise EvolutionSmokeError(f"checkpoint already exists: {path}")
    descriptor, temporary_name = tempfile.mkstemp(
        prefix=f".{path.name}.tmp-",
        dir=path.parent,
    )
    os.close(descriptor)
    temporary = Path(temporary_name)
    try:
        torch.save(state, temporary)
        loaded = torch.load(temporary, map_location="cpu", weights_only=False)
        if (
            loaded.get("global_step") != state["global_step"]
            or loaded.get("profile_sha256") != state["profile_sha256"]
            or loaded.get("sample_ids") != state["sample_ids"]
        ):
            raise EvolutionSmokeError("checkpoint load-back identity differs")
        os.replace(temporary, path)
        return _sha256(path)
    finally:
        temporary.unlink(missing_ok=True)


def run_evolution_smoke(
    *,
    profile_path: Path,
    repository_root: Path,
    data_root: Path,
    output_dir: Path,
    track_name: str,
    device_name: str,
    batch_size: int,
    precision: str,
    sample_offset: int,
) -> dict[str, Any]:
    try:
        import torch
    except ImportError as exc:
        raise EvolutionSmokeError("PyTorch is required for the evolution smoke run") from exc
    from .evolution import EvolutionNetwork, evolution_loss

    profile = load_nowcastnet_training_run_profile(
        profile_path,
        repository_root=repository_root,
    )
    track = profile.track(track_name)
    if device_name == "cuda" and not torch.cuda.is_available():
        raise EvolutionSmokeError("CUDA was requested but is unavailable")
    if batch_size < 1 or batch_size > profile.evolution.global_batch_size:
        raise EvolutionSmokeError("smoke batch size is outside the frozen global batch")
    if precision not in {"fp32", "bf16"}:
        raise EvolutionSmokeError("smoke precision must be fp32 or bf16")

    if track.name == profile.foundation.name:
        expected_sample_index_sha256 = profile.sample_index_sha256
    else:
        try:
            data_report = json.loads(
                (data_root / "pilot-report.json").read_text(encoding="utf-8")
            )
        except (OSError, json.JSONDecodeError) as exc:
            raise EvolutionSmokeError(f"cannot read conformance evidence: {exc}") from exc
        if (
            data_report.get("dataset_version")
            != "nowcastnet-mrms-paper-conformance-0p02-v1"
            or data_report.get("run_profile_sha256") != profile.profile_sha256
            or data_report.get("sample_count") != track.expected_sample_count
            or data_report.get("source_resolution_deg") != track.source_resolution_deg
            or data_report.get("resolution_deg") != track.resolution_deg
            or data_report.get("source_native_crop_size") != track.native_crop_size
            or data_report.get("model_crop_size") != track.model_crop_size
            or data_report.get("downsample_method") != track.downsample_method
            or data_report.get("official_kernel_published") is not False
        ):
            raise EvolutionSmokeError("conformance evidence differs from the run profile")
        expected_sample_index_sha256 = str(data_report["sample_index_sha256"])
    dataset = MRMSZarrTrainingDataset(
        data_root,
        expected_sample_index_sha256=expected_sample_index_sha256,
        expected_sample_count=track.expected_sample_count,
        expected_crop_size=track.model_crop_size,
        input_frames=profile.sequence[0],
        target_frames=profile.sequence[1],
    )
    indices = [
        (sample_offset + index) % len(dataset)
        for index in range(batch_size)
    ]
    samples = [dataset[index] for index in indices]
    inputs = torch.from_numpy(
        np.stack([sample.input_rate_mm_h for sample in samples])
    ).to(device_name)
    targets = torch.from_numpy(
        np.stack([sample.target_rate_mm_h for sample in samples])
    ).to(device_name)

    _seed_everything(torch, profile.run_seed)
    model = EvolutionNetwork(
        input_frames=profile.sequence[0],
        target_frames=profile.sequence[1],
        base_channels=profile.evolution.base_channels,
    ).to(device_name)
    optimizer = torch.optim.Adam(
        model.parameters(),
        lr=profile.evolution.initial_learning_rate,
    )
    scheduler = torch.optim.lr_scheduler.MultiStepLR(
        optimizer,
        milestones=[profile.evolution.decay_step],
        gamma=profile.evolution.decayed_learning_rate
        / profile.evolution.initial_learning_rate,
    )
    if device_name == "cuda":
        torch.cuda.reset_peak_memory_stats()
        torch.cuda.synchronize()
    started = time.perf_counter()
    optimizer.zero_grad(set_to_none=True)
    autocast_enabled = device_name == "cuda" and precision == "bf16"
    with torch.autocast(
        device_type=device_name,
        dtype=torch.bfloat16 if precision == "bf16" else torch.float32,
        enabled=autocast_enabled,
    ):
        intensity, motion = model(inputs)
        loss = evolution_loss(
            inputs,
            targets,
            intensity,
            motion,
            motion_regularization_lambda=(
                profile.evolution.motion_regularization_lambda
            ),
            weight_cap=profile.evolution.weight_cap,
        )
    if not bool(torch.isfinite(loss.total)):
        raise EvolutionSmokeError("evolution smoke loss is non-finite")
    loss.total.backward()
    gradient_norm = torch.nn.utils.clip_grad_norm_(
        model.parameters(),
        max_norm=profile.evolution.gradient_clip_norm,
    )
    if not bool(torch.isfinite(gradient_norm)):
        raise EvolutionSmokeError("evolution smoke gradient norm is non-finite")
    optimizer.step()
    scheduler.step()
    if device_name == "cuda":
        torch.cuda.synchronize()
    duration_seconds = time.perf_counter() - started

    sample_ids = [sample.sample_id for sample in samples]
    checkpoint_path = output_dir / "evolution-smoke-step-000001.pt"
    checkpoint_state = {
        "schema_version": "rainpulse.nowcastnet-evolution-checkpoint/1.0",
        "created_at": datetime.now(UTC).isoformat(),
        "global_step": 1,
        "stage": "evolution",
        "profile_version": profile.profile_version,
        "profile_sha256": profile.profile_sha256,
        "sample_index_sha256": expected_sample_index_sha256,
        "data_track": track.name,
        "sample_ids": sample_ids,
        "model_state_dict": model.state_dict(),
        "optimizer_state_dict": optimizer.state_dict(),
        "scheduler_state_dict": scheduler.state_dict(),
        "python_random_state": random.getstate(),
        "numpy_random_state": np.random.get_state(),
        "torch_random_state": torch.get_rng_state(),
        "cuda_random_state": (
            torch.cuda.get_rng_state_all() if torch.cuda.is_available() else None
        ),
        "amp_scaler_state_dict": None,
        "precision": precision,
    }
    checkpoint_sha256 = _save_checkpoint_atomic(
        torch,
        checkpoint_path,
        checkpoint_state,
    )
    peak_memory_bytes = (
        int(torch.cuda.max_memory_allocated()) if device_name == "cuda" else 0
    )
    peak_reserved_memory_bytes = (
        int(torch.cuda.max_memory_reserved()) if device_name == "cuda" else 0
    )
    gpu = torch.cuda.get_device_properties(0) if device_name == "cuda" else None
    result = {
        "schema_version": "1.0",
        "status": "passed",
        "created_at": datetime.now(UTC).isoformat(),
        "profile_version": profile.profile_version,
        "profile_sha256": profile.profile_sha256,
        "sample_index_sha256": expected_sample_index_sha256,
        "data_track": track.name,
        "resolution_deg": track.resolution_deg,
        "batch_size": batch_size,
        "precision": precision,
        "device": str(device_name),
        "torch_version": str(torch.__version__),
        "cuda_version": str(torch.version.cuda),
        "gpu_name": str(gpu.name) if gpu is not None else None,
        "gpu_total_memory_bytes": int(gpu.total_memory) if gpu is not None else 0,
        "python_version": platform.python_version(),
        "sample_ids": sample_ids,
        "loss": {
            "total": float(loss.total.detach().float().cpu()),
            "accumulation": float(loss.accumulation.detach().float().cpu()),
            "motion_regularization": float(
                loss.motion_regularization.detach().float().cpu()
            ),
            "gradient_norm_before_clip": float(gradient_norm.detach().float().cpu()),
        },
        "duration_seconds": duration_seconds,
        "peak_memory_bytes": peak_memory_bytes,
        "peak_reserved_memory_bytes": peak_reserved_memory_bytes,
        "checkpoint": {
            "path": checkpoint_path.name,
            "sha256": checkpoint_sha256,
            "load_back_verified": True,
        },
        "operational_eligible": False,
    }
    output_dir.mkdir(parents=True, exist_ok=True)
    report_path = output_dir / "evolution-smoke-report.json"
    report_path.write_text(
        json.dumps(result, ensure_ascii=False, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )
    return result


def _build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Run one audited NowcastNet evolution update")
    parser.add_argument("--profile", type=Path, required=True)
    parser.add_argument("--repository-root", type=Path, required=True)
    parser.add_argument("--data-root", type=Path, required=True)
    parser.add_argument("--output-dir", type=Path, required=True)
    parser.add_argument(
        "--track",
        choices=("foundation_0p01", "paper_conformance_0p02"),
        default="foundation_0p01",
    )
    parser.add_argument("--device", choices=("cpu", "cuda"), default="cuda")
    parser.add_argument("--batch-size", type=int, default=1)
    parser.add_argument("--precision", choices=("fp32", "bf16"), default="bf16")
    parser.add_argument("--sample-offset", type=int, default=0)
    return parser


def main() -> int:
    args = _build_parser().parse_args()
    result = run_evolution_smoke(
        profile_path=args.profile,
        repository_root=args.repository_root,
        data_root=args.data_root,
        output_dir=args.output_dir,
        track_name=args.track,
        device_name=args.device,
        batch_size=args.batch_size,
        precision=args.precision,
        sample_offset=args.sample_offset,
    )
    print(json.dumps(result, ensure_ascii=False, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
