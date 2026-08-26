from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

import yaml


class PystepsLKConfigError(ValueError):
    """Raised when a deterministic pySTEPS-LK profile is inconsistent."""


@dataclass(frozen=True)
class ModelSequenceConfig:
    minimum_frames: int
    maximum_frames: int
    timestep_minutes: int


@dataclass(frozen=True)
class LucasKanadeConfig:
    feature_detection: str
    interpolation: str
    decluster_scale_pixels: int
    opening_size_pixels: int
    outlier_stddev: float
    outlier_neighbours: int


@dataclass(frozen=True)
class MotionConfig:
    input_field: str
    method: str
    rain_threshold_dbz: float
    minimum_trackable_rain_pixels: int
    minimum_motion_features: int
    missing_buffer_pixels: int
    working_missing_fill_dbz: float
    missing_policy: str
    fallback: str
    lucas_kanade: LucasKanadeConfig


@dataclass(frozen=True)
class ExtrapolationConfig:
    method: str
    interpolation_order: int
    lead_count: int
    lead_step_minutes: int
    baselines: tuple[str, ...]


@dataclass(frozen=True)
class ConfidenceConfig:
    decay_minutes: float
    low_quality_factor: float


@dataclass(frozen=True)
class PystepsLKProfile:
    profile_version: str
    model_id: str
    model_version: str
    pysteps_version: str
    opencv_version: str
    nowcast_input_contract_version: str
    forecast_output_contract_version: str
    grid_id: str
    grid_config_version: str
    sequence: ModelSequenceConfig
    motion: MotionConfig
    extrapolation: ExtrapolationConfig
    confidence: ConfidenceConfig


def load_pysteps_lk_profile(path: str | Path) -> PystepsLKProfile:
    profile_path = Path(path)
    raw = yaml.safe_load(profile_path.read_text())
    if not isinstance(raw, dict) or raw.get("schema_version") != "1.0":
        raise PystepsLKConfigError("unsupported pySTEPS-LK profile schema")
    try:
        sequence = raw["sequence"]
        motion = raw["motion"]
        lk = motion["lucas_kanade"]
        extrapolation = raw["extrapolation"]
        confidence = raw["confidence"]
        profile = PystepsLKProfile(
            profile_version=str(raw["profile_version"]),
            model_id=str(raw["model_id"]),
            model_version=str(raw["model_version"]),
            pysteps_version=str(raw["pysteps_version"]),
            opencv_version=str(raw["opencv_version"]),
            nowcast_input_contract_version=str(raw["nowcast_input_contract_version"]),
            forecast_output_contract_version=str(raw["forecast_output_contract_version"]),
            grid_id=str(raw["grid_id"]),
            grid_config_version=str(raw["grid_config_version"]),
            sequence=ModelSequenceConfig(
                minimum_frames=int(sequence["minimum_frames"]),
                maximum_frames=int(sequence["maximum_frames"]),
                timestep_minutes=int(sequence["timestep_minutes"]),
            ),
            motion=MotionConfig(
                input_field=str(motion["input_field"]),
                method=str(motion["method"]),
                rain_threshold_dbz=float(motion["rain_threshold_dbz"]),
                minimum_trackable_rain_pixels=int(motion["minimum_trackable_rain_pixels"]),
                minimum_motion_features=int(motion.get("minimum_motion_features", 1)),
                missing_buffer_pixels=int(motion.get("missing_buffer_pixels", 0)),
                working_missing_fill_dbz=float(motion["working_missing_fill_dbz"]),
                missing_policy=str(motion["missing_policy"]),
                fallback=str(motion["fallback"]),
                lucas_kanade=LucasKanadeConfig(
                    feature_detection=str(lk["feature_detection"]),
                    interpolation=str(lk["interpolation"]),
                    decluster_scale_pixels=int(lk["decluster_scale_pixels"]),
                    opening_size_pixels=int(lk["opening_size_pixels"]),
                    outlier_stddev=float(lk["outlier_stddev"]),
                    outlier_neighbours=int(lk["outlier_neighbours"]),
                ),
            ),
            extrapolation=ExtrapolationConfig(
                method=str(extrapolation["method"]),
                interpolation_order=int(extrapolation["interpolation_order"]),
                lead_count=int(extrapolation["lead_count"]),
                lead_step_minutes=int(extrapolation["lead_step_minutes"]),
                baselines=tuple(str(value) for value in extrapolation["baselines"]),
            ),
            confidence=ConfidenceConfig(
                decay_minutes=float(confidence["decay_minutes"]),
                low_quality_factor=float(confidence["low_quality_factor"]),
            ),
        )
    except (KeyError, TypeError, ValueError) as exc:
        raise PystepsLKConfigError(f"invalid pySTEPS-LK profile {profile_path}: {exc}") from exc
    _validate(profile)
    return profile


def _validate(profile: PystepsLKProfile) -> None:
    if profile.model_id != "pysteps-lk":
        raise PystepsLKConfigError("model ID must be pysteps-lk")
    if profile.pysteps_version != "1.21.5" or profile.opencv_version != "5.0.0.93":
        raise PystepsLKConfigError("pySTEPS-LK runtime library versions are frozen")
    if profile.nowcast_input_contract_version != "1.2":
        raise PystepsLKConfigError("pySTEPS-LK requires NowcastInput contract 1.2")
    if profile.forecast_output_contract_version != "1.1":
        raise PystepsLKConfigError("pySTEPS-LK requires ForecastOutput contract 1.1")
    if profile.sequence != ModelSequenceConfig(3, 6, 5):
        raise PystepsLKConfigError("pySTEPS-LK requires 3-6 frames at five-minute steps")
    if profile.motion.input_field != "DBZH_QC":
        raise PystepsLKConfigError("pySTEPS-LK motion must consume DBZH_QC")
    if profile.motion.method != "dense_lucaskanade":
        raise PystepsLKConfigError("pySTEPS-LK motion method must be dense_lucaskanade")
    if profile.motion.minimum_trackable_rain_pixels <= 0:
        raise PystepsLKConfigError("minimum trackable rain pixels must be positive")
    if profile.motion.minimum_motion_features <= 0:
        raise PystepsLKConfigError("minimum motion features must be positive")
    if profile.motion.missing_buffer_pixels < 0:
        raise PystepsLKConfigError("missing-data buffer cannot be negative")
    supported_missing_policies = {
        "dry_floor_working_copy_preserve_advected_mask",
        "nearest_valid_buffer_preserve_advected_mask",
    }
    if profile.motion.missing_policy not in supported_missing_policies:
        raise PystepsLKConfigError("unsupported pySTEPS-LK missing-data policy")
    if (
        profile.motion.missing_policy == "nearest_valid_buffer_preserve_advected_mask"
        and profile.motion.missing_buffer_pixels <= 0
    ):
        raise PystepsLKConfigError("nearest-valid missing policy requires a positive buffer")
    if profile.motion.fallback != "zero_motion_when_insufficient_features":
        raise PystepsLKConfigError("unsupported pySTEPS-LK motion fallback")
    lk = profile.motion.lucas_kanade
    if lk.feature_detection != "shitomasi" or lk.interpolation != "idwinterp2d":
        raise PystepsLKConfigError("unsupported pySTEPS-LK Lucas-Kanade adapter")
    if min(lk.decluster_scale_pixels, lk.outlier_neighbours) <= 0:
        raise PystepsLKConfigError("Lucas-Kanade neighbourhoods must be positive")
    if lk.opening_size_pixels < 0 or lk.outlier_stddev <= 0:
        raise PystepsLKConfigError("invalid Lucas-Kanade cleanup parameters")
    if profile.extrapolation != ExtrapolationConfig(
        "semilagrangian",
        profile.extrapolation.interpolation_order,
        24,
        5,
        ("persistence", "translation"),
    ):
        raise PystepsLKConfigError("pySTEPS-LK extrapolation identity or lead times differ")
    if profile.extrapolation.interpolation_order not in {0, 1, 3}:
        raise PystepsLKConfigError("unsupported semi-Lagrangian interpolation order")
    if profile.confidence.decay_minutes <= 0:
        raise PystepsLKConfigError("confidence decay must be positive")
    if not 0 < profile.confidence.low_quality_factor <= 1:
        raise PystepsLKConfigError("low-quality confidence factor must be within (0, 1]")
