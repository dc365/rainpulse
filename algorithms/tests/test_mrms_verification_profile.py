from __future__ import annotations

from pathlib import Path

from rainpulse_algo.verification.mrms_profile import load_mrms_verification_profile

REPOSITORY_ROOT = Path(__file__).resolve().parents[2]
PROFILE_PATH = REPOSITORY_ROOT / "configs" / "verification" / "rp016-mrms-v1.yaml"


def test_profile_freezes_five_cases_fifty_three_issues_and_coordinate_hashes() -> None:
    profile = load_mrms_verification_profile(PROFILE_PATH)

    assert profile.profile_version == "rp016-mrms-v1"
    assert profile.source_cadence_minutes == 10
    assert profile.lead_minutes == tuple(range(10, 121, 10))
    assert sum(len(case.issue_times) for case in profile.cases) == 53
    assert {case.case_id for case in profile.cases} == {
        "socal_dry_20210805",
        "midwest_convection_20210810",
        "fred_20210816",
        "henri_20210822",
        "ida_20210829",
    }
    assert {case.grid.coordinate_sha256 for case in profile.cases} == {
        "3b1977e44fe9ea2e3326e1afdf47b83fdc05a0de535fe05f8be22801c8c78ec0",
        "d2d40ae1abfc723e787c27f5c65f40b417c438ba9f353231a2dd4de11d5f7a2d",
        "5cd2bf6edcd5b3855b66588e49c55f243f2fa831713ceb1b2e2ad63df2fa23ca",
        "03b7dd035db2b734976df6d74263b0443077f35b1abfe92dca63f21334be98d0",
        "f861a9362d7abc557edf36a73c9d24963ddaa5baadd9437147f47db43f8d6525",
    }
    assert profile.primary_truth_kind == "observed_mrms_10min"
    assert profile.operational_eligible is False
