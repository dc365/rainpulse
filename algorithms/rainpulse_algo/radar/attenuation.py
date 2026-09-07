from __future__ import annotations

from collections import Counter
from collections.abc import Mapping
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

import numpy as np
import yaml

SUPPORTED_ATTENUATION_METHOD = "kdp_path_integral"
SUPPORTED_INTEGRATION_SCHEME = "two_way_trapezoidal"
SUPPORTED_COEFFICIENT_SOURCES = {
    "unconfigured",
    "explicit_shadow_coefficients",
    "frozen_shadow_coefficient_table",
}
SUPPORTED_UNAVAILABLE_SEGMENT_POLICY = "split_and_reset"
SUPPORTED_KDP_INPUT_UNIT = "degree/km"
ATTENUATION_SHADOW_MODULE_NAME = "attenuation_shadow"
SPECIFIC_ATTENUATION_SHADOW_FIELD = "SPECIFIC_ATTENUATION_SHADOW"
ATTENUATION_CORRECTION_SHADOW_FIELD = "ATTENUATION_CORRECTION_SHADOW"
DBZH_ATTENUATION_SHADOW_CORRECTED_FIELD = "DBZH_ATTENUATION_SHADOW_CORRECTED"
ATTENUATION_SHADOW_AVAILABLE_MASK_FIELD = "ATTENUATION_SHADOW_AVAILABLE_MASK"
ATTENUATION_SHADOW_SEGMENT_INDEX_FIELD = "ATTENUATION_SHADOW_SEGMENT_INDEX"
ATTENUATION_SHADOW_OUTPUT_DTYPES = {
    SPECIFIC_ATTENUATION_SHADOW_FIELD: np.dtype("float32"),
    ATTENUATION_CORRECTION_SHADOW_FIELD: np.dtype("float32"),
    DBZH_ATTENUATION_SHADOW_CORRECTED_FIELD: np.dtype("float32"),
    ATTENUATION_SHADOW_AVAILABLE_MASK_FIELD: np.dtype("uint8"),
    ATTENUATION_SHADOW_SEGMENT_INDEX_FIELD: np.dtype("int32"),
}
ATTENUATION_SHADOW_AVAILABLE_MASK_FIELDS = {
    ATTENUATION_SHADOW_AVAILABLE_MASK_FIELD,
}
ATTENUATION_SHADOW_PRODUCED_VARIABLES = tuple(ATTENUATION_SHADOW_OUTPUT_DTYPES)


class AttenuationInputError(ValueError):
    """Raised when shadow attenuation inputs or provenance are invalid."""


@dataclass(frozen=True)
class AttenuationCoefficientConfig:
    source: str
    coefficient_a: float | None
    exponent_b: float | None
    minimum_kdp_deg_per_km: float
    maximum_specific_attenuation_db_per_km: float
    maximum_correction_db: float


@dataclass(frozen=True)
class AttenuationApplicabilityConfig:
    maximum_blockage_fraction: float
    corrected_reflectivity_cap_dbz: float
    unavailable_segment_policy: str


@dataclass(frozen=True)
class AttenuationProfile:
    profile_version: str
    artifact_contract_version: str
    source_normalized_radar_volume_contract_version: str
    source_phase_processing_profile_version: str
    radar_band: str
    method_name: str
    integration_scheme: str
    coefficients: AttenuationCoefficientConfig
    applicability: AttenuationApplicabilityConfig
    shadow_processing_enabled: bool
    worker_integration_enabled: bool
    required_gate: str


@dataclass(frozen=True)
class AttenuationRayResult:
    raw_dbzh_dbz: np.ndarray
    kdp_deg_per_km: np.ndarray
    specific_attenuation_db_per_km: np.ndarray
    attenuation_correction_db: np.ndarray
    corrected_dbzh_dbz: np.ndarray
    available_mask: np.ndarray
    segment_index: np.ndarray
    diagnostics: dict[str, Any]


@dataclass(frozen=True)
class AttenuationSweepResult:
    raw_dbzh_dbz: np.ndarray
    kdp_deg_per_km: np.ndarray
    specific_attenuation_db_per_km: np.ndarray
    attenuation_correction_db: np.ndarray
    corrected_dbzh_dbz: np.ndarray
    available_mask: np.ndarray
    segment_index: np.ndarray
    diagnostics: dict[str, Any]


def load_attenuation_profile(path: str | Path) -> AttenuationProfile:
    profile_path = Path(path)
    try:
        raw = yaml.safe_load(profile_path.read_text(encoding="utf-8"))
        method = raw["method"]
        coefficients = raw["coefficients"]
        applicability = raw["applicability"]
        activation = raw["activation"]
        profile = AttenuationProfile(
            profile_version=str(raw["profile_version"]),
            artifact_contract_version=str(raw["artifact_contract_version"]),
            source_normalized_radar_volume_contract_version=str(
                raw["source_normalized_radar_volume_contract_version"]
            ),
            source_phase_processing_profile_version=str(
                raw["source_phase_processing_profile_version"]
            ),
            radar_band=str(raw["radar_band"]),
            method_name=str(method["name"]),
            integration_scheme=str(method["integration_scheme"]),
            coefficients=AttenuationCoefficientConfig(
                source=str(coefficients["source"]),
                coefficient_a=_optional_float(coefficients.get("coefficient_a")),
                exponent_b=_optional_float(coefficients.get("exponent_b")),
                minimum_kdp_deg_per_km=float(coefficients["minimum_kdp_deg_per_km"]),
                maximum_specific_attenuation_db_per_km=float(
                    coefficients["maximum_specific_attenuation_db_per_km"]
                ),
                maximum_correction_db=float(coefficients["maximum_correction_db"]),
            ),
            applicability=AttenuationApplicabilityConfig(
                maximum_blockage_fraction=float(applicability["maximum_blockage_fraction"]),
                corrected_reflectivity_cap_dbz=float(
                    applicability["corrected_reflectivity_cap_dbz"]
                ),
                unavailable_segment_policy=str(applicability["unavailable_segment_policy"]),
            ),
            shadow_processing_enabled=bool(activation["shadow_processing_enabled"]),
            worker_integration_enabled=bool(activation["worker_integration_enabled"]),
            required_gate=str(activation["required_gate"]),
        )
    except (KeyError, TypeError, ValueError) as error:
        raise AttenuationInputError(
            f"invalid attenuation profile {profile_path}: {error}"
        ) from error
    _validate_profile(profile)
    return profile


def process_kdp_attenuation_ray(
    dbzh: np.ndarray,
    kdp: np.ndarray,
    range_m: np.ndarray,
    *,
    profile: AttenuationProfile,
    kdp_available_mask: np.ndarray | None = None,
    blockage_fraction: np.ndarray | None = None,
    input_kdp_unit: str = SUPPORTED_KDP_INPUT_UNIT,
) -> AttenuationRayResult:
    _validate_profile(profile)
    _validate_kdp_input_unit(input_kdp_unit)
    range_km = _normalize_range_km(range_m)
    raw_dbzh = np.asarray(dbzh, dtype="float32")
    raw_kdp = np.asarray(kdp, dtype="float32")
    if raw_dbzh.ndim != 1 or raw_kdp.ndim != 1:
        raise AttenuationInputError("DBZH and KDP rays must be one-dimensional")
    if raw_dbzh.shape != raw_kdp.shape or raw_dbzh.shape != range_km.shape:
        raise AttenuationInputError("DBZH, KDP and range_m shapes must match")

    if not profile.shadow_processing_enabled:
        return _empty_ray_result(
            raw_dbzh,
            raw_kdp,
            skip_reason="attenuation_shadow_disabled",
        )
    if profile.coefficients.source == "unconfigured":
        return _empty_ray_result(
            raw_dbzh,
            raw_kdp,
            skip_reason="attenuation_coefficients_unconfigured",
        )

    if blockage_fraction is None:
        return _empty_ray_result(raw_dbzh, raw_kdp, skip_reason="blockage_unavailable")
    finite_dbzh = np.isfinite(raw_dbzh)
    available_kdp = _normalize_kdp_available_mask(kdp_available_mask, raw_kdp.shape)
    available_kdp &= np.isfinite(raw_kdp)
    blockage_usable = _normalize_blockage_fraction(blockage_fraction, raw_dbzh.shape, profile)
    processable = finite_dbzh & available_kdp & blockage_usable
    segments = _contiguous_segments(processable)

    specific_attenuation = np.full(raw_dbzh.shape, np.nan, dtype="float32")
    attenuation_correction = np.full(raw_dbzh.shape, np.nan, dtype="float32")
    corrected_dbzh = np.full(raw_dbzh.shape, np.nan, dtype="float32")
    available_mask = np.zeros(raw_dbzh.shape, dtype="uint8")
    segment_index = np.full(raw_dbzh.shape, -1, dtype="int32")

    capped_gate_count = 0
    for index, (start, stop) in enumerate(segments):
        segment_index[start:stop] = index
        segment_kdp = np.asarray(raw_kdp[start:stop], dtype="float64")
        active_kdp = np.where(
            segment_kdp >= profile.coefficients.minimum_kdp_deg_per_km,
            segment_kdp,
            0.0,
        )
        segment_specific = profile.coefficients.coefficient_a * np.power(
            active_kdp,
            profile.coefficients.exponent_b,
        )
        segment_specific = np.clip(
            segment_specific,
            0.0,
            profile.coefficients.maximum_specific_attenuation_db_per_km,
        )
        segment_correction = _two_way_trapezoidal_correction(
            range_km[start:stop],
            segment_specific,
        )
        correction_clipped = segment_correction > profile.coefficients.maximum_correction_db
        segment_correction = np.minimum(
            segment_correction,
            profile.coefficients.maximum_correction_db,
        )
        segment_corrected = raw_dbzh[start:stop].astype("float64", copy=False) + segment_correction
        reflectivity_clipped = (
            segment_corrected > profile.applicability.corrected_reflectivity_cap_dbz
        )
        segment_corrected = np.minimum(
            segment_corrected,
            profile.applicability.corrected_reflectivity_cap_dbz,
        )

        specific_attenuation[start:stop] = segment_specific.astype("float32")
        attenuation_correction[start:stop] = segment_correction.astype("float32")
        corrected_dbzh[start:stop] = segment_corrected.astype("float32")
        available_mask[start:stop] = 1
        capped_gate_count += int(np.count_nonzero(correction_clipped | reflectivity_clipped))

    return AttenuationRayResult(
        raw_dbzh_dbz=raw_dbzh.astype("float32", copy=True),
        kdp_deg_per_km=raw_kdp.astype("float32", copy=True),
        specific_attenuation_db_per_km=specific_attenuation,
        attenuation_correction_db=attenuation_correction,
        corrected_dbzh_dbz=corrected_dbzh,
        available_mask=available_mask,
        segment_index=segment_index,
        diagnostics={
            "input_kdp_unit": input_kdp_unit,
            "segment_count": len(segments),
            "processed_segment_count": len(segments),
            "available_gate_count": int(np.count_nonzero(available_mask)),
            "capped_gate_count": capped_gate_count,
            "skip_reasons": [],
        },
    )


def process_kdp_attenuation_sweep(
    dbzh: np.ndarray,
    kdp: np.ndarray,
    range_m: np.ndarray,
    *,
    profile: AttenuationProfile,
    kdp_available_mask: np.ndarray | None = None,
    blockage_fraction: np.ndarray | None = None,
    input_kdp_unit: str = SUPPORTED_KDP_INPUT_UNIT,
) -> AttenuationSweepResult:
    values = np.asarray(dbzh, dtype="float32")
    kdp_values = np.asarray(kdp, dtype="float32")
    if values.ndim != 2 or kdp_values.ndim != 2:
        raise AttenuationInputError("DBZH and KDP sweeps must be two-dimensional [ray, gate]")
    if values.shape != kdp_values.shape:
        raise AttenuationInputError("DBZH and KDP sweeps must share the same shape")
    available = _normalize_optional_2d_mask(kdp_available_mask, values.shape)
    blockage = _normalize_optional_2d_values(blockage_fraction, values.shape)

    per_ray = [
        process_kdp_attenuation_ray(
            values[ray_index],
            kdp_values[ray_index],
            range_m,
            profile=profile,
            kdp_available_mask=(None if available is None else available[ray_index]),
            blockage_fraction=(None if blockage is None else blockage[ray_index]),
            input_kdp_unit=input_kdp_unit,
        )
        for ray_index in range(values.shape[0])
    ]
    return AttenuationSweepResult(
        raw_dbzh_dbz=np.stack([item.raw_dbzh_dbz for item in per_ray]).astype("float32"),
        kdp_deg_per_km=np.stack([item.kdp_deg_per_km for item in per_ray]).astype("float32"),
        specific_attenuation_db_per_km=np.stack(
            [item.specific_attenuation_db_per_km for item in per_ray]
        ).astype("float32"),
        attenuation_correction_db=np.stack(
            [item.attenuation_correction_db for item in per_ray]
        ).astype("float32"),
        corrected_dbzh_dbz=np.stack([item.corrected_dbzh_dbz for item in per_ray]).astype(
            "float32"
        ),
        available_mask=np.stack([item.available_mask for item in per_ray]).astype("uint8"),
        segment_index=np.stack([item.segment_index for item in per_ray]).astype("int32"),
        diagnostics={
            "ray_count": values.shape[0],
            "segment_count": int(sum(item.diagnostics["segment_count"] for item in per_ray)),
            "processed_segment_count": int(
                sum(item.diagnostics["processed_segment_count"] for item in per_ray)
            ),
            "available_gate_count": int(
                sum(item.diagnostics["available_gate_count"] for item in per_ray)
            ),
            "capped_gate_count": int(
                sum(item.diagnostics["capped_gate_count"] for item in per_ray)
            ),
            "skip_reasons": [
                reason for item in per_ray for reason in item.diagnostics.get("skip_reasons", [])
            ],
            "input_kdp_unit": input_kdp_unit,
        },
    )


def empty_attenuation_qc_fields(shape: tuple[int, int]) -> dict[str, np.ndarray]:
    return {
        SPECIFIC_ATTENUATION_SHADOW_FIELD: np.full(shape, np.nan, dtype="float32"),
        ATTENUATION_CORRECTION_SHADOW_FIELD: np.full(shape, np.nan, dtype="float32"),
        DBZH_ATTENUATION_SHADOW_CORRECTED_FIELD: np.full(shape, np.nan, dtype="float32"),
        ATTENUATION_SHADOW_AVAILABLE_MASK_FIELD: np.zeros(shape, dtype="uint8"),
        ATTENUATION_SHADOW_SEGMENT_INDEX_FIELD: np.full(shape, -1, dtype="int32"),
    }


def attenuation_qc_fields(result: AttenuationSweepResult) -> dict[str, np.ndarray]:
    return {
        SPECIFIC_ATTENUATION_SHADOW_FIELD: result.specific_attenuation_db_per_km.astype(
            "float32",
            copy=True,
        ),
        ATTENUATION_CORRECTION_SHADOW_FIELD: result.attenuation_correction_db.astype(
            "float32",
            copy=True,
        ),
        DBZH_ATTENUATION_SHADOW_CORRECTED_FIELD: result.corrected_dbzh_dbz.astype(
            "float32",
            copy=True,
        ),
        ATTENUATION_SHADOW_AVAILABLE_MASK_FIELD: result.available_mask.astype(
            "uint8",
            copy=True,
        ),
        ATTENUATION_SHADOW_SEGMENT_INDEX_FIELD: result.segment_index.astype(
            "int32",
            copy=True,
        ),
    }


def attenuation_qc_metrics(result: AttenuationSweepResult) -> dict[str, float]:
    return {
        "ray_count": float(result.raw_dbzh_dbz.shape[0]),
        "available_gate_count": float(result.diagnostics.get("available_gate_count", 0)),
        "segment_count": float(result.diagnostics.get("segment_count", 0)),
        "processed_segment_count": float(result.diagnostics.get("processed_segment_count", 0)),
        "capped_gate_count": float(result.diagnostics.get("capped_gate_count", 0)),
    }


def build_attenuation_artifact(
    results_by_sweep: Mapping[str, AttenuationRayResult | AttenuationSweepResult],
    *,
    profile: AttenuationProfile,
    artifact_id: str,
    created_at_utc: datetime,
    radar_id: str,
    scan_id: str,
    source_normalized_uri: str,
    source_manifest_sha256: str,
    kdp_input_unit: str,
    coefficient_table_version: str | None = None,
) -> dict[str, Any]:
    _validate_profile(profile)
    if not artifact_id:
        raise AttenuationInputError("artifact_id must be non-empty")
    if not radar_id or not scan_id:
        raise AttenuationInputError("radar_id and scan_id must be non-empty")
    if not source_normalized_uri:
        raise AttenuationInputError("source_normalized_uri must be non-empty")
    _validate_sha256(source_manifest_sha256, "source_manifest_sha256")
    _validate_kdp_input_unit(kdp_input_unit)
    if (
        profile.coefficients.source == "frozen_shadow_coefficient_table"
        and not coefficient_table_version
    ):
        raise AttenuationInputError(
            "frozen shadow coefficient artifacts require coefficient_table_version"
        )
    sweeps = {
        sweep_name: _artifact_sweep_summary(result)
        for sweep_name, result in sorted(results_by_sweep.items())
    }
    source_input = {
        "source_normalized_radar_volume_contract_version": (
            profile.source_normalized_radar_volume_contract_version
        ),
        "source_phase_processing_profile_version": (
            profile.source_phase_processing_profile_version
        ),
        "normalized_uri": source_normalized_uri,
        "source_manifest_sha256": source_manifest_sha256,
        "kdp_input_unit": kdp_input_unit,
        "coefficient_source": profile.coefficients.source,
    }
    if coefficient_table_version is not None:
        source_input["coefficient_table_version"] = coefficient_table_version
    return {
        "schema_version": "1.0",
        "artifact_contract_version": profile.artifact_contract_version,
        "artifact_id": artifact_id,
        "profile_version": profile.profile_version,
        "created_at_utc": _format_utc(created_at_utc),
        "radar_id": radar_id,
        "scan_id": scan_id,
        "radar_band": profile.radar_band,
        "attenuation_method": profile.method_name,
        "integration_scheme": profile.integration_scheme,
        "operational_eligible": False,
        "worker_integration_enabled": profile.worker_integration_enabled,
        "source_input": source_input,
        "sweeps": sweeps,
    }


def _optional_float(value: object) -> float | None:
    if value is None:
        return None
    return float(value)


def _validate_profile(profile: AttenuationProfile) -> None:
    if not profile.profile_version:
        raise AttenuationInputError("attenuation profile_version must be non-empty")
    if not profile.source_phase_processing_profile_version:
        raise AttenuationInputError("source_phase_processing_profile_version must be non-empty")
    if profile.artifact_contract_version != "1.0":
        raise AttenuationInputError("unsupported attenuation artifact contract version")
    if profile.source_normalized_radar_volume_contract_version != "1.0":
        raise AttenuationInputError("unsupported normalized radar-volume contract version")
    if profile.method_name != SUPPORTED_ATTENUATION_METHOD:
        raise AttenuationInputError("unsupported attenuation method")
    if profile.integration_scheme != SUPPORTED_INTEGRATION_SCHEME:
        raise AttenuationInputError("unsupported attenuation integration scheme")
    if profile.radar_band != "S":
        raise AttenuationInputError("current attenuation shadow module is frozen for S band")
    if profile.coefficients.source not in SUPPORTED_COEFFICIENT_SOURCES:
        raise AttenuationInputError("unsupported attenuation coefficient source")
    if not profile.required_gate:
        raise AttenuationInputError("required_gate must be non-empty")
    if profile.worker_integration_enabled:
        raise AttenuationInputError("attenuation worker integration must remain disabled")
    if profile.applicability.unavailable_segment_policy != SUPPORTED_UNAVAILABLE_SEGMENT_POLICY:
        raise AttenuationInputError("unsupported unavailable segment policy")
    if not 0.0 <= profile.applicability.maximum_blockage_fraction <= 1.0:
        raise AttenuationInputError("maximum_blockage_fraction must be between zero and one")
    if profile.applicability.corrected_reflectivity_cap_dbz <= 0.0:
        raise AttenuationInputError("corrected_reflectivity_cap_dbz must be positive")
    if profile.coefficients.minimum_kdp_deg_per_km < 0.0:
        raise AttenuationInputError("minimum_kdp_deg_per_km must be non-negative")
    if profile.coefficients.maximum_specific_attenuation_db_per_km <= 0.0:
        raise AttenuationInputError("maximum_specific_attenuation_db_per_km must be positive")
    if profile.coefficients.maximum_correction_db <= 0.0:
        raise AttenuationInputError("maximum_correction_db must be positive")
    if profile.coefficients.source == "unconfigured":
        if (
            profile.coefficients.coefficient_a is not None
            or profile.coefficients.exponent_b is not None
        ):
            raise AttenuationInputError("unconfigured attenuation coefficients must remain null")
        return
    if profile.coefficients.coefficient_a is None or profile.coefficients.exponent_b is None:
        raise AttenuationInputError("configured attenuation coefficients must be present")
    if profile.coefficients.coefficient_a <= 0.0 or profile.coefficients.exponent_b <= 0.0:
        raise AttenuationInputError("configured attenuation coefficients must be positive")


def _validate_kdp_input_unit(value: str) -> None:
    if value != SUPPORTED_KDP_INPUT_UNIT:
        raise AttenuationInputError("input_kdp_unit must be degree/km")


def _normalize_range_km(range_m: np.ndarray) -> np.ndarray:
    values = np.asarray(range_m, dtype="float64")
    if values.ndim != 1 or values.size == 0:
        raise AttenuationInputError("range_m must be a non-empty one-dimensional array")
    if not np.isfinite(values).all():
        raise AttenuationInputError("range_m must be finite")
    if not np.all(np.diff(values) > 0.0):
        raise AttenuationInputError("range_m must be strictly increasing")
    return values / 1000.0


def _normalize_kdp_available_mask(
    available_mask: np.ndarray | None,
    expected_shape: tuple[int, ...],
) -> np.ndarray:
    if available_mask is None:
        return np.ones(expected_shape, dtype=bool)
    values = np.asarray(available_mask, dtype=bool)
    if values.shape != expected_shape:
        raise AttenuationInputError("kdp_available_mask shape must match KDP shape")
    return values


def _normalize_blockage_fraction(
    blockage_fraction: np.ndarray | None,
    expected_shape: tuple[int, ...],
    profile: AttenuationProfile,
) -> np.ndarray:
    if blockage_fraction is None:
        return np.zeros(expected_shape, dtype=bool)
    values = np.asarray(blockage_fraction, dtype="float32")
    if values.shape != expected_shape:
        raise AttenuationInputError("blockage_fraction shape must match DBZH shape")
    usable = np.isfinite(values)
    usable &= values >= 0.0
    usable &= values <= profile.applicability.maximum_blockage_fraction
    return usable


def _normalize_optional_2d_mask(
    values: np.ndarray | None,
    expected_shape: tuple[int, int],
) -> np.ndarray | None:
    if values is None:
        return None
    mask = np.asarray(values, dtype=bool)
    if mask.shape != expected_shape:
        raise AttenuationInputError("optional 2D mask shape must match sweep geometry")
    return mask


def _normalize_optional_2d_values(
    values: np.ndarray | None,
    expected_shape: tuple[int, int],
) -> np.ndarray | None:
    if values is None:
        return None
    normalized = np.asarray(values, dtype="float32")
    if normalized.shape != expected_shape:
        raise AttenuationInputError("optional 2D values shape must match sweep geometry")
    return normalized


def _empty_ray_result(
    raw_dbzh: np.ndarray,
    raw_kdp: np.ndarray,
    *,
    skip_reason: str,
) -> AttenuationRayResult:
    shape = raw_dbzh.shape
    return AttenuationRayResult(
        raw_dbzh_dbz=raw_dbzh.astype("float32", copy=True),
        kdp_deg_per_km=raw_kdp.astype("float32", copy=True),
        specific_attenuation_db_per_km=np.full(shape, np.nan, dtype="float32"),
        attenuation_correction_db=np.full(shape, np.nan, dtype="float32"),
        corrected_dbzh_dbz=np.full(shape, np.nan, dtype="float32"),
        available_mask=np.zeros(shape, dtype="uint8"),
        segment_index=np.full(shape, -1, dtype="int32"),
        diagnostics={
            "segment_count": 0,
            "processed_segment_count": 0,
            "available_gate_count": 0,
            "capped_gate_count": 0,
            "skip_reasons": [skip_reason],
        },
    )


def _contiguous_segments(mask: np.ndarray) -> list[tuple[int, int]]:
    indices = np.flatnonzero(mask)
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


def _two_way_trapezoidal_correction(
    range_km: np.ndarray,
    specific_attenuation_db_per_km: np.ndarray,
) -> np.ndarray:
    correction = np.zeros(range_km.shape, dtype="float64")
    if range_km.size <= 1:
        return correction
    increments = (
        specific_attenuation_db_per_km[:-1] + specific_attenuation_db_per_km[1:]
    ) * np.diff(range_km)
    correction[1:] = np.cumsum(increments)
    return correction


def _artifact_sweep_summary(
    result: AttenuationRayResult | AttenuationSweepResult,
) -> dict[str, Any]:
    correction_values = result.attenuation_correction_db[
        np.isfinite(result.attenuation_correction_db)
    ]
    corrected_values = result.corrected_dbzh_dbz[np.isfinite(result.corrected_dbzh_dbz)]
    skip_reasons = Counter(str(item) for item in result.diagnostics.get("skip_reasons", []))
    summary = {
        "available_gate_count": int(np.count_nonzero(result.available_mask)),
        "segment_count": int(result.diagnostics.get("segment_count", 0)),
        "processed_segment_count": int(result.diagnostics.get("processed_segment_count", 0)),
        "capped_gate_count": int(result.diagnostics.get("capped_gate_count", 0)),
        "skip_reason_counts": dict(sorted(skip_reasons.items())),
    }
    summary["correction_summary_db"] = _summary_triplet(correction_values)
    summary["corrected_reflectivity_summary_dbz"] = _summary_triplet(corrected_values)
    return summary


def _summary_triplet(values: np.ndarray) -> dict[str, float | None]:
    if values.size == 0:
        return {"minimum": None, "median": None, "maximum": None}
    return {
        "minimum": float(np.min(values)),
        "median": float(np.median(values)),
        "maximum": float(np.max(values)),
    }


def _validate_sha256(value: str, field_name: str) -> None:
    if len(value) != 64 or any(character not in "0123456789abcdef" for character in value):
        raise AttenuationInputError(f"{field_name} must be a lowercase SHA-256")


def _format_utc(value: datetime) -> str:
    if value.tzinfo is None:
        value = value.replace(tzinfo=UTC)
    return value.astimezone(UTC).isoformat().replace("+00:00", "Z")
