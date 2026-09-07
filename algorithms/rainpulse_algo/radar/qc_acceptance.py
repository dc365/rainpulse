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
    failed_acceptance_metrics,
    gauge_verification_metrics,
    insufficient_acceptance_metrics,
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
    predicted, predicted_error = _predicted_mask(
        probability,
        valid,
        anomaly_threshold=anomaly_threshold,
    )

    if "truth_anomaly" in arrays:
        classification = _classification_report(
            arrays["truth_anomaly"],
            probability,
            valid_mask=valid,
            threshold=anomaly_threshold,
        )
    else:
        classification = unavailable_acceptance_metrics("labelled_anomaly_truth_unavailable")

    if "truth_meteorological" in arrays and "retained_mask" in arrays:
        retention = _retention_report(
            arrays["truth_meteorological"],
            arrays["retained_mask"],
            valid_mask=valid,
        )
    else:
        retention = unavailable_acceptance_metrics(
            "labelled_meteorological_truth_or_retained_mask_unavailable"
        )

    if "ranges_m" in arrays and "azimuth_deg" in arrays:
        pollution_area = _pollution_area_report(
            predicted,
            predicted_error,
            arrays["ranges_m"],
            arrays["azimuth_deg"],
            beam_width_deg=arrays.get("beam_width_deg"),
        )
    else:
        pollution_area = unavailable_acceptance_metrics(
            "polar_range_or_azimuth_coordinates_unavailable"
        )

    qpe = _qpe_report(arrays)
    if "qpe_accumulation_mm" in arrays and "gauge_accumulation_mm" in arrays:
        gauge = _gauge_report(arrays)
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


def _predicted_mask(
    probability: np.ndarray,
    valid_mask: np.ndarray | None,
    *,
    anomaly_threshold: float,
) -> tuple[np.ndarray | None, str | None]:
    predicted = np.isfinite(probability) & (probability >= anomaly_threshold)
    if valid_mask is None:
        return predicted, None
    valid_values = np.asarray(valid_mask)
    if valid_values.shape != probability.shape:
        return None, "acceptance valid mask must match anomaly probability"
    predicted &= np.isfinite(valid_values) & (valid_values != 0)
    return predicted, None


def _classification_report(
    truth_anomaly: np.ndarray,
    probability: np.ndarray,
    *,
    valid_mask: np.ndarray | None,
    threshold: float,
) -> dict[str, Any]:
    try:
        metrics = echo_classification_metrics(
            truth_anomaly,
            probability,
            threshold=threshold,
            valid_mask=valid_mask,
        )
    except ValueError as error:
        return failed_acceptance_metrics(str(error))
    if metrics["evaluated_gate_count"] == 0:
        return insufficient_acceptance_metrics(
            "no_valid_anomaly_truth_pairs",
            **metrics,
        )
    return {"status": "computed", **metrics}


def _retention_report(
    truth_meteorological: np.ndarray,
    retained_mask: np.ndarray,
    *,
    valid_mask: np.ndarray | None,
) -> dict[str, Any]:
    try:
        rate = real_precipitation_retention_rate(
            truth_meteorological,
            retained_mask,
            valid_mask=valid_mask,
        )
        eligible_gate_count = _retention_eligible_gate_count(
            truth_meteorological,
            retained_mask,
            valid_mask=valid_mask,
        )
    except ValueError as error:
        return failed_acceptance_metrics(str(error))
    payload = {"eligible_gate_count": eligible_gate_count, "rate": rate}
    if eligible_gate_count == 0:
        return insufficient_acceptance_metrics(
            "no_valid_meteorological_pairs",
            **payload,
        )
    return {"status": "computed", **payload}


def _pollution_area_report(
    predicted: np.ndarray | None,
    predicted_error: str | None,
    ranges_m: np.ndarray,
    azimuth_deg: np.ndarray,
    *,
    beam_width_deg: np.ndarray | None = None,
) -> dict[str, Any]:
    if predicted_error is not None:
        return failed_acceptance_metrics(predicted_error)
    assert predicted is not None
    try:
        if beam_width_deg is not None and np.asarray(beam_width_deg).size != 1:
            raise ValueError("beam_width_deg must be a scalar")
        area = polar_mask_area_km2(
            predicted,
            ranges_m,
            azimuth_deg,
            beam_width_deg=None
            if beam_width_deg is None
            else float(np.asarray(beam_width_deg).item()),
        )
        if area is None:
            return unavailable_acceptance_metrics("verified_horizontal_beam_width_unavailable")
        return {
            "status": "computed",
            "evaluated_gate_count": int(np.count_nonzero(np.isfinite(predicted))),
            "selected_gate_count": int(np.count_nonzero(predicted)),
            "area_km2": area,
            "beam_width_deg": float(np.asarray(beam_width_deg).item()),
            "area_definition": "observed_polar_wedges_per_elevation",
        }
    except ValueError as error:
        return failed_acceptance_metrics(str(error))


def _qpe_report(arrays: Mapping[str, np.ndarray]) -> dict[str, Any]:
    if "qpe_rate_mm_h" not in arrays:
        return unavailable_acceptance_metrics("downstream_qpe_rate_unavailable")
    try:
        metrics = qpe_distribution_metrics(
            arrays["qpe_rate_mm_h"],
            valid_mask=arrays.get("qpe_valid_mask"),
        )
    except ValueError as error:
        return failed_acceptance_metrics(str(error))
    if metrics["sample_count"] == 0:
        return insufficient_acceptance_metrics("no_valid_qpe_samples", **metrics)
    return {"status": "computed", **metrics}


def _gauge_report(arrays: Mapping[str, np.ndarray]) -> dict[str, Any]:
    try:
        metrics = gauge_verification_metrics(
            arrays["qpe_accumulation_mm"],
            arrays["gauge_accumulation_mm"],
            valid_mask=arrays.get("gauge_valid_mask"),
        )
    except ValueError as error:
        return failed_acceptance_metrics(str(error))
    if metrics["sample_count"] == 0:
        return insufficient_acceptance_metrics("no_valid_gauge_pairs", **metrics)
    return {"status": "computed", **metrics}


def _retention_eligible_gate_count(
    truth_meteorological: np.ndarray,
    retained_mask: np.ndarray,
    *,
    valid_mask: np.ndarray | None,
) -> int:
    truth_values = np.asarray(truth_meteorological)
    retained_values = np.asarray(retained_mask)
    if truth_values.shape != retained_values.shape:
        raise ValueError("meteorological truth and retained mask must have equal shape")
    eligible = (truth_values != 0) & np.isfinite(truth_values) & np.isfinite(retained_values)
    if valid_mask is not None:
        supplied_values = np.asarray(valid_mask)
        supplied = np.isfinite(supplied_values) & (supplied_values != 0)
        if supplied.shape != truth_values.shape:
            raise ValueError("retention valid mask must match truth shape")
        eligible &= supplied
    return int(np.count_nonzero(eligible))


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
