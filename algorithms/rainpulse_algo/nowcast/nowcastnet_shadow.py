from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from pathlib import Path

import numpy as np
import yaml


class NowcastNetShadowConfigError(ValueError):
    """Raised when the Fujian shadow adapter configuration is ambiguous."""


@dataclass(frozen=True)
class FixedROI:
    y_start: int
    x_start: int
    height: int
    width: int

    @property
    def shape(self) -> tuple[int, int]:
        return (self.height, self.width)


@dataclass(frozen=True)
class ShadowActivation:
    input_probe_enabled: bool
    inference_enabled: bool
    product_publication_enabled: bool
    operational_eligible: bool
    spatial_shape_validated: bool


@dataclass(frozen=True)
class NowcastNetShadowProfile:
    profile_version: str
    source_model_profile: str
    grid_id: str
    grid_config_version: str
    input_frames: int
    issue_cadence_minutes: int
    timestep_minutes: int
    output_lead_minutes: tuple[int, ...]
    missing_policy: str
    spatial_multiple: int
    roi: FixedROI
    activation: ShadowActivation


@dataclass(frozen=True)
class ShadowInput:
    eligible: bool
    reason: str | None
    issue_time: datetime
    frame_times: tuple[datetime, ...]
    rain_rate_mm_h: np.ndarray | None
    valid_mask: np.ndarray | None
    roi: FixedROI
    common_valid_ratio: float


def load_nowcastnet_shadow_profile(path: str | Path) -> NowcastNetShadowProfile:
    profile_path = Path(path)
    try:
        raw = yaml.safe_load(profile_path.read_text(encoding="utf-8"))
        protocol = raw["protocol"]
        roi = raw["fixed_roi"]
        activation = raw["activation"]
        profile = NowcastNetShadowProfile(
            profile_version=str(raw["profile_version"]),
            source_model_profile=str(raw["source_model_profile"]),
            grid_id=str(raw["grid_id"]),
            grid_config_version=str(raw["grid_config_version"]),
            input_frames=int(protocol["input_frames"]),
            issue_cadence_minutes=int(
                protocol.get("issue_cadence_minutes", protocol["timestep_minutes"])
            ),
            timestep_minutes=int(protocol["timestep_minutes"]),
            output_lead_minutes=tuple(
                int(value) for value in protocol["output_lead_minutes"]
            ),
            missing_policy=str(protocol["missing_policy"]),
            spatial_multiple=int(protocol["spatial_multiple"]),
            roi=FixedROI(
                y_start=int(roi["y_start"]),
                x_start=int(roi["x_start"]),
                height=int(roi["height"]),
                width=int(roi["width"]),
            ),
            activation=ShadowActivation(
                input_probe_enabled=bool(activation["input_probe_enabled"]),
                inference_enabled=bool(activation["inference_enabled"]),
                product_publication_enabled=bool(
                    activation["product_publication_enabled"]
                ),
                operational_eligible=bool(activation["operational_eligible"]),
                spatial_shape_validated=bool(
                    activation["spatial_shape_validated"]
                ),
            ),
        )
    except (OSError, KeyError, TypeError, ValueError, yaml.YAMLError) as exc:
        raise NowcastNetShadowConfigError(
            f"invalid NowcastNet shadow profile {profile_path}: {exc}"
        ) from exc
    if not isinstance(raw, dict) or raw.get("schema_version") != "1.0":
        raise NowcastNetShadowConfigError("unsupported NowcastNet shadow schema")
    _validate_profile(profile)
    return profile


def _validate_profile(profile: NowcastNetShadowProfile) -> None:
    if (
        not profile.profile_version
        or profile.source_model_profile != "rp026-nowcastnet-offline-v1"
    ):
        raise NowcastNetShadowConfigError(
            "shadow profile must retain the frozen RP-026 parent"
        )
    if (
        profile.grid_id != "fuzhou_118_123_25_27_0p01deg_v1"
        or profile.grid_config_version != "fuzhou-grid-0p01deg-v1"
    ):
        raise NowcastNetShadowConfigError(
            "shadow profile must bind the frozen Fujian grid"
        )
    if profile.input_frames != 9 or profile.timestep_minutes != 10:
        raise NowcastNetShadowConfigError(
            "NowcastNet shadow input must remain 9 x 10 minutes"
        )
    if (
        profile.issue_cadence_minutes < 1
        or 60 % profile.issue_cadence_minutes
        or profile.timestep_minutes % profile.issue_cadence_minutes
    ):
        raise NowcastNetShadowConfigError(
            "issue cadence must divide both one hour and the model input stride"
        )
    if profile.output_lead_minutes != tuple(range(10, 121, 10)):
        raise NowcastNetShadowConfigError(
            "main-workspace shadow leads must be +10..+120 minutes"
        )
    if profile.missing_policy != "reject_any_missing":
        raise NowcastNetShadowConfigError(
            "shadow input cannot fill missing radar coverage"
        )
    if profile.spatial_multiple < 1:
        raise NowcastNetShadowConfigError("spatial multiple must be positive")
    roi = profile.roi
    if min(roi.y_start, roi.x_start) < 0 or min(roi.height, roi.width) < 1:
        raise NowcastNetShadowConfigError("fixed ROI coordinates are invalid")
    if roi.height % profile.spatial_multiple or roi.width % profile.spatial_multiple:
        raise NowcastNetShadowConfigError(
            "fixed ROI must use the configured spatial multiple"
        )
    if (
        profile.activation.product_publication_enabled
        or profile.activation.operational_eligible
    ):
        raise NowcastNetShadowConfigError(
            "Fujian NowcastNet shadow cannot publish operational products"
        )
    if (
        not profile.activation.input_probe_enabled
        and not profile.activation.inference_enabled
    ):
        raise NowcastNetShadowConfigError("shadow profile has no enabled action")
    if (
        profile.activation.inference_enabled
        and not profile.activation.input_probe_enabled
    ):
        raise NowcastNetShadowConfigError(
            "shadow inference requires the input probe gate"
        )
    if (
        profile.activation.inference_enabled
        and not profile.activation.spatial_shape_validated
    ):
        raise NowcastNetShadowConfigError(
            "inference requires a GPU-validated fixed spatial shape"
        )


def cadence_aligned(value: datetime, cadence_minutes: int) -> bool:
    """Return whether an offset-aware timestamp lies on an exact UTC cadence."""
    at = _utc(value)
    if cadence_minutes < 1 or at.second or at.microsecond:
        return False
    return int(at.timestamp() // 60) % cadence_minutes == 0


def required_frame_times(
    issue_time: datetime,
    *,
    input_frames: int = 9,
    timestep_minutes: int = 10,
    issue_cadence_minutes: int | None = None,
) -> tuple[datetime, ...]:
    issue = _utc(issue_time)
    issue_cadence = (
        timestep_minutes
        if issue_cadence_minutes is None
        else issue_cadence_minutes
    )
    if input_frames < 1 or timestep_minutes < 1:
        raise NowcastNetShadowConfigError(
            "input frame count and timestep must be positive"
        )
    if not cadence_aligned(issue, issue_cadence):
        raise NowcastNetShadowConfigError("issue time is not on the issue cadence")
    step = timedelta(minutes=timestep_minutes)
    return tuple(
        issue - step * offset for offset in range(input_frames - 1, -1, -1)
    )


def prepare_shadow_input(
    frame_times: Sequence[datetime | np.datetime64],
    rain_rate_mm_h: np.ndarray,
    valid_mask: np.ndarray,
    *,
    issue_time: datetime,
    profile: NowcastNetShadowProfile,
) -> ShadowInput:
    times = tuple(_coerce_time(value) for value in frame_times)
    rate = np.asarray(rain_rate_mm_h, dtype="float32")
    valid = np.asarray(valid_mask)
    issue = _utc(issue_time)
    if rate.ndim != 3 or valid.shape != rate.shape or len(times) != rate.shape[0]:
        raise NowcastNetShadowConfigError(
            "shadow source arrays must be time x y x"
        )
    if np.any((valid != 0) & (valid != 1)):
        raise NowcastNetShadowConfigError("shadow valid mask is not binary")
    if len(set(times)) != len(times):
        raise NowcastNetShadowConfigError(
            "shadow source times must be unique"
        )

    required = required_frame_times(
        issue,
        input_frames=profile.input_frames,
        timestep_minutes=profile.timestep_minutes,
        issue_cadence_minutes=profile.issue_cadence_minutes,
    )
    index = {value: position for position, value in enumerate(times)}
    if any(value not in index for value in required):
        return _ineligible(
            profile, issue, required, "missing_required_frame"
        )

    selected = np.asarray([index[value] for value in required], dtype="int64")
    chosen_rate = rate[selected]
    chosen_valid = valid[selected]
    roi = profile.roi
    y_end = roi.y_start + roi.height
    x_end = roi.x_start + roi.width
    if y_end > chosen_rate.shape[1] or x_end > chosen_rate.shape[2]:
        return _ineligible(
            profile, issue, required, "fixed_roi_outside_grid"
        )
    cropped_rate = np.ascontiguousarray(
        chosen_rate[:, roi.y_start:y_end, roi.x_start:x_end],
        dtype="float32",
    )
    cropped_valid = np.ascontiguousarray(
        chosen_valid[:, roi.y_start:y_end, roi.x_start:x_end],
        dtype="uint8",
    )
    common_valid = np.all(cropped_valid == 1, axis=0)
    common_ratio = float(np.mean(common_valid))
    if (
        profile.missing_policy == "reject_any_missing"
        and not np.all(cropped_valid == 1)
    ):
        return ShadowInput(
            eligible=False,
            reason="fixed_roi_has_missing_cells",
            issue_time=issue,
            frame_times=required,
            rain_rate_mm_h=None,
            valid_mask=None,
            roi=roi,
            common_valid_ratio=common_ratio,
        )
    if np.any(~np.isfinite(cropped_rate)) or np.any(cropped_rate < 0):
        return ShadowInput(
            eligible=False,
            reason="invalid_rain_rate",
            issue_time=issue,
            frame_times=required,
            rain_rate_mm_h=None,
            valid_mask=None,
            roi=roi,
            common_valid_ratio=common_ratio,
        )
    if not profile.activation.spatial_shape_validated:
        return ShadowInput(
            eligible=False,
            reason="spatial_shape_not_validated",
            issue_time=issue,
            frame_times=required,
            rain_rate_mm_h=cropped_rate,
            valid_mask=cropped_valid,
            roi=roi,
            common_valid_ratio=common_ratio,
        )
    if not profile.activation.inference_enabled:
        return ShadowInput(
            eligible=False,
            reason="shadow_inference_disabled",
            issue_time=issue,
            frame_times=required,
            rain_rate_mm_h=cropped_rate,
            valid_mask=cropped_valid,
            roi=roi,
            common_valid_ratio=common_ratio,
        )
    return ShadowInput(
        eligible=True,
        reason=None,
        issue_time=issue,
        frame_times=required,
        rain_rate_mm_h=cropped_rate,
        valid_mask=cropped_valid,
        roi=roi,
        common_valid_ratio=common_ratio,
    )


def probe_fixed_roi(
    valid_mask: np.ndarray,
    *,
    roi: FixedROI,
) -> dict[str, float | int | bool]:
    valid = np.asarray(valid_mask)
    if valid.ndim != 3 or np.any((valid != 0) & (valid != 1)):
        raise NowcastNetShadowConfigError(
            "ROI probe requires a binary time x y x mask"
        )
    y_end = roi.y_start + roi.height
    x_end = roi.x_start + roi.width
    if y_end > valid.shape[1] or x_end > valid.shape[2]:
        return {
            "inside_grid": False,
            "all_frames_complete": False,
            "common_valid_ratio": 0.0,
            "missing_cell_count": roi.height * roi.width * valid.shape[0],
        }
    cropped = valid[:, roi.y_start:y_end, roi.x_start:x_end]
    common = np.all(cropped == 1, axis=0)
    missing = int(np.count_nonzero(cropped == 0))
    return {
        "inside_grid": True,
        "all_frames_complete": missing == 0,
        "common_valid_ratio": float(np.mean(common)),
        "missing_cell_count": missing,
    }


def _ineligible(
    profile: NowcastNetShadowProfile,
    issue_time: datetime,
    required: tuple[datetime, ...],
    reason: str,
) -> ShadowInput:
    return ShadowInput(
        eligible=False,
        reason=reason,
        issue_time=issue_time,
        frame_times=required,
        rain_rate_mm_h=None,
        valid_mask=None,
        roi=profile.roi,
        common_valid_ratio=0.0,
    )


def _coerce_time(value: datetime | np.datetime64) -> datetime:
    if isinstance(value, datetime):
        return _utc(value)
    if isinstance(value, np.datetime64):
        nanoseconds = value.astype("datetime64[ns]").astype("int64")
        return datetime.fromtimestamp(
            int(nanoseconds) / 1_000_000_000,
            tz=UTC,
        )
    raise NowcastNetShadowConfigError(
        "shadow frame time type is unsupported"
    )


def _utc(value: datetime) -> datetime:
    if value.tzinfo is None:
        raise NowcastNetShadowConfigError(
            "shadow times must include a UTC offset"
        )
    return value.astimezone(UTC).replace(microsecond=0)
