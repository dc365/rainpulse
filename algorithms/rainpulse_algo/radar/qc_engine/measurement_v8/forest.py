"""Train with sklearn; export validated JSON trees. No pickle or executable models."""

import json
from pathlib import Path

import numpy as np

from . import CLASS_NAMES, MODEL_SCHEMA
from .dataset import load_dataset
from .io import atomic_directory, file_hash, write_json


def group_weights(groups):
    _, inv, n = np.unique(groups, return_inverse=True, return_counts=True)
    weights = 1.0 / n[inv]
    return weights * len(weights) / weights.sum()


def preprocess(matrix, medians):
    if matrix.ndim != 2 or matrix.shape[1] != len(medians) or np.isinf(matrix).any():
        raise ValueError("model feature shape/values mismatch")
    missing = np.isnan(matrix)
    # Impute FEATURES only with train-fitted medians; retain missing indicators.
    # This function never edits a radar observation or rain field.
    return np.column_stack((np.where(missing, medians, matrix), missing.astype("float32")))


def validate_model(model):
    if model.get("schema_version") != MODEL_SCHEMA or model.get("classes") != list(CLASS_NAMES):
        raise ValueError("unsupported model schema or class order")
    names, medians = model["names"], np.asarray(model["medians"], float)
    if not 1 <= len(names) <= 256 or len(set(names)) != len(names):
        raise ValueError("invalid feature list")
    if medians.shape != (len(names),) or not np.isfinite(medians).all():
        raise ValueError("invalid feature imputer")
    forest = model["trees"]
    if not 1 <= len(forest) <= 256:
        raise ValueError("invalid forest size")
    node_budget = 0
    for tree in forest:
        left, right, feature = [np.asarray(tree[k]) for k in ("left", "right", "feature")]
        threshold = np.asarray(tree["threshold"], float)
        value = np.asarray(tree["value"], float)
        n = len(left)
        node_budget += n
        if (
            n == 0
            or node_budget > 300000
            or any(a.shape != (n,) for a in (right, feature, threshold))
        ):
            raise ValueError("tree size mismatch or budget exceeded")
        if value.shape != (n, 3) or not np.isfinite(value).all() or (value < 0).any():
            raise ValueError("invalid leaf probabilities")
        if not np.allclose(value.sum(axis=1), 1, atol=1e-8) or not np.isfinite(threshold).all():
            raise ValueError("invalid normalized tree probabilities")
        for i in range(n):
            if any(float(a[i]) != int(a[i]) for a in (left, right, feature)):
                raise ValueError("noninteger tree index")
            if left[i] == -1:
                if right[i] != -1:
                    raise ValueError("malformed leaf")
            elif not (i < left[i] < n and i < right[i] < n and 0 <= feature[i] < 2 * len(names)):
                raise ValueError("cyclic/out-of-range tree")
    calibration = model["calibration"]
    w, b = np.asarray(calibration["coef"], float), np.asarray(calibration["intercept"], float)
    if w.shape != (3, 3) or b.shape != (3,) or not np.isfinite(w).all() or not np.isfinite(b).all():
        raise ValueError("invalid calibration parameters")
    if model["data_kind"] not in {"real", "synthetic"}:
        raise ValueError("model must disclose training data origin")
    return model


def load_model(path, expected_sha256):
    if file_hash(path) != expected_sha256:
        raise ValueError("model SHA256 mismatch")
    if Path(path).stat().st_size > 64 * 1024 * 1024:
        raise ValueError("model exceeds size budget")
    return validate_model(json.loads(Path(path).read_text()))


def predict(model, matrix, *, calibrated=True, batch_size=65536):
    validate_model(model)
    if not 1 <= batch_size <= 1000000:
        raise ValueError("invalid inference batch size")
    medians = np.array(model["medians"], float)
    result = np.empty((len(matrix), 3), "float64")
    trees = [{k: np.array(v) for k, v in t.items()} for t in model["trees"]]
    for tree in trees:
        for key in ("left", "right", "feature"):
            tree[key] = tree[key].astype("int64")
    for start in range(0, len(matrix), batch_size):
        x = preprocess(matrix[start : start + batch_size], medians)
        probability = np.zeros((len(x), 3))
        for tree in trees:
            nodes = np.zeros(len(x), int)
            for _ in range(len(tree["left"])):
                active = np.flatnonzero(tree["left"][nodes] != -1)
                if not len(active):
                    break
                at = nodes[active]
                choose_left = x[active, tree["feature"][at]] <= tree["threshold"][at]
                nodes[active] = np.where(choose_left, tree["left"][at], tree["right"][at])
            probability += tree["value"][nodes]
        probability /= len(trees)
        if calibrated:
            cal = model["calibration"]
            logits = (
                np.log(np.clip(probability, 1e-6, 1)) @ np.array(cal["coef"]).T + cal["intercept"]
            )
            logits -= logits.max(axis=1, keepdims=True)
            probability = np.exp(logits)
            probability /= probability.sum(axis=1, keepdims=True)
        result[start : start + len(x)] = probability
    return result


def metrics(y, probability):
    predicted = probability.argmax(axis=1)
    table = np.zeros((3, 3), int)
    np.add.at(table, (y, predicted), 1)
    tp = int(table[1, 1])
    return {
        "n": len(y),
        "confusion_true_rows_predicted_columns": table.tolist(),
        "interference_precision": None if table[:, 1].sum() == 0 else tp / int(table[:, 1].sum()),
        "interference_recall": None if table[1].sum() == 0 else tp / int(table[1].sum()),
        "weather_classified_as_interference": int(table[0, 1]),
        "multiclass_brier": float(np.mean(np.sum((probability - np.eye(3)[y]) ** 2, axis=1))),
        "log_loss": float(-np.log(np.clip(probability[np.arange(len(y)), y], 1e-12, 1)).mean()),
        "meaning": "labeled_classifier_metrics_NOT_applied_QC_skill",
    }


def train(manifest_path, cfg, output):
    from importlib.metadata import version

    from sklearn.ensemble import RandomForestClassifier
    from sklearn.linear_model import LogisticRegression

    if version("scikit-learn") != "1.8.0":
        raise ValueError("training recipe pins scikit-learn==1.8.0")
    data, meta = load_dataset(manifest_path, cfg)
    train_data, cal, test = [data[k] for k in ("train", "calibrate", "validate")]
    x = train_data["X"]
    medians = np.array(
        [
            np.median(x[np.isfinite(x[:, i]), i]) if np.isfinite(x[:, i]).any() else 0
            for i in range(x.shape[1])
        ]
    )
    forest = RandomForestClassifier(
        n_estimators=cfg.n_estimators,
        max_depth=cfg.max_depth,
        min_samples_leaf=cfg.min_samples_leaf,
        n_jobs=1,
        random_state=cfg.random_state,
        class_weight="balanced",
    )
    forest.fit(
        preprocess(x, medians), train_data["y"], sample_weight=group_weights(train_data["process"])
    )
    pcal = forest.predict_proba(preprocess(cal["X"], medians))
    calibration = LogisticRegression(C=1.0, max_iter=1000, random_state=cfg.random_state)
    calibration.fit(
        np.log(np.clip(pcal, 1e-6, 1)), cal["y"], sample_weight=group_weights(cal["process"])
    )
    if list(forest.classes_) != [0, 1, 2] or list(calibration.classes_) != [0, 1, 2]:
        raise ValueError("all three classes are required for training and calibration")
    trees = []
    for estimator in forest.estimators_:
        t = estimator.tree_
        values = t.value[:, 0, :]
        values /= values.sum(axis=1, keepdims=True)
        trees.append(
            {
                "left": t.children_left.tolist(),
                "right": t.children_right.tolist(),
                "feature": t.feature.tolist(),
                "threshold": t.threshold.tolist(),
                "value": values.tolist(),
            }
        )
    model = {
        "schema_version": MODEL_SCHEMA,
        "classes": list(CLASS_NAMES),
        "names": meta["names"],
        "feature_identity": meta["feature_identity"],
        "medians": medians.tolist(),
        "trees": trees,
        "calibration": {
            "method": "multinomial_log_probability_logistic",
            "coef": calibration.coef_.tolist(),
            "intercept": calibration.intercept_.tolist(),
        },
        "data_kind": meta["data_kind"],
        "lineage": meta,
        "training_recipe": cfg.model_dump(mode="json"),
        "sklearn_version": version("scikit-learn"),
        "geometry_domain": {
            name: [
                float(np.nanmin(x[:, meta["names"].index(name)])),
                float(np.nanmax(x[:, meta["names"].index(name)])),
            ]
            for name in (
                "range_km",
                "gate_width_km",
                "azimuth_spacing_deg",
                "elevation_deg",
                "native_support_mode",
            )
            if name in meta["names"] and np.isfinite(x[:, meta["names"].index(name)]).any()
        },
        "operational_eligible": False,
        "weather_validation": "pending_independent_signoff",
    }
    validate_model(model)
    for split in ("calibrate", "validate"):
        a = data[split]
        reference = calibration.predict_proba(
            np.log(np.clip(forest.predict_proba(preprocess(a["X"], medians)), 1e-6, 1))
        )
        if not np.allclose(predict(model, a["X"]), reference, rtol=1e-6, atol=1e-7):
            raise ValueError("JSON exported inference differs from sklearn")
    probability = predict(model, test["X"])
    report = {
        "data_kind": meta["data_kind"],
        "dataset_sha256": meta["dataset_sha256"],
        "overall": metrics(test["y"], probability),
        "by_radar": {},
        "by_process": {},
        "signoff": "REQUIRED",
        "operational_eligible": False,
        "calibration_and_validation_disjoint": True,
        "export_parity_verified": True,
    }
    for key, target in (("radar", "by_radar"), ("process", "by_process")):
        for value in np.unique(test[key]):
            selected = test[key] == value
            report[target][str(value)] = metrics(test["y"][selected], probability[selected])
    with atomic_directory(output) as tmp:
        write_json(tmp / "model.json", model)
        report["model_sha256"] = file_hash(tmp / "model.json")
        write_json(tmp / "validation.json", report)
        write_json(tmp / "lineage.json", meta)
    return report
