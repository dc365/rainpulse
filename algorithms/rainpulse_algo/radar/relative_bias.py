from __future__ import annotations

from collections import defaultdict
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

import numpy as np
import yaml
import zarr
from pyproj import Geod

from .blockage import TerrainSampler, beam_centre_height_m, beam_radius_m
from .qc_geometry import (
    QC_GEOMETRY_BEAM_CONFIG,
    QC_GEOMETRY_BLOCKAGE_CONFIG,
    RadarBeamContext,
    _elevation_by_ray,
    _target_to_neighbour_matches,
    _trusted_support_blockage,
    beam_support_overlap_mask,
)


class RelativeBiasInputError(ValueError):
    """Raised when offline relative-bias configuration or inputs are invalid."""


@dataclass(frozen=True)
class RelativeBiasProfile:
    profile_version: str
    artifact_contract_version: str
    source_normalized_radar_volume_contract_version: str
    radar_band: str
    comparison_field_name: str
    minimum_dbzh: float
    maximum_time_offset_seconds: int
    maximum_blockage_fraction: float
    maximum_height_difference_m: float
    minimum_comparable_gate_count: int
    minimum_independent_processes: int
    bootstrap_samples: int
    random_seed: int
    worker_integration_enabled: bool
    truth_designation_enabled: bool
    required_gate: str


def load_relative_bias_profile(path: str | Path) -> RelativeBiasProfile:
    profile_path = Path(path)
    try:
        raw = yaml.safe_load(profile_path.read_text(encoding="utf-8"))
        thresholds = raw["thresholds"]
        activation = raw["activation"]
        profile = RelativeBiasProfile(
            profile_version=str(raw["profile_version"]),
            artifact_contract_version=str(raw["artifact_contract_version"]),
            source_normalized_radar_volume_contract_version=str(
                raw["source_normalized_radar_volume_contract_version"]
            ),
            radar_band=str(raw["radar_band"]),
            comparison_field_name=str(raw["comparison_field_name"]),
            minimum_dbzh=float(thresholds["minimum_dbzh"]),
            maximum_time_offset_seconds=int(thresholds["maximum_time_offset_seconds"]),
            maximum_blockage_fraction=float(thresholds["maximum_blockage_fraction"]),
            maximum_height_difference_m=float(thresholds["maximum_height_difference_m"]),
            minimum_comparable_gate_count=int(thresholds["minimum_comparable_gate_count"]),
            minimum_independent_processes=int(thresholds["minimum_independent_processes"]),
            bootstrap_samples=int(raw["bootstrap_samples"]),
            random_seed=int(raw["random_seed"]),
            worker_integration_enabled=bool(activation["worker_integration_enabled"]),
            truth_designation_enabled=bool(activation["truth_designation_enabled"]),
            required_gate=str(activation["required_gate"]),
        )
    except (KeyError, TypeError, ValueError) as error:
        raise RelativeBiasInputError(
            f"invalid relative-bias profile {profile_path}: {error}"
        ) from error
    _validate_profile(profile)
    return profile


def compare_scan_to_reference(
    current_root: zarr.Group,
    reference_root: zarr.Group,
    *,
    current_beam_context: RadarBeamContext | None,
    reference_beam_context: RadarBeamContext | None,
    terrain: TerrainSampler | None,
    profile: RelativeBiasProfile,
    valid_range_dbz: tuple[float, float] | None,
    hard_interference_by_sweep: dict[str, np.ndarray] | None = None,
) -> dict[str, Any]:
    _validate_profile(profile)
    if (
        terrain is None
        or current_beam_context is None
        or reference_beam_context is None
        or current_beam_context.altitude_datum_status != "verified_egm2008"
        or reference_beam_context.altitude_datum_status != "verified_egm2008"
    ):
        return _skipped_comparison("geometry_resources_unavailable")

    if profile.comparison_field_name not in reference_root["sweep_000"]:
        return _skipped_comparison("reference_field_unavailable")

    bias_segments: list[np.ndarray] = []
    current_segments: list[np.ndarray] = []
    reference_segments: list[np.ndarray] = []
    sweep_summaries: list[dict[str, Any]] = []

    for sweep_number in current_root["sweep_number"][:]:
        current_sweep_name = f"sweep_{int(sweep_number):03d}"
        if current_sweep_name not in current_root:
            continue
        current_group = current_root[current_sweep_name]
        if profile.comparison_field_name not in current_group:
            continue
        reference_group = _nearest_reference_sweep(current_group, reference_root)
        if reference_group is None or profile.comparison_field_name not in reference_group:
            sweep_summaries.append(
                {
                    "current_sweep": current_sweep_name,
                    "status": "skipped",
                    "skip_reason": "reference_sweep_unavailable",
                    "comparable_gate_count": 0,
                }
            )
            continue
        sweep_result = _compare_sweep(
            current_group=current_group,
            current_beam_context=current_beam_context,
            reference_group=reference_group,
            reference_beam_context=reference_beam_context,
            terrain=terrain,
            profile=profile,
            valid_range_dbz=valid_range_dbz,
            hard_interference_by_sweep=hard_interference_by_sweep or {},
        )
        sweep_summaries.append(
            {
                "current_sweep": current_sweep_name,
                "reference_sweep": reference_group.name.split("/")[-1],
                **{
                    key: value
                    for key, value in sweep_result.items()
                    if key != "bias_values"
                    and key != "current_values"
                    and key != "reference_values"
                },
            }
        )
        if sweep_result["status"] != "computed":
            continue
        bias_segments.append(sweep_result["bias_values"])
        current_segments.append(sweep_result["current_values"])
        reference_segments.append(sweep_result["reference_values"])

    if not bias_segments:
        return _skipped_comparison(
            "insufficient_comparable_gates",
            sweeps=sweep_summaries,
        )

    bias_values = np.concatenate(bias_segments).astype("float32")
    current_values = np.concatenate(current_segments).astype("float32")
    reference_values = np.concatenate(reference_segments).astype("float32")
    if bias_values.size < profile.minimum_comparable_gate_count:
        return _skipped_comparison(
            "insufficient_comparable_gates",
            comparable_gate_count=int(bias_values.size),
            sweeps=sweep_summaries,
        )
    return {
        "status": "computed",
        "comparison_semantics": "primary_minus_reference_dbz",
        "comparable_gate_count": int(bias_values.size),
        "median_relative_bias_dbz": float(np.median(bias_values)),
        "relative_bias_summary_dbz": _summary_triplet(bias_values),
        "current_reflectivity_summary_dbz": _summary_triplet(current_values),
        "reference_reflectivity_summary_dbz": _summary_triplet(reference_values),
        "sweeps": sweep_summaries,
    }


def build_relative_bias_artifact(
    comparisons: list[dict[str, Any]],
    *,
    profile: RelativeBiasProfile,
    artifact_id: str,
    created_at_utc: datetime,
    radar_id: str,
    scan_id: str,
    source_normalized_uri: str,
    source_normalized_artifact_sha256: str,
    source_qc_profile_version: str,
    grouping_mode: str,
    group_id: str,
) -> dict[str, Any]:
    _validate_profile(profile)
    if not artifact_id or not radar_id or not scan_id:
        raise RelativeBiasInputError("relative-bias artifact identity fields must be non-empty")
    if not source_normalized_uri or not source_normalized_artifact_sha256:
        raise RelativeBiasInputError("relative-bias artifact source provenance is required")
    if grouping_mode not in {"process_id", "scan_id_fallback"}:
        raise RelativeBiasInputError("unsupported grouping_mode")
    return {
        "schema_version": "1.0",
        "artifact_contract_version": profile.artifact_contract_version,
        "artifact_id": artifact_id,
        "profile_version": profile.profile_version,
        "created_at_utc": _format_utc(created_at_utc),
        "radar_id": radar_id,
        "scan_id": scan_id,
        "radar_band": profile.radar_band,
        "comparison_field_name": profile.comparison_field_name,
        "operational_eligible": False,
        "worker_integration_enabled": profile.worker_integration_enabled,
        "truth_designation_enabled": profile.truth_designation_enabled,
        "grouping": {
            "grouping_mode": grouping_mode,
            "group_id": group_id,
        },
        "source_input": {
            "source_normalized_radar_volume_contract_version": (
                profile.source_normalized_radar_volume_contract_version
            ),
            "normalized_uri": source_normalized_uri,
            "normalized_artifact_sha256": source_normalized_artifact_sha256,
            "source_qc_profile_version": source_qc_profile_version,
        },
        "comparisons": comparisons,
    }


def aggregate_relative_bias_artifacts(
    artifacts: list[dict[str, Any]],
    *,
    profile: RelativeBiasProfile,
) -> list[dict[str, Any]]:
    _validate_profile(profile)
    grouped: dict[tuple[str, str], dict[str, Any]] = {}
    for artifact in artifacts:
        primary_radar_id = str(artifact.get("radar_id", ""))
        grouping = artifact.get("grouping", {})
        group_id = str(grouping.get("group_id", "")).strip()
        grouping_mode = str(grouping.get("grouping_mode", "scan_id_fallback")).strip()
        if not primary_radar_id or not group_id:
            continue
        for comparison in artifact.get("comparisons", []):
            if comparison.get("status") != "computed":
                continue
            reference_radar_id = str(comparison.get("reference_radar_id", "")).strip()
            if not reference_radar_id:
                continue
            key = (primary_radar_id, reference_radar_id)
            entry = grouped.setdefault(
                key,
                {
                    "group_values": defaultdict(list),
                    "grouping_modes": set(),
                    "comparable_gate_count": 0,
                    "comparable_scan_count": 0,
                },
            )
            entry["group_values"][group_id].append(float(comparison["median_relative_bias_dbz"]))
            entry["grouping_modes"].add(grouping_mode)
            entry["comparable_gate_count"] += int(comparison["comparable_gate_count"])
            entry["comparable_scan_count"] += 1

    aggregates: list[dict[str, Any]] = []
    for index, key in enumerate(sorted(grouped)):
        primary_radar_id, reference_radar_id = key
        entry = grouped[key]
        group_values = {
            group_id: float(np.median(np.asarray(values, dtype="float32")))
            for group_id, values in sorted(entry["group_values"].items())
        }
        values = np.asarray(list(group_values.values()), dtype="float32")
        if values.size == 0:
            continue
        grouping_modes = entry["grouping_modes"]
        if grouping_modes == {"process_id"}:
            grouping_mode = "process_id"
        elif grouping_modes == {"scan_id_fallback"}:
            grouping_mode = "scan_id_fallback"
        else:
            grouping_mode = "mixed"
        if grouping_mode != "process_id":
            status = "engineering_only"
        elif len(group_values) < profile.minimum_independent_processes:
            status = "insufficient_process_count"
        else:
            status = "computed"
        interval = _bootstrap_interval(
            values,
            sample_count=profile.bootstrap_samples,
            seed=profile.random_seed + index,
        )
        aggregates.append(
            {
                "primary_radar_id": primary_radar_id,
                "reference_radar_id": reference_radar_id,
                "comparison_semantics": "primary_minus_reference_dbz",
                "grouping_mode": grouping_mode,
                "status": status,
                "truth_designation_enabled": profile.truth_designation_enabled,
                "independent_group_count": len(group_values),
                "comparable_scan_count": int(entry["comparable_scan_count"]),
                "comparable_gate_count": int(entry["comparable_gate_count"]),
                "median_relative_bias_dbz": float(np.median(values)),
                "relative_bias_interval_dbz": interval,
                "group_medians_dbz": group_values,
            }
        )
    return aggregates


def _compare_sweep(
    *,
    current_group: zarr.Group,
    current_beam_context: RadarBeamContext,
    reference_group: zarr.Group,
    reference_beam_context: RadarBeamContext,
    terrain: TerrainSampler,
    profile: RelativeBiasProfile,
    valid_range_dbz: tuple[float, float] | None,
    hard_interference_by_sweep: dict[str, np.ndarray],
) -> dict[str, Any]:
    current_values = np.asarray(current_group[profile.comparison_field_name][:], dtype="float32")
    reference_values = np.asarray(
        reference_group[profile.comparison_field_name][:],
        dtype="float32",
    )
    if current_values.ndim != 2 or reference_values.ndim != 2:
        return {
            "status": "skipped",
            "skip_reason": "invalid_field_dimensions",
            "comparable_gate_count": 0,
        }

    shape = current_values.shape
    current_azimuth = np.asarray(current_group["azimuth"][:], dtype="float64")
    current_range = np.asarray(current_group["range"][:], dtype="float64")
    current_elevation = _elevation_by_ray(current_group["elevation"][:], shape[0])
    azimuth_grid = np.broadcast_to(current_azimuth[:, None], shape)
    range_grid = np.broadcast_to(current_range[None, :], shape)
    geod = Geod(ellps="WGS84")
    longitude, latitude, _ = geod.fwd(
        np.full(azimuth_grid.size, current_beam_context.longitude_deg),
        np.full(azimuth_grid.size, current_beam_context.latitude_deg),
        azimuth_grid.ravel(),
        range_grid.ravel(),
    )
    try:
        nearest_rays, nearest_gates, supported = _target_to_neighbour_matches(
            np.asarray(longitude, dtype="float64").reshape(shape),
            np.asarray(latitude, dtype="float64").reshape(shape),
            reference_beam_context,
            reference_group,
        )
    except ValueError:
        return {
            "status": "skipped",
            "skip_reason": "geometry_projection_failed",
            "comparable_gate_count": 0,
        }
    if not np.any(supported):
        return {
            "status": "skipped",
            "skip_reason": "geometry_projection_failed",
            "comparable_gate_count": 0,
        }

    if valid_range_dbz is None:
        current_valid = np.isfinite(current_values)
        reference_valid = np.isfinite(reference_values)
    else:
        lower, upper = map(float, valid_range_dbz)
        current_valid = (
            np.isfinite(current_values) & (current_values >= lower) & (current_values <= upper)
        )
        reference_valid = (
            np.isfinite(reference_values)
            & (reference_values >= lower)
            & (reference_values <= upper)
        )
    projected_reference = reference_values[nearest_rays, nearest_gates]
    comparable = supported.copy()
    comparable &= current_valid
    comparable &= current_values >= profile.minimum_dbzh
    comparable &= reference_valid[nearest_rays, nearest_gates]
    comparable &= projected_reference >= profile.minimum_dbzh
    if not np.any(comparable):
        return {
            "status": "skipped",
            "skip_reason": "insufficient_comparable_gates",
            "comparable_gate_count": 0,
        }

    blockage = _trusted_support_blockage(
        reference_beam_context,
        reference_group,
        terrain,
        QC_GEOMETRY_BEAM_CONFIG,
        QC_GEOMETRY_BLOCKAGE_CONFIG,
        nearest_rays,
        nearest_gates,
        supported,
    )
    blockage_fraction = blockage.cumulative[nearest_rays, nearest_gates]
    comparable &= blockage.support_mask[nearest_rays, nearest_gates] == 1
    comparable &= np.isfinite(blockage_fraction)
    comparable &= blockage_fraction <= profile.maximum_blockage_fraction
    hard_rays = np.asarray(
        hard_interference_by_sweep.get(
            reference_group.name.split("/")[-1],
            np.zeros(reference_values.shape[0], dtype=bool),
        ),
        dtype=bool,
    )
    if hard_rays.shape != (reference_values.shape[0],):
        hard_rays = np.zeros(reference_values.shape[0], dtype=bool)
    comparable &= ~hard_rays[nearest_rays]

    current_height = beam_centre_height_m(
        range_grid,
        np.broadcast_to(current_elevation[:, None], shape),
        current_beam_context.antenna_altitude_m,
        QC_GEOMETRY_BEAM_CONFIG,
    )
    current_radius = beam_radius_m(range_grid, current_beam_context.beam_width_vertical_deg)
    reference_range = np.asarray(reference_group["range"][:], dtype="float64")
    reference_radius = beam_radius_m(
        reference_range[nearest_gates],
        reference_beam_context.beam_width_vertical_deg,
    )
    overlap, height_difference = beam_support_overlap_mask(
        current_height,
        current_radius,
        blockage.beam_height_m[nearest_rays, nearest_gates],
        reference_radius,
        maximum_height_difference_m=profile.maximum_height_difference_m,
    )
    comparable &= overlap
    if not np.any(comparable):
        return {
            "status": "skipped",
            "skip_reason": "insufficient_comparable_gates",
            "comparable_gate_count": 0,
        }

    bias_values = (current_values - projected_reference)[comparable].astype("float32")
    current_sample = current_values[comparable].astype("float32")
    reference_sample = projected_reference[comparable].astype("float32")
    height_values = height_difference[comparable].astype("float32")
    blockage_values = blockage_fraction[comparable].astype("float32")
    return {
        "status": "computed",
        "comparable_gate_count": int(bias_values.size),
        "height_difference_summary_m": _summary_triplet(height_values),
        "blockage_fraction_summary": _summary_triplet(blockage_values),
        "bias_values": bias_values,
        "current_values": current_sample,
        "reference_values": reference_sample,
    }


def _nearest_reference_sweep(
    current_group: zarr.Group,
    reference_root: zarr.Group,
) -> zarr.Group | None:
    target = float(np.nanmedian(np.asarray(current_group["elevation"][:], dtype="float64")))
    candidates = [
        reference_root[f"sweep_{int(number):03d}"]
        for number in reference_root["sweep_number"][:]
        if f"sweep_{int(number):03d}" in reference_root
    ]
    candidates = [
        item for item in candidates if "elevation" in item and "range" in item and "azimuth" in item
    ]
    if not candidates:
        return None
    return min(
        candidates,
        key=lambda item: abs(
            float(np.nanmedian(np.asarray(item["elevation"][:], dtype="float64"))) - target
        ),
    )


def _bootstrap_interval(
    values: np.ndarray,
    *,
    sample_count: int,
    seed: int,
) -> dict[str, float | None]:
    sample = np.asarray(values, dtype="float64")
    if sample.size == 0:
        return {"lower": None, "upper": None}
    if sample.size == 1:
        value = float(sample[0])
        return {"lower": value, "upper": value}
    rng = np.random.default_rng(seed)
    draws = rng.choice(sample, size=(sample_count, sample.size), replace=True)
    medians = np.median(draws, axis=1)
    return {
        "lower": float(np.quantile(medians, 0.025)),
        "upper": float(np.quantile(medians, 0.975)),
    }


def _skipped_comparison(
    skip_reason: str,
    *,
    comparable_gate_count: int = 0,
    sweeps: list[dict[str, Any]] | None = None,
) -> dict[str, Any]:
    return {
        "status": "skipped",
        "skip_reason": skip_reason,
        "comparable_gate_count": int(comparable_gate_count),
        "sweeps": [] if sweeps is None else sweeps,
    }


def _summary_triplet(values: np.ndarray) -> dict[str, float | None]:
    sample = np.asarray(values, dtype="float64")
    finite = sample[np.isfinite(sample)]
    if finite.size == 0:
        return {"minimum": None, "median": None, "maximum": None}
    return {
        "minimum": float(np.min(finite)),
        "median": float(np.median(finite)),
        "maximum": float(np.max(finite)),
    }


def _validate_profile(profile: RelativeBiasProfile) -> None:
    if not profile.profile_version:
        raise RelativeBiasInputError("relative-bias profile_version must be non-empty")
    if profile.artifact_contract_version != "1.0":
        raise RelativeBiasInputError("unsupported relative-bias artifact contract version")
    if profile.source_normalized_radar_volume_contract_version != "1.0":
        raise RelativeBiasInputError("unsupported normalized radar-volume contract version")
    if profile.radar_band != "S":
        raise RelativeBiasInputError("relative-bias shadow profile is frozen for S band")
    if profile.comparison_field_name != "DBZH":
        raise RelativeBiasInputError("relative-bias comparison field must remain DBZH")
    if profile.minimum_dbzh < 0.0:
        raise RelativeBiasInputError("minimum_dbzh must be non-negative")
    if not 0.0 <= profile.maximum_blockage_fraction <= 1.0:
        raise RelativeBiasInputError("maximum_blockage_fraction must be between zero and one")
    if profile.maximum_height_difference_m <= 0.0:
        raise RelativeBiasInputError("maximum_height_difference_m must be positive")
    if profile.minimum_comparable_gate_count <= 0:
        raise RelativeBiasInputError("minimum_comparable_gate_count must be positive")
    if profile.minimum_independent_processes <= 0:
        raise RelativeBiasInputError("minimum_independent_processes must be positive")
    if profile.maximum_time_offset_seconds <= 0:
        raise RelativeBiasInputError("maximum_time_offset_seconds must be positive")
    if profile.bootstrap_samples <= 0:
        raise RelativeBiasInputError("bootstrap_samples must be positive")
    if profile.worker_integration_enabled:
        raise RelativeBiasInputError("relative-bias worker integration must remain disabled")
    if profile.truth_designation_enabled:
        raise RelativeBiasInputError("relative-bias truth designation must remain disabled")
    if not profile.required_gate:
        raise RelativeBiasInputError("required_gate must be non-empty")


def _format_utc(value: datetime) -> str:
    if value.tzinfo is None:
        value = value.replace(tzinfo=UTC)
    return value.astimezone(UTC).isoformat().replace("+00:00", "Z")
