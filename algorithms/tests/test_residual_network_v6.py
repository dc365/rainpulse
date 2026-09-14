"""Frozen V5/V6 execution and source trace, distinct from real-weather acceptance."""

from __future__ import annotations

import json
from copy import deepcopy

import numpy as np
import pytest
import zarr

from rainpulse_algo.diagnostics.polar_sampling import polar_pixels, project_rgba
from rainpulse_algo.radar.qc import load_qc_profile
from rainpulse_algo.radar.qc_engine.network_compare import compare_case, run_network
from rainpulse_algo.radar.qc_engine.network_gate import NetworkLimits, assess_network
from rainpulse_algo.radar.qc_engine.replay import replay_task
from rainpulse_algo.radar.qc_engine.trace_pixel import trace
from rainpulse_algo.radar.qc_worker import _execute_basic_qc
from rainpulse_algo.worker.domain_contracts import RadarQCRequested
from rainpulse_algo.worker.object_store import artifact_sha256

from .test_crossradar_network import build_case, c, frozen
from .test_residual_v6 import FLAGS, V5, V6


def build_v6_case(root):
    path, spec, raw, client, old_job = build_case(root)
    profile = load_qc_profile(V5, FLAGS)
    data = old_job.model_dump(mode="json")
    data["payload"].update(
        qc_profile=profile.profile_version,
        qc_pipeline_version=profile.pipeline_version,
        qc_profile_sha256=frozen(V5)["sha256"],
    )
    job = RadarQCRequested.model_validate(data)
    (root / "task.json").write_text(job.model_dump_json())
    spec.update(
        task=frozen(root / "task.json"), baseline_profile=frozen(V5), candidate_profile=frozen(V6)
    )
    path.write_text(json.dumps(spec))
    return path, spec, raw, client, job


@pytest.fixture(scope="module")
def built(tmp_path_factory):
    return build_v6_case(tmp_path_factory.mktemp("v6-frozen"))


def test_real_library_comparison_keeps_exact_v5_context_and_embedded_baseline(built):
    result = compare_case(built[0], inspect_rays=(0,))
    assert result["comparison_methods"] == ["v5", "v6"]
    assert result["context_mode"] == "identical_frozen_v5_worker_context"
    sweep = result["sweeps"][0]
    assert sweep["residual_v6"]["method"] == "native-residual-v6"
    ray = sweep["radials"][0]
    assert ray["fields"] == ray["variants"]["v6"]
    assert "V6_DECISION_REASON" in ray["variants"]["v6"]
    assert "V6_DECISION_REASON" not in ray["variants"]["v5"]


def test_v6_worker_and_local_replay_identical_bytes_and_pixel_provenance(
    built, tmp_path, monkeypatch
):
    _, spec, raw, client, old = built
    profile = load_qc_profile(V6, FLAGS)
    data = old.model_dump(mode="json")
    data["payload"].update(
        qc_profile=profile.profile_version,
        qc_pipeline_version=profile.pipeline_version,
        qc_profile_sha256=frozen(V6)["sha256"],
    )
    job = RadarQCRequested.model_validate(data)
    task = tmp_path / "job.json"
    task.write_text(job.model_dump_json())
    assets = deepcopy(spec["artifacts"])
    assets[0]["path"] = str(built[0].parent / "source.zarr")
    replay = dict(
        schema_version="rainpulse.qc-task-replay.v1",
        task=frozen(task),
        profile=frozen(V6),
        flags=frozen(FLAGS),
        artifacts=assets,
    )
    manifest = tmp_path / "replay.json"
    manifest.write_text(json.dumps(replay))
    monkeypatch.setenv("RAINPULSE_RADAR_QC_CONFIG", str(V6))
    monkeypatch.setenv("RAINPULSE_QC_FLAG_DEFINITIONS", str(FLAGS))
    receipt = replay_task(manifest, tmp_path / "out")
    actual = _execute_basic_qc(job, client)
    assert receipt["output_sha256"] == artifact_sha256(actual.objects)
    assert artifact_sha256(raw) == spec["artifacts"][0]["sha256"]
    store = zarr.storage.MemoryStore()
    store.update(actual.objects)
    group = zarr.open_group(store=store, mode="r")
    g = group["sweep_000"]
    az = g["azimuth"][:]
    r = g["range"][:]
    pixels = polar_pixels(az, r, 128)
    row, col = np.argwhere(pixels.available)[0]
    answer = trace(group, sweep="sweep_000", row=int(row), column=int(col), size=128)
    assert answer["pipeline"] == "qc-opensource-6.0.0"
    assert answer["gate_index"] == pixels.gate[row, col]
    assert answer["ray_index"] == pixels.ray[row, col]
    rgba = np.zeros((*g["DBZH_RAW"].shape, 4), "uint8")
    rgba[..., 3] = g["QPE_ELIGIBLE_MASK"][:] * 255
    image = project_rgba(rgba, az, r, 128)
    assert bool(image[row, col, 3]) == answer["eligible"]
    assert "V6_DECISION_REASON" in answer["fields"]
    row, col = np.argwhere(~pixels.available)[0]
    missing = trace(group, sweep="sweep_000", row=int(row), column=int(col), size=128)
    assert missing["fields"] == {} and missing["ray_index"] == -1
    with pytest.raises(ValueError):
        trace(group, sweep="unknown", row=0, column=0, size=128)


def test_no_retuning_hidden_in_v6_comparison(built, tmp_path):
    import yaml

    spec = deepcopy(built[1])
    cfg = yaml.safe_load(V6.read_text())
    cfg["cross_radar"]["minimum_span_m"] = 110000
    changed = tmp_path / "retuned.yaml"
    changed.write_text(yaml.safe_dump(cfg))
    spec["candidate_profile"] = frozen(changed)
    case = tmp_path / "case.json"
    case.write_text(json.dumps(spec))
    with pytest.raises(ValueError, match="retuning"):
        compare_case(case)


def test_v6_network_worst_station_and_no_synthetic_promotion(built, tmp_path):
    suite = tmp_path / "suite.json"
    suite.write_text(
        json.dumps(
            dict(
                schema_version="rainpulse.qc-network.v1",
                cases=[frozen(built[0])],
                expected_radars=["z9999", "not-tested"],
            )
        )
    )
    result = run_network(suite, tmp_path / "report", inspect_rays=(0,))
    assert result["network_gate"]["status"] == "INSUFFICIENT"
    assert result["network_gate"]["comparison_methods"] == ["v5", "v6"]
    assert result["operational_eligible"] is False
    with pytest.raises(ValueError, match="exists"):
        run_network(suite, tmp_path / "report")
    rows = []
    for radar, loss in [("good", 0), ("bad", 50)]:
        rows.append(
            dict(
                radar_id=radar,
                scan_id=radar,
                partition="validation",
                data_kind="real",
                process_id="event-" + radar,
                comparison_methods=["v5", "v6"],
                evaluation_rows=[
                    dict(range_band="0-100000m", capability_code=3, v5=c(), v6=c(weather_loss=loss))
                ],
            )
        )
    gate = assess_network(rows, ["good", "bad"], NetworkLimits(minimum_processes=1))
    assert gate["status"] == "FAIL"
    assert any(
        x["radar_id"] == "bad" and "weather_coverage_regression" in x["failures"]
        for x in gate["groups"]
    )
    rows[0]["comparison_methods"] = ["v4", "v5"]
    with pytest.raises(ValueError, match="mix"):
        assess_network(rows, ["good", "bad"], NetworkLimits())


def test_residual_counts_use_label_domain_not_fewer_remaining_pixels():
    from rainpulse_algo.radar.qc_engine.network_gate import counts

    labels = np.array([[2, 2, 0, 2, 2, 1, 2]], "uint8")
    domain = np.ones(labels.shape, bool)
    eligible = np.array([[1, 1, 1, 0, 1, 1, 1]], bool)
    rejected = np.zeros(labels.shape, bool)
    z = np.array([[10, 20, 80, 40, 50, 75, 30]], float)
    out = counts(labels, domain, rejected, eligible, 250, z)
    assert out["interference"] == 5
    assert out["residual_fragment_count"] == 3
    assert out["longest_residual_m"] == 500
    assert out["maximum_residual_dbz"] == 50  # unknown and weather high echoes excluded
    cleared = counts(labels, domain, rejected, np.zeros_like(eligible), 250, z)
    assert cleared["maximum_residual_dbz"] is None
    assert cleared["interference"] == 5 and cleared["interference_rejected"] == 0
