from __future__ import annotations

import json
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import numpy as np
import yaml

from .config import RadarDecoderConfig
from .fmt import DecodedRadarVolume


class RadarHealthConfigError(ValueError):
    """Raised when an RP-007 integrity profile is incomplete."""


@dataclass(frozen=True)
class RadarHealthConfig:
    path: Path
    profile_version: str
    minimum_scan_completeness: float
    unavailable_below_completeness: float
    maximum_azimuth_gap_deg: float
    maximum_out_of_range_ratio: float
    degrade_when_noise_telemetry_missing: bool
    noise_dbm_range: tuple[float, float]
    field_hard_limits: dict[str, tuple[float, float]]


@dataclass(frozen=True)
class RadarHealthSummary:
    value: dict[str, Any]

    @property
    def metrics(self) -> dict[str, float]:
        return {
            "scan_completeness": float(self.value["scan_completeness"]),
            "expected_sweep_count": float(self.value["expected_sweep_count"]),
            "actual_sweep_count": float(self.value["actual_sweep_count"]),
            "missing_sweep_count": float(len(self.value["missing_sweep_numbers"])),
            "expected_radial_count": float(self.value["expected_radial_count"]),
            "actual_radial_count": float(self.value["actual_radial_count"]),
            "missing_radial_count": float(self.value["missing_radial_count"]),
            "maximum_azimuth_gap_deg": float(self.value["maximum_azimuth_gap_deg"]),
            "field_availability_ratio": float(self.value["field_availability_ratio"]),
            "noise_sample_count": float(self.value["noise_level"]["sample_count"]),
            "out_of_range_gate_count": float(self.value["out_of_range_gate_count"]),
            "anomaly_count": float(self.value["anomaly_count"]),
            "radar_health_code": float(
                {"HEALTHY": 0, "DEGRADED": 1, "UNAVAILABLE": 2}[self.value["health"]]
            ),
        }

    def json_bytes(self) -> bytes:
        return json.dumps(self.value, separators=(",", ":"), sort_keys=True).encode()


def load_radar_health_config(path: str | Path) -> RadarHealthConfig:
    config_path = Path(path)
    value = yaml.safe_load(config_path.read_text())
    if not isinstance(value, dict) or value.get("schema_version") != "1.0":
        raise RadarHealthConfigError("unsupported radar health configuration")
    thresholds = value.get("thresholds")
    limits = value.get("field_hard_limits")
    if not isinstance(thresholds, dict) or not isinstance(limits, dict):
        raise RadarHealthConfigError("radar health thresholds and field limits are required")
    required = {
        "minimum_scan_completeness",
        "unavailable_below_completeness",
        "maximum_azimuth_gap_deg",
        "maximum_out_of_range_ratio",
        "degrade_when_noise_telemetry_missing",
        "noise_dbm_range",
    }
    missing = sorted(required - thresholds.keys())
    if missing:
        raise RadarHealthConfigError(f"radar health thresholds are missing {', '.join(missing)}")

    minimum = float(thresholds["minimum_scan_completeness"])
    unavailable = float(thresholds["unavailable_below_completeness"])
    maximum_gap = float(thresholds["maximum_azimuth_gap_deg"])
    maximum_outlier = float(thresholds["maximum_out_of_range_ratio"])
    if not 0 <= unavailable < minimum <= 1:
        raise RadarHealthConfigError("radar health completeness thresholds are inconsistent")
    if not 0 < maximum_gap <= 180 or not 0 <= maximum_outlier <= 1:
        raise RadarHealthConfigError("radar health anomaly thresholds are invalid")

    noise_range = _range(thresholds["noise_dbm_range"], "noise_dbm_range")
    field_limits = {str(name): _range(item, str(name)) for name, item in limits.items()}
    return RadarHealthConfig(
        path=config_path,
        profile_version=str(value["profile_version"]),
        minimum_scan_completeness=minimum,
        unavailable_below_completeness=unavailable,
        maximum_azimuth_gap_deg=maximum_gap,
        maximum_out_of_range_ratio=maximum_outlier,
        degrade_when_noise_telemetry_missing=bool(
            thresholds["degrade_when_noise_telemetry_missing"]
        ),
        noise_dbm_range=noise_range,
        field_hard_limits=field_limits,
    )


def assess_volume_health(
    volume: DecodedRadarVolume,
    radar_config: RadarDecoderConfig,
    health_config: RadarHealthConfig,
) -> RadarHealthSummary:
    expected_cuts = radar_config.scan.get("expected_cut_elevations_deg") or []
    expected_sweeps = len(expected_cuts)
    actual_by_number = {sweep.source_sweep_number: sweep for sweep in volume.sweeps}
    missing_sweeps = [
        number for number in range(1, expected_sweeps + 1) if number not in actual_by_number
    ]
    expected_rays_per_sweep = max(
        1, round(360.0 / float(radar_config.scan["azimuth_resolution_deg"]))
    )
    expected_radials = expected_rays_per_sweep * expected_sweeps
    actual_radials = volume.ray_count
    missing_radials = len(missing_sweeps) * expected_rays_per_sweep
    azimuth_coverages: list[float] = []
    sweep_diagnostics: list[dict[str, Any]] = []

    for number in range(1, expected_sweeps + 1):
        sweep = actual_by_number.get(number)
        if sweep is None:
            azimuth_coverages.append(0.0)
            sweep_diagnostics.append(
                {
                    "sweep_number": number,
                    "nominal_elevation_deg": expected_cuts[number - 1],
                    "ray_count": 0,
                    "missing_radial_count": expected_rays_per_sweep,
                    "maximum_azimuth_gap_deg": 360.0,
                    "azimuth_coverage_ratio": 0.0,
                }
            )
            continue
        gap = _maximum_azimuth_gap(sweep.azimuth_deg)
        coverage = min(1.0, sweep.ray_count / expected_rays_per_sweep)
        missing = max(0, expected_rays_per_sweep - sweep.ray_count)
        missing_radials += missing
        azimuth_coverages.append(coverage)
        sweep_diagnostics.append(
            {
                "sweep_number": number,
                "nominal_elevation_deg": sweep.nominal_elevation_deg,
                "ray_count": sweep.ray_count,
                "missing_radial_count": missing,
                "maximum_azimuth_gap_deg": round(gap, 6),
                "azimuth_coverage_ratio": round(coverage, 6),
            }
        )

    sweep_ratio = min(1.0, len(volume.sweeps) / expected_sweeps) if expected_sweeps else 0.0
    radial_ratio = min(1.0, actual_radials / expected_radials) if expected_radials else 0.0
    azimuth_ratio = float(np.mean(azimuth_coverages)) if azimuth_coverages else 0.0
    scan_completeness = min(sweep_ratio, radial_ratio, azimuth_ratio)
    maximum_gap = max(
        (item["maximum_azimuth_gap_deg"] for item in sweep_diagnostics), default=360.0
    )

    field_availability: list[dict[str, Any]] = []
    layer_anomalies: list[dict[str, Any]] = []
    out_of_range_count = 0
    finite_gate_count = 0
    for mapping in radar_config.fields:
        arrays = [
            (sweep.source_sweep_number, sweep.fields[mapping.canonical_name])
            for sweep in volume.sweeps
            if mapping.canonical_name in sweep.fields
        ]
        field_finite = 0
        field_total = 0
        field_out_of_range = 0
        hard_limit = health_config.field_hard_limits.get(mapping.canonical_name)
        for sweep_number, values in arrays:
            finite = np.isfinite(values)
            count = int(finite.sum())
            field_finite += count
            field_total += int(values.size)
            if count == 0:
                layer_anomalies.append(
                    {
                        "code": "ALL_MISSING_LAYER",
                        "field": mapping.canonical_name,
                        "sweep_number": sweep_number,
                    }
                )
                continue
            finite_values = values[finite]
            if float(np.nanmax(finite_values) - np.nanmin(finite_values)) <= 1e-6:
                layer_anomalies.append(
                    {
                        "code": "CONSTANT_LAYER",
                        "field": mapping.canonical_name,
                        "sweep_number": sweep_number,
                    }
                )
            if hard_limit is not None:
                lower, upper = hard_limit
                field_out_of_range += int(
                    ((finite_values < lower) | (finite_values > upper)).sum()
                )
        out_of_range_count += field_out_of_range
        finite_gate_count += field_finite
        field_availability.append(
            {
                "field": mapping.canonical_name,
                "available": bool(arrays),
                "present_sweep_count": len(arrays),
                "finite_gate_ratio": round(field_finite / field_total, 6) if field_total else 0.0,
                "out_of_range_gate_count": field_out_of_range,
                "unit": mapping.canonical_unit,
            }
        )

    available_fields = sum(item["available"] for item in field_availability)
    field_ratio = available_fields / len(field_availability) if field_availability else 0.0
    outlier_ratio = out_of_range_count / finite_gate_count if finite_gate_count else 0.0
    horizontal_noise = _finite_noise(volume, "horizontal_noise_dbm")
    vertical_noise = _finite_noise(volume, "vertical_noise_dbm")
    all_noise = np.concatenate([item for item in (horizontal_noise, vertical_noise) if item.size]) \
        if horizontal_noise.size or vertical_noise.size else np.asarray([], dtype="float32")
    if all_noise.size == 0:
        channel_status = "UNKNOWN"
    else:
        lower, upper = health_config.noise_dbm_range
        channel_status = "OK" if np.all((all_noise >= lower) & (all_noise <= upper)) else "DEGRADED"

    reasons: list[str] = []
    dbzh_available = any(
        item["field"] == "DBZH" and item["available"] for item in field_availability
    )
    if not dbzh_available:
        reasons.append("DBZH_UNAVAILABLE")
    if scan_completeness < health_config.minimum_scan_completeness:
        reasons.append("SCAN_INCOMPLETE")
    allowed_gap = max(
        health_config.maximum_azimuth_gap_deg,
        float(radar_config.scan["azimuth_resolution_deg"]) * 1.5,
    )
    if maximum_gap > allowed_gap:
        reasons.append("AZIMUTH_GAP")
    if field_ratio < 1:
        reasons.append("FIELD_UNAVAILABLE")
    if outlier_ratio > health_config.maximum_out_of_range_ratio or layer_anomalies:
        reasons.append("ANOMALOUS_VALUES")
    if channel_status == "DEGRADED":
        reasons.append("NOISE_OUT_OF_RANGE")
    elif channel_status == "UNKNOWN" and health_config.degrade_when_noise_telemetry_missing:
        reasons.append("NOISE_TELEMETRY_MISSING")
    if radar_config.lifecycle != "ready":
        reasons.append("CONFIG_NOT_READY")
    if volume.warnings:
        reasons.append("SOURCE_TIME_MISMATCH")

    if not dbzh_available or scan_completeness < health_config.unavailable_below_completeness:
        health = "UNAVAILABLE"
    elif reasons:
        health = "DEGRADED"
    else:
        health = "HEALTHY"

    summary = {
        "schema_version": "1.0",
        "health_profile_version": health_config.profile_version,
        "radar_id": radar_config.radar_id,
        "radar_config_version": radar_config.config_version,
        "config_lifecycle": radar_config.lifecycle,
        "health": health,
        "health_reasons": sorted(set(reasons)),
        "volume_start_time_utc": volume.volume_start_time.isoformat(),
        "volume_end_time_utc": volume.volume_end_time.isoformat(),
        "scan_completeness": round(scan_completeness, 6),
        "expected_sweep_count": expected_sweeps,
        "actual_sweep_count": len(volume.sweeps),
        "missing_sweep_numbers": missing_sweeps,
        "expected_radial_count": expected_radials,
        "actual_radial_count": actual_radials,
        "missing_radial_count": missing_radials,
        "maximum_azimuth_gap_deg": round(float(maximum_gap), 6),
        "sweeps": sweep_diagnostics,
        "field_availability_ratio": round(field_ratio, 6),
        "field_availability": field_availability,
        "noise_level": {
            "source": "RSTM_RADIAL_HEADER",
            "horizontal_dbm": _median_or_none(horizontal_noise),
            "vertical_dbm": _median_or_none(vertical_noise),
            "sample_count": int(all_noise.size),
        },
        "channel_status": channel_status,
        "out_of_range_gate_count": out_of_range_count,
        "out_of_range_gate_ratio": round(outlier_ratio, 9),
        "layer_anomalies": layer_anomalies,
        "anomaly_count": out_of_range_count + len(layer_anomalies),
        "warnings": list(volume.warnings),
    }
    return RadarHealthSummary(summary)


def _range(value: Any, label: str) -> tuple[float, float]:
    if not isinstance(value, list) or len(value) != 2:
        raise RadarHealthConfigError(f"{label} must contain a lower and upper bound")
    lower, upper = map(float, value)
    if not lower < upper:
        raise RadarHealthConfigError(f"{label} lower bound must be below upper bound")
    return lower, upper


def _maximum_azimuth_gap(azimuth: np.ndarray) -> float:
    if azimuth.size < 2:
        return 360.0
    ordered = np.sort(np.unique(azimuth.astype("float64") % 360.0))
    if ordered.size < 2:
        return 360.0
    gaps = np.diff(np.concatenate([ordered, ordered[:1] + 360.0]))
    return float(gaps.max())


def _finite_noise(volume: DecodedRadarVolume, attribute: str) -> np.ndarray:
    arrays = [getattr(sweep, attribute) for sweep in volume.sweeps]
    if not arrays:
        return np.asarray([], dtype="float32")
    values = np.concatenate(arrays).astype("float32")
    return values[np.isfinite(values)]


def _median_or_none(values: np.ndarray) -> float | None:
    return round(float(np.median(values)), 6) if values.size else None
