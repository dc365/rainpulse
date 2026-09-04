from __future__ import annotations

import numpy as np

from rainpulse_algo.nowcast.temporal_adapter import adapt_members_to_five_minutes


def _moving_square(offset: int) -> np.ndarray:
    field = np.zeros((32, 32), dtype="float32")
    field[10:15, 5 + offset : 10 + offset] = 20.0
    return field


def _constant_motion(left: np.ndarray, right: np.ndarray, valid: np.ndarray) -> np.ndarray:
    del left, right, valid
    return np.stack((np.zeros((32, 32), dtype="float32"), np.ones((32, 32), dtype="float32") * 2))


def test_five_minute_adapter_preserves_every_native_member_field() -> None:
    analysis = _moving_square(0)
    native = np.stack([_moving_square(index * 2) for index in range(1, 13)], axis=0)
    members = np.stack((native, native + 1.0), axis=0)
    adapted = adapt_members_to_five_minutes(
        analysis,
        np.ones_like(analysis, dtype="uint8"),
        members,
        np.ones_like(members, dtype="uint8"),
        motion_estimator=_constant_motion,
    )

    assert adapted.rain_rate_mm_h.shape == (2, 24, 32, 32)
    assert [frame.lead_minutes for frame in adapted.frames] == list(range(5, 121, 5))
    assert adapted.frames[0].frame_kind == "derived"
    assert adapted.frames[0].source_leads == (0, 10)
    assert adapted.frames[1].frame_kind == "native"
    for native_index, output_index in enumerate(range(1, 24, 2)):
        np.testing.assert_array_equal(
            adapted.rain_rate_mm_h[:, output_index], members[:, native_index]
        )
        np.testing.assert_array_equal(adapted.valid_mask[:, output_index], 1)


def test_five_minute_adapter_keeps_missing_cells_missing() -> None:
    analysis = _moving_square(0)
    native = np.stack([_moving_square(index) for index in range(1, 13)], axis=0)[np.newaxis, ...]
    valid = np.ones_like(native, dtype="uint8")
    valid[:, :, :8, :] = 0
    native[:, :, :8, :] = np.nan
    analysis_valid = np.ones_like(analysis, dtype="uint8")
    analysis_valid[:8, :] = 0
    analysis[:8, :] = np.nan

    adapted = adapt_members_to_five_minutes(
        analysis,
        analysis_valid,
        native,
        valid,
        motion_estimator=_constant_motion,
    )

    assert np.all(adapted.valid_mask[:, :, :6, :] == 0)
    assert np.all(np.isnan(adapted.rain_rate_mm_h[:, :, :6, :]))


def test_insufficient_motion_support_publishes_no_derived_values() -> None:
    analysis = np.zeros((16, 16), dtype="float32")
    native = np.zeros((1, 12, 16, 16), dtype="float32")
    analysis_valid = np.zeros_like(analysis, dtype="uint8")
    analysis_valid[0, 0] = 1
    native_valid = np.broadcast_to(analysis_valid, native.shape).copy()
    adapted = adapt_members_to_five_minutes(
        analysis,
        analysis_valid,
        native,
        native_valid,
    )

    assert np.all(adapted.valid_mask[:, 0::2] == 0)
    assert np.all(adapted.valid_mask[:, 1::2] == native_valid)
