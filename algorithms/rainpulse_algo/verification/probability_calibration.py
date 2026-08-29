from __future__ import annotations

from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any

import numpy as np
import yaml

RAW_PROBABILITY_STATUS = "raw_ensemble_relative_frequency_uncalibrated"
CALIBRATION_PROFILE_VERSION = "rp025-probability-calibration-v1"
CALIBRATION_METHOD = "isotonic_pav"


class CalibrationInputError(ValueError):
    """Raised when calibration inputs or provenance violate the RP-025 boundary."""


@dataclass(frozen=True)
class CalibrationLeadBand:
    band_id: str
    minimum_lead_minutes: int
    maximum_lead_minutes: int


@dataclass(frozen=True)
class CalibrationFitPolicy:
    minimum_sample_count: int
    minimum_event_count: int
    minimum_non_event_count: int
    minimum_unique_probability_count: int
    minimum_case_count: int
    minimum_issue_count: int
    sample_weighting: str


@dataclass(frozen=True)
class ProbabilityCalibrationProfile:
    profile_version: str
    artifact_contract_version: str
    source_forecast_output_contract_version: str
    source_probability_status: str
    event_operator: str
    thresholds_mm_h: tuple[float, ...]
    lead_bands: tuple[CalibrationLeadBand, ...]
    method_name: str
    interpolation: str
    out_of_bounds: str
    threshold_coherence: str
    fit_policy: CalibrationFitPolicy
    training_role: str
    validation_role: str
    require_disjoint_namespaces: bool
    require_disjoint_case_ids: bool
    validation_untouched_at_fit: bool
    artifact_fitting_enabled: bool
    shadow_application_enabled: bool
    calibrated_product_publication_enabled: bool
    required_gate: str


@dataclass(frozen=True)
class IsotonicCalibrationCurve:
    lead_band: str
    minimum_lead_minutes: int
    maximum_lead_minutes: int
    threshold_mm_h: float
    input_probabilities: tuple[float, ...]
    calibrated_probabilities: tuple[float, ...]
    sample_count: int
    event_count: int
    non_event_count: int
    unique_probability_count: int
    training_brier_raw: float
    training_brier_calibrated: float

    def as_dict(self) -> dict[str, Any]:
        return {
            "lead_band": self.lead_band,
            "minimum_lead_minutes": self.minimum_lead_minutes,
            "maximum_lead_minutes": self.maximum_lead_minutes,
            "threshold_mm_h": self.threshold_mm_h,
            "sample_count": self.sample_count,
            "event_count": self.event_count,
            "non_event_count": self.non_event_count,
            "unique_probability_count": self.unique_probability_count,
            "training_brier_raw": self.training_brier_raw,
            "training_brier_calibrated": self.training_brier_calibrated,
            "points": [
                {
                    "input_probability": input_probability,
                    "calibrated_probability": calibrated_probability,
                }
                for input_probability, calibrated_probability in zip(
                    self.input_probabilities,
                    self.calibrated_probabilities,
                    strict=True,
                )
            ],
        }


def load_probability_calibration_profile(
    path: str | Path,
) -> ProbabilityCalibrationProfile:
    profile_path = Path(path)
    try:
        raw = yaml.safe_load(profile_path.read_text(encoding="utf-8"))
        method = raw["method"]
        fit = raw["fit_policy"]
        split = raw["split_policy"]
        activation = raw["activation"]
        bands = tuple(
            CalibrationLeadBand(
                band_id=str(item["band_id"]),
                minimum_lead_minutes=int(item["minimum_lead_minutes"]),
                maximum_lead_minutes=int(item["maximum_lead_minutes"]),
            )
            for item in raw["lead_bands"]
        )
        profile = ProbabilityCalibrationProfile(
            profile_version=str(raw["profile_version"]),
            artifact_contract_version=str(raw["artifact_contract_version"]),
            source_forecast_output_contract_version=str(
                raw["source_forecast_output_contract_version"]
            ),
            source_probability_status=str(raw["source_probability_status"]),
            event_operator=str(raw["event_operator"]),
            thresholds_mm_h=tuple(float(value) for value in raw["thresholds_mm_h"]),
            lead_bands=bands,
            method_name=str(method["name"]),
            interpolation=str(method["interpolation"]),
            out_of_bounds=str(method["out_of_bounds"]),
            threshold_coherence=str(method["threshold_coherence"]),
            fit_policy=CalibrationFitPolicy(
                minimum_sample_count=int(fit["minimum_sample_count"]),
                minimum_event_count=int(fit["minimum_event_count"]),
                minimum_non_event_count=int(fit["minimum_non_event_count"]),
                minimum_unique_probability_count=int(
                    fit["minimum_unique_probability_count"]
                ),
                minimum_case_count=int(fit["minimum_case_count"]),
                minimum_issue_count=int(fit["minimum_issue_count"]),
                sample_weighting=str(fit["sample_weighting"]),
            ),
            training_role=str(split["training_role"]),
            validation_role=str(split["validation_role"]),
            require_disjoint_namespaces=bool(split["require_disjoint_namespaces"]),
            require_disjoint_case_ids=bool(split["require_disjoint_case_ids"]),
            validation_untouched_at_fit=bool(split["validation_untouched_at_fit"]),
            artifact_fitting_enabled=bool(activation["artifact_fitting_enabled"]),
            shadow_application_enabled=bool(activation["shadow_application_enabled"]),
            calibrated_product_publication_enabled=bool(
                activation["calibrated_product_publication_enabled"]
            ),
            required_gate=str(activation["required_gate"]),
        )
    except (KeyError, TypeError, ValueError) as exc:
        raise CalibrationInputError(
            f"invalid probability calibration profile {profile_path}: {exc}"
        ) from exc
    _validate_profile(profile, raw)
    return profile


def fit_isotonic_calibration(
    raw_probability: np.ndarray,
    observed_event: np.ndarray,
    *,
    lead_band: str,
    minimum_lead_minutes: int,
    maximum_lead_minutes: int,
    threshold_mm_h: float,
    minimum_sample_count: int,
    minimum_event_count: int,
    minimum_non_event_count: int,
    minimum_unique_probability_count: int,
) -> IsotonicCalibrationCurve:
    raw, observed = _calibration_vectors(raw_probability, observed_event)
    sample_count = int(raw.size)
    event_count = int(np.sum(observed))
    non_event_count = sample_count - event_count
    unique, inverse, counts = np.unique(raw, return_inverse=True, return_counts=True)
    if sample_count < minimum_sample_count:
        raise CalibrationInputError("calibration sample count is below the fit-readiness floor")
    if event_count < minimum_event_count:
        raise CalibrationInputError("calibration event count is below the fit-readiness floor")
    if non_event_count < minimum_non_event_count:
        raise CalibrationInputError(
            "calibration non-event count is below the fit-readiness floor"
        )
    if unique.size < minimum_unique_probability_count:
        raise CalibrationInputError(
            "calibration unique probability count is below the fit-readiness floor"
        )
    if minimum_lead_minutes <= 0 or maximum_lead_minutes < minimum_lead_minutes:
        raise CalibrationInputError("calibration lead band is invalid")
    if not lead_band or threshold_mm_h < 0.0:
        raise CalibrationInputError("calibration curve identity is invalid")

    event_sums = np.bincount(inverse, weights=observed, minlength=unique.size)
    fitted = _pool_adjacent_violators(event_sums, counts.astype("float64"))
    calibrated = np.interp(raw, unique, fitted)
    raw_brier = float(np.mean((raw - observed) ** 2))
    calibrated_brier = float(np.mean((calibrated - observed) ** 2))
    return IsotonicCalibrationCurve(
        lead_band=lead_band,
        minimum_lead_minutes=minimum_lead_minutes,
        maximum_lead_minutes=maximum_lead_minutes,
        threshold_mm_h=float(threshold_mm_h),
        input_probabilities=tuple(float(value) for value in unique),
        calibrated_probabilities=tuple(float(value) for value in fitted),
        sample_count=sample_count,
        event_count=event_count,
        non_event_count=non_event_count,
        unique_probability_count=int(unique.size),
        training_brier_raw=raw_brier,
        training_brier_calibrated=calibrated_brier,
    )


def apply_probability_calibration(
    probability: np.ndarray,
    curve: IsotonicCalibrationCurve,
    *,
    valid_mask: np.ndarray | None = None,
) -> np.ndarray:
    values = np.asarray(probability, dtype="float64")
    valid = _valid_mask(values, valid_mask)
    _validate_curve(curve)
    if np.any(~np.isfinite(values[valid])):
        raise CalibrationInputError("valid raw probabilities must be finite")
    if np.any((values[valid] < 0.0) | (values[valid] > 1.0)):
        raise CalibrationInputError("raw probabilities must be between zero and one")
    output = np.full(values.shape, np.nan, dtype="float32")
    output[valid] = np.interp(
        values[valid],
        np.asarray(curve.input_probabilities),
        np.asarray(curve.calibrated_probabilities),
    ).astype("float32")
    return output


def apply_probability_calibration_suite(
    raw_probability_by_threshold: Mapping[float, np.ndarray],
    curves_by_threshold: Mapping[float, IsotonicCalibrationCurve],
    *,
    thresholds_mm_h: Sequence[float],
    valid_mask: np.ndarray | None = None,
) -> dict[float, np.ndarray]:
    thresholds = tuple(float(value) for value in thresholds_mm_h)
    if not thresholds or tuple(sorted(thresholds)) != thresholds or len(set(thresholds)) != len(
        thresholds
    ):
        raise CalibrationInputError("calibration thresholds must be unique and increasing")
    if set(raw_probability_by_threshold) != set(thresholds) or set(curves_by_threshold) != set(
        thresholds
    ):
        raise CalibrationInputError("calibration probability suite is incomplete")
    shapes = {np.asarray(raw_probability_by_threshold[value]).shape for value in thresholds}
    if len(shapes) != 1:
        raise CalibrationInputError("calibration probability suite shapes differ")
    reference = np.asarray(raw_probability_by_threshold[thresholds[0]], dtype="float64")
    valid = _valid_mask(reference, valid_mask)
    lead_identities = {
        (
            curves_by_threshold[value].lead_band,
            curves_by_threshold[value].minimum_lead_minutes,
            curves_by_threshold[value].maximum_lead_minutes,
        )
        for value in thresholds
    }
    if len(lead_identities) != 1 or any(
        curves_by_threshold[value].threshold_mm_h != value for value in thresholds
    ):
        raise CalibrationInputError("calibration curve suite identity differs")

    raw_values = {
        threshold: np.asarray(raw_probability_by_threshold[threshold], dtype="float64")
        for threshold in thresholds
    }
    for lower, higher in zip(thresholds, thresholds[1:], strict=False):
        if np.any(raw_values[higher][valid] > raw_values[lower][valid] + 1e-7):
            raise CalibrationInputError("raw exceedance probabilities are threshold-incoherent")

    calibrated = {
        threshold: apply_probability_calibration(
            raw_values[threshold],
            curves_by_threshold[threshold],
            valid_mask=valid,
        )
        for threshold in thresholds
    }
    for lower, higher in zip(thresholds, thresholds[1:], strict=False):
        calibrated[higher][valid] = np.minimum(
            calibrated[higher][valid],
            calibrated[lower][valid],
        )
    return calibrated


def build_probability_calibration_artifact(
    profile: ProbabilityCalibrationProfile,
    curves: Sequence[IsotonicCalibrationCurve],
    *,
    artifact_id: str,
    created_at_utc: str,
    source_model_profile_version: str,
    grid_id: str,
    training_namespace: str,
    training_source_manifest_sha256: str,
    training_case_ids: Sequence[str],
    training_issue_count: int,
    validation_namespace: str,
    validation_case_ids: Sequence[str],
) -> dict[str, Any]:
    training_ids = _unique_ids(training_case_ids, "training")
    validation_ids = _unique_ids(validation_case_ids, "validation")
    if (
        training_namespace == validation_namespace
        or set(training_ids).intersection(validation_ids)
    ):
        raise CalibrationInputError("calibration training and validation splits overlap")
    if len(training_ids) < profile.fit_policy.minimum_case_count:
        raise CalibrationInputError("calibration training case count is below the floor")
    if training_issue_count < profile.fit_policy.minimum_issue_count:
        raise CalibrationInputError("calibration training issue count is below the floor")
    if not _is_sha256(training_source_manifest_sha256):
        raise CalibrationInputError("calibration source manifest SHA-256 is invalid")
    _parse_utc(created_at_utc)
    if not all(
        (
            artifact_id,
            source_model_profile_version,
            grid_id,
            training_namespace,
            validation_namespace,
        )
    ):
        raise CalibrationInputError("calibration artifact identity is incomplete")

    band_by_id = {band.band_id: band for band in profile.lead_bands}
    expected_keys = {
        (band.band_id, threshold)
        for band in profile.lead_bands
        for threshold in profile.thresholds_mm_h
    }
    curve_by_key: dict[tuple[str, float], IsotonicCalibrationCurve] = {}
    for curve in curves:
        _validate_curve(curve)
        key = (curve.lead_band, curve.threshold_mm_h)
        if key in curve_by_key:
            raise CalibrationInputError("calibration curve suite contains duplicates")
        band = band_by_id.get(curve.lead_band)
        if band is None or (
            curve.minimum_lead_minutes != band.minimum_lead_minutes
            or curve.maximum_lead_minutes != band.maximum_lead_minutes
        ):
            raise CalibrationInputError("calibration curve lead band differs from profile")
        _validate_curve_readiness(curve, profile.fit_policy)
        curve_by_key[key] = curve
    if set(curve_by_key) != expected_keys:
        raise CalibrationInputError("calibration curve suite is incomplete")

    ordered_curves = [
        curve_by_key[(band.band_id, threshold)].as_dict()
        for band in profile.lead_bands
        for threshold in profile.thresholds_mm_h
    ]
    return {
        "schema_version": "1.0",
        "artifact_id": artifact_id,
        "artifact_contract_version": profile.artifact_contract_version,
        "profile_version": profile.profile_version,
        "created_at_utc": created_at_utc,
        "source": {
            "model_id": "pysteps-steps",
            "model_profile_version": source_model_profile_version,
            "forecast_output_contract_version": (
                profile.source_forecast_output_contract_version
            ),
            "probability_status": profile.source_probability_status,
            "event_operator": profile.event_operator,
            "grid_id": grid_id,
        },
        "method": {
            "name": profile.method_name,
            "interpolation": profile.interpolation,
            "out_of_bounds": profile.out_of_bounds,
            "threshold_coherence": profile.threshold_coherence,
        },
        "training_evidence": {
            "namespace": training_namespace,
            "role": profile.training_role,
            "source_manifest_sha256": training_source_manifest_sha256,
            "case_ids": list(training_ids),
            "case_count": len(training_ids),
            "issue_count": training_issue_count,
        },
        "reserved_validation": {
            "namespace": validation_namespace,
            "role": profile.validation_role,
            "case_ids": list(validation_ids),
            "untouched_at_fit": profile.validation_untouched_at_fit,
        },
        "curves": ordered_curves,
        "operational": {
            "shadow_only": True,
            "operational_eligible": False,
            "publication_enabled": False,
            "blockers": [
                "representative_fujian_qc_qpe_calibration_data_required",
                "independent_fujian_calibration_acceptance_required",
            ],
        },
    }


def evaluate_probability_calibration_shadow(
    raw_probability: np.ndarray,
    calibrated_probability: np.ndarray,
    observed_event: np.ndarray,
    *,
    artifact: Mapping[str, Any],
    evaluation_namespace: str,
    evaluation_case_ids: Sequence[str],
) -> dict[str, Any]:
    training = artifact.get("training_evidence")
    reserved = artifact.get("reserved_validation")
    operational = artifact.get("operational")
    if not isinstance(training, Mapping) or not isinstance(reserved, Mapping):
        raise CalibrationInputError("calibration artifact provenance is incomplete")
    if not isinstance(operational, Mapping) or (
        operational.get("operational_eligible") is not False
        or operational.get("publication_enabled") is not False
    ):
        raise CalibrationInputError("calibration artifact is not shadow-only")
    evaluation_ids = _unique_ids(evaluation_case_ids, "evaluation")
    training_ids = {str(value) for value in training.get("case_ids", ())}
    if (
        not evaluation_namespace
        or evaluation_namespace == training.get("namespace")
        or training_ids.intersection(evaluation_ids)
    ):
        raise CalibrationInputError("calibration evaluation and training splits overlap")
    if evaluation_namespace == reserved.get("namespace") and not set(evaluation_ids).issubset(
        {str(value) for value in reserved.get("case_ids", ())}
    ):
        raise CalibrationInputError("calibration reserved validation case identity differs")

    raw, observed = _calibration_vectors(raw_probability, observed_event)
    calibrated = np.asarray(calibrated_probability, dtype="float64")
    if calibrated.shape != raw.shape or np.any(~np.isfinite(calibrated)):
        raise CalibrationInputError("calibrated evaluation probabilities are invalid")
    if np.any((calibrated < 0.0) | (calibrated > 1.0)):
        raise CalibrationInputError("calibrated probabilities must be between zero and one")
    raw_brier = float(np.mean((raw - observed) ** 2))
    calibrated_brier = float(np.mean((calibrated - observed) ** 2))
    return {
        "schema_version": "1.0",
        "artifact_id": artifact.get("artifact_id"),
        "evaluation_namespace": evaluation_namespace,
        "evaluation_case_ids": list(evaluation_ids),
        "sample_count": int(raw.size),
        "event_count": int(np.sum(observed)),
        "raw_brier_score": raw_brier,
        "calibrated_brier_score": calibrated_brier,
        "brier_skill_against_raw": (
            1.0 - calibrated_brier / raw_brier if raw_brier > 0.0 else None
        ),
        "operational_eligible": False,
        "promotion_decision": "blocked_pending_fujian_acceptance",
    }


def _validate_profile(profile: ProbabilityCalibrationProfile, raw: object) -> None:
    fit = profile.fit_policy
    expected_bands = (
        CalibrationLeadBand("near", 5, 60),
        CalibrationLeadBand("far", 65, 120),
    )
    if not isinstance(raw, dict) or raw.get("schema_version") != "1.0":
        raise CalibrationInputError("unsupported probability calibration schema")
    if (
        profile.profile_version != CALIBRATION_PROFILE_VERSION
        or profile.artifact_contract_version != "1.0"
        or profile.source_forecast_output_contract_version != "1.2"
        or profile.source_probability_status != RAW_PROBABILITY_STATUS
        or profile.event_operator != "greater_than"
        or profile.thresholds_mm_h != (1.0, 5.0, 10.0, 20.0, 50.0)
        or profile.lead_bands != expected_bands
    ):
        raise CalibrationInputError("RP-025 calibration identity or probability semantics differ")
    if (
        profile.method_name != CALIBRATION_METHOD
        or profile.interpolation != "linear"
        or profile.out_of_bounds != "clip"
        or profile.threshold_coherence != "cumulative_minimum"
    ):
        raise CalibrationInputError("RP-025 calibration method differs")
    if (
        fit.minimum_sample_count < 1000
        or fit.minimum_event_count < 50
        or fit.minimum_non_event_count < 50
        or fit.minimum_unique_probability_count < 5
        or fit.minimum_case_count < 6
        or fit.minimum_issue_count < 50
        or fit.sample_weighting != "equal_valid_grid_cell"
    ):
        raise CalibrationInputError("RP-025 fit-readiness policy is too weak")
    if (
        profile.training_role != "calibration_training"
        or profile.validation_role != "calibration_validation"
        or not profile.require_disjoint_namespaces
        or not profile.require_disjoint_case_ids
        or not profile.validation_untouched_at_fit
    ):
        raise CalibrationInputError("RP-025 split leakage policy differs")
    if (
        profile.artifact_fitting_enabled
        or profile.shadow_application_enabled
        or profile.calibrated_product_publication_enabled
        or profile.required_gate != "independent_fujian_calibration_acceptance_required"
    ):
        raise CalibrationInputError("RP-025 activation must remain disabled")


def _calibration_vectors(
    raw_probability: np.ndarray,
    observed_event: np.ndarray,
) -> tuple[np.ndarray, np.ndarray]:
    raw = np.asarray(raw_probability, dtype="float64")
    observed = np.asarray(observed_event, dtype="float64")
    if raw.ndim != 1 or observed.shape != raw.shape or raw.size == 0:
        raise CalibrationInputError("calibration samples must be aligned non-empty vectors")
    if np.any(~np.isfinite(raw)) or np.any(~np.isfinite(observed)):
        raise CalibrationInputError("calibration samples must be finite")
    if np.any((raw < 0.0) | (raw > 1.0)):
        raise CalibrationInputError("raw probabilities must be between zero and one")
    if np.any((observed != 0.0) & (observed != 1.0)):
        raise CalibrationInputError("observed events must be binary")
    return raw, observed


def _pool_adjacent_violators(event_sums: np.ndarray, weights: np.ndarray) -> np.ndarray:
    blocks: list[list[float | int]] = []
    for index, (event_sum, weight) in enumerate(zip(event_sums, weights, strict=True)):
        blocks.append([index, index, float(event_sum), float(weight)])
        while len(blocks) >= 2:
            previous = blocks[-2]
            current = blocks[-1]
            if float(previous[2]) / float(previous[3]) <= float(current[2]) / float(
                current[3]
            ):
                break
            blocks[-2:] = [
                [
                    int(previous[0]),
                    int(current[1]),
                    float(previous[2]) + float(current[2]),
                    float(previous[3]) + float(current[3]),
                ]
            ]
    fitted = np.empty(event_sums.size, dtype="float64")
    for start, end, event_sum, weight in blocks:
        fitted[int(start) : int(end) + 1] = float(event_sum) / float(weight)
    return fitted


def _valid_mask(values: np.ndarray, valid_mask: np.ndarray | None) -> np.ndarray:
    if valid_mask is None:
        return np.isfinite(values)
    raw_mask = np.asarray(valid_mask)
    if raw_mask.shape != values.shape or np.any((raw_mask != 0) & (raw_mask != 1)):
        raise CalibrationInputError("calibration valid mask is invalid")
    return raw_mask == 1


def _validate_curve(curve: IsotonicCalibrationCurve) -> None:
    inputs = np.asarray(curve.input_probabilities, dtype="float64")
    outputs = np.asarray(curve.calibrated_probabilities, dtype="float64")
    brier_scores = np.asarray(
        (curve.training_brier_raw, curve.training_brier_calibrated),
        dtype="float64",
    )
    if (
        not curve.lead_band
        or curve.minimum_lead_minutes <= 0
        or curve.maximum_lead_minutes < curve.minimum_lead_minutes
        or not np.isfinite(curve.threshold_mm_h)
        or curve.threshold_mm_h < 0.0
        or inputs.ndim != 1
        or inputs.size < 2
        or outputs.shape != inputs.shape
        or np.any(~np.isfinite(inputs))
        or np.any(~np.isfinite(outputs))
        or np.any((inputs < 0.0) | (inputs > 1.0))
        or np.any((outputs < 0.0) | (outputs > 1.0))
        or np.any(np.diff(inputs) <= 0.0)
        or np.any(np.diff(outputs) < 0.0)
        or np.any(~np.isfinite(brier_scores))
        or np.any((brier_scores < 0.0) | (brier_scores > 1.0))
        or curve.sample_count <= 0
        or curve.event_count <= 0
        or curve.non_event_count <= 0
        or curve.sample_count != curve.event_count + curve.non_event_count
    ):
        raise CalibrationInputError("isotonic calibration curve is invalid")
    if curve.unique_probability_count != len(curve.input_probabilities):
        raise CalibrationInputError("calibration curve unique probability count differs")


def _validate_curve_readiness(
    curve: IsotonicCalibrationCurve,
    policy: CalibrationFitPolicy,
) -> None:
    if (
        curve.sample_count < policy.minimum_sample_count
        or curve.event_count < policy.minimum_event_count
        or curve.non_event_count < policy.minimum_non_event_count
        or curve.unique_probability_count < policy.minimum_unique_probability_count
        or curve.sample_count != curve.event_count + curve.non_event_count
    ):
        raise CalibrationInputError("calibration curve does not meet fit-readiness policy")


def _unique_ids(values: Sequence[str], label: str) -> tuple[str, ...]:
    normalized = tuple(str(value) for value in values)
    if not normalized or any(not value for value in normalized) or len(set(normalized)) != len(
        normalized
    ):
        raise CalibrationInputError(f"calibration {label} case IDs are invalid")
    return normalized


def _is_sha256(value: str) -> bool:
    return len(value) == 64 and all(character in "0123456789abcdef" for character in value)


def _parse_utc(value: str) -> datetime:
    try:
        parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError as exc:
        raise CalibrationInputError("calibration timestamp is invalid") from exc
    if parsed.tzinfo is None or parsed.utcoffset() != timedelta(0):
        raise CalibrationInputError("calibration timestamp must be UTC")
    return parsed.astimezone(UTC)
