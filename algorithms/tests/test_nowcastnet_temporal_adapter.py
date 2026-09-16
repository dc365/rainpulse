from __future__ import annotations

import numpy as np
import pytest

from rainpulse_algo.nowcast.temporal_adapter import adapt_members_to_six_minutes


def _moving_square(offset: int) -> np.ndarray:
    field = np.zeros((32, 32), dtype="float32")
    field[10:15, 5 + offset : 10 + offset] = 20.0
    return field


def _constant_motion(left: np.ndarray, right: np.ndarray, valid: np.ndarray) -> np.ndarray:
    del left, right, valid
    return np.stack((np.zeros((32, 32), dtype="float32"), np.ones((32, 32), dtype="float32") * 2))


def test_six_minute_adapter_preserves_every_native_member_field() -> None:
    analysis = _moving_square(0)
    native = np.stack([_moving_square(index * 2) for index in range(1, 13)], axis=0)
    members = np.stack((native, native + 1.0), axis=0)
    adapted = adapt_members_to_six_minutes(
        analysis,
        np.ones_like(analysis, dtype="uint8"),
        members,
        np.ones_like(members, dtype="uint8"),
        motion_estimator=_constant_motion,
    )

    assert adapted.rain_rate_mm_h.shape == (2, 20, 32, 32)
    assert [frame.lead_minutes for frame in adapted.frames] == list(range(6, 121, 6))
    assert adapted.frames[0].frame_kind == "derived"
    assert adapted.frames[0].source_leads == (0, 10)
    assert adapted.frames[4].frame_kind == "native"
    for native_index, output_index in zip(range(2, 12, 3), range(4, 20, 5), strict=True):
        np.testing.assert_array_equal(
            adapted.rain_rate_mm_h[:, output_index], members[:, native_index]
        )
        np.testing.assert_array_equal(adapted.valid_mask[:, output_index], 1)


@pytest.mark.parametrize("midpoint_space", ["log", "rain"])
def test_six_minute_adapter_keeps_missing_cells_missing(midpoint_space: str) -> None:
    analysis = _moving_square(0)
    native = np.stack([_moving_square(index) for index in range(1, 13)], axis=0)[np.newaxis, ...]
    valid = np.ones_like(native, dtype="uint8")
    valid[:, :, :8, :] = 0
    native[:, :, :8, :] = np.nan
    analysis_valid = np.ones_like(analysis, dtype="uint8")
    analysis_valid[:8, :] = 0
    analysis[:8, :] = np.nan

    adapted = adapt_members_to_six_minutes(
        analysis,
        analysis_valid,
        native,
        valid,
        motion_estimator=_constant_motion,
        midpoint_space=midpoint_space,
    )

    assert np.all(adapted.valid_mask[:, :, :6, :] == 0)
    assert np.all(np.isnan(adapted.rain_rate_mm_h[:, :, :6, :]))


def test_insufficient_motion_support_publishes_no_derived_values() -> None:
    analysis = np.zeros((16, 16), dtype="float32")
    native = np.zeros((1, 12, 16, 16), dtype="float32")
    analysis_valid = np.zeros_like(analysis, dtype="uint8")
    analysis_valid[0, 0] = 1
    native_valid = np.broadcast_to(analysis_valid, native.shape).copy()
    adapted = adapt_members_to_six_minutes(
        analysis,
        analysis_valid,
        native,
        native_valid,
    )

    for index, frame in enumerate(adapted.frames):
        if frame.frame_kind == "derived":
            assert np.all(adapted.valid_mask[:, index] == 0)
        else:
            np.testing.assert_array_equal(
                adapted.valid_mask[:, index], native_valid[:, frame.lead_minutes // 10 - 1]
            )


def test_linear_rain_candidate_only_changes_derived_blending() -> None:
    analysis = np.full((16, 16), 40, dtype="float32")
    native = np.zeros((1, 1, 16, 16), dtype="float32")
    valid = np.ones_like(native, dtype="uint8")

    def motion(left, right, mask):
        return np.zeros((2, 16, 16), dtype="float32")

    legacy = adapt_members_to_six_minutes(
        analysis, valid[0, 0], native, valid, native_leads=(10,), motion_estimator=motion
    )
    candidate = adapt_members_to_six_minutes(
        analysis,
        valid[0, 0],
        native,
        valid,
        native_leads=(10,),
        motion_estimator=motion,
        midpoint_space="rain",
    )
    np.testing.assert_allclose(legacy.rain_rate_mm_h[:, 0], 41**0.4 - 1, rtol=1e-6)
    np.testing.assert_allclose(candidate.rain_rate_mm_h[:, 0], 16, rtol=1e-6)
    assert candidate.frames[0].derivation != legacy.frames[0].derivation
