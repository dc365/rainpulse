from __future__ import annotations

from dataclasses import dataclass
from typing import Protocol

import numpy as np
from pyproj import Geod

from .grid_profile import BeamGeometryConfig, BlockageConfig, PolarMappingConfig


class TerrainSampler(Protocol):
    def sample(self, longitude: np.ndarray, latitude: np.ndarray) -> np.ndarray: ...


@dataclass(frozen=True)
class GridPolarMapping:
    ray_index: np.ndarray
    gate_index: np.ndarray
    supported: np.ndarray
    azimuth_deg: np.ndarray
    distance_m: np.ndarray


@dataclass(frozen=True)
class PolarBlockage:
    partial: np.ndarray
    cumulative: np.ndarray
    beam_height_m: np.ndarray
    terrain_height_m: np.ndarray
    support_mask: np.ndarray


def beam_centre_height_m(
    range_m: np.ndarray,
    elevation_deg: np.ndarray,
    antenna_altitude_m: float,
    config: BeamGeometryConfig,
) -> np.ndarray:
    distance = np.asarray(range_m, dtype="float64")
    elevation = np.deg2rad(np.asarray(elevation_deg, dtype="float64"))
    effective_radius = config.earth_radius_m * config.effective_earth_radius_factor
    height = np.sqrt(
        distance * distance
        + effective_radius * effective_radius
        + 2 * distance * effective_radius * np.sin(elevation)
    ) - effective_radius
    return (height + antenna_altitude_m).astype("float32")


def beam_radius_m(range_m: np.ndarray, vertical_beam_width_deg: float) -> np.ndarray:
    half_width = np.deg2rad(vertical_beam_width_deg / 2)
    return (
        np.asarray(range_m, dtype="float64") * np.tan(half_width)
    ).astype("float32")


def circular_partial_blockage(
    terrain_height_m: np.ndarray,
    beam_height_m: np.ndarray,
    radius_m: np.ndarray,
) -> np.ndarray:
    terrain, centre, radius = np.broadcast_arrays(
        np.asarray(terrain_height_m, dtype="float64"),
        np.asarray(beam_height_m, dtype="float64"),
        np.asarray(radius_m, dtype="float64"),
    )
    result = np.full(terrain.shape, np.nan, dtype="float64")
    finite = np.isfinite(terrain) & np.isfinite(centre) & np.isfinite(radius) & (radius > 0)
    normalized = np.zeros(terrain.shape, dtype="float64")
    normalized[finite] = (terrain[finite] - centre[finite]) / radius[finite]
    below = finite & (normalized <= -1)
    above = finite & (normalized >= 1)
    partial = finite & ~below & ~above
    result[below] = 0.0
    result[above] = 1.0
    values = normalized[partial]
    result[partial] = (
        values * np.sqrt(np.maximum(0.0, 1.0 - values * values))
        + np.arcsin(values)
        + np.pi / 2
    ) / np.pi
    return result.astype("float32")


def map_grid_to_polar(
    longitude: np.ndarray,
    latitude: np.ndarray,
    *,
    radar_longitude_deg: float,
    radar_latitude_deg: float,
    sweep_azimuth_deg: np.ndarray,
    sweep_range_m: np.ndarray,
    config: PolarMappingConfig,
) -> GridPolarMapping:
    lon, lat = np.broadcast_arrays(
        np.asarray(longitude, dtype="float64"),
        np.asarray(latitude, dtype="float64"),
    )
    geod = Geod(ellps="WGS84")
    forward, _, distance = geod.inv(
        np.full(lon.shape, radar_longitude_deg),
        np.full(lat.shape, radar_latitude_deg),
        lon,
        lat,
    )
    target_azimuth = np.mod(forward, 360.0)
    source_azimuth = np.mod(np.asarray(sweep_azimuth_deg, dtype="float64"), 360.0)
    order = np.argsort(source_azimuth)
    sorted_azimuth = source_azimuth[order]
    positions = np.searchsorted(sorted_azimuth, target_azimuth, side="left")
    right_position = positions % len(sorted_azimuth)
    left_position = (positions - 1) % len(sorted_azimuth)
    right_values = sorted_azimuth[right_position]
    left_values = sorted_azimuth[left_position]
    right_delta = np.abs((right_values - target_azimuth + 180) % 360 - 180)
    left_delta = np.abs((left_values - target_azimuth + 180) % 360 - 180)
    choose_right = right_delta < left_delta
    sorted_position = np.where(choose_right, right_position, left_position)
    ray_index = order[sorted_position]
    azimuth_offset = np.where(choose_right, right_delta, left_delta)

    ranges = np.asarray(sweep_range_m, dtype="float64")
    gate_positions = np.searchsorted(ranges, distance, side="left")
    right_gate = np.clip(gate_positions, 0, len(ranges) - 1)
    left_gate = np.clip(gate_positions - 1, 0, len(ranges) - 1)
    choose_right_gate = np.abs(ranges[right_gate] - distance) < np.abs(
        ranges[left_gate] - distance
    )
    gate_index = np.where(choose_right_gate, right_gate, left_gate)
    if len(ranges) > 1:
        gate_spacing = float(np.median(np.diff(ranges)))
    else:
        gate_spacing = float("inf")
    range_offset = np.abs(ranges[gate_index] - distance)
    supported = (
        np.isfinite(distance)
        & (azimuth_offset <= config.maximum_azimuth_offset_deg)
        & (
            range_offset
            <= gate_spacing * config.maximum_range_offset_gate_fraction
        )
    )
    return GridPolarMapping(
        ray_index=ray_index.astype("int32"),
        gate_index=gate_index.astype("int32"),
        supported=supported,
        azimuth_deg=target_azimuth.astype("float32"),
        distance_m=np.asarray(distance, dtype="float32"),
    )


def required_gate_by_ray(
    mapping: GridPolarMapping,
    ray_count: int,
) -> np.ndarray:
    maximum = np.full(ray_count, -1, dtype="int32")
    supported = mapping.supported.ravel()
    np.maximum.at(
        maximum,
        mapping.ray_index.ravel()[supported],
        mapping.gate_index.ravel()[supported],
    )
    return maximum


def calculate_polar_blockage(
    *,
    azimuth_deg: np.ndarray,
    elevation_deg: np.ndarray,
    range_m: np.ndarray,
    required_max_gate: np.ndarray,
    radar_longitude_deg: float,
    radar_latitude_deg: float,
    antenna_altitude_m: float,
    vertical_beam_width_deg: float,
    beam_config: BeamGeometryConfig,
    blockage_config: BlockageConfig,
    terrain: TerrainSampler,
) -> PolarBlockage:
    ray_count = len(azimuth_deg)
    gate_count = len(range_m)
    shape = (ray_count, gate_count)
    partial_output = np.full(shape, np.nan, dtype="float32")
    cumulative_output = np.full(shape, np.nan, dtype="float32")
    beam_output = np.full(shape, np.nan, dtype="float32")
    terrain_output = np.full(shape, np.nan, dtype="float32")
    support_output = np.zeros(shape, dtype="uint8")
    used_rays = np.flatnonzero(required_max_gate >= 0)
    if used_rays.size == 0:
        return PolarBlockage(
            partial_output,
            cumulative_output,
            beam_output,
            terrain_output,
            support_output,
        )
    maximum_gate = int(required_max_gate[used_rays].max())
    ranges = np.asarray(range_m[: maximum_gate + 1], dtype="float64")
    ray_azimuth = np.asarray(azimuth_deg[used_rays], dtype="float64")
    ray_elevation = np.asarray(elevation_deg[used_rays], dtype="float64")
    range_matrix = np.broadcast_to(ranges, (len(used_rays), len(ranges)))
    azimuth_matrix = np.broadcast_to(ray_azimuth[:, None], range_matrix.shape)
    elevation_matrix = np.broadcast_to(ray_elevation[:, None], range_matrix.shape)
    gate_numbers = np.broadcast_to(np.arange(len(ranges)), range_matrix.shape)
    supported = gate_numbers <= required_max_gate[used_rays, None]

    geod = Geod(ellps="WGS84")
    longitude, latitude, _ = geod.fwd(
        np.full(range_matrix.size, radar_longitude_deg),
        np.full(range_matrix.size, radar_latitude_deg),
        azimuth_matrix.ravel(),
        range_matrix.ravel(),
    )
    terrain_height = terrain.sample(longitude, latitude).reshape(range_matrix.shape)
    terrain_height[~supported] = np.nan
    beam_height = beam_centre_height_m(
        range_matrix,
        elevation_matrix,
        antenna_altitude_m,
        beam_config,
    )
    beam_height[~supported] = np.nan
    radius = beam_radius_m(range_matrix, vertical_beam_width_deg)
    partial = circular_partial_blockage(terrain_height, beam_height, radius)
    partial[~supported] = np.nan
    missing_upstream = np.cumsum(supported & ~np.isfinite(partial), axis=1) > 0
    cumulative = np.maximum.accumulate(np.where(np.isfinite(partial), partial, 0.0), axis=1)
    cumulative[~supported | missing_upstream] = np.nan
    if np.any(np.isfinite(cumulative) & ((cumulative < 0) | (cumulative > 1))):
        raise RuntimeError("calculated cumulative blockage is outside [0, 1]")
    if blockage_config.maximum_usable_fraction <= blockage_config.flag_fraction:
        raise RuntimeError("blockage thresholds are inconsistent")

    columns = slice(0, maximum_gate + 1)
    partial_output[used_rays, columns] = partial
    cumulative_output[used_rays, columns] = cumulative
    beam_output[used_rays, columns] = beam_height
    terrain_output[used_rays, columns] = terrain_height
    support_output[used_rays, columns] = supported.astype("uint8")
    return PolarBlockage(
        partial=partial_output,
        cumulative=cumulative_output,
        beam_height_m=beam_output,
        terrain_height_m=terrain_output,
        support_mask=support_output,
    )
