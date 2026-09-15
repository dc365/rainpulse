"""Label assembly with process/physical-scan isolation and input integrity checks."""

import csv
import json
from pathlib import Path

import numpy as np

from . import CLASS_NAMES, FEATURE_SCHEMA
from .io import checked, digest, load_npz


def read_pack(path):
    pack = load_npz(path)
    meta = json.loads(str(pack.pop("metadata")))
    if meta.get("schema_version") != FEATURE_SCHEMA:
        raise ValueError("feature pack version mismatch")
    matrix = pack["X"]
    if matrix.ndim != 2 or matrix.shape[1] != len(meta["names"]):
        raise ValueError("feature matrix schema mismatch")
    if np.isinf(matrix).any():
        raise ValueError("infinite features forbidden; uncomputed values must be NaN")
    if pack["ray"].shape != (len(matrix),) or pack["gate"].shape != (len(matrix),):
        raise ValueError("feature coordinates mismatch")
    if pack.get("observed", np.empty(0)).shape != (len(matrix),):
        raise ValueError("observation support mismatch")
    if np.any((pack["observed"] != 0) & (pack["observed"] != 1)):
        raise ValueError("invalid observation support")
    for name in ("ray", "gate"):
        a = pack[name]
        if not np.issubdtype(a.dtype, np.integer) or np.any(a < 0):
            raise ValueError("coordinates must be nonnegative original integer indices")
    if len(set(zip(pack["ray"].tolist(), pack["gate"].tolist()))) != len(matrix):
        raise ValueError("duplicate feature coordinates")
    if len(set(meta["names"])) != len(meta["names"]):
        raise ValueError("duplicate feature names")
    return pack, meta


def load_dataset(manifest_path, cfg):
    path = Path(manifest_path)
    manifest = json.loads(path.read_text())
    if manifest.get("schema_version") != "rainpulse.measurement-dataset.v1":
        raise ValueError("unsupported dataset manifest")
    if set(manifest) != {"schema_version", "packs"} or not manifest["packs"]:
        raise ValueError("dataset must contain exact content-bound pack/label references")
    batches = {k: [] for k in ("train", "calibrate", "validate")}
    processes, scans, observations = {}, {}, set()
    identity = None
    kinds, ids = set(), {k: [] for k in batches}
    total = 0
    unknown_count = 0
    for item in manifest["packs"]:
        if set(item) != {"features", "labels"}:
            raise ValueError("each dataset item requires features and labels")
        pack, meta = read_pack(checked(path.parent, item["features"]))
        case = meta["case"]
        split, process, scan = case["partition"], case["process_id"], case["scan_id"]
        if split not in batches or not process or not scan:
            raise ValueError("declare train/calibrate/validate, process and physical scan")
        for value, registry in ((process, processes), (scan, scans)):
            if value in registry and registry[value] != split:
                raise ValueError("process or physical scan leaks across dataset partitions")
            registry[value] = split
        obs = (case["radar_id"], scan, meta["sweep"])
        if obs in observations:
            raise ValueError("duplicate physical cut, including repeated analysis times")
        observations.add(obs)
        if identity is None:
            identity = meta["feature_identity"]
        if identity != meta["feature_identity"]:
            raise ValueError("different feature/native recipes cannot be mixed in one model")
        kinds.add(case["data_kind"])
        index = {
            (int(r), int(g)): i
            for i, (r, g) in enumerate(zip(pack["ray"], pack["gate"], strict=True))
        }
        selected, labels, seen = [], [], set()
        label_path = checked(path.parent, item["labels"])
        with label_path.open(encoding="utf-8", newline="") as f:
            reader = csv.DictReader(f)
            if reader.fieldnames != ["ray", "gate", "label"]:
                raise ValueError("label CSV header must be ray,gate,label")
            for row in reader:
                coord = int(row["ray"]), int(row["gate"])
                if coord in seen or coord not in index:
                    raise ValueError("duplicate or out-of-range original ray/gate label")
                seen.add(coord)
                i = index[coord]
                if not pack["observed"][i]:
                    raise ValueError("cannot label a missing observation as weather/interference")
                if row["label"] == "unknown":
                    unknown_count += 1
                    continue
                if row["label"] not in CLASS_NAMES:
                    raise ValueError("labels must be weather/interference/mixed/unknown")
                selected.append(i)
                labels.append(CLASS_NAMES.index(row["label"]))
        if not selected:
            continue
        n = len(selected)
        total += n
        if total > cfg.maximum_samples:
            raise ValueError("dataset sample budget exceeded")
        batches[split].append(
            {
                "X": pack["X"][selected],
                "y": np.array(labels, "int8"),
                "process": np.full(n, process),
                "radar": np.full(n, case["radar_id"]),
                "scan": np.full(n, scan),
            }
        )
        ids[split].append(
            {
                "scan_id": scan,
                "process_id": process,
                "radar_id": case["radar_id"],
                "data_kind": case["data_kind"],
            }
        )
    if len(kinds) != 1:
        raise ValueError("real and synthetic data must not be mixed in an artifact")
    result = {}
    for split, parts in batches.items():
        if not parts:
            raise ValueError("each dataset partition needs independently labeled data")
        result[split] = {k: np.concatenate([b[k] for b in parts]) for k in parts[0]}
        if len(set(result[split]["process"])) < cfg.min_processes_per_split:
            raise ValueError("insufficient independent processes in " + split)
        for cls in range(3):
            if np.count_nonzero(result[split]["y"] == cls) < cfg.min_class_samples:
                raise ValueError("insufficient class samples in " + split)
    return result, {
        "dataset_sha256": digest(manifest),
        "feature_identity": identity,
        "names": meta["names"],
        "data_kind": kinds.pop(),
        "partitions": ids,
        "unknown_labels_excluded": unknown_count,
        "split_semantics": "physical_scan_and_process_disjoint",
    }
