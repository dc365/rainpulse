from __future__ import annotations

import argparse
import hashlib
import json
import math
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
from .profile import NowcastNetTrainingRunProfile, load_nowcastnet_training_run_profile


class EvolutionTrainError(RuntimeError):
    """Raised when deterministic evolution training or exact recovery is unsafe."""


def deterministic_batch_indices(
    *,
    dataset_size: int,
    batch_size: int,
    run_seed: int,
    global_step: int,
) -> tuple[int, ...]:
    if dataset_size < 1 or batch_size < 1 or batch_size > dataset_size or global_step < 0:
        raise EvolutionTrainError("deterministic batch dimensions are invalid")
    identity = f"{run_seed}:{global_step}".encode()
    seed = int.from_bytes(hashlib.sha256(identity).digest()[:8], "big", signed=False)
    generator = np.random.default_rng(seed)
    indices = generator.choice(dataset_size, size=batch_size, replace=False)
    return tuple(int(index) for index in indices)


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(8 * 1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _canonical_sha256(value: Any) -> str:
    payload = json.dumps(value, sort_keys=True, separators=(",", ":")).encode()
    return hashlib.sha256(payload).hexdigest()


def _update_state_digest(torch: Any, digest: Any, value: Any) -> None:
    if torch.is_tensor(value):
        tensor = value.detach().cpu().contiguous()
        digest.update(b"tensor\0")
        digest.update(str(tensor.dtype).encode())
        digest.update(b"\0")
        digest.update(json.dumps(tuple(tensor.shape)).encode())
        digest.update(b"\0")
        digest.update(tensor.reshape(-1).view(torch.uint8).numpy().tobytes())
        return
    if isinstance(value, np.ndarray):
        array = np.ascontiguousarray(value)
        digest.update(b"ndarray\0")
        digest.update(str(array.dtype).encode())
        digest.update(b"\0")
        digest.update(json.dumps(array.shape).encode())
        digest.update(b"\0")
        digest.update(array.tobytes())
        return
    if isinstance(value, dict):
        digest.update(b"dict\0")
        for key in sorted(value, key=lambda candidate: repr(candidate)):
            _update_state_digest(torch, digest, key)
            _update_state_digest(torch, digest, value[key])
        return
    if isinstance(value, (list, tuple)):
        digest.update(type(value).__name__.encode() + b"\0")
        for item in value:
            _update_state_digest(torch, digest, item)
        return
    digest.update(type(value).__name__.encode() + b"\0")
    digest.update(repr(value).encode())
    digest.update(b"\0")


def _state_fingerprint(torch: Any, value: Any) -> str:
    digest = hashlib.sha256()
    _update_state_digest(torch, digest, value)
    return digest.hexdigest()


def _read_metrics(path: Path) -> list[dict[str, Any]]:
    try:
        rows = [
            json.loads(line)
            for line in path.read_text(encoding="utf-8").splitlines()
            if line
        ]
    except (OSError, json.JSONDecodeError) as exc:
        raise EvolutionTrainError(f"cannot read training metrics: {exc}") from exc
    if [int(row.get("global_step", -1)) for row in rows] != list(range(1, len(rows) + 1)):
        raise EvolutionTrainError("training metric steps are not contiguous")
    return rows


def compare_resume_metrics(
    reference_path: Path,
    resumed_path: Path,
    *,
    resume_after_step: int,
    absolute_tolerance: float,
    relative_tolerance: float,
) -> dict[str, Any]:
    if (
        resume_after_step < 1
        or absolute_tolerance < 0.0
        or relative_tolerance < 0.0
    ):
        raise EvolutionTrainError("resume comparison boundary is invalid")
    reference = _read_metrics(reference_path)
    resumed = _read_metrics(resumed_path)
    if len(reference) != len(resumed) or len(reference) <= resume_after_step:
        raise EvolutionTrainError("resume metric lengths differ or contain no resumed steps")
    numeric_fields = ("loss_total", "loss_accumulation")
    for reference_row, resumed_row in zip(
        reference[resume_after_step:],
        resumed[resume_after_step:],
        strict=True,
    ):
        if (
            reference_row["global_step"] != resumed_row["global_step"]
            or reference_row["batch_indices_sha256"]
            != resumed_row["batch_indices_sha256"]
            or any(
                not math.isclose(
                    float(reference_row[field]),
                    float(resumed_row[field]),
                    abs_tol=absolute_tolerance,
                    rel_tol=relative_tolerance,
                )
                for field in numeric_fields
            )
        ):
            raise EvolutionTrainError(
                f"resumed loss trajectory differs at step {reference_row['global_step']}"
            )
    return {
        "status": "passed",
        "resume_after_step": resume_after_step,
        "compared_steps": len(reference) - resume_after_step,
        "absolute_tolerance": absolute_tolerance,
        "relative_tolerance": relative_tolerance,
        "compared_fields": list(numeric_fields),
        "cuda_bitwise_trajectory_required": False,
    }


def _atomic_json(path: Path, value: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    descriptor, temporary_name = tempfile.mkstemp(
        prefix=f".{path.name}.tmp-",
        dir=path.parent,
    )
    temporary = Path(temporary_name)
    try:
        with os.fdopen(descriptor, "w", encoding="utf-8") as handle:
            json.dump(value, handle, ensure_ascii=False, indent=2, sort_keys=True)
            handle.write("\n")
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(temporary, path)
    finally:
        temporary.unlink(missing_ok=True)


def _code_revision(repository_root: Path) -> str:
    try:
        return subprocess.check_output(
            ["git", "rev-parse", "HEAD"],
            cwd=repository_root,
            text=True,
            stderr=subprocess.DEVNULL,
        ).strip()
    except (OSError, subprocess.CalledProcessError) as exc:
        raise EvolutionTrainError("training requires a Git code revision") from exc


def _seed_everything(torch: Any, seed: int) -> None:
    random.seed(seed)
    np.random.seed(seed)
    torch.manual_seed(seed)
    if torch.cuda.is_available():
        torch.cuda.manual_seed_all(seed)


def _checkpoint_state(
    torch: Any,
    *,
    profile: NowcastNetTrainingRunProfile,
    code_revision: str,
    data_track: str,
    sample_index_sha256: str,
    batch_size: int,
    precision: str,
    global_step: int,
    model: Any,
    optimizer: Any,
    scheduler: Any,
) -> dict[str, Any]:
    state = {
        "schema_version": "rainpulse.nowcastnet-evolution-checkpoint/1.0",
        "created_at": datetime.now(UTC).isoformat(),
        "global_step": global_step,
        "stage": "evolution",
        "profile_version": profile.profile_version,
        "profile_sha256": profile.profile_sha256,
        "code_revision": code_revision,
        "sample_index_sha256": sample_index_sha256,
        "data_track": data_track,
        "batch_size": batch_size,
        "precision": precision,
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
    }
    state["model_state_sha256"] = _state_fingerprint(
        torch,
        state["model_state_dict"],
    )
    state["optimizer_state_sha256"] = _state_fingerprint(
        torch,
        state["optimizer_state_dict"],
    )
    state["scheduler_state_sha256"] = _state_fingerprint(
        torch,
        state["scheduler_state_dict"],
    )
    state["random_state_sha256"] = _state_fingerprint(
        torch,
        {
            "python": state["python_random_state"],
            "numpy": state["numpy_random_state"],
            "torch": state["torch_random_state"],
            "cuda": state["cuda_random_state"],
        },
    )
    return state


def _write_checkpoint(
    torch: Any,
    *,
    output_dir: Path,
    state: dict[str, Any],
) -> dict[str, Any]:
    global_step = int(state["global_step"])
    relative = Path("checkpoints") / f"evolution-step-{global_step:09d}.pt"
    target = output_dir / relative
    target.parent.mkdir(parents=True, exist_ok=True)
    if target.exists():
        raise EvolutionTrainError(f"checkpoint already exists at step {global_step}")
    descriptor, temporary_name = tempfile.mkstemp(
        prefix=f".{target.name}.tmp-",
        dir=target.parent,
    )
    os.close(descriptor)
    temporary = Path(temporary_name)
    try:
        torch.save(state, temporary)
        loaded = torch.load(temporary, map_location="cpu", weights_only=False)
        if (
            loaded.get("global_step") != global_step
            or loaded.get("profile_sha256") != state["profile_sha256"]
            or loaded.get("code_revision") != state["code_revision"]
            or _state_fingerprint(torch, loaded["model_state_dict"])
            != state["model_state_sha256"]
            or _state_fingerprint(torch, loaded["optimizer_state_dict"])
            != state["optimizer_state_sha256"]
            or _state_fingerprint(torch, loaded["scheduler_state_dict"])
            != state["scheduler_state_sha256"]
            or _state_fingerprint(
                torch,
                {
                    "python": loaded["python_random_state"],
                    "numpy": loaded["numpy_random_state"],
                    "torch": loaded["torch_random_state"],
                    "cuda": loaded["cuda_random_state"],
                },
            )
            != state["random_state_sha256"]
        ):
            raise EvolutionTrainError("checkpoint load-back identity differs")
        os.replace(temporary, target)
    finally:
        temporary.unlink(missing_ok=True)
    checkpoint_sha256 = _sha256(target)
    latest = {
        "schema_version": "1.0",
        "global_step": global_step,
        "path": relative.as_posix(),
        "sha256": checkpoint_sha256,
    }
    _atomic_json(output_dir / "LATEST.json", latest)
    return latest


def _latest_checkpoint(output_dir: Path) -> tuple[Path, dict[str, Any]]:
    try:
        latest = json.loads((output_dir / "LATEST.json").read_text(encoding="utf-8"))
        relative = Path(str(latest["path"]))
    except (KeyError, OSError, json.JSONDecodeError) as exc:
        raise EvolutionTrainError(f"cannot read latest checkpoint marker: {exc}") from exc
    if relative.is_absolute() or ".." in relative.parts:
        raise EvolutionTrainError("latest checkpoint path is unsafe")
    checkpoint = (output_dir / relative).resolve()
    if not checkpoint.is_relative_to(output_dir.resolve()) or not checkpoint.is_file():
        raise EvolutionTrainError("latest checkpoint is missing or escapes the run")
    if _sha256(checkpoint) != str(latest.get("sha256")):
        raise EvolutionTrainError("latest checkpoint SHA-256 differs")
    return checkpoint, latest


def _restore_checkpoint(
    torch: Any,
    *,
    output_dir: Path,
    profile: NowcastNetTrainingRunProfile,
    code_revision: str,
    sample_index_sha256: str,
    batch_size: int,
    precision: str,
    model: Any,
    optimizer: Any,
    scheduler: Any,
) -> tuple[int, dict[str, bool]]:
    checkpoint_path, latest = _latest_checkpoint(output_dir)
    state = torch.load(checkpoint_path, map_location="cpu", weights_only=False)
    if (
        state.get("schema_version") != "rainpulse.nowcastnet-evolution-checkpoint/1.0"
        or state.get("stage") != "evolution"
        or state.get("profile_sha256") != profile.profile_sha256
        or state.get("code_revision") != code_revision
        or state.get("sample_index_sha256") != sample_index_sha256
        or state.get("data_track") != profile.foundation.name
        or int(state.get("batch_size", -1)) != batch_size
        or state.get("precision") != precision
        or int(state.get("global_step", -1)) != int(latest["global_step"])
    ):
        raise EvolutionTrainError("checkpoint contract differs from the requested run")
    model.load_state_dict(state["model_state_dict"], strict=True)
    optimizer.load_state_dict(state["optimizer_state_dict"])
    scheduler.load_state_dict(state["scheduler_state_dict"])
    random.setstate(state["python_random_state"])
    np.random.set_state(state["numpy_random_state"])
    torch.set_rng_state(state["torch_random_state"])
    if torch.cuda.is_available() and state["cuda_random_state"] is not None:
        torch.cuda.set_rng_state_all(state["cuda_random_state"])
    if (
        _state_fingerprint(torch, model.state_dict()) != state["model_state_sha256"]
        or _state_fingerprint(torch, optimizer.state_dict())
        != state["optimizer_state_sha256"]
        or _state_fingerprint(torch, scheduler.state_dict())
        != state["scheduler_state_sha256"]
    ):
        raise EvolutionTrainError("restored model, optimizer, or scheduler state differs")
    restored_random_sha256 = _state_fingerprint(
        torch,
        {
            "python": random.getstate(),
            "numpy": np.random.get_state(),
            "torch": torch.get_rng_state(),
            "cuda": torch.cuda.get_rng_state_all() if torch.cuda.is_available() else None,
        },
    )
    if restored_random_sha256 != state["random_state_sha256"]:
        raise EvolutionTrainError("restored random state differs")
    return int(state["global_step"]), {
        "checkpoint_state_exact": True,
        "random_state_exact": True,
    }


def _append_metric(path: Path, row: dict[str, Any]) -> None:
    with path.open("a", encoding="utf-8") as handle:
        handle.write(json.dumps(row, ensure_ascii=False, sort_keys=True) + "\n")
        handle.flush()
        os.fsync(handle.fileno())


def run_evolution_training(
    *,
    profile_path: Path,
    repository_root: Path,
    data_root: Path,
    output_dir: Path,
    device_name: str,
    batch_size: int,
    precision: str,
    stop_after_steps: int,
    checkpoint_every_steps: int,
    resume: bool,
) -> dict[str, Any]:
    try:
        import torch
    except ImportError as exc:
        raise EvolutionTrainError("PyTorch is required for evolution training") from exc
    from .evolution import EvolutionNetwork, evolution_loss

    profile = load_nowcastnet_training_run_profile(
        profile_path,
        repository_root=repository_root,
    )
    track = profile.foundation
    code_revision = _code_revision(repository_root)
    if device_name == "cuda" and not torch.cuda.is_available():
        raise EvolutionTrainError("CUDA was requested but is unavailable")
    if batch_size < 1 or batch_size > profile.evolution.global_batch_size:
        raise EvolutionTrainError("training batch size is outside the frozen global batch")
    if precision not in {"fp32", "bf16"}:
        raise EvolutionTrainError("training precision must be fp32 or bf16")
    if (
        stop_after_steps < 1
        or stop_after_steps > profile.evolution.total_steps
        or checkpoint_every_steps < 1
    ):
        raise EvolutionTrainError("training stop or checkpoint interval is invalid")

    dataset = MRMSZarrTrainingDataset(
        data_root,
        expected_sample_index_sha256=profile.sample_index_sha256,
        expected_sample_count=track.expected_sample_count,
        expected_crop_size=track.model_crop_size,
        input_frames=profile.sequence[0],
        target_frames=profile.sequence[1],
        maximum_open_shards=64,
    )
    metrics_path = output_dir / "metrics.jsonl"
    manifest_path = output_dir / "run-manifest.json"
    if resume:
        if not manifest_path.is_file() or not metrics_path.is_file():
            raise EvolutionTrainError("resume run manifest or metrics are missing")
        manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
        if (
            manifest.get("profile_sha256") != profile.profile_sha256
            or manifest.get("code_revision") != code_revision
            or manifest.get("sample_index_sha256") != profile.sample_index_sha256
            or manifest.get("batch_size") != batch_size
            or manifest.get("precision") != precision
        ):
            raise EvolutionTrainError("resume run manifest differs")
    else:
        if output_dir.exists() and any(output_dir.iterdir()):
            raise EvolutionTrainError("new training output directory is not empty")
        output_dir.mkdir(parents=True, exist_ok=True)
        manifest = {
            "schema_version": "1.0",
            "created_at": datetime.now(UTC).isoformat(),
            "stage": "evolution",
            "profile_version": profile.profile_version,
            "profile_sha256": profile.profile_sha256,
            "code_revision": code_revision,
            "sample_index_sha256": profile.sample_index_sha256,
            "data_track": track.name,
            "batch_size": batch_size,
            "precision": precision,
            "run_seed": profile.run_seed,
            "sampler": "sha256(run_seed:global_step)-numpy-choice-without-replacement",
            "operational_eligible": False,
        }
        _atomic_json(manifest_path, manifest)
        metrics_path.touch(exist_ok=False)

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
    global_step = 0
    resume_validation = {
        "checkpoint_state_exact": not resume,
        "random_state_exact": not resume,
    }
    if resume:
        global_step, resume_validation = _restore_checkpoint(
            torch,
            output_dir=output_dir,
            profile=profile,
            code_revision=code_revision,
            sample_index_sha256=profile.sample_index_sha256,
            batch_size=batch_size,
            precision=precision,
            model=model,
            optimizer=optimizer,
            scheduler=scheduler,
        )
        existing_metrics = _read_metrics(metrics_path)
        if len(existing_metrics) != global_step:
            raise EvolutionTrainError("resume metrics do not end at the checkpoint step")
    if stop_after_steps <= global_step:
        raise EvolutionTrainError("training stop step is not after the current checkpoint")

    if device_name == "cuda":
        torch.cuda.reset_peak_memory_stats()
    invocation_started = time.perf_counter()
    latest: dict[str, Any] | None = None
    while global_step < stop_after_steps:
        step_started = time.perf_counter()
        indices = deterministic_batch_indices(
            dataset_size=len(dataset),
            batch_size=batch_size,
            run_seed=profile.run_seed,
            global_step=global_step,
        )
        samples = [dataset[index] for index in indices]
        inputs = torch.from_numpy(
            np.stack([sample.input_rate_mm_h for sample in samples])
        ).to(device_name)
        targets = torch.from_numpy(
            np.stack([sample.target_rate_mm_h for sample in samples])
        ).to(device_name)
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
            raise EvolutionTrainError(f"non-finite loss before step {global_step + 1}")
        loss.total.backward()
        gradient_norm = torch.nn.utils.clip_grad_norm_(
            model.parameters(),
            max_norm=profile.evolution.gradient_clip_norm,
        )
        if not bool(torch.isfinite(gradient_norm)):
            raise EvolutionTrainError(f"non-finite gradient before step {global_step + 1}")
        optimizer.step()
        scheduler.step()
        global_step += 1
        if device_name == "cuda":
            torch.cuda.synchronize()
        metric = {
            "global_step": global_step,
            "batch_indices_sha256": _canonical_sha256(indices),
            "loss_total": float(loss.total.detach().float().cpu()),
            "loss_accumulation": float(loss.accumulation.detach().float().cpu()),
            "loss_motion_regularization": float(
                loss.motion_regularization.detach().float().cpu()
            ),
            "gradient_norm_before_clip": float(gradient_norm.detach().float().cpu()),
            "learning_rate": float(optimizer.param_groups[0]["lr"]),
            "duration_seconds": time.perf_counter() - step_started,
        }
        _append_metric(metrics_path, metric)
        if global_step % checkpoint_every_steps == 0 or global_step == stop_after_steps:
            latest = _write_checkpoint(
                torch,
                output_dir=output_dir,
                state=_checkpoint_state(
                    torch,
                    profile=profile,
                    code_revision=code_revision,
                    data_track=track.name,
                    sample_index_sha256=profile.sample_index_sha256,
                    batch_size=batch_size,
                    precision=precision,
                    global_step=global_step,
                    model=model,
                    optimizer=optimizer,
                    scheduler=scheduler,
                ),
            )

    if latest is None:
        _, latest = _latest_checkpoint(output_dir)
    gpu = torch.cuda.get_device_properties(0) if device_name == "cuda" else None
    result = {
        "schema_version": "1.0",
        "status": "passed",
        "finished_at": datetime.now(UTC).isoformat(),
        "profile_sha256": profile.profile_sha256,
        "code_revision": code_revision,
        "sample_index_sha256": profile.sample_index_sha256,
        "global_step": global_step,
        "batch_size": batch_size,
        "precision": precision,
        "resumed": resume,
        "resume_validation": resume_validation,
        "invocation_duration_seconds": time.perf_counter() - invocation_started,
        "latest_checkpoint": latest,
        "metrics_sha256": _sha256(metrics_path),
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
    _atomic_json(output_dir / "training-report.json", result)
    return result


def compare_evolution_runs(
    reference_dir: Path,
    resumed_dir: Path,
    *,
    resume_after_step: int,
    absolute_tolerance: float = 0.05,
    relative_tolerance: float = 0.01,
) -> dict[str, Any]:
    try:
        import torch
    except ImportError as exc:
        raise EvolutionTrainError("PyTorch is required for checkpoint comparison") from exc
    metric_result = compare_resume_metrics(
        reference_dir / "metrics.jsonl",
        resumed_dir / "metrics.jsonl",
        resume_after_step=resume_after_step,
        absolute_tolerance=absolute_tolerance,
        relative_tolerance=relative_tolerance,
    )
    reference_path, reference_latest = _latest_checkpoint(reference_dir)
    resumed_path, resumed_latest = _latest_checkpoint(resumed_dir)
    if reference_latest["global_step"] != resumed_latest["global_step"]:
        raise EvolutionTrainError("final checkpoint steps differ")
    reference = torch.load(reference_path, map_location="cpu", weights_only=False)
    resumed = torch.load(resumed_path, map_location="cpu", weights_only=False)
    for key in (
        "global_step",
        "profile_sha256",
        "code_revision",
        "sample_index_sha256",
        "data_track",
        "batch_size",
        "precision",
    ):
        if reference[key] != resumed[key]:
            raise EvolutionTrainError(f"final checkpoint identity differs: {key}")
    for state in (reference, resumed):
        if (
            _state_fingerprint(torch, state["model_state_dict"])
            != state["model_state_sha256"]
            or _state_fingerprint(torch, state["optimizer_state_dict"])
            != state["optimizer_state_sha256"]
            or _state_fingerprint(torch, state["scheduler_state_dict"])
            != state["scheduler_state_sha256"]
        ):
            raise EvolutionTrainError("final checkpoint state fingerprint differs")
    return {
        **metric_result,
        "global_step": int(reference["global_step"]),
        "checkpoint_serialization_fingerprints_verified": True,
        "independent_cuda_runs_bitwise_equal": False,
        "nondeterministic_operator": "grid_sampler_2d_backward_cuda",
    }


def _build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Train the RainPulse evolution network")
    parser.add_argument("--profile", type=Path, required=True)
    parser.add_argument("--repository-root", type=Path, required=True)
    parser.add_argument("--data-root", type=Path, required=True)
    parser.add_argument("--output-dir", type=Path, required=True)
    parser.add_argument("--device", choices=("cpu", "cuda"), default="cuda")
    parser.add_argument("--batch-size", type=int, default=16)
    parser.add_argument("--precision", choices=("fp32", "bf16"), default="bf16")
    parser.add_argument("--stop-after-steps", type=int, required=True)
    parser.add_argument("--checkpoint-every-steps", type=int, default=100)
    parser.add_argument("--resume", action="store_true")
    return parser


def main() -> int:
    args = _build_parser().parse_args()
    result = run_evolution_training(
        profile_path=args.profile,
        repository_root=args.repository_root,
        data_root=args.data_root,
        output_dir=args.output_dir,
        device_name=args.device,
        batch_size=args.batch_size,
        precision=args.precision,
        stop_after_steps=args.stop_after_steps,
        checkpoint_every_steps=args.checkpoint_every_steps,
        resume=args.resume,
    )
    print(json.dumps(result, ensure_ascii=False, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
