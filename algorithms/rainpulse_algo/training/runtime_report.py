from __future__ import annotations

import argparse
import hashlib
import json
import os
import pickle
import shutil
import subprocess
from collections.abc import Callable
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from .data import MRMSZarrTrainingDataset
from .evolution_train import _atomic_json, _sha256, _state_fingerprint
from .profile import load_nowcastnet_training_run_profile


class TrainingRuntimeReportError(RuntimeError):
    """Raised when a sanitized training runtime report cannot be produced safely."""


def _canonical_sha256(value: Any) -> str:
    payload = json.dumps(value, sort_keys=True, separators=(",", ":")).encode()
    return hashlib.sha256(payload).hexdigest()


def _passed(**details: Any) -> dict[str, Any]:
    return {"status": "passed", **details}


def _failed(reason: str, **details: Any) -> dict[str, Any]:
    return {"status": "failed", "reason": reason, **details}


def _nearest_existing_parent(path: Path) -> Path:
    candidate = path.resolve()
    while not candidate.exists():
        if candidate.parent == candidate:
            raise TrainingRuntimeReportError("no existing storage parent")
        candidate = candidate.parent
    return candidate


def _repository_state(
    repository_root: Path,
    *,
    require_clean_training_tree: bool,
) -> tuple[str, dict[str, Any]]:
    try:
        revision = subprocess.check_output(
            ["git", "rev-parse", "HEAD"],
            cwd=repository_root,
            text=True,
            stderr=subprocess.DEVNULL,
        ).strip()
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
                "docs/nowcastnet-training",
            ],
            cwd=repository_root,
            text=True,
            stderr=subprocess.DEVNULL,
        )
    except (OSError, subprocess.CalledProcessError) as exc:
        raise TrainingRuntimeReportError("repository identity is unavailable") from exc
    changed_count = len([line for line in status.splitlines() if line])
    if require_clean_training_tree and changed_count:
        return revision, _failed(
            "training_tree_not_clean",
            changed_entry_count=changed_count,
        )
    return revision, _passed(changed_entry_count=changed_count)


def _checkpoint_preflight(
    *,
    output_dir: Path,
    run_mode: str,
    profile: Any,
    code_revision: str,
    sample_index_sha256: str,
    batch_size: int,
    precision: str,
) -> dict[str, Any]:
    latest_path = output_dir / "LATEST.json"
    if run_mode == "new":
        if output_dir.exists() and any(output_dir.iterdir()):
            return _failed("new_run_output_not_empty")
        return _passed(mode="new", resume_step=0, checkpoint_sha256=None)
    if not latest_path.is_file():
        return _failed("resume_checkpoint_marker_missing")
    try:
        import torch

        latest = json.loads(latest_path.read_text(encoding="utf-8"))
        relative = Path(str(latest["path"]))
        checkpoint = (output_dir / relative).resolve()
        if (
            relative.is_absolute()
            or ".." in relative.parts
            or not checkpoint.is_relative_to(output_dir.resolve())
            or not checkpoint.is_file()
            or _sha256(checkpoint) != str(latest.get("sha256"))
        ):
            return _failed("resume_checkpoint_marker_invalid")
        state = torch.load(checkpoint, map_location="cpu", weights_only=False)
        identity_valid = (
            state.get("schema_version")
            == "rainpulse.nowcastnet-evolution-checkpoint/1.0"
            and state.get("stage") == "evolution"
            and state.get("profile_sha256") == profile.profile_sha256
            and state.get("code_revision") == code_revision
            and state.get("sample_index_sha256") == sample_index_sha256
            and state.get("data_track") == profile.foundation.name
            and int(state.get("batch_size", -1)) == batch_size
            and state.get("precision") == precision
            and int(state.get("global_step", -1)) == int(latest["global_step"])
        )
        fingerprints_valid = identity_valid and all(
            _state_fingerprint(torch, state[f"{name}_state_dict"])
            == state[f"{name}_state_sha256"]
            for name in ("model", "optimizer", "scheduler")
        )
        random_valid = fingerprints_valid and _state_fingerprint(
            torch,
            {
                "python": state["python_random_state"],
                "numpy": state["numpy_random_state"],
                "torch": state["torch_random_state"],
                "cuda": state["cuda_random_state"],
            },
        ) == state["random_state_sha256"]
    except (
        EOFError,
        ImportError,
        KeyError,
        OSError,
        RuntimeError,
        ValueError,
        json.JSONDecodeError,
        pickle.UnpicklingError,
    ):
        return _failed("resume_checkpoint_load_failed")
    if not identity_valid or not fingerprints_valid or not random_valid:
        return _failed("resume_checkpoint_contract_invalid")
    return _passed(
        mode="resume",
        resume_step=int(latest["global_step"]),
        checkpoint_sha256=str(latest["sha256"]),
        state_fingerprints_verified=True,
    )


def _runtime_preflight(
    *,
    device_name: str,
    minimum_cuda_free_bytes: int,
) -> dict[str, Any]:
    try:
        import torch
    except ImportError:
        return _failed("pytorch_unavailable")
    details: dict[str, Any] = {
        "torch_version": str(torch.__version__),
        "cuda_version": str(torch.version.cuda),
    }
    if device_name == "cpu":
        return _passed(device="cpu", **details)
    if not torch.cuda.is_available():
        return _failed("cuda_unavailable", **details)
    try:
        free_bytes, total_bytes = torch.cuda.mem_get_info()
        properties = torch.cuda.get_device_properties(0)
    except (RuntimeError, ValueError):
        return _failed("cuda_probe_failed", **details)
    details.update(
        {
            "device": "cuda",
            "gpu_name": str(properties.name),
            "gpu_total_memory_bytes": int(total_bytes),
            "gpu_free_memory_bytes": int(free_bytes),
            "minimum_free_memory_bytes": minimum_cuda_free_bytes,
        }
    )
    if int(free_bytes) < minimum_cuda_free_bytes:
        return _failed("cuda_free_memory_below_threshold", **details)
    return _passed(**details)


def build_training_preflight_report(
    *,
    profile_path: Path,
    repository_root: Path,
    data_root: Path,
    output_dir: Path,
    device_name: str,
    batch_size: int,
    precision: str,
    run_mode: str,
    minimum_output_free_bytes: int,
    minimum_shared_memory_bytes: int,
    minimum_cuda_free_bytes: int,
    require_clean_training_tree: bool = True,
    sample_probe_count: int = 4,
) -> dict[str, Any]:
    if (
        device_name not in {"cpu", "cuda"}
        or precision not in {"fp32", "bf16"}
        or run_mode not in {"new", "resume"}
        or batch_size < 1
        or min(
            minimum_output_free_bytes,
            minimum_shared_memory_bytes,
            minimum_cuda_free_bytes,
            sample_probe_count,
        )
        < 0
    ):
        raise TrainingRuntimeReportError("preflight arguments are invalid")

    generated_at = datetime.now(UTC).isoformat()
    checks: dict[str, dict[str, Any]] = {}
    revision = "unavailable"
    profile: Any | None = None
    dataset: MRMSZarrTrainingDataset | None = None

    try:
        revision, checks["repository_state"] = _repository_state(
            repository_root,
            require_clean_training_tree=require_clean_training_tree,
        )
    except TrainingRuntimeReportError:
        checks["repository_state"] = _failed("repository_identity_unavailable")

    try:
        profile = load_nowcastnet_training_run_profile(
            profile_path,
            repository_root=repository_root,
        )
        checks["profile_contract"] = _passed(
            profile_version=profile.profile_version,
            profile_sha256=profile.profile_sha256,
        )
    except (OSError, ValueError):
        checks["profile_contract"] = _failed("profile_contract_invalid")

    if profile is None:
        checks["dataset_contract"] = _failed("profile_unavailable")
        checks["dataset_probe"] = _failed("dataset_contract_unavailable")
    else:
        track = profile.foundation
        try:
            dataset = MRMSZarrTrainingDataset(
                data_root,
                expected_sample_index_sha256=profile.sample_index_sha256,
                expected_sample_count=track.expected_sample_count,
                expected_crop_size=track.model_crop_size,
                expected_shard_count=track.expected_shard_count,
                dataset_contract=track.dataset_contract,
                expected_dataset_version=track.expected_dataset_version,
                expected_profile_sha256=track.expected_profile_sha256,
                expected_plan_id=track.expected_plan_id,
                require_validation_report=track.require_validation_report,
                input_frames=profile.sequence[0],
                target_frames=profile.sequence[1],
                maximum_open_shards=max(4, sample_probe_count),
            )
            checks["dataset_contract"] = _passed(
                sample_count=len(dataset),
                shard_count=track.expected_shard_count,
                sample_index_sha256=dataset.sample_index_sha256,
                dataset_version=track.expected_dataset_version,
                plan_id=track.expected_plan_id,
                holdout_windows_processed=0,
            )
        except (OSError, RuntimeError, ValueError):
            checks["dataset_contract"] = _failed("dataset_contract_invalid_or_incomplete")

        if dataset is None:
            checks["dataset_probe"] = _failed("dataset_contract_unavailable")
        else:
            try:
                indices = list(range(min(sample_probe_count, len(dataset))))
                samples = dataset.__getitems__(indices) if indices else []
                checks["dataset_probe"] = _passed(
                    sample_count=len(samples),
                    all_finite=True,
                    all_valid=True,
                )
            except (OSError, RuntimeError, ValueError):
                checks["dataset_probe"] = _failed("dataset_sample_probe_failed")

    try:
        output_parent = _nearest_existing_parent(output_dir)
        output_usage = shutil.disk_usage(output_parent)
        output_details = {
            "free_bytes": int(output_usage.free),
            "minimum_free_bytes": minimum_output_free_bytes,
        }
        checks["output_storage"] = (
            _passed(**output_details)
            if output_usage.free >= minimum_output_free_bytes
            else _failed("output_free_space_below_threshold", **output_details)
        )
    except (OSError, TrainingRuntimeReportError):
        output_parent = None
        checks["output_storage"] = _failed("output_storage_unavailable")

    try:
        if not data_root.exists() or output_parent is None:
            raise OSError
        separated = os.stat(data_root).st_dev != os.stat(output_parent).st_dev
        checks["data_output_separation"] = (
            _passed(separate_filesystems=True)
            if separated
            else _failed("data_and_output_share_filesystem", separate_filesystems=False)
        )
    except OSError:
        checks["data_output_separation"] = _failed("storage_identity_unavailable")

    shared_memory_path = Path("/dev/shm")
    if minimum_shared_memory_bytes == 0 and not shared_memory_path.exists():
        checks["shared_memory"] = _passed(
            free_bytes=0,
            minimum_free_bytes=0,
            not_applicable=True,
        )
    else:
        try:
            shared_usage = shutil.disk_usage(shared_memory_path)
            shared_details = {
                "free_bytes": int(shared_usage.free),
                "minimum_free_bytes": minimum_shared_memory_bytes,
            }
            checks["shared_memory"] = (
                _passed(**shared_details)
                if shared_usage.free >= minimum_shared_memory_bytes
                else _failed("shared_memory_below_threshold", **shared_details)
            )
        except OSError:
            checks["shared_memory"] = _failed("shared_memory_unavailable")

    checks["runtime"] = _runtime_preflight(
        device_name=device_name,
        minimum_cuda_free_bytes=minimum_cuda_free_bytes,
    )
    if profile is None or dataset is None or revision == "unavailable":
        checks["checkpoint"] = _failed("training_identity_unavailable")
    else:
        checks["checkpoint"] = _checkpoint_preflight(
            output_dir=output_dir,
            run_mode=run_mode,
            profile=profile,
            code_revision=revision,
            sample_index_sha256=dataset.sample_index_sha256,
            batch_size=batch_size,
            precision=precision,
        )

    status = (
        "passed"
        if all(check["status"] == "passed" for check in checks.values())
        else "failed"
    )
    identity = {
        "generated_at": generated_at,
        "code_revision": revision,
        "profile_sha256": checks["profile_contract"].get("profile_sha256"),
        "sample_index_sha256": checks["dataset_contract"].get(
            "sample_index_sha256"
        ),
        "run_mode": run_mode,
        "device": device_name,
        "batch_size": batch_size,
        "precision": precision,
    }
    return {
        "schema_version": "rainpulse.nowcastnet-training-preflight/1.0",
        "status": status,
        "preflight_id": _canonical_sha256(identity),
        "generated_at": generated_at,
        **identity,
        "checks": checks,
        "training_start_allowed": status == "passed",
        "operational_eligible": False,
    }


def _read_metrics(path: Path) -> list[dict[str, Any]]:
    try:
        return [
            json.loads(line)
            for line in path.read_text(encoding="utf-8").splitlines()
            if line
        ]
    except (OSError, json.JSONDecodeError) as exc:
        raise TrainingRuntimeReportError("training metrics are unavailable") from exc


def _loss_summary(rows: list[dict[str, Any]]) -> dict[str, dict[str, float]]:
    names = sorted(
        {
            key
            for row in rows
            for key, value in row.items()
            if "loss" in key and isinstance(value, (int, float))
        }
    )
    summary: dict[str, dict[str, float]] = {}
    for name in names:
        values = [float(row[name]) for row in rows if name in row]
        summary[name] = {
            "minimum": min(values),
            "maximum": max(values),
            "mean": sum(values) / len(values),
            "last": values[-1],
        }
    return summary


def build_nightly_training_report(
    *,
    run_dir: Path,
    preflight_report: dict[str, Any],
    shared_service_recovery: str,
) -> dict[str, Any]:
    if shared_service_recovery not in {"passed", "failed", "not_required"}:
        raise TrainingRuntimeReportError("shared service recovery state is invalid")
    try:
        training = json.loads(
            (run_dir / "training-report.json").read_text(encoding="utf-8")
        )
        latest = json.loads((run_dir / "LATEST.json").read_text(encoding="utf-8"))
        relative = Path(str(latest["path"]))
        checkpoint = (run_dir / relative).resolve()
        if (
            relative.is_absolute()
            or ".." in relative.parts
            or not checkpoint.is_relative_to(run_dir.resolve())
            or not checkpoint.is_file()
            or _sha256(checkpoint) != str(latest["sha256"])
            or latest.get("sha256")
            != training.get("latest_checkpoint", {}).get("sha256")
        ):
            raise TrainingRuntimeReportError("final checkpoint identity differs")
        metrics = _read_metrics(run_dir / "metrics.jsonl")
    except (KeyError, OSError, ValueError, json.JSONDecodeError) as exc:
        raise TrainingRuntimeReportError("training run artifacts are incomplete") from exc

    start_step = int(training["invocation_start_step"])
    end_step = int(training["global_step"])
    invocation_rows = [
        row for row in metrics if start_step < int(row.get("global_step", -1)) <= end_step
    ]
    expected_steps = list(range(start_step + 1, end_step + 1))
    if [int(row["global_step"]) for row in invocation_rows] != expected_steps:
        raise TrainingRuntimeReportError("invocation metrics are not contiguous")
    duration_seconds = float(training["invocation_duration_seconds"])
    stop_signal = training.get("stop_signal")
    if stop_signal:
        stop_reason = f"signal:{stop_signal}"
    elif end_step >= int(training["planned_total_steps"]):
        stop_reason = "planned_training_complete"
    else:
        stop_reason = "invocation_target_complete"
    checkpoint_verified = True
    expected_run_mode = "resume" if training.get("resumed") is True else "new"
    preflight_checkpoint = preflight_report.get("checks", {}).get("checkpoint", {})
    input_checkpoint = training.get("input_checkpoint")
    if expected_run_mode == "resume":
        preflight_checkpoint_matches = (
            isinstance(input_checkpoint, dict)
            and preflight_checkpoint.get("mode") == "resume"
            and int(preflight_checkpoint.get("resume_step", -1)) == start_step
            and int(input_checkpoint.get("global_step", -1)) == start_step
            and preflight_checkpoint.get("checkpoint_sha256")
            == input_checkpoint.get("sha256")
        )
    else:
        preflight_checkpoint_matches = (
            start_step == 0
            and input_checkpoint is None
            and preflight_checkpoint.get("mode") == "new"
            and int(preflight_checkpoint.get("resume_step", -1)) == 0
            and preflight_checkpoint.get("checkpoint_sha256") is None
        )
    preflight_identity_verified = (
        preflight_report.get("status") == "passed"
        and preflight_report.get("training_start_allowed") is True
        and preflight_report.get("code_revision") == training.get("code_revision")
        and preflight_report.get("profile_sha256") == training.get("profile_sha256")
        and preflight_report.get("sample_index_sha256")
        == training.get("sample_index_sha256")
        and preflight_report.get("run_mode") == expected_run_mode
        and preflight_report.get("device") == training.get("device")
        and int(preflight_report.get("batch_size", -1))
        == int(training.get("batch_size", -2))
        and preflight_report.get("precision") == training.get("precision")
        and preflight_checkpoint.get("status") == "passed"
        and preflight_checkpoint_matches
    )
    training_status_valid = training.get("status") in {"passed", "stopped"}
    report_status = (
        "passed"
        if preflight_identity_verified
        and training_status_valid
        and checkpoint_verified
        and shared_service_recovery in {"passed", "not_required"}
        else "failed"
    )
    completed = end_step - start_step
    return {
        "schema_version": "rainpulse.nowcastnet-nightly-report/1.0",
        "status": report_status,
        "generated_at": datetime.now(UTC).isoformat(),
        "preflight_id": preflight_report.get("preflight_id"),
        "preflight_identity_verified": preflight_identity_verified,
        "training_status": training.get("status"),
        "stage": "evolution",
        "started_at": training.get("started_at"),
        "finished_at": training.get("finished_at"),
        "steps": {
            "start": start_step,
            "end": end_step,
            "completed_this_invocation": completed,
            "planned_total": int(training["planned_total_steps"]),
        },
        "identity": {
            "profile_sha256": training.get("profile_sha256"),
            "code_revision": training.get("code_revision"),
            "sample_index_sha256": training.get("sample_index_sha256"),
        },
        "input_checkpoint": (
            {
                "global_step": int(training["input_checkpoint"]["global_step"]),
                "sha256": str(training["input_checkpoint"]["sha256"]),
            }
            if training.get("input_checkpoint") is not None
            else None
        ),
        "output_checkpoint": {
            "global_step": int(latest["global_step"]),
            "sha256": str(latest["sha256"]),
            "hash_verified": checkpoint_verified,
        },
        "loss_summary": _loss_summary(invocation_rows),
        "performance": {
            "duration_seconds": duration_seconds,
            "steps_per_second": completed / duration_seconds if duration_seconds > 0 else 0.0,
            "peak_allocated_memory_bytes": int(
                training.get("peak_allocated_memory_bytes", 0)
            ),
            "peak_reserved_memory_bytes": int(
                training.get("peak_reserved_memory_bytes", 0)
            ),
        },
        "stop_reason": stop_reason,
        "shared_service_recovery": shared_service_recovery,
        "next_night_auto_resume_allowed": (
            report_status == "passed"
            and training.get("status") == "stopped"
            and end_step < int(training["planned_total_steps"])
        ),
        "operational_eligible": False,
    }


def _write_report(path: Path, builder: Callable[[], dict[str, Any]]) -> int:
    try:
        report = builder()
    except (OSError, ValueError, TrainingRuntimeReportError):
        report = {
            "schema_version": "rainpulse.nowcastnet-runtime-report-error/1.0",
            "status": "failed",
            "generated_at": datetime.now(UTC).isoformat(),
            "reason": "runtime_report_generation_failed",
            "operational_eligible": False,
        }
    _atomic_json(path, report)
    print(json.dumps(report, ensure_ascii=False, sort_keys=True))
    return 0 if report.get("status") == "passed" else 2


def _build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Build sanitized NowcastNet runtime reports")
    subparsers = parser.add_subparsers(dest="command", required=True)
    preflight = subparsers.add_parser("preflight")
    preflight.add_argument("--profile", type=Path, required=True)
    preflight.add_argument("--repository-root", type=Path, required=True)
    preflight.add_argument("--data-root", type=Path, required=True)
    preflight.add_argument("--output-dir", type=Path, required=True)
    preflight.add_argument("--report-path", type=Path, required=True)
    preflight.add_argument("--device", choices=("cpu", "cuda"), default="cuda")
    preflight.add_argument("--batch-size", type=int, default=16)
    preflight.add_argument("--precision", choices=("fp32", "bf16"), default="bf16")
    preflight.add_argument("--run-mode", choices=("new", "resume"), required=True)
    preflight.add_argument(
        "--minimum-output-free-bytes",
        type=int,
        default=100_000_000_000,
    )
    preflight.add_argument(
        "--minimum-shared-memory-bytes",
        type=int,
        default=8_000_000_000,
    )
    preflight.add_argument(
        "--minimum-cuda-free-bytes",
        type=int,
        default=16_000_000_000,
    )
    preflight.add_argument("--sample-probe-count", type=int, default=4)

    nightly = subparsers.add_parser("nightly")
    nightly.add_argument("--run-dir", type=Path, required=True)
    nightly.add_argument("--preflight-report", type=Path, required=True)
    nightly.add_argument("--report-path", type=Path, required=True)
    nightly.add_argument(
        "--shared-service-recovery",
        choices=("passed", "failed", "not_required"),
        required=True,
    )
    return parser


def main() -> int:
    args = _build_parser().parse_args()
    if args.command == "preflight":
        return _write_report(
            args.report_path,
            lambda: build_training_preflight_report(
                profile_path=args.profile,
                repository_root=args.repository_root,
                data_root=args.data_root,
                output_dir=args.output_dir,
                device_name=args.device,
                batch_size=args.batch_size,
                precision=args.precision,
                run_mode=args.run_mode,
                minimum_output_free_bytes=args.minimum_output_free_bytes,
                minimum_shared_memory_bytes=args.minimum_shared_memory_bytes,
                minimum_cuda_free_bytes=args.minimum_cuda_free_bytes,
                sample_probe_count=args.sample_probe_count,
            ),
        )
    try:
        preflight_report = json.loads(args.preflight_report.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        preflight_report = {"status": "failed"}
    return _write_report(
        args.report_path,
        lambda: build_nightly_training_report(
            run_dir=args.run_dir,
            preflight_report=preflight_report,
            shared_service_recovery=args.shared_service_recovery,
        ),
    )


if __name__ == "__main__":
    raise SystemExit(main())
