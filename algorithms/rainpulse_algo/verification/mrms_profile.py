from __future__ import annotations

import hashlib
import json
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
    coverage_gate_metric: str
    near_lead_minutes: tuple[int, int]
    far_lead_minutes: tuple[int, int]
    accumulation_windows_minutes: tuple[int, ...]
    accumulation_thresholds_mm: tuple[float, ...]
    cases: tuple[MRMSVerificationCase, ...]
    frozen_case_count: int
    frozen_issue_count: int
    selection_evidence_path: str | None
    selection_evidence_sha256: str | None
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


def _validate_selection_evidence(
    raw: dict[str, Any],
    cases: tuple[MRMSVerificationCase, ...],
    expected_protocol_version: str | None,
) -> None:
    if raw.get("model_forecast_or_skill_fields_read") is not False:
        raise MRMSVerificationProfileError(
            "holdout selection evidence must exclude model forecasts and skill fields"
        )
    if expected_protocol_version and raw.get("selection_protocol_version") != (
        expected_protocol_version
    ):
        raise MRMSVerificationProfileError("holdout selection protocol version differs")
    selected = raw.get("selected_cases")
    if not isinstance(selected, list) or len(selected) != len(cases):
        raise MRMSVerificationProfileError("holdout evidence case count differs")
    evidence_by_id = {str(item["case_id"]): item for item in selected}
    if len(evidence_by_id) != len(selected):
        raise MRMSVerificationProfileError("holdout evidence case identifiers are not unique")
    for case in cases:
        try:
            evidence = evidence_by_id[case.case_id]
            issue_times = _issue_times(evidence["issues"], 10)
            grid_hash = str(evidence["grid"]["coordinate_sha256"])
        except (KeyError, TypeError, ValueError) as exc:
            raise MRMSVerificationProfileError(
                f"holdout evidence does not describe profile case {case.case_id}"
            ) from exc
        if (
            str(evidence["label"]) != case.label
            or str(evidence["category"]) != case.category
            or issue_times != case.issue_times
            or grid_hash != case.grid.coordinate_sha256
        ):
            raise MRMSVerificationProfileError(
                f"holdout evidence differs from profile case {case.case_id}"
            )


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
        freeze = raw.get("freeze", {})
        if not freeze and str(raw["profile_version"]) not in {
            "rp016-mrms-v1",
            "rp018-mrms-v1",
        }:
            raise MRMSVerificationProfileError(
                "new MRMS verification profiles must freeze expected case and issue counts"
            )
        selection_evidence_path = freeze.get("selection_evidence_path")
        selection_evidence_sha256 = freeze.get("selection_evidence_sha256")
        selection_evidence: dict[str, Any] | None = None
        if bool(selection_evidence_path) != bool(selection_evidence_sha256):
            raise MRMSVerificationProfileError(
                "selection evidence path and SHA-256 must be configured together"
            )
        if selection_evidence_path:
            relative = Path(str(selection_evidence_path))
            if relative.is_absolute() or ".." in relative.parts:
                raise MRMSVerificationProfileError("selection evidence path must remain local")
            evidence_path = path.parent / relative
            evidence_bytes = evidence_path.read_bytes()
            evidence_digest = hashlib.sha256(evidence_bytes).hexdigest()
            if evidence_digest != str(selection_evidence_sha256):
                raise MRMSVerificationProfileError(
                    "selection evidence SHA-256 differs from the frozen profile"
                )
            selection_evidence = json.loads(evidence_bytes)
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
            coverage_gate_metric=str(
                coverage.get("metric", "forecast_to_truth_coverage")
            ),
            near_lead_minutes=_lead_band(lead_bands.get("near_minutes"), (10, 60)),
            far_lead_minutes=_lead_band(lead_bands.get("far_minutes"), (70, 120)),
            accumulation_windows_minutes=tuple(
                int(value) for value in accumulation.get("windows_minutes", (60, 120))
            ),
            accumulation_thresholds_mm=tuple(
                float(value) for value in accumulation.get("thresholds_mm", (1, 5, 10, 25, 50))
            ),
            cases=cases,
            frozen_case_count=int(freeze.get("expected_case_count", 5)),
            frozen_issue_count=int(freeze.get("expected_issue_count", 53)),
            selection_evidence_path=(
                str(selection_evidence_path) if selection_evidence_path else None
            ),
            selection_evidence_sha256=(
                str(selection_evidence_sha256) if selection_evidence_sha256 else None
            ),
            operational_eligible=bool(raw["operational_eligible"]),
        )
        if selection_evidence is not None:
            _validate_selection_evidence(
                selection_evidence,
                cases,
                (
                    str(freeze["selection_protocol_version"])
                    if freeze.get("selection_protocol_version")
                    else None
                ),
            )
    except MRMSVerificationProfileError:
        raise
    except (json.JSONDecodeError, KeyError, TypeError, ValueError, yaml.YAMLError) as exc:
        raise MRMSVerificationProfileError(
            f"invalid MRMS verification profile {path}: {exc}"
        ) from exc

    if profile.operational_eligible:
        raise MRMSVerificationProfileError(
            "MRMS engineering profile cannot be operationally eligible"
        )
    if len(profile.cases) != profile.frozen_case_count or sum(
        len(case.issue_times) for case in profile.cases
    ) != profile.frozen_issue_count:
        raise MRMSVerificationProfileError(
            "MRMS case or issue count differs from the frozen profile"
        )
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
    if profile.coverage_gate_metric not in {
        "forecast_to_truth_coverage",
        "boundary_adjusted_forecast_to_truth_coverage",
    }:
        raise MRMSVerificationProfileError("MRMS coverage gate metric is unsupported")
    if any(value not in profile.lead_minutes for value in profile.accumulation_windows_minutes):
        raise MRMSVerificationProfileError("accumulation windows must align to observed leads")
    if not profile.accumulation_thresholds_mm or any(
        value < 0.0 for value in profile.accumulation_thresholds_mm
    ):
        raise MRMSVerificationProfileError("accumulation thresholds are invalid")
    return profile
