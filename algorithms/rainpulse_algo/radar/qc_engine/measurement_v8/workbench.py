"""Complete local extract -> label -> train -> infer path against a frozen V7 asset.

Experiments are sidecars, NOT publishable QCRadarVolume objects. Existing V7
artifacts/configs/queues remain untouched, including the bounded NATS event fix.
"""

import json
import time
from pathlib import Path

import numpy as np

from . import VERSION
from .case import FrozenCase
from .features import extract
from .forest import load_model, predict
from .io import atomic_directory, checked, digest, file_hash, write_json
from .native import run_native
from .policy import check_receipt, decide, policy_identity
from .schema import Ref
from .states import decode


def _features(case, cut, cfg, binary, binary_sha):
    start = time.perf_counter()
    native, baseline, context = case.sweep(cut)
    read_ms = (time.perf_counter() - start) * 1000
    start = time.perf_counter()
    reference = run_native(native, cfg.native, binary=binary, expected_sha256=binary_sha)
    native_ms = (time.perf_counter() - start) * 1000
    start = time.perf_counter()
    features = extract(native, cfg.features, reference, context)
    features.metadata["baseline_parameters_sha256"] = case.profile.parameters_hash
    features.metadata["feature_identity"] = digest(
        {
            "feature_recipe": features.metadata["feature_identity"],
            "baseline_parameters_sha256": case.profile.parameters_hash,
        }
    )
    feature_ms = (time.perf_counter() - start) * 1000
    return (
        native,
        baseline,
        context,
        reference,
        features,
        {
            "read_cut_ms": read_ms,
            "native_ms": native_ms,
            "features_ms": feature_ms,
            "context_prepare_ms": None,
            "upload_ms": None,
            "timing_scope": "local_frozen_asset_not_105_worker_performance",
        },
    )


def _pack(tmp, cut, native, features, case):
    shape = native.shape
    rays = np.broadcast_to(native.original_indices[:, None], shape).ravel()
    gates = np.broadcast_to(np.arange(shape[1])[None, :], shape).ravel()
    meta = {**features.metadata, "case": case.metadata(cut), "sweep": cut, "shape": shape}
    path = tmp / (cut + "-features.npz")
    np.savez_compressed(
        path,
        X=features.matrix,
        ray=rays.astype("int32"),
        gate=gates.astype("int32"),
        observed=native.field_available["DBZH"].ravel(),
        metadata=np.array(json.dumps(meta, ensure_ascii=False)),
    )
    return path, meta


def _check_output(manifest, case, output):
    out = Path(output).absolute()
    base = Path(manifest).resolve().parent
    for ref in (case.spec.normalized, case.spec.qc):
        source = (base / ref.path).resolve()
        if out == source or out.is_relative_to(source):
            raise ValueError("output cannot be inside an immutable input artifact")


def extract_case(manifest, cfg, output, *, binary=None, binary_sha=None):
    case = FrozenCase(manifest)
    _check_output(manifest, case, output)
    reports = []
    with atomic_directory(output) as tmp:
        for cut in case.spec.sweeps:
            native, baseline, _, reference, features, timing = _features(
                case, cut, cfg, binary, binary_sha
            )
            t = time.perf_counter()
            path, _ = _pack(tmp, cut, native, features, case)
            # Every original measured gate is labelable; unknown is a real abstention label.
            np.savez_compressed(
                tmp / (cut + "-native.npz"),
                emitter1=native.restore(reference.scores["1"]),
                emitter2=native.restore(reference.scores["2"]),
                observed=native.restore(native.field_available["DBZH"]),
                azimuth=case.normalized[cut]["azimuth"][:],
                ranges=native.ranges,
            )
            labels = tmp / (cut + "-labels-TEMPLATE.csv")
            labels.write_text("ray,gate,label\n", encoding="utf-8")
            timing["serialization_ms"] = (time.perf_counter() - t) * 1000
            reports.append(
                {
                    "sweep": cut,
                    "feature_file": path.name,
                    "feature_sha256": file_hash(path),
                    "feature_identity": features.metadata["feature_identity"],
                    "n_observed": int(native.field_available["DBZH"].sum()),
                    "baseline_qpe_eligible": int((baseline["QPE_ELIGIBLE_MASK"] == 1).sum()),
                    "native": reference.summary,
                    "timing": timing,
                }
            )
        report = {
            "version": VERSION,
            "case": case.spec.model_dump(mode="json"),
            "config": cfg.model_dump(mode="json"),
            "sweeps": reports,
            "truth_metrics": None,
            "operational_eligible": False,
            "mutated_input": False,
            "published": False,
        }
        t = time.perf_counter()
        case.verify_unchanged()
        report["integrity_recheck_ms"] = (time.perf_counter() - t) * 1000
        write_json(tmp / "extraction.json", report)
    return report


def _review(path, model, model_sha, cfg):
    receipt_path = Path(path)
    receipt = json.loads(receipt_path.read_text())
    report_path = checked(receipt_path.parent, Ref.model_validate(receipt["validation_report"]))
    report = json.loads(report_path.read_text())
    check_receipt(receipt, model, model_sha, cfg)
    expected = {
        "schema_version": "rainpulse.measurement-policy-validation.v1",
        "model_sha256": model_sha,
        "policy_sha256": policy_identity(cfg),
        "data_kind": "real",
        "independent_from_training": True,
        "labels_source": "human_reviewed",
    }
    if any(report.get(k) != v for k, v in expected.items()):
        raise ValueError("receipt does not bind independent human-labeled policy validation")
    if receipt["validation_report_sha256"] != file_hash(report_path):
        raise ValueError("validation report hash mismatch")
    if receipt["validation_dataset_sha256"] != report.get("dataset_sha256"):
        raise ValueError("validation dataset identity mismatch")
    if report.get("missing_required_radars") or report.get("outcome") == "INSUFFICIENT":
        raise ValueError("required-radar validation is incomplete")
    if report.get("independent_process_count", 0) < 2 or not report.get("by_radar"):
        raise ValueError("insufficient independently reviewed validation processes")
    return receipt


def _domain(model, features):
    valid = np.ones(len(features.matrix), bool)
    for name, bounds in model.get("geometry_domain", {}).items():
        a = features.matrix[:, features.names.index(name)]
        valid &= np.isfinite(a) & (a >= bounds[0] - 1e-5) & (a <= bounds[1] + 1e-5)
    return valid


def infer_case(
    manifest,
    cfg,
    model_path,
    model_sha,
    output,
    *,
    binary=None,
    binary_sha=None,
    review=None,
    render=True,
):
    case = FrozenCase(manifest)
    _check_output(manifest, case, output)
    model = load_model(model_path, model_sha)
    if model["data_kind"] != case.spec.data_kind:
        raise ValueError("synthetic/real model-input mismatch")
    receipt = None
    if cfg.policy.mode == "experimental":
        if review is None:
            raise ValueError("human review receipt is required for experimental application")
        receipt = _review(review, model, model_sha, cfg)
    prior = [a for split in ("train", "calibrate") for a in model["lineage"]["partitions"][split]]
    in_sample = any(
        a["process_id"] == case.spec.process_id or a["scan_id"] == case.spec.expected_scan_id
        for a in prior
    )
    if receipt and in_sample:
        raise ValueError("experimental case overlaps training/calibration")
    reports = []
    with atomic_directory(output) as tmp:
        for cut in case.spec.sweeps:
            native, baseline, context, reference, features, timing = _features(
                case, cut, cfg, binary, binary_sha
            )
            if (
                list(features.names) != model["names"]
                or features.metadata["feature_identity"] != model["feature_identity"]
            ):
                raise ValueError("feature/native recipe differs from trained model")
            observed = native.field_available["DBZH"] & native.geometry_good[:, None]
            domain = _domain(model, features).reshape(native.shape) & observed
            t = time.perf_counter()
            probabilities = np.full((*native.shape, 3), np.nan)
            probabilities[domain] = predict(model, features.matrix[domain.ravel()])
            timing["classifier_ms"] = (time.perf_counter() - t) * 1000
            t = time.perf_counter()
            # Weather PRESENCE is not a hard measurement-truth barrier. It is a
            # conflict feature/policy constraint, not a blanket veto on a rain area.
            states = decode(probabilities, observed, native.ranges, cfg.state)
            missing_fraction = np.isnan(features.matrix).mean(axis=1).reshape(native.shape)
            arrays = decide(
                native,
                baseline,
                probabilities,
                states,
                missing_fraction,
                cfg,
                weather_support=context.get("weather_support"),
                approved=receipt is not None,
            )
            timing["state_and_policy_ms"] = (time.perf_counter() - t) * 1000
            arrays["V8_FEATURE_DOMAIN_MASK"] = domain.astype("uint8")
            # Explicit namespace prevents stale inherited V7 stage validators being
            # misrepresented as validation of modified experimental decisions.
            wanted = (
                "DBZH_RAW",
                "DBZH_USABLE",
                "QC_ACTION",
                "QC_FLAGS",
                "QUALITY_INDEX",
                "RFI_QUARANTINE_MASK",
                "QPE_ELIGIBLE_MASK",
                "REFLECTIVITY_TRUST_MASK",
                "VALID_MASK",
                "LOW_QUALITY_MASK",
                "RFI_RISK_STATE",
                "QI_METEO",
                "QI_INTERFERENCE",
            )
            payload = {"EXPERIMENT_" + k: native.restore(arrays[k]) for k in wanted if k in arrays}
            for key, value in arrays.items():
                if key.startswith("V8_"):
                    payload[key] = native.restore(value)
            for key in ("QC_ACTION", "QC_FLAGS", "QPE_ELIGIBLE_MASK", "RFI_QUARANTINE_MASK"):
                payload["BASELINE_" + key] = native.restore(baseline[key])
            payload.update(
                azimuth=case.normalized[cut]["azimuth"][:],
                elevation=case.normalized[cut]["elevation"][:],
                ranges=native.ranges,
                ray_time=case.normalized[cut]["ray_time"][:],
            )
            t = time.perf_counter()
            np.savez_compressed(tmp / (cut + "-experiment.npz"), **payload)
            timing["serialization_ms"] = (time.perf_counter() - t) * 1000
            images = _render(tmp, cut, native, baseline, arrays) if render else {}
            reports.append(
                {
                    "sweep": cut,
                    "case": case.metadata(cut),
                    "native": reference.summary,
                    "artifact": cut + "-experiment.npz",
                    "artifact_sha256": file_hash(tmp / (cut + "-experiment.npz")),
                    "domain_gates": int(domain.sum()),
                    "proposed_confirm": int(arrays["V8_PROPOSED_CONFIRM_MASK"].sum()),
                    "proposed_quarantine": int(arrays["V8_PROPOSED_QUARANTINE_MASK"].sum()),
                    "actual_added_confirm": int(arrays["V8_CONFIRMED_ADDITION_MASK"].sum()),
                    "actual_added_quarantine": int(arrays["V8_QUARANTINED_ADDITION_MASK"].sum()),
                    "timing": timing,
                    "images": images,
                }
            )
        report = {
            "schema_version": "rainpulse.qc-measurement-experiment.v1",
            "version": VERSION,
            "case": case.spec.model_dump(mode="json"),
            "model_sha256": model_sha,
            "config": cfg.model_dump(mode="json"),
            "policy_sha256": policy_identity(cfg),
            "in_training_or_calibration": in_sample,
            "sweeps": reports,
            "review_receipt_sha256": None if review is None else file_hash(review),
            "truth_metrics": None,
            "operational_eligible": False,
            "published": False,
            "production_contract": False,
            "output_semantics": "local_experiment_not_registered_QC_asset",
        }
        t = time.perf_counter()
        case.verify_unchanged()
        if file_hash(model_path) != model_sha:
            raise ValueError("model changed during experiment")
        report["integrity_recheck_ms"] = (time.perf_counter() - t) * 1000
        write_json(tmp / "experiment.json", report)
    return report


def _render(out, cut, native, baseline, arrays):
    from ....diagnostics.png import encode_rgba_png
    from ....diagnostics.renderer import REFLECTIVITY_STOPS, _polar_to_ppi, _scalar_rgba

    result = {}
    for name, eligible in (
        ("baseline", baseline["QPE_ELIGIBLE_MASK"] == 1),
        ("experimental_actual", arrays["QPE_ELIGIBLE_MASK"] == 1),
        (
            "proposed_not_applied",
            (baseline["QPE_ELIGIBLE_MASK"] == 1)
            & (arrays["V8_PROPOSED_CONFIRM_MASK"] == 0)
            & (arrays["V8_PROPOSED_QUARANTINE_MASK"] == 0),
        ),
    ):
        rgba = _scalar_rgba(
            baseline.get("DBZH_QC", native.fields["DBZH"]), eligible, REFLECTIVITY_STOPS
        )
        png = encode_rgba_png(_polar_to_ppi(rgba, native.azimuth, native.ranges, 640))
        path = out / f"{cut}-{name}.png"
        path.write_bytes(png)
        result[name] = {
            "path": path.name,
            "sha256": file_hash(path),
            "size": [640, 640],
            "renderer": "existing_1.2.0_functions",
            "not_a_skill_metric": True,
        }
    return result
