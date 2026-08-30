from __future__ import annotations

import hashlib
import json
from dataclasses import replace
from datetime import UTC, datetime
from pathlib import Path

import numpy as np
import pytest

from rainpulse_algo.datasets.mrms_precip import MRMSPrecipFrame, MRMSSourceState
from rainpulse_algo.grid import RegularLatLonGrid
from rainpulse_algo.verification.mrms_nowcastnet_gate import (
    evaluate_nowcastnet_summary,
)
from rainpulse_algo.verification.mrms_nowcastnet_hindcast import (
    compute_nowcastnet_grid,
    conform_nowcastnet_split,
    issue_random_seed,
    run_mrms_nowcastnet_hindcast,
)
from rainpulse_algo.verification.mrms_nowcastnet_profile import (
    FROZEN_GATE_CRITERIA,
    MRMSNowcastNetGate,
    load_mrms_nowcastnet_profile,
)

REPOSITORY_ROOT = Path(__file__).resolve().parents[2]
PROFILE_PATH = REPOSITORY_ROOT / "configs/verification/rp026-mrms-nowcastnet-v1.yaml"


class DryMRMSFrameSource:
    def read(self, valid_time: datetime, grid: RegularLatLonGrid) -> MRMSPrecipFrame:
        return MRMSPrecipFrame(
            valid_time=valid_time,
            rate_mm_h=np.zeros(grid.shape, dtype="float32"),
            valid_mask=np.ones(grid.shape, dtype="uint8"),
            source_state=np.full(grid.shape, MRMSSourceState.NO_RAIN, dtype="int8"),
            source_path=f"synthetic/{valid_time:%Y%m%d-%H%M%S}.grib2.gz",
        )


def _profile():
    return load_mrms_nowcastnet_profile(PROFILE_PATH, repository_root=REPOSITORY_ROOT)


def _sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def _gate_summary() -> dict[str, object]:
    skill = {
        "crps_skill": 0.1,
        "brier_skill_by_threshold": {
            "1.0": 0.1,
            "5.0": 0.2,
            "10.0": None,
            "20.0": None,
            "50.0": None,
        },
    }
    band = {
        "minimum_common_verification_coverage": 0.8,
        "nowcastnet_skill": {"persistence": skill},
    }
    return {
        "completed_issue_count": 50,
        "failed_issue_count": 0,
        "lead_band_summary": {
            "near": band,
            "far": {"minimum_common_verification_coverage": 0.1},
        },
        "category_lead_band_summary": {"wet": {"near": band}},
    }


def test_frozen_profile_uses_disjoint_unspent_observation_only_splits() -> None:
    profile = _profile()

    assert profile.development.months == ("2024-07", "2024-11")
    assert profile.holdout.months == ("2025-03", "2025-07")
    assert len(profile.development.cases) == 6
    assert len(profile.holdout.cases) == 6
    assert profile.development.issue_count == 50
    assert profile.holdout.issue_count == 50
    assert profile.gate.status == "frozen_before_holdout"
    assert profile.gate.artifact is not None
    assert profile.gate.artifact["approved_to_run_holdout"] is True
    assert profile.operational_eligible is False


def test_model_grid_is_512_square_and_crops_exactly_to_frozen_target() -> None:
    target = _profile().development.cases[0].grid

    model_grid, crop = compute_nowcastnet_grid(target)

    assert model_grid.shape == (512, 512)
    assert crop == (slice(155, 356), slice(5, 506))
    np.testing.assert_array_equal(model_grid.latitude[crop[0]], target.latitude)
    np.testing.assert_array_equal(model_grid.longitude[crop[1]], target.longitude)


def test_seed_is_stable_per_case_and_issue() -> None:
    issue = datetime(2024, 7, 3, 12, tzinfo=UTC)

    assert issue_random_seed(7, "case-a", issue) == issue_random_seed(7, "case-a", issue)
    assert issue_random_seed(7, "case-a", issue) != issue_random_seed(7, "case-b", issue)


def test_conformance_checks_raw_inputs_and_truth_and_holdout_is_locked(
    tmp_path: Path,
) -> None:
    profile = _profile()

    report = conform_nowcastnet_split(
        profile,
        split="development",
        frame_source=DryMRMSFrameSource(),
        maximum_issues=1,
    )

    assert report["complete"] is True
    assert report["checked_issue_count"] == 1
    assert report["checked_frame_count"] == 21
    pending = replace(
        profile,
        gate=MRMSNowcastNetGate(
            status="development_pending",
            artifact_path=None,
            artifact_sha256=None,
            artifact=None,
        ),
    )
    with pytest.raises(ValueError, match="holdout is locked"):
        run_mrms_nowcastnet_hindcast(
            pending,
            split="holdout",
            frame_source=DryMRMSFrameSource(),
            output_directory=tmp_path,
            nowcastnet_backend=lambda _frames, _members, _seed: np.empty(0),
            runtime_info={},
            maximum_issues=1,
        )


def test_development_gate_requires_frozen_coverage_and_wet_skill() -> None:
    passing = evaluate_nowcastnet_summary(_gate_summary(), expected_issue_count=50)
    assert passing["passed"] is True
    assert passing["checks"] == {name: True for name in passing["checks"]}

    failing_summary = _gate_summary()
    failing_summary["lead_band_summary"]["near"][
        "minimum_common_verification_coverage"
    ] = 0.69
    failing = evaluate_nowcastnet_summary(failing_summary, expected_issue_count=50)
    assert failing["passed"] is False
    assert failing["checks"]["near_common_verification_coverage"] is False
    assert FROZEN_GATE_CRITERIA["near_minimum_common_verification_coverage"] == 0.70


def test_independent_acceptance_is_hash_bound_and_keeps_steps_baseline() -> None:
    path = REPOSITORY_ROOT / "configs/verification/rp026-independent-acceptance-v1.json"
    acceptance = json.loads(path.read_text())

    assert acceptance["acceptance_status"] == "passed_with_steps_baseline_retained"
    assert acceptance["operational_eligible"] is False
    assert acceptance["product_publication_enabled"] is False
    for section in ("development", "holdout"):
        for name in ("selection", "summary", "reliability"):
            referenced = REPOSITORY_ROOT / acceptance[section][f"{name}_path"]
            assert _sha256(referenced) == acceptance[section][f"{name}_sha256"]
    conformance = REPOSITORY_ROOT / acceptance["holdout"]["conformance_path"]
    assert _sha256(conformance) == acceptance["holdout"]["conformance_sha256"]
    gate = REPOSITORY_ROOT / acceptance["development"]["gate_path"]
    assert _sha256(gate) == acceptance["development"]["gate_sha256"]
    assert acceptance["holdout"]["near_crps_skill"]["against_persistence"] > 0
    assert acceptance["holdout"]["near_crps_skill"]["against_steps"] < 0
