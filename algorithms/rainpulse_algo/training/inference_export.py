from __future__ import annotations

import argparse
import hashlib
import json
import os
import platform
import subprocess
import tempfile
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from .data import MRMSZarrTrainingDataset
from .generative_profile import load_generative_training_profile
from .profile import load_nowcastnet_training_run_profile


class InferenceExportValidationError(RuntimeError):
    """Raised when inference export or fixed-vector comparison fails."""


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
        raise InferenceExportValidationError(f"cannot resolve code revision: {exc}") from exc


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


def _save_state_atomic(torch: Any, path: Path, state: dict[str, Any]) -> str:
    from .evolution_train import _state_fingerprint

    path.parent.mkdir(parents=True, exist_ok=True)
    descriptor, temporary_name = tempfile.mkstemp(prefix=f".{path.name}.tmp-", dir=path.parent)
    os.close(descriptor)
    temporary = Path(temporary_name)
    try:
        torch.save(state, temporary)
        loaded = torch.load(temporary, map_location="cpu", weights_only=True)
        if _state_fingerprint(torch, loaded) != _state_fingerprint(torch, state):
            raise InferenceExportValidationError("exported state differs on load-back")
        os.replace(temporary, path)
    finally:
        temporary.unlink(missing_ok=True)
    return _sha256(path)


def _tensor_sha256(values: Any) -> str:
    contiguous = values.detach().float().cpu().contiguous().numpy()
    return hashlib.sha256(contiguous.tobytes(order="C")).hexdigest()


def export_and_compare_inference(
    *,
    generative_profile_path: Path,
    repository_root: Path,
    data_root: Path,
    evolution_checkpoint_path: Path,
    generative_checkpoint_path: Path,
    output_dir: Path,
    device_name: str,
    precision: str,
    sample_offset: int,
    absolute_tolerance: float,
    relative_tolerance: float,
) -> dict[str, Any]:
    try:
        import torch
    except ImportError as exc:
        raise InferenceExportValidationError("PyTorch is required for inference export") from exc
    from .evolution import EvolutionNetwork, rollout_evolution
    from .generation import NowcastNetGenerator
    from .inference import load_inference_export

    profile = load_generative_training_profile(
        generative_profile_path,
        repository_root=repository_root,
    )
    evolution_profile = load_nowcastnet_training_run_profile(
        repository_root / "configs/training/nowcastnet-mrms-run-v1.yaml",
        repository_root=repository_root,
    )
    if evolution_profile.profile_sha256 != profile.evolution_profile_sha256:
        raise InferenceExportValidationError("evolution profile differs from generative profile")
    if device_name == "cuda" and not torch.cuda.is_available():
        raise InferenceExportValidationError("CUDA was requested but is unavailable")
    if precision not in {"fp32", "bf16"}:
        raise InferenceExportValidationError("inference precision must be fp32 or bf16")
    if absolute_tolerance < 0.0 or relative_tolerance < 0.0:
        raise InferenceExportValidationError("inference tolerances must be non-negative")
    if output_dir.exists() and any(output_dir.iterdir()):
        raise InferenceExportValidationError("inference export output directory is not empty")

    evolution_checkpoint_sha256 = _sha256(evolution_checkpoint_path)
    generative_checkpoint_sha256 = _sha256(generative_checkpoint_path)
    evolution_state = torch.load(
        evolution_checkpoint_path,
        map_location="cpu",
        weights_only=False,
    )
    generative_state = torch.load(
        generative_checkpoint_path,
        map_location="cpu",
        weights_only=False,
    )
    if (
        evolution_state.get("schema_version")
        != "rainpulse.nowcastnet-evolution-checkpoint/1.0"
        or evolution_state.get("profile_sha256") != evolution_profile.profile_sha256
        or generative_state.get("schema_version")
        != "rainpulse.nowcastnet-generative-checkpoint/1.0"
        or generative_state.get("profile_sha256") != profile.profile_sha256
        or generative_state.get("evolution_checkpoint_sha256")
        != evolution_checkpoint_sha256
        or generative_state.get("sample_index_sha256")
        != evolution_profile.sample_index_sha256
    ):
        raise InferenceExportValidationError("training checkpoint lineage differs")

    dataset = MRMSZarrTrainingDataset(
        data_root,
        expected_sample_index_sha256=evolution_profile.sample_index_sha256,
        expected_sample_count=evolution_profile.foundation.expected_sample_count,
        expected_crop_size=evolution_profile.foundation.model_crop_size,
        input_frames=profile.input_frames,
        target_frames=profile.target_frames,
    )
    sample = dataset[sample_offset % len(dataset)]
    inputs = torch.from_numpy(sample.input_rate_mm_h[None]).to(device_name)
    noise_generator = torch.Generator(device=device_name).manual_seed(profile.run_seed + 1000)
    noise = torch.randn(
        1,
        profile.ensemble_members,
        profile.base_channels,
        inputs.shape[-2] // 32,
        inputs.shape[-1] // 32,
        generator=noise_generator,
        device=device_name,
        dtype=inputs.dtype,
    )

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
    evolution.load_state_dict(evolution_state["model_state_dict"], strict=True)
    generator.load_state_dict(generative_state["generator_state_dict"], strict=True)
    evolution.eval().requires_grad_(False)
    generator.eval().requires_grad_(False)
    autocast_enabled = device_name == "cuda" and precision == "bf16"
    with torch.no_grad(), torch.autocast(
        device_type=device_name,
        dtype=torch.bfloat16 if precision == "bf16" else torch.float32,
        enabled=autocast_enabled,
    ):
        intensity, motion = evolution(inputs)
        evolution_prediction = rollout_evolution(inputs, intensity, motion)
        reference = torch.stack(
            [
                generator(inputs, evolution_prediction, noise=member_noise)
                for member_noise in noise.unbind(dim=1)
            ],
            dim=1,
        )
        reference_clipped = torch.clamp(
            reference,
            min=0.0,
            max=profile.rain_rate_cap_mm_h,
        )

    output_dir.mkdir(parents=True, exist_ok=True)
    evolution_export_path = output_dir / "evolution-state.pt"
    generator_export_path = output_dir / "generator-state.pt"
    evolution_export_sha256 = _save_state_atomic(
        torch,
        evolution_export_path,
        evolution.state_dict(),
    )
    generator_export_sha256 = _save_state_atomic(
        torch,
        generator_export_path,
        generator.state_dict(),
    )
    manifest = {
        "schema_version": "rainpulse.nowcastnet-inference-export/1.0",
        "created_at": datetime.now(UTC).isoformat(),
        "code_revision": _code_revision(repository_root),
        "profile_version": profile.profile_version,
        "profile_sha256": profile.profile_sha256,
        "evolution_profile_sha256": evolution_profile.profile_sha256,
        "source_checkpoints": {
            "evolution_global_step": int(evolution_state["global_step"]),
            "evolution_sha256": evolution_checkpoint_sha256,
            "generative_global_step": int(generative_state["global_step"]),
            "generative_sha256": generative_checkpoint_sha256,
        },
        "architecture": {
            "input_frames": profile.input_frames,
            "target_frames": profile.target_frames,
            "evolution_base_channels": evolution_profile.evolution.base_channels,
            "generator_base_channels": profile.base_channels,
            "rain_rate_cap_mm_h": profile.rain_rate_cap_mm_h,
            "raw_output_semantics": "unbounded_generator_mm_h",
            "product_clip_range_mm_h": [0.0, profile.rain_rate_cap_mm_h],
        },
        "artifacts": {
            "evolution_state": {
                "path": evolution_export_path.name,
                "sha256": evolution_export_sha256,
            },
            "generator_state": {
                "path": generator_export_path.name,
                "sha256": generator_export_sha256,
            },
        },
        "contains_discriminator": False,
        "contains_optimizer_state": False,
        "operational_eligible": False,
    }
    _atomic_json(output_dir / "manifest.json", manifest)

    reloaded, loaded_manifest = load_inference_export(output_dir, device=device_name)
    if loaded_manifest != manifest:
        raise InferenceExportValidationError("loaded inference manifest differs")
    with torch.no_grad(), torch.autocast(
        device_type=device_name,
        dtype=torch.bfloat16 if precision == "bf16" else torch.float32,
        enabled=autocast_enabled,
    ):
        reloaded_raw = reloaded(inputs, noise)
        reloaded_clipped = reloaded.clip_to_product_range(reloaded_raw)
    raw_difference = torch.max(torch.abs(reference.float() - reloaded_raw.float()))
    clipped_difference = torch.max(
        torch.abs(reference_clipped.float() - reloaded_clipped.float())
    )
    raw_equal = bool(
        torch.allclose(
            reference.float(),
            reloaded_raw.float(),
            atol=absolute_tolerance,
            rtol=relative_tolerance,
        )
    )
    clipped_equal = bool(
        torch.allclose(
            reference_clipped.float(),
            reloaded_clipped.float(),
            atol=absolute_tolerance,
            rtol=relative_tolerance,
        )
    )
    if not raw_equal or not clipped_equal:
        raise InferenceExportValidationError("reloaded inference output differs")

    report = {
        "schema_version": "1.0",
        "status": "passed",
        "created_at": datetime.now(UTC).isoformat(),
        "code_revision": manifest["code_revision"],
        "profile_sha256": profile.profile_sha256,
        "sample_index_sha256": evolution_profile.sample_index_sha256,
        "sample_id": sample.sample_id,
        "noise_seed": profile.run_seed + 1000,
        "ensemble_members": profile.ensemble_members,
        "precision": precision,
        "device": device_name,
        "raw_output_shape": list(reference.shape),
        "raw_output_sha256_float32": _tensor_sha256(reference),
        "clipped_output_sha256_float32": _tensor_sha256(reference_clipped),
        "comparison": {
            "absolute_tolerance": absolute_tolerance,
            "relative_tolerance": relative_tolerance,
            "raw_allclose": raw_equal,
            "clipped_allclose": clipped_equal,
            "raw_max_absolute_difference": float(raw_difference.cpu()),
            "clipped_max_absolute_difference": float(clipped_difference.cpu()),
        },
        "artifacts": manifest["artifacts"],
        "manifest_sha256": _sha256(output_dir / "manifest.json"),
        "torch_version": str(torch.__version__),
        "cuda_version": str(torch.version.cuda),
        "python_version": platform.python_version(),
        "source_evolution_checkpoint_sha256": evolution_checkpoint_sha256,
        "source_generative_checkpoint_sha256": generative_checkpoint_sha256,
        "contains_discriminator": False,
        "contains_optimizer_state": False,
        "operational_eligible": False,
    }
    _atomic_json(output_dir / "comparison-report.json", report)
    return report


def _build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Export and compare RainPulse inference weights")
    parser.add_argument("--profile", type=Path, required=True)
    parser.add_argument("--repository-root", type=Path, required=True)
    parser.add_argument("--data-root", type=Path, required=True)
    parser.add_argument("--evolution-checkpoint", type=Path, required=True)
    parser.add_argument("--generative-checkpoint", type=Path, required=True)
    parser.add_argument("--output-dir", type=Path, required=True)
    parser.add_argument("--device", choices=("cpu", "cuda"), default="cuda")
    parser.add_argument("--precision", choices=("fp32", "bf16"), default="bf16")
    parser.add_argument("--sample-offset", type=int, default=0)
    parser.add_argument("--absolute-tolerance", type=float, default=0.0)
    parser.add_argument("--relative-tolerance", type=float, default=0.0)
    return parser


def main() -> int:
    args = _build_parser().parse_args()
    report = export_and_compare_inference(
        generative_profile_path=args.profile,
        repository_root=args.repository_root,
        data_root=args.data_root,
        evolution_checkpoint_path=args.evolution_checkpoint,
        generative_checkpoint_path=args.generative_checkpoint,
        output_dir=args.output_dir,
        device_name=args.device,
        precision=args.precision,
        sample_offset=args.sample_offset,
        absolute_tolerance=args.absolute_tolerance,
        relative_tolerance=args.relative_tolerance,
    )
    print(json.dumps(report, ensure_ascii=False, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
