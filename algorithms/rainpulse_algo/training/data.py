from __future__ import annotations

import hashlib
import json
from collections import OrderedDict
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import numpy as np
import zarr


class TrainingDataError(RuntimeError):
    """Raised when an offline training artifact violates its frozen contract."""


@dataclass(frozen=True)
class TrainingSample:
    input_rate_mm_h: np.ndarray
    target_rate_mm_h: np.ndarray
    input_valid_mask: np.ndarray
    target_valid_mask: np.ndarray
    sample_id: str
    branch: str
    window_id: str


def deterministic_batch_indices(
    *,
    dataset_size: int,
    batch_size: int,
    run_seed: int,
    global_step: int,
) -> tuple[int, ...]:
    if dataset_size < 1 or batch_size < 1 or batch_size > dataset_size or global_step < 0:
        raise TrainingDataError("deterministic batch dimensions are invalid")
    identity = f"{run_seed}:{global_step}".encode()
    seed = int.from_bytes(hashlib.sha256(identity).digest()[:8], "big", signed=False)
    generator = np.random.default_rng(seed)
    indices = generator.choice(dataset_size, size=batch_size, replace=False)
    return tuple(int(index) for index in indices)


class DeterministicStepBatchSampler:
    """Yield one reproducible, without-replacement batch for each optimizer step."""

    def __init__(
        self,
        *,
        dataset_size: int,
        batch_size: int,
        run_seed: int,
        start_step: int,
        stop_step: int,
    ) -> None:
        if start_step < 0 or stop_step <= start_step:
            raise TrainingDataError("deterministic sampler step boundary is invalid")
        self.dataset_size = dataset_size
        self.batch_size = batch_size
        self.run_seed = run_seed
        self.start_step = start_step
        self.stop_step = stop_step
        deterministic_batch_indices(
            dataset_size=dataset_size,
            batch_size=batch_size,
            run_seed=run_seed,
            global_step=start_step,
        )

    def __iter__(self):
        for global_step in range(self.start_step, self.stop_step):
            yield list(
                deterministic_batch_indices(
                    dataset_size=self.dataset_size,
                    batch_size=self.batch_size,
                    run_seed=self.run_seed,
                    global_step=global_step,
                )
            )

    def __len__(self) -> int:
        return self.stop_step - self.start_step


def _sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def downsample_all_valid_2x2(
    rain_rate_mm_h: np.ndarray,
    valid_mask: np.ndarray,
) -> tuple[np.ndarray, np.ndarray]:
    """Reduce both spatial axes by two using a conservative block mean.

    This is a RainPulse-owned preprocessing rule. The NowcastNet paper states
    that MRMS width and height were halved, but does not publish the resampling
    kernel. Missing values are rejected instead of being averaged as no rain.
    """

    rain = np.asarray(rain_rate_mm_h)
    mask = np.asarray(valid_mask)
    if rain.shape != mask.shape or rain.ndim < 2:
        raise TrainingDataError("rain rate and validity mask shapes differ")
    height, width = rain.shape[-2:]
    if height % 2 or width % 2:
        raise TrainingDataError("2x2 downsampling requires even spatial dimensions")
    if np.any(mask != 1):
        raise TrainingDataError("2x2 downsampling input contains missing values")
    if np.any(~np.isfinite(rain)):
        raise TrainingDataError("2x2 downsampling input contains non-finite values")

    reshaped = rain.astype("float32", copy=False).reshape(
        *rain.shape[:-2],
        height // 2,
        2,
        width // 2,
        2,
    )
    reduced = reshaped.mean(axis=(-3, -1), dtype="float32")
    reduced_mask = np.ones(reduced.shape, dtype="uint8")
    return np.ascontiguousarray(reduced), reduced_mask


class MRMSZarrTrainingDataset:
    """Random-access reader for completed RainPulse MRMS Zarr sample shards."""

    def __init__(
        self,
        root: Path,
        *,
        expected_sample_index_sha256: str | None,
        expected_sample_count: int,
        expected_crop_size: int,
        expected_shard_count: int | None = None,
        dataset_contract: str = "pilot_v1",
        expected_dataset_version: str | None = None,
        expected_profile_sha256: str | None = None,
        expected_plan_id: str | None = None,
        require_validation_report: bool = False,
        input_frames: int = 9,
        target_frames: int = 20,
        maximum_open_shards: int = 4,
    ) -> None:
        self.root = root.resolve()
        self.expected_crop_size = int(expected_crop_size)
        self.input_frames = int(input_frames)
        self.target_frames = int(target_frames)
        self.total_frames = self.input_frames + self.target_frames
        self.maximum_open_shards = int(maximum_open_shards)
        self.dataset_contract = str(dataset_contract)
        self.expected_dataset_version = expected_dataset_version
        self.expected_profile_sha256 = expected_profile_sha256
        self.expected_plan_id = expected_plan_id
        self.expected_shard_count = expected_shard_count
        if (
            self.expected_crop_size < 1
            or self.input_frames < 1
            or self.target_frames < 1
            or self.maximum_open_shards < 1
            or self.dataset_contract not in {"pilot_v1", "full_sample_v1"}
        ):
            raise TrainingDataError("training dataset dimensions or cache size are invalid")

        index_path = self.root / "samples.jsonl"
        report_path = self.root / (
            "pilot-report.json"
            if self.dataset_contract == "pilot_v1"
            else "full-sample-report.json"
        )
        marker_path = self.root / "COMPLETED"
        try:
            actual_sha256 = _sha256(index_path)
            marker = marker_path.read_text(encoding="utf-8").strip()
            report = json.loads(report_path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError) as exc:
            raise TrainingDataError(f"cannot open completed training dataset: {exc}") from exc
        if (
            expected_sample_index_sha256 is not None
            and actual_sha256 != expected_sample_index_sha256
        ):
            raise TrainingDataError("sample index SHA-256 differs from frozen evidence")
        if marker != actual_sha256:
            raise TrainingDataError("completion marker differs from the sample index")
        if (
            report.get("status") != "complete"
            or int(report.get("sample_count", -1)) != expected_sample_count
            or report.get("sample_index_sha256") != actual_sha256
            or report.get("all_samples_valid") is not True
            or int(report.get("holdout_windows_processed", -1)) != 0
            or (
                expected_shard_count is not None
                and int(report.get("processed_window_count", expected_shard_count))
                != expected_shard_count
            )
        ):
            raise TrainingDataError("training dataset report differs from frozen evidence")
        if self.dataset_contract == "full_sample_v1":
            if (
                report.get("dataset_version") != expected_dataset_version
                or report.get("full_sample_profile_sha256")
                != expected_profile_sha256
                or report.get("plan_id") != expected_plan_id
            ):
                raise TrainingDataError("full-sample dataset identity differs")
            validation_path = self.root / "validation-report.json"
            if require_validation_report:
                try:
                    validation = json.loads(validation_path.read_text(encoding="utf-8"))
                except (OSError, json.JSONDecodeError) as exc:
                    raise TrainingDataError(
                        f"cannot read full-sample validation report: {exc}"
                    ) from exc
                if (
                    validation.get("status") != "passed"
                    or validation.get("validation_scope") != "complete_library"
                    or validation.get("dataset_version") != expected_dataset_version
                    or validation.get("full_sample_profile_sha256")
                    != expected_profile_sha256
                    or validation.get("plan_id") != expected_plan_id
                    or int(validation.get("sample_count", -1))
                    != expected_sample_count
                    or (
                        expected_shard_count is not None
                        and int(validation.get("shard_count", -1))
                        != expected_shard_count
                    )
                    or validation.get("sample_index_sha256") != actual_sha256
                    or validation.get("content_hash_verified") is not True
                    or int(validation.get("holdout_windows_processed", -1)) != 0
                ):
                    raise TrainingDataError("full-sample validation report differs")

        try:
            samples = [
                json.loads(line)
                for line in index_path.read_text(encoding="utf-8").splitlines()
                if line
            ]
        except (OSError, json.JSONDecodeError) as exc:
            raise TrainingDataError(f"cannot read training sample index: {exc}") from exc
        if len(samples) != expected_sample_count:
            raise TrainingDataError("training sample index count differs from report")
        for sample in samples:
            if (
                not isinstance(sample, dict)
                or int(sample.get("shard_index", -1)) < 0
                or int(sample.get("sample_index_in_shard", -1)) < 0
                or sample.get("all_valid") is not True
                or sample.get("branch") not in {"importance", "uniform"}
                or not sample.get("sample_id")
                or not sample.get("window_id")
            ):
                raise TrainingDataError("training sample metadata is invalid")
        self._samples = samples
        self._shards: OrderedDict[int, Any] = OrderedDict()
        self.sample_index_sha256 = actual_sha256

    def __len__(self) -> int:
        return len(self._samples)

    def __getstate__(self) -> dict[str, Any]:
        state = dict(self.__dict__)
        state["_shards"] = OrderedDict()
        return state

    def _open_shard(self, shard_index: int) -> Any:
        cached = self._shards.pop(shard_index, None)
        if cached is not None:
            self._shards[shard_index] = cached
            return cached
        path = self.root / "shards" / f"shard-{shard_index:05d}.zarr"
        if not path.is_dir():
            raise TrainingDataError(f"training shard is missing: {path.name}")
        group = zarr.open_group(str(path), mode="r")
        if (
            group.attrs.get("missing_value_policy") != "reject_any_missing"
            or group.attrs.get("unit") != "mm/h"
            or (
                self.dataset_contract == "full_sample_v1"
                and (
                    group.attrs.get("schema_version")
                    != "rainpulse.nowcastnet-mrms-full-sample-shard/1.0"
                    or group.attrs.get("dataset_version")
                    != self.expected_dataset_version
                    or group.attrs.get("full_sample_profile_sha256")
                    != self.expected_profile_sha256
                )
            )
        ):
            raise TrainingDataError(f"training shard contract differs: {path.name}")
        expected_shape = (
            self.total_frames,
            self.expected_crop_size,
            self.expected_crop_size,
        )
        if (
            "rain_rate" not in group
            or "valid_mask" not in group
            or tuple(group["rain_rate"].shape[1:]) != expected_shape
            or tuple(group["valid_mask"].shape) != tuple(group["rain_rate"].shape)
        ):
            raise TrainingDataError(f"training shard arrays differ: {path.name}")
        self._shards[shard_index] = group
        while len(self._shards) > self.maximum_open_shards:
            self._shards.popitem(last=False)
        return group

    def _read_sample(self, index: int, group: Any | None = None) -> TrainingSample:
        sample = self._samples[index]
        shard_index = int(sample["shard_index"])
        offset = int(sample["sample_index_in_shard"])
        group = group if group is not None else self._open_shard(shard_index)
        if offset >= int(group["rain_rate"].shape[0]):
            raise TrainingDataError("training sample offset exceeds its shard")
        rain = np.asarray(group["rain_rate"][offset], dtype="float32")
        mask = np.asarray(group["valid_mask"][offset], dtype="uint8")
        if (
            rain.shape
            != (self.total_frames, self.expected_crop_size, self.expected_crop_size)
            or np.any(~np.isfinite(rain))
            or np.any(rain < 0.0)
            or np.any(rain > 128.0)
            or np.any(mask != 1)
        ):
            raise TrainingDataError("training sample values violate the frozen range")
        return TrainingSample(
            input_rate_mm_h=np.ascontiguousarray(rain[: self.input_frames]),
            target_rate_mm_h=np.ascontiguousarray(rain[self.input_frames :]),
            input_valid_mask=np.ascontiguousarray(mask[: self.input_frames]),
            target_valid_mask=np.ascontiguousarray(mask[self.input_frames :]),
            sample_id=str(sample["sample_id"]),
            branch=str(sample["branch"]),
            window_id=str(sample["window_id"]),
        )

    def __getitem__(self, index: int) -> TrainingSample:
        return self._read_sample(index)

    def __getitems__(self, indices: list[int]) -> list[TrainingSample]:
        grouped: dict[int, list[tuple[int, int]]] = {}
        for position, index in enumerate(indices):
            sample = self._samples[index]
            grouped.setdefault(int(sample["shard_index"]), []).append((position, index))
        output: list[TrainingSample | None] = [None] * len(indices)
        for shard_index, positions in grouped.items():
            group = self._open_shard(shard_index)
            for position, index in positions:
                output[position] = self._read_sample(index, group)
        if any(sample is None for sample in output):
            raise TrainingDataError("batched training sample read did not close")
        return [sample for sample in output if sample is not None]


class TorchMRMSZarrTrainingDataset:
    """PyTorch adapter kept import-safe for CPU environments without PyTorch."""

    def __init__(self, dataset: MRMSZarrTrainingDataset) -> None:
        self.dataset = dataset

    def __len__(self) -> int:
        return len(self.dataset)

    def __getitem__(self, index: int) -> dict[str, Any]:
        try:
            import torch
        except ImportError as exc:
            raise TrainingDataError("PyTorch is required for the training adapter") from exc
        sample = self.dataset[index]
        return self._to_torch(sample, index=index, torch=torch)

    @staticmethod
    def _to_torch(sample: TrainingSample, *, index: int, torch: Any) -> dict[str, Any]:
        return {
            "inputs": torch.from_numpy(sample.input_rate_mm_h),
            "targets": torch.from_numpy(sample.target_rate_mm_h),
            "input_valid_mask": torch.from_numpy(sample.input_valid_mask),
            "target_valid_mask": torch.from_numpy(sample.target_valid_mask),
            "sample_id": sample.sample_id,
            "branch": sample.branch,
            "window_id": sample.window_id,
            "sample_index": index,
        }

    def __getitems__(self, indices: list[int]) -> list[dict[str, Any]]:
        try:
            import torch
        except ImportError as exc:
            raise TrainingDataError("PyTorch is required for the training adapter") from exc
        samples = self.dataset.__getitems__(indices)
        return [
            self._to_torch(sample, index=index, torch=torch)
            for index, sample in zip(indices, samples, strict=True)
        ]


def build_deterministic_training_dataloader(
    dataset: MRMSZarrTrainingDataset,
    *,
    batch_size: int,
    run_seed: int,
    start_step: int,
    stop_step: int,
    worker_count: int,
    prefetch_factor: int,
    persistent_workers: bool,
    pin_memory: bool,
    in_order: bool,
):
    try:
        import torch
    except ImportError as exc:
        raise TrainingDataError("PyTorch is required for the training data loader") from exc
    if worker_count < 0 or prefetch_factor < 1:
        raise TrainingDataError("training data loader worker settings are invalid")
    sampler = DeterministicStepBatchSampler(
        dataset_size=len(dataset),
        batch_size=batch_size,
        run_seed=run_seed,
        start_step=start_step,
        stop_step=stop_step,
    )
    worker_options: dict[str, Any] = {}
    if worker_count > 0:
        worker_options.update(
            {
                "prefetch_factor": prefetch_factor,
                "persistent_workers": persistent_workers,
            }
        )
    return torch.utils.data.DataLoader(
        TorchMRMSZarrTrainingDataset(dataset),
        batch_sampler=sampler,
        num_workers=worker_count,
        pin_memory=pin_memory,
        in_order=in_order,
        **worker_options,
    )
