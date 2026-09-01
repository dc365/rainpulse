from __future__ import annotations

import hashlib
import json
import pickle
import sys
from pathlib import Path
from types import SimpleNamespace

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
    DeterministicStepBatchSampler,
    MRMSZarrTrainingDataset,
    TrainingDataError,
    build_deterministic_training_dataloader,
    downsample_all_valid_2x2,
)
from rainpulse_algo.training.evolution_train import (
    EvolutionTrainError,
    compare_resume_metrics,
    deterministic_batch_indices,
)
from rainpulse_algo.training.generative_profile import (
    GenerativeTrainingProfileError,
    load_generative_training_profile,
)
from rainpulse_algo.training.generative_train import (
    GenerativeTrainError,
    compare_generative_metrics,
)
from rainpulse_algo.training.profile import (
    NowcastNetTrainingRunError,
    load_nowcastnet_training_run_profile,
)
from rainpulse_algo.training.runtime_report import (
    _checkpoint_preflight,
    build_nightly_training_report,
    build_training_preflight_report,
)

REPOSITORY_ROOT = Path(__file__).resolve().parents[2]
PROFILE_PATH = (
    REPOSITORY_ROOT / "configs" / "training" / "nowcastnet-mrms-run-v1.yaml"
)
FOUNDATION_PROFILE_PATH = (
    REPOSITORY_ROOT / "configs" / "training" / "nowcastnet-mrms-foundation-v1.yaml"
)
SCHEMA_PATH = (
    REPOSITORY_ROOT / "configs" / "schemas" / "nowcastnet-training-run.schema.json"
)
FOUNDATION_SCHEMA_PATH = (
    REPOSITORY_ROOT / "configs" / "schemas" / "nowcastnet-foundation-run.schema.json"
)
PREFLIGHT_SCHEMA_PATH = (
    REPOSITORY_ROOT / "configs" / "schemas" / "nowcastnet-training-preflight.schema.json"
)
NIGHTLY_SCHEMA_PATH = (
    REPOSITORY_ROOT / "configs" / "schemas" / "nowcastnet-nightly-report.schema.json"
)
SYSTEMD_REHEARSAL_EVIDENCE_PATH = (
    REPOSITORY_ROOT
    / "configs"
    / "training"
    / "evidence"
    / "nowcastnet-foundation-systemd-rehearsal-v1.json"
)
NIGHTLY_RUNBOOK_PATH = (
    REPOSITORY_ROOT / "docs" / "nowcastnet-training" / "RUNBOOK_NIGHTLY.md"
)
GENERATIVE_PROFILE_PATH = (
    REPOSITORY_ROOT / "configs" / "training" / "nowcastnet-mrms-generative-v1.yaml"
)
GENERATIVE_SCHEMA_PATH = (
    REPOSITORY_ROOT / "configs" / "schemas" / "nowcastnet-generative.schema.json"
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


def _write_full_fixture_dataset(root: Path) -> str:
    digest = _write_fixture_dataset(root)
    (root / "pilot-report.json").unlink()
    full_profile_sha256 = "a" * 64
    plan_id = "b" * 64
    (root / "full-sample-report.json").write_text(
        json.dumps(
            {
                "status": "complete",
                "dataset_version": "nowcastnet-mrms-full-samples-v1",
                "full_sample_profile_sha256": full_profile_sha256,
                "plan_id": plan_id,
                "processed_window_count": 1,
                "sample_count": 2,
                "sample_index_sha256": digest,
                "all_samples_valid": True,
                "holdout_windows_processed": 0,
            }
        ),
        encoding="utf-8",
    )
    (root / "validation-report.json").write_text(
        json.dumps(
            {
                "status": "passed",
                "validation_scope": "complete_library",
                "dataset_version": "nowcastnet-mrms-full-samples-v1",
                "full_sample_profile_sha256": full_profile_sha256,
                "plan_id": plan_id,
                "shard_count": 1,
                "sample_count": 2,
                "sample_index_sha256": digest,
                "content_hash_verified": True,
                "holdout_windows_processed": 0,
            }
        ),
        encoding="utf-8",
    )
    group = zarr.open_group(str(root / "shards" / "shard-00000.zarr"), mode="a")
    group.attrs.update(
        {
            "schema_version": "rainpulse.nowcastnet-mrms-full-sample-shard/1.0",
            "dataset_version": "nowcastnet-mrms-full-samples-v1",
            "full_sample_profile_sha256": full_profile_sha256,
        }
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


def test_repository_foundation_profile_binds_full_sample_plan_without_holdout() -> None:
    raw = yaml.safe_load(FOUNDATION_PROFILE_PATH.read_text(encoding="utf-8"))
    Draft202012Validator(json.loads(FOUNDATION_SCHEMA_PATH.read_text())).validate(raw)

    profile = load_nowcastnet_training_run_profile(
        FOUNDATION_PROFILE_PATH,
        repository_root=REPOSITORY_ROOT,
    )

    assert profile.profile_version == "nowcastnet-mrms-foundation-v1"
    assert raw["frozen_inputs"]["full_sample_library_evidence"] == {
        "path": "configs/training/evidence/nowcastnet-full-sample-library-v1.json",
        "sha256": "aa04f27b6b43907974310684150e2f6cea6b4dfd7c56c20b7e098a0d51a3c692",
    }
    assert profile.sample_index_sha256 == (
        "e758c938c929020c64e17ea827b05431f08b059c4033fcaa3b27a72ec1decddf"
    )
    assert profile.foundation.data_root_env == "RAINPULSE_MRMS_FULL_ROOT"
    assert profile.foundation.dataset_contract == "full_sample_v1"
    assert profile.foundation.expected_sample_count == 100000
    assert profile.foundation.expected_shard_count == 4000
    assert profile.foundation.expected_dataset_version == "nowcastnet-mrms-full-samples-v1"
    assert profile.foundation.expected_profile_sha256 == (
        "c84c76c399c9a0d74f94dea608bc4030e71b482a5247fc821fc7e245fe034be5"
    )
    assert profile.foundation.expected_plan_id == (
        "5ce3859f8f914c9cd55ea92a96a5dd860d46e297fff229472843d21e0bc26892"
    )
    assert profile.foundation.require_validation_report is True
    assert profile.data_loading.worker_count == 4
    assert profile.data_loading.prefetch_factor == 2
    assert profile.data_loading.pin_memory is True
    assert profile.checkpoint.maximum_interval_steps == 5000
    assert profile.checkpoint.maximum_interval_seconds == 1800


def test_systemd_rehearsal_evidence_keeps_formal_training_closed() -> None:
    evidence = json.loads(SYSTEMD_REHEARSAL_EVIDENCE_PATH.read_text())
    invocations = evidence["validated_invocations"]

    assert evidence["status"] == "passed"
    assert evidence["identity"]["holdout_windows_processed"] == 0
    assert evidence["runtime"]["kill_mode"] == "mixed"
    assert [invocation["run_mode"] for invocation in invocations] == [
        "new",
        "resume",
    ]
    assert invocations[1]["checkpoint_state_exact"] is True
    assert invocations[1]["random_state_exact"] is True
    assert invocations[1]["shared_service_recovery"] == "passed"
    assert evidence["decision"] == {
        "systemd_rehearsal_gate_passed": True,
        "formal_evolution_training_started": False,
        "rehearsal_checkpoint_promoted": False,
        "independent_holdout_opened": False,
        "next_gate": (
            "approve_rehearsal_checkpoint_promotion_and_enable_the_formal_nightly_schedule"
        ),
    }
    assert evidence["operational_eligible"] is False
    runbook = NIGHTLY_RUNBOOK_PATH.read_text()
    assert "`KillMode=mixed`" in runbook
    assert "默认 `control-group` 模式" in runbook


def test_generative_profile_matches_schema_and_published_contract() -> None:
    raw = yaml.safe_load(GENERATIVE_PROFILE_PATH.read_text(encoding="utf-8"))
    Draft202012Validator(json.loads(GENERATIVE_SCHEMA_PATH.read_text())).validate(raw)

    profile = load_generative_training_profile(
        GENERATIVE_PROFILE_PATH,
        repository_root=REPOSITORY_ROOT,
    )

    assert profile.profile_version == "nowcastnet-mrms-generative-v1"
    assert profile.input_frames == 9
    assert profile.target_frames == 20
    assert profile.context_frames == 4
    assert profile.base_channels == 32
    assert profile.completed_pretraining_step == 300000
    assert profile.stage_b_smoke_minimum_step == 1000
    assert profile.ensemble_members == 4
    assert profile.adversarial_weight == 6.0
    assert profile.pool_weight == 20.0
    assert profile.generator_learning_rate == 3e-5
    assert profile.discriminator_learning_rate == 3e-5
    assert profile.total_steps == 500000


def test_generative_profile_rejects_an_official_training_source_claim(
    tmp_path: Path,
) -> None:
    raw = yaml.safe_load(GENERATIVE_PROFILE_PATH.read_text(encoding="utf-8"))
    raw["provenance"]["official_training_source_published"] = True
    path = tmp_path / "generative.yaml"
    path.write_text(yaml.safe_dump(raw, sort_keys=False), encoding="utf-8")

    with pytest.raises(GenerativeTrainingProfileError, match="differs from frozen v1"):
        load_generative_training_profile(path, repository_root=REPOSITORY_ROOT)


def test_generative_resume_comparison_checks_batches_and_all_losses(
    tmp_path: Path,
) -> None:
    reference_path = tmp_path / "reference.jsonl"
    resumed_path = tmp_path / "resumed.jsonl"
    rows = [
        {
            "global_step": step,
            "batch_indices_sha256": f"batch-{step}",
            "generator_loss_total": 100.0 + step,
            "generator_loss_adversarial": 0.5 + step / 100.0,
            "generator_loss_pool_regularization": 4.0 + step / 10.0,
            "discriminator_loss_total": 1.4 + step / 100.0,
        }
        for step in range(1, 7)
    ]
    reference_path.write_text(
        "".join(json.dumps(row) + "\n" for row in rows),
        encoding="utf-8",
    )
    resumed_path.write_text(
        "".join(json.dumps(row) + "\n" for row in rows),
        encoding="utf-8",
    )

    result = compare_generative_metrics(
        reference_path,
        resumed_path,
        resume_after_step=3,
        absolute_tolerance=0.05,
        relative_tolerance=0.01,
    )

    assert result["status"] == "passed"
    assert result["compared_steps"] == 3

    changed = [dict(row) for row in rows]
    changed[4]["generator_loss_pool_regularization"] = 99.0
    resumed_path.write_text(
        "".join(json.dumps(row) + "\n" for row in changed),
        encoding="utf-8",
    )
    with pytest.raises(GenerativeTrainError, match="step 5"):
        compare_generative_metrics(
            reference_path,
            resumed_path,
            resume_after_step=3,
            absolute_tolerance=0.05,
            relative_tolerance=0.01,
        )


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


def test_full_zarr_training_dataset_requires_final_validation_and_identity(
    tmp_path: Path,
) -> None:
    digest = _write_full_fixture_dataset(tmp_path)

    dataset = MRMSZarrTrainingDataset(
        tmp_path,
        expected_sample_index_sha256=None,
        expected_sample_count=2,
        expected_crop_size=8,
        expected_shard_count=1,
        dataset_contract="full_sample_v1",
        expected_dataset_version="nowcastnet-mrms-full-samples-v1",
        expected_profile_sha256="a" * 64,
        expected_plan_id="b" * 64,
        require_validation_report=True,
    )

    assert dataset.sample_index_sha256 == digest
    assert dataset.dataset_contract == "full_sample_v1"
    assert [sample.sample_id for sample in dataset.__getitems__([1, 0])] == [
        "fixture-1",
        "fixture-0",
    ]

    validation_path = tmp_path / "validation-report.json"
    validation = json.loads(validation_path.read_text())
    validation["holdout_windows_processed"] = 1
    validation_path.write_text(json.dumps(validation), encoding="utf-8")
    with pytest.raises(TrainingDataError, match="validation report"):
        MRMSZarrTrainingDataset(
            tmp_path,
            expected_sample_index_sha256=None,
            expected_sample_count=2,
            expected_crop_size=8,
            expected_shard_count=1,
            dataset_contract="full_sample_v1",
            expected_dataset_version="nowcastnet-mrms-full-samples-v1",
            expected_profile_sha256="a" * 64,
            expected_plan_id="b" * 64,
            require_validation_report=True,
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


def test_step_batch_sampler_preserves_global_step_identity_across_resume() -> None:
    sampler = DeterministicStepBatchSampler(
        dataset_size=100,
        batch_size=16,
        run_seed=2026083002,
        start_step=17,
        stop_step=20,
    )

    batches = list(sampler)

    assert len(sampler) == 3
    assert batches[0] == list(
        deterministic_batch_indices(
            dataset_size=100,
            batch_size=16,
            run_seed=2026083002,
            global_step=17,
        )
    )
    assert batches[2] == list(
        deterministic_batch_indices(
            dataset_size=100,
            batch_size=16,
            run_seed=2026083002,
            global_step=19,
        )
    )


def test_dataloader_emits_requested_indices_with_batched_shard_reads(
    tmp_path: Path,
) -> None:
    torch = pytest.importorskip("torch")
    digest = _write_fixture_dataset(tmp_path)
    dataset = MRMSZarrTrainingDataset(
        tmp_path,
        expected_sample_index_sha256=digest,
        expected_sample_count=2,
        expected_crop_size=8,
    )
    loader = build_deterministic_training_dataloader(
        dataset,
        batch_size=2,
        run_seed=17,
        start_step=0,
        stop_step=1,
        worker_count=0,
        prefetch_factor=1,
        persistent_workers=False,
        pin_memory=False,
        in_order=True,
    )

    batch = next(iter(loader))
    expected = deterministic_batch_indices(
        dataset_size=2,
        batch_size=2,
        run_seed=17,
        global_step=0,
    )
    assert tuple(int(value) for value in batch["sample_index"]) == expected
    assert tuple(batch["inputs"].shape) == (2, 9, 8, 8)
    assert batch["inputs"].dtype == torch.float32


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
    resumed_rows = [dict(row) for row in rows]
    resumed_rows[3]["loss_total"] += 0.01
    resumed_rows[3]["loss_accumulation"] += 0.01
    resumed_rows[3]["gradient_norm_before_clip"] += 10.0
    resumed.write_text(
        "\n".join(json.dumps(row) for row in resumed_rows) + "\n",
        encoding="utf-8",
    )

    result = compare_resume_metrics(
        reference,
        resumed,
        resume_after_step=3,
        absolute_tolerance=0.02,
        relative_tolerance=0.01,
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
            absolute_tolerance=0.02,
            relative_tolerance=0.01,
        )


def test_preflight_writes_a_sanitized_failure_report_for_incomplete_data(
    tmp_path: Path,
) -> None:
    report = build_training_preflight_report(
        profile_path=FOUNDATION_PROFILE_PATH,
        repository_root=REPOSITORY_ROOT,
        data_root=tmp_path / "private-full-samples",
        output_dir=tmp_path / "private-run",
        device_name="cpu",
        batch_size=16,
        precision="bf16",
        run_mode="new",
        minimum_output_free_bytes=1,
        minimum_shared_memory_bytes=0,
        minimum_cuda_free_bytes=0,
        require_clean_training_tree=False,
        sample_probe_count=1,
    )

    assert report["status"] == "failed"
    assert report["checks"]["profile_contract"]["status"] == "passed"
    assert report["checks"]["dataset_contract"]["status"] == "failed"
    assert str(tmp_path) not in json.dumps(report)
    assert report["operational_eligible"] is False
    Draft202012Validator(json.loads(PREFLIGHT_SCHEMA_PATH.read_text())).validate(report)


def test_checkpoint_preflight_reports_a_corrupt_checkpoint_as_failed(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    def reject_corrupt_checkpoint(*args: object, **kwargs: object) -> None:
        raise pickle.UnpicklingError("invalid load key")

    monkeypatch.setitem(
        sys.modules,
        "torch",
        SimpleNamespace(load=reject_corrupt_checkpoint),
    )
    output_dir = tmp_path / "run"
    checkpoint = output_dir / "checkpoints" / "evolution-step-000000006.pt"
    checkpoint.parent.mkdir(parents=True)
    checkpoint.write_bytes(b"not-a-pytorch-checkpoint")
    (output_dir / "LATEST.json").write_text(
        json.dumps(
            {
                "global_step": 6,
                "path": "checkpoints/evolution-step-000000006.pt",
                "sha256": _sha256(checkpoint),
            }
        ),
        encoding="utf-8",
    )

    report = _checkpoint_preflight(
        output_dir=output_dir,
        run_mode="resume",
        profile=SimpleNamespace(
            profile_sha256="a" * 64,
            foundation=SimpleNamespace(name="full_sample_v1"),
        ),
        code_revision="b" * 40,
        sample_index_sha256="c" * 64,
        batch_size=16,
        precision="bf16",
    )

    assert report == {
        "status": "failed",
        "reason": "resume_checkpoint_load_failed",
    }


def test_nightly_report_summarizes_only_the_current_invocation(tmp_path: Path) -> None:
    run_dir = tmp_path / "run"
    checkpoint_dir = run_dir / "checkpoints"
    checkpoint_dir.mkdir(parents=True)
    checkpoint = checkpoint_dir / "evolution-step-000000006.pt"
    checkpoint.write_bytes(b"checkpoint")
    checkpoint_sha256 = _sha256(checkpoint)
    (run_dir / "LATEST.json").write_text(
        json.dumps(
            {
                "schema_version": "1.0",
                "global_step": 6,
                "path": "checkpoints/evolution-step-000000006.pt",
                "sha256": checkpoint_sha256,
            }
        ),
        encoding="utf-8",
    )
    metrics = [
        {
            "global_step": step,
            "loss_total": float(step),
            "duration_seconds": 2.0,
        }
        for step in range(1, 7)
    ]
    (run_dir / "metrics.jsonl").write_text(
        "".join(json.dumps(row) + "\n" for row in metrics),
        encoding="utf-8",
    )
    training_report = {
        "schema_version": "1.0",
        "status": "stopped",
        "started_at": "2026-08-31T12:00:00+00:00",
        "finished_at": "2026-08-31T12:00:06+00:00",
        "profile_sha256": "a" * 64,
        "code_revision": "b" * 40,
        "sample_index_sha256": "c" * 64,
        "device": "cuda",
        "batch_size": 16,
        "precision": "bf16",
        "resumed": True,
        "global_step": 6,
        "invocation_start_step": 3,
        "invocation_target_step": 300000,
        "planned_total_steps": 300000,
        "stop_signal": "SIGTERM",
        "input_checkpoint": {"global_step": 3, "sha256": "d" * 64},
        "latest_checkpoint": {
            "global_step": 6,
            "sha256": checkpoint_sha256,
        },
        "invocation_duration_seconds": 6.0,
        "peak_allocated_memory_bytes": 123,
        "peak_reserved_memory_bytes": 456,
        "operational_eligible": False,
    }
    (run_dir / "training-report.json").write_text(
        json.dumps(training_report),
        encoding="utf-8",
    )
    preflight = {
        "schema_version": "rainpulse.nowcastnet-training-preflight/1.0",
        "status": "passed",
        "preflight_id": "e" * 64,
        "code_revision": "b" * 40,
        "profile_sha256": "a" * 64,
        "sample_index_sha256": "c" * 64,
        "run_mode": "resume",
        "device": "cuda",
        "batch_size": 16,
        "precision": "bf16",
        "training_start_allowed": True,
        "checks": {
            "checkpoint": {
                "status": "passed",
                "mode": "resume",
                "resume_step": 3,
                "checkpoint_sha256": "d" * 64,
            }
        },
    }

    report = build_nightly_training_report(
        run_dir=run_dir,
        preflight_report=preflight,
        shared_service_recovery="passed",
    )

    assert report["status"] == "passed"
    assert report["steps"] == {
        "start": 3,
        "end": 6,
        "completed_this_invocation": 3,
        "planned_total": 300000,
    }
    assert report["loss_summary"]["loss_total"]["mean"] == pytest.approx(5.0)
    assert report["stop_reason"] == "signal:SIGTERM"
    assert report["preflight_identity_verified"] is True
    assert report["next_night_auto_resume_allowed"] is True
    assert str(tmp_path) not in json.dumps(report)
    Draft202012Validator(json.loads(NIGHTLY_SCHEMA_PATH.read_text())).validate(report)

    mismatched = build_nightly_training_report(
        run_dir=run_dir,
        preflight_report={**preflight, "code_revision": "f" * 40},
        shared_service_recovery="passed",
    )
    assert mismatched["status"] == "failed"
    assert mismatched["preflight_identity_verified"] is False
    assert mismatched["next_night_auto_resume_allowed"] is False
