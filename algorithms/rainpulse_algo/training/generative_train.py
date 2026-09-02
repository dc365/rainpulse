from __future__ import annotations

import argparse
import json
import math
import os
import platform
import random
import signal
import tempfile
import threading
import time
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

import numpy as np

from .data import MRMSZarrTrainingDataset
from .evolution_train import (
    _atomic_json,
    _canonical_sha256,
    _code_revision,
    _seed_everything,
    _sha256,
    _state_fingerprint,
    deterministic_batch_indices,
)
from .generative_profile import GenerativeTrainingProfile, load_generative_training_profile
from .profile import load_nowcastnet_training_run_profile


class GenerativeTrainError(RuntimeError):
    """Raised when continuous generative training or recovery is unsafe."""


def _open_generative_training_dataset(
    data_root: Path,
    *,
    profile: GenerativeTrainingProfile,
    evolution_profile: Any,
) -> MRMSZarrTrainingDataset:
    """Open the dataset with the exact identity frozen by the evolution profile."""

    track = evolution_profile.foundation
    return MRMSZarrTrainingDataset(
        data_root,
        expected_sample_index_sha256=evolution_profile.sample_index_sha256,
        expected_sample_count=track.expected_sample_count,
        expected_crop_size=track.model_crop_size,
        expected_shard_count=track.expected_shard_count,
        dataset_contract=track.dataset_contract,
        expected_dataset_version=track.expected_dataset_version,
        expected_profile_sha256=track.expected_profile_sha256,
        expected_plan_id=track.expected_plan_id,
        require_validation_report=track.require_validation_report,
        input_frames=profile.input_frames,
        target_frames=profile.target_frames,
        maximum_open_shards=64,
    )


def _read_metrics(path: Path) -> list[dict[str, Any]]:
    try:
        rows = [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines() if line]
    except (OSError, json.JSONDecodeError) as exc:
        raise GenerativeTrainError(f"cannot read generative metrics: {exc}") from exc
    if [int(row.get("global_step", -1)) for row in rows] != list(range(1, len(rows) + 1)):
        raise GenerativeTrainError("generative metric steps are not contiguous")
    return rows


def compare_generative_metrics(
    reference_path: Path,
    resumed_path: Path,
    *,
    resume_after_step: int,
    absolute_tolerance: float,
    relative_tolerance: float,
) -> dict[str, Any]:
    if resume_after_step < 1 or absolute_tolerance < 0.0 or relative_tolerance < 0.0:
        raise GenerativeTrainError("generative resume comparison boundary is invalid")
    reference = _read_metrics(reference_path)
    resumed = _read_metrics(resumed_path)
    if len(reference) != len(resumed) or len(reference) <= resume_after_step:
        raise GenerativeTrainError("generative metric lengths differ or have no resumed steps")
    numeric_fields = (
        "generator_loss_total",
        "generator_loss_adversarial",
        "generator_loss_pool_regularization",
        "discriminator_loss_total",
    )
    for reference_row, resumed_row in zip(
        reference[resume_after_step:],
        resumed[resume_after_step:],
        strict=True,
    ):
        if (
            reference_row["global_step"] != resumed_row["global_step"]
            or reference_row["batch_indices_sha256"] != resumed_row["batch_indices_sha256"]
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
            raise GenerativeTrainError(
                f"resumed generative trajectory differs at step {reference_row['global_step']}"
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


def _checkpoint_state(
    torch: Any,
    *,
    profile: GenerativeTrainingProfile,
    code_revision: str,
    sample_index_sha256: str,
    evolution_checkpoint_step: int,
    evolution_checkpoint_sha256: str,
    batch_size: int,
    precision: str,
    stage_b_smoke: bool,
    global_step: int,
    evolution: Any,
    generator: Any,
    discriminator: Any,
    generator_optimizer: Any,
    discriminator_optimizer: Any,
) -> dict[str, Any]:
    state = {
        "schema_version": "rainpulse.nowcastnet-generative-checkpoint/2.0",
        "created_at": datetime.now(UTC).isoformat(),
        "global_step": global_step,
        "stage": "generative",
        "profile_version": profile.profile_version,
        "profile_sha256": profile.profile_sha256,
        "code_revision": code_revision,
        "sample_index_sha256": sample_index_sha256,
        "evolution_checkpoint_step": evolution_checkpoint_step,
        "evolution_checkpoint_sha256": evolution_checkpoint_sha256,
        "evolution_pretraining_complete": (
            evolution_checkpoint_step == profile.completed_pretraining_step
        ),
        "stage_b_smoke": stage_b_smoke,
        "batch_size": batch_size,
        "ensemble_members": profile.ensemble_members,
        "precision": precision,
        "evolution_state_dict": evolution.state_dict(),
        "generator_state_dict": generator.state_dict(),
        "discriminator_state_dict": discriminator.state_dict(),
        "generator_optimizer_state_dict": generator_optimizer.state_dict(),
        "discriminator_optimizer_state_dict": discriminator_optimizer.state_dict(),
        "generator_scheduler_state_dict": None,
        "discriminator_scheduler_state_dict": None,
        "python_random_state": random.getstate(),
        "numpy_random_state": np.random.get_state(),
        "torch_random_state": torch.get_rng_state(),
        "cuda_random_state": (
            torch.cuda.get_rng_state_all() if torch.cuda.is_available() else None
        ),
        "amp_scaler_state_dict": None,
    }
    for name in (
        "evolution_state",
        "generator_state",
        "discriminator_state",
        "generator_optimizer_state",
        "discriminator_optimizer_state",
    ):
        state[f"{name}_sha256"] = _state_fingerprint(
            torch,
            state[f"{name}_dict"],
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
    relative = Path("checkpoints") / f"generative-step-{global_step:09d}.pt"
    target = output_dir / relative
    target.parent.mkdir(parents=True, exist_ok=True)
    if target.exists():
        raise GenerativeTrainError(f"generative checkpoint exists at step {global_step}")
    descriptor, temporary_name = tempfile.mkstemp(prefix=f".{target.name}.tmp-", dir=target.parent)
    os.close(descriptor)
    temporary = Path(temporary_name)
    try:
        torch.save(state, temporary)
        loaded = torch.load(temporary, map_location="cpu", weights_only=False)
        if (
            loaded.get("global_step") != global_step
            or loaded.get("profile_sha256") != state["profile_sha256"]
            or loaded.get("code_revision") != state["code_revision"]
            or loaded.get("evolution_checkpoint_sha256") != state["evolution_checkpoint_sha256"]
            or any(
                _state_fingerprint(torch, loaded[f"{name}_dict"]) != state[f"{name}_sha256"]
                for name in (
                    "evolution_state",
                    "generator_state",
                    "discriminator_state",
                    "generator_optimizer_state",
                    "discriminator_optimizer_state",
                )
            )
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
            raise GenerativeTrainError("generative checkpoint load-back differs")
        os.replace(temporary, target)
    finally:
        temporary.unlink(missing_ok=True)
    latest = {
        "schema_version": "1.0",
        "global_step": global_step,
        "path": relative.as_posix(),
        "sha256": _sha256(target),
    }
    _atomic_json(output_dir / "LATEST.json", latest)
    return latest


def _checkpoint_step(path: Path) -> int:
    prefix = "generative-step-"
    if not path.name.startswith(prefix) or path.suffix != ".pt":
        raise GenerativeTrainError(f"unexpected generative checkpoint name: {path.name}")
    try:
        return int(path.stem.removeprefix(prefix))
    except ValueError as exc:
        raise GenerativeTrainError(f"unexpected generative checkpoint step: {path.name}") from exc


def _window_final_paths(output_dir: Path) -> set[str]:
    marker = output_dir / "window-finals.json"
    if not marker.exists():
        return set()
    try:
        payload = json.loads(marker.read_text(encoding="utf-8"))
        entries = payload["checkpoints"]
        if payload.get("schema_version") != "1.0" or not isinstance(entries, list):
            raise ValueError("window final marker contract differs")
        paths = {str(entry["path"]) for entry in entries}
    except (KeyError, OSError, TypeError, ValueError, json.JSONDecodeError) as exc:
        raise GenerativeTrainError(f"cannot read generative window finals: {exc}") from exc
    for value in paths:
        relative = Path(value)
        if relative.is_absolute() or ".." in relative.parts:
            raise GenerativeTrainError("generative window final path is unsafe")
    return paths


def _record_window_final(output_dir: Path, latest: dict[str, Any]) -> None:
    marker = output_dir / "window-finals.json"
    existing: list[dict[str, Any]] = []
    if marker.exists():
        try:
            payload = json.loads(marker.read_text(encoding="utf-8"))
            if payload.get("schema_version") != "1.0":
                raise ValueError("window final marker contract differs")
            existing = list(payload["checkpoints"])
        except (KeyError, OSError, TypeError, ValueError, json.JSONDecodeError) as exc:
            raise GenerativeTrainError(f"cannot update generative window finals: {exc}") from exc
    entry = {
        "global_step": int(latest["global_step"]),
        "path": str(latest["path"]),
        "sha256": str(latest["sha256"]),
    }
    by_step = {int(item["global_step"]): item for item in existing}
    by_step[entry["global_step"]] = entry
    _atomic_json(
        marker,
        {
            "schema_version": "1.0",
            "checkpoints": [by_step[step] for step in sorted(by_step)],
        },
    )


def _prune_checkpoints(
    output_dir: Path,
    *,
    latest: dict[str, Any],
    rolling_keep: int,
    milestone_interval_steps: int,
    preserve_window_final: bool,
) -> dict[str, int]:
    if rolling_keep < 1 or milestone_interval_steps < 1:
        raise GenerativeTrainError("generative checkpoint retention is invalid")
    checkpoint_dir = output_dir / "checkpoints"
    checkpoints = sorted(
        checkpoint_dir.glob("generative-step-*.pt"),
        key=_checkpoint_step,
    )
    protected = {str(latest["path"])}
    protected.update(
        path.relative_to(output_dir).as_posix() for path in checkpoints[-rolling_keep:]
    )
    protected.update(
        path.relative_to(output_dir).as_posix()
        for path in checkpoints
        if _checkpoint_step(path) % milestone_interval_steps == 0
    )
    if preserve_window_final:
        protected.update(_window_final_paths(output_dir))
    deleted = 0
    for path in checkpoints:
        if path.relative_to(output_dir).as_posix() not in protected:
            path.unlink()
            deleted += 1
    return {
        "retained": len(checkpoints) - deleted,
        "deleted": deleted,
    }


def _latest_checkpoint(output_dir: Path) -> tuple[Path, dict[str, Any]]:
    try:
        latest = json.loads((output_dir / "LATEST.json").read_text(encoding="utf-8"))
        relative = Path(str(latest["path"]))
    except (KeyError, OSError, json.JSONDecodeError) as exc:
        raise GenerativeTrainError(f"cannot read latest generative checkpoint: {exc}") from exc
    checkpoint = (output_dir / relative).resolve()
    if (
        relative.is_absolute()
        or ".." in relative.parts
        or not checkpoint.is_relative_to(output_dir.resolve())
        or not checkpoint.is_file()
        or _sha256(checkpoint) != str(latest.get("sha256"))
    ):
        raise GenerativeTrainError("latest generative checkpoint is unsafe or differs")
    return checkpoint, latest


def _restore_checkpoint(
    torch: Any,
    *,
    output_dir: Path,
    profile: GenerativeTrainingProfile,
    code_revision: str,
    sample_index_sha256: str,
    evolution_checkpoint_step: int,
    evolution_checkpoint_sha256: str,
    batch_size: int,
    precision: str,
    stage_b_smoke: bool,
    evolution: Any,
    generator: Any,
    discriminator: Any,
    generator_optimizer: Any,
    discriminator_optimizer: Any,
) -> tuple[int, dict[str, bool]]:
    checkpoint_path, latest = _latest_checkpoint(output_dir)
    state = torch.load(checkpoint_path, map_location="cpu", weights_only=False)
    if (
        state.get("schema_version") != "rainpulse.nowcastnet-generative-checkpoint/2.0"
        or state.get("stage") != "generative"
        or state.get("profile_sha256") != profile.profile_sha256
        or state.get("code_revision") != code_revision
        or state.get("sample_index_sha256") != sample_index_sha256
        or int(state.get("evolution_checkpoint_step", -1)) != evolution_checkpoint_step
        or state.get("evolution_checkpoint_sha256") != evolution_checkpoint_sha256
        or int(state.get("batch_size", -1)) != batch_size
        or int(state.get("ensemble_members", -1)) != profile.ensemble_members
        or state.get("precision") != precision
        or state.get("stage_b_smoke") is not stage_b_smoke
        or int(state.get("global_step", -1)) != int(latest["global_step"])
        or state.get("generator_scheduler_state_dict") is not None
        or state.get("discriminator_scheduler_state_dict") is not None
    ):
        raise GenerativeTrainError("generative checkpoint contract differs")
    evolution.load_state_dict(state["evolution_state_dict"], strict=True)
    generator.load_state_dict(state["generator_state_dict"], strict=True)
    discriminator.load_state_dict(state["discriminator_state_dict"], strict=True)
    generator_optimizer.load_state_dict(state["generator_optimizer_state_dict"])
    discriminator_optimizer.load_state_dict(state["discriminator_optimizer_state_dict"])
    random.setstate(state["python_random_state"])
    np.random.set_state(state["numpy_random_state"])
    torch.set_rng_state(state["torch_random_state"])
    if torch.cuda.is_available() and state["cuda_random_state"] is not None:
        torch.cuda.set_rng_state_all(state["cuda_random_state"])
    for name, value in (
        ("evolution_state", evolution.state_dict()),
        ("generator_state", generator.state_dict()),
        ("discriminator_state", discriminator.state_dict()),
        ("generator_optimizer_state", generator_optimizer.state_dict()),
        ("discriminator_optimizer_state", discriminator_optimizer.state_dict()),
    ):
        if _state_fingerprint(torch, value) != state[f"{name}_sha256"]:
            raise GenerativeTrainError(f"restored {name} differs")
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
        raise GenerativeTrainError("restored generative random state differs")
    return int(state["global_step"]), {
        "checkpoint_state_exact": True,
        "random_state_exact": True,
    }


def _append_metric(path: Path, row: dict[str, Any]) -> None:
    with path.open("a", encoding="utf-8") as handle:
        handle.write(json.dumps(row, ensure_ascii=False, sort_keys=True) + "\n")
        handle.flush()
        os.fsync(handle.fileno())


def _validate_evolution_checkpoint(
    torch: Any,
    *,
    path: Path,
    evolution_profile: Any,
    generative_profile: GenerativeTrainingProfile,
    stage_b_smoke: bool,
) -> tuple[dict[str, Any], int, str]:
    digest = _sha256(path)
    state = torch.load(path, map_location="cpu", weights_only=False)
    step = int(state.get("global_step", -1))
    minimum = (
        generative_profile.stage_b_smoke_minimum_step
        if stage_b_smoke
        else generative_profile.completed_pretraining_step
    )
    if (
        state.get("schema_version") != "rainpulse.nowcastnet-evolution-checkpoint/1.0"
        or state.get("stage") != "evolution"
        or state.get("profile_sha256") != evolution_profile.profile_sha256
        or state.get("sample_index_sha256") != evolution_profile.sample_index_sha256
        or state.get("data_track") != evolution_profile.foundation.name
        or step < minimum
        or step > generative_profile.completed_pretraining_step
        or (not stage_b_smoke and step != generative_profile.completed_pretraining_step)
    ):
        raise GenerativeTrainError("evolution checkpoint is ineligible for generative training")
    return state, step, digest


def run_generative_training(
    *,
    profile_path: Path,
    repository_root: Path,
    data_root: Path,
    evolution_checkpoint_path: Path,
    output_dir: Path,
    device_name: str,
    batch_size: int,
    precision: str,
    stop_after_steps: int,
    checkpoint_every_steps: int | None,
    checkpoint_every_seconds: int | None,
    rolling_checkpoint_keep: int | None,
    milestone_every_steps: int | None,
    preserve_window_final: bool | None,
    resume: bool,
    stage_b_smoke: bool,
) -> dict[str, Any]:
    try:
        import torch
    except ImportError as exc:
        raise GenerativeTrainError("PyTorch is required for generative training") from exc
    from .evolution import EvolutionNetwork, rollout_evolution
    from .generation import NowcastNetGenerator, TemporalDiscriminator, generative_train_step

    profile = load_generative_training_profile(profile_path, repository_root=repository_root)
    evolution_profile = load_nowcastnet_training_run_profile(
        profile.evolution_profile_path,
        repository_root=repository_root,
    )
    if evolution_profile.profile_sha256 != profile.evolution_profile_sha256:
        raise GenerativeTrainError("evolution and generative profile hashes differ")
    if device_name == "cuda" and not torch.cuda.is_available():
        raise GenerativeTrainError("CUDA was requested but is unavailable")
    if batch_size < 1 or batch_size > profile.global_batch_size:
        raise GenerativeTrainError("generative batch size is outside the frozen global batch")
    if precision not in {"fp32", "bf16"}:
        raise GenerativeTrainError("generative precision must be fp32 or bf16")
    if stop_after_steps < 1 or stop_after_steps > profile.total_steps:
        raise GenerativeTrainError("generative stop step is invalid")
    checkpoint_every_steps = (
        profile.checkpoint_maximum_interval_steps
        if checkpoint_every_steps is None
        else checkpoint_every_steps
    )
    checkpoint_every_seconds = (
        profile.checkpoint_maximum_interval_seconds
        if checkpoint_every_seconds is None
        else checkpoint_every_seconds
    )
    rolling_checkpoint_keep = (
        profile.checkpoint_rolling_keep
        if rolling_checkpoint_keep is None
        else rolling_checkpoint_keep
    )
    milestone_every_steps = (
        profile.checkpoint_milestone_interval_steps
        if milestone_every_steps is None
        else milestone_every_steps
    )
    preserve_window_final = (
        profile.checkpoint_preserve_window_final
        if preserve_window_final is None
        else preserve_window_final
    )
    if (
        min(
            checkpoint_every_steps,
            checkpoint_every_seconds,
            rolling_checkpoint_keep,
            milestone_every_steps,
        )
        < 1
    ):
        raise GenerativeTrainError("generative checkpoint interval is invalid")
    if not stage_b_smoke and (
        checkpoint_every_steps != profile.checkpoint_maximum_interval_steps
        or checkpoint_every_seconds != profile.checkpoint_maximum_interval_seconds
        or rolling_checkpoint_keep != profile.checkpoint_rolling_keep
        or milestone_every_steps != profile.checkpoint_milestone_interval_steps
        or preserve_window_final != profile.checkpoint_preserve_window_final
    ):
        raise GenerativeTrainError("formal generative checkpoint policy differs")

    evolution_state, evolution_step, evolution_sha256 = _validate_evolution_checkpoint(
        torch,
        path=evolution_checkpoint_path,
        evolution_profile=evolution_profile,
        generative_profile=profile,
        stage_b_smoke=stage_b_smoke,
    )
    dataset = _open_generative_training_dataset(
        data_root,
        profile=profile,
        evolution_profile=evolution_profile,
    )
    code_revision = _code_revision(repository_root)
    metrics_path = output_dir / "metrics.jsonl"
    manifest_path = output_dir / "run-manifest.json"
    manifest_identity = {
        "profile_sha256": profile.profile_sha256,
        "code_revision": code_revision,
        "sample_index_sha256": evolution_profile.sample_index_sha256,
        "dataset_contract": evolution_profile.foundation.dataset_contract,
        "dataset_version": evolution_profile.foundation.expected_dataset_version,
        "dataset_profile_sha256": evolution_profile.foundation.expected_profile_sha256,
        "dataset_plan_id": evolution_profile.foundation.expected_plan_id,
        "evolution_checkpoint_step": evolution_step,
        "evolution_checkpoint_sha256": evolution_sha256,
        "batch_size": batch_size,
        "ensemble_members": profile.ensemble_members,
        "precision": precision,
        "stage_b_smoke": stage_b_smoke,
        "checkpoint_every_steps": checkpoint_every_steps,
        "checkpoint_every_seconds": checkpoint_every_seconds,
        "rolling_checkpoint_keep": rolling_checkpoint_keep,
        "milestone_every_steps": milestone_every_steps,
        "preserve_window_final": preserve_window_final,
    }
    if resume:
        if not manifest_path.is_file() or not metrics_path.is_file():
            raise GenerativeTrainError("resume manifest or metrics are missing")
        manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
        if any(manifest.get(key) != value for key, value in manifest_identity.items()):
            raise GenerativeTrainError("generative resume manifest differs")
    else:
        if output_dir.exists() and any(output_dir.iterdir()):
            raise GenerativeTrainError("new generative output directory is not empty")
        output_dir.mkdir(parents=True, exist_ok=True)
        manifest = {
            "schema_version": "1.0",
            "created_at": datetime.now(UTC).isoformat(),
            "stage": "generative",
            "profile_version": profile.profile_version,
            **manifest_identity,
            "run_seed": profile.run_seed,
            "sampler": "sha256(run_seed:global_step)-numpy-choice-without-replacement",
            "optimizer_pair_order": "discriminator_then_generator",
            "discriminator_buffer_policy_during_generator_update": "training_mode_updates",
            "learning_rate_schedule": "constant_no_scheduler",
            "operational_eligible": False,
        }
        _atomic_json(manifest_path, manifest)
        metrics_path.touch(exist_ok=False)

    _seed_everything(torch, profile.run_seed)
    evolution = EvolutionNetwork(
        input_frames=profile.input_frames,
        target_frames=profile.target_frames,
        base_channels=evolution_profile.evolution.base_channels,
    ).to(device_name)
    generator = NowcastNetGenerator(
        input_frames=profile.input_frames,
        target_frames=profile.target_frames,
        base_channels=profile.base_channels,
        rain_rate_cap_mm_h=profile.rain_rate_cap_mm_h,
    ).to(device_name)
    discriminator = TemporalDiscriminator(
        context_frames=profile.context_frames,
        target_frames=profile.target_frames,
    ).to(device_name)
    evolution.load_state_dict(evolution_state["model_state_dict"], strict=True)
    evolution.eval().requires_grad_(False)
    generator.train()
    discriminator.train()
    generator_optimizer = torch.optim.Adam(
        generator.parameters(),
        lr=profile.generator_learning_rate,
    )
    discriminator_optimizer = torch.optim.Adam(
        discriminator.parameters(),
        lr=profile.discriminator_learning_rate,
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
            sample_index_sha256=evolution_profile.sample_index_sha256,
            evolution_checkpoint_step=evolution_step,
            evolution_checkpoint_sha256=evolution_sha256,
            batch_size=batch_size,
            precision=precision,
            stage_b_smoke=stage_b_smoke,
            evolution=evolution,
            generator=generator,
            discriminator=discriminator,
            generator_optimizer=generator_optimizer,
            discriminator_optimizer=discriminator_optimizer,
        )
        if len(_read_metrics(metrics_path)) != global_step:
            raise GenerativeTrainError("resume metrics do not end at checkpoint step")
    if stop_after_steps <= global_step:
        raise GenerativeTrainError("stop step is not after current generative checkpoint")

    stop_requested = False
    old_handlers: dict[int, Any] = {}
    if threading.current_thread() is threading.main_thread():

        def request_stop(_signum: int, _frame: Any) -> None:
            nonlocal stop_requested
            stop_requested = True

        for signum in (signal.SIGINT, signal.SIGTERM):
            old_handlers[signum] = signal.getsignal(signum)
            signal.signal(signum, request_stop)

    if device_name == "cuda":
        torch.cuda.reset_peak_memory_stats()
    invocation_started = time.perf_counter()
    last_checkpoint_at = invocation_started
    latest: dict[str, Any] | None = None

    def save_checkpoint() -> dict[str, Any]:
        nonlocal last_checkpoint_at, latest
        latest = _write_checkpoint(
            torch,
            output_dir=output_dir,
            state=_checkpoint_state(
                torch,
                profile=profile,
                code_revision=code_revision,
                sample_index_sha256=evolution_profile.sample_index_sha256,
                evolution_checkpoint_step=evolution_step,
                evolution_checkpoint_sha256=evolution_sha256,
                batch_size=batch_size,
                precision=precision,
                stage_b_smoke=stage_b_smoke,
                global_step=global_step,
                evolution=evolution,
                generator=generator,
                discriminator=discriminator,
                generator_optimizer=generator_optimizer,
                discriminator_optimizer=discriminator_optimizer,
            ),
        )
        last_checkpoint_at = time.perf_counter()
        _prune_checkpoints(
            output_dir,
            latest=latest,
            rolling_keep=rolling_checkpoint_keep,
            milestone_interval_steps=milestone_every_steps,
            preserve_window_final=preserve_window_final,
        )
        return latest

    try:
        while global_step < stop_after_steps and not stop_requested:
            step_started = time.perf_counter()
            indices = deterministic_batch_indices(
                dataset_size=len(dataset),
                batch_size=batch_size,
                run_seed=profile.run_seed,
                global_step=global_step,
            )
            samples = [dataset[index] for index in indices]
            inputs = torch.from_numpy(np.stack([sample.input_rate_mm_h for sample in samples])).to(
                device_name
            )
            targets = torch.from_numpy(
                np.stack([sample.target_rate_mm_h for sample in samples])
            ).to(device_name)
            autocast_enabled = device_name == "cuda" and precision == "bf16"
            with (
                torch.no_grad(),
                torch.autocast(
                    device_type=device_name,
                    dtype=torch.bfloat16 if precision == "bf16" else torch.float32,
                    enabled=autocast_enabled,
                ),
            ):
                intensity, motion = evolution(inputs)
                evolution_prediction = rollout_evolution(inputs, intensity, motion)
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
                    ensemble_members=profile.ensemble_members,
                    adversarial_weight=profile.adversarial_weight,
                    pool_weight=profile.pool_weight,
                    gradient_clip_norm=profile.gradient_clip_norm,
                )
            global_step += 1
            if device_name == "cuda":
                torch.cuda.synchronize()
            metric = {
                "global_step": global_step,
                "batch_indices_sha256": _canonical_sha256(indices),
                "generator_loss_total": float(result.generator.total.detach().float().cpu()),
                "generator_loss_adversarial": float(
                    result.generator.adversarial.detach().float().cpu()
                ),
                "generator_loss_pool_regularization": float(
                    result.generator.pool_regularization.detach().float().cpu()
                ),
                "discriminator_loss_total": float(
                    result.discriminator.total.detach().float().cpu()
                ),
                "discriminator_loss_real": float(result.discriminator.real.detach().float().cpu()),
                "discriminator_loss_fake": float(result.discriminator.fake.detach().float().cpu()),
                "generator_gradient_norm_before_clip": float(
                    result.generator_gradient_norm.float().cpu()
                ),
                "discriminator_gradient_norm_before_clip": float(
                    result.discriminator_gradient_norm.float().cpu()
                ),
                "generator_learning_rate": float(generator_optimizer.param_groups[0]["lr"]),
                "discriminator_learning_rate": float(discriminator_optimizer.param_groups[0]["lr"]),
                "duration_seconds": time.perf_counter() - step_started,
            }
            _append_metric(metrics_path, metric)
            if (
                global_step % checkpoint_every_steps == 0
                or time.perf_counter() - last_checkpoint_at >= checkpoint_every_seconds
                or global_step == stop_after_steps
                or stop_requested
            ):
                save_checkpoint()
        if latest is None or int(latest["global_step"]) != global_step:
            save_checkpoint()
    finally:
        for signum, handler in old_handlers.items():
            signal.signal(signum, handler)

    if latest is None:
        _, latest = _latest_checkpoint(output_dir)
    _record_window_final(output_dir, latest)
    retention = _prune_checkpoints(
        output_dir,
        latest=latest,
        rolling_keep=rolling_checkpoint_keep,
        milestone_interval_steps=milestone_every_steps,
        preserve_window_final=preserve_window_final,
    )
    gpu = torch.cuda.get_device_properties(0) if device_name == "cuda" else None
    report = {
        "schema_version": "1.0",
        "status": "stopped" if stop_requested else "passed",
        "finished_at": datetime.now(UTC).isoformat(),
        "profile_sha256": profile.profile_sha256,
        "code_revision": code_revision,
        "sample_index_sha256": evolution_profile.sample_index_sha256,
        "evolution_checkpoint_step": evolution_step,
        "evolution_checkpoint_sha256": evolution_sha256,
        "evolution_pretraining_complete": (evolution_step == profile.completed_pretraining_step),
        "stage_b_smoke": stage_b_smoke,
        "global_step": global_step,
        "batch_size": batch_size,
        "ensemble_members": profile.ensemble_members,
        "precision": precision,
        "resumed": resume,
        "resume_validation": resume_validation,
        "stop_requested": stop_requested,
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
        "checkpoint_policy": {
            "maximum_interval_steps": checkpoint_every_steps,
            "maximum_interval_seconds": checkpoint_every_seconds,
            "rolling_keep": rolling_checkpoint_keep,
            "milestone_interval_steps": milestone_every_steps,
            "preserve_window_final": preserve_window_final,
            **retention,
        },
        "full_generative_training_eligible": (
            evolution_step == profile.completed_pretraining_step and not stage_b_smoke
        ),
        "operational_eligible": False,
    }
    _atomic_json(output_dir / "training-report.json", report)
    return report


def compare_generative_runs(
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
        raise GenerativeTrainError("PyTorch is required for comparison") from exc
    metric_result = compare_generative_metrics(
        reference_dir / "metrics.jsonl",
        resumed_dir / "metrics.jsonl",
        resume_after_step=resume_after_step,
        absolute_tolerance=absolute_tolerance,
        relative_tolerance=relative_tolerance,
    )
    reference_path, reference_latest = _latest_checkpoint(reference_dir)
    resumed_path, resumed_latest = _latest_checkpoint(resumed_dir)
    if reference_latest["global_step"] != resumed_latest["global_step"]:
        raise GenerativeTrainError("final generative checkpoint steps differ")
    reference = torch.load(reference_path, map_location="cpu", weights_only=False)
    resumed = torch.load(resumed_path, map_location="cpu", weights_only=False)
    for key in (
        "global_step",
        "profile_sha256",
        "code_revision",
        "sample_index_sha256",
        "evolution_checkpoint_step",
        "evolution_checkpoint_sha256",
        "batch_size",
        "ensemble_members",
        "precision",
        "stage_b_smoke",
    ):
        if reference[key] != resumed[key]:
            raise GenerativeTrainError(f"final generative identity differs: {key}")
    for state in (reference, resumed):
        for name in (
            "evolution_state",
            "generator_state",
            "discriminator_state",
            "generator_optimizer_state",
            "discriminator_optimizer_state",
        ):
            if _state_fingerprint(torch, state[f"{name}_dict"]) != state[f"{name}_sha256"]:
                raise GenerativeTrainError(f"final {name} fingerprint differs")
    return {
        **metric_result,
        "global_step": int(reference_latest["global_step"]),
        "checkpoint_serialization_fingerprints_verified": True,
        "independent_cuda_runs_bitwise_equal": False,
    }


def _build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Train the RainPulse generative stage")
    subparsers = parser.add_subparsers(dest="command", required=True)
    train = subparsers.add_parser("train")
    train.add_argument("--profile", type=Path, required=True)
    train.add_argument("--repository-root", type=Path, required=True)
    train.add_argument("--data-root", type=Path, required=True)
    train.add_argument("--evolution-checkpoint", type=Path, required=True)
    train.add_argument("--output-dir", type=Path, required=True)
    train.add_argument("--device", choices=("cpu", "cuda"), default="cuda")
    train.add_argument("--batch-size", type=int, default=16)
    train.add_argument("--precision", choices=("fp32", "bf16"), default="bf16")
    train.add_argument("--stop-after-steps", type=int, required=True)
    train.add_argument("--checkpoint-every-steps", type=int)
    train.add_argument("--checkpoint-every-seconds", type=int)
    train.add_argument("--rolling-checkpoint-keep", type=int)
    train.add_argument("--milestone-every-steps", type=int)
    train.add_argument(
        "--no-preserve-window-final",
        action="store_true",
        help="stage-B-only override that does not retain invocation-final checkpoints",
    )
    train.add_argument("--resume", action="store_true")
    train.add_argument("--stage-b-smoke", action="store_true")

    compare = subparsers.add_parser("compare")
    compare.add_argument("--reference-dir", type=Path, required=True)
    compare.add_argument("--resumed-dir", type=Path, required=True)
    compare.add_argument("--resume-after-step", type=int, required=True)
    compare.add_argument("--absolute-tolerance", type=float, default=0.05)
    compare.add_argument("--relative-tolerance", type=float, default=0.01)
    return parser


def main() -> int:
    args = _build_parser().parse_args()
    if args.command == "compare":
        report = compare_generative_runs(
            args.reference_dir,
            args.resumed_dir,
            resume_after_step=args.resume_after_step,
            absolute_tolerance=args.absolute_tolerance,
            relative_tolerance=args.relative_tolerance,
        )
    else:
        report = run_generative_training(
            profile_path=args.profile,
            repository_root=args.repository_root,
            data_root=args.data_root,
            evolution_checkpoint_path=args.evolution_checkpoint,
            output_dir=args.output_dir,
            device_name=args.device,
            batch_size=args.batch_size,
            precision=args.precision,
            stop_after_steps=args.stop_after_steps,
            checkpoint_every_steps=args.checkpoint_every_steps,
            checkpoint_every_seconds=args.checkpoint_every_seconds,
            rolling_checkpoint_keep=args.rolling_checkpoint_keep,
            milestone_every_steps=args.milestone_every_steps,
            preserve_window_final=(False if args.no_preserve_window_final else None),
            resume=args.resume,
            stage_b_smoke=args.stage_b_smoke,
        )
    print(json.dumps(report, ensure_ascii=False, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
