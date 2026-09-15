"""New experiment tests. No test results constitute real-weather skill evidence."""

import copy
import json
import subprocess
import sys
from dataclasses import replace
from pathlib import Path

import numpy as np
import pytest
import zarr

from rainpulse_algo.radar.qc_engine.measurement_v8 import CLASS_NAMES
from rainpulse_algo.radar.qc_engine.measurement_v8.assessment import assess
from rainpulse_algo.radar.qc_engine.measurement_v8.case import FrozenCase
from rainpulse_algo.radar.qc_engine.measurement_v8.dataset import load_dataset, read_pack
from rainpulse_algo.radar.qc_engine.measurement_v8.features import extract, window_moments
from rainpulse_algo.radar.qc_engine.measurement_v8.forest import (
    load_model,
    predict,
    train,
    validate_model,
)
from rainpulse_algo.radar.qc_engine.measurement_v8.io import (
    atomic_directory,
    checked,
    file_hash,
    load_npz,
    tree_hash,
    write_json,
)
from rainpulse_algo.radar.qc_engine.measurement_v8.native import run_native
from rainpulse_algo.radar.qc_engine.measurement_v8.policy import (
    check_receipt,
    decide,
    policy_identity,
)
from rainpulse_algo.radar.qc_engine.measurement_v8.schema import Config, Ref
from rainpulse_algo.radar.qc_engine.measurement_v8.states import decode
from rainpulse_algo.radar.qc_engine.measurement_v8.synthetic import make_case
from rainpulse_algo.radar.qc_engine.measurement_v8.workbench import extract_case, infer_case

ROOT = Path(__file__).resolve().parents[2]


@pytest.fixture(scope="module")
def native_binary(tmp_path_factory):
    out = tmp_path_factory.mktemp("native") / "core"
    subprocess.run(
        [sys.executable, str(ROOT / "tools/radar_native/build.py"), "--output", str(out)],
        check=True,
        capture_output=True,
    )
    return out, file_hash(out)


@pytest.fixture
def case(tmp_path):
    return FrozenCase(make_case(tmp_path / "source", ROOT, index=41))


@pytest.fixture(scope="module")
def trained(tmp_path_factory):
    root = tmp_path_factory.mktemp("training")
    cfg = Config()
    cfg = cfg.model_copy(
        update={
            "native": cfg.native.model_copy(update={"enabled": False}),
            "training": cfg.training.model_copy(update={"n_estimators": 8, "max_depth": 5}),
        }
    )
    packs, cases = [], []
    for index, split in enumerate(
        ("train", "train", "calibrate", "calibrate", "validate", "validate")
    ):
        c = make_case(root / f"source-{index}", ROOT, partition=split, index=index)
        extract_case(c, cfg, root / f"features-{index}")
        f = root / f"features-{index}/sweep_000-features.npz"
        labels = c.parent / "labels.csv"
        packs.append(
            {
                "features": {"path": f.relative_to(root).as_posix(), "sha256": file_hash(f)},
                "labels": {
                    "path": labels.relative_to(root).as_posix(),
                    "sha256": file_hash(labels),
                },
            }
        )
        cases.append(c)
    manifest = root / "dataset.json"
    write_json(manifest, {"schema_version": "rainpulse.measurement-dataset.v1", "packs": packs})
    report = train(manifest, cfg.training, root / "trained")
    modelpath = root / "trained/model.json"
    return root, cfg, manifest, cases, modelpath, report


def test_native_build_receipt_binds_sources(native_binary):
    binary, sha = native_binary
    r = json.loads(binary.with_name(binary.name + ".json").read_text())
    assert r["binary_sha256"] == sha
    assert r["source_revisions"]["bropo_revision"].startswith("c330b05")
    assert r["detectors"] == ["detect_emitters", "detect_emitters2"]


def test_native_calls_real_cores_and_is_repeatable(case, native_binary):
    n, _, _ = case.sweep("sweep_000")
    binary, sha = native_binary
    before = n.fields["DBZH"].copy()
    a = run_native(n, Config().native, binary, sha)
    b = run_native(n, Config().native, binary, sha)
    assert a.summary["calls"] == 2 and a.summary["status"] == "executed"
    for k in ("1", "2"):
        assert np.isfinite(a.scores[k]).any()
        assert (a.scores[k][np.isfinite(a.scores[k])] >= 0).all()
        assert (a.scores[k][np.isfinite(a.scores[k])] <= 1).all()
        assert np.array_equal(a.scores[k], b.scores[k], equal_nan=True)
    assert np.array_equal(n.fields["DBZH"], before)


def test_native_missing_stays_unknown(case, native_binary):
    n, _, _ = case.sweep("sweep_000")
    z = n.fields["DBZH"].copy()
    z[:, 120:123] = np.nan
    n = replace(
        n,
        fields={**n.fields, "DBZH": z},
        field_available={**n.field_available, "DBZH": np.isfinite(z)},
    )
    a = run_native(n, Config().native, *native_binary)
    assert a.summary["calls"] > 0
    assert all(np.isnan(x[:, 120:123]).all() for x in a.scores.values())
    assert all(t["mode"] == "observed_tile" for t in a.summary["tiles"])


@pytest.mark.parametrize("missing_binary,required", [(True, False), (True, True)])
def test_native_unavailable_not_zero(case, missing_binary, required):
    n, _, _ = case.sweep("sweep_000")
    cfg = Config().native.model_copy(update={"required": required})
    if required:
        with pytest.raises(ValueError, match="required"):
            run_native(n, cfg)
    else:
        out = run_native(n, cfg)
        assert out.summary["status"] == "unavailable_binary"
        assert np.isnan(out.scores["1"]).all()


def test_native_bad_binary_hash(case, native_binary):
    with pytest.raises(ValueError, match="checksum"):
        run_native(case.sweep("sweep_000")[0], Config().native, native_binary[0], "0" * 64)


def test_native_failure_no_false_negative(case, tmp_path):
    p = tmp_path / "bad"
    p.write_text("#!/bin/sh\nexit 9\n")
    p.chmod(0o755)
    a = run_native(case.sweep("sweep_000")[0], Config().native, p, file_hash(p))
    assert a.summary["failures"] and np.isnan(a.scores["2"]).all()


def test_native_budget_explicit(case, native_binary):
    n, _, _ = case.sweep("sweep_000")
    z = n.fields["DBZH"].copy()
    z[0, 120] = np.nan
    n = replace(
        n,
        fields={**n.fields, "DBZH": z},
        field_available={**n.field_available, "DBZH": np.isfinite(z)},
    )
    out = run_native(n, Config().native.model_copy(update={"maximum_calls": 2}), *native_binary)
    assert out.summary["calls"] <= 2 and out.summary["budget_exhausted"]


def test_feature_missing_is_nan_no_input_mutation(case):
    n, _, _ = case.sweep("sweep_000")
    z = n.fields["DBZH"].copy()
    z[10, 100] = np.nan
    n = replace(
        n,
        fields={**n.fields, "DBZH": z},
        field_available={**n.field_available, "DBZH": np.isfinite(z)},
    )
    before = z.copy()
    f = extract(n, Config().features)
    assert np.isnan(f.matrix[10 * n.shape[1] + 100]).all()
    assert np.array_equal(before, z, equal_nan=True)
    assert "radar_id" not in f.names and "azimuth" not in f.names
    assert np.isnan(f.matrix[:, f.names.index("weather_support")]).all()


def test_phase_wrap_is_circular(case):
    n, _, _ = case.sweep("sweep_000")
    phi = n.fields["PHIDP"].copy()
    phi[:, 100], phi[:, 101] = 359, 1
    n = replace(n, fields={**n.fields, "PHIDP": phi})
    f = extract(n, Config().features)
    i = f.names.index("phase_pair_deg_per_km")
    assert f.matrix[101, i] == pytest.approx(8)  # 2 degrees / 0.25 km


def test_rolling_missing_not_clearair():
    a = np.array([[10, 10, np.nan, 10, 10]], float)
    mean, _, _ = window_moments(a, np.isfinite(a), 5, 0.8)
    assert np.isnan(mean[0, 2])
    b = np.array([[10, 10, 10, np.nan, 10]], float)
    mean, _, _ = window_moments(b, np.isfinite(b), 5, 0.8)
    assert mean[0, 2] == 10


def test_context_zero_samples_has_no_persistence(case):
    n, _, _ = case.sweep("sweep_000")
    f = extract(
        n,
        Config().features,
        context={"temporal_samples": np.zeros(n.shape), "temporal_persistence": np.ones(n.shape)},
    )
    assert np.isnan(f.matrix[:, f.names.index("temporal_persistence")]).all()


@pytest.mark.parametrize("value", [-0.1, 1.1, np.inf])
def test_context_invalid_values_rejected(case, value):
    n, _, _ = case.sweep("sweep_000")
    with pytest.raises(ValueError):
        extract(n, Config().features, context={"weather_support": np.full(n.shape, value)})


def test_states_missing_breaks_and_weather_barrier():
    p = np.full((1, 20, 3), 0.01)
    p[..., 1] = 0.98
    obs = np.ones((1, 20), bool)
    obs[0, 10] = False
    barrier = np.zeros_like(obs)
    barrier[0, 7] = True
    s = decode(p, obs, np.arange(20) * 250 + 125, Config().state, barrier)
    assert s[0, 10] == -1 and s[0, 7] == 0


def test_state_proposal_does_not_override_low_local_probability(case):
    n, b, _ = case.sweep("sweep_000")
    p = np.broadcast_to([0.1, 0.8, 0.1], (*n.shape, 3)).copy()
    s = np.ones(n.shape, "int8")
    out = decide(n, b, p, s, np.zeros(n.shape), Config())
    assert not out["V8_PROPOSED_CONFIRM_MASK"].any()
    assert not out["V8_PROPOSED_QUARANTINE_MASK"].any()


def test_audit_preserves_every_baseline_array(case):
    n, b, _ = case.sweep("sweep_000")
    p = np.broadcast_to([0.001, 0.998, 0.001], (*n.shape, 3)).copy()
    out = decide(n, b, p, np.ones(n.shape, "int8"), np.zeros(n.shape), Config())
    for key in b:
        assert np.array_equal(out[key], b[key], equal_nan=True), key
    assert out["V8_PROPOSED_CONFIRM_MASK"].any()
    assert not out["V8_CONFIRMED_ADDITION_MASK"].any()


def test_experimental_requires_review(case):
    n, b, _ = case.sweep("sweep_000")
    cfg = Config()
    cfg = cfg.model_copy(update={"policy": cfg.policy.model_copy(update={"mode": "experimental"})})
    p = np.broadcast_to([0.001, 0.998, 0.001], (*n.shape, 3)).copy()
    with pytest.raises(ValueError, match="reviewed"):
        decide(n, b, p, np.ones(n.shape, "int8"), np.zeros(n.shape), cfg)


def test_policy_flags_noecho_and_old_exclusion(case):
    n, b, _ = case.sweep("sweep_000")
    cfg = Config()
    cfg = cfg.model_copy(update={"policy": cfg.policy.model_copy(update={"mode": "experimental"})})
    b["QC_ACTION"][1, 1] = 2
    b["QPE_ELIGIBLE_MASK"][1, 1] = 0
    p = np.broadcast_to([0.001, 0.998, 0.001], (*n.shape, 3)).copy()
    out = decide(n, b, p, np.ones(n.shape, "int8"), np.zeros(n.shape), cfg, approved=True)
    added = out["V8_CONFIRMED_ADDITION_MASK"] == 1
    assert added.any()
    assert np.all((out["QC_FLAGS"][added] & 16384) != 0)
    assert np.all((out["QC_FLAGS"][added] & 256) == 0)
    assert not out["V8_PROPOSED_CONFIRM_MASK"][n.fields["DBZH"] < -10].any()
    assert out["QC_ACTION"][1, 1] == 2
    assert not np.any(out["QPE_ELIGIBLE_MASK"] & ~b["QPE_ELIGIBLE_MASK"])


def test_weather_presence_conflict_is_not_no_rain(case):
    n, b, _ = case.sweep("sweep_000")
    p = np.broadcast_to([0.001, 0.998, 0.001], (*n.shape, 3)).copy()
    out = decide(
        n,
        b,
        p,
        np.ones(n.shape, "int8"),
        np.zeros(n.shape),
        Config(),
        weather_support=np.ones(n.shape),
    )
    assert not out["V8_PROPOSED_CONFIRM_MASK"].any()
    assert out["V8_PROPOSED_QUARANTINE_MASK"].any()
    assert np.array_equal(out["DBZH_RAW"], b["DBZH_RAW"])


def test_real_and_synthetic_receipts_not_interchangeable(trained):
    _, cfg, _, _, modelpath, _ = trained
    model = load_model(modelpath, file_hash(modelpath))
    with pytest.raises(ValueError):
        check_receipt({}, model, file_hash(modelpath), cfg)


def test_policy_mode_is_not_a_threshold_change():
    a = Config()
    b = a.model_copy(update={"policy": a.policy.model_copy(update={"mode": "experimental"})})
    assert policy_identity(a) == policy_identity(b)


def test_model_trains_calibrates_and_json_parity(trained):
    _, cfg, manifest, _, modelpath, report = trained
    data, _ = load_dataset(manifest, cfg.training)
    model = load_model(modelpath, file_hash(modelpath))
    assert report["export_parity_verified"]
    assert model["data_kind"] == "synthetic" and model["operational_eligible"] is False
    x = data["validate"]["X"]
    p = predict(model, x)
    assert p.shape == (len(x), 3) and np.allclose(p.sum(axis=1), 1)
    assert np.array_equal(p, predict(model, x))


@pytest.mark.parametrize("mutation", ["cycle", "negative", "calibration", "class_order", "nan"])
def test_malformed_model_rejected(trained, mutation):
    model = json.loads(trained[4].read_text())
    if mutation == "cycle":
        model["trees"][0]["left"][0] = 0
    elif mutation == "negative":
        model["trees"][0]["value"][0][0] = -1
    elif mutation == "calibration":
        model["calibration"]["coef"] = [[1]]
    elif mutation == "class_order":
        model["classes"] = list(reversed(CLASS_NAMES))
    else:
        model["medians"][0] = float("nan")
    with pytest.raises(ValueError):
        validate_model(model)


def test_model_wrong_hash_rejected(trained):
    with pytest.raises(ValueError, match="SHA256"):
        load_model(trained[4], "0" * 64)


def _changed_dataset(trained, tmp_path, change):
    # Copy only bound feature/label inputs, mutate one pack and update its digest.
    root, _, manifest, _, _, _ = trained
    import shutil

    spec = json.loads(manifest.read_text())
    for entry in spec["packs"]:
        for key in ("features", "labels"):
            src = root / entry[key]["path"]
            dst = tmp_path / entry[key]["path"]
            dst.parent.mkdir(parents=True, exist_ok=True)
            shutil.copyfile(src, dst)
    change(spec, tmp_path)
    write_json(tmp_path / "dataset.json", spec)
    return tmp_path / "dataset.json"


@pytest.mark.parametrize("kind", ["process", "scan", "duplicate", "labels", "data_kind"])
def test_dataset_leakage_and_invalid_labels(trained, tmp_path, kind):
    def change(spec, root):
        if kind == "duplicate":
            spec["packs"].append(copy.deepcopy(spec["packs"][0]))
            return
        if kind == "labels":
            item = spec["packs"][0]["labels"]
            f = root / item["path"]
            f.write_text(f.read_text() + "-1,4,weather\n")
            item["sha256"] = file_hash(f)
            return
        item = spec["packs"][4]["features"]
        f = root / item["path"]
        a = load_npz(f)
        meta = json.loads(str(a["metadata"]))
        meta["case"][
            {"process": "process_id", "scan": "scan_id", "data_kind": "data_kind"}[kind]
        ] = {"process": "process-0", "scan": "synthetic-scan-0", "data_kind": "real"}[kind]
        a["metadata"] = np.array(json.dumps(meta))
        np.savez_compressed(f, **a)
        item["sha256"] = file_hash(f)

    manifest = _changed_dataset(trained, tmp_path, change)
    with pytest.raises(ValueError):
        load_dataset(manifest, trained[1].training)


def test_pack_duplicate_coordinates_rejected(trained, tmp_path):
    f = trained[0] / "features-0/sweep_000-features.npz"
    a = load_npz(f)
    a["ray"][1], a["gate"][1] = a["ray"][0], a["gate"][0]
    np.savez_compressed(tmp_path / "bad.npz", **a)
    with pytest.raises(ValueError, match="duplicate"):
        read_pack(tmp_path / "bad.npz")


def test_input_raw_and_asset_hash_binding(tmp_path):
    path = make_case(tmp_path / "source", ROOT)
    a = json.loads(path.read_text())
    q = zarr.open_group(str(path.parent / "qc.zarr"), mode="a")
    q["DBZH_RAW"] if "DBZH_RAW" in q else None
    q["sweep_000/DBZH_RAW"][20, 100] = 77
    with pytest.raises(ValueError, match="checksum"):
        FrozenCase(path)
    a["qc"]["sha256"] = tree_hash(path.parent / "qc.zarr")
    write_json(path, a)
    with pytest.raises(ValueError, match="measurement mismatch"):
        FrozenCase(path).sweep("sweep_000")


def test_input_path_escape_and_symlink(tmp_path):
    with pytest.raises(ValueError):
        checked(tmp_path, Ref(path="../outside", sha256="0" * 64))
    original = tmp_path / "real"
    original.write_text("x")
    (tmp_path / "link").symlink_to(original)
    with pytest.raises(ValueError, match="symlink"):
        checked(tmp_path, Ref(path="link", sha256=file_hash(original)))


def test_atomic_output_and_failure_cleanup(tmp_path):
    out = tmp_path / "new"
    with pytest.raises(RuntimeError):
        with atomic_directory(out) as t:
            (t / "partial").write_text("x")
            raise RuntimeError("failure")
    assert not out.exists() and not out.with_name("new.lock").exists()
    with atomic_directory(out) as t:
        (t / "ok").write_text("done")
    with pytest.raises(ValueError, match="exists"):
        with atomic_directory(out):
            pass
    assert (out / "ok").read_text() == "done"


def test_infer_is_separate_artifact_preserves_baseline(trained, tmp_path):
    _, cfg, _, cases, model, _ = trained
    sourcehash = tree_hash(cases[4].parent / "qc.zarr")
    r = infer_case(cases[4], cfg, model, file_hash(model), tmp_path / "inference")
    assert r["published"] is False and r["production_contract"] is False
    assert r["in_training_or_calibration"] is False
    a = load_npz(tmp_path / "inference/sweep_000-experiment.npz")
    assert np.array_equal(a["EXPERIMENT_QPE_ELIGIBLE_MASK"], a["BASELINE_QPE_ELIGIBLE_MASK"])
    assert np.array_equal(a["EXPERIMENT_QC_ACTION"], a["BASELINE_QC_ACTION"])
    assert a["V8_PROPOSED_CONFIRM_MASK"].any()
    assert not a["V8_CONFIRMED_ADDITION_MASK"].any()
    images = r["sweeps"][0]["images"]
    assert images["baseline"]["sha256"] == images["experimental_actual"]["sha256"]
    assert tree_hash(cases[4].parent / "qc.zarr") == sourcehash
    with pytest.raises(ValueError, match="exists"):
        infer_case(cases[4], cfg, model, file_hash(model), tmp_path / "inference")


def test_infer_synthetic_model_cannot_apply_to_real(trained, tmp_path):
    path = trained[3][4]
    spec = json.loads(path.read_text())
    # manifest lives with its bound inputs; restore it after the negative check.
    original = path.read_bytes()
    spec["data_kind"] = "real"
    write_json(path, spec)
    try:
        with pytest.raises(ValueError, match="synthetic/real"):
            infer_case(path, trained[1], trained[4], file_hash(trained[4]), tmp_path / "out")
    finally:
        path.write_bytes(original)


def test_extraction_cannot_write_inside_input(case):
    c = Path(case.normalized.store.path).parent / "case.json"
    with pytest.raises(ValueError, match="immutable"):
        extract_case(c, Config(), Path(case.qc.store.path) / "output")


def test_changed_feature_recipe_rejected(trained, tmp_path):
    cfg = trained[1]
    cfg = cfg.model_copy(
        update={"features": cfg.features.model_copy(update={"radial_windows_m": (1000, 5000)})}
    )
    with pytest.raises(ValueError, match="recipe"):
        infer_case(trained[3][4], cfg, trained[4], file_hash(trained[4]), tmp_path / "out")
    assert not (tmp_path / "out").exists()


def test_real_pyart_wradlib_paths_remain_callable(case):
    from rainpulse_algo.radar.qc_engine.algorithms import library_evidence

    n, _, _ = case.sweep("sweep_000")
    before = n.fields["DBZH"].copy()
    evidence = library_evidence(n, case.profile)
    assert evidence.arrays["METEO_SCORE"].shape == n.shape
    assert np.array_equal(before, n.fields["DBZH"])


def test_assessment_separates_isolation_and_missing_radars(trained, tmp_path):
    _, cfg, _, cases, model, _ = trained
    items = []
    for index in (4, 5):
        out = tmp_path / f"infer-{index}"
        infer_case(cases[index], cfg, model, file_hash(model), out, render=False)
        label = tmp_path / f"labels-{index}.csv"
        label.write_bytes((cases[index].parent / "labels.csv").read_bytes())
        report = out / "experiment.json"
        items.append(
            {
                "report": {
                    "path": report.relative_to(tmp_path).as_posix(),
                    "sha256": file_hash(report),
                },
                "labels": {"sweep_000": {"path": label.name, "sha256": file_hash(label)}},
            }
        )
    spec = {
        "schema_version": "rainpulse.measurement-assessment.v1",
        "labels_source": "synthetic_generator",
        "required_radars": ["synthetic-radar-0", "synthetic-radar-1", "not-present"],
        "cases": items,
    }
    manifest = tmp_path / "assess.json"
    write_json(manifest, spec)
    report = assess(manifest, tmp_path / "assessment")
    assert report["outcome"] == "INSUFFICIENT"
    assert report["missing_required_radars"] == ["not-present"]
    assert report["independent_scan_count"] == 2
    assert report["data_kind"] == "synthetic" and not report["operational_eligible"]
    assert report["overall"]["proposed_confirm"] > 0
    assert "proposed_isolation" in report["overall"]
    spec["cases"].append(spec["cases"][0])
    write_json(manifest, spec)
    with pytest.raises(ValueError, match="duplicate"):
        assess(manifest, tmp_path / "bad")


def test_classifier_does_not_recalculate_kept_values(case):
    n, b, _ = case.sweep("sweep_000")
    b["DBZH_USABLE"] = b["DBZH_USABLE"].copy() + 0.25
    probabilities = np.broadcast_to([0.8, 0.1, 0.1], (*n.shape, 3)).copy()
    out = decide(n, b, probabilities, np.zeros(n.shape, "int8"), np.zeros(n.shape), Config())
    assert np.array_equal(out["DBZH_USABLE"], b["DBZH_USABLE"], equal_nan=True)
