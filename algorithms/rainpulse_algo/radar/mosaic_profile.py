from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

import yaml

from .qc_flags import PHASE1_HARD_REJECT_FLAGS


class RadarMosaicConfigError(ValueError):
    """Raised when a radar mosaic profile is incomplete or inconsistent."""


@dataclass(frozen=True)
class MosaicAlignmentConfig:
    step_seconds: int
    maximum_absolute_offset_seconds: int
    minimum_contributors: int
    minimum_operational_contributors: int
    expected_radar_ids: tuple[str, ...]


@dataclass(frozen=True)
class MosaicFusionConfig:
    minimum_quality_index: float
    similar_quality_max_difference: float
    quality_weight_power: float
    low_quality_threshold: float
    blended_source_code: int
    reject_flags: tuple[str, ...]


@dataclass(frozen=True)
class RadarMosaicProfile:
    profile_version: str
    algorithm_version: str
    analysis_cycle_version: str
    flag_definition_version: str
    grid_id: str
    grid_config_version: str
    alignment: MosaicAlignmentConfig
    fusion: MosaicFusionConfig


def load_radar_mosaic_profile(path: str | Path) -> RadarMosaicProfile:
    profile_path = Path(path)
    raw = yaml.safe_load(profile_path.read_text())
    if not isinstance(raw, dict) or raw.get("schema_version") != "1.0":
        raise RadarMosaicConfigError("unsupported radar mosaic profile schema")
    try:
        alignment = raw["alignment"]
        fusion = raw["fusion"]
        if alignment["selection"] != "closest_volume_end_to_analysis_time":
            raise RadarMosaicConfigError("unsupported radar alignment selection")
        if fusion["method"] != "highest_qi_then_linear_z_blend":
            raise RadarMosaicConfigError("unsupported radar mosaic fusion method")
        profile = RadarMosaicProfile(
            profile_version=str(raw["profile_version"]),
            algorithm_version=str(raw["algorithm_version"]),
            analysis_cycle_version=str(raw["analysis_cycle_version"]),
            flag_definition_version=str(raw["flag_definition_version"]),
            grid_id=str(raw["grid_id"]),
            grid_config_version=str(raw["grid_config_version"]),
            alignment=MosaicAlignmentConfig(
                step_seconds=int(alignment["step_seconds"]),
                maximum_absolute_offset_seconds=int(
                    alignment["maximum_absolute_offset_seconds"]
                ),
                minimum_contributors=int(alignment["minimum_contributors"]),
                minimum_operational_contributors=int(
                    alignment["minimum_operational_contributors"]
                ),
                expected_radar_ids=tuple(
                    str(item) for item in alignment["expected_radar_ids"]
                ),
            ),
            fusion=MosaicFusionConfig(
                minimum_quality_index=float(fusion["minimum_quality_index"]),
                similar_quality_max_difference=float(
                    fusion["similar_quality_max_difference"]
                ),
                quality_weight_power=float(fusion["quality_weight_power"]),
                low_quality_threshold=float(fusion["low_quality_threshold"]),
                blended_source_code=int(fusion["blended_source_code"]),
                reject_flags=tuple(str(item) for item in fusion["reject_flags"]),
            ),
        )
    except (KeyError, TypeError, ValueError) as exc:
        if isinstance(exc, RadarMosaicConfigError):
            raise
        raise RadarMosaicConfigError(
            f"invalid radar mosaic profile {profile_path}: {exc}"
        ) from exc
    _validate_profile(profile)
    return profile


def _validate_profile(profile: RadarMosaicProfile) -> None:
    alignment = profile.alignment
    fusion = profile.fusion
    if alignment.step_seconds <= 0:
        raise RadarMosaicConfigError("analysis step must be positive")
    if alignment.maximum_absolute_offset_seconds <= 0:
        raise RadarMosaicConfigError("alignment tolerance must be positive")
    if alignment.minimum_contributors <= 0:
        raise RadarMosaicConfigError("minimum contributors must be positive")
    if alignment.minimum_operational_contributors < alignment.minimum_contributors:
        raise RadarMosaicConfigError(
            "operational contributor minimum cannot be below engineering minimum"
        )
    if len(alignment.expected_radar_ids) != len(set(alignment.expected_radar_ids)):
        raise RadarMosaicConfigError("expected radar IDs must be unique")
    probabilities = (
        fusion.minimum_quality_index,
        fusion.similar_quality_max_difference,
        fusion.low_quality_threshold,
    )
    if any(value < 0 or value > 1 for value in probabilities):
        raise RadarMosaicConfigError("mosaic probabilities must be in [0, 1]")
    if fusion.quality_weight_power < 1:
        raise RadarMosaicConfigError("quality weight power must be at least one")
    if fusion.blended_source_code != 65535:
        raise RadarMosaicConfigError("the reserved blended source code must be 65535")
    reject_flags = set(fusion.reject_flags)
    missing = sorted(PHASE1_HARD_REJECT_FLAGS - reject_flags)
    if missing:
        raise RadarMosaicConfigError(
            "mosaic reject_flags omit Phase-1 hard rejects: " + ",".join(missing)
        )
