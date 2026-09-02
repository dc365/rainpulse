from __future__ import annotations

import numpy as np
import pytest

from rainpulse_algo.radar.qc_acceptance import build_acceptance_report
from rainpulse_algo.radar.qc_metrics import (
    echo_classification_metrics,
    gauge_verification_metrics,
    polar_mask_area_km2,
    qpe_distribution_metrics,
    real_precipitation_retention_rate,
)


def test_echo_classification_metrics_keep_undefined_denominators_explicit() -> None:
    truth = np.array([[1, 0, 1, 0]], dtype="uint8")
    predicted = np.array([[0.9, 0.8, 0.2, 0.1]], dtype="float32")

    metrics = echo_classification_metrics(truth, predicted, threshold=0.8)

    assert metrics["true_positive"] == 1
    assert metrics["false_positive"] == 1
    assert metrics["false_negative"] == 1
    assert metrics["precision"] == pytest.approx(0.5)
    assert metrics["recall"] == pytest.approx(0.5)

    empty = echo_classification_metrics(
        np.zeros((1, 2), dtype="uint8"),
        np.zeros((1, 2), dtype="float32"),
        threshold=0.8,
    )
    assert empty["precision"] is None
    assert empty["recall"] is None

    missing_truth = echo_classification_metrics(
        np.array([[1.0, np.nan]], dtype="float32"),
        np.array([[0.9, 0.9]], dtype="float32"),
        threshold=0.8,
    )
    assert missing_truth["evaluated_gate_count"] == 1


def test_real_precipitation_retention_rate_uses_labelled_meteorological_gates() -> None:
    truth_meteo = np.array([[1, 1, 0, 1]], dtype="uint8")
    retained = np.array([[1, 0, 1, 1]], dtype="uint8")

    assert real_precipitation_retention_rate(truth_meteo, retained) == pytest.approx(
        2 / 3
    )


def test_polar_mask_area_uses_gate_wedge_geometry() -> None:
    mask = np.ones((4, 2), dtype=bool)
    ranges = np.array([500.0, 1_500.0], dtype="float32")
    azimuth = np.array([0.0, 90.0, 180.0, 270.0], dtype="float32")

    assert polar_mask_area_km2(mask, ranges, azimuth) == pytest.approx(
        np.pi * 2_000.0**2 / 1_000_000.0,
        rel=1e-5,
    )


def test_qpe_and_gauge_metrics_report_tail_bias_and_correlation() -> None:
    qpe = np.array([0.0, 1.0, 2.0, 10.0], dtype="float32")
    gauges = np.array([0.0, 1.0, 2.0, 8.0], dtype="float32")

    distribution = qpe_distribution_metrics(qpe)
    verification = gauge_verification_metrics(qpe, gauges)

    assert distribution["p95_mm_h"] == pytest.approx(np.quantile(qpe, 0.95))
    assert distribution["maximum_mm_h"] == 10.0
    assert verification["bias_ratio"] == pytest.approx(13 / 11)
    assert verification["correlation"] is not None
    assert verification["mae_mm"] == pytest.approx(0.5)

    masked = qpe_distribution_metrics(
        qpe,
        valid_mask=np.array([1.0, 1.0, np.nan, 0.0], dtype="float32"),
    )
    assert masked["sample_count"] == 2
    assert masked["maximum_mm_h"] == 1.0


def test_acceptance_report_computes_available_metrics_and_skips_absent_truth() -> None:
    report = build_acceptance_report(
        {
            "predicted_anomaly_probability": np.array(
                [[0.9, 0.1], [0.8, 0.2]], dtype="float32"
            ),
            "ranges_m": np.array([500.0, 1_500.0], dtype="float32"),
            "azimuth_deg": np.array([0.0, 180.0], dtype="float32"),
            "qpe_rate_mm_h": np.array([[1.0, 2.0], [3.0, 4.0]], dtype="float32"),
        },
        anomaly_threshold=0.8,
    )

    assert report["pollution_area"]["status"] == "computed"
    assert report["qpe_distribution"]["p95_mm_h"] is not None
    assert report["echo_classification"] == {
        "status": "skipped",
        "reason": "labelled_anomaly_truth_unavailable",
    }
    assert report["gauge_verification"]["status"] == "skipped"


def test_acceptance_report_masks_pollution_area_and_marks_computed_truth() -> None:
    report = build_acceptance_report(
        {
            "predicted_anomaly_probability": np.array(
                [[0.9, 0.9], [0.9, 0.9]], dtype="float32"
            ),
            "truth_anomaly": np.array([[1, 1], [1, 1]], dtype="uint8"),
            "valid_mask": np.array([[1, 0], [0, 0]], dtype="uint8"),
            "ranges_m": np.array([500.0, 1_500.0], dtype="float32"),
            "azimuth_deg": np.array([0.0, 180.0], dtype="float32"),
        },
        anomaly_threshold=0.8,
    )

    assert report["echo_classification"]["status"] == "computed"
    assert report["echo_classification"]["evaluated_gate_count"] == 1
    assert report["pollution_area"]["area_km2"] == pytest.approx(
        0.5 * np.pi * 1_000.0**2 / 1_000_000.0
    )
