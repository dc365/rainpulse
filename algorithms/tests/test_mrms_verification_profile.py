from __future__ import annotations

from pathlib import Path

from rainpulse_algo.verification.mrms_profile import load_mrms_verification_profile

REPOSITORY_ROOT = Path(__file__).resolve().parents[2]
PROFILE_PATH = REPOSITORY_ROOT / "configs" / "verification" / "rp016-mrms-v1.yaml"
RIGOR_PROFILE_PATH = REPOSITORY_ROOT / "configs" / "verification" / "rp018-mrms-v1.yaml"
HOLDOUT_PROFILE_PATH = (
    REPOSITORY_ROOT / "configs" / "verification" / "rp021-mrms-holdout-v1.yaml"
)


EXPECTED_CASE_IDS = {
    "socal_dry_20210805",
    "midwest_convection_20210810",
    "fred_20210816",
    "henri_20210822",
    "ida_20210829",
}
EXPECTED_COORDINATE_HASHES = {
    "3b1977e44fe9ea2e3326e1afdf47b83fdc05a0de535fe05f8be22801c8c78ec0",
    "d2d40ae1abfc723e787c27f5c65f40b417c438ba9f353231a2dd4de11d5f7a2d",
    "5cd2bf6edcd5b3855b66588e49c55f243f2fa831713ceb1b2e2ad63df2fa23ca",
    "03b7dd035db2b734976df6d74263b0443077f35b1abfe92dca63f21334be98d0",
    "f861a9362d7abc557edf36a73c9d24963ddaa5baadd9437147f47db43f8d6525",
}


def assert_frozen_cases(profile) -> None:
    assert profile.source_cadence_minutes == 10
    assert profile.lead_minutes == tuple(range(10, 121, 10))
    assert sum(len(case.issue_times) for case in profile.cases) == 53
    assert {case.case_id for case in profile.cases} == EXPECTED_CASE_IDS
    assert {case.grid.coordinate_sha256 for case in profile.cases} == EXPECTED_COORDINATE_HASHES
    assert profile.primary_truth_kind == "observed_mrms_10min"
    assert profile.operational_eligible is False
    assert len(profile.profile_sha256) == 64


def test_profile_freezes_five_cases_fifty_three_issues_and_coordinate_hashes() -> None:
    profile = load_mrms_verification_profile(PROFILE_PATH)

    assert profile.profile_version == "rp016-mrms-v1"
    assert_frozen_cases(profile)


def test_rp018_keeps_cases_but_adds_physical_windows_coverage_and_accumulation() -> None:
    profile = load_mrms_verification_profile(RIGOR_PROFILE_PATH)

    assert profile.profile_version == "rp018-mrms-v1"
    assert_frozen_cases(profile)
    assert profile.fss_windows_km == (1.0, 5.0, 10.0, 20.0, 40.0)
    assert profile.coverage_minimum_ratio == 0.95
    assert profile.coverage_gate_metric == "forecast_to_truth_coverage"
    assert profile.frozen_case_count == 5
    assert profile.frozen_issue_count == 53
    assert profile.near_lead_minutes == (10, 60)
    assert profile.far_lead_minutes == (70, 120)
    assert profile.accumulation_windows_minutes == (60, 120)
    assert profile.accumulation_thresholds_mm == (1.0, 5.0, 10.0, 25.0, 50.0)


def test_rp021_freezes_observation_selected_holdout_and_boundary_adjusted_gate() -> None:
    profile = load_mrms_verification_profile(HOLDOUT_PROFILE_PATH)

    assert profile.profile_version == "rp021-mrms-holdout-v1"
    assert profile.frozen_case_count == 6
    assert profile.frozen_issue_count == 50
    assert len(profile.cases) == 6
    assert sum(len(case.issue_times) for case in profile.cases) == 50
    assert sum(case.category == "wet" for case in profile.cases) == 4
    assert sum(case.category == "dry" for case in profile.cases) == 2
    assert profile.coverage_gate_metric == "boundary_adjusted_forecast_to_truth_coverage"
    assert profile.coverage_minimum_ratio == 0.95
    assert profile.selection_evidence_path == "rp021-mrms-holdout-selection-v1.json"
    assert (
        profile.selection_evidence_sha256
        == "db2c217d69417d0ffec13872dab2c41633589628d4df525a7f42adc04c6eea03"
    )
