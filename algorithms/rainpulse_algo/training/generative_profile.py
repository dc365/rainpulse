from __future__ import annotations

import hashlib
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import yaml


class GenerativeTrainingProfileError(ValueError):
    """Raised when the frozen generative-training contract differs."""


@dataclass(frozen=True)
class GenerativeTrainingProfile:
    profile_version: str
    profile_sha256: str
    evolution_profile_sha256: str
    completed_pretraining_step: int
    stage_b_smoke_minimum_step: int
    input_frames: int
    target_frames: int
    context_frames: int
    base_channels: int
    rain_rate_cap_mm_h: float
    ensemble_members: int
    adversarial_weight: float
    pool_weight: float
    pool_kernel_size: int
    pool_stride: int
    weight_cap: float
    global_batch_size: int
    generator_learning_rate: float
    discriminator_learning_rate: float
    total_steps: int
    default_precision: str
    gradient_clip_norm: float
    run_seed: int


def _resolve_local(repository_root: Path, value: str) -> Path:
    relative = Path(value)
    if relative.is_absolute() or ".." in relative.parts:
        raise GenerativeTrainingProfileError("generative references must be repository-local")
    root = repository_root.resolve()
    resolved = (root / relative).resolve()
    if not resolved.is_relative_to(root):
        raise GenerativeTrainingProfileError("generative reference escapes the repository")
    return resolved


def _verify_reference(repository_root: Path, reference: dict[str, Any]) -> str:
    expected = str(reference["sha256"])
    path = _resolve_local(repository_root, str(reference["path"]))
    try:
        actual = hashlib.sha256(path.read_bytes()).hexdigest()
    except OSError as exc:
        raise GenerativeTrainingProfileError(f"cannot read generative reference: {exc}") from exc
    if actual != expected:
        raise GenerativeTrainingProfileError(f"generative reference SHA-256 differs: {path.name}")
    return actual


def load_generative_training_profile(
    path: Path,
    *,
    repository_root: Path,
) -> GenerativeTrainingProfile:
    try:
        payload = path.read_bytes()
        raw = yaml.safe_load(payload)
        provenance = raw["provenance"]
        frozen = raw["frozen_evolution"]
        architecture = raw["architecture"]
        generator = architecture["generator"]
        projector = architecture["noise_projector"]
        discriminator = architecture["temporal_discriminator"]
        loss = raw["loss"]
        adversarial = loss["adversarial"]
        pool = loss["pool_regularization"]
        optimization = raw["optimization"]
        reproducibility = raw["reproducibility"]
    except (KeyError, OSError, TypeError, yaml.YAMLError) as exc:
        raise GenerativeTrainingProfileError(
            f"cannot load generative training profile {path}: {exc}"
        ) from exc

    _verify_reference(repository_root, provenance["upstream_license"])
    evolution_profile_sha256 = _verify_reference(repository_root, frozen["run_profile"])
    if (
        raw.get("schema_version") != "1.0"
        or raw.get("profile_version") != "nowcastnet-mrms-generative-v1"
        or raw.get("lifecycle") != "offline_training"
        or raw.get("operational_eligible") is not False
        or provenance.get("paper_doi") != "10.1038/s41586-023-06184-4"
        or provenance.get("generator_source") != "official_mit_inference_capsule"
        or provenance.get("discriminator_source")
        != "paper_methods_and_extended_data_figure_1c"
        or provenance.get("official_training_source_published") is not False
        or provenance.get("reconstruction_note")
        != "rainpulse_explicit_reconstruction_of_unpublished_training_code"
        or frozen.get("checkpoint_schema")
        != "rainpulse.nowcastnet-evolution-checkpoint/1.0"
        or frozen.get("require_completed_pretraining") is not True
        or int(frozen["completed_pretraining_step"]) != 300000
        or int(frozen["stage_b_smoke_minimum_step"]) != 1000
        or frozen.get("detach_from_generative_gradient") is not True
        or int(architecture["input_frames"]) != 9
        or int(architecture["target_frames"]) != 20
        or int(architecture["discriminator_context_frames"]) != 4
        or int(architecture["base_channels"]) != 32
        or float(architecture["rain_rate_cap_mm_h"]) != 128.0
        or architecture.get("spectral_normalization") is not True
        or generator.get("evolution_normalization") != "divide_by_128"
        or generator.get("decoder_conditioning") != "spade"
        or generator.get("upsampling") != "nearest"
        or projector.get("distribution") != "standard_normal"
        or int(projector["latent_channels"]) != 32
        or int(projector["latent_grid_divisor"]) != 32
        or int(projector["projected_grid_divisor"]) != 8
        or int(projector["channel_to_space_factor"]) != 4
        or discriminator.get("spatial_branch")
        != "conv2d_channels_64_kernel_9_stride_2"
        or discriminator.get("short_branch")
        != "conv3d_channels_4_kernel_4x9x9_stride_1x2x2"
        or discriminator.get("long_branch")
        != "conv3d_channels_8_kernel_20x9x9_stride_1x2x2"
        or int(discriminator["flattened_channels"]) != 188
        or list(discriminator["residual_channels"]) != [128, 256, 512, 512]
        or discriminator.get("downsampling")
        != "bilinear_before_first_three_residual_blocks"
        or discriminator.get("output") != "patch_logits"
        or int(loss["ensemble_members"]) != 4
        or adversarial.get("type") != "binary_cross_entropy_with_logits"
        or float(adversarial["beta"]) != 6.0
        or pool.get("aggregation") != "ensemble_mean_after_member_pooling"
        or pool.get("operator") != "max_pool_2d"
        or int(pool["kernel_size"]) != 5
        or int(pool["stride"]) != 2
        or pool.get("weighted_l1_formula") != "min(24,1+pooled_target_mm_h)"
        or float(pool["weight_cap"]) != 24.0
        or float(pool["gamma"]) != 20.0
        or optimization.get("optimizer") != "Adam"
        or int(optimization["global_batch_size"]) != 16
        or float(optimization["generator_learning_rate"]) != 0.00003
        or float(optimization["discriminator_learning_rate"]) != 0.00003
        or int(optimization["total_steps"]) != 500000
        or optimization.get("default_precision") != "bf16"
        or float(optimization["gradient_clip_norm"]) != 1.0
        or optimization.get("update_order") != "discriminator_then_generator"
        or int(reproducibility["run_seed"]) != 2026083003
        or reproducibility.get("deterministic_sample_order") is not True
        or reproducibility.get("checkpoint_after_complete_optimizer_pair") is not True
        or reproducibility.get("exact_state_resume_required") is not True
    ):
        raise GenerativeTrainingProfileError("generative profile differs from frozen v1")

    return GenerativeTrainingProfile(
        profile_version=str(raw["profile_version"]),
        profile_sha256=hashlib.sha256(payload).hexdigest(),
        evolution_profile_sha256=evolution_profile_sha256,
        completed_pretraining_step=int(frozen["completed_pretraining_step"]),
        stage_b_smoke_minimum_step=int(frozen["stage_b_smoke_minimum_step"]),
        input_frames=int(architecture["input_frames"]),
        target_frames=int(architecture["target_frames"]),
        context_frames=int(architecture["discriminator_context_frames"]),
        base_channels=int(architecture["base_channels"]),
        rain_rate_cap_mm_h=float(architecture["rain_rate_cap_mm_h"]),
        ensemble_members=int(loss["ensemble_members"]),
        adversarial_weight=float(adversarial["beta"]),
        pool_weight=float(pool["gamma"]),
        pool_kernel_size=int(pool["kernel_size"]),
        pool_stride=int(pool["stride"]),
        weight_cap=float(pool["weight_cap"]),
        global_batch_size=int(optimization["global_batch_size"]),
        generator_learning_rate=float(optimization["generator_learning_rate"]),
        discriminator_learning_rate=float(optimization["discriminator_learning_rate"]),
        total_steps=int(optimization["total_steps"]),
        default_precision=str(optimization["default_precision"]),
        gradient_clip_norm=float(optimization["gradient_clip_norm"]),
        run_seed=int(reproducibility["run_seed"]),
    )
