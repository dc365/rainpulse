from __future__ import annotations

import argparse
import json
import os
import shutil
import subprocess
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

import numpy as np

from .data import MRMSZarrTrainingDataset
from .evolution_train import _atomic_json, _code_revision, _state_fingerprint
from .generative_profile import load_generative_training_profile
from .generative_train import _validate_evolution_checkpoint
from .profile import load_nowcastnet_training_run_profile


class GenerativeRuntimeError(RuntimeError):
    """Raised when a generative-stage preflight cannot be completed safely."""


def _passed(**details: Any) -> dict[str, Any]:
    return {"status": "passed", **details}


def _failed(reason: str, **details: Any) -> dict[str, Any]:
    return {"status": "failed", "reason": reason, **details}


def _nearest_existing_parent(path: Path) -> Path:
    candidate = path.resolve()
    while not candidate.exists():
        if candidate.parent == candidate:
            raise GenerativeRuntimeError("no existing output storage parent")
        candidate = candidate.parent
    return candidate


def build_generative_preflight_report(
    *,
    profile_path: Path,
    repository_root: Path,
    data_root: Path,
    evolution_checkpoint_path: Path,
    output_dir: Path,
    device_name: str,
    minimum_output_free_bytes: int,
    minimum_cuda_free_bytes: int,
    sample_probe_count: int,
) -> dict[str, Any]:
    if (
        device_name not in {"cpu", "cuda"}
        or min(
            minimum_output_free_bytes,
            minimum_cuda_free_bytes,
            sample_probe_count,
        )
        < 0
    ):
        raise GenerativeRuntimeError("generative preflight arguments are invalid")
    generated_at = datetime.now(UTC).isoformat()
    checks: dict[str, dict[str, Any]] = {}
    code_revision = "unavailable"
    profile: Any | None = None
    evolution_profile: Any | None = None
    checkpoint_state: dict[str, Any] | None = None

    try:
        code_revision = _code_revision(repository_root)
        status = subprocess.check_output(
            [
                "git",
                "status",
                "--porcelain",
                "--untracked-files=all",
                "--",
                "algorithms/rainpulse_algo/training",
                "algorithms/tests",
                "configs/training",
                "configs/schemas",
            ],
            cwd=repository_root,
            text=True,
        )
        checks["repository_state"] = (
            _passed(changed_entry_count=0)
            if not status.strip()
            else _failed(
                "training_tree_not_clean",
                changed_entry_count=len(status.splitlines()),
            )
        )
    except (OSError, subprocess.CalledProcessError):
        checks["repository_state"] = _failed("repository_identity_unavailable")

    try:
        profile = load_generative_training_profile(
            profile_path,
            repository_root=repository_root,
        )
        evolution_profile = load_nowcastnet_training_run_profile(
            profile.evolution_profile_path,
            repository_root=repository_root,
        )
        if evolution_profile.profile_sha256 != profile.evolution_profile_sha256:
            raise ValueError("evolution profile identity differs")
        checks["profile_contract"] = _passed(
            profile_version=profile.profile_version,
            profile_sha256=profile.profile_sha256,
            evolution_profile_sha256=evolution_profile.profile_sha256,
        )
    except (OSError, RuntimeError, ValueError):
        checks["profile_contract"] = _failed("profile_contract_invalid")

    try:
        if profile is None or evolution_profile is None:
            raise ValueError("profile unavailable")
        import torch

        from .evolution import EvolutionNetwork

        checkpoint_state, checkpoint_step, checkpoint_sha256 = _validate_evolution_checkpoint(
            torch,
            path=evolution_checkpoint_path,
            evolution_profile=evolution_profile,
            generative_profile=profile,
            stage_b_smoke=False,
        )
        fingerprints_valid = all(
            _state_fingerprint(torch, checkpoint_state[f"{name}_state_dict"])
            == checkpoint_state[f"{name}_state_sha256"]
            for name in ("model", "optimizer", "scheduler")
        )
        random_valid = (
            _state_fingerprint(
                torch,
                {
                    "python": checkpoint_state["python_random_state"],
                    "numpy": checkpoint_state["numpy_random_state"],
                    "torch": checkpoint_state["torch_random_state"],
                    "cuda": checkpoint_state["cuda_random_state"],
                },
            )
            == checkpoint_state["random_state_sha256"]
        )
        model = EvolutionNetwork(
            input_frames=profile.input_frames,
            target_frames=profile.target_frames,
            base_channels=evolution_profile.evolution.base_channels,
        )
        model.load_state_dict(checkpoint_state["model_state_dict"], strict=True)
        if not fingerprints_valid or not random_valid:
            raise ValueError("checkpoint fingerprints differ")
        checks["evolution_parent"] = _passed(
            global_step=checkpoint_step,
            checkpoint_sha256=checkpoint_sha256,
            state_fingerprints_verified=True,
            random_state_verified=True,
            strict_model_import_verified=True,
        )
    except (ImportError, KeyError, OSError, RuntimeError, ValueError):
        checks["evolution_parent"] = _failed("evolution_parent_checkpoint_invalid")
        checkpoint_state = None

    try:
        if evolution_profile is None:
            raise ValueError("evolution profile unavailable")
        track = evolution_profile.foundation
        dataset = MRMSZarrTrainingDataset(
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
            input_frames=evolution_profile.sequence[0],
            target_frames=evolution_profile.sequence[1],
            maximum_open_shards=max(4, sample_probe_count),
        )
        indices = (
            np.linspace(0, len(dataset) - 1, sample_probe_count, dtype=int).tolist()
            if sample_probe_count
            else []
        )
        samples = dataset.__getitems__(indices) if indices else []
        checks["dataset_contract"] = _passed(
            sample_count=len(dataset),
            shard_count=track.expected_shard_count,
            sample_index_sha256=dataset.sample_index_sha256,
            dataset_version=track.expected_dataset_version,
            plan_id=track.expected_plan_id,
            holdout_windows_processed=0,
        )
        checks["dataset_probe"] = _passed(
            sample_count=len(samples),
            all_finite=True,
            all_valid=True,
        )
    except (OSError, RuntimeError, ValueError):
        checks["dataset_contract"] = _failed("dataset_contract_invalid_or_incomplete")
        checks["dataset_probe"] = _failed("dataset_sample_probe_failed")

    try:
        output_parent = _nearest_existing_parent(output_dir)
        usage = shutil.disk_usage(output_parent)
        details = {
            "free_bytes": int(usage.free),
            "minimum_free_bytes": minimum_output_free_bytes,
        }
        checks["output_storage"] = (
            _passed(**details)
            if usage.free >= minimum_output_free_bytes
            else _failed("output_free_space_below_threshold", **details)
        )
        separated = os.stat(data_root).st_dev != os.stat(output_parent).st_dev
        checks["data_output_separation"] = (
            _passed(separate_filesystems=True)
            if separated
            else _failed(
                "data_and_output_share_filesystem",
                separate_filesystems=False,
            )
        )
    except (OSError, GenerativeRuntimeError):
        checks["output_storage"] = _failed("output_storage_unavailable")
        checks["data_output_separation"] = _failed("storage_identity_unavailable")

    try:
        import torch

        if device_name == "cpu":
            checks["runtime"] = _passed(
                device="cpu",
                torch_version=str(torch.__version__),
                cuda_version=str(torch.version.cuda),
            )
        elif not torch.cuda.is_available():
            checks["runtime"] = _failed("cuda_unavailable")
        else:
            free_bytes, total_bytes = torch.cuda.mem_get_info()
            details = {
                "device": "cuda",
                "torch_version": str(torch.__version__),
                "cuda_version": str(torch.version.cuda),
                "gpu_name": str(torch.cuda.get_device_properties(0).name),
                "gpu_total_memory_bytes": int(total_bytes),
                "gpu_free_memory_bytes": int(free_bytes),
                "minimum_free_memory_bytes": minimum_cuda_free_bytes,
            }
            checks["runtime"] = (
                _passed(**details)
                if free_bytes >= minimum_cuda_free_bytes
                else _failed("cuda_free_memory_below_threshold", **details)
            )
    except (ImportError, RuntimeError, ValueError):
        checks["runtime"] = _failed("runtime_probe_failed")

    status = (
        "passed"
        if checks
        and checkpoint_state is not None
        and all(check.get("status") == "passed" for check in checks.values())
        else "failed"
    )
    return {
        "schema_version": "rainpulse.nowcastnet-generative-preflight/1.0",
        "status": status,
        "generated_at": generated_at,
        "code_revision": code_revision,
        "device": device_name,
        "checks": checks,
        "training_start_allowed": status == "passed",
        "independent_holdout_opened": False,
        "operational_eligible": False,
    }


def _build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="Validate the RainPulse formal generative training boundary"
    )
    parser.add_argument("--profile", type=Path, required=True)
    parser.add_argument("--repository-root", type=Path, required=True)
    parser.add_argument("--data-root", type=Path, required=True)
    parser.add_argument("--evolution-checkpoint", type=Path, required=True)
    parser.add_argument("--output-dir", type=Path, required=True)
    parser.add_argument("--report-path", type=Path, required=True)
    parser.add_argument("--device", choices=("cpu", "cuda"), default="cpu")
    parser.add_argument("--minimum-output-free-bytes", type=int, default=100_000_000_000)
    parser.add_argument("--minimum-cuda-free-bytes", type=int, default=0)
    parser.add_argument("--sample-probe-count", type=int, default=64)
    return parser


def main() -> int:
    args = _build_parser().parse_args()
    report = build_generative_preflight_report(
        profile_path=args.profile,
        repository_root=args.repository_root,
        data_root=args.data_root,
        evolution_checkpoint_path=args.evolution_checkpoint,
        output_dir=args.output_dir,
        device_name=args.device,
        minimum_output_free_bytes=args.minimum_output_free_bytes,
        minimum_cuda_free_bytes=args.minimum_cuda_free_bytes,
        sample_probe_count=args.sample_probe_count,
    )
    _atomic_json(args.report_path, report)
    print(json.dumps(report, ensure_ascii=False, sort_keys=True))
    return 0 if report["status"] == "passed" else 1


if __name__ == "__main__":
    raise SystemExit(main())
