from __future__ import annotations

import json
from dataclasses import replace
from datetime import UTC, datetime, timedelta
from pathlib import Path

import numpy as np
import yaml
import zarr
from jsonschema import Draft202012Validator

from rainpulse_algo.datasets.mrms_pilot import (
    _aggregate_pilot_output,
    build_window_shard_arrays,
    crop_coordinates,
    load_mrms_pilot_profile,
    select_spatial_crops,
    select_temporal_windows,
    validate_pilot_output,
    write_zarr_shard,
)
from rainpulse_algo.datasets.mrms_precip import MRMSNativePrecipFrame

REPOSITORY_ROOT = Path(__file__).resolve().parents[2]
PILOT_PROFILE_PATH = (
    REPOSITORY_ROOT / "configs" / "training" / "nowcastnet-mrms-pilot-v1.yaml"
)
PILOT_SCHEMA_PATH = (
    REPOSITORY_ROOT / "configs" / "schemas" / "nowcastnet-mrms-pilot.schema.json"
)


def test_repository_pilot_profile_matches_schema_and_frozen_references() -> None:
    raw = yaml.safe_load(PILOT_PROFILE_PATH.read_text(encoding="utf-8"))
    Draft202012Validator(json.loads(PILOT_SCHEMA_PATH.read_text())).validate(raw)

    profile = load_mrms_pilot_profile(PILOT_PROFILE_PATH, repository_root=REPOSITORY_ROOT)

    assert profile.pilot_version == "nowcastnet-mrms-pilot-v1"
    assert profile.training_profile.profile_version == "nowcastnet-mrms-training-v1"
    assert profile.sample_count == 10000
    assert profile.shard_count == 400
    assert profile.samples_per_shard == 25
    assert profile.importance_crops_per_window == 20
    assert profile.uniform_crops_per_window == 5
    assert profile.holdout_window_index_emitted is False


def test_temporal_selection_is_deterministic_stratified_and_separated() -> None:
    profile = load_mrms_pilot_profile(PILOT_PROFILE_PATH, repository_root=REPOSITORY_ROOT)
    profile = replace(
        profile,
        years=(2019, 2020),
        windows_per_year=2,
        minimum_separation_minutes=360,
    )
    rows = []
    for year in profile.years:
        start = datetime(year, 1, 1, tzinfo=UTC)
        for index in range(5):
            issue = start + timedelta(hours=index * 12)
            rows.append(
                {
                    "window_id": f"window-{year}-{index}",
                    "split": "training",
                    "issue_time": issue.isoformat().replace("+00:00", "Z"),
                    "asset_start_index": index * 29,
                    "asset_end_index": index * 29 + 28,
                    "asset_count": 29,
                }
            )
    rows.append(
        {
            "window_id": "development-window",
            "split": "development",
            "issue_time": "2024-02-01T00:00:00Z",
            "asset_start_index": 0,
            "asset_end_index": 28,
            "asset_count": 29,
        }
    )

    first = select_temporal_windows(rows, profile)
    second = select_temporal_windows(rows, profile)

    assert first == second
    assert len(first) == 4
    selected_years = {
        datetime.fromisoformat(row["issue_time"].replace("Z", "+00:00")).year
        for row in first
    }
    assert selected_years == {2019, 2020}
    assert all(row["split"] == "training" for row in first)
    for year in profile.years:
        selected = sorted(
            datetime.fromisoformat(row["issue_time"].replace("Z", "+00:00"))
            for row in first
            if row["issue_time"].startswith(str(year))
        )
        assert selected[1] - selected[0] >= timedelta(hours=6)


def test_spatial_selection_preserves_validity_and_branch_counts() -> None:
    profile = load_mrms_pilot_profile(PILOT_PROFILE_PATH, repository_root=REPOSITORY_ROOT)
    profile = replace(
        profile,
        crop_size=4,
        candidate_stride_cells=2,
        crops_per_window=3,
        importance_crops_per_window=2,
        uniform_crops_per_window=1,
        minimum_origin_separation_cells=4,
        longitude_count=12,
        latitude_count=8,
        west_edge_deg=-100.0,
        east_edge_deg=-99.88,
        south_edge_deg=30.0,
        north_edge_deg=30.08,
    )
    aggregate_score = np.ones((8, 12), dtype="float32")
    aggregate_score[4:8, 8:12] = 1000.0
    common_valid = np.ones((8, 12), dtype=bool)
    common_valid[0:4, 0:4] = False

    crops = select_spatial_crops(aggregate_score, common_valid, profile, seed=17)

    assert [crop["branch"] for crop in crops].count("importance") == 2
    assert [crop["branch"] for crop in crops].count("uniform") == 1
    assert len({(crop["y_start"], crop["x_start"]) for crop in crops}) == 3
    for crop in crops:
        y_start = crop["y_start"]
        x_start = crop["x_start"]
        assert common_valid[y_start : y_start + 4, x_start : x_start + 4].all()
    assert max(crop["importance_score"] for crop in crops) >= 16000.0

    coordinates = crop_coordinates(profile, y_start=4, x_start=8)
    assert coordinates == {
        "west_edge_deg": -99.92,
        "east_edge_deg": -99.88,
        "south_edge_deg": 30.04,
        "north_edge_deg": 30.08,
    }


def test_zarr_shard_round_trip_keeps_explicit_valid_mask(tmp_path: Path) -> None:
    profile = load_mrms_pilot_profile(PILOT_PROFILE_PATH, repository_root=REPOSITORY_ROOT)
    profile = replace(
        profile,
        crop_size=4,
        crops_per_window=3,
        samples_per_shard=3,
    )
    rain_rate = np.arange(3 * 29 * 4 * 4, dtype="float16").reshape(3, 29, 4, 4)
    rain_rate %= np.float16(128.0)
    valid_mask = np.ones(rain_rate.shape, dtype="uint8")
    samples = [
        {
            "sample_id": f"sample-{index}",
            "sample_index_in_shard": index,
            "all_valid": True,
        }
        for index in range(3)
    ]

    report = write_zarr_shard(
        tmp_path / "shard-00000.zarr",
        rain_rate=rain_rate,
        valid_mask=valid_mask,
        samples=samples,
        attributes={"window_id": "window-test"},
        profile=profile,
    )

    group = zarr.open_group(str(tmp_path / "shard-00000.zarr"), mode="r")
    np.testing.assert_array_equal(group["rain_rate"][:], rain_rate)
    np.testing.assert_array_equal(group["valid_mask"][:], valid_mask)
    assert group.attrs["window_id"] == "window-test"
    assert group.attrs["missing_value_policy"] == "reject_any_missing"
    assert report["sample_count"] == 3
    assert report["logical_bytes"] == rain_rate.nbytes + valid_mask.nbytes
    assert len(report["content_sha256"]) == 64


def test_window_builder_caps_rain_and_emits_only_all_valid_crops(tmp_path: Path) -> None:
    profile = load_mrms_pilot_profile(PILOT_PROFILE_PATH, repository_root=REPOSITORY_ROOT)
    profile = replace(
        profile,
        crop_size=4,
        candidate_stride_cells=2,
        crops_per_window=3,
        importance_crops_per_window=2,
        uniform_crops_per_window=1,
        minimum_origin_separation_cells=4,
        longitude_count=12,
        latitude_count=8,
        west_edge_deg=-100.0,
        east_edge_deg=-99.88,
        south_edge_deg=30.0,
        north_edge_deg=30.08,
        samples_per_shard=3,
    )
    start = datetime(2019, 1, 1, tzinfo=UTC)
    frames: dict[str, MRMSNativePrecipFrame] = {}
    assets = []
    for index in range(29):
        valid_time = start + timedelta(minutes=10 * index)
        relative = f"raw/frame-{index:02d}.grib2.gz"
        rate = np.ones((8, 12), dtype="float32")
        rate[4:8, 8:12] = 20.0
        if index == 10:
            rate[:, :] = 200.0
        frames[relative] = MRMSNativePrecipFrame(
            valid_time=valid_time,
            rate_mm_h=rate,
            valid_mask=np.ones(rate.shape, dtype="uint8"),
            source_state=np.ones(rate.shape, dtype="int8"),
            longitudes=-99.995 + np.arange(12) * 0.01,
            latitudes=30.005 + np.arange(8) * 0.01,
            longitude_interval_deg=0.01,
            latitude_interval_deg=0.01,
            source_path=relative,
        )
        assets.append(
            {
                "asset_index": index,
                "relative_path": relative,
                "split": "training",
                "valid_time": valid_time.isoformat().replace("+00:00", "Z"),
            }
        )

    def reader(path: Path) -> MRMSNativePrecipFrame:
        return frames[path.relative_to(tmp_path).as_posix()]

    rain_rate, valid_mask, samples, diagnostics = build_window_shard_arrays(
        profile,
        dataset_root=tmp_path,
        asset_rows=assets,
        window={"window_id": "window-test", "split": "training"},
        reader=reader,
    )

    assert rain_rate.shape == (3, 29, 4, 4)
    assert np.isfinite(rain_rate).all()
    assert float(rain_rate.max()) == 128.0
    assert np.all(valid_mask == 1)
    assert [sample["branch"] for sample in samples].count("importance") == 2
    assert [sample["branch"] for sample in samples].count("uniform") == 1
    assert diagnostics["clipped_full_domain_pixel_count"] == 8 * 12
    assert diagnostics["common_valid_fraction"] == 1.0


def test_completed_pilot_validation_checks_hashes_and_training_readback(
    tmp_path: Path,
) -> None:
    profile = load_mrms_pilot_profile(PILOT_PROFILE_PATH, repository_root=REPOSITORY_ROOT)
    profile = replace(
        profile,
        crop_size=4,
        crops_per_window=3,
        importance_crops_per_window=2,
        uniform_crops_per_window=1,
        samples_per_shard=3,
        sample_count=3,
        shard_count=1,
        longitude_count=12,
        latitude_count=8,
        west_edge_deg=-100.0,
        east_edge_deg=-99.88,
        south_edge_deg=30.0,
        north_edge_deg=30.08,
    )
    output_root = tmp_path / "pilot"
    shard_path = output_root / "shards" / "shard-00000.zarr"
    rain_rate = np.arange(3 * 29 * 4 * 4, dtype="float16").reshape(3, 29, 4, 4)
    rain_rate %= np.float16(128.0)
    valid_mask = np.ones(rain_rate.shape, dtype="uint8")
    origins = ((0, 0), (0, 4), (4, 8))
    samples = []
    for index, (y_start, x_start) in enumerate(origins):
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
                "rain_rate_mean_mm_h": float(np.mean(rain_rate[index])),
                "rain_rate_max_mm_h": float(np.max(rain_rate[index])),
                "rain_pixel_fraction": float(np.mean(rain_rate[index] > 0.0)),
                "all_valid": True,
                "window_id": "window-test",
            }
        )
    shard_report = write_zarr_shard(
        shard_path,
        rain_rate=rain_rate,
        valid_mask=valid_mask,
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
    _aggregate_pilot_output(
        output_root,
        profile,
        plan,
        plan["selected_windows"],
        [shard_report],
        complete=True,
        started_at=datetime.now(UTC),
    )

    result = validate_pilot_output(
        profile,
        plan=plan,
        output_root=output_root,
        random_sample_count=2,
    )

    assert result["status"] == "passed"
    assert result["sample_count"] == 3
    assert result["shard_count"] == 1
    assert result["content_hash_verified"] is True
    assert result["random_read_sample_count"] == 2
    assert result["sampled_rain_min_mm_h"] >= 0.0
    assert result["sampled_rain_max_mm_h"] <= 128.0
