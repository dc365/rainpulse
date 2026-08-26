from __future__ import annotations

from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Any

import numpy as np
import zarr
from zarr.storage import MemoryStore

from rainpulse_algo.grid import RegularLatLonGrid

from .grid_zarr import validate_radar_grid_zarr_store
from .mosaic_profile import RadarMosaicProfile


class RadarMosaicInputError(ValueError):
    """Raised when committed RadarGrid inputs cannot safely enter the mosaic."""


QI_COMPONENTS = (
    "QI_METEO",
    "QI_BLOCKAGE",
    "QI_BEAM_HEIGHT",
    "QI_ATTENUATION",
    "QI_INTERFERENCE",
    "QI_TIME",
    "QI_CALIBRATION",
    "QI_RANGE",
)

WEIGHTED_FIELDS = (
    "QUALITY_INDEX",
    *QI_COMPONENTS,
    "SOURCE_ELEVATION",
    "BEAM_HEIGHT",
    "TERRAIN_HEIGHT",
    "BLOCKAGE_RATE",
    "DATA_AGE",
)


@dataclass(frozen=True)
class RadarMosaicInput:
    radar_id: str
    scan_id: str
    grid_uri: str
    time_offset_seconds: int
    hybrid_scan_version: str
    objects: Mapping[str, bytes]


@dataclass(frozen=True)
class RadarMosaicResult:
    grid: RegularLatLonGrid
    profile: RadarMosaicProfile
    analysis_time: datetime
    fields: dict[str, np.ndarray]
    contributors: tuple[dict[str, Any], ...]
    radar_source_codes: dict[str, int]
    summary: dict[str, Any]
    operational_eligible: bool
    operational_reasons: tuple[str, ...]
    created_at: datetime


def build_radar_mosaic(
    inputs: Sequence[RadarMosaicInput],
    *,
    analysis_time: datetime,
    grid: RegularLatLonGrid,
    profile: RadarMosaicProfile,
    flag_masks: Mapping[str, np.uint32],
    created_at: datetime | None = None,
) -> RadarMosaicResult:
    analysis_time = _utc(analysis_time)
    _validate_analysis_identity(inputs, analysis_time, grid, profile, flag_masks)
    roots = [_open_and_validate(item, analysis_time, grid, profile) for item in inputs]
    codes = {radar_id: index + 1 for index, radar_id in enumerate(sorted({
        item.radar_id for item in inputs
    }))}
    shape = grid.shape
    count = len(inputs)

    dbzh = _stack(roots, "DBZH_QC")
    source_quality = _stack(roots, "QUALITY_INDEX")
    source_flags = _stack(roots, "QC_FLAGS")
    source_valid = _stack(roots, "VALID_MASK") == 1
    time_quality = np.asarray(
        [
            max(
                0.0,
                1.0
                - abs(item.time_offset_seconds)
                / profile.alignment.maximum_absolute_offset_seconds,
            )
            for item in inputs
        ],
        dtype="float32",
    )[:, None, None]
    adjusted_quality = source_quality * time_quality
    reject_mask = np.uint32(0)
    for name in profile.fusion.reject_flags:
        reject_mask |= np.uint32(flag_masks[name])
    usable = (
        source_valid
        & np.isfinite(dbzh)
        & np.isfinite(adjusted_quality)
        & (adjusted_quality >= profile.fusion.minimum_quality_index)
        & ((source_flags & reject_mask) == 0)
    )
    best_quality = np.max(np.where(usable, adjusted_quality, -np.inf), axis=0)
    contributes = usable & (
        adjusted_quality
        >= best_quality[None, :, :] - profile.fusion.similar_quality_max_difference
    )
    contributor_count = np.sum(contributes, axis=0, dtype="uint8")
    valid = contributor_count > 0
    raw_weights = np.where(
        contributes,
        np.power(adjusted_quality, profile.fusion.quality_weight_power),
        0.0,
    ).astype("float32")
    weight_sum = raw_weights.sum(axis=0)
    weights = np.divide(
        raw_weights,
        weight_sum[None, :, :],
        out=np.zeros_like(raw_weights),
        where=weight_sum[None, :, :] > 0,
    )

    fields = _empty_fields(shape, flag_masks)
    linear_z = np.full(dbzh.shape, np.nan, dtype="float32")
    np.power(10.0, dbzh / 10.0, out=linear_z, where=np.isfinite(dbzh))
    blended_z = np.sum(np.where(contributes, linear_z * weights, 0.0), axis=0)
    mosaic_dbzh = np.full(shape, np.nan, dtype="float32")
    mosaic_dbzh[valid] = (10.0 * np.log10(blended_z[valid])).astype("float32")
    fields["DBZH_QC"] = mosaic_dbzh
    fields["REF_NOWCAST"] = mosaic_dbzh.copy()

    weighted_sources = {
        "QUALITY_INDEX": adjusted_quality,
        "QI_TIME": np.broadcast_to(time_quality, (count, *shape)),
        **{
            name: _stack(roots, name)
            for name in QI_COMPONENTS
            if name != "QI_TIME"
        },
        "SOURCE_ELEVATION": _stack(roots, "SOURCE_ELEVATION"),
        "BEAM_HEIGHT": _stack(roots, "BEAM_HEIGHT"),
        "TERRAIN_HEIGHT": _stack(roots, "TERRAIN_HEIGHT"),
        "BLOCKAGE_RATE": _stack(roots, "BLOCKAGE_RATE"),
        "DATA_AGE": _stack(roots, "DATA_AGE")
        + np.asarray(
            [abs(item.time_offset_seconds) / 60.0 for item in inputs],
            dtype="float32",
        )[:, None, None],
    }
    for name in WEIGHTED_FIELDS:
        fields[name] = _weighted_available(weighted_sources[name], weights, contributes)

    selected_flags = np.where(contributes, source_flags, np.uint32(0))
    fields["QC_FLAGS"] = np.bitwise_or.reduce(selected_flags, axis=0).astype("uint32")
    fields["QC_FLAGS"][~valid] = np.uint32(flag_masks["MISSING"])
    low = valid & (fields["QUALITY_INDEX"] < profile.fusion.low_quality_threshold)
    fields["QC_FLAGS"][low] |= np.uint32(flag_masks["LOW_QUALITY"])
    fields["CONTRIBUTOR_COUNT"] = contributor_count
    source_codes = np.zeros(shape, dtype="uint16")
    single = contributor_count == 1
    for index, item in enumerate(inputs):
        source_codes[single & contributes[index]] = np.uint16(codes[item.radar_id])
    source_codes[contributor_count > 1] = np.uint16(profile.fusion.blended_source_code)
    fields["SOURCE_RADAR"] = source_codes
    fields["VALID_MASK"] = valid.astype("uint8")
    fields["LOW_QUALITY_MASK"] = low.astype("uint8")

    contributor_details = _contributor_details(
        inputs, roots, contributes, adjusted_quality
    )
    actual_radars = {
        item.radar_id
        for index, item in enumerate(inputs)
        if np.any(contributes[index])
    }
    operational_reasons = _operational_reasons(
        inputs, roots, actual_radars, profile
    )
    total_cells = int(np.prod(shape))
    valid_count = int(np.count_nonzero(valid))
    summary = {
        "schema_version": "1.0",
        "analysis_time": analysis_time.isoformat(),
        "grid_id": grid.grid_id,
        "grid_config_version": grid.config_version,
        "profile_version": profile.profile_version,
        "algorithm_version": profile.algorithm_version,
        "operational_eligible": not operational_reasons,
        "operational_reasons": list(operational_reasons),
        "input_radar_count": len(inputs),
        "actual_contributing_radar_count": len(actual_radars),
        "grid_cell_count": total_cells,
        "valid_cell_count": valid_count,
        "missing_cell_count": total_cells - valid_count,
        "low_quality_cell_count": int(np.count_nonzero(low)),
        "blended_cell_count": int(np.count_nonzero(contributor_count > 1)),
        "valid_coverage_ratio": valid_count / total_cells,
        "mean_quality_index": (
            float(np.mean(fields["QUALITY_INDEX"][valid])) if valid_count else 0.0
        ),
        "contributors": list(contributor_details),
    }
    return RadarMosaicResult(
        grid=grid,
        profile=profile,
        analysis_time=analysis_time,
        fields=fields,
        contributors=contributor_details,
        radar_source_codes=codes,
        summary=summary,
        operational_eligible=not operational_reasons,
        operational_reasons=operational_reasons,
        created_at=created_at or datetime.now(UTC),
    )


def _validate_analysis_identity(
    inputs: Sequence[RadarMosaicInput],
    analysis_time: datetime,
    grid: RegularLatLonGrid,
    profile: RadarMosaicProfile,
    flag_masks: Mapping[str, np.uint32],
) -> None:
    if len(inputs) < profile.alignment.minimum_contributors:
        raise RadarMosaicInputError("not enough aligned RadarGrid contributors")
    if analysis_time.timestamp() % profile.alignment.step_seconds != 0:
        raise RadarMosaicInputError("analysis time is not on the configured UTC boundary")
    if grid.grid_id != profile.grid_id or grid.config_version != profile.grid_config_version:
        raise RadarMosaicInputError("target grid differs from the mosaic profile")
    radar_ids = [item.radar_id for item in inputs]
    scan_ids = [item.scan_id for item in inputs]
    if len(radar_ids) != len(set(radar_ids)) or len(scan_ids) != len(set(scan_ids)):
        raise RadarMosaicInputError("mosaic radar and scan identities must be unique")
    required_flags = {
        "MISSING",
        "HARDWARE_ANOMALY",
        "LOW_QUALITY",
        *profile.fusion.reject_flags,
    }
    missing_flags = sorted(required_flags - flag_masks.keys())
    if missing_flags:
        raise RadarMosaicInputError(
            "flag definition is missing mosaic flags: " + ",".join(missing_flags)
        )


def _open_and_validate(
    item: RadarMosaicInput,
    analysis_time: datetime,
    grid: RegularLatLonGrid,
    profile: RadarMosaicProfile,
) -> zarr.Group:
    validate_radar_grid_zarr_store(item.objects)
    store = MemoryStore()
    store.update({key: bytes(value) for key, value in item.objects.items()})
    root = zarr.open_group(store=store, mode="r")
    expected = (
        ("radar_id", str(root.attrs.get("radar_id")), item.radar_id),
        ("scan_id", str(root.attrs.get("scan_id")), item.scan_id),
        ("grid_id", str(root.attrs.get("grid_id")), grid.grid_id),
        (
            "grid_config_version",
            str(root.attrs.get("grid_config_version")),
            grid.config_version,
        ),
        (
            "hybrid_scan_version",
            str(root.attrs.get("hybrid_scan_version")),
            item.hybrid_scan_version,
        ),
        (
            "flag_definition_version",
            str(root.attrs.get("flag_definition_version")),
            profile.flag_definition_version,
        ),
        (
            "coordinate_sha256",
            str(root.attrs.get("coordinate_sha256")),
            grid.coordinate_sha256,
        ),
    )
    for name, actual, requested in expected:
        if actual != requested:
            raise RadarMosaicInputError(
                f"RadarGrid {name} differs from the mosaic request or configuration"
            )
    if not np.array_equal(root["lat"][:], grid.latitude) or not np.array_equal(
        root["lon"][:], grid.longitude
    ):
        raise RadarMosaicInputError("RadarGrid coordinates differ from the immutable grid")
    end_time = _parse_time(str(root.attrs.get("volume_end_time_utc")))
    actual_offset = int(round((end_time - analysis_time).total_seconds()))
    if actual_offset != item.time_offset_seconds:
        raise RadarMosaicInputError("RadarGrid time offset differs from the request")
    if abs(actual_offset) > profile.alignment.maximum_absolute_offset_seconds:
        raise RadarMosaicInputError("RadarGrid lies outside the analysis alignment window")
    return root


def _empty_fields(
    shape: tuple[int, int], flag_masks: Mapping[str, np.uint32]
) -> dict[str, np.ndarray]:
    floating = ("DBZH_QC", "REF_NOWCAST", *WEIGHTED_FIELDS)
    fields = {name: np.full(shape, np.nan, dtype="float32") for name in floating}
    fields["QC_FLAGS"] = np.full(shape, flag_masks["MISSING"], dtype="uint32")
    fields["SOURCE_RADAR"] = np.zeros(shape, dtype="uint16")
    fields["CONTRIBUTOR_COUNT"] = np.zeros(shape, dtype="uint8")
    fields["VALID_MASK"] = np.zeros(shape, dtype="uint8")
    fields["LOW_QUALITY_MASK"] = np.zeros(shape, dtype="uint8")
    return fields


def _stack(roots: Sequence[zarr.Group], name: str) -> np.ndarray:
    return np.stack([root[name][:] for root in roots])


def _weighted_available(
    values: np.ndarray, weights: np.ndarray, contributes: np.ndarray
) -> np.ndarray:
    available = contributes & np.isfinite(values)
    effective = np.where(available, weights, 0.0)
    denominator = effective.sum(axis=0)
    numerator = np.sum(np.where(available, values * effective, 0.0), axis=0)
    return np.divide(
        numerator,
        denominator,
        out=np.full(denominator.shape, np.nan, dtype="float32"),
        where=denominator > 0,
    ).astype("float32")


def _contributor_details(
    inputs: Sequence[RadarMosaicInput],
    roots: Sequence[zarr.Group],
    contributes: np.ndarray,
    adjusted_quality: np.ndarray,
) -> tuple[dict[str, Any], ...]:
    details: list[dict[str, Any]] = []
    for index, item in enumerate(inputs):
        mask = contributes[index]
        details.append(
            {
                "radar_id": item.radar_id,
                "scan_id": item.scan_id,
                "grid_uri": item.grid_uri,
                "time_offset_seconds": item.time_offset_seconds,
                "hybrid_scan_version": item.hybrid_scan_version,
                "input_asset_ids": list(roots[index].attrs["input_asset_ids"]),
                "qc_pipeline_version": str(
                    roots[index].attrs["qc_pipeline_version"]
                ),
                "input_operational_eligible": bool(
                    roots[index].attrs.get("operational_eligible")
                ),
                "contributing_cell_count": int(np.count_nonzero(mask)),
                "mean_adjusted_quality_index": (
                    float(np.mean(adjusted_quality[index][mask]))
                    if np.any(mask)
                    else 0.0
                ),
            }
        )
    return tuple(details)


def _operational_reasons(
    inputs: Sequence[RadarMosaicInput],
    roots: Sequence[zarr.Group],
    actual_radars: set[str],
    profile: RadarMosaicProfile,
) -> tuple[str, ...]:
    reasons: list[str] = []
    if len(actual_radars) < profile.alignment.minimum_operational_contributors:
        reasons.append("insufficient_operational_contributors")
    for item, root in zip(inputs, roots, strict=True):
        if item.radar_id in actual_radars and not root.attrs.get("operational_eligible"):
            reasons.append(f"input_not_operational:{item.radar_id}")
    missing_expected = sorted(
        set(profile.alignment.expected_radar_ids) - {item.radar_id for item in inputs}
    )
    reasons.extend(f"expected_radar_missing:{radar_id}" for radar_id in missing_expected)
    return tuple(dict.fromkeys(reasons))


def _parse_time(value: str) -> datetime:
    try:
        return _utc(datetime.fromisoformat(value.replace("Z", "+00:00")))
    except ValueError as exc:
        raise RadarMosaicInputError("RadarGrid volume end time is invalid") from exc


def _utc(value: datetime) -> datetime:
    if value.tzinfo is None or value.utcoffset() is None:
        raise RadarMosaicInputError("analysis and volume times must include UTC offset")
    return value.astimezone(UTC)
