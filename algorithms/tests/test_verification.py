from __future__ import annotations

import numpy as np
import pytest

from rainpulse_algo.verification.deterministic import (
    DeterministicForecast,
    score_accumulation_forecasts,
    score_deterministic_forecasts,
    summarize_coverage,
    summarize_fss_skill,
)


def test_scores_all_models_on_one_common_mask_with_worked_contingency_values() -> None:
    truth = np.asarray([[[1.0, 1.0], [0.0, 0.0]]], dtype="float32")
    forecast = np.asarray([[[1.0, 0.0], [1.0, 0.0]]], dtype="float32")
    valid = np.ones(truth.shape, dtype="uint8")

    rows = score_deterministic_forecasts(
        truth,
        valid,
        {"lk": DeterministicForecast(forecast, valid)},
        lead_minutes=(10,),
        thresholds_mm_h=(0.5,),
        windows_pixels=(1,),
        pixel_spacing_km=1.0,
    )

    assert len(rows) == 1
    row = rows[0]
    assert row["hits"] == 1
    assert row["misses"] == 1
    assert row["false_alarms"] == 1
    assert row["correct_negatives"] == 1
    assert row["csi"] == pytest.approx(1 / 3)
    assert row["pod"] == pytest.approx(0.5)
    assert row["far"] == pytest.approx(0.5)
    assert row["fss"] == pytest.approx(0.5)
    assert row["mae_mm_h"] == pytest.approx(0.5)
    assert row["rmse_mm_h"] == pytest.approx(np.sqrt(0.5))
    assert row["common_coverage"] == 1.0
    assert row["forecast_to_truth_coverage"] == 1.0
    assert row["validity_domain"] == "common"
    assert row["window_km"] == 1.0


def test_fss_averages_only_neighbourhoods_centered_on_the_common_valid_domain() -> None:
    truth = np.asarray([[[1.0, 0.0, np.nan]]], dtype="float32")
    forecast = np.asarray([[[0.0, 1.0, np.nan]]], dtype="float32")
    valid = np.asarray([[[1, 1, 0]]], dtype="uint8")

    rows = score_deterministic_forecasts(
        truth,
        valid,
        {"lk": DeterministicForecast(forecast, valid)},
        lead_minutes=(10,),
        thresholds_mm_h=(0.5,),
        windows_pixels=(3,),
        pixel_spacing_km=1.0,
    )

    assert rows[0]["fss"] == pytest.approx(1.0)


def test_fixed_truth_domain_penalizes_missing_forecast_coverage() -> None:
    truth = np.asarray([[[2.0, 2.0, 0.0, 0.0]]], dtype="float32")
    truth_valid = np.ones(truth.shape, dtype="uint8")
    forecast = np.asarray([[[2.0, np.nan, 0.0, np.nan]]], dtype="float32")
    forecast_valid = np.asarray([[[1, 0, 1, 0]]], dtype="uint8")

    common = score_deterministic_forecasts(
        truth,
        truth_valid,
        {"lk": DeterministicForecast(forecast, forecast_valid)},
        lead_minutes=(10,),
        thresholds_mm_h=(1.0,),
        windows_pixels=(1,),
        pixel_spacing_km=1.0,
        validity_domain="common",
    )[0]
    fixed = score_deterministic_forecasts(
        truth,
        truth_valid,
        {"lk": DeterministicForecast(forecast, forecast_valid)},
        lead_minutes=(10,),
        thresholds_mm_h=(1.0,),
        windows_pixels=(),
        windows_km=(1.0,),
        pixel_spacing_km=1.0,
        validity_domain="truth",
    )[0]

    assert common["csi"] == 1.0
    assert fixed["csi"] == pytest.approx(0.5)
    assert fixed["misses"] == 1
    assert fixed["forecast_to_truth_coverage"] == pytest.approx(0.5)
    assert fixed["validity_domain"] == "truth"


def test_physical_fss_window_is_converted_to_an_odd_pixel_count() -> None:
    truth = np.ones((1, 5, 5), dtype="float32")
    valid = np.ones(truth.shape, dtype="uint8")
    rows = score_deterministic_forecasts(
        truth,
        valid,
        {"lk": DeterministicForecast(truth, valid)},
        lead_minutes=(10,),
        thresholds_mm_h=(0.5,),
        windows_pixels=(),
        windows_km=(10.0,),
        pixel_spacing_km=1.2,
    )

    assert rows[0]["window_pixels"] == 9
    assert rows[0]["window_target_km"] == 10.0
    assert rows[0]["window_km"] == pytest.approx(10.8)


def test_accumulation_scores_use_the_observed_lead_intervals() -> None:
    truth = np.full((6, 2, 2), 6.0, dtype="float32")
    forecast = np.full((6, 2, 2), 3.0, dtype="float32")
    valid = np.ones(truth.shape, dtype="uint8")

    rows = score_accumulation_forecasts(
        truth,
        valid,
        {"lk": DeterministicForecast(forecast, valid)},
        lead_minutes=(10, 20, 30, 40, 50, 60),
        accumulation_windows_minutes=(60,),
        thresholds_mm=(5.0,),
        windows_pixels=(1,),
        pixel_spacing_km=1.0,
    )

    assert len(rows) == 1
    assert rows[0]["accumulation_minutes"] == 60
    assert rows[0]["threshold_mm"] == 5.0
    assert rows[0]["mae_mm"] == pytest.approx(3.0)


def test_skill_summary_applies_three_of_four_wet_case_gate_and_block_bootstrap() -> None:
    rows: list[dict[str, object]] = []
    for case_index in range(4):
        for threshold in (1.0, 5.0, 10.0):
            for model, fss in (
                ("lk", 0.6 if case_index < 3 else 0.4),
                ("persistence", 0.5),
                ("translation", 0.55 if case_index < 3 else 0.45),
            ):
                rows.append(
                    {
                        "case_id": f"wet-{case_index}",
                        "case_category": "wet",
                        "issue_time_utc": f"2021-08-{case_index + 1:02d}T00:00:00Z",
                        "model": model,
                        "lead_minutes": 10,
                        "threshold_mm_h": threshold,
                        "window_pixels": 11,
                        "fss": fss,
                    }
                )

    summary = summarize_fss_skill(rows, bootstrap_samples=200)

    assert summary["status"] == "lk_supported"
    for comparison in summary["comparisons"]:
        assert comparison["positive_case_count"] == 3
        assert comparison["evaluable_case_count"] == 4
        assert comparison["passes_case_gate"] is True
        assert comparison["bootstrap_sample_count"] == 200
        assert len(comparison["mean_difference_95pct_interval"]) == 2

    one_case_summary = summarize_fss_skill(
        [row for row in rows if row["case_id"] == "wet-0"],
        bootstrap_samples=20,
    )
    assert one_case_summary["status"] == "insufficient_evidence"


def test_coverage_gate_blocks_an_otherwise_positive_skill_summary() -> None:
    rows: list[dict[str, object]] = []
    for case_index in range(4):
        for threshold in (1.0, 5.0, 10.0):
            for model, fss in (("lk", 0.7), ("persistence", 0.5), ("translation", 0.55)):
                rows.append(
                    {
                        "case_id": f"wet-{case_index}",
                        "case_category": "wet",
                        "issue_time_utc": f"2021-08-{case_index + 1:02d}T00:00:00Z",
                        "model": model,
                        "lead_minutes": 10,
                        "threshold_mm_h": threshold,
                        "window_pixels": 11,
                        "fss": fss,
                        "forecast_to_truth_coverage": 0.9 if model == "lk" else 1.0,
                    }
                )

    summary = summarize_fss_skill(
        rows,
        bootstrap_samples=20,
        minimum_forecast_to_truth_coverage=0.95,
    )
    coverage = summarize_coverage(rows, models=("lk",), minimum_ratio=0.95)

    assert summary["status"] == "skill_not_demonstrated"
    assert all(not item["coverage_gate_passes"] for item in summary["comparisons"])
    assert not coverage["all_models_pass"]
