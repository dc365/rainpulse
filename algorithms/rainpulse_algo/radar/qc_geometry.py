from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass
from typing import Any

import numpy as np
import zarr
from pyproj import Geod

from .blockage import (
    PolarBlockage,
    TerrainSampler,
    beam_centre_height_m,
    beam_radius_m,
    calculate_polar_blockage,
)
from .config import RadarDecoderConfig
from .grid_profile import BeamGeometryConfig, BlockageConfig

QC_GEOMETRY_VERTICAL_CRS = "EPSG:3855"
QC_GEOMETRY_BEAM_CONFIG = BeamGeometryConfig(
    effective_earth_radius_factor=1.3333333333333333,
    earth_radius_m=6_371_000.0,
    unverified_vertical_datum_policy="allow_engineering_only",
)
QC_GEOMETRY_BLOCKAGE_CONFIG = BlockageConfig(
    flag_fraction=0.10,
    maximum_usable_fraction=0.70,
)
QC_VERTICAL_MAX_HEIGHT_DIFFERENCE_M = 500.0
QC_TRUSTED_MAX_BLOCKAGE_FRACTION = 0.30
VERTICAL_CONSISTENCY_AVAILABLE_MASK_FIELD = "P_VERTICAL_CONSISTENCY_AVAILABLE_MASK"
VERTICAL_HEIGHT_DIFFERENCE_M_FIELD = "VERTICAL_HEIGHT_DIFFERENCE_M"
CROSS_RADAR_TRUSTED_SUPPORT_FIELD = "P_CROSS_RADAR_TRUSTED_SUPPORT"
CROSS_RADAR_TRUSTED_AVAILABLE_MASK_FIELD = "P_CROSS_RADAR_TRUSTED_AVAILABLE_MASK"
QC_GEOMETRY_AVAILABLE_MASK_FIELDS = {
    VERTICAL_CONSISTENCY_AVAILABLE_MASK_FIELD,
    CROSS_RADAR_TRUSTED_AVAILABLE_MASK_FIELD,
}
QC_GEOMETRY_OUTPUT_DTYPES = {
    VERTICAL_CONSISTENCY_AVAILABLE_MASK_FIELD: np.dtype("uint8"),
    VERTICAL_HEIGHT_DIFFERENCE_M_FIELD: np.dtype("float32"),
    CROSS_RADAR_TRUSTED_SUPPORT_FIELD: np.dtype("float32"),
    CROSS_RADAR_TRUSTED_AVAILABLE_MASK_FIELD: np.dtype("uint8"),
}


@dataclass(frozen=True)
class RadarBeamContext:
    radar_id: str
    longitude_deg: float
    latitude_deg: float
    antenna_altitude_m: float
    beam_width_vertical_deg: float
    altitude_datum_status: str
    radar_config_version: str | None = None
    beam_width_horizontal_deg: float | None = None


@dataclass(frozen=True)
class VerticalConsistencyDiagnostics:
    probabilities: tuple[np.ndarray, ...]
    available_masks: tuple[np.ndarray, ...]
    height_differences_m: tuple[np.ndarray, ...]
    metrics: dict[str, float]


@dataclass(frozen=True)
class CrossRadarSupportReference:
    radar_id: str
    root: zarr.Group
    beam_context: RadarBeamContext | None
    health_available: bool
    dem_compatible: bool
    hard_interference_by_sweep: Mapping[str, np.ndarray]


@dataclass(frozen=True)
class CrossRadarTrustedSupportDiagnostics:
    support_fraction: np.ndarray
    available_mask: np.ndarray
    consistency_by_ray: np.ndarray
    reference_used_mask: tuple[bool, ...]
    metrics: dict[str, float]


def vertical_datum_status(
    site: Mapping[str, Any],
    *,
    vertical_crs: str = QC_GEOMETRY_VERTICAL_CRS,
) -> str:
    datum = site.get("altitude_datum")
    if datum is None:
        return "unverified_engineering"
    normalized = str(datum).strip().upper().replace(" ", "")
    if normalized not in {"EPSG:3855", "EGM2008", "EGM2008HEIGHT"}:
        return f"incompatible_with_{vertical_crs.lower().replace(':', '_')}"
    return "verified_egm2008"


def radar_beam_context_from_config(
    radar_config: RadarDecoderConfig,
    *,
    vertical_crs: str = QC_GEOMETRY_VERTICAL_CRS,
) -> RadarBeamContext:
    try:
        longitude_deg = float(radar_config.site["longitude_deg"])
        latitude_deg = float(radar_config.site["latitude_deg"])
        antenna_altitude_m = float(radar_config.site["antenna_altitude_m"])
        beam_width_vertical_deg = float(radar_config.hardware["beam_width_vertical_deg"])
    except (KeyError, TypeError, ValueError) as error:
        raise ValueError("radar config lacks usable beam geometry fields") from error
    if not all(
        np.isfinite(item)
        for item in (
            longitude_deg,
            latitude_deg,
            antenna_altitude_m,
            beam_width_vertical_deg,
        )
    ):
        raise ValueError("radar config beam geometry fields must be finite")
    if beam_width_vertical_deg <= 0:
        raise ValueError("radar config vertical beam width must be positive")
    horizontal_width = radar_config.hardware.get("beam_width_deg")
    if horizontal_width is not None:
        horizontal_width = float(horizontal_width)
        if not np.isfinite(horizontal_width) or not 0 < horizontal_width <= 360:
            raise ValueError("radar config horizontal beam width is invalid")
    return RadarBeamContext(
        radar_id=radar_config.radar_id,
        longitude_deg=longitude_deg,
        latitude_deg=latitude_deg,
        antenna_altitude_m=antenna_altitude_m,
        beam_width_vertical_deg=beam_width_vertical_deg,
        altitude_datum_status=vertical_datum_status(
            radar_config.site,
            vertical_crs=vertical_crs,
        ),
        radar_config_version=radar_config.config_version,
        beam_width_horizontal_deg=horizontal_width,
    )


def beam_support_overlap_mask(
    reference_height_m: np.ndarray,
    reference_radius_m: np.ndarray,
    comparison_height_m: np.ndarray,
    comparison_radius_m: np.ndarray,
    *,
    maximum_height_difference_m: float = QC_VERTICAL_MAX_HEIGHT_DIFFERENCE_M,
) -> tuple[np.ndarray, np.ndarray]:
    reference_height, reference_radius, comparison_height, comparison_radius = np.broadcast_arrays(
        np.asarray(reference_height_m, dtype="float64"),
        np.asarray(reference_radius_m, dtype="float64"),
        np.asarray(comparison_height_m, dtype="float64"),
        np.asarray(comparison_radius_m, dtype="float64"),
    )
    height_difference = np.abs(reference_height - comparison_height)
    overlap = height_difference <= (reference_radius + comparison_radius)
    overlap &= height_difference <= maximum_height_difference_m
    overlap &= np.isfinite(reference_height)
    overlap &= np.isfinite(reference_radius)
    overlap &= np.isfinite(comparison_height)
    overlap &= np.isfinite(comparison_radius)
    return overlap, height_difference.astype("float32")


def nearest_azimuth_matches(
    source: np.ndarray,
    target: np.ndarray,
) -> tuple[np.ndarray, np.ndarray, np.ndarray]:
    source_values = np.asarray(source, dtype="float64")
    target_values = np.asarray(target, dtype="float64")
    target_indices = np.flatnonzero(np.isfinite(target_values))
    if target_indices.size == 0:
        raise ValueError("comparison target has no finite azimuths")
    normalized_target = np.mod(target_values[target_indices], 360.0)
    order = np.argsort(normalized_target, kind="stable")
    sorted_target = normalized_target[order]
    sorted_indices = target_indices[order]
    unique_mask = np.ones(sorted_target.shape, dtype=bool)
    if sorted_target.size > 1:
        unique_mask[1:] = sorted_target[1:] != sorted_target[:-1]
    unique_target = sorted_target[unique_mask]
    unique_indices = sorted_indices[unique_mask]

    result = np.zeros(source_values.shape, dtype="int64")
    offset = np.full(source_values.shape, np.nan, dtype="float64")
    supported = np.isfinite(source_values)
    if not np.any(supported):
        return result, offset, supported
    normalized_source = np.mod(source_values[supported], 360.0)
    positions = np.searchsorted(unique_target, normalized_source, side="left")
    right = positions % unique_target.size
    left = (positions - 1) % unique_target.size
    right_values = unique_target[right]
    left_values = unique_target[left]
    right_delta = np.abs((right_values - normalized_source + 180.0) % 360.0 - 180.0)
    left_delta = np.abs((left_values - normalized_source + 180.0) % 360.0 - 180.0)
    choose_right = right_delta < left_delta
    equal = right_delta == left_delta
    if np.any(equal):
        choose_right[equal] = unique_indices[right[equal]] < unique_indices[left[equal]]
    chosen = np.where(choose_right, right, left)
    result[supported] = unique_indices[chosen]
    offset[supported] = np.where(choose_right, right_delta, left_delta)
    return result, offset, supported


def nearest_coordinate_indices(source: np.ndarray, target: np.ndarray) -> np.ndarray:
    target_values = np.asarray(target, dtype="float64")
    if target_values.size == 0:
        raise ValueError("comparison target has no range gates")
    source_values = np.asarray(source, dtype="float64")
    insertion = np.searchsorted(target_values, source_values)
    following = np.clip(insertion, 0, target_values.size - 1)
    previous = np.clip(insertion - 1, 0, target_values.size - 1)
    use_previous = np.abs(source_values - target_values[previous]) <= np.abs(
        source_values - target_values[following]
    )
    return np.where(use_previous, previous, following)


def nearest_coordinate_matches(
    source: np.ndarray,
    target: np.ndarray,
) -> tuple[np.ndarray, np.ndarray]:
    source_values = np.asarray(source, dtype="float64")
    target_values = np.asarray(target, dtype="float64")
    indices = nearest_coordinate_indices(source_values, target_values)
    supported = np.isfinite(source_values)
    if target_values.size:
        supported &= source_values >= float(target_values[0])
        supported &= source_values <= float(target_values[-1])
    return indices, supported


def median_circular_ray_spacing(azimuth_deg: np.ndarray) -> float:
    values = np.sort(np.mod(np.asarray(azimuth_deg, dtype="float64"), 360.0))
    if values.size < 2:
        return 0.5
    spacing = np.diff(np.concatenate((values, values[:1] + 360.0)))
    finite = spacing[np.isfinite(spacing) & (spacing > 0)]
    return float(np.median(finite)) if finite.size else 0.5


def build_vertical_consistency_diagnostics(
    sweeps: tuple[dict[str, Any], ...],
    *,
    minimum_dbzh: float,
    support_tolerance_db: float,
    maximum_range_m: float,
    strict_observability: bool = False,
    maximum_azimuth_offset_deg: float | None = None,
    radar_beam_context: RadarBeamContext | None = None,
    beam_config: BeamGeometryConfig = QC_GEOMETRY_BEAM_CONFIG,
    maximum_height_difference_m: float = QC_VERTICAL_MAX_HEIGHT_DIFFERENCE_M,
) -> VerticalConsistencyDiagnostics:
    shapes = [np.asarray(sweep["dbzh"]).shape for sweep in sweeps]
    probabilities = [np.full(shape, np.nan, dtype="float32") for shape in shapes]
    available_masks = [np.zeros(shape, dtype="uint8") for shape in shapes]
    height_differences = [np.full(shape, np.nan, dtype="float32") for shape in shapes]
    geometry_verified = (
        radar_beam_context is not None
        and radar_beam_context.altitude_datum_status == "verified_egm2008"
    )
    if strict_observability and not geometry_verified:
        return VerticalConsistencyDiagnostics(
            probabilities=tuple(probabilities),
            available_masks=tuple(available_masks),
            height_differences_m=tuple(height_differences),
            metrics={
                "geometry_aware": 1.0,
                "verified_vertical_datum": 0.0,
                "available_gate_count": 0.0,
            },
        )

    for low_index, low in enumerate(sweeps):
        low_elevation_value = float(np.nanmedian(np.asarray(low["elevation"], dtype="float64")))
        higher = [
            (index, item)
            for index, item in enumerate(sweeps)
            if float(np.nanmedian(np.asarray(item["elevation"], dtype="float64")))
            > low_elevation_value + 0.2
        ]
        if not higher:
            continue
        _, high = min(
            higher,
            key=lambda item: float(np.nanmedian(np.asarray(item[1]["elevation"], dtype="float64"))),
        )
        low_dbzh = np.asarray(low["dbzh"], dtype="float32")
        high_dbzh = np.asarray(high["dbzh"], dtype="float32")
        if low_dbzh.ndim != 2 or high_dbzh.ndim != 2:
            raise ValueError("vertical consistency expects two-dimensional sweeps")
        low_azimuth = np.asarray(low["azimuth"], dtype="float64")
        high_azimuth = np.asarray(high["azimuth"], dtype="float64")
        low_range = np.asarray(low["range"], dtype="float64")
        high_range = np.asarray(high["range"], dtype="float64")
        ray_index, azimuth_offset, azimuth_supported = nearest_azimuth_matches(
            low_azimuth,
            high_azimuth,
        )
        gate_index, gate_supported = nearest_coordinate_matches(low_range, high_range)
        matched = high_dbzh[ray_index[:, None], gate_index[None, :]]
        eligible = (
            np.isfinite(low_dbzh)
            & (low_dbzh >= minimum_dbzh)
            & (low_range[None, :] <= maximum_range_m)
        )
        difference = np.maximum(low_dbzh - matched, 0.0)
        consistency = 1.0 - np.clip(difference / support_tolerance_db, 0.0, 1.0)

        supported = np.broadcast_to(azimuth_supported[:, None], low_dbzh.shape).copy()
        supported &= np.broadcast_to(gate_supported[None, :], low_dbzh.shape)
        if maximum_azimuth_offset_deg is not None:
            supported &= np.broadcast_to(
                azimuth_offset[:, None] <= maximum_azimuth_offset_deg,
                low_dbzh.shape,
            )
        supported &= np.isfinite(matched)
        if geometry_verified:
            low_elevation = _elevation_by_ray(low["elevation"], low_dbzh.shape[0])
            high_elevation = _elevation_by_ray(high["elevation"], high_dbzh.shape[0])
            low_range_grid = np.broadcast_to(low_range[None, :], low_dbzh.shape)
            low_elevation_grid = np.broadcast_to(low_elevation[:, None], low_dbzh.shape)
            high_range_grid = np.broadcast_to(high_range[gate_index][None, :], low_dbzh.shape)
            high_elevation_grid = np.broadcast_to(
                high_elevation[ray_index][:, None], low_dbzh.shape
            )
            low_height = beam_centre_height_m(
                low_range_grid,
                low_elevation_grid,
                radar_beam_context.antenna_altitude_m,
                beam_config,
            )
            low_radius = beam_radius_m(
                low_range_grid,
                radar_beam_context.beam_width_vertical_deg,
            )
            high_height = beam_centre_height_m(
                high_range_grid,
                high_elevation_grid,
                radar_beam_context.antenna_altitude_m,
                beam_config,
            )
            high_radius = beam_radius_m(
                high_range_grid,
                radar_beam_context.beam_width_vertical_deg,
            )
            overlap, height_difference = beam_support_overlap_mask(
                low_height,
                low_radius,
                high_height,
                high_radius,
                maximum_height_difference_m=maximum_height_difference_m,
            )
            comparable = eligible & supported
            height_differences[low_index][comparable] = height_difference[comparable]
            supported &= overlap
        elif not strict_observability:
            comparable = eligible & supported
            available_masks[low_index][comparable] = 1
            probabilities[low_index][comparable] = consistency[comparable]
            continue

        available = eligible & supported
        available_masks[low_index][available] = 1
        probabilities[low_index][available] = consistency[available]

    if not strict_observability and not geometry_verified:
        for index, values in enumerate(probabilities):
            available_masks[index][np.isfinite(values)] = 1

    available_gate_count = float(sum(int(np.count_nonzero(mask)) for mask in available_masks))
    return VerticalConsistencyDiagnostics(
        probabilities=tuple(probabilities),
        available_masks=tuple(available_masks),
        height_differences_m=tuple(height_differences),
        metrics={
            "geometry_aware": float(strict_observability),
            "verified_vertical_datum": float(geometry_verified),
            "available_gate_count": available_gate_count,
        },
    )


def build_trusted_cross_radar_support(
    current_sweep: dict[str, Any],
    current_beam_context: RadarBeamContext | None,
    references: tuple[CrossRadarSupportReference, ...],
    *,
    terrain: TerrainSampler | None,
    echo_threshold_dbzh: float,
    minimum_overlap_gates: int,
    valid_range_dbz: tuple[float, float] | None = None,
    beam_config: BeamGeometryConfig = QC_GEOMETRY_BEAM_CONFIG,
    blockage_config: BlockageConfig = QC_GEOMETRY_BLOCKAGE_CONFIG,
    blockage_threshold: float = QC_TRUSTED_MAX_BLOCKAGE_FRACTION,
    maximum_height_difference_m: float = QC_VERTICAL_MAX_HEIGHT_DIFFERENCE_M,
    blockage_cache: dict[tuple[str, str], Any] | None = None,
) -> CrossRadarTrustedSupportDiagnostics:
    current_dbzh = np.asarray(current_sweep["dbzh"], dtype="float32")
    if current_dbzh.ndim != 2:
        raise ValueError("cross-radar trusted support expects a two-dimensional current sweep")
    shape = current_dbzh.shape
    support_fraction = np.full(shape, np.nan, dtype="float32")
    available_mask = np.zeros(shape, dtype="uint8")
    consistency_by_ray = np.full(shape[0], np.nan, dtype="float32")
    reference_used = [False] * len(references)
    geometry_verified = (
        current_beam_context is not None
        and current_beam_context.altitude_datum_status == "verified_egm2008"
    )
    if not geometry_verified or terrain is None:
        return CrossRadarTrustedSupportDiagnostics(
            support_fraction=support_fraction,
            available_mask=available_mask,
            consistency_by_ray=consistency_by_ray,
            reference_used_mask=tuple(reference_used),
            metrics={
                "reference_count": float(len(references)),
                "available_gate_count": 0.0,
                "available_ray_count": 0.0,
                "maximum_supporting_neighbour_count": 0.0,
                "verified_current_vertical_datum": float(geometry_verified),
            },
        )

    current_azimuth = np.asarray(current_sweep["azimuth"], dtype="float64")
    current_range = np.asarray(current_sweep["range"], dtype="float64")
    current_elevation = _elevation_by_ray(current_sweep["elevation"], shape[0])
    current_valid = np.isfinite(current_dbzh)
    if valid_range_dbz is not None:
        current_valid &= current_dbzh >= float(valid_range_dbz[0])
        current_valid &= current_dbzh <= float(valid_range_dbz[1])
    current_echo = current_valid & (current_dbzh >= echo_threshold_dbzh)
    current_azimuth_grid = np.broadcast_to(current_azimuth[:, None], shape)
    current_range_grid = np.broadcast_to(current_range[None, :], shape)
    current_elevation_grid = np.broadcast_to(current_elevation[:, None], shape)
    current_height = beam_centre_height_m(
        current_range_grid,
        current_elevation_grid,
        current_beam_context.antenna_altitude_m,
        beam_config,
    )
    current_radius = beam_radius_m(
        current_range_grid,
        current_beam_context.beam_width_vertical_deg,
    )
    geod = Geod(ellps="WGS84")
    longitude, latitude, _ = geod.fwd(
        np.full(current_azimuth_grid.size, current_beam_context.longitude_deg),
        np.full(current_azimuth_grid.size, current_beam_context.latitude_deg),
        current_azimuth_grid.ravel(),
        current_range_grid.ravel(),
    )
    longitude = np.asarray(longitude, dtype="float64").reshape(shape)
    latitude = np.asarray(latitude, dtype="float64").reshape(shape)

    observed_count = np.zeros(shape, dtype="int16")
    echo_count = np.zeros(shape, dtype="int16")
    cache = blockage_cache if blockage_cache is not None else {}
    for index, reference in enumerate(references):
        if (
            reference.beam_context is None
            or reference.beam_context.altitude_datum_status != "verified_egm2008"
            or not reference.health_available
            or not reference.dem_compatible
        ):
            continue
        best_values = np.full(shape, np.nan, dtype="float32")
        best_supported = np.zeros(shape, dtype=bool)
        best_height_difference = np.full(shape, np.inf, dtype="float32")
        for sweep_number in reference.root["sweep_number"][:]:
            sweep_name = f"sweep_{int(sweep_number):03d}"
            neighbour_group = reference.root[sweep_name]
            if "DBZH" not in neighbour_group:
                continue
            try:
                (
                    nearest_rays,
                    nearest_gates,
                    horizontal_supported,
                ) = _target_to_neighbour_matches(
                    longitude,
                    latitude,
                    reference.beam_context,
                    neighbour_group,
                )
            except ValueError:
                continue
            if not np.any(horizontal_supported):
                continue
            neighbour_dbzh = neighbour_group["DBZH"][:].astype("float32", copy=False)
            reference_valid = np.isfinite(neighbour_dbzh)
            if valid_range_dbz is not None:
                reference_valid &= neighbour_dbzh >= float(valid_range_dbz[0])
                reference_valid &= neighbour_dbzh <= float(valid_range_dbz[1])
            matched_values = neighbour_dbzh[nearest_rays, nearest_gates]
            supported = horizontal_supported & reference_valid[nearest_rays, nearest_gates]
            if not np.any(supported):
                continue
            cache_key = (reference.radar_id, sweep_name)
            blockage = cache.get(cache_key)
            if blockage is None:
                blockage = _trusted_support_blockage(
                    reference.beam_context,
                    neighbour_group,
                    terrain,
                    beam_config,
                    blockage_config,
                    nearest_rays,
                    nearest_gates,
                    horizontal_supported,
                )
                cache[cache_key] = blockage
            blockage_supported = blockage.support_mask[nearest_rays, nearest_gates] == 1
            blockage_supported &= np.isfinite(blockage.cumulative[nearest_rays, nearest_gates])
            blockage_supported &= (
                blockage.cumulative[nearest_rays, nearest_gates] <= blockage_threshold
            )
            supported &= blockage_supported
            hard_rays = np.asarray(
                reference.hard_interference_by_sweep.get(
                    sweep_name,
                    np.zeros(len(neighbour_group["azimuth"]), dtype=bool),
                ),
                dtype=bool,
            )
            if hard_rays.shape != (len(neighbour_group["azimuth"]),):
                hard_rays = np.zeros(len(neighbour_group["azimuth"]), dtype=bool)
            supported &= ~hard_rays[nearest_rays]
            if not np.any(supported):
                continue

            neighbour_range = np.asarray(neighbour_group["range"][:], dtype="float64")
            neighbour_range_grid = neighbour_range[nearest_gates]
            neighbour_radius = beam_radius_m(
                neighbour_range_grid,
                reference.beam_context.beam_width_vertical_deg,
            )
            overlap, height_difference = beam_support_overlap_mask(
                current_height,
                current_radius,
                blockage.beam_height_m[nearest_rays, nearest_gates],
                neighbour_radius,
                maximum_height_difference_m=maximum_height_difference_m,
            )
            supported &= overlap
            if not np.any(supported):
                continue
            update = supported & (height_difference < best_height_difference)
            best_values[update] = matched_values[update]
            best_supported[update] = True
            best_height_difference[update] = height_difference[update]
        if not np.any(best_supported):
            continue
        reference_used[index] = True
        observed_count[best_supported] += 1
        echo_count[best_supported & (best_values >= echo_threshold_dbzh)] += 1

    available = observed_count > 0
    support_fraction[available] = echo_count[available] / observed_count[available]
    available_mask[available] = 1
    for ray_index in range(shape[0]):
        overlap = current_echo[ray_index] & available[ray_index]
        overlap_count = int(np.count_nonzero(overlap))
        if overlap_count < minimum_overlap_gates:
            continue
        consistency_by_ray[ray_index] = float(np.mean(support_fraction[ray_index, overlap]))

    return CrossRadarTrustedSupportDiagnostics(
        support_fraction=support_fraction,
        available_mask=available_mask,
        consistency_by_ray=consistency_by_ray,
        reference_used_mask=tuple(reference_used),
        metrics={
            "reference_count": float(len(references)),
            "available_gate_count": float(np.count_nonzero(available)),
            "available_ray_count": float(np.count_nonzero(np.isfinite(consistency_by_ray))),
            "maximum_supporting_neighbour_count": float(observed_count.max())
            if observed_count.size
            else 0.0,
            "verified_current_vertical_datum": 1.0,
        },
    )


def _elevation_by_ray(values: Any, ray_count: int) -> np.ndarray:
    elevation = np.asarray(values, dtype="float64")
    if elevation.ndim == 0:
        return np.full(ray_count, float(elevation), dtype="float64")
    if elevation.shape != (ray_count,):
        raise ValueError("elevation coordinate differs from ray shape")
    return elevation


def _target_to_neighbour_matches(
    longitude_deg: np.ndarray,
    latitude_deg: np.ndarray,
    neighbour_beam_context: RadarBeamContext,
    neighbour_group: zarr.Group,
) -> tuple[np.ndarray, np.ndarray, np.ndarray]:
    target_longitude = np.asarray(longitude_deg, dtype="float64")
    target_latitude = np.asarray(latitude_deg, dtype="float64")
    shape = target_longitude.shape
    geod = Geod(ellps="WGS84")
    forward, _, distance = geod.inv(
        np.full(target_longitude.size, neighbour_beam_context.longitude_deg),
        np.full(target_latitude.size, neighbour_beam_context.latitude_deg),
        target_longitude.ravel(),
        target_latitude.ravel(),
    )
    neighbour_azimuth = neighbour_group["azimuth"][:].astype("float64", copy=False)
    neighbour_range = neighbour_group["range"][:].astype("float64", copy=False)
    nearest_rays, azimuth_offset, azimuth_supported = nearest_azimuth_matches(
        np.mod(forward, 360.0),
        neighbour_azimuth,
    )
    nearest_gates, gate_supported = nearest_coordinate_matches(distance, neighbour_range)
    ray_spacing = median_circular_ray_spacing(neighbour_azimuth)
    gate_spacing = float(np.median(np.diff(neighbour_range))) if neighbour_range.size > 1 else 0.0
    supported = np.isfinite(distance)
    supported &= np.isfinite(azimuth_offset)
    supported &= azimuth_supported
    supported &= gate_supported
    supported &= azimuth_offset <= max(0.5, ray_spacing * 0.75)
    supported &= np.abs(neighbour_range[nearest_gates] - distance) <= max(1.0, gate_spacing * 0.75)
    return (
        nearest_rays.reshape(shape),
        nearest_gates.reshape(shape),
        supported.reshape(shape),
    )


def _trusted_support_blockage(
    beam_context: RadarBeamContext,
    group: zarr.Group,
    terrain: TerrainSampler,
    beam_config: BeamGeometryConfig,
    blockage_config: BlockageConfig,
    nearest_rays: np.ndarray,
    nearest_gates: np.ndarray,
    supported: np.ndarray,
) -> PolarBlockage:
    ray_count = len(group["azimuth"])
    required = np.full(ray_count, -1, dtype="int32")
    if np.any(supported):
        np.maximum.at(required, nearest_rays[supported], nearest_gates[supported])
    return calculate_polar_blockage(
        azimuth_deg=group["azimuth"][:],
        elevation_deg=group["elevation"][:],
        range_m=group["range"][:],
        required_max_gate=required,
        radar_longitude_deg=beam_context.longitude_deg,
        radar_latitude_deg=beam_context.latitude_deg,
        antenna_altitude_m=beam_context.antenna_altitude_m,
        vertical_beam_width_deg=beam_context.beam_width_vertical_deg,
        beam_config=beam_config,
        blockage_config=blockage_config,
        terrain=terrain,
    )
