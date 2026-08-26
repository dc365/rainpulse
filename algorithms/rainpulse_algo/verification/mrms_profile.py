from __future__ import annotations

from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any

import yaml

from rainpulse_algo.grid import RegularLatLonGrid


class MRMSVerificationProfileError(ValueError):
    """Raised when the frozen MRMS engineering-validation profile is inconsistent."""


@dataclass(frozen=True)
class MRMSVerificationCase:
    case_id: str
    label: str
    category: str
    grid: RegularLatLonGrid
    issue_times: tuple[datetime, ...]


@dataclass(frozen=True)
class MRMSVerificationProfile:
    profile_version: str
    source_cadence_minutes: int
    nowcast_profile: str
    primary_truth_kind: str
    lead_minutes: tuple[int, ...]
    thresholds_mm_h: tuple[float, ...]
    fss_windows_pixels: tuple[int, ...]
    cases: tuple[MRMSVerificationCase, ...]
    operational_eligible: bool


def _parse_utc(value: str) -> datetime:
    try:
        parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError as exc:
        raise MRMSVerificationProfileError(f"invalid UTC timestamp {value}") from exc
    if parsed.tzinfo is None or parsed.utcoffset() != timedelta(0):
        raise MRMSVerificationProfileError(f"timestamp is not UTC: {value}")
    return parsed.astimezone(UTC)


def _issue_times(raw: dict[str, Any], cadence_minutes: int) -> tuple[datetime, ...]:
    start = _parse_utc(str(raw["start"]))
    end = _parse_utc(str(raw["end"]))
    step = int(raw["step_minutes"])
    if end < start or step <= 0 or step % cadence_minutes:
        raise MRMSVerificationProfileError("case issue range is not source-cadence aligned")
    values: list[datetime] = []
    current = start
    while current <= end:
        if current.second or current.microsecond or current.minute % cadence_minutes:
            raise MRMSVerificationProfileError("case issue time is not source-cadence aligned")
        values.append(current)
        current += timedelta(minutes=step)
    return tuple(values)


def _grid(raw: dict[str, Any]) -> RegularLatLonGrid:
    bounds = raw["bounds"]
    grid = RegularLatLonGrid(
        grid_id=str(raw["grid_id"]),
        config_version=str(raw["config_version"]),
        west=float(bounds["west"]),
        east=float(bounds["east"]),
        south=float(bounds["south"]),
        north=float(bounds["north"]),
        longitude_interval_deg=0.01,
        latitude_interval_deg=0.01,
        longitude_count=501,
        latitude_count=201,
        reference_latitude_deg=(float(bounds["south"]) + float(bounds["north"])) / 2.0,
        ancillary_domain_id="mrms-validation-conus-v1",
    )
    if grid.coordinate_sha256 != str(raw["coordinate_sha256"]):
        raise MRMSVerificationProfileError(
            f"case {grid.grid_id} coordinate SHA-256 differs from the frozen profile"
        )
    return grid


def load_mrms_verification_profile(path: Path) -> MRMSVerificationProfile:
    try:
        raw = yaml.safe_load(path.read_text(encoding="utf-8"))
        if raw["schema_version"] != "1.0" or raw["lifecycle"] != "engineering_validation":
            raise MRMSVerificationProfileError("unsupported MRMS verification profile lifecycle")
        cadence = int(raw["source"]["cadence_minutes"])
        if cadence != 10:
            raise MRMSVerificationProfileError("RP016 MRMS validation requires 10-minute input")
        lead_minutes = tuple(int(value) for value in raw["lead_minutes"])
        if lead_minutes != tuple(range(10, 121, 10)):
            raise MRMSVerificationProfileError("primary leads must be observed 10-minute slots")
        cases = tuple(
            MRMSVerificationCase(
                case_id=str(case["case_id"]),
                label=str(case["label"]),
                category=str(case["category"]),
                grid=_grid(case["grid"]),
                issue_times=_issue_times(case["issues"], cadence),
            )
            for case in raw["cases"]
        )
        profile = MRMSVerificationProfile(
            profile_version=str(raw["profile_version"]),
            source_cadence_minutes=cadence,
            nowcast_profile=str(raw["nowcast_profile"]),
            primary_truth_kind=str(raw["primary_truth_kind"]),
            lead_minutes=lead_minutes,
            thresholds_mm_h=tuple(float(value) for value in raw["thresholds_mm_h"]),
            fss_windows_pixels=tuple(int(value) for value in raw["fss_windows_pixels"]),
            cases=cases,
            operational_eligible=bool(raw["operational_eligible"]),
        )
    except MRMSVerificationProfileError:
        raise
    except (KeyError, TypeError, ValueError, yaml.YAMLError) as exc:
        raise MRMSVerificationProfileError(
            f"invalid MRMS verification profile {path}: {exc}"
        ) from exc

    if profile.operational_eligible:
        raise MRMSVerificationProfileError(
            "MRMS engineering profile cannot be operationally eligible"
        )
    if len(profile.cases) != 5 or sum(len(case.issue_times) for case in profile.cases) != 53:
        raise MRMSVerificationProfileError("frozen RP016 MRMS case count differs from 5/53")
    return profile
