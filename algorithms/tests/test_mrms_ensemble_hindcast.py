from __future__ import annotations

import csv
import hashlib
import json
from datetime import UTC, datetime
from pathlib import Path

import numpy as np
import pytest

from rainpulse_algo.datasets.mrms_precip import MRMSPrecipFrame, MRMSSourceState
from rainpulse_algo.grid import RegularLatLonGrid
from rainpulse_algo.verification.mrms_ensemble_hindcast import (
    conform_ensemble_split,
    run_mrms_ensemble_hindcast,
)
from rainpulse_algo.verification.mrms_ensemble_profile import (
    MRMSEnsembleGate,
    MRMSEnsembleProfile,
    MRMSEnsembleSplit,
)
from rainpulse_algo.verification.mrms_profile import MRMSVerificationCase

REPOSITORY_ROOT = Path(__file__).resolve().parents[2]


class DryMRMSFrameSource:
    def read(self, valid_time: datetime, grid: RegularLatLonGrid) -> MRMSPrecipFrame:
        rate = np.zeros(grid.shape, dtype="float32")
        return MRMSPrecipFrame(
            valid_time=valid_time,
            rate_mm_h=rate,
            valid_mask=np.ones(grid.shape, dtype="uint8"),
            source_state=np.full(grid.shape, MRMSSourceState.NO_RAIN, dtype="int8"),
            source_path=f"synthetic-boundary/{valid_time:%Y%m%d-%H%M%S}.grib2.gz",
        )


def _sha(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def _profile() -> MRMSEnsembleProfile:
    grid = RegularLatLonGrid(
        grid_id="rp024-test-grid",
        config_version="rp024-test-grid-v1",
        west=118.0,
        east=118.31,
        south=25.0,
        north=25.23,
        longitude_interval_deg=0.01,
        latitude_interval_deg=0.01,
        longitude_count=32,
        latitude_count=24,
        reference_latitude_deg=25.115,
        ancillary_domain_id="rp024-test-ancillary",
    )
    development_case = MRMSVerificationCase(
        case_id="development_20220110_test_dry_1",
        label="test development dry",
        category="dry",
        grid=grid,
        issue_times=(datetime(2022, 1, 10, 12, 0, tzinfo=UTC),),
    )
    holdout_case = MRMSVerificationCase(
        case_id="holdout_20230610_test_dry_1",
        label="test holdout dry",
        category="dry",
        grid=grid,
        issue_times=(datetime(2023, 6, 10, 12, 0, tzinfo=UTC),),
    )
    steps = REPOSITORY_ROOT / "configs/nowcast/rp022-pysteps-steps-v1.yaml"
    lk = REPOSITORY_ROOT / "configs/nowcast/rp016-pysteps-lk-v1.yaml"
    return MRMSEnsembleProfile(
        profile_version="rp024-test-v1",
        profile_sha256="0" * 64,
        configuration_sha256="3" * 64,
        source_cadence_minutes=10,
        steps_profile=steps,
        steps_profile_sha256=_sha(steps),
        lk_profile=lk,
        lk_profile_sha256=_sha(lk),
        member_count=12,
        lead_minutes=tuple(range(10, 121, 10)),
        thresholds_mm_h=(1.0, 5.0, 10.0, 20.0, 50.0),
        reliability_bin_edges=tuple(index / 10 for index in range(11)),
        near_lead_minutes=(10, 60),
        far_lead_minutes=(70, 120),
        baselines=("lk", "persistence", "phase_correlation"),
        forbidden_months=("2021-08", "2024-06", "2025-01"),
        development=MRMSEnsembleSplit(
            name="development",
            role="development",
            months=("2022-01",),
            selection_evidence_path=Path("development.json"),
            selection_evidence_sha256="1" * 64,
            cases=(development_case,),
            issue_count=1,
        ),
        holdout=MRMSEnsembleSplit(
            name="holdout",
            role="independent_holdout",
            months=("2023-06",),
            selection_evidence_path=Path("holdout.json"),
            selection_evidence_sha256="2" * 64,
            cases=(holdout_case,),
            issue_count=1,
        ),
        gate=MRMSEnsembleGate(
            status="development_pending",
            artifact_path=None,
            artifact_sha256=None,
            artifact=None,
        ),
        operational_eligible=False,
    )


def test_dry_ensemble_hindcast_writes_common_support_probabilistic_evidence(
    tmp_path: Path,
) -> None:
    profile = _profile()

    summary = run_mrms_ensemble_hindcast(
        profile,
        split="development",
        repository_root=REPOSITORY_ROOT,
        frame_source=DryMRMSFrameSource(),
        output_directory=tmp_path,
        maximum_issues=1,
    )

    assert summary["completed_issue_count"] == 1
    assert summary["failed_issue_count"] == 0
    assert summary["member_count"] == 12
    assert summary["ensemble_fallback_issue_count"] == 1
    assert summary["operational_eligible"] is False
    assert summary["metric_row_count"] == 240
    assert summary["coverage_row_count"] == 48
    assert summary["reliability_row_count"] == 2400
    assert summary["lead_band_summary"]["near"][
        "minimum_common_verification_coverage"
    ] == pytest.approx(1.0)
    assert summary["lead_band_summary"]["far"][
        "minimum_steps_member_mean_coverage"
    ] == pytest.approx(1.0)
    assert summary["lead_band_summary"]["near"]["scores"]["steps"][
        "crps_mm_h"
    ] == pytest.approx(0.0)
    assert (tmp_path / "probabilistic_metrics.csv").is_file()
    assert (tmp_path / "coverage.csv").is_file()
    assert (tmp_path / "reliability.json").is_file()
    assert (tmp_path / "runtime_metrics.csv").is_file()
    assert "offline engineering" in (tmp_path / "report.md").read_text()
    persisted = json.loads((tmp_path / "summary.json").read_text())
    assert persisted["calibration_status"].endswith("uncalibrated")
    with (tmp_path / "probabilistic_metrics.csv").open(newline="") as handle:
        rows = list(csv.DictReader(handle))
    assert {row["model"] for row in rows} == {
        "steps",
        "lk",
        "persistence",
        "phase_correlation",
    }
    assert {int(row["member_count"]) for row in rows if row["model"] == "steps"} == {
        12
    }


def test_conformance_checks_unique_required_frames_and_holdout_is_locked(
    tmp_path: Path,
) -> None:
    profile = _profile()

    report = conform_ensemble_split(
        profile,
        split="development",
        frame_source=DryMRMSFrameSource(),
        maximum_issues=1,
    )

    assert report["complete"] is True
    assert report["checked_issue_count"] == 1
    assert report["checked_frame_count"] == 15
    with pytest.raises(ValueError, match="holdout forecast is locked"):
        run_mrms_ensemble_hindcast(
            profile,
            split="holdout",
            repository_root=REPOSITORY_ROOT,
            frame_source=DryMRMSFrameSource(),
            output_directory=tmp_path,
            maximum_issues=1,
        )
