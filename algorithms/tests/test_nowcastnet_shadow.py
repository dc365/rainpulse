from __future__ import annotations

from datetime import UTC, datetime, timedelta
from pathlib import Path

import numpy as np
import pytest

from rainpulse_algo.nowcast.nowcastnet_shadow import (
    FixedROI,
    NowcastNetShadowConfigError,
    NowcastNetShadowProfile,
    ShadowActivation,
    cadence_aligned,
    load_nowcastnet_shadow_profile,
    prepare_shadow_input,
    probe_fixed_roi,
    required_frame_times,
)


def profile(
    *,
    validated: bool = True,
    enabled: bool = True,
    issue_cadence_minutes: int = 5,
) -> NowcastNetShadowProfile:
    return NowcastNetShadowProfile(
        profile_version="fujian-nowcastnet-shadow-v1",
        source_model_profile="rp026-nowcastnet-offline-v1",
        grid_id="fuzhou_118_123_25_27_0p01deg_v1",
        grid_config_version="fuzhou-grid-0p01deg-v1",
        input_frames=9,
        issue_cadence_minutes=issue_cadence_minutes,
        timestep_minutes=10,
        output_lead_minutes=tuple(range(10, 121, 10)),
        missing_policy="reject_any_missing",
        spatial_multiple=32,
        roi=FixedROI(y_start=0, x_start=0, height=32, width=64),
        activation=ShadowActivation(
            input_probe_enabled=True,
            inference_enabled=enabled,
            product_publication_enabled=False,
            operational_eligible=False,
            spatial_shape_validated=validated,
        ),
    )


def sequence(
    *,
    issue: datetime | None = None,
) -> tuple[list[datetime], np.ndarray, np.ndarray, datetime]:
    issue = issue or datetime(2026, 9, 1, 2, 0, tzinfo=UTC)
    times = [
        issue - timedelta(minutes=5 * offset)
        for offset in range(20, -1, -1)
    ]
    rain = np.ones((len(times), 64, 96), dtype="float32")
    valid = np.ones_like(rain, dtype="uint8")
    return times, rain, valid, issue


def test_required_frames_select_exact_ten_minute_cadence_without_interpolation() -> None:
    times, rain, valid, issue = sequence()
    result = prepare_shadow_input(
        times,
        rain,
        valid,
        issue_time=issue,
        profile=profile(),
    )
    assert result.eligible is True
    assert result.rain_rate_mm_h is not None
    assert result.rain_rate_mm_h.shape == (9, 32, 64)
    assert result.frame_times == required_frame_times(
        issue,
        issue_cadence_minutes=5,
    )
    assert all(
        (right - left) == timedelta(minutes=10)
        for left, right in zip(
            result.frame_times[:-1],
            result.frame_times[1:],
            strict=True,
        )
    )


def test_five_minute_issue_uses_latest_frame_with_ten_minute_input_stride() -> None:
    issue = datetime(2026, 9, 1, 2, 5, tzinfo=UTC)
    times, rain, valid, issue = sequence(issue=issue)
    result = prepare_shadow_input(
        times,
        rain,
        valid,
        issue_time=issue,
        profile=profile(),
    )
    assert result.eligible is True
    assert result.frame_times[-1] == issue
    assert result.frame_times[0] == issue - timedelta(minutes=80)
    assert all(value.minute % 10 == 5 for value in result.frame_times)


def test_issue_cadence_rejects_non_aligned_time() -> None:
    issue = datetime(2026, 9, 1, 2, 3, tzinfo=UTC)
    assert cadence_aligned(issue, 5) is False
    with pytest.raises(NowcastNetShadowConfigError, match="issue cadence"):
        required_frame_times(issue, issue_cadence_minutes=5)


def test_profile_rejects_issue_cadence_that_does_not_divide_model_stride() -> None:
    with pytest.raises(NowcastNetShadowConfigError, match="issue cadence"):
        from rainpulse_algo.nowcast.nowcastnet_shadow import _validate_profile

        _validate_profile(profile(issue_cadence_minutes=6))


def test_missing_required_time_fails_closed() -> None:
    times, rain, valid, issue = sequence()
    remove = times.index(issue - timedelta(minutes=40))
    times.pop(remove)
    rain = np.delete(rain, remove, axis=0)
    valid = np.delete(valid, remove, axis=0)
    result = prepare_shadow_input(
        times,
        rain,
        valid,
        issue_time=issue,
        profile=profile(),
    )
    assert result.eligible is False
    assert result.reason == "missing_required_frame"


def test_missing_roi_cell_is_not_converted_to_no_rain() -> None:
    times, rain, valid, issue = sequence()
    valid[-1, 3, 4] = 0
    rain[-1, 3, 4] = np.nan
    result = prepare_shadow_input(
        times,
        rain,
        valid,
        issue_time=issue,
        profile=profile(),
    )
    assert result.eligible is False
    assert result.reason == "fixed_roi_has_missing_cells"
    assert result.rain_rate_mm_h is None
    assert result.common_valid_ratio < 1


def test_unvalidated_shape_returns_probe_data_but_cannot_infer() -> None:
    times, rain, valid, issue = sequence()
    result = prepare_shadow_input(
        times,
        rain,
        valid,
        issue_time=issue,
        profile=profile(validated=False, enabled=False),
    )
    assert result.eligible is False
    assert result.reason == "spatial_shape_not_validated"
    assert result.rain_rate_mm_h is not None
    assert result.common_valid_ratio == 1


def test_profile_rejects_inference_before_shape_validation() -> None:
    with pytest.raises(NowcastNetShadowConfigError):
        from rainpulse_algo.nowcast.nowcastnet_shadow import _validate_profile

        _validate_profile(profile(validated=False, enabled=True))


def test_roi_probe_reports_exact_missing_count() -> None:
    valid = np.ones((9, 64, 96), dtype="uint8")
    valid[0, 0, 0] = 0
    report = probe_fixed_roi(
        valid,
        roi=FixedROI(0, 0, 32, 64),
    )
    assert report["inside_grid"] is True
    assert report["all_frames_complete"] is False
    assert report["missing_cell_count"] == 1


def test_repository_shadow_profile_is_probe_only() -> None:
    repository_root = Path(__file__).resolve().parents[2]
    loaded = load_nowcastnet_shadow_profile(
        repository_root
        / "configs/nowcast/fujian-nowcastnet-shadow-v1.yaml"
    )
    assert loaded.roi.shape == (192, 480)
    assert loaded.issue_cadence_minutes == 5
    assert loaded.timestep_minutes == 10
    assert loaded.activation.input_probe_enabled is True
    assert loaded.activation.inference_enabled is False
    assert loaded.activation.operational_eligible is False
