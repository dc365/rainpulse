from __future__ import annotations

from collections import Counter
from collections.abc import Mapping
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

import numpy as np
import yaml

PHASE_PROCESSING_MODULE_NAME = "phase_processing_shadow"
PHIDP_SHADOW_UNWRAPPED_FIELD = "PHIDP_SHADOW_UNWRAPPED"
PHIDP_SHADOW_CORRECTED_FIELD = "PHIDP_SHADOW_CORRECTED"
PHIDP_SHADOW_TRUSTED_GATE_MASK_FIELD = "PHIDP_SHADOW_TRUSTED_GATE_MASK"
PHIDP_SHADOW_SEGMENT_INDEX_FIELD = "PHIDP_SHADOW_SEGMENT_INDEX"
KDP_SHADOW_FIELD = "KDP_SHADOW"
KDP_SHADOW_UNCERTAINTY_FIELD = "KDP_SHADOW_UNCERTAINTY"
KDP_SHADOW_AVAILABLE_MASK_FIELD = "KDP_SHADOW_AVAILABLE_MASK"
PHASE_PROCESSING_OUTPUT_DTYPES = {
    PHIDP_SHADOW_UNWRAPPED_FIELD: np.dtype("float32"),
    PHIDP_SHADOW_CORRECTED_FIELD: np.dtype("float32"),
    PHIDP_SHADOW_TRUSTED_GATE_MASK_FIELD: np.dtype("uint8"),
    PHIDP_SHADOW_SEGMENT_INDEX_FIELD: np.dtype("int32"),
    KDP_SHADOW_FIELD: np.dtype("float32"),
    KDP_SHADOW_UNCERTAINTY_FIELD: np.dtype("float32"),
    KDP_SHADOW_AVAILABLE_MASK_FIELD: np.dtype("uint8"),
}
PHASE_PROCESSING_AVAILABLE_MASK_FIELDS = {
    PHIDP_SHADOW_TRUSTED_GATE_MASK_FIELD,
    KDP_SHADOW_AVAILABLE_MASK_FIELD,
}
PHASE_PROCESSING_PRODUCED_VARIABLES = tuple(PHASE_PROCESSING_OUTPUT_DTYPES)


class PhaseProcessingInputError(ValueError):
    """Raised when offline PHIDP/KDP processing inputs are invalid."""


@dataclass(frozen=True)
class PhaseProcessingSegmentationConfig:
    minimum_valid_run_gates: int
    maximum_missing_run_gates: int


@dataclass(frozen=True)
class PhaseProcessingSystemPhaseConfig:
    source_mode: str
    first_gate_count: int
    minimum_gate_count: int
    allow_override_input: bool


@dataclass(frozen=True)
class PhaseProcessingFitConfig:
    method: str
    window_half_width_km: float
    minimum_window_gates: int
    maximum_window_gates: int
    minimum_segment_gates: int
    robust_weighting: str
    tukey_tuning_constant: float
    maximum_iterations: int


@dataclass(frozen=True)
class PhaseProcessingProfile:
    profile_version: str
    artifact_contract_version: str
    source_normalized_radar_volume_contract_version: str
    phase_field_name: str
    phase_wrap_period_degrees: float
    segmentation: PhaseProcessingSegmentationConfig
    system_phase: PhaseProcessingSystemPhaseConfig
    fitting: PhaseProcessingFitConfig
    shadow_processing_enabled: bool
    worker_integration_enabled: bool
    required_gate: str


@dataclass(frozen=True)
class PhaseProcessingRayResult:
    raw_phidp_deg: np.ndarray
    unwrapped_phidp_deg: np.ndarray
    corrected_phidp_deg: np.ndarray
    kdp_deg_per_km: np.ndarray
    kdp_uncertainty_deg_per_km: np.ndarray
    available_mask: np.ndarray
    trusted_gate_mask: np.ndarray
    segment_index: np.ndarray
    system_phase_deg: float | None
    diagnostics: dict[str, Any]


@dataclass(frozen=True)
class PhaseProcessingSweepResult:
    raw_phidp_deg: np.ndarray
    unwrapped_phidp_deg: np.ndarray
    corrected_phidp_deg: np.ndarray
    kdp_deg_per_km: np.ndarray
    kdp_uncertainty_deg_per_km: np.ndarray
    available_mask: np.ndarray
    trusted_gate_mask: np.ndarray
    segment_index: np.ndarray
    system_phase_deg_by_ray: np.ndarray
    diagnostics: dict[str, Any]


def load_phase_processing_profile(path: str | Path) -> PhaseProcessingProfile:
    profile_path = Path(path)
    try:
        raw = yaml.safe_load(profile_path.read_text(encoding="utf-8"))
        segmentation = raw["segmentation"]
        system_phase = raw["system_phase"]
        fitting = raw["fitting"]
        activation = raw["activation"]
        profile = PhaseProcessingProfile(
            profile_version=str(raw["profile_version"]),
            artifact_contract_version=str(raw["artifact_contract_version"]),
            source_normalized_radar_volume_contract_version=str(
                raw["source_normalized_radar_volume_contract_version"]
            ),
            phase_field_name=str(raw["phase_field_name"]),
            phase_wrap_period_degrees=float(raw["phase_wrap_period_degrees"]),
            segmentation=PhaseProcessingSegmentationConfig(
                minimum_valid_run_gates=int(segmentation["minimum_valid_run_gates"]),
                maximum_missing_run_gates=int(segmentation["maximum_missing_run_gates"]),
            ),
            system_phase=PhaseProcessingSystemPhaseConfig(
                source_mode=str(system_phase["source_mode"]),
                first_gate_count=int(system_phase["first_gate_count"]),
                minimum_gate_count=int(system_phase["minimum_gate_count"]),
                allow_override_input=bool(system_phase["allow_override_input"]),
            ),
            fitting=PhaseProcessingFitConfig(
                method=str(fitting["method"]),
                window_half_width_km=float(fitting["window_half_width_km"]),
                minimum_window_gates=int(fitting["minimum_window_gates"]),
                maximum_window_gates=int(fitting["maximum_window_gates"]),
                minimum_segment_gates=int(fitting["minimum_segment_gates"]),
                robust_weighting=str(fitting["robust_weighting"]),
                tukey_tuning_constant=float(fitting["tukey_tuning_constant"]),
                maximum_iterations=int(fitting["maximum_iterations"]),
            ),
            shadow_processing_enabled=bool(activation["shadow_processing_enabled"]),
            worker_integration_enabled=bool(activation["worker_integration_enabled"]),
            required_gate=str(activation["required_gate"]),
        )
    except (KeyError, TypeError, ValueError) as error:
        raise PhaseProcessingInputError(
            f"invalid phase-processing profile {profile_path}: {error}"
        ) from error
    _validate_profile(profile)
    return profile


def process_phidp_ray(
    phidp: np.ndarray,
    range_m: np.ndarray,
    *,
    profile: PhaseProcessingProfile,
    trusted_gate_mask: np.ndarray | None = None,
    system_phase_deg: float | None = None,
    input_phase_unit: str = "degree",
) -> PhaseProcessingRayResult:
    range_km = _normalize_range_km(range_m)
    raw_phidp_deg = _normalize_phase_degrees(phidp, input_phase_unit)
    if raw_phidp_deg.ndim != 1:
        raise PhaseProcessingInputError("PHIDP ray must be one-dimensional")
    trusted_mask = _normalize_trusted_gate_mask(trusted_gate_mask, raw_phidp_deg.shape)
    valid_mask = np.isfinite(raw_phidp_deg) & trusted_mask
    segments = _contiguous_segments(valid_mask, profile.segmentation.maximum_missing_run_gates)

    unwrapped = np.full(raw_phidp_deg.shape, np.nan, dtype="float32")
    corrected = np.full(raw_phidp_deg.shape, np.nan, dtype="float32")
    kdp = np.full(raw_phidp_deg.shape, np.nan, dtype="float32")
    uncertainty = np.full(raw_phidp_deg.shape, np.nan, dtype="float32")
    available_mask = np.zeros(raw_phidp_deg.shape, dtype="uint8")
    segment_index = np.full(raw_phidp_deg.shape, -1, dtype="int32")

    processed_segment_count = 0
    segment_failures: list[str] = []
    system_phase_values: list[float] = []

    for index, (start, stop) in enumerate(segments):
        segment_index[start:stop] = index
        if (
            stop - start < profile.segmentation.minimum_valid_run_gates
            or stop - start < profile.fitting.minimum_segment_gates
        ):
            segment_failures.append("segment_too_short")
            continue

        segment_phase = raw_phidp_deg[start:stop].astype("float64", copy=False)
        segment_unwrapped = np.rad2deg(
            np.unwrap(
                np.deg2rad(segment_phase),
                period=np.deg2rad(profile.phase_wrap_period_degrees),
            )
        )
        estimated_system_phase = _segment_system_phase(
            segment_unwrapped,
            profile=profile,
            override_system_phase_deg=system_phase_deg,
        )
        if estimated_system_phase is None:
            segment_failures.append("system_phase_unavailable")
            continue

        processed_segment_count += 1
        system_phase_values.append(float(estimated_system_phase))
        segment_corrected = segment_unwrapped - estimated_system_phase
        unwrapped[start:stop] = segment_unwrapped.astype("float32")
        corrected[start:stop] = segment_corrected.astype("float32")
        segment_range_km = range_km[start:stop]

        for local_index in range(stop - start):
            window_indices = _fit_window_indices(segment_range_km, local_index, profile)
            if window_indices is None:
                continue
            slope_deg_per_km, slope_se = _robust_local_linear_fit(
                segment_range_km[window_indices],
                segment_corrected[window_indices],
                profile,
            )
            if not np.isfinite(slope_deg_per_km):
                continue
            global_index = start + local_index
            kdp[global_index] = np.float32(0.5 * slope_deg_per_km)
            uncertainty[global_index] = (
                np.float32(0.5 * slope_se) if np.isfinite(slope_se) else np.nan
            )
            available_mask[global_index] = 1

    diagnostics = {
        "input_phase_unit": input_phase_unit,
        "segment_count": len(segments),
        "processed_segment_count": processed_segment_count,
        "available_gate_count": int(np.count_nonzero(available_mask)),
        "segment_failures": segment_failures,
        "required_gate": profile.required_gate,
    }
    system_phase_value = system_phase_values[0] if len(system_phase_values) == 1 else None
    return PhaseProcessingRayResult(
        raw_phidp_deg=raw_phidp_deg,
        unwrapped_phidp_deg=unwrapped,
        corrected_phidp_deg=corrected,
        kdp_deg_per_km=kdp,
        kdp_uncertainty_deg_per_km=uncertainty,
        available_mask=available_mask,
        trusted_gate_mask=trusted_mask.astype("uint8"),
        segment_index=segment_index,
        system_phase_deg=system_phase_value,
        diagnostics=diagnostics,
    )


def process_phidp_sweep(
    phidp: np.ndarray,
    range_m: np.ndarray,
    *,
    profile: PhaseProcessingProfile,
    trusted_gate_mask: np.ndarray | None = None,
    system_phase_deg_by_ray: np.ndarray | None = None,
    input_phase_unit: str = "degree",
) -> PhaseProcessingSweepResult:
    values = _normalize_phase_degrees(phidp, input_phase_unit)
    if values.ndim != 2:
        raise PhaseProcessingInputError("PHIDP sweep must be two-dimensional [ray, gate]")
    if trusted_gate_mask is None:
        trusted = np.ones(values.shape, dtype=bool)
    else:
        trusted = np.asarray(trusted_gate_mask, dtype=bool)
        if trusted.shape != values.shape:
            raise PhaseProcessingInputError("trusted_gate_mask shape must match PHIDP sweep")
    if system_phase_deg_by_ray is None:
        system_phase_values = np.full(values.shape[0], np.nan, dtype="float32")
    else:
        system_phase_values = np.asarray(system_phase_deg_by_ray, dtype="float32")
        if system_phase_values.shape != (values.shape[0],):
            raise PhaseProcessingInputError("system_phase_deg_by_ray must match ray count")

    per_ray = [
        process_phidp_ray(
            values[ray_index],
            range_m,
            profile=profile,
            trusted_gate_mask=trusted[ray_index],
            system_phase_deg=(
                None
                if np.isnan(system_phase_values[ray_index])
                else float(system_phase_values[ray_index])
            ),
            input_phase_unit="degree",
        )
        for ray_index in range(values.shape[0])
    ]
    return PhaseProcessingSweepResult(
        raw_phidp_deg=np.stack([result.raw_phidp_deg for result in per_ray]).astype("float32"),
        unwrapped_phidp_deg=np.stack([result.unwrapped_phidp_deg for result in per_ray]).astype(
            "float32"
        ),
        corrected_phidp_deg=np.stack([result.corrected_phidp_deg for result in per_ray]).astype(
            "float32"
        ),
        kdp_deg_per_km=np.stack([result.kdp_deg_per_km for result in per_ray]).astype("float32"),
        kdp_uncertainty_deg_per_km=np.stack(
            [result.kdp_uncertainty_deg_per_km for result in per_ray]
        ).astype("float32"),
        available_mask=np.stack([result.available_mask for result in per_ray]).astype("uint8"),
        trusted_gate_mask=np.stack([result.trusted_gate_mask for result in per_ray]).astype(
            "uint8"
        ),
        segment_index=np.stack([result.segment_index for result in per_ray]).astype("int32"),
        system_phase_deg_by_ray=np.asarray(
            [
                np.nan if result.system_phase_deg is None else result.system_phase_deg
                for result in per_ray
            ],
            dtype="float32",
        ),
        diagnostics={
            "ray_count": values.shape[0],
            "available_gate_count": int(
                sum(item.diagnostics["available_gate_count"] for item in per_ray)
            ),
            "segment_count": int(sum(item.diagnostics["segment_count"] for item in per_ray)),
            "processed_segment_count": int(
                sum(item.diagnostics["processed_segment_count"] for item in per_ray)
            ),
            "segment_failures": [
                failure for item in per_ray for failure in item.diagnostics["segment_failures"]
            ],
            "input_phase_unit": input_phase_unit,
            "required_gate": profile.required_gate,
        },
    )


def empty_phase_processing_qc_fields(shape: tuple[int, int]) -> dict[str, np.ndarray]:
    return {
        PHIDP_SHADOW_UNWRAPPED_FIELD: np.full(shape, np.nan, dtype="float32"),
        PHIDP_SHADOW_CORRECTED_FIELD: np.full(shape, np.nan, dtype="float32"),
        PHIDP_SHADOW_TRUSTED_GATE_MASK_FIELD: np.zeros(shape, dtype="uint8"),
        PHIDP_SHADOW_SEGMENT_INDEX_FIELD: np.full(shape, -1, dtype="int32"),
        KDP_SHADOW_FIELD: np.full(shape, np.nan, dtype="float32"),
        KDP_SHADOW_UNCERTAINTY_FIELD: np.full(shape, np.nan, dtype="float32"),
        KDP_SHADOW_AVAILABLE_MASK_FIELD: np.zeros(shape, dtype="uint8"),
    }


def phase_processing_qc_fields(result: PhaseProcessingSweepResult) -> dict[str, np.ndarray]:
    return {
        PHIDP_SHADOW_UNWRAPPED_FIELD: result.unwrapped_phidp_deg.astype(
            "float32",
            copy=True,
        ),
        PHIDP_SHADOW_CORRECTED_FIELD: result.corrected_phidp_deg.astype(
            "float32",
            copy=True,
        ),
        PHIDP_SHADOW_TRUSTED_GATE_MASK_FIELD: result.trusted_gate_mask.astype(
            "uint8",
            copy=True,
        ),
        PHIDP_SHADOW_SEGMENT_INDEX_FIELD: result.segment_index.astype("int32", copy=True),
        KDP_SHADOW_FIELD: result.kdp_deg_per_km.astype("float32", copy=True),
        KDP_SHADOW_UNCERTAINTY_FIELD: result.kdp_uncertainty_deg_per_km.astype(
            "float32",
            copy=True,
        ),
        KDP_SHADOW_AVAILABLE_MASK_FIELD: result.available_mask.astype("uint8", copy=True),
    }


def phase_processing_qc_metrics(result: PhaseProcessingSweepResult) -> dict[str, float]:
    failure_counts = Counter(str(item) for item in result.diagnostics.get("segment_failures", []))
    metrics = {
        "ray_count": float(result.raw_phidp_deg.shape[0]),
        "available_gate_count": float(result.diagnostics.get("available_gate_count", 0)),
        "segment_count": float(result.diagnostics.get("segment_count", 0)),
        "processed_segment_count": float(result.diagnostics.get("processed_segment_count", 0)),
        "trusted_gate_count": float(np.count_nonzero(result.trusted_gate_mask)),
        "system_phase_available_ray_count": float(
            np.count_nonzero(np.isfinite(result.system_phase_deg_by_ray))
        ),
    }
    for failure_name, count in sorted(failure_counts.items()):
        metrics[f"failure_{failure_name}_count"] = float(count)
    return metrics


def build_phase_processing_artifact(
    results_by_sweep: Mapping[str, PhaseProcessingRayResult | PhaseProcessingSweepResult],
    *,
    profile: PhaseProcessingProfile,
    artifact_id: str,
    created_at_utc: datetime,
    radar_id: str,
    scan_id: str,
    source_normalized_uri: str,
    source_manifest_sha256: str,
    input_phase_unit: str,
) -> dict[str, Any]:
    if not artifact_id:
        raise PhaseProcessingInputError("artifact_id must be non-empty")
    if not radar_id or not scan_id:
        raise PhaseProcessingInputError("radar_id and scan_id must be non-empty")
    if not source_normalized_uri:
        raise PhaseProcessingInputError("source_normalized_uri must be non-empty")
    _validate_sha256(source_manifest_sha256, "source_manifest_sha256")
    _validate_input_phase_unit(input_phase_unit)
    sweeps = {
        sweep_name: _artifact_sweep_summary(result)
        for sweep_name, result in sorted(results_by_sweep.items())
    }
    return {
        "schema_version": "1.0",
        "artifact_contract_version": profile.artifact_contract_version,
        "artifact_id": artifact_id,
        "profile_version": profile.profile_version,
        "created_at_utc": _format_utc(created_at_utc),
        "radar_id": radar_id,
        "scan_id": scan_id,
        "phase_field_name": profile.phase_field_name,
        "operational_eligible": False,
        "worker_integration_enabled": profile.worker_integration_enabled,
        "source_input": {
            "source_normalized_radar_volume_contract_version": (
                profile.source_normalized_radar_volume_contract_version
            ),
            "normalized_uri": source_normalized_uri,
            "source_manifest_sha256": source_manifest_sha256,
            "input_phase_unit": input_phase_unit,
        },
        "sweeps": sweeps,
    }


def _validate_profile(profile: PhaseProcessingProfile) -> None:
    if profile.profile_version != "fujian-phidp-kdp-shadow-v1":
        raise PhaseProcessingInputError("unsupported phase-processing profile version")
    if profile.artifact_contract_version != "1.0":
        raise PhaseProcessingInputError("unsupported phase-processing artifact contract")
    if profile.source_normalized_radar_volume_contract_version != "1.0":
        raise PhaseProcessingInputError("unsupported normalized radar-volume contract version")
    if profile.phase_field_name != "PHIDP":
        raise PhaseProcessingInputError("phase-processing profile must target PHIDP")
    if profile.phase_wrap_period_degrees <= 0.0:
        raise PhaseProcessingInputError("phase_wrap_period_degrees must be positive")
    if profile.segmentation.maximum_missing_run_gates != 0:
        raise PhaseProcessingInputError("maximum_missing_run_gates must remain zero in C1a")
    if profile.fitting.method != "robust_local_linear":
        raise PhaseProcessingInputError("unsupported phase-processing fit method")
    if profile.fitting.robust_weighting != "tukey_biweight":
        raise PhaseProcessingInputError("unsupported phase-processing robust weighting")
    if profile.fitting.minimum_window_gates > profile.fitting.maximum_window_gates:
        raise PhaseProcessingInputError("minimum_window_gates must not exceed maximum_window_gates")
    if profile.system_phase.first_gate_count > profile.fitting.minimum_segment_gates:
        raise PhaseProcessingInputError(
            "system_phase.first_gate_count must not exceed minimum_segment_gates"
        )


def _normalize_range_km(range_m: np.ndarray) -> np.ndarray:
    values = np.asarray(range_m, dtype="float64")
    if values.ndim != 1 or values.size == 0:
        raise PhaseProcessingInputError("range_m must be a non-empty one-dimensional array")
    if not np.isfinite(values).all():
        raise PhaseProcessingInputError("range_m must be finite")
    if not np.all(np.diff(values) > 0.0):
        raise PhaseProcessingInputError("range_m must be strictly increasing")
    return values / 1000.0


def _normalize_phase_degrees(phidp: np.ndarray, input_phase_unit: str) -> np.ndarray:
    values = np.asarray(phidp, dtype="float32")
    _validate_input_phase_unit(input_phase_unit)
    if input_phase_unit == "degree":
        return values.astype("float32", copy=True)
    return np.rad2deg(values).astype("float32")


def _normalize_trusted_gate_mask(
    trusted_gate_mask: np.ndarray | None,
    expected_shape: tuple[int, ...],
) -> np.ndarray:
    if trusted_gate_mask is None:
        return np.ones(expected_shape, dtype=bool)
    values = np.asarray(trusted_gate_mask, dtype=bool)
    if values.shape != expected_shape:
        raise PhaseProcessingInputError("trusted_gate_mask shape must match PHIDP shape")
    return values


def _validate_input_phase_unit(input_phase_unit: str) -> None:
    if input_phase_unit not in {"degree", "radian"}:
        raise PhaseProcessingInputError("input_phase_unit must be degree or radian")


def _contiguous_segments(
    valid_mask: np.ndarray,
    maximum_missing_run_gates: int,
) -> list[tuple[int, int]]:
    if maximum_missing_run_gates != 0:
        raise PhaseProcessingInputError("C1a does not support bridging missing PHIDP gates")
    indices = np.flatnonzero(valid_mask)
    if indices.size == 0:
        return []
    segments: list[tuple[int, int]] = []
    start = int(indices[0])
    previous = int(indices[0])
    for value in indices[1:]:
        current = int(value)
        if current != previous + 1:
            segments.append((start, previous + 1))
            start = current
        previous = current
    segments.append((start, previous + 1))
    return segments


def _segment_system_phase(
    segment_unwrapped_deg: np.ndarray,
    *,
    profile: PhaseProcessingProfile,
    override_system_phase_deg: float | None,
) -> float | None:
    if override_system_phase_deg is not None:
        if not profile.system_phase.allow_override_input:
            return None
        if not np.isfinite(override_system_phase_deg):
            raise PhaseProcessingInputError("system_phase_deg override must be finite")
        return float(override_system_phase_deg)
    if segment_unwrapped_deg.size < profile.system_phase.minimum_gate_count:
        return None
    first_gate_count = min(profile.system_phase.first_gate_count, segment_unwrapped_deg.size)
    seed_values = segment_unwrapped_deg[:first_gate_count]
    if seed_values.size < profile.system_phase.minimum_gate_count:
        return None
    return float(np.median(seed_values))


def _fit_window_indices(
    range_km: np.ndarray,
    center_index: int,
    profile: PhaseProcessingProfile,
) -> np.ndarray | None:
    distance = np.abs(range_km - range_km[center_index])
    indices = np.flatnonzero(distance <= profile.fitting.window_half_width_km)
    if indices.size < profile.fitting.minimum_window_gates:
        return None
    if indices.size > profile.fitting.maximum_window_gates:
        keep_order = np.argsort(distance[indices], kind="stable")[
            : profile.fitting.maximum_window_gates
        ]
        indices = np.sort(indices[keep_order])
    if indices.size < 2:
        return None
    return indices


def _robust_local_linear_fit(
    range_km: np.ndarray,
    corrected_deg: np.ndarray,
    profile: PhaseProcessingProfile,
) -> tuple[float, float]:
    x = np.asarray(range_km, dtype="float64")
    y = np.asarray(corrected_deg, dtype="float64")
    weights = np.ones(x.shape, dtype="float64")
    slope = np.nan
    intercept = np.nan
    for _ in range(profile.fitting.maximum_iterations):
        intercept, slope = _weighted_linear_fit(x, y, weights)
        residual = y - (intercept + slope * x)
        scale = 1.4826 * np.median(np.abs(residual - np.median(residual)))
        if not np.isfinite(scale) or scale < 1e-6:
            break
        scaled = residual / (profile.fitting.tukey_tuning_constant * scale)
        updated = np.where(np.abs(scaled) < 1.0, (1.0 - scaled**2) ** 2, 0.0)
        if np.count_nonzero(updated) < max(3, profile.fitting.minimum_window_gates - 1):
            break
        if np.allclose(updated, weights, atol=1e-6, rtol=1e-6):
            weights = updated
            break
        weights = updated
    intercept, slope = _weighted_linear_fit(x, y, weights)
    residual = y - (intercept + slope * x)
    positive = weights > 0.0
    if np.count_nonzero(positive) <= 2:
        return float(slope), float("nan")
    x_bar = np.average(x[positive], weights=weights[positive])
    sxx = float(np.sum(weights[positive] * (x[positive] - x_bar) ** 2))
    if sxx <= 0.0:
        return float(slope), float("nan")
    dof = max(1, int(np.count_nonzero(positive)) - 2)
    sigma2 = float(np.sum(weights[positive] * residual[positive] ** 2) / dof)
    return float(slope), float(np.sqrt(max(sigma2, 0.0) / sxx))


def _weighted_linear_fit(
    x: np.ndarray,
    y: np.ndarray,
    weights: np.ndarray,
) -> tuple[float, float]:
    positive = np.where(weights > 0.0, weights, 0.0)
    if np.count_nonzero(positive) < 2:
        positive = np.ones_like(weights)
    matrix = np.column_stack((np.ones(x.shape, dtype="float64"), x))
    scaled = np.sqrt(positive)
    beta, _, _, _ = np.linalg.lstsq(matrix * scaled[:, None], y * scaled, rcond=None)
    return float(beta[0]), float(beta[1])


def _artifact_sweep_summary(
    result: PhaseProcessingRayResult | PhaseProcessingSweepResult,
) -> dict[str, Any]:
    kdp_values = result.kdp_deg_per_km[np.isfinite(result.kdp_deg_per_km)]
    summary = {
        "available_gate_count": int(np.count_nonzero(result.available_mask)),
        "segment_count": int(result.diagnostics["segment_count"]),
        "processed_segment_count": int(result.diagnostics["processed_segment_count"]),
        "segment_failures": list(result.diagnostics["segment_failures"]),
        "system_phase_deg": (
            None
            if getattr(result, "system_phase_deg", None) is None
            else float(result.system_phase_deg)
        ),
    }
    if kdp_values.size:
        summary["kdp_summary_deg_per_km"] = {
            "minimum": float(np.min(kdp_values)),
            "median": float(np.median(kdp_values)),
            "maximum": float(np.max(kdp_values)),
        }
    else:
        summary["kdp_summary_deg_per_km"] = {
            "minimum": None,
            "median": None,
            "maximum": None,
        }
    return summary


def _validate_sha256(value: str, field_name: str) -> None:
    if len(value) != 64 or any(character not in "0123456789abcdef" for character in value):
        raise PhaseProcessingInputError(f"{field_name} must be a lowercase SHA-256")


def _format_utc(value: datetime) -> str:
    if value.tzinfo is None:
        value = value.replace(tzinfo=UTC)
    return value.astimezone(UTC).isoformat().replace("+00:00", "Z")
