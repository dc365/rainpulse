from __future__ import annotations

import hashlib
import json
from datetime import UTC, datetime
from pathlib import Path

import pytest
import yaml

from rainpulse_algo.grid import RegularLatLonGrid
from rainpulse_algo.verification.mrms_ensemble_profile import (
    MRMSEnsembleProfileError,
    load_mrms_ensemble_profile,
)

REPOSITORY_ROOT = Path(__file__).resolve().parents[2]


def _sha(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def _evidence(path: Path, role: str, month: str) -> None:
    grid = RegularLatLonGrid(
        grid_id=f"grid-{role}",
        config_version="test-grid-v1",
        west=-100.0,
        east=-95.0,
        south=35.0,
        north=37.0,
        longitude_interval_deg=0.01,
        latitude_interval_deg=0.01,
        longitude_count=501,
        latitude_count=201,
        reference_latitude_deg=36.0,
        ancillary_domain_id="mrms-validation-conus-v1",
    )
    prefix = "development" if role == "development" else "holdout"
    start = datetime(int(month[:4]), int(month[5:]), 10, tzinfo=UTC)
    payload = {
        "schema_version": "1.0",
        "selection_protocol_version": "mrms-observation-split-v2",
        "selection_role": role,
        "model_forecast_or_skill_fields_read": False,
        "source": {"months": [month]},
        "selected_case_count": 1,
        "selected_issue_count": 1,
        "selected_cases": [
            {
                "case_id": f"{prefix}_{start:%Y%m%d}_test_wet_1",
                "label": "test",
                "category": "wet",
                "grid": {
                    "grid_id": grid.grid_id,
                    "config_version": grid.config_version,
                    "bounds": {
                        "west": grid.west,
                        "east": grid.east,
                        "south": grid.south,
                        "north": grid.north,
                    },
                    "coordinate_sha256": grid.coordinate_sha256,
                },
                "issues": {
                    "start": start.isoformat().replace("+00:00", "Z"),
                    "end": start.isoformat().replace("+00:00", "Z"),
                    "step_minutes": 10,
                },
            }
        ],
    }
    path.write_text(json.dumps(payload), encoding="utf-8")


def _profile(tmp_path: Path) -> Path:
    development = tmp_path / "development.json"
    holdout = tmp_path / "holdout.json"
    _evidence(development, "development", "2022-01")
    _evidence(holdout, "independent_holdout", "2023-06")
    source_steps = REPOSITORY_ROOT / "configs/nowcast/rp022-pysteps-steps-v1.yaml"
    source_lk = REPOSITORY_ROOT / "configs/nowcast/rp016-pysteps-lk-v1.yaml"
    steps = tmp_path / "configs/nowcast/rp022-pysteps-steps-v1.yaml"
    lk = tmp_path / "configs/nowcast/rp016-pysteps-lk-v1.yaml"
    steps.parent.mkdir(parents=True)
    steps.write_bytes(source_steps.read_bytes())
    lk.write_bytes(source_lk.read_bytes())
    profile = {
        "schema_version": "1.0",
        "profile_version": "rp024-test-v1",
        "lifecycle": "engineering_validation",
        "operational_eligible": False,
        "source": {"cadence_minutes": 10},
        "forecast": {
            "steps_profile": str(steps.relative_to(tmp_path)),
            "steps_profile_sha256": _sha(steps),
            "lk_profile": str(lk.relative_to(tmp_path)),
            "lk_profile_sha256": _sha(lk),
            "member_count": 12,
            "compute_halo_cells": 100,
        },
        "splits": {
            "development": {
                "months": ["2022-01"],
                "selection_evidence_path": development.name,
                "selection_evidence_sha256": _sha(development),
                "expected_case_count": 1,
                "expected_issue_count": 1,
            },
            "holdout": {
                "months": ["2023-06"],
                "selection_evidence_path": holdout.name,
                "selection_evidence_sha256": _sha(holdout),
                "expected_case_count": 1,
                "expected_issue_count": 1,
            },
        },
        "verification": {
            "lead_minutes": list(range(10, 121, 10)),
            "thresholds_mm_h": [1, 5, 10, 20, 50],
            "reliability_bin_edges": [index / 10 for index in range(11)],
            "near_minutes": [10, 60],
            "far_minutes": [70, 120],
            "baselines": ["lk", "persistence", "phase_correlation"],
        },
        "leakage": {"forbidden_months": ["2021-08", "2024-06", "2025-01"]},
        "holdout_gate": {
            "status": "development_pending",
            "artifact_path": None,
            "artifact_sha256": None,
        },
    }
    path = tmp_path / "profile.yaml"
    path.write_text(yaml.safe_dump(profile, sort_keys=False), encoding="utf-8")
    return path


def test_profile_freezes_disjoint_observation_only_splits(tmp_path: Path) -> None:
    profile = load_mrms_ensemble_profile(_profile(tmp_path), repository_root=tmp_path)

    assert profile.member_count == 12
    assert profile.compute_halo_cells == 100
    assert profile.development.months == ("2022-01",)
    assert profile.holdout.months == ("2023-06",)
    assert profile.gate.status == "development_pending"
    assert profile.operational_eligible is False


def test_profile_rejects_spent_or_overlapping_months(tmp_path: Path) -> None:
    path = _profile(tmp_path)
    raw = yaml.safe_load(path.read_text())
    raw["leakage"]["forbidden_months"].append("2023-06")
    path.write_text(yaml.safe_dump(raw, sort_keys=False), encoding="utf-8")

    with pytest.raises(MRMSEnsembleProfileError, match="leakage"):
        load_mrms_ensemble_profile(path, repository_root=tmp_path)
