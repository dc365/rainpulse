from __future__ import annotations

import argparse
import hashlib
import json
import os
import platform
import random
import subprocess
import tempfile
import time
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

import numpy as np

from .data import MRMSZarrTrainingDataset
from .generative_profile import load_generative_training_profile
from .profile import load_nowcastnet_training_run_profile


class GenerativeSmokeError(RuntimeError):
    """Raised when the stage-B generative smoke violates its frozen contract."""


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(8 * 1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _code_revision(repository_root: Path) -> str:
    try:
        return subprocess.run(
            ["git", "rev-parse", "HEAD"],
            cwd=repository_root,
            check=True,
            capture_output=True,
            text=True,
        ).stdout.strip()
    except (OSError, subprocess.CalledProcessError) as exc:
        raise GenerativeSmokeError(f"cannot resolve training code revision: {exc}") from exc


def _seed_everything(torch: Any, seed: int) -> None:
    random.seed(seed)
    np.random.seed(seed)
    torch.manual_seed(seed)
    if torch.cuda.is_available():
        torch.cuda.manual_seed_all(seed)


def _atomic_json(path: Path, payload: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    descriptor, temporary_name = tempfile.mkstemp(prefix=f".{path.name}.tmp-", dir=path.parent)
    os.close(descriptor)
    temporary = Path(temporary_name)
    try:
        temporary.write_text(
            json.dumps(payload, ensure_ascii=False, indent=2, sort_keys=True) + "\n",
            encoding="utf-8",
        )
        os.replace(temporary, path)
    finally:
        temporary.unlink(missing_ok=True)


def _save_checkpoint_atomic(
    torch: Any,
    *,
    path: Path,
    state: dict[str, Any],
) -> str:
    from .evolution_train import _state_fingerprint

    path.parent.mkdir(parents=True, exist_ok=True)
    if path.exists():
        raise GenerativeSmokeError(f"generative smoke checkpoint already exists: {path}")
    state["generator_state_sha256"] = _state_fingerprint(
        torch,
        state["generator_state_dict"],
    )
    state["discriminator_state_sha256"] = _state_fingerprint(
        torch,
        state["discriminator_state_dict"],
    )
    state["generator_optimizer_state_sha256"] = _state_fingerprint(
        torch,
        state["generator_optimizer_state_dict"],
    )
    state["discriminator_optimizer_state_sha256"] = _state_fingerprint(
        torch,
        state["discriminator_optimizer_state_dict"],
    )
    descriptor, temporary_name = tempfile.mkstemp(prefix=f".{path.name}.tmp-", dir=path.parent)
    os.close(descriptor)
    temporary = Path(temporary_name)
    try:
        torch.save(state, temporary)
        loaded = torch.load(temporary, map_location="cpu", weights_only=False)
        if (
            loaded.get("global_step") != 1
            or loaded.get("profile_sha256") != state["profile_sha256"]
            or loaded.get("evolution_checkpoint_sha256")
            != state["evolution_checkpoint_sha256"]
            or _state_fingerprint(torch, loaded["generator_state_dict"])
            != state["generator_state_sha256"]
            or _state_fingerprint(torch, loaded["discriminator_state_dict"])
            != state["discriminator_state_sha256"]
            or _state_fingerprint(torch, loaded["generator_optimizer_state_dict"])
            != state["generator_optimizer_state_sha256"]
            or _state_fingerprint(torch, loaded["discriminator_optimizer_state_dict"])
            != state["discriminator_optimizer_state_sha256"]
        ):
            raise GenerativeSmokeError("generative smoke checkpoint load-back differs")
        os.replace(temporary, path)
    finally:
        temporary.unlink(missing_ok=True)
    return _sha256(path)


def run_generative_smoke(
    *,
    generative_profile_path: Path,
    repository_root: Path,
    data_root: Path,
    evolution_checkpoint_path: Path,
    output_dir: Path,
    device_name: str,
    batch_size: int,
    precision: str,
    sample_offset: int,
) -> dict[str, Any]:
    try:
        import torch
    except ImportError as exc:
        raise GenerativeSmokeError("PyTorch is required for generative smoke") from exc
    from .evolution import EvolutionNetwork, rollout_evolution
    from .generation import NowcastNetGenerator, TemporalDiscriminator, generative_train_step

    generative_profile = load_generative_training_profile(
        generative_profile_path,
        repository_root=repository_root,
    )
    evolution_profile_path = repository_root / "configs/training/nowcastnet-mrms-run-v1.yaml"
    evolution_profile = load_nowcastnet_training_run_profile(
        evolution_profile_path,
        repository_root=repository_root,
    )
    if evolution_profile.profile_sha256 != generative_profile.evolution_profile_sha256:
        raise GenerativeSmokeError("evolution and generative profile hashes differ")
    if device_name == "cuda" and not torch.cuda.is_available():
        raise GenerativeSmokeError("CUDA was requested but is unavailable")
    if batch_size < 1 or batch_size > generative_profile.global_batch_size:
        raise GenerativeSmokeError("smoke batch size is outside the frozen global batch")
    if precision not in {"fp32", "bf16"}:
        raise GenerativeSmokeError("smoke precision must be fp32 or bf16")
    if output_dir.exists() and any(output_dir.iterdir()):
        raise GenerativeSmokeError("generative smoke output directory is not empty")

    dataset = MRMSZarrTrainingDataset(
        data_root,
        expected_sample_index_sha256=evolution_profile.sample_index_sha256,
        expected_sample_count=evolution_profile.foundation.expected_sample_count,
        expected_crop_size=evolution_profile.foundation.model_crop_size,
        input_frames=generative_profile.input_frames,
        target_frames=generative_profile.target_frames,
    )
    indices = [(sample_offset + index) % len(dataset) for index in range(batch_size)]
    samples = [dataset[index] for index in indices]
    inputs = torch.from_numpy(
        np.stack([sample.input_rate_mm_h for sample in samples])
    ).to(device_name)
    targets = torch.from_numpy(
        np.stack([sample.target_rate_mm_h for sample in samples])
    ).to(device_name)

    evolution_checkpoint_sha256 = _sha256(evolution_checkpoint_path)
    evolution_state = torch.load(
        evolution_checkpoint_path,
        map_location="cpu",
        weights_only=False,
    )
    evolution_step = int(evolution_state.get("global_step", -1))
    if (
        evolution_state.get("schema_version")
        != "rainpulse.nowcastnet-evolution-checkpoint/1.0"
        or evolution_state.get("stage") != "evolution"
        or evolution_state.get("profile_sha256") != evolution_profile.profile_sha256
        or evolution_state.get("sample_index_sha256")
        != evolution_profile.sample_index_sha256
        or evolution_state.get("data_track") != evolution_profile.foundation.name
        or evolution_step < generative_profile.stage_b_smoke_minimum_step
        or evolution_step > generative_profile.completed_pretraining_step
    ):
        raise GenerativeSmokeError("evolution checkpoint is ineligible for stage-B smoke")

    _seed_everything(torch, generative_profile.run_seed)
    if device_name == "cuda":
        torch.cuda.reset_peak_memory_stats()
    autocast_enabled = device_name == "cuda" and precision == "bf16"
    evolution = EvolutionNetwork(
        input_frames=generative_profile.input_frames,
        target_frames=generative_profile.target_frames,
        base_channels=evolution_profile.evolution.base_channels,
    ).to(device_name)
    evolution.load_state_dict(evolution_state["model_state_dict"], strict=True)
    evolution.eval().requires_grad_(False)
    with torch.no_grad(), torch.autocast(
        device_type=device_name,
        dtype=torch.bfloat16 if precision == "bf16" else torch.float32,
        enabled=autocast_enabled,
    ):
        intensity, motion = evolution(inputs)
        evolution_prediction = rollout_evolution(inputs, intensity, motion)
    del evolution, evolution_state, intensity, motion
    if device_name == "cuda":
        torch.cuda.empty_cache()

    generator = NowcastNetGenerator(
        input_frames=generative_profile.input_frames,
        target_frames=generative_profile.target_frames,
        base_channels=generative_profile.base_channels,
        rain_rate_cap_mm_h=generative_profile.rain_rate_cap_mm_h,
    ).to(device_name)
    discriminator = TemporalDiscriminator(
        context_frames=generative_profile.context_frames,
        target_frames=generative_profile.target_frames,
    ).to(device_name)
    generator_optimizer = torch.optim.Adam(
        generator.parameters(),
        lr=generative_profile.generator_learning_rate,
    )
    discriminator_optimizer = torch.optim.Adam(
        discriminator.parameters(),
        lr=generative_profile.discriminator_learning_rate,
    )

    if device_name == "cuda":
        torch.cuda.synchronize()
    started = time.perf_counter()
    with torch.autocast(
        device_type=device_name,
        dtype=torch.bfloat16 if precision == "bf16" else torch.float32,
        enabled=autocast_enabled,
    ):
        result = generative_train_step(
            generator=generator,
            discriminator=discriminator,
            generator_optimizer=generator_optimizer,
            discriminator_optimizer=discriminator_optimizer,
            inputs=inputs,
            targets=targets,
            evolution_prediction=evolution_prediction,
            ensemble_members=generative_profile.ensemble_members,
            adversarial_weight=generative_profile.adversarial_weight,
            pool_weight=generative_profile.pool_weight,
            gradient_clip_norm=generative_profile.gradient_clip_norm,
        )
    if device_name == "cuda":
        torch.cuda.synchronize()
    duration_seconds = time.perf_counter() - started

    output_dir.mkdir(parents=True, exist_ok=True)
    checkpoint_path = output_dir / "generative-smoke-step-000001.pt"
    checkpoint_state = {
        "schema_version": "rainpulse.nowcastnet-generative-checkpoint/1.0",
        "created_at": datetime.now(UTC).isoformat(),
        "global_step": 1,
        "stage": "generative_smoke",
        "profile_version": generative_profile.profile_version,
        "profile_sha256": generative_profile.profile_sha256,
        "evolution_profile_sha256": evolution_profile.profile_sha256,
        "evolution_checkpoint_step": evolution_step,
        "evolution_checkpoint_sha256": evolution_checkpoint_sha256,
        "evolution_pretraining_complete": (
            evolution_step == generative_profile.completed_pretraining_step
        ),
        "sample_index_sha256": evolution_profile.sample_index_sha256,
        "sample_ids": [sample.sample_id for sample in samples],
        "batch_size": batch_size,
        "ensemble_members": generative_profile.ensemble_members,
        "precision": precision,
        "code_revision": _code_revision(repository_root),
        "generator_state_dict": generator.state_dict(),
        "discriminator_state_dict": discriminator.state_dict(),
        "generator_optimizer_state_dict": generator_optimizer.state_dict(),
        "discriminator_optimizer_state_dict": discriminator_optimizer.state_dict(),
        "python_random_state": random.getstate(),
        "numpy_random_state": np.random.get_state(),
        "torch_random_state": torch.get_rng_state(),
        "cuda_random_state": (
            torch.cuda.get_rng_state_all() if torch.cuda.is_available() else None
        ),
        "amp_scaler_state_dict": None,
    }
    checkpoint_sha256 = _save_checkpoint_atomic(
        torch,
        path=checkpoint_path,
        state=checkpoint_state,
    )
    gpu = torch.cuda.get_device_properties(0) if device_name == "cuda" else None
    report = {
        "schema_version": "1.0",
        "status": "passed",
        "created_at": datetime.now(UTC).isoformat(),
        "profile_version": generative_profile.profile_version,
        "profile_sha256": generative_profile.profile_sha256,
        "evolution_profile_sha256": evolution_profile.profile_sha256,
        "evolution_checkpoint_step": evolution_step,
        "evolution_checkpoint_sha256": evolution_checkpoint_sha256,
        "evolution_pretraining_complete": (
            evolution_step == generative_profile.completed_pretraining_step
        ),
        "full_generative_training_eligible": (
            evolution_step == generative_profile.completed_pretraining_step
        ),
        "sample_index_sha256": evolution_profile.sample_index_sha256,
        "sample_ids": [sample.sample_id for sample in samples],
        "batch_size": batch_size,
        "ensemble_members": generative_profile.ensemble_members,
        "precision": precision,
        "device": device_name,
        "loss": {
            "generator_total": float(result.generator.total.detach().float().cpu()),
            "adversarial": float(result.generator.adversarial.detach().float().cpu()),
            "pool_regularization": float(
                result.generator.pool_regularization.detach().float().cpu()
            ),
            "discriminator_total": float(
                result.discriminator.total.detach().float().cpu()
            ),
            "discriminator_real": float(
                result.discriminator.real.detach().float().cpu()
            ),
            "discriminator_fake": float(
                result.discriminator.fake.detach().float().cpu()
            ),
            "generator_gradient_norm_before_clip": float(
                result.generator_gradient_norm.float().cpu()
            ),
            "discriminator_gradient_norm_before_clip": float(
                result.discriminator_gradient_norm.float().cpu()
            ),
        },
        "duration_seconds": duration_seconds,
        "checkpoint": {
            "path": checkpoint_path.name,
            "sha256": checkpoint_sha256,
        },
        "torch_version": str(torch.__version__),
        "cuda_version": str(torch.version.cuda),
        "python_version": platform.python_version(),
        "gpu_name": str(gpu.name) if gpu is not None else None,
        "gpu_total_memory_bytes": int(gpu.total_memory) if gpu is not None else 0,
        "peak_allocated_memory_bytes": (
            int(torch.cuda.max_memory_allocated()) if device_name == "cuda" else 0
        ),
        "peak_reserved_memory_bytes": (
            int(torch.cuda.max_memory_reserved()) if device_name == "cuda" else 0
        ),
        "operational_eligible": False,
    }
    _atomic_json(output_dir / "generative-smoke-report.json", report)
    return report


def _build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Run the RainPulse generative stage smoke")
    parser.add_argument("--profile", type=Path, required=True)
    parser.add_argument("--repository-root", type=Path, required=True)
    parser.add_argument("--data-root", type=Path, required=True)
    parser.add_argument("--evolution-checkpoint", type=Path, required=True)
    parser.add_argument("--output-dir", type=Path, required=True)
    parser.add_argument("--device", choices=("cpu", "cuda"), default="cuda")
    parser.add_argument("--batch-size", type=int, default=1)
    parser.add_argument("--precision", choices=("fp32", "bf16"), default="bf16")
    parser.add_argument("--sample-offset", type=int, default=0)
    return parser


def main() -> int:
    args = _build_parser().parse_args()
    report = run_generative_smoke(
        generative_profile_path=args.profile,
        repository_root=args.repository_root,
        data_root=args.data_root,
        evolution_checkpoint_path=args.evolution_checkpoint,
        output_dir=args.output_dir,
        device_name=args.device,
        batch_size=args.batch_size,
        precision=args.precision,
        sample_offset=args.sample_offset,
    )
    print(json.dumps(report, ensure_ascii=False, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
