from __future__ import annotations

import hashlib
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
    profile_sha256: str
    source_cadence_minutes: int
    nowcast_profile: str
    primary_truth_kind: str
    lead_minutes: tuple[int, ...]
    thresholds_mm_h: tuple[float, ...]
    fss_windows_pixels: tuple[int, ...]
    fss_windows_km: tuple[float, ...]
    coverage_minimum_ratio: float
    near_lead_minutes: tuple[int, int]
    far_lead_minutes: tuple[int, int]
    accumulation_windows_minutes: tuple[int, ...]
    accumulation_thresholds_mm: tuple[float, ...]
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


def _lead_band(raw: Any, default: tuple[int, int]) -> tuple[int, int]:
    if raw is None:
        return default
    values = tuple(int(value) for value in raw)
    if len(values) != 2 or values[0] < 0 or values[1] < values[0]:
        raise MRMSVerificationProfileError("verification lead band is invalid")
    return values[0], values[1]


def load_mrms_verification_profile(path: Path) -> MRMSVerificationProfile:
    try:
        profile_bytes = path.read_bytes()
        raw = yaml.safe_load(profile_bytes)
        if raw["schema_version"] != "1.0" or raw["lifecycle"] != "engineering_validation":
            raise MRMSVerificationProfileError("unsupported MRMS verification profile lifecycle")
        cadence = int(raw["source"]["cadence_minutes"])
        if cadence != 10:
            raise MRMSVerificationProfileError("MRMS validation requires 10-minute source input")
        lead_minutes = tuple(int(value) for value in raw["lead_minutes"])
        if lead_minutes != tuple(range(10, 121, 10)):
            raise MRMSVerificationProfileError("primary leads must be observed 10-minute slots")
        rigor = raw.get("rigor", {})
        coverage = rigor.get("coverage_gate", {})
        accumulation = rigor.get("accumulation", {})
        lead_bands = rigor.get("lead_bands", {})
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
            profile_sha256=hashlib.sha256(profile_bytes).hexdigest(),
            source_cadence_minutes=cadence,
            nowcast_profile=str(raw["nowcast_profile"]),
            primary_truth_kind=str(raw["primary_truth_kind"]),
            lead_minutes=lead_minutes,
            thresholds_mm_h=tuple(float(value) for value in raw["thresholds_mm_h"]),
            fss_windows_pixels=tuple(int(value) for value in raw["fss_windows_pixels"]),
            fss_windows_km=tuple(
                float(value) for value in rigor.get("fss_windows_km", (1, 5, 10, 20, 40))
            ),
            coverage_minimum_ratio=float(coverage.get("minimum_forecast_to_truth_ratio", 0.95)),
            near_lead_minutes=_lead_band(lead_bands.get("near_minutes"), (10, 60)),
            far_lead_minutes=_lead_band(lead_bands.get("far_minutes"), (70, 120)),
            accumulation_windows_minutes=tuple(
                int(value) for value in accumulation.get("windows_minutes", (60, 120))
            ),
            accumulation_thresholds_mm=tuple(
                float(value) for value in accumulation.get("thresholds_mm", (1, 5, 10, 25, 50))
            ),
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
        raise MRMSVerificationProfileError("frozen MRMS case count differs from 5/53")
    if not profile.thresholds_mm_h or any(value < 0.0 for value in profile.thresholds_mm_h):
        raise MRMSVerificationProfileError("MRMS rate thresholds are invalid")
    if not profile.fss_windows_pixels or any(
        value < 1 or value % 2 == 0 for value in profile.fss_windows_pixels
    ):
        raise MRMSVerificationProfileError("MRMS pixel FSS windows must be positive and odd")
    if not profile.fss_windows_km or any(value <= 0.0 for value in profile.fss_windows_km):
        raise MRMSVerificationProfileError("MRMS physical FSS windows must be positive")
    if not 0.0 < profile.coverage_minimum_ratio <= 1.0:
        raise MRMSVerificationProfileError("MRMS coverage gate must be within (0, 1]")
    if any(value not in profile.lead_minutes for value in profile.accumulation_windows_minutes):
        raise MRMSVerificationProfileError("accumulation windows must align to observed leads")
    if not profile.accumulation_thresholds_mm or any(
        value < 0.0 for value in profile.accumulation_thresholds_mm
    ):
        raise MRMSVerificationProfileError("accumulation thresholds are invalid")
    return profile
