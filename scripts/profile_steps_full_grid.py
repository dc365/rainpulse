#!/usr/bin/env python3
from __future__ import annotations

import argparse
import json
import resource
import sys
import time
from dataclasses import replace
from datetime import UTC, datetime
from pathlib import Path
from uuid import NAMESPACE_URL, UUID, uuid5

import numpy as np
from scipy import ndimage

REPOSITORY_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPOSITORY_ROOT / "algorithms"))

from rainpulse_algo.grid import load_grid_config  # noqa: E402
from rainpulse_algo.nowcast.ensemble_zarr import (  # noqa: E402
    build_ensemble_forecast_output_zarr_store,
)
from rainpulse_algo.nowcast.pysteps_lk import PystepsLKFields  # noqa: E402
from rainpulse_algo.nowcast.pysteps_profile import load_pysteps_lk_profile  # noqa: E402
from rainpulse_algo.nowcast.pysteps_steps import run_pysteps_steps_fields  # noqa: E402
from rainpulse_algo.nowcast.steps_profile import load_pysteps_steps_profile  # noqa: E402
from rainpulse_algo.products.ensemble_builder import (  # noqa: E402
    build_ensemble_application_product_bundle,
)
from rainpulse_algo.products.ensemble_profile import (  # noqa: E402
    load_ensemble_application_product_profile,
)
from rainpulse_algo.worker.object_store import artifact_sha256  # noqa: E402


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Profile RP-023 STEPS on the frozen 501x201 Fuzhou grid."
    )
    parser.add_argument("--member-count", type=int, choices=range(2, 97), default=12)
    parser.add_argument("--output-root", type=Path)
    parser.add_argument("--skip-products", action="store_true")
    arguments = parser.parse_args()

    grid = load_grid_config(REPOSITORY_ROOT / "configs/grids/fuzhou-0p01deg-v1.yaml")
    steps = load_pysteps_steps_profile(
        REPOSITORY_ROOT / "configs/nowcast/rp022-pysteps-steps-v1.yaml"
    )
    steps = replace(
        steps,
        ensemble=replace(steps.ensemble, member_count=arguments.member_count),
    )
    lk = load_pysteps_lk_profile(
        REPOSITORY_ROOT / "configs/nowcast/rp016-pysteps-lk-v1.yaml"
    )
    product_profile = load_ensemble_application_product_profile(
        REPOSITORY_ROOT
        / "configs/products/rp023-ensemble-application-products-v1.yaml"
    )
    fields = _synthetic_textured_fields(grid.shape)
    run_id = uuid5(NAMESPACE_URL, f"rainpulse-rp023-full-grid-{arguments.member_count}")
    model_job_id = uuid5(run_id, "model")
    product_job_id = uuid5(run_id, "products")
    issue_time = datetime(2026, 8, 29, 0, 0, tzinfo=UTC)

    started = time.perf_counter()
    result = run_pysteps_steps_fields(
        fields,
        profile=steps,
        lk_profile=lk,
        grid=grid,
    )
    algorithm_seconds = time.perf_counter() - started

    started = time.perf_counter()
    forecast_objects = build_ensemble_forecast_output_zarr_store(
        result,
        run_id=run_id,
        job_id=model_job_id,
        issue_time=issue_time,
        input_uri="s3://rainpulse/offline-validation/rp023/full-grid-input.zarr",
        input_asset_ids=[uuid5(run_id, f"input-{index}") for index in range(3)],
        profile=steps,
        grid=grid,
        runtime_ms=round(algorithm_seconds * 1000),
    )
    forecast_seconds = time.perf_counter() - started
    forecast_size = sum(len(value) for value in forecast_objects.values())

    product_objects: dict[str, bytes] = {}
    product_seconds = 0.0
    if not arguments.skip_products:
        started = time.perf_counter()
        product_objects = build_ensemble_application_product_bundle(
            forecast_objects,
            source_forecast_uri=(
                f"s3://rainpulse/offline-validation/rp023/{run_id}/forecast.zarr"
            ),
            source_forecast_sha256=artifact_sha256(forecast_objects),
            run_id=run_id,
            job_id=product_job_id,
            profile=product_profile,
            grid=grid,
        )
        product_seconds = time.perf_counter() - started
        if arguments.output_root:
            _write_bundle(arguments.output_root, run_id, product_objects)

    report = {
        "profile": "rp023-full-grid-resource-v1",
        "member_count": arguments.member_count,
        "grid_shape": list(grid.shape),
        "lead_count": 24,
        "algorithm_seconds": round(algorithm_seconds, 3),
        "forecast_zarr_seconds": round(forecast_seconds, 3),
        "application_product_seconds": round(product_seconds, 3),
        "peak_rss_bytes": _peak_rss_bytes(),
        "forecast_zarr_bytes": forecast_size,
        "forecast_zarr_object_count": len(forecast_objects),
        "application_product_bytes": sum(len(value) for value in product_objects.values()),
        "application_product_object_count": len(product_objects),
        "first_lead_common_coverage": float(np.mean(result.output_valid_mask[0] == 1)),
        "last_lead_common_coverage": float(np.mean(result.output_valid_mask[-1] == 1)),
        "first_lead_mean_member_coverage": float(
            np.mean(result.member_valid_mask[:, 0] == 1)
        ),
        "last_lead_mean_member_coverage": float(
            np.mean(result.member_valid_mask[:, -1] == 1)
        ),
        "maximum_rain_rate_mm_h": float(np.nanmax(result.rain_rate)),
        "ensemble_fallback_used": result.ensemble_fallback_used,
        "ensemble_fallback_reason": result.ensemble_fallback_reason,
        "bundle_id": str(run_id) if product_objects else None,
        "output_root": str(arguments.output_root) if arguments.output_root else None,
    }
    print(json.dumps(report, ensure_ascii=False, indent=2, sort_keys=True))


def _synthetic_textured_fields(shape: tuple[int, int]) -> PystepsLKFields:
    height, width = shape
    y, x = np.mgrid[0:height, 0:width]
    base = np.zeros(shape, dtype="float64")
    cells = (
        (0.31, 0.45, 0.070, 0.095, 9.5),
        (0.44, 0.53, 0.050, 0.070, 7.0),
        (0.58, 0.60, 0.085, 0.105, 5.5),
        (0.69, 0.40, 0.060, 0.080, 4.0),
    )
    for centre_x, centre_y, scale_x, scale_y, amplitude in cells:
        base += amplitude * np.exp(
            -0.5
            * (
                ((x / width - centre_x) / scale_x) ** 2
                + ((y / height - centre_y) / scale_y) ** 2
            )
        )
    texture = 1.0 + 0.18 * np.sin(x / 8.0) * np.cos(y / 6.0)
    texture += 0.08 * np.sin((x + y) / 13.0)
    base = np.where(base * texture >= 0.08, base * texture, 0.0)
    rates = np.stack(
        [
            ndimage.shift(
                base,
                shift=(frame * 0.7, frame * 2.0),
                order=1,
                mode="constant",
                cval=0.0,
                prefilter=False,
            )
            for frame in range(3)
        ]
    ).astype("float32")
    reflectivity = np.zeros_like(rates)
    raining = rates >= 0.01
    reflectivity[raining] = 10.0 * np.log10(
        200.0 * np.power(rates[raining], 1.6)
    )
    return PystepsLKFields(
        reflectivity_dbz=reflectivity,
        rate_mm_h=rates,
        quality_index=np.full(rates.shape, 0.9, dtype="float32"),
        valid_mask=np.ones(rates.shape, dtype="uint8"),
        low_quality_mask=np.zeros(rates.shape, dtype="uint8"),
    )


def _write_bundle(root: Path, run_id: UUID, objects: dict[str, bytes]) -> None:
    destination = root.resolve() / str(run_id)
    if destination.exists():
        raise RuntimeError(f"refusing to overwrite existing bundle {destination}")
    for relative_path, data in objects.items():
        path = destination / relative_path
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(data)


def _peak_rss_bytes() -> int:
    value = int(resource.getrusage(resource.RUSAGE_SELF).ru_maxrss)
    return value if sys.platform == "darwin" else value * 1024


if __name__ == "__main__":
    main()
