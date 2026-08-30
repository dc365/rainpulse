from __future__ import annotations

import hashlib
import json
from dataclasses import replace
from datetime import UTC, datetime
from pathlib import Path

import numpy as np
import pytest
import yaml
from jsonschema import Draft202012Validator

from rainpulse_algo.datasets.mrms_full_samples import (
    MRMSFullSampleError,
    _aggregate_output,
    enforce_capacity_gate,
    load_full_sample_plan,
    load_mrms_full_sample_profile,
    normalize_inventory_relative_path,
    resolve_storage_roots,
    validate_full_sample_output,
    write_full_sample_shard,
)
from rainpulse_algo.datasets.mrms_pilot import crop_coordinates

REPOSITORY_ROOT = Path(__file__).resolve().parents[2]
PROFILE_PATH = (
    REPOSITORY_ROOT / "configs" / "training" / "nowcastnet-mrms-full-samples-v1.yaml"
)
SCHEMA_PATH = (
    REPOSITORY_ROOT / "configs" / "schemas" / "nowcastnet-mrms-full-samples.schema.json"
)


def _profile():
    return load_mrms_full_sample_profile(PROFILE_PATH, repository_root=REPOSITORY_ROOT)


def test_repository_full_sample_profile_matches_schema_and_frozen_capacity() -> None:
    raw = yaml.safe_load(PROFILE_PATH.read_text(encoding="utf-8"))
    Draft202012Validator(json.loads(SCHEMA_PATH.read_text())).validate(raw)

    profile = _profile()

    assert profile.dataset_version == "nowcastnet-mrms-full-samples-v1"
    assert profile.sample_count == 100000
    assert profile.shard_count == 4000
    assert profile.windows_per_year == 800
    assert profile.minimum_free_bytes == 500000000000
    assert profile.maximum_logical_output_bytes == 600000000000
    assert profile.maximum_physical_output_bytes == 400000000000
    assert profile.raw_root_env == "RAINPULSE_MRMS_RAW_ROOT"
    assert profile.output_root_env == "RAINPULSE_MRMS_FULL_ROOT"
    assert profile.holdout_window_index_emitted is False


def test_storage_roots_require_absolute_non_overlapping_nfs_paths(tmp_path: Path) -> None:
    profile = _profile()
    raw_root = tmp_path / "raw"
    raw_root.mkdir()
    output_root = tmp_path / "derived" / "full-v1"

    actual = resolve_storage_roots(
        profile,
        environ={
            profile.raw_root_env: str(raw_root),
            profile.output_root_env: str(output_root),
        },
        filesystem_type="nfs",
    )

    assert actual == (raw_root.resolve(), output_root.resolve())
    with pytest.raises(MRMSFullSampleError, match="must not overlap"):
        resolve_storage_roots(
            profile,
            environ={
                profile.raw_root_env: str(raw_root),
                profile.output_root_env: str(raw_root / "samples"),
            },
            filesystem_type="nfs",
        )
    with pytest.raises(MRMSFullSampleError, match="approved network filesystem"):
        resolve_storage_roots(
            profile,
            environ={
                profile.raw_root_env: str(raw_root),
                profile.output_root_env: str(output_root),
            },
            filesystem_type="ext4",
        )


def test_inventory_paths_are_resolved_relative_to_the_explicit_raw_root() -> None:
    assert (
        normalize_inventory_relative_path(
            "raw/noaa-mrms-pds/CONUS/PrecipRate_00.00/frame.grib2.gz"
        )
        == "noaa-mrms-pds/CONUS/PrecipRate_00.00/frame.grib2.gz"
    )
    assert normalize_inventory_relative_path("year/frame.grib2.gz") == "year/frame.grib2.gz"
    with pytest.raises(MRMSFullSampleError, match="unsafe"):
        normalize_inventory_relative_path("../raw/frame.grib2.gz")


def test_capacity_gate_runs_for_existing_output_and_reserves_in_flight_shards(
    tmp_path: Path,
) -> None:
    profile = replace(
        _profile(),
        crop_size=4,
        samples_per_shard=1,
        minimum_free_bytes=0,
        maximum_physical_output_bytes=10000,
    )
    output_root = tmp_path / "full"
    output_root.mkdir()
    (output_root / "existing.bin").write_bytes(b"12345")

    result = enforce_capacity_gate(profile, output_root=output_root, in_flight_shards=1)

    assert result["physical_bytes"] == 5
    assert result["reserved_bytes"] == 29 * 4 * 4 * 3
    with pytest.raises(MRMSFullSampleError, match="physical-output gate"):
        enforce_capacity_gate(
            replace(profile, maximum_physical_output_bytes=100),
            output_root=output_root,
            in_flight_shards=1,
        )


def test_full_sample_plan_rejects_identity_or_holdout_drift(tmp_path: Path) -> None:
    profile = replace(_profile(), years=(2019,), windows_per_year=1, shard_count=1, sample_count=25)
    selected = [
        {
            "window_id": "training-window",
            "split": "training",
            "shard_index": 0,
        }
    ]
    identity = {
        "full_sample_profile_sha256": profile.profile_sha256,
        "asset_inventory_sha256": profile.asset_inventory_sha256,
        "window_index_sha256": profile.window_index_sha256,
        "selected_windows": selected,
    }
    plan = {
        "schema_version": "1.0",
        "plan_id": hashlib.sha256(
            json.dumps(identity, sort_keys=True, separators=(",", ":")).encode()
        ).hexdigest(),
        "dataset_version": profile.dataset_version,
        "full_sample_profile_sha256": profile.profile_sha256,
        "training_profile_sha256": profile.training_profile.profile_sha256,
        "asset_inventory_sha256": profile.asset_inventory_sha256,
        "window_index_sha256": profile.window_index_sha256,
        "selected_window_count": 1,
        "planned_sample_count": 25,
        "selected_windows": selected,
        "holdout_windows_selected": 0,
    }
    path = tmp_path / "plan.json"
    path.write_text(json.dumps(plan), encoding="utf-8")

    assert load_full_sample_plan(path, profile)["plan_id"] == plan["plan_id"]
    plan["holdout_windows_selected"] = 1
    path.write_text(json.dumps(plan), encoding="utf-8")
    with pytest.raises(MRMSFullSampleError, match="leakage boundary"):
        load_full_sample_plan(path, profile)


def test_partial_full_sample_shard_round_trip_and_validation(tmp_path: Path) -> None:
    profile = replace(
        _profile(),
        crop_size=4,
        crops_per_window=3,
        samples_per_shard=3,
        importance_crops_per_window=2,
        uniform_crops_per_window=1,
        longitude_count=12,
        latitude_count=8,
        west_edge_deg=-100.0,
        east_edge_deg=-99.88,
        south_edge_deg=30.0,
        north_edge_deg=30.08,
        sample_count=3,
        shard_count=1,
    )
    output_root = tmp_path / "full"
    shard_path = output_root / "shards" / "shard-00000.zarr"
    rain = np.arange(3 * 29 * 4 * 4, dtype="float16").reshape(3, 29, 4, 4)
    rain %= np.float16(128.0)
    mask = np.ones(rain.shape, dtype="uint8")
    samples = []
    for index, (y_start, x_start) in enumerate(((0, 0), (0, 4), (4, 8))):
        samples.append(
            {
                "sample_id": f"sample-{index}",
                "sample_index_in_shard": index,
                "branch": "importance" if index < 2 else "uniform",
                "importance_score": float(index + 1),
                "x_start": x_start,
                "y_start": y_start,
                "coordinates": crop_coordinates(
                    profile,
                    y_start=y_start,
                    x_start=x_start,
                ),
                "rain_rate_mean_mm_h": float(np.mean(rain[index])),
                "rain_rate_max_mm_h": float(np.max(rain[index])),
                "rain_pixel_fraction": float(np.mean(rain[index] > 0.0)),
                "all_valid": True,
                "window_id": "window-test",
            }
        )
    shard_report = write_full_sample_shard(
        shard_path,
        rain_rate=rain,
        valid_mask=mask,
        samples=samples,
        attributes={"window_id": "window-test", "shard_index": 0},
        profile=profile,
    )
    plan = {
        "plan_id": "plan-test",
        "selected_windows": [
            {"window_id": "window-test", "shard_index": 0, "split": "training"}
        ],
    }
    _aggregate_output(
        output_root,
        profile,
        plan,
        plan["selected_windows"],
        [shard_report],
        complete=False,
        started_at=datetime.now(UTC),
    )

    result = validate_full_sample_output(
        profile,
        plan=plan,
        output_root=output_root,
        random_sample_count=2,
        allow_partial=True,
        expected_windows=1,
    )

    assert result["status"] == "passed"
    assert result["validation_scope"] == "partial_smoke"
    assert result["sample_count"] == 3
    assert result["holdout_windows_processed"] == 0
    assert result["content_hash_verified"] is True
