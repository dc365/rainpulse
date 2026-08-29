from __future__ import annotations

import numpy as np
import pytest

from rainpulse_algo.verification.deterministic import VerificationInputError
from rainpulse_algo.verification.probabilistic import (
    score_deterministic_probability_baseline,
    score_probabilistic_forecast,
)


def test_scores_brier_crps_reliability_and_spread_with_worked_values() -> None:
    truth = np.asarray([[[0.0, 2.0]]], dtype="float32")
    members = np.asarray(
        [
            [[[0.0, 1.0]]],
            [[[0.0, 3.0]]],
        ],
        dtype="float32",
    )
    valid = np.ones(truth.shape, dtype="uint8")

    rows = score_probabilistic_forecast(
        truth,
        valid,
        members,
        valid,
        lead_minutes=(5,),
        thresholds_mm_h=(1.0,),
        reliability_bin_edges=(0.0, 0.5, 1.0),
    )

    assert len(rows) == 1
    row = rows[0]
    assert row["member_count"] == 2
    assert row["evaluation_cell_count"] == 2
    assert row["brier_score"] == pytest.approx(0.125)
    assert row["crps_mm_h"] == pytest.approx(0.25)
    assert row["ensemble_mean_rmse_mm_h"] == pytest.approx(0.0)
    assert row["mean_ensemble_spread_mm_h"] == pytest.approx(0.5)
    assert row["reliability"][0]["forecast_count"] == 1
    assert row["reliability"][0]["observed_frequency"] == 0.0
    assert row["reliability"][1]["forecast_count"] == 1
    assert row["reliability"][1]["mean_forecast_probability"] == 0.5
    assert row["reliability"][1]["observed_frequency"] == 1.0


def test_probabilistic_scoring_uses_only_common_valid_support() -> None:
    truth = np.asarray([[[0.0, 2.0]]], dtype="float32")
    members = np.asarray(
        [
            [[[0.0, np.nan]]],
            [[[0.0, np.nan]]],
        ],
        dtype="float32",
    )
    truth_valid = np.ones(truth.shape, dtype="uint8")
    forecast_valid = np.asarray([[[1, 0]]], dtype="uint8")

    row = score_probabilistic_forecast(
        truth,
        truth_valid,
        members,
        forecast_valid,
        lead_minutes=(5,),
        thresholds_mm_h=(1.0,),
    )[0]

    assert row["evaluation_cell_count"] == 1
    assert row["evaluation_coverage"] == 0.5
    assert row["brier_score"] == 0.0


def test_probabilistic_scoring_rejects_single_member_input() -> None:
    truth = np.zeros((1, 1, 1), dtype="float32")
    valid = np.ones_like(truth, dtype="uint8")

    with pytest.raises(VerificationInputError, match="ensemble must be"):
        score_probabilistic_forecast(
            truth,
            valid,
            truth[np.newaxis, ...],
            valid,
            lead_minutes=(5,),
            thresholds_mm_h=(1.0,),
        )


def test_deterministic_probability_baseline_uses_zero_one_events_and_mae_crps() -> None:
    truth = np.asarray([[[0.0, 1.0]]], dtype="float32")
    forecast = np.asarray([[[0.0, 2.0]]], dtype="float32")
    valid = np.ones_like(truth, dtype="uint8")

    row = score_deterministic_probability_baseline(
        truth,
        valid,
        forecast,
        valid,
        lead_minutes=(10,),
        thresholds_mm_h=(1.0,),
        reliability_bin_edges=(0.0, 0.5, 1.0),
    )[0]

    assert row["member_count"] == 1
    assert row["forecast_kind"] == "deterministic_degenerate_probability"
    assert row["brier_score"] == pytest.approx(0.5)
    assert row["crps_mm_h"] == pytest.approx(0.5)
    assert row["mean_ensemble_spread_mm_h"] == pytest.approx(0.0)
