"""Real installed-library / frozen-task tests, not meteorological skill validation."""

from __future__ import annotations

import hashlib
import json
import warnings

import numpy as np
import pytest
import yaml

from rainpulse_algo.radar.qc import apply_basic_qc, load_qc_profile
from rainpulse_algo.radar.qc_engine.paper_compare import compare_task, run_comparison
from rainpulse_algo.radar.qc_engine.paper_validation import validate_paper_fields
from rainpulse_algo.radar.qc_input import open_qc_input
from rainpulse_algo.radar.qc_zarr import build_qc_zarr_store, validate_qc_zarr_store
from rainpulse_algo.worker.domain_contracts import RadarQCRequested
from rainpulse_algo.worker.object_store import artifact_sha256

from .test_object_store import FakeMinio
from .test_qc_paper_algorithms import FLAGS, V3, V4, config
from .test_rfi_objects_context import mount, request, stamp
from .test_rfi_objects_v2 import scene


def frozen(path):
    return {"path": str(path), "sha256": hashlib.sha256(path.read_bytes()).hexdigest()}


@pytest.fixture(scope="module")
def evaluated(tmp_path_factory):
    root = tmp_path_factory.mktemp("paper-case")
    objects, target = scene(rho=0.99)
    current = stamp(objects, "2026-08-28T00:40:00Z")
    client = FakeMinio()
    uri = mount(client, current, "current")
    job = request(current, uri, [])
    data = job.model_dump(mode="json")
    profile = load_qc_profile(V3, FLAGS)
    data["payload"].update(
        qc_profile=profile.profile_version,
        qc_pipeline_version=profile.pipeline_version,
        qc_profile_sha256=hashlib.sha256(V3.read_bytes()).hexdigest(),
    )
    job = RadarQCRequested.model_validate(data)
    (root / "task.json").write_text(job.model_dump_json())
    for key, val in current.items():
        file = root / "source.zarr" / key
        file.parent.mkdir(parents=True, exist_ok=True)
        file.write_bytes(val)
    manifest = {
        "schema_version": "rainpulse.qc-paper-comparison.v1",
        "case_id": "synthetic-only",
        "partition": "development",
        "process_id": "synthetic-not-a-weather-process",
        "task": frozen(root / "task.json"),
        "flags": frozen(FLAGS),
        "v3_profile": frozen(V3),
        "fusion_profile": frozen(V4),
        "artifacts": [{"uri": uri, "path": "source.zarr", "sha256": artifact_sha256(current)}],
    }
    report, bundles = compare_task(manifest, root, inspect_rays=(110,))
    return root, current, target, manifest, report, bundles


def test_actual_libraries_and_common_frozen_context_are_used(evaluated):
    _, _, _, _, report, bundles = evaluated
    case = report["cases"][0]
    assert case["context_mode"] == "identical_frozen_v3_worker_context"
    assert set(bundles) == {"v3", "paper_fusion_v4"}
    for bundle in bundles.values():
        assert validate_qc_zarr_store(bundle)["valid_gate_count"] > 0
    assert report["operational_eligible"] is False
    evidence = case["sweeps"][0]["paper_sources"]
    assert evidence["afl"]["author_complete_reproduction"] is False


def test_rdd_unavailable_has_no_fake_zero_count_or_skill(evaluated):
    row = evaluated[4]["cases"][0]["sweeps"][0]
    rdd = row["methods"]["rdd_reference"]
    assert rdd["status"].startswith("not_executed")
    assert rdd["marked_observed_gates"] is None
    assert rdd["measurement_metrics"] is None
    assert "rdd_reference" not in row["radials"][0]["variants"]


def test_candidate_preview_never_invents_final_qc_actions(evaluated):
    row = evaluated[4]["cases"][0]["sweeps"][0]
    for name in ("afl_parameterized", "afl_local"):
        variant = row["radials"][0]["variants"][name]
        assert "QC_ACTION" not in variant
        assert "DBZH_USABLE" not in variant
        assert "CANDIDATE_PREVIEW_DBZH" in variant
        assert row["methods"][name]["measurement_metrics"] is None


def test_repeated_identical_task_has_identical_bundles_and_raw_unchanged(evaluated):
    root, source, _, manifest, _, bundles = evaluated
    with warnings.catch_warnings():
        warnings.simplefilter("ignore")
        _, again = compare_task(manifest, root, inspect_rays=(110,))
    for method, bundle in bundles.items():
        differing = [k for k in bundle if bundle[k] != again[method].get(k)]
        if differing:

            def first_difference(a, b, path=""):
                if isinstance(a, dict) and isinstance(b, dict):
                    for key in sorted(a.keys() | b.keys()):
                        found = first_difference(a.get(key), b.get(key), path + "/" + key)
                        if found:
                            return found
                    return None
                if isinstance(a, list) and isinstance(b, list):
                    if len(a) != len(b):
                        return path, repr(a)[:1200], repr(b)[:1200]
                    for i, (x, y) in enumerate(zip(a, b, strict=True)):
                        found = first_difference(x, y, path + "/" + str(i))
                        if found:
                            return found
                    return None
                return (path, a, b) if a != b else None

            detail = first_difference(
                json.loads(bundle["qc/summary.json"]),
                json.loads(again[method]["qc/summary.json"]),
            )
            pytest.fail(f"nonrepeatable {method} bundle keys={differing}, detail={detail}")
    stored = open_qc_input(bundles["paper_fusion_v4"]).root["sweep_000"]
    raw = open_qc_input(source).root["sweep_000"]
    np.testing.assert_array_equal(stored["DBZH_RAW"][:], raw["DBZH"][:])


def test_comparison_rejects_changed_profile_hash_before_execution(evaluated):
    manifest = json.loads(json.dumps(evaluated[3]))
    manifest["fusion_profile"]["sha256"] = "0" * 64
    with pytest.raises(ValueError, match="checksum"):
        compare_task(manifest, evaluated[0])


def test_nonpaper_parameter_retuning_cannot_be_hidden(evaluated, tmp_path):
    data = yaml.safe_load(V4.read_text())
    data["quality_index"]["suspect_quality"] = 0.7
    p = tmp_path / "other.yaml"
    p.write_text(yaml.safe_dump(data))
    manifest = {**evaluated[3], "fusion_profile": frozen(p)}
    with pytest.raises(ValueError, match="non-paper"):
        compare_task(manifest, evaluated[0])


def test_wrong_task_scan_identity_is_rejected(evaluated, tmp_path):
    task = json.loads((evaluated[0] / "task.json").read_text())
    task["payload"]["radar_id"] = "another-radar"
    f = tmp_path / "wrong.json"
    f.write_text(json.dumps(task))
    manifest = {**evaluated[3], "task": frozen(f)}
    with pytest.raises(ValueError, match="radar_id"):
        compare_task(manifest, evaluated[0])


def test_fixed_label_denominator_does_not_follow_algorithm_availability(evaluated, tmp_path):
    shape = evaluated[2].shape
    labels = np.full(shape, 2, "uint8")
    p = tmp_path / "labels.npy"
    np.save(p, labels)
    manifest = {**evaluated[3], "labels": {"sweep_000": frozen(p)}}
    report, _ = compare_task(manifest, evaluated[0], inspect_rays=(110,))
    methods = report["cases"][0]["sweeps"][0]["methods"]
    assert methods["afl_parameterized"]["unavailable_observed_gates"] > 0
    for key, value in methods.items():
        if key != "rdd_reference":
            assert value["measurement_metrics"]["interference_count"] == np.prod(shape)


def test_output_is_atomic_and_existing_result_not_overwritten(evaluated, tmp_path):
    manifest = {
        **evaluated[3],
        "artifacts": [
            {**x, "path": str(evaluated[0] / x["path"])} for x in evaluated[3]["artifacts"]
        ],
    }
    path = tmp_path / "manifest.json"
    path.write_text(json.dumps(manifest))
    run_comparison(path, tmp_path / "result", inspect_rays=(110,), save_bundles=True)
    assert (tmp_path / "result/report.json").is_file()
    assert (tmp_path / "result/paper_fusion_v4/qc.zarr/.zattrs").is_file()
    with pytest.raises(ValueError, match="exists"):
        run_comparison(path, tmp_path / "result")
    assert not (tmp_path / "result.lock").exists()


def test_integrity_validation_blocks_false_addition_masks(evaluated):
    view = open_qc_input(evaluated[5]["paper_fusion_v4"]).root["sweep_000"]
    fields = {k: view[k][:].copy() for k in view}
    fields["PAPER_CONFIRMED_ADDITION_MASK"][0, 0] = 1
    observed = fields["VALID_MASK"] == 1
    rejected = fields["QC_ACTION"] == 2
    with pytest.raises(ValueError, match="domain|decisions"):
        validate_paper_fields(fields, observed, rejected, fields["RFI_QUARANTINE_MASK"] == 1)


def test_v4_core_serializes_unlabeled_missing_domain():
    objects, _ = scene(missing_background=True)
    result = apply_basic_qc(objects, config())
    store = build_qc_zarr_store(
        objects, result, asset_id="test", normalized_volume_uri="local-test"
    )
    assert validate_qc_zarr_store(store)["missing_gate_count"] > 0


def test_new_json_schemas_accept_the_frozen_case(evaluated):
    import jsonschema

    from .test_qc_paper_algorithms import ROOT

    for name in ("qc-paper-comparison", "qc-paper-batch", "qc-rdd-reference"):
        definition = json.loads((ROOT / "configs/schemas" / (name + ".schema.json")).read_text())
        jsonschema.Draft202012Validator.check_schema(definition)
        if name == "qc-paper-comparison":
            jsonschema.validate(evaluated[3], definition)


def test_batch_rejects_duplicate_physical_scan_before_computing(evaluated, tmp_path):
    from rainpulse_algo.radar.qc_engine.paper_batch import preflight

    f = tmp_path / "case.json"
    f.write_text(json.dumps(evaluated[3]))
    b = tmp_path / "batch.json"
    b.write_text(
        json.dumps(
            {"schema_version": "rainpulse.qc-paper-batch.v1", "cases": [frozen(f), frozen(f)]}
        )
    )
    with pytest.raises(ValueError, match="duplicate physical"):
        preflight(b)


def test_batch_rejects_cross_partition_context_leakage(evaluated, tmp_path):
    from rainpulse_algo.radar.qc_engine.paper_batch import preflight

    first = json.loads(json.dumps(evaluated[3]))
    second = json.loads(json.dumps(evaluated[3]))
    second["partition"] = "validation"
    second["process_id"] = "other-process"
    task = json.loads((evaluated[0] / "task.json").read_text())
    task["payload"]["scan_id"] = "00000000-0000-4000-8000-000000000001"
    t = tmp_path / "task2.json"
    t.write_text(json.dumps(task))
    second["task"] = frozen(t)
    digest = second["artifacts"][0]["sha256"]
    second["artifacts"][0]["sha256"] = "b" * 64
    second["artifacts"].append({"uri": "local:previous", "path": "unused.zarr", "sha256": digest})
    f = tmp_path / "first.json"
    f.write_text(json.dumps(first))
    g = tmp_path / "second.json"
    g.write_text(json.dumps(second))
    b = tmp_path / "batch.json"
    b.write_text(
        json.dumps(
            {"schema_version": "rainpulse.qc-paper-batch.v1", "cases": [frozen(f), frozen(g)]}
        )
    )
    with pytest.raises(ValueError, match="leakage"):
        preflight(b)


def test_manifest_helper_does_not_rewrite_v3_task(evaluated, tmp_path):
    from rainpulse_algo.radar.qc_engine.paper_manifest import freeze_comparison

    m = evaluated[3]
    replay = {
        "schema_version": "rainpulse.qc-task-replay.v1",
        "task": m["task"],
        "profile": m["v3_profile"],
        "flags": m["flags"],
        "artifacts": [{**x, "path": str(evaluated[0] / x["path"])} for x in m["artifacts"]],
    }
    f = tmp_path / "replay.json"
    f.write_text(json.dumps(replay))
    output = tmp_path / "compare.json"
    result = freeze_comparison(f, output, V4, process_id="test-process")
    assert result["task"] == m["task"]
    assert result["rdd_references"] == {}
    with pytest.raises(FileExistsError):
        freeze_comparison(f, output, V4, process_id="test-process")


def test_same_palette_images_keep_missing_rdd_explicit(evaluated, tmp_path):
    from rainpulse_algo.diagnostics.png import png_dimensions
    from rainpulse_algo.radar.qc_engine.paper_images import write_comparison_images

    write_comparison_images(evaluated[5], evaluated[4], tmp_path / "images")
    assert "NOT EXECUTED" in (tmp_path / "images/index.html").read_text()
    assert not (tmp_path / "images/sweep_000-rdd.png").exists()
    assert png_dimensions((tmp_path / "images/sweep_000-raw.png").read_bytes()) == (640, 640)


def test_ppi_does_not_fill_unobserved_sector_angles():
    from rainpulse_algo.radar.qc_engine.paper_images import _ppi

    array = np.full((2, 100), 30.0)
    image = _ppi(
        array,
        np.ones(array.shape, bool),
        np.array([0.0, 1.0]),
        np.arange(1, 101) * 1000.0,
        size=101,
    )
    assert image[50, 99, 3] == 0  # east is outside this north-looking sector
    assert image[1, 50, 3] > 0
