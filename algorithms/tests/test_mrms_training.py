from __future__ import annotations

import hashlib
import json
from datetime import UTC, date, datetime, timedelta
from pathlib import Path

import pytest
import yaml
from jsonschema import Draft202012Validator

from rainpulse_algo.datasets.mrms_training import (
    MRMSTrainingProfileError,
    audit_training_archive,
    load_mrms_training_profile,
)

REPOSITORY_ROOT = Path(__file__).resolve().parents[2]
PROFILE_PATH = (
    REPOSITORY_ROOT / "configs" / "training" / "nowcastnet-mrms-training-v1.yaml"
)
SCHEMA_PATH = (
    REPOSITORY_ROOT / "configs" / "schemas" / "nowcastnet-training-profile.schema.json"
)


def _sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def _write_manifest(
    root: Path,
    day: date,
    frame_count: int,
    *,
    gap_index: int | None = None,
) -> None:
    metadata = {
        "archive_start": "2019-01-01",
        "cadence_minutes": 10,
        "product": "PrecipRate_00.00",
        "source_cadence_minutes": 2,
    }
    (root / "dataset.json").parent.mkdir(parents=True, exist_ok=True)
    (root / "dataset.json").write_text(json.dumps(metadata), encoding="utf-8")
    start = datetime(day.year, day.month, day.day, tzinfo=UTC)
    assets = []
    for index in range(frame_count):
        if index == gap_index:
            continue
        valid_time = start + timedelta(minutes=10 * index)
        relative = Path(
            "raw/noaa-mrms-pds/CONUS/PrecipRate_00.00/10min"
        ) / f"{valid_time:%Y/%m/%d}" / (
            f"MRMS_PrecipRate_00.00_{valid_time:%Y%m%d-%H%M%S}.grib2.gz"
        )
        target = root / relative
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_bytes(f"frame-{index}".encode())
        assets.append(
            {
                "relative_path": relative.as_posix(),
                "sha256": _sha256(target),
                "size_bytes": target.stat().st_size,
                "source_id": "noaa-mrms-pds-CONUS-PrecipRate_00.00-v1",
                "valid_time": valid_time.isoformat().replace("+00:00", "Z"),
            }
        )
    manifest = {
        "assets": assets,
        "cadence_minutes": 10,
        "complete": frame_count == 144 and gap_index is None,
        "day": day.isoformat(),
        "expected_count": 144,
        "failures": [],
        "missing_source_times": [],
        "product": "PrecipRate_00.00",
    }
    path = (
        root
        / "manifests/PrecipRate_00.00/10min"
        / f"{day:%Y/%m/%d}.json"
    )
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(manifest), encoding="utf-8")


def test_repository_training_profile_matches_schema_and_frozen_split() -> None:
    raw = yaml.safe_load(PROFILE_PATH.read_text(encoding="utf-8"))
    Draft202012Validator(json.loads(SCHEMA_PATH.read_text())).validate(raw)

    profile = load_mrms_training_profile(PROFILE_PATH)

    assert profile.profile_version == "nowcastnet-mrms-training-v1"
    assert profile.input_frames == 9
    assert profile.target_frames == 20
    assert profile.total_frames == 29
    assert profile.pilot_sample_count == 10000
    assert profile.pilot_minimum_free_bytes == 120000000000
    assert profile.pilot_maximum_output_bytes == 80000000000
    assert profile.full_sample_minimum_free_bytes == 500000000000
    assert profile.split_for(datetime(2023, 12, 1, tzinfo=UTC)) == "training"
    assert profile.split_for(datetime(2024, 2, 1, tzinfo=UTC)) == "development"
    assert profile.split_for(datetime(2025, 2, 1, tzinfo=UTC)) == "independent_holdout"
    assert profile.split_for(datetime(2024, 7, 1, tzinfo=UTC)) == "spent_verification"
    assert profile.split_for(datetime(2024, 1, 1, tzinfo=UTC)) == "reserve"
    assert profile.holdout_open is False


def test_profile_rejects_development_and_holdout_leakage(tmp_path: Path) -> None:
    raw = yaml.safe_load(PROFILE_PATH.read_text(encoding="utf-8"))
    raw["splits"]["development_months"].append("2025-02")
    path = tmp_path / "profile.yaml"
    path.write_text(yaml.safe_dump(raw, sort_keys=False), encoding="utf-8")

    with pytest.raises(MRMSTrainingProfileError, match="overlap"):
        load_mrms_training_profile(path)


def test_audit_builds_only_continuous_29_frame_windows(tmp_path: Path) -> None:
    root = tmp_path / "dataset"
    _write_manifest(root, date(2019, 1, 1), 31)
    output = tmp_path / "audit"

    report = audit_training_archive(
        load_mrms_training_profile(PROFILE_PATH),
        dataset_root=root,
        output_root=output,
        start=date(2019, 1, 1),
        end=date(2019, 1, 1),
    )

    assert report["transport_integrity"] is True
    assert report["duration_seconds"] >= 0
    assert report["asset_count"] == 31
    assert report["missing_slot_count"] == 113
    assert report["splits"]["training"]["eligible_window_count"] == 3
    assert report["splits"]["training"]["candidate_window_count"] == 116
    assert report["splits"]["training"]["rejected_window_count"] == 113
    assert report["holdout_gate"]["status"] == "closed"
    assert report["holdout_gate"]["forecast_results_read"] is False
    window_lines = (output / "window-index.jsonl").read_text().splitlines()
    assert len(window_lines) == 3
    first = json.loads(window_lines[0])
    assert first["asset_count"] == 29
    assert first["issue_time"] == "2019-01-01T01:20:00Z"
    assert first["split"] == "training"
    assert _sha256(output / "asset-inventory.jsonl") == report["asset_inventory_sha256"]
    assert _sha256(output / "window-index.jsonl") == report["window_index_sha256"]


def test_audit_preserves_gap_and_rejects_affected_windows(tmp_path: Path) -> None:
    root = tmp_path / "dataset"
    _write_manifest(root, date(2019, 1, 1), 31, gap_index=15)

    report = audit_training_archive(
        load_mrms_training_profile(PROFILE_PATH),
        dataset_root=root,
        output_root=tmp_path / "audit",
        start=date(2019, 1, 1),
        end=date(2019, 1, 1),
    )

    assert report["transport_integrity"] is True
    assert report["missing_slot_count"] == 114
    assert report["splits"]["training"]["eligible_window_count"] == 0
    assert report["splits"]["training"]["rejected_window_count"] == 116


def test_audit_counts_holdout_windows_but_keeps_index_closed(tmp_path: Path) -> None:
    root = tmp_path / "dataset"
    _write_manifest(root, date(2025, 2, 1), 31)
    output = tmp_path / "audit"

    report = audit_training_archive(
        load_mrms_training_profile(PROFILE_PATH),
        dataset_root=root,
        output_root=output,
        start=date(2025, 2, 1),
        end=date(2025, 2, 1),
    )

    holdout = report["splits"]["independent_holdout"]
    assert holdout["eligible_window_count"] == 3
    assert holdout["indexed_window_count"] == 0
    assert holdout["window_index_status"] == "closed_by_holdout_gate"
    assert report["holdout_gate"]["window_index_emitted"] is False
    assert (output / "window-index.jsonl").read_text() == ""


def test_audit_reports_missing_asset_without_treating_it_as_no_rain(tmp_path: Path) -> None:
    root = tmp_path / "dataset"
    _write_manifest(root, date(2019, 1, 1), 29)
    first_asset = next((root / "raw").rglob("*.grib2.gz"))
    first_asset.unlink()

    report = audit_training_archive(
        load_mrms_training_profile(PROFILE_PATH),
        dataset_root=root,
        output_root=tmp_path / "audit",
        start=date(2019, 1, 1),
        end=date(2019, 1, 1),
    )

    assert report["transport_integrity"] is False
    assert report["asset_count"] == 28
    assert report["error_count"] == 1
    assert report["incomplete_days"] == ["2019-01-01"]
    assert report["splits"]["training"]["eligible_window_count"] == 0
    assert "missing asset" in report["errors"][0]
