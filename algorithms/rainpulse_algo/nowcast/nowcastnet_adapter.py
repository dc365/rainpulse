from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass
from typing import Any

import numpy as np

from .nowcastnet_profile import NowcastNetProfile


class NowcastNetInputError(ValueError):
    """Raised when input or backend output violates the RP-026 adapter boundary."""


@dataclass(frozen=True)
class PreparedNowcastNetInput:
    model_frames: np.ndarray
    clipped_pixel_count: int


@dataclass(frozen=True)
class NowcastNetResult:
    rain_rate_mm_h: np.ndarray
    valid_mask: np.ndarray
    clipped_input_pixel_count: int
    clipped_negative_output_pixel_count: int
    random_seed: int
    operational_eligible: bool = False


@dataclass(frozen=True)
class NowcastNetBatchResult:
    """Validated same-shape native forecasts for one fixed Tile Atlas batch."""

    rain_rate_mm_h: np.ndarray
    valid_mask: np.ndarray
    clipped_input_pixel_count: int
    clipped_negative_output_pixel_count: int
    random_seed: int


NowcastNetBackend = Callable[[np.ndarray, int, int], np.ndarray]


def prepare_nowcastnet_input(
    rain_rate_mm_h: np.ndarray,
    valid_mask: np.ndarray,
    *,
    profile: NowcastNetProfile,
) -> PreparedNowcastNetInput:
    rate = np.asarray(rain_rate_mm_h, dtype="float32")
    valid = np.asarray(valid_mask)
    expected_shape = (
        profile.protocol.input_frames,
        profile.protocol.input_height,
        profile.protocol.input_width,
    )
    if rate.shape != expected_shape or valid.shape != expected_shape:
        raise NowcastNetInputError(
            f"NowcastNet input shape must be {expected_shape}, got {rate.shape}/{valid.shape}"
        )
    if np.any((valid != 0) & (valid != 1)):
        raise NowcastNetInputError("NowcastNet input valid mask is not binary")
    if profile.protocol.missing_policy == "reject_any_missing" and not np.all(valid == 1):
        raise NowcastNetInputError("RP-026 NowcastNet rejects any missing input cell")
    if np.any(~np.isfinite(rate[valid == 1])) or np.any(rate[valid == 1] < 0.0):
        raise NowcastNetInputError("NowcastNet valid input rain rates are invalid")
    cap = profile.protocol.rain_rate_cap_mm_h
    clipped_count = int(np.count_nonzero((rate > cap) & (valid == 1)))
    clipped_rate = np.minimum(rate, cap).astype("float32", copy=False)
    model_frames = np.empty((*expected_shape, profile.protocol.input_channels), dtype="float32")
    model_frames[..., 0] = clipped_rate
    model_frames[..., 1] = valid
    return PreparedNowcastNetInput(
        model_frames=model_frames,
        clipped_pixel_count=clipped_count,
    )


def validate_nowcastnet_backend_output(
    values: np.ndarray,
    *,
    profile: NowcastNetProfile,
) -> np.ndarray:
    output = np.asarray(values, dtype="float32")
    expected_shape = (
        profile.protocol.ensemble_members,
        profile.protocol.output_frames,
        profile.protocol.input_height,
        profile.protocol.input_width,
    )
    if output.shape != expected_shape:
        raise NowcastNetInputError(
            f"NowcastNet backend output must be {expected_shape}, got {output.shape}"
        )
    if np.any(~np.isfinite(output)):
        raise NowcastNetInputError("NowcastNet backend output contains non-finite rain rates")
    return output


def run_nowcastnet_fields(
    rain_rate_mm_h: np.ndarray,
    valid_mask: np.ndarray,
    *,
    profile: NowcastNetProfile,
    backend: NowcastNetBackend,
    random_seed: int,
) -> NowcastNetResult:
    profile.require_offline_ready()
    if not 0 <= random_seed <= 2**32 - 1:
        raise NowcastNetInputError("NowcastNet random seed is outside uint32")
    prepared = prepare_nowcastnet_input(rain_rate_mm_h, valid_mask, profile=profile)
    try:
        values = backend(
            prepared.model_frames,
            profile.protocol.ensemble_members,
            random_seed,
        )
    except Exception as exc:
        raise NowcastNetInputError(
            f"NowcastNet backend failed: {type(exc).__name__}: {exc}"
        ) from exc
    raw_output = validate_nowcastnet_backend_output(values, profile=profile)
    negative_count = int(np.count_nonzero(raw_output < 0.0))
    if profile.protocol.output_negative_policy != "clip_to_zero_with_diagnostic":
        raise NowcastNetInputError("NowcastNet negative-output policy is unsupported")
    output = np.maximum(raw_output, 0.0).astype("float32", copy=False)
    return NowcastNetResult(
        rain_rate_mm_h=output,
        valid_mask=np.ones(output.shape, dtype="uint8"),
        clipped_input_pixel_count=prepared.clipped_pixel_count,
        clipped_negative_output_pixel_count=negative_count,
        random_seed=random_seed,
    )


def run_nowcastnet_batch_fields(
    rain_rate_mm_h: np.ndarray,
    valid_mask: np.ndarray,
    *,
    profile: NowcastNetProfile,
    backend: Any,
    random_seed: int,
) -> NowcastNetBatchResult:
    """Use an optional native backend batch while preserving the RP-026 checks.

    The public capsule's normal callable remains deliberately single-tile.  A
    backend that explicitly exposes ``infer_batch`` may process an equally
    shaped Tile Atlas batch.  Callers can catch :class:`NowcastNetInputError`
    and retry the exact same tiles serially, so GPU compatibility never
    changes output eligibility semantics.
    """

    profile.require_offline_ready()
    if not 0 <= random_seed <= 2**32 - 1:
        raise NowcastNetInputError("NowcastNet random seed is outside uint32")
    rate = np.asarray(rain_rate_mm_h, dtype="float32")
    valid = np.asarray(valid_mask, dtype="uint8")
    expected = (
        profile.protocol.input_frames,
        profile.protocol.input_height,
        profile.protocol.input_width,
    )
    if rate.ndim != 4 or rate.shape[1:] != expected or valid.shape != rate.shape:
        raise NowcastNetInputError(
            "NowcastNet batch input must be tile x "
            f"{expected}, got {rate.shape}/{valid.shape}"
        )
    if rate.shape[0] < 1:
        raise NowcastNetInputError("NowcastNet batch must contain at least one tile")
    infer = getattr(backend, "infer_batch", None)
    if not callable(infer):
        raise NowcastNetInputError("NowcastNet backend does not expose infer_batch")
    prepared = [
        prepare_nowcastnet_input(rate[index], valid[index], profile=profile)
        for index in range(rate.shape[0])
    ]
    model_frames = np.stack([item.model_frames for item in prepared], axis=0)
    try:
        raw = np.asarray(
            infer(
                model_frames,
                profile.protocol.ensemble_members,
                random_seed,
            ),
            dtype="float32",
        )
    except Exception as exc:
        raise NowcastNetInputError(
            f"NowcastNet batch backend failed: {type(exc).__name__}: {exc}"
        ) from exc
    expected_output = (
        profile.protocol.ensemble_members,
        rate.shape[0],
        profile.protocol.output_frames,
        profile.protocol.input_height,
        profile.protocol.input_width,
    )
    if raw.shape != expected_output:
        raise NowcastNetInputError(
            "NowcastNet batch backend output must be "
            f"{expected_output}, got {raw.shape}"
        )
    if np.any(~np.isfinite(raw)):
        raise NowcastNetInputError("NowcastNet batch backend output contains non-finite rain rates")
    if profile.protocol.output_negative_policy != "clip_to_zero_with_diagnostic":
        raise NowcastNetInputError("NowcastNet negative-output policy is unsupported")
    negative_count = int(np.count_nonzero(raw < 0.0))
    output = np.maximum(raw, 0.0).astype("float32", copy=False)
    return NowcastNetBatchResult(
        rain_rate_mm_h=output,
        valid_mask=np.ones(output.shape, dtype="uint8"),
        clipped_input_pixel_count=sum(item.clipped_pixel_count for item in prepared),
        clipped_negative_output_pixel_count=negative_count,
        random_seed=random_seed,
    )
