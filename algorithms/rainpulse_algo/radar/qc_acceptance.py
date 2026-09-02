from __future__ import annotations

import argparse
import json
import os
import sys
from collections.abc import Mapping
from pathlib import Path
from typing import Any

import numpy as np

from .qc_metrics import (
    echo_classification_metrics,
    gauge_verification_metrics,
    polar_mask_area_km2,
    qpe_distribution_metrics,
    real_precipitation_retention_rate,
    unavailable_acceptance_metrics,
)


def build_acceptance_report(
    arrays: Mapping[str, np.ndarray],
    *,
    anomaly_threshold: float,
) -> dict[str, Any]:
    probability = _required(arrays, "predicted_anomaly_probability")
    valid = arrays.get("valid_mask")
    predicted = np.isfinite(probability) & (probability >= anomaly_threshold)
    if valid is not None:
        valid_values = np.asarray(valid)
        if valid_values.shape != probability.shape:
            raise ValueError("acceptance valid mask must match anomaly probability")
        predicted &= np.isfinite(valid_values) & (valid_values != 0)

    if "truth_anomaly" in arrays:
        classification: dict[str, Any] = {
            "status": "computed",
            **echo_classification_metrics(
                arrays["truth_anomaly"],
                probability,
                threshold=anomaly_threshold,
                valid_mask=valid,
            ),
        }
    else:
        classification = unavailable_acceptance_metrics(
            "labelled_anomaly_truth_unavailable"
        )

    if "truth_meteorological" in arrays and "retained_mask" in arrays:
        retention: dict[str, Any] = {
            "status": "computed",
            "rate": real_precipitation_retention_rate(
                arrays["truth_meteorological"],
                arrays["retained_mask"],
                valid_mask=valid,
            ),
        }
    else:
        retention = unavailable_acceptance_metrics(
            "labelled_meteorological_truth_or_retained_mask_unavailable"
        )

    if "ranges_m" in arrays and "azimuth_deg" in arrays:
        pollution_area: dict[str, Any] = {
            "status": "computed",
            "area_km2": polar_mask_area_km2(
                predicted,
                arrays["ranges_m"],
                arrays["azimuth_deg"],
            ),
        }
    else:
        pollution_area = unavailable_acceptance_metrics(
            "polar_range_or_azimuth_coordinates_unavailable"
        )

    qpe = (
        {
            "status": "computed",
            **qpe_distribution_metrics(
                arrays["qpe_rate_mm_h"],
                valid_mask=arrays.get("qpe_valid_mask"),
            ),
        }
        if "qpe_rate_mm_h" in arrays
        else unavailable_acceptance_metrics("downstream_qpe_rate_unavailable")
    )
    if "qpe_accumulation_mm" in arrays and "gauge_accumulation_mm" in arrays:
        gauge: dict[str, Any] = {
            "status": "computed",
            **gauge_verification_metrics(
                arrays["qpe_accumulation_mm"],
                arrays["gauge_accumulation_mm"],
                valid_mask=arrays.get("gauge_valid_mask"),
            ),
        }
    else:
        gauge = unavailable_acceptance_metrics(
            "collocated_quality_controlled_gauge_accumulation_unavailable"
        )

    return {
        "schema_version": "1.0",
        "anomaly_threshold": anomaly_threshold,
        "echo_classification": classification,
        "real_precipitation_retention": retention,
        "pollution_area": pollution_area,
        "qpe_distribution": qpe,
        "gauge_verification": gauge,
    }


def main() -> int:
    parser = argparse.ArgumentParser(
        description="Build a radar-QC acceptance report without fabricating absent truth."
    )
    parser.add_argument("--input", type=Path, required=True, help="NPZ acceptance bundle")
    parser.add_argument("--output", type=Path, required=True, help="JSON report path")
    parser.add_argument("--anomaly-threshold", type=float, default=0.8)
    args = parser.parse_args()
    if not 0 <= args.anomaly_threshold <= 1:
        parser.error("--anomaly-threshold must be in [0, 1]")
    with np.load(args.input, allow_pickle=False) as archive:
        report = build_acceptance_report(
            {name: archive[name] for name in archive.files},
            anomaly_threshold=args.anomaly_threshold,
        )
    args.output.parent.mkdir(parents=True, exist_ok=True)
    temporary = args.output.with_suffix(f"{args.output.suffix}.tmp")
    temporary.write_text(json.dumps(report, ensure_ascii=False, indent=2) + "\n")
    os.replace(temporary, args.output)
    return 0


def _required(arrays: Mapping[str, np.ndarray], name: str) -> np.ndarray:
    if name not in arrays:
        raise ValueError(f"acceptance bundle is missing {name}")
    return np.asarray(arrays[name])


if __name__ == "__main__":
    sys.exit(main())
