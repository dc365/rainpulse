from __future__ import annotations

import hashlib
import json
import subprocess
import sys
from pathlib import Path

import numpy as np

ROOT = Path(__file__).resolve().parents[2]


def run_cli(*args: str) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        [sys.executable, str(ROOT / "scripts/radar_qc_b3.py"), *args],
        cwd=ROOT,
        text=True,
        capture_output=True,
        check=False,
    )


def test_b3_cli_builds_labels_and_refuses_incomplete_promotion(tmp_path: Path) -> None:
    labels = tmp_path / "labels.npz"
    np.savez(labels, label_values=np.array([[0, 1, -1]]))
    manifest = tmp_path / "input.json"
    manifest.write_text(
        json.dumps(
            {
                "entries": [
                    {
                        "process_id": "development-1",
                        "partition": "development",
                        "case_category": "clear_sky",
                        "radar_id": "r1",
                        "scan_id": "s1",
                        "volume_end_time_utc": "2026-09-08T00:00:00Z",
                        "input_uri": "fixture://normalized/s1",
                        "annotator": "reviewer",
                        "label_source": "fixture://labels",
                        "label_version": "1",
                        "review_status": "verified",
                        "label_npz": labels.name,
                        "label_sha256": hashlib.sha256(labels.read_bytes()).hexdigest(),
                    }
                ]
            }
        )
    )
    output = tmp_path / "labels.json"
    result = run_cli(
        "labels",
        "--input",
        str(manifest),
        "--output",
        str(output),
        "--frozen-config-sha256",
        "a" * 64,
        "--frozen-code-revision",
        "test-revision",
    )
    assert result.returncode == 0, result.stderr
    built = json.loads(output.read_text())
    assert built["scans"][0]["label_counts"] == {
        "meteorological": 1,
        "non_meteorological": 1,
        "uncertain": 1,
    }
    metrics = tmp_path / "metrics.json"
    metrics.write_text(
        json.dumps(
            {"frozen_config_sha256": "a" * 64, "frozen_code_revision": "test-revision", "rows": []}
        )
    )
    report = tmp_path / "promotion.json"
    result = run_cli(
        "promotion",
        "--input",
        str(metrics),
        "--label-manifest",
        str(output),
        "--profile",
        str(ROOT / "configs/verification/fujian-qc-promotion-v1.yaml"),
        "--output",
        str(report),
    )
    assert result.returncode == 3, result.stderr
    assert json.loads(report.read_text())["overall_status"] == "insufficient_data"


def test_clutter_cli_outputs_station_support_and_no_overwrite(tmp_path: Path) -> None:
    sample = tmp_path / "sample.npz"
    np.savez(
        sample, dbzh=np.array([[15.0]]), azimuth_deg=np.array([0.0]), range_m=np.array([250.0])
    )
    manifest = tmp_path / "samples.json"
    manifest.write_text(
        json.dumps(
            {
                "samples": [
                    {
                        "radar_id": "r1",
                        "sweep_name": "sweep_000",
                        "elevation_deg": 0.5,
                        "case_category": "clear_sky",
                        "observed_at_utc": "2026-09-08T00:00:00Z",
                        "npz_path": sample.name,
                        "source_uri": "fixture://clear-sky-evidence",
                        "sha256": hashlib.sha256(sample.read_bytes()).hexdigest(),
                    }
                ]
            }
        )
    )
    output = tmp_path / "clutter"
    result = run_cli("clutter", "--input", str(manifest), "--output", str(output))
    assert result.returncode == 0, result.stderr
    assert json.loads((output / "manifest.json").read_text())["radar_id"] == "r1"
    with np.load(output / "support.npz") as arrays:
        assert arrays["sweep_000"][0, 0] == 1
    with np.load(output / "prior.npz") as arrays:
        assert np.isnan(arrays["sweep_000__ground_clutter"]).all()
    assert run_cli("clutter", "--input", str(manifest), "--output", str(output)).returncode == 2
