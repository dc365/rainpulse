from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Literal

import yaml


class RadarGridConfigError(ValueError):
    """Raised when an RP-009 grid profile is incomplete or inconsistent."""


@dataclass(frozen=True)
class DEMConfig:
    asset_version: str
    horizontal_crs: str
    vertical_crs: str
    sampling: Literal["nearest_native_pixel"]


@dataclass(frozen=True)
class BeamGeometryConfig:
    effective_earth_radius_factor: float
    earth_radius_m: float
    unverified_vertical_datum_policy: Literal["reject", "allow_engineering_only"]


@dataclass(frozen=True)
class BlockageConfig:
    flag_fraction: float
    maximum_usable_fraction: float


@dataclass(frozen=True)
class PolarMappingConfig:
    maximum_azimuth_offset_deg: float
    maximum_range_offset_gate_fraction: float


@dataclass(frozen=True)
class HybridScanConfig:
    minimum_source_quality_index: float
    low_quality_threshold: float
    maximum_beam_height_agl_m: float
    beam_height_quality_scale_m: float
    reject_flags: tuple[str, ...]


@dataclass(frozen=True)
class RadarGridProfile:
    profile_version: str
    algorithm_version: str
    flag_definition_version: str
    grid_id: str
    grid_config_version: str
    ancillary_config_version: str
    dem: DEMConfig
    beam_geometry: BeamGeometryConfig
    blockage: BlockageConfig
    polar_mapping: PolarMappingConfig
    hybrid_scan: HybridScanConfig


def load_radar_grid_profile(path: str | Path) -> RadarGridProfile:
    profile_path = Path(path)
    raw = yaml.safe_load(profile_path.read_text())
    if not isinstance(raw, dict) or raw.get("schema_version") != "1.0":
        raise RadarGridConfigError("unsupported radar grid profile schema")
    try:
        dem = raw["dem"]
        beam = raw["beam_geometry"]
        blockage = raw["blockage"]
        mapping = raw["polar_mapping"]
        hybrid = raw["hybrid_scan"]
        if dem["sampling"] != "nearest_native_pixel":
            raise RadarGridConfigError("unsupported DEM sampling method")
        if beam["antenna_altitude_source"] != "radar_config":
            raise RadarGridConfigError("antenna altitude must come from radar configuration")
        if beam["vertical_beam_width_source"] != "radar_config":
            raise RadarGridConfigError("vertical beam width must come from radar configuration")
        if blockage["method"] != "circular_beam_partial_blockage":
            raise RadarGridConfigError("unsupported beam blockage method")
        if blockage["cumulative_rule"] != "maximum_along_ray":
            raise RadarGridConfigError("unsupported cumulative blockage rule")
        if mapping["method"] != "nearest_polar_gate":
            raise RadarGridConfigError("unsupported polar mapping method")
        if hybrid["selection"] != "lowest_usable_elevation":
            raise RadarGridConfigError("unsupported Hybrid Scan selection rule")
        profile = RadarGridProfile(
            profile_version=str(raw["profile_version"]),
            algorithm_version=str(raw["algorithm_version"]),
            flag_definition_version=str(raw["flag_definition_version"]),
            grid_id=str(raw["grid_id"]),
            grid_config_version=str(raw["grid_config_version"]),
            ancillary_config_version=str(raw["ancillary_config_version"]),
            dem=DEMConfig(
                asset_version=str(dem["asset_version"]),
                horizontal_crs=str(dem["horizontal_crs"]),
                vertical_crs=str(dem["vertical_crs"]),
                sampling=dem["sampling"],
            ),
            beam_geometry=BeamGeometryConfig(
                effective_earth_radius_factor=float(
                    beam["effective_earth_radius_factor"]
                ),
                earth_radius_m=float(beam["earth_radius_m"]),
                unverified_vertical_datum_policy=beam[
                    "unverified_vertical_datum_policy"
                ],
            ),
            blockage=BlockageConfig(
                flag_fraction=float(blockage["flag_fraction"]),
                maximum_usable_fraction=float(blockage["maximum_usable_fraction"]),
            ),
            polar_mapping=PolarMappingConfig(
                maximum_azimuth_offset_deg=float(
                    mapping["maximum_azimuth_offset_deg"]
                ),
                maximum_range_offset_gate_fraction=float(
                    mapping["maximum_range_offset_gate_fraction"]
                ),
            ),
            hybrid_scan=HybridScanConfig(
                minimum_source_quality_index=float(
                    hybrid["minimum_source_quality_index"]
                ),
                low_quality_threshold=float(hybrid["low_quality_threshold"]),
                maximum_beam_height_agl_m=float(
                    hybrid["maximum_beam_height_agl_m"]
                ),
                beam_height_quality_scale_m=float(
                    hybrid["beam_height_quality_scale_m"]
                ),
                reject_flags=tuple(str(item) for item in hybrid["reject_flags"]),
            ),
        )
    except (KeyError, TypeError, ValueError) as exc:
        if isinstance(exc, RadarGridConfigError):
            raise
        raise RadarGridConfigError(f"invalid radar grid profile {profile_path}: {exc}") from exc
    _validate_profile(profile)
    return profile


def _validate_profile(profile: RadarGridProfile) -> None:
    probabilities = (
        profile.blockage.flag_fraction,
        profile.blockage.maximum_usable_fraction,
        profile.hybrid_scan.minimum_source_quality_index,
        profile.hybrid_scan.low_quality_threshold,
    )
    if any(value < 0 or value > 1 for value in probabilities):
        raise RadarGridConfigError("radar grid probabilities must be in [0, 1]")
    if profile.blockage.flag_fraction >= profile.blockage.maximum_usable_fraction:
        raise RadarGridConfigError("blockage flag fraction must be below the usable limit")
    if profile.beam_geometry.effective_earth_radius_factor < 1:
        raise RadarGridConfigError("effective Earth radius factor must be at least one")
    if profile.beam_geometry.earth_radius_m <= 0:
        raise RadarGridConfigError("Earth radius must be positive")
    if profile.polar_mapping.maximum_azimuth_offset_deg <= 0:
        raise RadarGridConfigError("maximum azimuth offset must be positive")
    if not 0 < profile.polar_mapping.maximum_range_offset_gate_fraction <= 1:
        raise RadarGridConfigError("maximum range offset fraction must be in (0, 1]")
    if profile.hybrid_scan.maximum_beam_height_agl_m <= 0:
        raise RadarGridConfigError("maximum beam height must be positive")
    if profile.hybrid_scan.beam_height_quality_scale_m <= 0:
        raise RadarGridConfigError("beam height quality scale must be positive")
