"""Frozen Worker comparison, leakage guards and worst-group gate tests."""

from __future__ import annotations

import hashlib
import json
from copy import deepcopy
from pathlib import Path

import numpy as np
import pytest

from rainpulse_algo.radar.qc import load_qc_profile
from rainpulse_algo.radar.qc_engine.network_compare import compare_case, run_network
from rainpulse_algo.radar.qc_engine.network_gate import NetworkLimits, assess_network, counts
from rainpulse_algo.radar.qc_engine.replay import replay_task
from rainpulse_algo.radar.qc_worker import _execute_basic_qc
from rainpulse_algo.worker.domain_contracts import RadarQCRequested
from rainpulse_algo.worker.object_store import artifact_sha256

from .test_crossradar_v5 import FLAGS, ROOT, V5, long_scene
from .test_object_store import FakeMinio
from .test_radar_qc import synthetic_normalized_fixture
from .test_rfi_objects_context import mount, request, stamp

V4 = ROOT / "configs/qc/fujian-qc-paper-fusion-v4.yaml"


def frozen(p):
    return dict(path=str(p), sha256=hashlib.sha256(Path(p).read_bytes()).hexdigest())


def build_case(root):
    root.mkdir(parents=True, exist_ok=True)
    n = long_scene(ceiling=70.0, dr=1000.0)
    raw = synthetic_normalized_fixture(
        n.fields["DBZH"],
        azimuth_deg=n.azimuth,
        range_m=n.ranges,
        moments={k: v for k, v in n.fields.items() if k != "DBZH"},
    )
    raw = stamp(raw, "2026-08-28T00:40:00Z")
    client = FakeMinio()
    uri = mount(client, raw, "current")
    job = request(raw, uri, [])
    p4 = load_qc_profile(V4, FLAGS)
    data = job.model_dump(mode="json")
    data["payload"].update(
        qc_profile=p4.profile_version,
        qc_pipeline_version=p4.pipeline_version,
        qc_profile_sha256=frozen(V4)["sha256"],
    )
    job = RadarQCRequested.model_validate(data)
    (root / "task.json").write_text(job.model_dump_json())
    for k, v in raw.items():
        file = root / "source.zarr" / k
        file.parent.mkdir(parents=True, exist_ok=True)
        file.write_bytes(v)
    labels = np.zeros(n.shape, "uint8")
    labels[:, n.ranges >= 30000] = 2
    np.save(root / "labels.npy", labels)
    manifest = dict(
        schema_version="rainpulse.qc-network-case.v1",
        case_id="synthetic-only",
        partition="development",
        process_id="synthetic-signal-not-weather",
        data_kind="synthetic",
        task=frozen(root / "task.json"),
        baseline_profile=frozen(V4),
        candidate_profile=frozen(V5),
        flags=frozen(FLAGS),
        artifacts=[dict(uri=uri, path="source.zarr", sha256=artifact_sha256(raw))],
        labels={"sweep_000": frozen(root / "labels.npy")},
    )
    path = root / "case.json"
    path.write_text(json.dumps(manifest))
    return path, manifest, raw, client, job


@pytest.fixture(scope="module")
def built(tmp_path_factory):
    root = tmp_path_factory.mktemp("v5-network")
    return build_case(root)


def test_actual_frozen_worker_comparison_and_separate_withholding_metrics(built):
    path, _, _, _, _ = built
    result = compare_case(path, inspect_rays=(0,))
    sweep = result["sweeps"][0]
    assert (
        sweep["methods"]["v5"]["quantitative_eligible_gates"]
        < sweep["methods"]["v4"]["quantitative_eligible_gates"]
    )
    assert sweep["methods"]["v5"]["measurement_metrics"]["interference_recall"] == 0
    assert sweep["methods"]["v5"]["withheld_measurement_metrics"]["interference_recall"] > 0.98
    assert sweep["decision_funnel"]["rows"]
    assert "V5_CAPABILITY_CODE" in sweep["radials"][0]["variants"]["v5"]
    assert result["context_mode"] == "identical_frozen_v4_worker_context"


def test_atomic_network_output_is_not_promoted_and_is_not_overwritten(built, tmp_path):
    path, _, _, _, _ = built
    spec = dict(
        schema_version="rainpulse.qc-network.v1",
        cases=[frozen(path)],
        expected_radars=["z9999", "missing-station"],
    )
    suite = tmp_path / "suite.json"
    suite.write_text(json.dumps(spec))
    report = run_network(suite, tmp_path / "out")
    assert report["network_gate"]["status"] == "INSUFFICIENT"
    assert report["operational_eligible"] is False
    assert (tmp_path / "out/report.json").exists()
    with pytest.raises(ValueError, match="already exists"):
        run_network(suite, tmp_path / "out")
    spec["cases"] *= 2
    suite.write_text(json.dumps(spec))
    with pytest.raises(ValueError, match="duplicate physical"):
        run_network(suite, tmp_path / "bad")
    assert not (tmp_path / "bad").exists()


def test_profile_hash_changes_and_retargeting_are_rejected(built, tmp_path):
    _, spec, _, _, _ = built
    bad = deepcopy(spec)
    bad["candidate_profile"]["sha256"] = "0" * 64
    file = tmp_path / "bad.json"
    file.write_text(json.dumps(bad))
    with pytest.raises(ValueError, match="checksum"):
        compare_case(file)


def test_actual_v5_worker_and_offline_replay_have_identical_artifact_bytes(
    built, tmp_path, monkeypatch
):
    _, spec, raw, client, job = built
    p5 = load_qc_profile(V5, FLAGS)
    data = job.model_dump(mode="json")
    data["payload"].update(
        qc_profile=p5.profile_version,
        qc_pipeline_version=p5.pipeline_version,
        qc_profile_sha256=frozen(V5)["sha256"],
    )
    v5job = RadarQCRequested.model_validate(data)
    task = tmp_path / "task.json"
    task.write_text(v5job.model_dump_json())
    artifacts = deepcopy(spec["artifacts"])
    artifacts[0]["path"] = str(built[0].parent / "source.zarr")
    manifest = dict(
        schema_version="rainpulse.qc-task-replay.v1",
        task=frozen(task),
        profile=frozen(V5),
        flags=frozen(FLAGS),
        artifacts=artifacts,
    )
    file = tmp_path / "replay.json"
    file.write_text(json.dumps(manifest))
    monkeypatch.setenv("RAINPULSE_RADAR_QC_CONFIG", str(V5))
    monkeypatch.setenv("RAINPULSE_QC_FLAG_DEFINITIONS", str(FLAGS))
    result = replay_task(file, tmp_path / "replayed")
    actual = _execute_basic_qc(v5job, client)
    assert result["output_sha256"] == artifact_sha256(actual.objects)
    second = _execute_basic_qc(v5job, client)
    assert artifact_sha256(second.objects) == artifact_sha256(actual.objects)
    assert artifact_sha256(raw) == spec["artifacts"][0]["sha256"]


def c(weather=1000, interference=1000, rejected=1000, weather_loss=0):
    return dict(
        weather=weather,
        interference=interference,
        mixed=0,
        uncertain=0,
        strong_weather=weather,
        strong_weather_rejected=0,
        strong_weather_withheld=weather_loss,
        weather_rejected=0,
        weather_withheld=weather_loss,
        interference_rejected=rejected,
        interference_withheld=interference,
        mixed_withheld=0,
        longest_residual_m=0,
    )


def case(radar, scan, *, old=None, new=None, kind="real", process="p"):
    return dict(
        radar_id=radar,
        scan_id=scan,
        data_kind=kind,
        process_id=process,
        partition="validation",
        evaluation_rows=[
            dict(range_band="0-100000m", capability_code=3, v4=old or c(), v5=new or c())
        ],
    )


def test_worst_station_failure_cannot_be_hidden_by_network_average():
    cases = [case("good", str(i), process=str(i)) for i in range(10)]
    cases.append(case("bad", "x", new=c(rejected=100), process="x"))
    result = assess_network(cases, ["good", "bad"], NetworkLimits(minimum_processes=1))
    assert result["status"] == "FAIL"
    assert any(x["radar_id"] == "bad" and x["status"] == "FAIL" for x in result["groups"])


def test_quarantine_is_coverage_loss_not_confirmed_recall():
    result = assess_network(
        [case("test", "a", new=c(rejected=0, weather_loss=1000))],
        ["test"],
        NetworkLimits(minimum_processes=1),
    )
    assert result["status"] == "FAIL"
    assert result["groups"][0]["v5"]["interference_recall"] == 0
    assert result["groups"][0]["v5"]["interference_withheld_rate"] == 1


def test_missing_station_synthetic_and_no_labels_cannot_pass():
    limits = NetworkLimits(minimum_processes=1)
    assert assess_network([case("a", "1")], ["a", "b"], limits)["status"] == "INSUFFICIENT"
    assert (
        assess_network([case("a", "1", kind="synthetic")], ["a"], limits)["status"]
        == "INSUFFICIENT"
    )
    assert (
        assess_network(
            [
                case(
                    "a",
                    "1",
                    old=c(weather=0, interference=0, rejected=0),
                    new=c(weather=0, interference=0, rejected=0),
                )
            ],
            ["a"],
            limits,
        )["status"]
        == "INSUFFICIENT"
    )
    with pytest.raises(ValueError):
        assess_network([case("a", "1"), case("a", "1")], ["a"], limits)


def test_fixed_denominator_counts_do_not_use_algorithm_valid_mask():
    labels = np.array([[1, 1, 2, 2, 3, 0]], "uint8")
    domain = np.ones(labels.shape, bool)
    result = counts(labels, domain, np.zeros_like(domain), np.zeros_like(domain), 1000)
    assert result["weather"] == 2 and result["weather_withheld"] == 2
    assert result["interference"] == 2 and result["interference_rejected"] == 0
    assert result["interference_withheld"] == 2


def test_strong_weather_and_process_support_are_explicit(built):
    labels = np.array([[1, 1, 2]], "uint8")
    domain = np.ones(labels.shape, bool)
    rejected = np.zeros_like(domain)
    eligible = np.array([[False, True, False]])
    c1 = counts(labels, domain, rejected, eligible, 250, np.array([[40.0, 20.0, 50.0]]))
    assert c1["strong_weather"] == 1 and c1["strong_weather_withheld"] == 1
    lim = NetworkLimits(minimum_processes=1)
    result = assess_network([case("a", "1", new=c(weather_loss=10))], ["a"], lim)
    assert "strong_weather_coverage_loss" in result["groups"][0]["failures"]
    enough = assess_network(
        [case("a", "1", process="p1"), case("a", "2", process="p2")], ["a"], NetworkLimits()
    )
    assert enough["status"] == "PASS" and enough["operational_eligible"] is False
    unlabeled = c(weather=0, interference=0, rejected=0)
    insufficient = assess_network(
        [case("a", "1", process="p1"), case("a", "2", process="p2", old=unlabeled, new=unlabeled)],
        ["a"],
        NetworkLimits(),
    )
    assert insufficient["status"] == "INSUFFICIENT"


def test_generated_network_schema_and_runtime_contracts_agree(built):
    import jsonschema

    from rainpulse_algo.radar.qc_engine.network_manifest import NetworkCaseManifest, NetworkManifest

    path, spec, _, _, _ = built
    for name, model, data in [
        ("qc-network-case-v1", NetworkCaseManifest, spec),
        (
            "qc-network-v1",
            NetworkManifest,
            dict(
                schema_version="rainpulse.qc-network.v1",
                cases=[frozen(path)],
                expected_radars=["test"],
            ),
        ),
    ]:
        schema = json.loads((ROOT / "configs/schemas" / (name + ".schema.json")).read_text())
        assert schema == model.model_json_schema()
        jsonschema.validate(data, schema)
        model.model_validate(data)
        bad = {**data, "unexpected_key": True}
        with pytest.raises(ValueError):
            model.model_validate(bad)


def test_degraded_health_retains_exact_final_v4_qualification(built):
    from rainpulse_algo.radar.qc import apply_basic_qc

    raw = dict(built[2])
    health = json.loads(raw["health/summary.json"])
    health["health"] = "DEGRADED"
    raw["health/summary.json"] = json.dumps(health).encode()
    old = apply_basic_qc(raw, load_qc_profile(V4, FLAGS))
    new = apply_basic_qc(raw, load_qc_profile(V5, FLAGS))
    for a, b in zip(old.sweeps, new.sweeps, strict=True):
        for key, original in [
            ("V5_BASELINE_ELIGIBLE_MASK", "QPE_ELIGIBLE_MASK"),
            ("V5_BASELINE_QUARANTINE_MASK", "RFI_QUARANTINE_MASK"),
        ]:
            np.testing.assert_array_equal(b.optional_qc_fields[key], a.optional_qc_fields[original])
