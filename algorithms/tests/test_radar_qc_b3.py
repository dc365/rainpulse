from __future__ import annotations

from datetime import UTC, datetime, timedelta
from pathlib import Path

import numpy as np
import pytest

from rainpulse_algo.radar.qc_clutter import (
    build_static_ground_clutter_asset,
    clutter_asset_npz_arrays,
)
from rainpulse_algo.radar.qc_labels import (
    REQUIRED_QC_LABEL_CATEGORIES,
    RadarQCLabelInputError,
    build_label_manifest,
)
from rainpulse_algo.radar.qc_promotion import (
    load_qc_promotion_profile,
    summarize_qc_promotion,
)

REPOSITORY_ROOT = Path(__file__).resolve().parents[2]
PROMOTION_PROFILE_PATH = (
    REPOSITORY_ROOT / "configs" / "verification" / "fujian-qc-promotion-v1.yaml"
)


def _label_entry(
    *,
    process_id: str,
    partition: str,
    case_category: str,
    scan_suffix: int,
    label_values: np.ndarray | None = None,
    temporal_context: list[dict[str, str]] | None = None,
    cross_radar_context: list[dict[str, str]] | None = None,
) -> dict[str, object]:
    scan_id = f"10000000-0000-4000-8000-{scan_suffix:012d}"
    return {
        "process_id": process_id,
        "partition": partition,
        "case_category": case_category,
        "radar_id": "z9598",
        "scan_id": scan_id,
        "volume_end_time_utc": "2026-08-28T02:30:00Z",
        "input_uri": f"s3://rainpulse/radar/normalized/z9598/{scan_id}/volume.zarr",
        "label_values": (
            np.asarray(label_values, dtype="int8")
            if label_values is not None
            else np.asarray([[0, 1], [-1, 0]], dtype="int8")
        ),
        "annotator": "analyst-a",
        "label_source": "manual-radial-review",
        "label_version": "radar-qc-labels-v1",
        "review_status": "verified",
        "temporal_context": temporal_context or [],
        "cross_radar_context": cross_radar_context or [],
    }


def _clear_sky_sample(
    *,
    index: int,
    dbzh: np.ndarray,
    azimuth_deg: np.ndarray,
    range_m: np.ndarray,
) -> dict[str, object]:
    observed_at = datetime(2026, 1, 1, tzinfo=UTC) + timedelta(
        days=index % 20,
        hours=index // 20,
    )
    return {
        "process_id": f"clear-{index:03d}",
        "case_category": "clear_sky",
        "radar_id": "z9598",
        "sweep_name": "sweep_000",
        "elevation_deg": 0.5,
        "observed_at_utc": observed_at.isoformat().replace("+00:00", "Z"),
        "azimuth_deg": azimuth_deg,
        "range_m": range_m,
        "dbzh": dbzh,
    }


def _promotion_row(
    *,
    process_id: str,
    case_category: str,
    subset: str,
    anomaly_precision_candidate: float | None = 0.80,
    anomaly_precision_reference: float | None = 0.75,
    anomaly_recall_candidate: float | None = 0.78,
    anomaly_recall_reference: float | None = 0.73,
    meteorological_retention_candidate: float | None = 0.997,
    meteorological_retention_reference: float | None = 0.998,
    qpe_rmse_candidate: float | None = 9.9,
    qpe_rmse_reference: float | None = 10.0,
) -> dict[str, object]:
    return {
        "process_id": process_id,
        "case_category": case_category,
        "subset": subset,
        "partition": "holdout",
        "anomaly_precision_candidate": anomaly_precision_candidate,
        "anomaly_precision_reference": anomaly_precision_reference,
        "anomaly_recall_candidate": anomaly_recall_candidate,
        "anomaly_recall_reference": anomaly_recall_reference,
        "meteorological_retention_candidate": meteorological_retention_candidate,
        "meteorological_retention_reference": meteorological_retention_reference,
        "qpe_rmse_candidate": qpe_rmse_candidate,
        "qpe_rmse_reference": qpe_rmse_reference,
    }


def test_label_manifest_rejects_process_partition_leakage() -> None:
    entries = [
        _label_entry(
            process_id="storm-001",
            partition="development",
            case_category="deep_convection",
            scan_suffix=1,
        ),
        _label_entry(
            process_id="storm-001",
            partition="holdout",
            case_category="deep_convection",
            scan_suffix=2,
        ),
    ]

    with pytest.raises(RadarQCLabelInputError, match="single partition"):
        build_label_manifest(
            entries,
            generated_at=datetime(2026, 9, 6, tzinfo=UTC),
            frozen_config_sha256="a" * 64,
            frozen_code_revision="3de4da3eaac8",
        )


def test_label_manifest_reports_insufficient_categories_by_independent_process_count() -> None:
    entries = [
        _label_entry(
            process_id=f"clear-{index}",
            partition="development",
            case_category="clear_sky",
            scan_suffix=10 + index,
        )
        for index in range(3)
    ]
    entries.extend(
        _label_entry(
            process_id=f"sea-{index}",
            partition="holdout",
            case_category="sea_ap",
            scan_suffix=20 + index,
        )
        for index in range(2)
    )

    manifest = build_label_manifest(
        entries,
        generated_at=datetime(2026, 9, 6, tzinfo=UTC),
        frozen_config_sha256="b" * 64,
        frozen_code_revision="3de4da3eaac8",
    )

    assert manifest["holdout_locked_after_freeze"] is True
    assert manifest["required_case_categories"] == list(REQUIRED_QC_LABEL_CATEGORIES)
    development = manifest["partition_summaries"]["development"]["case_categories"]
    holdout = manifest["partition_summaries"]["holdout"]["case_categories"]
    assert development["clear_sky"] == {
        "independent_process_count": 3,
        "status": "ready",
    }
    assert holdout["sea_ap"] == {
        "independent_process_count": 2,
        "status": "insufficient_data",
    }


def test_static_ground_clutter_asset_uses_beta_smoothing_and_support_thresholds() -> None:
    azimuth_deg = np.asarray([0.0], dtype="float32")
    range_m = np.asarray([250.0, 500.0], dtype="float32")
    samples = []
    for index in range(210):
        second_gate = 12.0 if index < 36 else 0.0
        if index >= 180:
            second_gate = np.nan
        samples.append(
            _clear_sky_sample(
                index=index,
                dbzh=np.asarray(
                    [[12.0 if index < 42 else 0.0, second_gate]],
                    dtype="float32",
                ),
                azimuth_deg=azimuth_deg,
                range_m=range_m,
            )
        )

    asset = build_static_ground_clutter_asset(samples)
    prior = asset.probability_by_sweep["sweep_000"]
    support = asset.support_count_by_sweep["sweep_000"]
    arrays = clutter_asset_npz_arrays(asset)

    assert prior[0, 0] == pytest.approx((42.0 + 1.0) / (210.0 + 2.0))
    assert support[0, 0] == 210
    assert np.isnan(prior[0, 1])
    assert support[0, 1] == 180
    assert asset.clear_sky_day_count_by_sweep["sweep_000"] == 20
    assert set(arrays) == {"sweep_000__ground_clutter"}


def test_static_ground_clutter_asset_requires_twenty_distinct_clear_sky_days() -> None:
    azimuth_deg = np.asarray([0.0], dtype="float32")
    range_m = np.asarray([250.0], dtype="float32")
    samples = []
    for index in range(209):
        observed_at = datetime(2026, 1, 1, tzinfo=UTC) + timedelta(
            days=index % 19,
            hours=index // 19,
        )
        samples.append(
            {
                "process_id": f"clear-19d-{index:03d}",
                "case_category": "clear_sky",
                "radar_id": "z9598",
                "sweep_name": "sweep_000",
                "elevation_deg": 0.5,
                "observed_at_utc": observed_at.isoformat().replace("+00:00", "Z"),
                "azimuth_deg": azimuth_deg,
                "range_m": range_m,
                "dbzh": np.asarray([[12.0]], dtype="float32"),
            }
        )

    asset = build_static_ground_clutter_asset(samples)

    assert np.isnan(asset.probability_by_sweep["sweep_000"]).all()
    assert asset.clear_sky_day_count_by_sweep["sweep_000"] == 19


def test_qc_promotion_summary_fails_when_strong_echo_subset_degrades() -> None:
    profile = load_qc_promotion_profile(PROMOTION_PROFILE_PATH)
    rows: list[dict[str, object]] = []
    for category in REQUIRED_QC_LABEL_CATEGORIES:
        for process_index in range(3):
            process_id = f"{category}-{process_index}"
            rows.append(
                _promotion_row(
                    process_id=process_id,
                    case_category=category,
                    subset="overall",
                )
            )
            rows.append(
                _promotion_row(
                    process_id=process_id,
                    case_category=category,
                    subset="strong_echo_ge_40_dbz",
                    anomaly_recall_candidate=0.60,
                    anomaly_recall_reference=0.74,
                )
            )

    summary = summarize_qc_promotion(rows, profile=profile)

    assert summary["overall_status"] == "failed"
    strong_echo = next(
        scope for scope in summary["scopes"] if scope["scope_id"] == "subset:strong_echo_ge_40_dbz"
    )
    assert strong_echo["status"] == "failed"
    assert strong_echo["gates"]["anomaly_recall"]["passes"] is False


def test_qc_promotion_summary_marks_missing_category_evidence_insufficient() -> None:
    profile = load_qc_promotion_profile(PROMOTION_PROFILE_PATH)
    rows: list[dict[str, object]] = []
    for category in REQUIRED_QC_LABEL_CATEGORIES:
        process_count = 2 if category == "typhoon_or_mountain" else 3
        for process_index in range(process_count):
            process_id = f"{category}-{process_index}"
            rows.append(
                _promotion_row(
                    process_id=process_id,
                    case_category=category,
                    subset="overall",
                )
            )
            rows.append(
                _promotion_row(
                    process_id=process_id,
                    case_category=category,
                    subset="strong_echo_ge_40_dbz",
                )
            )

    summary = summarize_qc_promotion(rows, profile=profile)

    assert summary["overall_status"] == "insufficient_data"
    typhoon = next(
        scope for scope in summary["scopes"] if scope["scope_id"] == "category:typhoon_or_mountain"
    )
    assert typhoon["status"] == "insufficient_data"
    assert typhoon["independent_process_count"] == 2


def test_promotion_cannot_pass_on_development_data() -> None:
    profile = load_qc_promotion_profile(PROMOTION_PROFILE_PATH)
    rows = [
        _promotion_row(process_id=f"{category}-{i}", case_category=category, subset=subset)
        for category in REQUIRED_QC_LABEL_CATEGORIES
        for i in range(3)
        for subset in ("overall", "strong_echo_ge_40_dbz")
    ]
    for row in rows:
        row["partition"] = "development"
    summary = summarize_qc_promotion(rows, profile=profile)
    assert summary["overall_status"] == "insufficient_data"
    assert summary["evaluated_partition"] == "holdout"


def test_promotion_requires_strong_echo_evidence() -> None:
    profile = load_qc_promotion_profile(PROMOTION_PROFILE_PATH)
    rows = [
        _promotion_row(process_id=f"{category}-{i}", case_category=category, subset="overall")
        for category in REQUIRED_QC_LABEL_CATEGORIES
        for i in range(3)
    ]
    summary = summarize_qc_promotion(rows, profile=profile)
    assert summary["overall_status"] == "insufficient_data"
    strong = next(
        item for item in summary["scopes"] if item["scope_id"] == "subset:strong_echo_ge_40_dbz"
    )
    assert strong["status"] == "insufficient_data"


@pytest.mark.parametrize("changed", ["radar_id", "elevation_deg"])
def test_clutter_rejects_mixed_station_or_elevation(changed: str) -> None:
    from rainpulse_algo.radar.qc_clutter import RadarQCClutterInputError

    sample = _clear_sky_sample(
        index=0, dbzh=np.ones((1, 1)), azimuth_deg=np.array([0.0]), range_m=np.array([250.0])
    )
    sample["elevation_deg"] = 0.5
    other = dict(sample)
    other[changed] = "z9599" if changed == "radar_id" else 1.5
    with pytest.raises(RadarQCClutterInputError, match="radar|elevation"):
        build_static_ground_clutter_asset([sample, other])


def test_labels_reject_context_crossing_holdout_boundary() -> None:
    development = _label_entry(
        process_id="dev", partition="development", case_category="clear_sky", scan_suffix=901
    )
    holdout = _label_entry(
        process_id="hold", partition="holdout", case_category="clear_sky", scan_suffix=902
    )
    development["temporal_context"] = [
        {key: holdout[key] for key in ("radar_id", "scan_id", "input_uri")}
    ]
    with pytest.raises(RadarQCLabelInputError, match="partition"):
        build_label_manifest(
            [development, holdout],
            generated_at=datetime.now(UTC),
            frozen_config_sha256="a" * 64,
            frozen_code_revision="revision",
        )


def test_clutter_rejects_duplicate_observation_support() -> None:
    from rainpulse_algo.radar.qc_clutter import RadarQCClutterInputError

    sample = _clear_sky_sample(
        index=0, dbzh=np.ones((1, 1)), azimuth_deg=np.array([0.0]), range_m=np.array([250.0])
    )
    with pytest.raises(RadarQCClutterInputError, match="duplicate"):
        build_static_ground_clutter_asset([sample, dict(sample)])


def test_promotion_accepts_complete_holdout_evidence_as_engineering_only() -> None:
    profile = load_qc_promotion_profile(PROMOTION_PROFILE_PATH)
    rows = [
        _promotion_row(process_id=f"{category}-{i}", case_category=category, subset=subset)
        for category in REQUIRED_QC_LABEL_CATEGORIES
        for i in range(3)
        for subset in ("overall", "strong_echo_ge_40_dbz")
    ]
    report = summarize_qc_promotion(rows, profile=profile)
    assert report["overall_status"] == "passed"
    assert report["holdout_process_count"] == 21
    assert report["operational_eligible"] is False


def test_promotion_rejects_one_process_in_both_partitions() -> None:
    from rainpulse_algo.radar.qc_promotion import QCPromotionInputError

    row = _promotion_row(process_id="storm-1", case_category="clear_sky", subset="overall")
    with pytest.raises(QCPromotionInputError, match="single partition"):
        summarize_qc_promotion(
            [row, {**row, "partition": "development"}],
            profile=load_qc_promotion_profile(PROMOTION_PROFILE_PATH),
        )
