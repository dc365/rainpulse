from __future__ import annotations

import hashlib
import json
from datetime import UTC, datetime
from pathlib import Path

import pytest
import yaml

from rainpulse_algo.verification.mrms_ensemble_gate import (
    build_development_gate_artifact,
    evaluate_ensemble_summary,
)
from rainpulse_algo.verification.mrms_ensemble_profile import load_mrms_ensemble_profile

from .test_mrms_ensemble_profile import _profile


def _sha(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def _summary(profile, *, crps_skill: float = 0.1) -> dict:
    bands = {
        "near": {
            "minimum_common_verification_coverage": 0.90,
            "minimum_steps_member_mean_coverage": 0.95,
            "steps_skill_against_deterministic_baselines": {
                "persistence": {
                    "crps_skill": crps_skill,
                    "brier_skill_by_threshold": {
                        "1.0": 0.12,
                        "5.0": 0.08,
                        "10.0": 0.03,
                        "20.0": None,
                        "50.0": None,
                    },
                }
            },
        },
        "far": {
            "minimum_common_verification_coverage": 0.20,
            "minimum_steps_member_mean_coverage": 0.65,
        },
    }
    return {
        "profile_version": profile.profile_version,
        "profile_sha256": profile.profile_sha256,
        "configuration_sha256": profile.configuration_sha256,
        "split": "development",
        "selection_evidence_sha256": profile.development.selection_evidence_sha256,
        "member_count": 12,
        "completed_issue_count": 1,
        "failed_issue_count": 0,
        "lead_band_summary": bands,
        "category_lead_band_summary": {"wet": bands},
    }


def test_gate_freezes_only_after_development_passes_and_reloads(tmp_path: Path) -> None:
    profile_path = _profile(tmp_path)
    profile = load_mrms_ensemble_profile(profile_path, repository_root=tmp_path)
    summary = _summary(profile)

    artifact = build_development_gate_artifact(
        profile,
        summary,
        development_summary_path="development-summary.json",
        development_summary_sha256="a" * 64,
        generated_at=datetime(2026, 8, 29, tzinfo=UTC),
    )

    assert artifact["approved_to_run_holdout"] is True
    assert artifact["development_evaluation"]["passed"] is True
    summary_path = tmp_path / "development-summary.json"
    summary_path.write_text(json.dumps(summary), encoding="utf-8")
    artifact["development_summary_sha256"] = _sha(summary_path)
    gate_path = tmp_path / "gate.json"
    gate_path.write_text(json.dumps(artifact), encoding="utf-8")
    raw = yaml.safe_load(profile_path.read_text())
    raw["holdout_gate"] = {
        "status": "frozen_before_holdout",
        "artifact_path": gate_path.name,
        "artifact_sha256": _sha(gate_path),
    }
    profile_path.write_text(yaml.safe_dump(raw, sort_keys=False), encoding="utf-8")

    frozen = load_mrms_ensemble_profile(profile_path, repository_root=tmp_path)

    assert frozen.configuration_sha256 == profile.configuration_sha256
    assert frozen.gate.artifact["development_summary_sha256"] == _sha(summary_path)


def test_gate_rejects_nonpositive_near_skill(tmp_path: Path) -> None:
    profile = load_mrms_ensemble_profile(_profile(tmp_path), repository_root=tmp_path)
    summary = _summary(profile, crps_skill=0.0)

    evaluation = evaluate_ensemble_summary(summary, expected_issue_count=1)

    assert evaluation["passed"] is False
    assert evaluation["checks"]["near_wet_crps_skill_against_persistence"] is False
    with pytest.raises(ValueError, match="near_wet_crps_skill_against_persistence"):
        build_development_gate_artifact(
            profile,
            summary,
            development_summary_path="development-summary.json",
            development_summary_sha256="a" * 64,
        )
