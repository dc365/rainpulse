from __future__ import annotations

from collections.abc import Mapping, Sequence
from datetime import date, datetime, timedelta
from typing import TypeVar

from .nowcastnet_shadow import cadence_aligned, required_frame_times

T = TypeVar("T")


def comparison_candidates(
    by_time: Mapping[datetime, T],
    *,
    target_date: date,
    issue_cadence_minutes: int = 5,
    input_frames: int = 9,
    input_timestep_minutes: int = 10,
    output_lead_minutes: Sequence[int] = tuple(range(10, 121, 10)),
) -> list[datetime]:
    """Return strict five-minute issue cycles without changing model cadence.

    A candidate is accepted only when all native ten-minute input frames and all
    requested verification frames exist. No temporal interpolation or missing
    frame fill is performed.
    """
    if issue_cadence_minutes < 1:
        raise ValueError("issue cadence must be positive")
    if input_frames < 1 or input_timestep_minutes < 1:
        raise ValueError("input frame count and timestep must be positive")
    if input_timestep_minutes % issue_cadence_minutes:
        raise ValueError("issue cadence must divide the model input timestep")
    leads = tuple(int(value) for value in output_lead_minutes)
    if not leads or any(value <= 0 for value in leads):
        raise ValueError("output lead minutes must be positive")

    candidates: list[datetime] = []
    for issue_time in sorted(by_time):
        if issue_time.date() != target_date or not cadence_aligned(
            issue_time,
            issue_cadence_minutes,
        ):
            continue
        history = required_frame_times(
            issue_time,
            input_frames=input_frames,
            timestep_minutes=input_timestep_minutes,
            issue_cadence_minutes=issue_cadence_minutes,
        )
        truth = tuple(
            issue_time + timedelta(minutes=lead)
            for lead in leads
        )
        if all(value in by_time for value in (*history, *truth)):
            candidates.append(issue_time)
    return candidates
