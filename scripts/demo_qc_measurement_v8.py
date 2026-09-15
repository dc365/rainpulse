#!/usr/bin/env python3
"""Synthetic offline chain including REAL native execution and REAL RF training.

Not a V7 actual replay or any form of real-weather skill verification.
"""

from pathlib import Path
import argparse
import sys

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "algorithms"))
from rainpulse_algo.radar.qc_engine.measurement_v8.schema import Config
from rainpulse_algo.radar.qc_engine.measurement_v8.synthetic import make_case
from rainpulse_algo.radar.qc_engine.measurement_v8.workbench import (
    extract_case,
    infer_case,
)
from rainpulse_algo.radar.qc_engine.measurement_v8.forest import train
from rainpulse_algo.radar.qc_engine.measurement_v8.assessment import assess
from rainpulse_algo.radar.qc_engine.measurement_v8.io import (
    atomic_directory,
    file_hash,
    write_json,
)


def main():
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument("--output", required=True)
    p.add_argument("--native-binary", required=True)
    p.add_argument("--native-sha256", required=True)
    args = p.parse_args()
    repo = Path(__file__).resolve().parents[1]
    cfg = Config()
    cfg = cfg.model_copy(
        update={"training": cfg.training.model_copy(update={"n_estimators": 16})}
    )
    with atomic_directory(args.output) as tmp:
        packs, test_cases, assessment = [], [], []
        for index, split in enumerate(
            ("train", "train", "calibrate", "calibrate", "validate", "validate")
        ):
            c = make_case(tmp / f"input-{index}", repo, partition=split, index=index)
            out = tmp / f"extract-{index}"
            extract_case(
                c, cfg, out, binary=args.native_binary, binary_sha=args.native_sha256
            )
            feature = out / "sweep_000-features.npz"
            labels = c.parent / "labels.csv"
            packs.append(
                {
                    "features": {
                        "path": feature.relative_to(tmp).as_posix(),
                        "sha256": file_hash(feature),
                    },
                    "labels": {
                        "path": labels.relative_to(tmp).as_posix(),
                        "sha256": file_hash(labels),
                    },
                }
            )
            if split == "validate":
                test_cases.append((index, c, labels))
        write_json(
            tmp / "dataset.json",
            {"schema_version": "rainpulse.measurement-dataset.v1", "packs": packs},
        )
        result = train(tmp / "dataset.json", cfg.training, tmp / "trained")
        model = tmp / "trained/model.json"
        for index, case, labels in test_cases:
            out = tmp / f"infer-{index}"
            infer_case(
                case,
                cfg,
                model,
                file_hash(model),
                out,
                binary=args.native_binary,
                binary_sha=args.native_sha256,
            )
            report = out / "experiment.json"
            assessment.append(
                {
                    "report": {
                        "path": report.relative_to(tmp).as_posix(),
                        "sha256": file_hash(report),
                    },
                    "labels": {
                        "sweep_000": {
                            "path": labels.relative_to(tmp).as_posix(),
                            "sha256": file_hash(labels),
                        }
                    },
                }
            )
        write_json(
            tmp / "assessment.json",
            {
                "schema_version": "rainpulse.measurement-assessment.v1",
                "labels_source": "synthetic_generator",
                "cases": assessment,
            },
        )
        assess(tmp / "assessment.json", tmp / "assessed")
        write_json(
            tmp / "DEMO_ONLY.json",
            {
                "data_kind": "synthetic",
                "actual_native_execution": True,
                "actual_sklearn_training": True,
                "production_model": False,
                "real_weather_validation": False,
                "result": result,
            },
        )
    print(
        "Completed synthetic-only native/features/train/calibrate/infer/assess chain:",
        args.output,
    )


if __name__ == "__main__":
    main()
