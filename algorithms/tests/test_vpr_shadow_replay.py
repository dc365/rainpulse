from __future__ import annotations

from pathlib import Path

from rainpulse_algo.radar.vpr_shadow_replay import run_vpr_shadow_replay_validation

REPOSITORY_ROOT = Path(__file__).resolve().parents[2]
MANIFEST_PATH = (
    REPOSITORY_ROOT / "configs" / "verification" / "rp017-vpr-shadow-replay-manifest-v1.json"
)


def test_rp017_shadow_replay_manifest_replays_fixed_vpr_cases() -> None:
    report = run_vpr_shadow_replay_validation(MANIFEST_PATH)

    assert report["manifest_version"] == "rp017-vpr-shadow-replay-v1"
    assert report["case_count"] == 2
    assert report["passed_case_count"] == 2
    assert report["failed_case_count"] == 0
    assert [item["case_id"] for item in report["cases"]] == [
        "stratiform_vpr_and_far_range_overshoot",
        "missing_explicit_precipitation_type",
    ]
    assert report["cases"][0]["summary"] == {
        "input_field": "DBZH_VPR_CORRECTED",
        "vpr_corrected_cell_count": 2,
        "vpr_bright_band_cell_count": 1,
        "vpr_overshoot_missing_cell_count": 1,
    }
    assert report["cases"][1]["expected_error_substring"] == "PRECIP_TYPE"