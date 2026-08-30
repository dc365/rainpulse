from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import yaml


class NowcastNetTrainingRunError(ValueError):
    """Raised when the frozen offline-training run contract is inconsistent."""


@dataclass(frozen=True)
class TrainingDataTrack:
    name: str
    purpose: str
    data_root_env: str
    source_resolution_deg: float
    resolution_deg: float
    native_crop_size: int
    model_crop_size: int
    downsample_method: str
    expected_sample_count: int
    official_kernel_published: bool | None


@dataclass(frozen=True)
class EvolutionTrainingContract:
    base_channels: int
    weight_cap: float
    motion_regularization_lambda: float
    global_batch_size: int
    initial_learning_rate: float
    decay_step: int
    decayed_learning_rate: float
    total_steps: int
    default_precision: str
    gradient_clip_norm: float


@dataclass(frozen=True)
class NowcastNetTrainingRunProfile:
    profile_version: str
    profile_sha256: str
    sequence: tuple[int, int, int]
    cadence_minutes: int
    rain_rate_cap_mm_h: float
    sample_index_sha256: str
    foundation: TrainingDataTrack
    paper_conformance: TrainingDataTrack
    evolution: EvolutionTrainingContract
    run_seed: int

    def track(self, name: str) -> TrainingDataTrack:
        if name == self.foundation.name:
            return self.foundation
        if name == self.paper_conformance.name:
            return self.paper_conformance
        raise NowcastNetTrainingRunError(f"unknown training data track: {name}")


def _resolve_local(repository_root: Path, value: str) -> Path:
    relative = Path(value)
    if relative.is_absolute() or ".." in relative.parts:
        raise NowcastNetTrainingRunError("training references must remain repository-local")
    root = repository_root.resolve()
    resolved = (root / relative).resolve()
    if not resolved.is_relative_to(root):
        raise NowcastNetTrainingRunError("training reference escapes the repository")
    return resolved


def _sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def _load_track(name: str, raw: dict[str, Any]) -> TrainingDataTrack:
    return TrainingDataTrack(
        name=name,
        purpose=str(raw["purpose"]),
        data_root_env=str(raw["data_root_env"]),
        source_resolution_deg=float(raw["source_resolution_deg"]),
        resolution_deg=float(raw["resolution_deg"]),
        native_crop_size=int(raw["native_crop_size"]),
        model_crop_size=int(raw["model_crop_size"]),
        downsample_method=str(raw["downsample_method"]),
        expected_sample_count=int(raw["expected_sample_count"]),
        official_kernel_published=(
            bool(raw["official_kernel_published"])
            if "official_kernel_published" in raw
            else None
        ),
    )


def load_nowcastnet_training_run_profile(
    path: Path,
    *,
    repository_root: Path,
) -> NowcastNetTrainingRunProfile:
    try:
        payload = path.read_bytes()
        raw = yaml.safe_load(payload)
        if not isinstance(raw, dict):
            raise NowcastNetTrainingRunError("training run profile must be an object")
        frozen = raw["frozen_inputs"]
        sequence = raw["sequence"]
        tracks = raw["data_tracks"]
        evolution = raw["evolution"]
        optimization = raw["optimization"]
        reproducibility = raw["reproducibility"]
    except (KeyError, OSError, TypeError, yaml.YAMLError) as exc:
        raise NowcastNetTrainingRunError(f"cannot load training run profile {path}: {exc}") from exc

    for name in ("training_profile", "pilot_profile", "pilot_evidence"):
        reference = frozen[name]
        referenced_path = _resolve_local(repository_root, str(reference["path"]))
        if _sha256(referenced_path) != str(reference["sha256"]):
            raise NowcastNetTrainingRunError(f"{name} SHA-256 differs")

    evidence_path = _resolve_local(repository_root, str(frozen["pilot_evidence"]["path"]))
    try:
        evidence = json.loads(evidence_path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise NowcastNetTrainingRunError(f"cannot load pilot evidence: {exc}") from exc
    sample_index_sha256 = str(frozen["pilot_evidence"]["sample_index_sha256"])
    if (
        evidence.get("result", {}).get("status") != "passed"
        or evidence.get("result", {}).get("sample_count") != 10000
        or evidence.get("result", {}).get("all_samples_valid") is not True
        or evidence.get("source_integrity", {}).get("holdout_windows_processed") != 0
        or evidence.get("artifacts", {}).get("sample_index_sha256")
        != sample_index_sha256
        or evidence.get("operational_eligible") is not False
    ):
        raise NowcastNetTrainingRunError("pilot evidence differs from the accepted boundary")

    foundation = _load_track("foundation_0p01", tracks["foundation_0p01"])
    conformance = _load_track(
        "paper_conformance_0p02",
        tracks["paper_conformance_0p02"],
    )
    frame_tuple = (
        int(sequence["input_frames"]),
        int(sequence["target_frames"]),
        int(sequence["total_frames"]),
    )
    if (
        raw.get("schema_version") != "1.0"
        or raw.get("profile_version") != "nowcastnet-mrms-run-v1"
        or raw.get("lifecycle") != "offline_training"
        or raw.get("operational_eligible") is not False
        or frame_tuple != (9, 20, 29)
        or sum(frame_tuple[:2]) != frame_tuple[2]
        or int(sequence["cadence_minutes"]) != 10
        or float(sequence["rain_rate_cap_mm_h"]) != 128.0
        or foundation.purpose != "rainpulse_foundation_training"
        or foundation.source_resolution_deg != 0.01
        or foundation.resolution_deg != 0.01
        or foundation.native_crop_size != 256
        or foundation.model_crop_size != 256
        or foundation.downsample_method != "none"
        or foundation.expected_sample_count != 10000
        or conformance.purpose != "trainer_protocol_conformance_only"
        or conformance.source_resolution_deg != 0.01
        or conformance.resolution_deg != 0.02
        or conformance.native_crop_size != 512
        or conformance.model_crop_size != 256
        or conformance.downsample_method != "block_mean_2x2"
        or tracks["paper_conformance_0p02"].get("downsample_rule_provenance")
        != "rainpulse_conservative_area_mean"
        or conformance.expected_sample_count != 16
        or int(evolution["base_channels"]) != 32
        or evolution.get("spectral_normalization") is not True
        or evolution.get("interpolation_for_prediction") != "nearest"
        or evolution.get("interpolation_for_motion_gradient") != "bilinear"
        or evolution.get("padding_mode") != "border"
        or evolution.get("stop_gradient_between_steps") is not True
        or evolution["weighted_l1"].get("formula") != "min(24,1+target_mm_h)"
        or float(evolution["weighted_l1"]["weight_cap"]) != 24.0
        or evolution["motion_regularization"].get("operator") != "sobel"
        or float(evolution["motion_regularization"]["lambda"]) != 0.01
        or optimization.get("optimizer") != "Adam"
        or int(optimization["global_batch_size"]) != 16
        or float(optimization["initial_learning_rate"]) != 0.001
        or int(optimization["decay_step"]) != 200000
        or float(optimization["decayed_learning_rate"]) != 0.0001
        or int(optimization["total_steps"]) != 300000
        or optimization.get("default_precision") != "bf16"
        or float(optimization["gradient_clip_norm"]) != 1.0
        or int(reproducibility["run_seed"]) != 2026083002
        or reproducibility.get("deterministic_sample_order") is not True
        or reproducibility.get("checkpoint_after_complete_optimizer_step") is not True
        or reproducibility.get("exact_resume_required") is not True
    ):
        raise NowcastNetTrainingRunError("training run profile differs from frozen v1 invariants")
    if conformance.official_kernel_published is not False:
        raise NowcastNetTrainingRunError(
            "the official preprocessing downsample kernel is not published"
        )

    return NowcastNetTrainingRunProfile(
        profile_version=str(raw["profile_version"]),
        profile_sha256=hashlib.sha256(payload).hexdigest(),
        sequence=frame_tuple,
        cadence_minutes=int(sequence["cadence_minutes"]),
        rain_rate_cap_mm_h=float(sequence["rain_rate_cap_mm_h"]),
        sample_index_sha256=sample_index_sha256,
        foundation=foundation,
        paper_conformance=conformance,
        evolution=EvolutionTrainingContract(
            base_channels=int(evolution["base_channels"]),
            weight_cap=float(evolution["weighted_l1"]["weight_cap"]),
            motion_regularization_lambda=float(
                evolution["motion_regularization"]["lambda"]
            ),
            global_batch_size=int(optimization["global_batch_size"]),
            initial_learning_rate=float(optimization["initial_learning_rate"]),
            decay_step=int(optimization["decay_step"]),
            decayed_learning_rate=float(optimization["decayed_learning_rate"]),
            total_steps=int(optimization["total_steps"]),
            default_precision=str(optimization["default_precision"]),
            gradient_clip_norm=float(optimization["gradient_clip_norm"]),
        ),
        run_seed=int(reproducibility["run_seed"]),
    )
