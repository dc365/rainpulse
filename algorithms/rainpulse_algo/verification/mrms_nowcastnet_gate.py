from __future__ import annotations

import argparse
import hashlib
import json
import sys
from collections.abc import Mapping
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from .mrms_nowcastnet_profile import (
    FROZEN_GATE_CRITERIA,
    GATE_POLICY_VERSION,
    MRMSNowcastNetProfile,
    load_mrms_nowcastnet_profile,
)


def _sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def _number(value: Any) -> float | None:
    if isinstance(value, (int, float)):
        return float(value)
    return None


def evaluate_nowcastnet_summary(
    summary: Mapping[str, Any],
    *,
    expected_issue_count: int,
    criteria: Mapping[str, Any] = FROZEN_GATE_CRITERIA,
) -> dict[str, Any]:
    near = summary["lead_band_summary"]["near"]
    far = summary["lead_band_summary"]["far"]
    wet_near = summary["category_lead_band_summary"]["wet"]["near"]
    persistence = wet_near["nowcastnet_skill"]["persistence"]
    brier = persistence["brier_skill_by_threshold"]
    threshold_values = [
        _number(brier.get(str(float(threshold)), brier.get(str(int(threshold)))))
        for threshold in criteria["near_wet_brier_skill_thresholds_mm_h"]
    ]
    finite_brier = [value for value in threshold_values if value is not None]
    mean_brier = sum(finite_brier) / len(finite_brier) if finite_brier else None
    crps_skill = _number(persistence["crps_skill"])
    near_coverage = _number(near["minimum_common_verification_coverage"])
    far_coverage = _number(far["minimum_common_verification_coverage"])
    checks = {
        "completed_issue_count": int(summary["completed_issue_count"])
        == expected_issue_count,
        "failed_issue_count": int(summary["failed_issue_count"])
        <= int(criteria["maximum_failed_issue_count"]),
        "near_common_verification_coverage": near_coverage is not None
        and near_coverage >= float(criteria["near_minimum_common_verification_coverage"]),
        "far_common_verification_coverage": far_coverage is not None
        and far_coverage >= float(criteria["far_minimum_common_verification_coverage"]),
        "near_wet_crps_skill_against_persistence": crps_skill is not None
        and crps_skill
        >= float(criteria["near_wet_minimum_crps_skill_against_persistence"]),
        "near_wet_finite_brier_skill_threshold_count": len(finite_brier)
        >= int(criteria["near_wet_minimum_finite_brier_skill_threshold_count"]),
        "near_wet_mean_brier_skill_against_persistence": mean_brier is not None
        and mean_brier
        >= float(criteria["near_wet_minimum_mean_brier_skill_against_persistence"]),
    }
    return {
        "expected_issue_count": expected_issue_count,
        "passed": all(checks.values()),
        "checks": checks,
        "observed": {
            "completed_issue_count": int(summary["completed_issue_count"]),
            "failed_issue_count": int(summary["failed_issue_count"]),
            "near_minimum_common_verification_coverage": near_coverage,
            "far_minimum_common_verification_coverage": far_coverage,
            "near_wet_crps_skill_against_persistence": crps_skill,
            "near_wet_brier_skill_against_persistence": {
                str(threshold): value
                for threshold, value in zip(
                    criteria["near_wet_brier_skill_thresholds_mm_h"],
                    threshold_values,
                    strict=True,
                )
            },
            "near_wet_mean_brier_skill_against_persistence": mean_brier,
        },
    }


def build_development_gate_artifact(
    profile: MRMSNowcastNetProfile,
    development_summary: Mapping[str, Any],
    *,
    development_summary_path: str,
    development_summary_sha256: str,
    generated_at: datetime | None = None,
) -> dict[str, Any]:
    if profile.gate.status != "development_pending":
        raise ValueError("RP-026 development gate has already been frozen")
    if (
        development_summary.get("profile_version") != profile.profile_version
        or development_summary.get("profile_sha256") != profile.profile_sha256
        or development_summary.get("configuration_sha256")
        != profile.configuration_sha256
        or development_summary.get("split") != "development"
        or development_summary.get("selection_evidence_sha256")
        != profile.development.selection_evidence_sha256
        or int(development_summary.get("nowcastnet_member_count", -1))
        != profile.nowcastnet_member_count
        or int(development_summary.get("steps_member_count", -1))
        != profile.steps_member_count
    ):
        raise ValueError("RP-026 development summary does not match the pending profile")
    evaluation = evaluate_nowcastnet_summary(
        development_summary,
        expected_issue_count=profile.development.issue_count,
    )
    if not evaluation["passed"]:
        failed = sorted(name for name, passed in evaluation["checks"].items() if not passed)
        raise ValueError(f"RP-026 development gate failed: {', '.join(failed)}")
    return {
        "schema_version": "1.0",
        "gate_policy_version": GATE_POLICY_VERSION,
        "generated_at_utc": (generated_at or datetime.now(UTC))
        .isoformat()
        .replace("+00:00", "Z"),
        "frozen_before_holdout_forecast": True,
        "approved_to_run_holdout": True,
        "profile_version": profile.profile_version,
        "configuration_sha256": profile.configuration_sha256,
        "development_profile_sha256": profile.profile_sha256,
        "development_selection_evidence_sha256": (
            profile.development.selection_evidence_sha256
        ),
        "development_summary_path": development_summary_path,
        "development_summary_sha256": development_summary_sha256,
        "criteria": FROZEN_GATE_CRITERIA,
        "development_evaluation": evaluation,
    }


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Freeze the RP-026 pre-holdout gate")
    parser.add_argument("--repository-root", type=Path, default=Path.cwd())
    parser.add_argument("--profile", type=Path, required=True)
    parser.add_argument("--development-summary", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    return parser


def main(argv: list[str] | None = None) -> int:
    args = _parser().parse_args(argv)
    repository_root = args.repository_root.resolve()
    profile_path = args.profile
    if not profile_path.is_absolute():
        profile_path = repository_root / profile_path
    try:
        profile = load_mrms_nowcastnet_profile(
            profile_path, repository_root=repository_root
        )
        summary_path = args.development_summary
        if not summary_path.is_absolute():
            summary_path = repository_root / summary_path
        summary_path = summary_path.resolve()
        if not summary_path.is_relative_to(repository_root):
            raise ValueError("RP-026 development summary must remain repository-local")
        summary = json.loads(summary_path.read_text(encoding="utf-8"))
        artifact = build_development_gate_artifact(
            profile,
            summary,
            development_summary_path=summary_path.relative_to(repository_root).as_posix(),
            development_summary_sha256=_sha256(summary_path),
        )
        output_path = args.output
        if not output_path.is_absolute():
            output_path = repository_root / output_path
        output_path = output_path.resolve()
        if not output_path.is_relative_to(repository_root):
            raise ValueError("RP-026 gate artifact must remain repository-local")
        output_path.parent.mkdir(parents=True, exist_ok=True)
        output_path.write_text(
            json.dumps(artifact, ensure_ascii=False, indent=2, sort_keys=True) + "\n",
            encoding="utf-8",
        )
        print(json.dumps(artifact, ensure_ascii=False, indent=2, sort_keys=True))
        return 0
    except (OSError, ValueError, KeyError, TypeError, json.JSONDecodeError) as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
