from __future__ import annotations

import csv
import json
from datetime import UTC, datetime
from pathlib import Path

import numpy as np
import pytest

from rainpulse_algo.datasets.mrms_precip import (
    MRMSPrecipError,
    MRMSPrecipFrame,
    MRMSSourceState,
)
from rainpulse_algo.grid import RegularLatLonGrid
from rainpulse_algo.verification.mrms_hindcast import (
    MRMSArchiveFrameSource,
    _runtime_fingerprint,
    conform_mrms_cases,
    main,
    run_mrms_hindcast,
)
from rainpulse_algo.verification.mrms_profile import load_mrms_verification_profile

REPOSITORY_ROOT = Path(__file__).resolve().parents[2]
PROFILE_PATH = REPOSITORY_ROOT / "configs" / "verification" / "rp016-mrms-v1.yaml"
RIGOR_PROFILE_PATH = REPOSITORY_ROOT / "configs" / "verification" / "rp018-mrms-v1.yaml"


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


class FailingMRMSFrameSource:
    def read(self, valid_time: datetime, grid: RegularLatLonGrid) -> MRMSPrecipFrame:
        raise MRMSPrecipError(f"synthetic missing frame {valid_time.isoformat()}")


def test_runtime_fingerprint_accepts_explicit_deployment_revision(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    revision = "a" * 40
    monkeypatch.setenv("RAINPULSE_BUILD_REVISION", revision)

    fingerprint = _runtime_fingerprint(
        load_mrms_verification_profile(RIGOR_PROFILE_PATH),
        REPOSITORY_ROOT,
    )

    assert fingerprint["git_commit"] == revision
    assert fingerprint["git_commit_source"] == "environment"


def test_runtime_fingerprint_rejects_ambiguous_short_revision(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("RAINPULSE_BUILD_REVISION", "abc123")

    with pytest.raises(ValueError, match="full hexadecimal revision"):
        _runtime_fingerprint(
            load_mrms_verification_profile(RIGOR_PROFILE_PATH),
            REPOSITORY_ROOT,
        )


def test_hindcast_runs_shared_core_and_writes_non_operational_report(tmp_path: Path) -> None:
    profile = load_mrms_verification_profile(RIGOR_PROFILE_PATH)

    summary = run_mrms_hindcast(
        profile,
        repository_root=REPOSITORY_ROOT,
        frame_source=DryMRMSFrameSource(),
        output_directory=tmp_path,
        case_ids={"socal_dry_20210805"},
        maximum_issues=1,
    )

    assert summary["completed_issue_count"] == 1
    assert summary["failed_issue_count"] == 0
    assert summary["operational_eligible"] is False
    assert summary["motion_fallback_issue_count"] == 1
    assert summary["native_motion_fallback_issue_count"] == 1
    assert summary["phase_correlation_fallback_issue_count"] == 1
    assert summary["skill_summary"]["status"] == "insufficient_evidence"
    assert summary["coverage_summary"]["all_models_pass"] is True
    assert (tmp_path / "metrics.csv").is_file()
    assert (tmp_path / "metrics_truth_domain.csv").is_file()
    assert (tmp_path / "adaptation_metrics.csv").is_file()
    assert (tmp_path / "accumulation_metrics.csv").is_file()
    assert (tmp_path / "runtime_metrics.csv").is_file()
    assert "engineering validation" in (tmp_path / "report.md").read_text()
    persisted = json.loads((tmp_path / "summary.json").read_text())
    assert persisted["profile_version"] == "rp018-mrms-v1"
    assert persisted["schema_version"] == "1.2"
    assert len(persisted["profile_sha256"]) == 64
    assert persisted["map_bundle_count"] == 1
    assert persisted["map_layer_count"] == 60
    assert persisted["map_renderer_version"] == "algorithm-verification-map-renderer-1.0.0"
    assert persisted["runtime_fingerprint"]["packages"]["pysteps"]
    assert persisted["report_files"]["fixed_truth_domain"] == "metrics_truth_domain.csv"
    assert persisted["report_files"]["performance"] == "runtime_metrics.csv"
    assert persisted["performance_summary"]["completed_issue_count"] == 1
    assert persisted["performance_summary"]["failed_issue_count"] == 0
    assert persisted["performance_summary"]["total_runtime_ms"]["p95"] >= 0
    assert persisted["performance_summary"]["peak_rss_bytes"]["max"] > 0
    with (tmp_path / "runtime_metrics.csv").open(newline="") as handle:
        runtime_rows = list(csv.DictReader(handle))
    assert len(runtime_rows) == 1
    assert runtime_rows[0]["status"] == "completed"
    assert runtime_rows[0]["case_id"] == "socal_dry_20210805"
    assert int(runtime_rows[0]["core_runtime_ms"]) >= 0
    assert int(runtime_rows[0]["peak_rss_bytes"]) > 0
    map_index = json.loads((tmp_path / "maps" / "index.json").read_text())
    assert map_index["bundle_count"] == 1
    assert map_index["layer_count"] == 60
    issue_manifest = tmp_path / "maps" / map_index["issues"][0]["manifest_path"]
    assert issue_manifest.is_file()
    issue_payload = json.loads(issue_manifest.read_text())
    assert any(layer.get("model") == "phase_correlation" for layer in issue_payload["layers"])


def test_hindcast_records_runtime_evidence_for_failed_issue(tmp_path: Path) -> None:
    summary = run_mrms_hindcast(
        load_mrms_verification_profile(RIGOR_PROFILE_PATH),
        repository_root=REPOSITORY_ROOT,
        frame_source=FailingMRMSFrameSource(),
        output_directory=tmp_path,
        case_ids={"socal_dry_20210805"},
        maximum_issues=1,
    )

    assert summary["completed_issue_count"] == 0
    assert summary["failed_issue_count"] == 1
    assert summary["performance_summary"]["completed_issue_count"] == 0
    assert summary["performance_summary"]["failed_issue_count"] == 1
    assert summary["performance_summary"]["total_runtime_ms"]["p95"] is None
    with (tmp_path / "runtime_metrics.csv").open(newline="") as handle:
        runtime_rows = list(csv.DictReader(handle))
    assert len(runtime_rows) == 1
    assert runtime_rows[0]["status"] == "failed"
    assert runtime_rows[0]["failed_stage"] == "input_read"
    assert "synthetic missing frame" in runtime_rows[0]["error"]


def test_archive_source_rejects_a_missing_required_source_slot(tmp_path: Path) -> None:
    source = MRMSArchiveFrameSource(tmp_path, cadence_minutes=10, verify_hash=False)

    with pytest.raises(MRMSPrecipError, match=r"2021-08-10T13:50:00\+00:00"):
        source.read(
            datetime(2021, 8, 10, 13, 50, tzinfo=UTC),
            load_mrms_verification_profile(PROFILE_PATH).cases[1].grid,
        )


def test_archive_source_checks_manifest_hash_before_grib_decode(tmp_path: Path) -> None:
    valid_time = datetime(2021, 8, 29, 14, 0, tzinfo=UTC)
    relative = Path(
        "raw/noaa-mrms-pds/CONUS/PrecipRate_00.00/10min/2021/08/29/"
        "MRMS_PrecipRate_00.00_20210829-140000.grib2.gz"
    )
    asset = tmp_path / relative
    asset.parent.mkdir(parents=True)
    asset.write_bytes(b"not-a-grib")
    manifest = tmp_path / "manifests/PrecipRate_00.00/10min/2021/08/29.json"
    manifest.parent.mkdir(parents=True)
    manifest.write_text(
        json.dumps(
            {
                "assets": [
                    {
                        "relative_path": relative.as_posix(),
                        "size_bytes": len(b"not-a-grib"),
                        "sha256": "0" * 64,
                    }
                ]
            }
        )
    )

    source = MRMSArchiveFrameSource(tmp_path, cadence_minutes=10, verify_hash=True)

    with pytest.raises(MRMSPrecipError, match="sha256 mismatch"):
        source.read(valid_time, load_mrms_verification_profile(PROFILE_PATH).cases[-1].grid)


def test_conformance_checks_only_frames_required_by_selected_issues() -> None:
    profile = load_mrms_verification_profile(PROFILE_PATH)

    report = conform_mrms_cases(
        profile,
        frame_source=DryMRMSFrameSource(),
        case_ids={"socal_dry_20210805"},
        maximum_issues=1,
    )

    assert report["checked_issue_count"] == 1
    assert report["checked_frame_count"] == 15
    assert report["failed_frame_count"] == 0
    assert report["complete"] is True
    assert len(report["profile_sha256"]) == 64


def test_conformance_cli_returns_nonzero_and_json_for_missing_required_frames(
    tmp_path: Path,
    capsys,
) -> None:
    status = main(
        [
            "conformance",
            "--repository-root",
            str(REPOSITORY_ROOT),
            "--profile",
            str(PROFILE_PATH),
            "--root",
            str(tmp_path),
            "--case",
            "socal_dry_20210805",
            "--max-issues",
            "1",
            "--skip-hash",
        ]
    )

    assert status == 1
    report = json.loads(capsys.readouterr().out)
    assert report["checked_issue_count"] == 1
    assert report["failed_frame_count"] == 15
