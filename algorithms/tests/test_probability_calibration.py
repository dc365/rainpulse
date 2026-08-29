from __future__ import annotations

import json
from dataclasses import replace
from pathlib import Path

import numpy as np
import pytest
import yaml
from jsonschema import Draft202012Validator, FormatChecker

from rainpulse_algo.verification.probability_calibration import (
    CalibrationInputError,
    IsotonicCalibrationCurve,
    apply_probability_calibration,
    apply_probability_calibration_suite,
    build_probability_calibration_artifact,
    evaluate_probability_calibration_shadow,
    fit_isotonic_calibration,
    load_probability_calibration_profile,
)

REPOSITORY_ROOT = Path(__file__).resolve().parents[2]
PROFILE_PATH = (
    REPOSITORY_ROOT
    / "configs"
    / "calibration"
    / "rp025-probability-calibration-v1.yaml"
)
PROFILE_SCHEMA_PATH = (
    REPOSITORY_ROOT
    / "configs"
    / "schemas"
    / "probability-calibration-profile.schema.json"
)
ARTIFACT_SCHEMA_PATH = (
    REPOSITORY_ROOT
    / "configs"
    / "schemas"
    / "probability-calibration-artifact.schema.json"
)


def _training_arrays(repeat_count: int = 200) -> tuple[np.ndarray, np.ndarray]:
    raw = np.repeat(np.asarray([0.0, 0.25, 0.5, 0.75, 1.0]), repeat_count)
    event_counts = tuple(
        round(repeat_count * frequency) for frequency in (0.05, 0.15, 0.4, 0.65, 0.95)
    )
    observed = np.concatenate(
        tuple(
            np.concatenate((np.ones(count), np.zeros(repeat_count - count)))
            for count in event_counts
        )
    )
    return raw.astype("float32"), observed.astype("uint8")


def _curve(
    *,
    lead_band: str = "near",
    minimum_lead_minutes: int = 5,
    maximum_lead_minutes: int = 60,
    threshold_mm_h: float = 1.0,
) -> IsotonicCalibrationCurve:
    raw, observed = _training_arrays()
    return fit_isotonic_calibration(
        raw,
        observed,
        lead_band=lead_band,
        minimum_lead_minutes=minimum_lead_minutes,
        maximum_lead_minutes=maximum_lead_minutes,
        threshold_mm_h=threshold_mm_h,
        minimum_sample_count=1000,
        minimum_event_count=50,
        minimum_non_event_count=50,
        minimum_unique_probability_count=5,
    )


def _complete_curves() -> tuple[IsotonicCalibrationCurve, ...]:
    values = []
    for lead_band, minimum, maximum in (("near", 5, 60), ("far", 65, 120)):
        for threshold in (1.0, 5.0, 10.0, 20.0, 50.0):
            values.append(
                _curve(
                    lead_band=lead_band,
                    minimum_lead_minutes=minimum,
                    maximum_lead_minutes=maximum,
                    threshold_mm_h=threshold,
                )
            )
    return tuple(values)


def _artifact() -> dict[str, object]:
    return build_probability_calibration_artifact(
        load_probability_calibration_profile(PROFILE_PATH),
        _complete_curves(),
        artifact_id="rp025-synthetic-calibration-v1",
        created_at_utc="2026-08-29T16:00:00Z",
        source_model_profile_version="synthetic-steps-v1",
        grid_id="synthetic-grid-v1",
        training_namespace="synthetic-training-v1",
        training_source_manifest_sha256="a" * 64,
        training_case_ids=tuple(f"train-{index}" for index in range(6)),
        training_issue_count=50,
        validation_namespace="synthetic-validation-v1",
        validation_case_ids=("validation-1", "validation-2"),
    )


def test_profile_and_artifact_schemas_freeze_shadow_only_boundary() -> None:
    profile_schema = json.loads(PROFILE_SCHEMA_PATH.read_text())
    artifact_schema = json.loads(ARTIFACT_SCHEMA_PATH.read_text())
    profile_raw = yaml.safe_load(PROFILE_PATH.read_text())

    Draft202012Validator.check_schema(profile_schema)
    Draft202012Validator.check_schema(artifact_schema)
    Draft202012Validator(profile_schema).validate(profile_raw)
    profile = load_probability_calibration_profile(PROFILE_PATH)
    artifact = _artifact()
    Draft202012Validator(
        artifact_schema,
        format_checker=FormatChecker(),
    ).validate(artifact)

    assert profile.artifact_fitting_enabled is False
    assert profile.shadow_application_enabled is False
    assert profile.calibrated_product_publication_enabled is False
    assert artifact["operational"] == {
        "shadow_only": True,
        "operational_eligible": False,
        "publication_enabled": False,
        "blockers": [
            "representative_fujian_qc_qpe_calibration_data_required",
            "independent_fujian_calibration_acceptance_required",
        ],
    }


def test_isotonic_fit_is_monotone_and_improves_synthetic_training_brier() -> None:
    curve = _curve()

    assert curve.input_probabilities == (0.0, 0.25, 0.5, 0.75, 1.0)
    assert curve.calibrated_probabilities == pytest.approx((0.05, 0.15, 0.4, 0.65, 0.95))
    assert np.all(np.diff(curve.calibrated_probabilities) >= 0.0)
    assert curve.training_brier_calibrated < curve.training_brier_raw


def test_fit_rejects_insufficient_or_invalid_samples() -> None:
    raw, observed = _training_arrays(repeat_count=2)
    with pytest.raises(CalibrationInputError, match="sample count"):
        fit_isotonic_calibration(
            raw,
            observed,
            lead_band="near",
            minimum_lead_minutes=5,
            maximum_lead_minutes=60,
            threshold_mm_h=1.0,
            minimum_sample_count=100,
            minimum_event_count=1,
            minimum_non_event_count=1,
            minimum_unique_probability_count=5,
        )

    invalid = raw.copy()
    invalid[0] = 1.1
    with pytest.raises(CalibrationInputError, match="between zero and one"):
        fit_isotonic_calibration(
            invalid,
            observed,
            lead_band="near",
            minimum_lead_minutes=5,
            maximum_lead_minutes=60,
            threshold_mm_h=1.0,
            minimum_sample_count=1,
            minimum_event_count=1,
            minimum_non_event_count=1,
            minimum_unique_probability_count=2,
        )


def test_application_preserves_missing_and_probability_bounds() -> None:
    curve = _curve()
    raw = np.asarray([[0.0, 0.5, np.nan], [1.0, 0.25, np.nan]], dtype="float32")
    valid = np.asarray([[1, 1, 0], [1, 1, 0]], dtype="uint8")

    calibrated = apply_probability_calibration(raw, curve, valid_mask=valid)

    assert calibrated[0, 0] == pytest.approx(0.05)
    assert calibrated[0, 1] == pytest.approx(0.4)
    assert calibrated[1, 0] == pytest.approx(0.95)
    assert np.all((calibrated[valid == 1] >= 0.0) & (calibrated[valid == 1] <= 1.0))
    assert np.all(np.isnan(calibrated[valid == 0]))


def test_suite_enforces_exceedance_threshold_coherence() -> None:
    low_curve = replace(
        _curve(threshold_mm_h=1.0),
        input_probabilities=(0.0, 1.0),
        calibrated_probabilities=(0.2, 0.3),
        unique_probability_count=2,
    )
    high_curve = replace(
        _curve(threshold_mm_h=5.0),
        input_probabilities=(0.0, 1.0),
        calibrated_probabilities=(0.6, 0.8),
        unique_probability_count=2,
    )
    calibrated = apply_probability_calibration_suite(
        {
            1.0: np.asarray([[0.5]], dtype="float32"),
            5.0: np.asarray([[0.25]], dtype="float32"),
        },
        {1.0: low_curve, 5.0: high_curve},
        thresholds_mm_h=(1.0, 5.0),
        valid_mask=np.ones((1, 1), dtype="uint8"),
    )

    assert calibrated[1.0][0, 0] == pytest.approx(0.25)
    assert calibrated[5.0][0, 0] == pytest.approx(0.25)


def test_application_rejects_invalid_curve_and_incoherent_raw_suite() -> None:
    invalid_curve = replace(
        _curve(),
        calibrated_probabilities=(0.1, 0.4, 0.3, 0.8, 0.9),
    )
    with pytest.raises(CalibrationInputError, match="curve is invalid"):
        apply_probability_calibration(np.asarray([0.5]), invalid_curve)

    with pytest.raises(CalibrationInputError, match="threshold-incoherent"):
        apply_probability_calibration_suite(
            {
                1.0: np.asarray([0.2], dtype="float32"),
                5.0: np.asarray([0.4], dtype="float32"),
            },
            {1.0: _curve(threshold_mm_h=1.0), 5.0: _curve(threshold_mm_h=5.0)},
            thresholds_mm_h=(1.0, 5.0),
        )


def test_artifact_and_shadow_evaluation_reject_split_leakage() -> None:
    profile = load_probability_calibration_profile(PROFILE_PATH)
    with pytest.raises(CalibrationInputError, match="overlap"):
        build_probability_calibration_artifact(
            profile,
            _complete_curves(),
            artifact_id="leaking-artifact",
            created_at_utc="2026-08-29T16:00:00Z",
            source_model_profile_version="synthetic-steps-v1",
            grid_id="synthetic-grid-v1",
            training_namespace="same",
            training_source_manifest_sha256="a" * 64,
            training_case_ids=tuple(f"case-{index}" for index in range(6)),
            training_issue_count=50,
            validation_namespace="same",
            validation_case_ids=("case-0",),
        )

    artifact = _artifact()
    with pytest.raises(CalibrationInputError, match="overlap"):
        evaluate_probability_calibration_shadow(
            np.asarray([0.2, 0.8]),
            np.asarray([0.1, 0.9]),
            np.asarray([0, 1]),
            artifact=artifact,
            evaluation_namespace="new-validation",
            evaluation_case_ids=("train-0",),
        )


def test_shadow_evaluation_reports_brier_without_promotion() -> None:
    report = evaluate_probability_calibration_shadow(
        np.asarray([0.0, 0.3, 0.7, 1.0]),
        np.asarray([0.0, 0.1, 0.9, 1.0]),
        np.asarray([0, 0, 1, 1]),
        artifact=_artifact(),
        evaluation_namespace="synthetic-shadow-v1",
        evaluation_case_ids=("shadow-1",),
    )

    assert report["sample_count"] == 4
    assert report["calibrated_brier_score"] < report["raw_brier_score"]
    assert report["operational_eligible"] is False
    assert report["promotion_decision"] == "blocked_pending_fujian_acceptance"
