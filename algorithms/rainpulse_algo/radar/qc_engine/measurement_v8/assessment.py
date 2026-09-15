"""Independent human-label assessment of actual PROPOSED actions, not pixel counts."""

import csv
import json
from pathlib import Path

import numpy as np

from .io import atomic_directory, checked, digest, file_hash, load_npz, write_json


def _counts(records):
    a = np.asarray(records, dtype=int)
    if not len(a):
        return {"n": 0, "precision": None, "recall": None}
    # y: weather=0, interference=1, mixed=2. Unknown excluded explicitly.
    y, confirm, isolate, old_eligible = a.T
    tp = int(((y == 1) & (confirm == 1)).sum())
    predicted = int(confirm.sum())
    actual = int((y == 1).sum())
    weather = int((y == 0).sum())
    lost_weather = int(
        ((y == 0) & old_eligible.astype(bool) & (confirm.astype(bool) | isolate.astype(bool))).sum()
    )
    return {
        "n": len(a),
        "confirmed_interference_precision": None if not predicted else tp / predicted,
        "confirmed_interference_recall": None if not actual else tp / actual,
        "proposed_confirm": predicted,
        "proposed_isolation": int(isolate.sum()),
        "weather_count": weather,
        "weather_proposed_reject": int(((y == 0) & (confirm == 1)).sum()),
        "weather_proposed_isolate": int(((y == 0) & (isolate == 1)).sum()),
        "weather_available_support_lost": lost_weather,
        "weather_loss_fraction": None if not weather else lost_weather / weather,
        "mixed_count": int((y == 2).sum()),
        "evaluation_unit": "original_labeled_gate_not_image_pixel",
    }


def assess(manifest_path, output):
    path = Path(manifest_path)
    spec = json.loads(path.read_text())
    if spec.get("schema_version") != "rainpulse.measurement-assessment.v1":
        raise ValueError("unsupported assessment manifest")
    if spec.get("labels_source") not in {"human_reviewed", "synthetic_generator"}:
        raise ValueError("label source must be explicit")
    all_rows, by_radar, by_case, processes, scans, seen = [], {}, {}, set(), set(), set()
    model_sha = policy_sha = kind = None
    inputs = []
    for item in spec["cases"]:
        report_path = checked(path.parent, item["report"])
        report = json.loads(report_path.read_text())
        if report.get("schema_version") != "rainpulse.qc-measurement-experiment.v1":
            raise ValueError("not a V8 inference report")
        if report.get("in_training_or_calibration") is not False:
            raise ValueError("assessment overlaps training/calibration")
        case = report["case"]
        current = (report["model_sha256"], report["policy_sha256"], case["data_kind"])
        if model_sha is None:
            model_sha, policy_sha, kind = current
        if current != (model_sha, policy_sha, kind):
            raise ValueError("assessment mixes model/policy/data-origin")
        if kind == "real" and spec["labels_source"] != "human_reviewed":
            raise ValueError("real verification requires independently reviewed labels")
        processes.add(case["process_id"])
        scans.add(case["expected_scan_id"])
        for cut in report["sweeps"]:
            if cut["sweep"] not in item["labels"]:
                continue
            identity = (case["expected_radar_id"], case["expected_scan_id"], cut["sweep"])
            if identity in seen:
                raise ValueError("duplicate physical cut in assessment")
            seen.add(identity)
            artifact = checked(
                report_path.parent, {"path": cut["artifact"], "sha256": cut["artifact_sha256"]}
            )
            arr = load_npz(artifact)
            label = checked(path.parent, item["labels"][cut["sweep"]])
            rows, seen_gates = [], set()
            shape = arr["EXPERIMENT_VALID_MASK"].shape
            with label.open(newline="") as f:
                reader = csv.DictReader(f)
                if reader.fieldnames != ["ray", "gate", "label"]:
                    raise ValueError("expected original ray,gate,label annotations")
                for row in reader:
                    i, j = int(row["ray"]), int(row["gate"])
                    if (i, j) in seen_gates or not (0 <= i < shape[0] and 0 <= j < shape[1]):
                        raise ValueError("duplicate/out-of-range labeled gate")
                    seen_gates.add((i, j))
                    if not arr["EXPERIMENT_VALID_MASK"][i, j]:
                        raise ValueError("label refers to an unobserved gate")
                    if row["label"] == "unknown":
                        continue
                    if row["label"] not in ("weather", "interference", "mixed"):
                        raise ValueError("invalid label")
                    rows.append(
                        (
                            ("weather", "interference", "mixed").index(row["label"]),
                            int(arr["V8_PROPOSED_CONFIRM_MASK"][i, j]),
                            int(arr["V8_PROPOSED_QUARANTINE_MASK"][i, j]),
                            int(arr["BASELINE_QPE_ELIGIBLE_MASK"][i, j]),
                        )
                    )
            all_rows.extend(rows)
            by_radar.setdefault(case["expected_radar_id"], []).extend(rows)
            by_case["/".join(identity)] = _counts(rows)
            inputs.append(
                {
                    "report_sha256": file_hash(report_path),
                    "artifact_sha256": file_hash(artifact),
                    "labels_sha256": file_hash(label),
                    "identity": identity,
                }
            )
    required_radars = spec.get("required_radars", [])
    if not isinstance(required_radars, list) or any(
        not isinstance(r, str) for r in required_radars
    ):
        raise ValueError(
            "required_radars must be a list of station identifiers, not a deletion rule"
        )
    if not all_rows:
        raise ValueError("assessment has no reviewed observed labels")
    report = {
        "schema_version": "rainpulse.measurement-policy-validation.v1",
        "model_sha256": model_sha,
        "policy_sha256": policy_sha,
        "data_kind": kind,
        "labels_source": spec["labels_source"],
        "independent_from_training": True,
        "dataset_sha256": digest(inputs),
        "independent_process_count": len(processes),
        "independent_scan_count": len(scans),
        "overall": _counts(all_rows),
        "by_radar": {k: _counts(v) for k, v in by_radar.items()},
        "required_radars": required_radars,
        "missing_required_radars": sorted(set(required_radars) - set(by_radar)),
        "by_physical_cut": by_case,
        "inputs": inputs,
        "outcome": "INSUFFICIENT"
        if set(required_radars) - set(by_radar)
        else "MANUAL_REVIEW_REQUIRED",
        "operational_eligible": False,
        "notes": "No thresholds or allowed degradations auto-selected.",
    }
    with atomic_directory(output) as tmp:
        write_json(tmp / "policy-validation.json", report)
    return report
