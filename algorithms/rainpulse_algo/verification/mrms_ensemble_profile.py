from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any

import yaml

from rainpulse_algo.grid import RegularLatLonGrid

from .mrms_profile import MRMSVerificationCase

GATE_POLICY_VERSION = "rp024-independent-probability-gate-v1"
FROZEN_GATE_CRITERIA: dict[str, Any] = {
    "completed_issue_count_must_equal_expected": True,
    "maximum_failed_issue_count": 0,
    "near_minimum_common_verification_coverage": 0.70,
    "near_minimum_steps_member_mean_coverage": 0.85,
    "far_minimum_common_verification_coverage": 0.05,
    "far_minimum_steps_member_mean_coverage": 0.40,
    "near_wet_minimum_crps_skill_against_persistence": 0.0,
    "near_wet_brier_skill_thresholds_mm_h": [1.0, 5.0, 10.0],
    "near_wet_minimum_finite_brier_skill_threshold_count": 2,
    "near_wet_minimum_mean_brier_skill_against_persistence": 0.0,
    "far_skill_policy": "descriptive_only_retain_all_deterministic_baselines",
    "reliability_policy": "diagnostic_only_raw_ensemble_uncalibrated",
}


class MRMSEnsembleProfileError(ValueError):
    """Raised when the frozen RP-024 split or leakage boundary is inconsistent."""


@dataclass(frozen=True)
class MRMSEnsembleSplit:
    name: str
    role: str
    months: tuple[str, ...]
    selection_evidence_path: Path
    selection_evidence_sha256: str
    cases: tuple[MRMSVerificationCase, ...]
    issue_count: int


@dataclass(frozen=True)
class MRMSEnsembleGate:
    status: str
    artifact_path: Path | None
    artifact_sha256: str | None
    artifact: dict[str, Any] | None


@dataclass(frozen=True)
class MRMSEnsembleProfile:
    profile_version: str
    profile_sha256: str
    configuration_sha256: str
    source_cadence_minutes: int
    steps_profile: Path
    steps_profile_sha256: str
    lk_profile: Path
    lk_profile_sha256: str
    member_count: int
    lead_minutes: tuple[int, ...]
    thresholds_mm_h: tuple[float, ...]
    reliability_bin_edges: tuple[float, ...]
    near_lead_minutes: tuple[int, int]
    far_lead_minutes: tuple[int, int]
    baselines: tuple[str, ...]
    forbidden_months: tuple[str, ...]
    development: MRMSEnsembleSplit
    holdout: MRMSEnsembleSplit
    gate: MRMSEnsembleGate
    operational_eligible: bool

    def split(self, name: str) -> MRMSEnsembleSplit:
        if name == "development":
            return self.development
        if name == "holdout":
            return self.holdout
        raise MRMSEnsembleProfileError(f"unsupported RP-024 split {name}")


def _sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def _configuration_sha256(raw: dict[str, Any]) -> str:
    stable = {key: value for key, value in raw.items() if key != "holdout_gate"}
    payload = json.dumps(
        stable,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
    ).encode("utf-8")
    return hashlib.sha256(payload).hexdigest()


def _resolve_local(repository_root: Path, value: str) -> Path:
    relative = Path(value)
    if relative.is_absolute() or ".." in relative.parts:
        raise MRMSEnsembleProfileError("RP-024 references must remain repository-local")
    root = repository_root.resolve()
    resolved = (root / relative).resolve()
    if not resolved.is_relative_to(root):
        raise MRMSEnsembleProfileError("RP-024 reference escapes the repository")
    return resolved


def _parse_utc(value: str) -> datetime:
    parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    if parsed.tzinfo is None or parsed.utcoffset() != timedelta(0):
        raise MRMSEnsembleProfileError(f"RP-024 timestamp is not UTC: {value}")
    return parsed.astimezone(UTC)


def _issue_times(raw: dict[str, Any]) -> tuple[datetime, ...]:
    start = _parse_utc(str(raw["start"]))
    end = _parse_utc(str(raw["end"]))
    step = int(raw["step_minutes"])
    if end < start or step <= 0 or step % 10:
        raise MRMSEnsembleProfileError("RP-024 issue range is not ten-minute aligned")
    values: list[datetime] = []
    current = start
    while current <= end:
        if current.second or current.microsecond or current.minute % 10:
            raise MRMSEnsembleProfileError("RP-024 issue timestamp is not cadence aligned")
        values.append(current)
        current += timedelta(minutes=step)
    return tuple(values)


def _case(raw: dict[str, Any]) -> MRMSVerificationCase:
    grid_raw = raw["grid"]
    bounds = grid_raw["bounds"]
    grid = RegularLatLonGrid(
        grid_id=str(grid_raw["grid_id"]),
        config_version=str(grid_raw["config_version"]),
        west=float(bounds["west"]),
        east=float(bounds["east"]),
        south=float(bounds["south"]),
        north=float(bounds["north"]),
        longitude_interval_deg=0.01,
        latitude_interval_deg=0.01,
        longitude_count=501,
        latitude_count=201,
        reference_latitude_deg=(float(bounds["south"]) + float(bounds["north"])) / 2,
        ancillary_domain_id="mrms-validation-conus-v1",
    )
    if grid.coordinate_sha256 != str(grid_raw["coordinate_sha256"]):
        raise MRMSEnsembleProfileError("RP-024 evidence grid coordinate hash differs")
    return MRMSVerificationCase(
        case_id=str(raw["case_id"]),
        label=str(raw["label"]),
        category=str(raw["category"]),
        grid=grid,
        issue_times=_issue_times(raw["issues"]),
    )


def _load_split(
    name: str,
    raw: dict[str, Any],
    *,
    repository_root: Path,
) -> MRMSEnsembleSplit:
    role = "development" if name == "development" else "independent_holdout"
    months = tuple(str(value) for value in raw["months"])
    evidence_path = _resolve_local(repository_root, str(raw["selection_evidence_path"]))
    expected_sha = str(raw["selection_evidence_sha256"])
    if _sha256(evidence_path) != expected_sha:
        raise MRMSEnsembleProfileError(f"RP-024 {name} evidence SHA-256 differs")
    evidence = json.loads(evidence_path.read_text(encoding="utf-8"))
    if (
        evidence.get("schema_version") != "1.0"
        or evidence.get("selection_protocol_version") != "mrms-observation-split-v2"
        or evidence.get("selection_role") != role
        or evidence.get("model_forecast_or_skill_fields_read") is not False
        or tuple(evidence.get("source", {}).get("months", ())) != months
    ):
        raise MRMSEnsembleProfileError(f"RP-024 {name} selection boundary differs")
    selected = evidence.get("selected_cases")
    if not isinstance(selected, list):
        raise MRMSEnsembleProfileError(f"RP-024 {name} evidence cases are invalid")
    cases = tuple(_case(value) for value in selected)
    expected_cases = int(raw["expected_case_count"])
    expected_issues = int(raw["expected_issue_count"])
    issue_count = sum(len(case.issue_times) for case in cases)
    namespace = "development_" if name == "development" else "holdout_"
    if (
        len(cases) != expected_cases
        or issue_count != expected_issues
        or int(evidence.get("selected_case_count", -1)) != expected_cases
        or int(evidence.get("selected_issue_count", -1)) != expected_issues
        or len({case.case_id for case in cases}) != len(cases)
        or any(not case.case_id.startswith(namespace) for case in cases)
    ):
        raise MRMSEnsembleProfileError(f"RP-024 {name} frozen counts or namespace differ")
    return MRMSEnsembleSplit(
        name=name,
        role=role,
        months=months,
        selection_evidence_path=evidence_path,
        selection_evidence_sha256=expected_sha,
        cases=cases,
        issue_count=issue_count,
    )


def _load_gate(
    raw: dict[str, Any],
    repository_root: Path,
    *,
    profile_version: str,
    configuration_sha256: str,
    development_selection_sha256: str,
    expected_development_issue_count: int,
) -> MRMSEnsembleGate:
    status = str(raw["status"])
    path_value = raw.get("artifact_path")
    hash_value = raw.get("artifact_sha256")
    if status == "development_pending":
        if path_value is not None or hash_value is not None:
            raise MRMSEnsembleProfileError("pending RP-024 gate cannot reference an artifact")
        return MRMSEnsembleGate(status, None, None, None)
    if status != "frozen_before_holdout" or not path_value or not hash_value:
        raise MRMSEnsembleProfileError("RP-024 holdout gate status is invalid")
    artifact_path = _resolve_local(repository_root, str(path_value))
    artifact_sha = str(hash_value)
    if _sha256(artifact_path) != artifact_sha:
        raise MRMSEnsembleProfileError("RP-024 holdout gate SHA-256 differs")
    artifact = json.loads(artifact_path.read_text(encoding="utf-8"))
    if (
        artifact.get("schema_version") != "1.0"
        or artifact.get("gate_policy_version") != GATE_POLICY_VERSION
        or artifact.get("frozen_before_holdout_forecast") is not True
        or artifact.get("approved_to_run_holdout") is not True
        or artifact.get("profile_version") != profile_version
        or artifact.get("configuration_sha256") != configuration_sha256
        or artifact.get("development_selection_evidence_sha256")
        != development_selection_sha256
        or artifact.get("development_evaluation", {}).get("expected_issue_count")
        != expected_development_issue_count
        or artifact.get("development_evaluation", {}).get("passed") is not True
        or artifact.get("criteria") != FROZEN_GATE_CRITERIA
    ):
        raise MRMSEnsembleProfileError("RP-024 holdout gate artifact is invalid")
    summary_path_value = artifact.get("development_summary_path")
    summary_sha = artifact.get("development_summary_sha256")
    if not summary_path_value or not summary_sha:
        raise MRMSEnsembleProfileError("RP-024 gate development summary reference is invalid")
    summary_path = _resolve_local(repository_root, str(summary_path_value))
    if _sha256(summary_path) != str(summary_sha):
        raise MRMSEnsembleProfileError("RP-024 gate development summary SHA-256 differs")
    summary = json.loads(summary_path.read_text(encoding="utf-8"))
    if (
        summary.get("profile_version") != profile_version
        or summary.get("profile_sha256") != artifact.get("development_profile_sha256")
        or summary.get("configuration_sha256") != configuration_sha256
        or summary.get("split") != "development"
        or summary.get("selection_evidence_sha256") != development_selection_sha256
        or int(summary.get("member_count", -1)) != 12
        or int(summary.get("completed_issue_count", -1))
        != expected_development_issue_count
        or int(summary.get("failed_issue_count", -1)) != 0
    ):
        raise MRMSEnsembleProfileError("RP-024 gate development summary identity differs")
    return MRMSEnsembleGate(status, artifact_path, artifact_sha, artifact)


def load_mrms_ensemble_profile(
    path: Path,
    *,
    repository_root: Path | None = None,
) -> MRMSEnsembleProfile:
    try:
        profile_bytes = path.read_bytes()
        raw = yaml.safe_load(profile_bytes)
        root = (repository_root or path.resolve().parents[2]).resolve()
        if (
            raw["schema_version"] != "1.0"
            or raw["lifecycle"] != "engineering_validation"
            or bool(raw["operational_eligible"])
        ):
            raise MRMSEnsembleProfileError("unsupported RP-024 profile lifecycle")
        steps_path = _resolve_local(root, str(raw["forecast"]["steps_profile"]))
        lk_path = _resolve_local(root, str(raw["forecast"]["lk_profile"]))
        if _sha256(steps_path) != str(raw["forecast"]["steps_profile_sha256"]):
            raise MRMSEnsembleProfileError("RP-024 STEPS profile SHA-256 differs")
        if _sha256(lk_path) != str(raw["forecast"]["lk_profile_sha256"]):
            raise MRMSEnsembleProfileError("RP-024 LK profile SHA-256 differs")
        splits = raw["splits"]
        development = _load_split("development", splits["development"], repository_root=root)
        holdout = _load_split("holdout", splits["holdout"], repository_root=root)
        verification = raw["verification"]
        leakage = raw["leakage"]
        profile_version = str(raw["profile_version"])
        configuration_sha256 = _configuration_sha256(raw)
        profile = MRMSEnsembleProfile(
            profile_version=profile_version,
            profile_sha256=hashlib.sha256(profile_bytes).hexdigest(),
            configuration_sha256=configuration_sha256,
            source_cadence_minutes=int(raw["source"]["cadence_minutes"]),
            steps_profile=steps_path,
            steps_profile_sha256=str(raw["forecast"]["steps_profile_sha256"]),
            lk_profile=lk_path,
            lk_profile_sha256=str(raw["forecast"]["lk_profile_sha256"]),
            member_count=int(raw["forecast"]["member_count"]),
            lead_minutes=tuple(int(value) for value in verification["lead_minutes"]),
            thresholds_mm_h=tuple(float(value) for value in verification["thresholds_mm_h"]),
            reliability_bin_edges=tuple(
                float(value) for value in verification["reliability_bin_edges"]
            ),
            near_lead_minutes=tuple(int(value) for value in verification["near_minutes"]),
            far_lead_minutes=tuple(int(value) for value in verification["far_minutes"]),
            baselines=tuple(str(value) for value in verification["baselines"]),
            forbidden_months=tuple(str(value) for value in leakage["forbidden_months"]),
            development=development,
            holdout=holdout,
            gate=_load_gate(
                raw["holdout_gate"],
                root,
                profile_version=profile_version,
                configuration_sha256=configuration_sha256,
                development_selection_sha256=development.selection_evidence_sha256,
                expected_development_issue_count=development.issue_count,
            ),
            operational_eligible=False,
        )
    except MRMSEnsembleProfileError:
        raise
    except (KeyError, OSError, TypeError, ValueError, json.JSONDecodeError, yaml.YAMLError) as exc:
        raise MRMSEnsembleProfileError(f"invalid RP-024 profile {path}: {exc}") from exc

    used = set(profile.development.months) | set(profile.holdout.months)
    if (
        profile.source_cadence_minutes != 10
        or profile.member_count != 12
        or profile.lead_minutes != tuple(range(10, 121, 10))
        or profile.thresholds_mm_h != (1.0, 5.0, 10.0, 20.0, 50.0)
        or profile.baselines != ("lk", "persistence", "phase_correlation")
        or profile.near_lead_minutes != (10, 60)
        or profile.far_lead_minutes != (70, 120)
        or profile.reliability_bin_edges != tuple(index / 10 for index in range(11))
    ):
        raise MRMSEnsembleProfileError("RP-024 frozen forecast or metric semantics differ")
    if (
        set(profile.development.months) & set(profile.holdout.months)
        or used & set(profile.forbidden_months)
        or {case.case_id for case in profile.development.cases}
        & {case.case_id for case in profile.holdout.cases}
    ):
        raise MRMSEnsembleProfileError("RP-024 split leakage boundary is violated")
    return profile
