from __future__ import annotations

import hashlib
import json
from pathlib import Path

import numpy as np
import pytest
import yaml
import zarr
from jsonschema import Draft202012Validator

from rainpulse_algo.training.conformance import (
    materialize_conformance_arrays,
    select_conformance_windows,
)
from rainpulse_algo.training.data import (
    MRMSZarrTrainingDataset,
    TrainingDataError,
    downsample_all_valid_2x2,
)
from rainpulse_algo.training.evolution_train import (
    EvolutionTrainError,
    compare_resume_metrics,
    deterministic_batch_indices,
)
from rainpulse_algo.training.profile import (
    NowcastNetTrainingRunError,
    load_nowcastnet_training_run_profile,
)

REPOSITORY_ROOT = Path(__file__).resolve().parents[2]
PROFILE_PATH = (
    REPOSITORY_ROOT / "configs" / "training" / "nowcastnet-mrms-run-v1.yaml"
)
SCHEMA_PATH = (
    REPOSITORY_ROOT / "configs" / "schemas" / "nowcastnet-training-run.schema.json"
)


def _sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def _write_fixture_dataset(root: Path) -> str:
    shards = root / "shards"
    shard = shards / "shard-00000.zarr"
    group = zarr.open_group(str(shard), mode="w")
    rain = np.arange(2 * 29 * 8 * 8, dtype="float32").reshape(2, 29, 8, 8)
    rain = np.mod(rain, 129).astype("float16")
    mask = np.ones(rain.shape, dtype="uint8")
    group.create_dataset("rain_rate", data=rain, chunks=(1, 29, 8, 8))
    group.create_dataset("valid_mask", data=mask, chunks=(1, 29, 8, 8))
    group.attrs.update(
        {
            "schema_version": "rainpulse.nowcastnet-mrms-pilot-shard/1.0",
            "missing_value_policy": "reject_any_missing",
            "unit": "mm/h",
        }
    )

    samples = []
    for index in range(2):
        sample = {
            "sample_id": f"fixture-{index}",
            "shard_index": 0,
            "sample_index_in_shard": index,
            "branch": "importance" if index == 0 else "uniform",
            "all_valid": True,
            "window_id": "fixture-window",
        }
        samples.append(sample)
    lines = [
        json.dumps(sample, sort_keys=True, separators=(",", ":"))
        for sample in samples
    ]
    sample_index = root / "samples.jsonl"
    sample_index.parent.mkdir(parents=True, exist_ok=True)
    sample_index.write_text("\n".join(lines) + "\n", encoding="utf-8")
    digest = _sha256(sample_index)
    (root / "COMPLETED").write_text(digest + "\n", encoding="utf-8")
    (root / "pilot-report.json").write_text(
        json.dumps(
            {
                "status": "complete",
                "sample_count": 2,
                "sample_index_sha256": digest,
                "all_samples_valid": True,
                "holdout_windows_processed": 0,
            }
        ),
        encoding="utf-8",
    )
    return digest


def test_repository_run_profile_matches_schema_and_frozen_evidence() -> None:
    raw = yaml.safe_load(PROFILE_PATH.read_text(encoding="utf-8"))
    Draft202012Validator(json.loads(SCHEMA_PATH.read_text())).validate(raw)

    profile = load_nowcastnet_training_run_profile(
        PROFILE_PATH,
        repository_root=REPOSITORY_ROOT,
    )

    assert profile.profile_version == "nowcastnet-mrms-run-v1"
    assert profile.sequence == (9, 20, 29)
    assert profile.foundation.resolution_deg == 0.01
    assert profile.foundation.native_crop_size == 256
    assert profile.foundation.model_crop_size == 256
    assert profile.foundation.downsample_method == "none"
    assert profile.paper_conformance.resolution_deg == 0.02
    assert profile.paper_conformance.native_crop_size == 512
    assert profile.paper_conformance.model_crop_size == 256
    assert profile.paper_conformance.downsample_method == "block_mean_2x2"
    assert profile.paper_conformance.official_kernel_published is False
    assert profile.evolution.base_channels == 32
    assert profile.evolution.motion_regularization_lambda == 0.01
    assert profile.evolution.weight_cap == 24.0


def test_run_profile_rejects_claim_that_official_downsample_kernel_is_known(
    tmp_path: Path,
) -> None:
    raw = yaml.safe_load(PROFILE_PATH.read_text(encoding="utf-8"))
    raw["data_tracks"]["paper_conformance_0p02"]["official_kernel_published"] = True
    path = tmp_path / "run.yaml"
    path.write_text(yaml.safe_dump(raw, sort_keys=False), encoding="utf-8")

    with pytest.raises(NowcastNetTrainingRunError, match="not published"):
        load_nowcastnet_training_run_profile(path, repository_root=REPOSITORY_ROOT)


def test_downsample_uses_area_mean_and_rejects_missing() -> None:
    rain = np.asarray(
        [[[[1.0, 3.0, 5.0, 7.0], [5.0, 7.0, 9.0, 11.0]]]],
        dtype="float32",
    )
    mask = np.ones(rain.shape, dtype="uint8")

    reduced, reduced_mask = downsample_all_valid_2x2(rain, mask)

    np.testing.assert_array_equal(reduced, np.asarray([[[[4.0, 8.0]]]], dtype="float32"))
    np.testing.assert_array_equal(reduced_mask, np.ones((1, 1, 1, 2), dtype="uint8"))

    mask[..., 0, 0] = 0
    with pytest.raises(TrainingDataError, match="missing"):
        downsample_all_valid_2x2(rain, mask)


def test_zarr_training_dataset_preserves_sequence_and_metadata(tmp_path: Path) -> None:
    digest = _write_fixture_dataset(tmp_path)
    dataset = MRMSZarrTrainingDataset(
        tmp_path,
        expected_sample_index_sha256=digest,
        expected_sample_count=2,
        expected_crop_size=8,
        input_frames=9,
        target_frames=20,
    )

    assert len(dataset) == 2
    first = dataset[0]
    assert first.input_rate_mm_h.shape == (9, 8, 8)
    assert first.target_rate_mm_h.shape == (20, 8, 8)
    assert first.input_rate_mm_h.dtype == np.dtype("float32")
    assert first.target_rate_mm_h.dtype == np.dtype("float32")
    assert np.all(first.input_valid_mask == 1)
    assert np.all(first.target_valid_mask == 1)
    assert first.sample_id == "fixture-0"
    assert first.branch == "importance"
    assert first.window_id == "fixture-window"


def test_zarr_training_dataset_requires_a_matching_completion_marker(
    tmp_path: Path,
) -> None:
    digest = _write_fixture_dataset(tmp_path)
    (tmp_path / "COMPLETED").write_text("0" * 64 + "\n", encoding="utf-8")

    with pytest.raises(TrainingDataError, match="completion marker"):
        MRMSZarrTrainingDataset(
            tmp_path,
            expected_sample_index_sha256=digest,
            expected_sample_count=2,
            expected_crop_size=8,
        )


def test_conformance_window_selection_is_year_stratified_and_reindexed() -> None:
    windows = [
        {
            "window_id": f"{year}-{index}",
            "issue_time": f"{year}-01-{index + 1:02d}T00:00:00Z",
            "shard_index": year * 10 + index,
            "split": "training",
        }
        for year in range(2019, 2024)
        for index in range(5)
    ]

    selected = select_conformance_windows(windows, sample_count=16)

    assert len(selected) == 16
    assert [window["shard_index"] for window in selected] == list(range(16))
    counts = {
        year: sum(window["issue_time"].startswith(str(year)) for window in selected)
        for year in range(2019, 2024)
    }
    assert counts == {2019: 4, 2020: 3, 2021: 3, 2022: 3, 2023: 3}


def test_conformance_materialization_reduces_512_native_cells_to_256() -> None:
    rain = np.arange(29 * 8 * 8, dtype="float32").reshape(1, 29, 8, 8)
    mask = np.ones(rain.shape, dtype="uint8")

    reduced, reduced_mask = materialize_conformance_arrays(
        rain,
        mask,
        native_crop_size=8,
        model_crop_size=4,
    )

    assert reduced.shape == (1, 29, 4, 4)
    assert reduced.dtype == np.dtype("float16")
    assert reduced_mask.shape == reduced.shape
    assert np.all(reduced_mask == 1)


def test_training_batch_indices_are_step_deterministic_without_duplicates() -> None:
    first = deterministic_batch_indices(
        dataset_size=100,
        batch_size=16,
        run_seed=2026083002,
        global_step=17,
    )
    repeated = deterministic_batch_indices(
        dataset_size=100,
        batch_size=16,
        run_seed=2026083002,
        global_step=17,
    )
    following = deterministic_batch_indices(
        dataset_size=100,
        batch_size=16,
        run_seed=2026083002,
        global_step=18,
    )

    assert first == repeated
    assert len(first) == len(set(first)) == 16
    assert first != following


def test_resume_metric_comparison_checks_post_resume_trajectory(tmp_path: Path) -> None:
    reference = tmp_path / "reference.jsonl"
    resumed = tmp_path / "resumed.jsonl"
    rows = [
        {
            "global_step": step,
            "batch_indices_sha256": f"batch-{step}",
            "loss_total": float(step),
            "loss_accumulation": float(step) - 0.1,
            "loss_motion_regularization": 0.1,
            "gradient_norm_before_clip": 1.0,
        }
        for step in range(1, 7)
    ]
    payload = "\n".join(json.dumps(row) for row in rows) + "\n"
    reference.write_text(payload, encoding="utf-8")
    resumed.write_text(payload, encoding="utf-8")

    result = compare_resume_metrics(
        reference,
        resumed,
        resume_after_step=3,
        absolute_tolerance=0.0,
    )

    assert result["status"] == "passed"
    assert result["compared_steps"] == 3

    changed = list(rows)
    changed[-1] = {**changed[-1], "loss_total": 99.0}
    resumed.write_text(
        "\n".join(json.dumps(row) for row in changed) + "\n",
        encoding="utf-8",
    )
    with pytest.raises(EvolutionTrainError, match="trajectory differs"):
        compare_resume_metrics(
            reference,
            resumed,
            resume_after_step=3,
            absolute_tolerance=0.0,
        )
