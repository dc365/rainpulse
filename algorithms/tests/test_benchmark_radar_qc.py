import json
import subprocess
import sys
from pathlib import Path

REPOSITORY_ROOT = Path(__file__).resolve().parents[2]
BENCHMARK_SCRIPT = REPOSITORY_ROOT / "scripts" / "benchmark_radar_qc.py"


def test_benchmark_radar_qc_generates_json_report(tmp_path: Path) -> None:
    output_path = tmp_path / "radar-qc-benchmark.json"

    completed = subprocess.run(
        [
            sys.executable,
            str(BENCHMARK_SCRIPT),
            "--shape",
            "48x160",
            "--warmup-runs",
            "0",
            "--measurement-runs",
            "1",
            "--output",
            str(output_path),
        ],
        check=True,
        capture_output=True,
        text=True,
        cwd=REPOSITORY_ROOT,
    )

    stdout_report = json.loads(completed.stdout)
    file_report = json.loads(output_path.read_text(encoding="utf-8"))

    assert file_report == stdout_report
    assert stdout_report["metadata"]["shape"] == {"rays": 48, "gates": 160}
    assert stdout_report["metadata"]["measurement_runs"] == 1
    assert stdout_report["timings"]["context_seconds"]["count"] == 1
    assert stdout_report["timings"]["core_seconds"]["count"] == 1
    assert stdout_report["timings"]["serialization_validation_seconds"]["count"] == 1
    assert stdout_report["timings"]["object_io_seconds"]["count"] == 1
    assert stdout_report["timings"]["diagnostic_read_seconds"]["count"] == 1
    assert stdout_report["object_io"]["input_get_count"]["mean"] > 0
    assert stdout_report["object_io"]["context_get_count"]["mean"] > 0
    assert stdout_report["object_io"]["publish_put_count"]["mean"] > 0
    assert stdout_report["object_io"]["diagnostic_get_count"]["mean"] > 0
    assert stdout_report["sample_run"]["context_fingerprint"]
    assert stdout_report["sample_run"]["zarr_write_settings"]["layout"] == "64x512"
    assert stdout_report["sample_run"]["validation"]["sweep_count"] == 1
    assert stdout_report["storage_layout_experiment"]["baseline_layout"] == "64x512"
    assert stdout_report["storage_layout_experiment"]["selected_layout"]
    assert "128x1024" in stdout_report["storage_layout_experiment"]["layouts"]
    assert "full-ray-range" in stdout_report["storage_layout_experiment"]["layouts"]
    assert stdout_report["sample_run"]["publish_result"]["artifact_uri"].endswith("/volume.zarr")
