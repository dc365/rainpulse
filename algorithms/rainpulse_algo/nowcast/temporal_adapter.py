"""Traceable five-minute adaptation for native ten-minute NowcastNet output.

The public NowcastNet weights retain their original ten-minute protocol.  This
module never changes an even (native) lead; it derives only the odd five-minute
leads after the native member fields have been stitched to the target grid.
"""

from __future__ import annotations

from collections.abc import Callable, Sequence
from dataclasses import dataclass

import numpy as np
from scipy.ndimage import map_coordinates


class TemporalAdapterError(ValueError):
    """Raised when a native forecast cannot safely be adapted."""


@dataclass(frozen=True)
class AdaptedFrame:
    lead_minutes: int
    frame_kind: str
    derivation: str | None
    source_leads: tuple[int, ...]


@dataclass(frozen=True)
class AdaptedForecast:
    rain_rate_mm_h: np.ndarray
    valid_mask: np.ndarray
    confidence: np.ndarray
    frames: tuple[AdaptedFrame, ...]


DenseMotionEstimator = Callable[[np.ndarray, np.ndarray, np.ndarray], np.ndarray]


def adapt_members_to_five_minutes(
    analysis_rate: np.ndarray,
    analysis_valid: np.ndarray,
    native_members: np.ndarray,
    native_valid: np.ndarray,
    *,
    native_leads: Sequence[int] = tuple(range(10, 121, 10)),
    motion_estimator: DenseMotionEstimator | None = None,
) -> AdaptedForecast:
    """Add odd leads by bidirectional dense-flow advection in log-rain space.

    A motion field is estimated from the ensemble mean for each interval and
    reused for each member.  This keeps the member spread intact while avoiding
    a separate, unstable optical-flow estimate for every stochastic member.
    A derived value is valid only when *both* warped anchors are valid.
    """

    members = np.asarray(native_members, dtype="float32")
    member_valid = np.asarray(native_valid, dtype="uint8")
    analysis = np.asarray(analysis_rate, dtype="float32")
    analysis_mask = np.asarray(analysis_valid, dtype="uint8")
    leads = tuple(int(value) for value in native_leads)
    if members.ndim != 4 or analysis.shape != members.shape[2:]:
        raise TemporalAdapterError("native-member or analysis shape differs")
    if member_valid.shape == members.shape[1:]:
        member_valid = np.broadcast_to(member_valid[np.newaxis, ...], members.shape).copy()
    if member_valid.shape != members.shape or analysis_mask.shape != analysis.shape:
        raise TemporalAdapterError("native or analysis mask shape differs")
    if len(leads) != members.shape[1] or leads != tuple(range(10, 10 * (len(leads) + 1), 10)):
        raise TemporalAdapterError("native leads must be consecutive ten-minute values")
    _validate_rate_and_mask(analysis, analysis_mask, "analysis")
    _validate_rate_and_mask(members, member_valid, "native")

    output_leads = tuple(range(5, leads[-1] + 1, 5))
    output = np.full(
        (members.shape[0], len(output_leads), *analysis.shape),
        np.nan,
        dtype="float32",
    )
    output_valid = np.zeros(output.shape, dtype="uint8")
    confidence = np.zeros(output.shape, dtype="float32")
    metadata: list[AdaptedFrame] = []
    estimate = motion_estimator or estimate_dense_optical_flow

    anchors = np.concatenate(
        (np.broadcast_to(analysis, (members.shape[0], 1, *analysis.shape)), members), axis=1
    )
    anchor_valid = np.concatenate(
        (np.broadcast_to(analysis_mask, (members.shape[0], 1, *analysis.shape)), member_valid),
        axis=1,
    )
    anchor_leads = (0, *leads)
    for output_index, lead in enumerate(output_leads):
        if lead in leads:
            source_index = leads.index(lead)
            output[:, output_index] = members[:, source_index]
            output_valid[:, output_index] = member_valid[:, source_index]
            confidence[:, output_index] = member_valid[:, source_index]
            metadata.append(AdaptedFrame(lead, "native", None, (lead,)))
            continue

        right_index = next(index for index, value in enumerate(anchor_leads) if value > lead)
        left_index = right_index - 1
        left_lead = anchor_leads[left_index]
        right_lead = anchor_leads[right_index]
        left_mean, left_mask = _ensemble_anchor(
            anchors[:, left_index], anchor_valid[:, left_index]
        )
        right_mean, right_mask = _ensemble_anchor(
            anchors[:, right_index], anchor_valid[:, right_index]
        )
        common = left_mask & right_mask
        try:
            forward_motion = estimate(left_mean, right_mean, common)
            backward_motion = estimate(right_mean, left_mean, common)
            _validate_motion(forward_motion, analysis.shape)
            _validate_motion(backward_motion, analysis.shape)
        except (TemporalAdapterError, ValueError, RuntimeError):
            forward_motion = backward_motion = None

        if forward_motion is not None and backward_motion is not None:
            for member_index in range(members.shape[0]):
                value, valid = _bidirectional_midpoint(
                    anchors[member_index, left_index],
                    anchor_valid[member_index, left_index],
                    anchors[member_index, right_index],
                    anchor_valid[member_index, right_index],
                    forward_motion,
                    backward_motion,
                )
                output[member_index, output_index] = value
                output_valid[member_index, output_index] = valid
                confidence[member_index, output_index] = valid.astype("float32")
        metadata.append(
            AdaptedFrame(
                lead,
                "derived",
                "bidirectional-dense-optical-flow-advection-v1",
                (left_lead, right_lead),
            )
        )
    return AdaptedForecast(output, output_valid, confidence, tuple(metadata))


def estimate_dense_optical_flow(
    left: np.ndarray,
    right: np.ndarray,
    valid: np.ndarray,
) -> np.ndarray:
    """Estimate a dense Farnebäck optical-flow field on valid log-rain cells.

    Farnebäck is used here because it is available in the frozen worker image
    through OpenCV.  The result is dense and is only trusted after the caller
    reapplies the original masks during advection.
    """

    mask = np.asarray(valid, dtype=bool)
    if mask.shape != left.shape or left.shape != right.shape or np.count_nonzero(mask) < 64:
        raise TemporalAdapterError("insufficient common support for dense optical flow")
    try:
        import cv2
    except ImportError as error:  # pragma: no cover - covered by worker environment
        raise TemporalAdapterError("OpenCV dense optical flow is unavailable") from error
    left_work = np.where(mask, np.log1p(np.maximum(left, 0.0)), 0.0).astype("float32")
    right_work = np.where(mask, np.log1p(np.maximum(right, 0.0)), 0.0).astype("float32")
    scale = max(float(np.max(left_work[mask])), float(np.max(right_work[mask])), 1e-6)
    left_image = np.clip(left_work * (255.0 / scale), 0, 255).astype("uint8")
    right_image = np.clip(right_work * (255.0 / scale), 0, 255).astype("uint8")
    flow = cv2.calcOpticalFlowFarneback(
        left_image,
        right_image,
        None,
        pyr_scale=0.5,
        levels=3,
        winsize=21,
        iterations=3,
        poly_n=5,
        poly_sigma=1.2,
        flags=0,
    )
    # OpenCV returns x/y displacement; RainPulse stores y/x array coordinates.
    return np.stack((flow[..., 1], flow[..., 0]), axis=0).astype("float32", copy=False)


def _ensemble_anchor(values: np.ndarray, valid: np.ndarray) -> tuple[np.ndarray, np.ndarray]:
    support = np.asarray(valid, dtype=bool) & np.isfinite(values)
    count = np.sum(support, axis=0)
    result = np.full(values.shape[1:], np.nan, dtype="float32")
    if np.any(count):
        total = np.sum(np.where(support, values, 0.0), axis=0, dtype="float64")
        result[count > 0] = (total[count > 0] / count[count > 0]).astype("float32")
    return result, count > 0


def _bidirectional_midpoint(
    left: np.ndarray,
    left_valid: np.ndarray,
    right: np.ndarray,
    right_valid: np.ndarray,
    forward_motion: np.ndarray,
    backward_motion: np.ndarray,
) -> tuple[np.ndarray, np.ndarray]:
    left_value, left_support = _warp_log_field(left, left_valid, forward_motion, 0.5)
    right_value, right_support = _warp_log_field(right, right_valid, backward_motion, 0.5)
    valid = left_support & right_support & np.isfinite(left_value) & np.isfinite(right_value)
    result = np.full(left.shape, np.nan, dtype="float32")
    result[valid] = np.expm1((left_value[valid] + right_value[valid]) * 0.5).astype("float32")
    result[valid] = np.maximum(result[valid], 0.0)
    return result, valid.astype("uint8")


def _warp_log_field(
    values: np.ndarray,
    valid: np.ndarray,
    motion: np.ndarray,
    fraction: float,
) -> tuple[np.ndarray, np.ndarray]:
    y, x = np.indices(values.shape, dtype="float32")
    coordinates = np.stack((y - fraction * motion[0], x - fraction * motion[1]))
    source = np.log1p(np.where(valid == 1, np.maximum(values, 0.0), 0.0)).astype("float32")
    warped = map_coordinates(source, coordinates, order=1, mode="constant", cval=np.nan)
    support = map_coordinates(
        (valid == 1).astype("float32"), coordinates, order=0, mode="constant", cval=0.0
    ) >= 0.5
    return warped.astype("float32", copy=False), support


def _validate_rate_and_mask(values: np.ndarray, valid: np.ndarray, label: str) -> None:
    if np.any((valid != 0) & (valid != 1)):
        raise TemporalAdapterError(f"{label} valid mask is not binary")
    if np.any(~np.isfinite(values[valid == 1])) or np.any(values[valid == 1] < 0):
        raise TemporalAdapterError(f"{label} valid rain rates are invalid")


def _validate_motion(motion: np.ndarray, shape: tuple[int, int]) -> None:
    if np.asarray(motion).shape != (2, *shape) or not np.all(np.isfinite(motion)):
        raise TemporalAdapterError("dense optical-flow output is invalid")
