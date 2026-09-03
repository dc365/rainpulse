from __future__ import annotations

from datetime import UTC, datetime, timedelta

import pytest

from rainpulse_algo.nowcast.fujian_shadow_schedule import comparison_candidates
from rainpulse_algo.nowcast.nowcastnet_shadow import required_frame_times


def complete_catalog(issue: datetime) -> dict[datetime, object]:
    values: dict[datetime, object] = {}
    for offset in range(-120, 121, 5):
        values[issue + timedelta(minutes=offset)] = object()
    return values


def test_five_minute_issue_uses_ten_minute_native_history() -> None:
    issue = datetime(2026, 8, 28, 10, 5, tzinfo=UTC)
    catalog = complete_catalog(issue)
    candidates = comparison_candidates(
        catalog,
        target_date=issue.date(),
    )
    assert issue in candidates
    history = required_frame_times(
        issue,
        input_frames=9,
        timestep_minutes=10,
        issue_cadence_minutes=5,
    )
    assert history[0] == issue - timedelta(minutes=80)
    assert history[-1] == issue
    assert all(
        right - left == timedelta(minutes=10)
        for left, right in zip(history[:-1], history[1:], strict=True)
    )


def test_candidate_fails_closed_when_native_truth_is_missing() -> None:
    issue = datetime(2026, 8, 28, 10, 5, tzinfo=UTC)
    catalog = complete_catalog(issue)
    del catalog[issue + timedelta(minutes=70)]
    assert issue not in comparison_candidates(
        catalog,
        target_date=issue.date(),
    )


def test_issue_cadence_must_divide_model_stride() -> None:
    issue = datetime(2026, 8, 28, 10, 5, tzinfo=UTC)
    with pytest.raises(ValueError, match="divide"):
        comparison_candidates(
            complete_catalog(issue),
            target_date=issue.date(),
            issue_cadence_minutes=6,
        )
