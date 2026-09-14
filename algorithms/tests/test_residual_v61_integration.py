"""Actual library/Worker/replay and read-only snapshot audit, on synthetic data."""

import json
from copy import deepcopy

import numpy as np
import pytest
import yaml
import zarr
from PIL import Image

from rainpulse_algo.diagnostics.polar_sampling import SAMPLING_VERSION, polar_pixels, project_rgba
from rainpulse_algo.radar.qc import load_qc_profile
from rainpulse_algo.radar.qc_engine.forensic_export import export
from rainpulse_algo.radar.qc_engine.forensic_io import directory_digest, frozen_resources
from rainpulse_algo.radar.qc_engine.network_compare import compare_case, run_network
from rainpulse_algo.radar.qc_engine.replay import replay_task
from rainpulse_algo.radar.qc_worker import _execute_basic_qc
from rainpulse_algo.worker.domain_contracts import RadarQCRequested
from rainpulse_algo.worker.object_store import artifact_sha256

from .test_crossradar_network import build_case, frozen
from .test_residual_v61_repair import FLAGS, ROOT, V6, V61


def build_case61(root):
    path, spec, raw, client, job = build_case(root)
    p = load_qc_profile(V6, FLAGS)
    task = job.model_dump(mode="json")
    task["payload"].update(
        qc_profile=p.profile_version,
        qc_pipeline_version=p.pipeline_version,
        qc_profile_sha256=frozen(V6)["sha256"],
    )
    job = RadarQCRequested.model_validate(task)
    (root / "task.json").write_text(job.model_dump_json())
    # Use a real parseable radar configuration, not a fake resource loader.
    radar = yaml.safe_load((ROOT / "configs/radars/fujian-20260828/z9591.yaml").read_text())
    radar["radar_id"] = job.payload.radar_id
    radar["config_version"] = job.payload.radar_config_version
    directory = root / "radars"
    directory.mkdir()
    (directory / f"{job.payload.radar_id}.yaml").write_text(yaml.safe_dump(radar))
    spec.update(
        task=frozen(root / "task.json"),
        baseline_profile=frozen(V6),
        candidate_profile=frozen(V61),
        resource_environment={
            "RAINPULSE_RADAR_CONFIG_DIR": {
                "path": str(directory),
                "sha256": directory_digest(directory),
            }
        },
    )
    path.write_text(json.dumps(spec))
    return path, spec, raw, client, job


@pytest.fixture(scope="module")
def built(tmp_path_factory):
    return build_case61(tmp_path_factory.mktemp("v61-case"))


@pytest.mark.parametrize("mode", ["shared_baseline", "shared_candidate", "each_profile"])
def test_comparison_explicit_context_modes_with_actual_resources(built, mode):
    result = compare_case(built[0], context_mode=mode)
    assert result["comparison_methods"] == ["v6", "v61"]
    assert (
        result["context_arrays_by_method"]["v6"]["sha256"]
        == result["context_arrays_by_method"]["v61"]["sha256"]
    )
    assert result["frozen_resources"]["RAINPULSE_RADAR_CONFIG_DIR"]["kind"] == "directory"
    if mode != "shared_baseline":
        assert result["context_by_method"]["v61"]["geometry_resources"]["beam_status"] == "loaded"
    assert "V61_REVIEW_OUTCOME" in result["sweeps"][0]["radials"][0]["fields"]
    assert result["outputs"]["v61"]["serialize_validate_ms"] >= 0


def test_empty_labels_and_missing_station_do_not_pass_acceptance(built, tmp_path):
    spec = deepcopy(built[1])
    spec["labels"] = {}
    case = tmp_path / "case.json"
    spec["artifacts"][0]["path"] = str(built[0].parent / "source.zarr")
    case.write_text(json.dumps(spec))
    suite = tmp_path / "suite.json"
    suite.write_text(
        json.dumps(
            dict(
                schema_version="rainpulse.qc-network.v1",
                cases=[frozen(case)],
                expected_radars=["z9999", "missing"],
            )
        )
    )
    report = run_network(suite, tmp_path / "out")
    assert report["network_gate"]["status"] == "INSUFFICIENT"
    assert not report["operational_eligible"]
    assert report["cases"][0]["sweeps"][0]["methods"]["v61"]["measurement_metrics"] is None


def test_resource_checksums_no_ambient_credentials_and_restore(tmp_path, monkeypatch):
    monkeypatch.setenv("RAINPULSE_RADAR_CONFIG_DIR", "/unfrozen/ambient")
    with frozen_resources(tmp_path, {}):
        import os

        assert "RAINPULSE_RADAR_CONFIG_DIR" not in os.environ
    assert os.environ["RAINPULSE_RADAR_CONFIG_DIR"] == "/unfrozen/ambient"
    with pytest.raises(ValueError):
        with frozen_resources(
            tmp_path, {"RAINPULSE_OBJECT_STORE_SECRET_KEY": {"path": "x", "sha256": "0" * 64}}
        ):
            pass
    d = tmp_path / "radars"
    d.mkdir()
    (d / "one.yaml").write_text("one")
    spec = {"RAINPULSE_RADAR_CONFIG_DIR": {"path": str(d), "sha256": directory_digest(d)}}
    with pytest.raises(ValueError, match="checksum"):
        with frozen_resources(tmp_path, spec):
            (d / "one.yaml").write_text("changed")
    assert os.environ["RAINPULSE_RADAR_CONFIG_DIR"] == "/unfrozen/ambient"


@pytest.fixture(scope="module")
def worker_bundle(built, tmp_path_factory):
    root = tmp_path_factory.mktemp("v61-worker")
    p = load_qc_profile(V61, FLAGS)
    task = built[4].model_dump(mode="json")
    task["payload"].update(
        qc_profile=p.profile_version,
        qc_pipeline_version=p.pipeline_version,
        qc_profile_sha256=frozen(V61)["sha256"],
    )
    job = RadarQCRequested.model_validate(task)
    with pytest.MonkeyPatch.context() as monkey:
        monkey.setenv("RAINPULSE_RADAR_QC_CONFIG", str(V61))
        monkey.setenv("RAINPULSE_QC_FLAG_DEFINITIONS", str(FLAGS))
        with frozen_resources(built[0].parent, built[1]["resource_environment"]):
            actual = _execute_basic_qc(job, built[3])
            (root / "task.json").write_text(job.model_dump_json())
            assets = deepcopy(built[1]["artifacts"])
            assets[0]["path"] = str(built[0].parent / "source.zarr")
            manifest = root / "replay.json"
            manifest.write_text(
                json.dumps(
                    dict(
                        schema_version="rainpulse.qc-task-replay.v1",
                        task=frozen(root / "task.json"),
                        profile=frozen(V61),
                        flags=frozen(FLAGS),
                        artifacts=assets,
                    )
                )
            )
            receipt = replay_task(manifest, root / "replayed")
    assert receipt["output_sha256"] == artifact_sha256(actual.objects)
    assert actual.observability["qc_core_ms"] >= 0
    return root / "replayed/qc.zarr", receipt


def test_worker_uses_audited_geometry_and_replay_identical_bytes(worker_bundle):
    receipt = worker_bundle[1]
    context = receipt["summary"]["radial_context"]
    assert context["geometry_resources"]["beam_status"] == "loaded"
    assert context["geometry_resources"]["terrain_status"] == "not_configured"
    assert context["prepared_context"]["sha256"]
    assert receipt["published"] is False


def manifest_for_png(q, path):
    root = zarr.open_group(str(q), mode="r")
    g = root["sweep_000"]
    rgba = np.zeros((*g["DBZH_RAW"].shape, 4), "uint8")
    rgba[..., :3] = 100
    rgba[..., 3] = g["QPE_ELIGIBLE_MASK"][:] * 255
    png = path / "original.png"
    Image.fromarray(project_rgba(rgba, g["azimuth"][:], g["range"][:], 640)).save(png)
    pixels = polar_pixels(g["azimuth"][:], g["range"][:], 640)
    row, col = np.argwhere(pixels.available)[0]
    layer = path / "layer.json"
    layer.write_text(
        json.dumps(
            dict(
                sampling_version=SAMPLING_VERSION,
                field="DBZH_QC",
                scope="polar",
                scan_id=str(root.attrs["scan_id"]),
                radar_id=root.attrs["radar_id"],
                sweep_number=0,
            )
        )
    )
    panel = dict(
        panel_id="test",
        analysis_id="analysis-1",
        expected_qc_asset_id=str(root.attrs["asset_id"]),
        qc_zarr={"path": str(q), "sha256": directory_digest(q)},
        png=frozen(png),
        layer=frozen(layer),
        sweep="sweep_000",
        samples=[dict(label="synthetic; not a real ray selection", row=int(row), column=int(col))],
    )
    manifest = path / "forensic.json"
    manifest.write_text(json.dumps(dict(schema_version="rainpulse.qc-forensic.v1", panels=[panel])))
    return manifest, panel


def test_forensic_reads_all_fields_and_deduplicates_same_scan(worker_bundle, tmp_path):
    manifest, panel = manifest_for_png(worker_bundle[0], tmp_path)
    other = {**panel, "panel_id": "same-scan-another-analysis", "analysis_id": "analysis-2"}
    manifest.write_text(
        json.dumps(dict(schema_version="rainpulse.qc-forensic.v1", panels=[panel, other]))
    )
    result = export(manifest, tmp_path / "evidence")
    assert result["unique_physical_scan_count"] == 1
    assert result["accuracy"] is None and result["recall"] is None
    assert result["same_scan_pairs"][0]["same_observation_arrays"]
    assert result["same_scan_pairs"][0]["same_decision_arrays"]
    point = result["panels"][0]["samples"][0]
    assert all(
        k in point["fields"]
        for k in ("RHOHV_RAW", "ZDR_RAW", "SNR_RAW", "QC_FLAGS", "V61_REVIEW_OUTCOME")
    )
    assert not point["display_contradiction"]
    assert point["sampling_verified_against_png"] and point["truth_label"] is None
    with pytest.raises(ValueError, match="exists"):
        export(manifest, tmp_path / "evidence")


@pytest.mark.parametrize("bad", ["size", "hash", "sampling", "sweep", "asset"])
def test_forensic_rejects_unbound_or_wrong_pngs(worker_bundle, tmp_path, bad):
    manifest, panel = manifest_for_png(worker_bundle[0], tmp_path)
    if bad == "size":
        Image.new("RGBA", (320, 320)).save(tmp_path / "original.png")
        panel["png"] = frozen(tmp_path / "original.png")
    if bad == "hash":
        panel["png"]["sha256"] = "0" * 64
    if bad == "asset":
        panel["expected_qc_asset_id"] = "wrong"
    if bad in {"sampling", "sweep"}:
        layer = json.loads((tmp_path / "layer.json").read_text())
        layer["sampling_version" if bad == "sampling" else "sweep_number"] = (
            "wrong" if bad == "sampling" else 1
        )
        (tmp_path / "layer.json").write_text(json.dumps(layer))
        panel["layer"] = frozen(tmp_path / "layer.json")
    manifest.write_text(json.dumps(dict(schema_version="rainpulse.qc-forensic.v1", panels=[panel])))
    with pytest.raises(ValueError):
        export(manifest, tmp_path / "failed")
    assert not (tmp_path / "failed").exists()


def test_forensic_native_datetime64_ray_times_keep_nanosecond_precision(worker_bundle, tmp_path):
    import shutil

    q = tmp_path / "dated.zarr"
    shutil.copytree(worker_bundle[0], q)
    root = zarr.open_group(str(q), mode="a")
    g = root["sweep_000"]
    del g["ray_time"]
    times = np.datetime64("2026-08-28T00:15:00.123456789", "ns") + np.arange(len(g["azimuth"]))
    g.create_dataset("ray_time", data=times)
    g["ray_time"].attrs["timezone"] = "UTC"
    manifest, _ = manifest_for_png(q, tmp_path)
    out = export(manifest, tmp_path / "evidence")
    point = out["panels"][0]["samples"][0]
    assert point["source_ray_time"] == np.datetime_as_string(
        times[point["ray_index"]], unit="ns", timezone="UTC"
    )
    assert out["panels"][0]["semantic_hashes"]["observation"]["ray_time"]


def test_saved_comparison_artifacts_preserve_explicit_context(built, tmp_path):
    result = compare_case(built[0], bundle_dir=tmp_path, context_mode="each_profile")
    group = zarr.open_group(str(tmp_path / "v61/qc.zarr"), mode="r")
    context = json.loads(group.attrs["radial_context"])
    assert context["prepared_context"] == result["context_arrays_by_method"]["v61"]
    assert group.attrs["comparison_context_mode"] == "each_profile"
