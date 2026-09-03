#!/usr/bin/env python3
"""Generate Fujian NowcastNet shadow products every five minutes.

The public model remains native 9 x 10-minute input and 10-minute output. This
runner only decouples issue cadence from model cadence: a 10:05 issue uses
08:45, 08:55, ..., 10:05 as input. It reuses the accepted tiling, stitching,
publication-gate and bundle writer from the frozen historical runner.
"""

from __future__ import annotations

import argparse
import gc
import importlib.util
import json
import time
from collections import defaultdict
from datetime import datetime
from pathlib import Path
from types import ModuleType
from typing import Any

import numpy as np

from rainpulse_algo.grid import load_grid_config
from rainpulse_algo.nowcast.fujian_shadow_schedule import comparison_candidates
from rainpulse_algo.nowcast.nowcastnet_adapter import run_nowcastnet_fields
from rainpulse_algo.nowcast.nowcastnet_official_backend import OfficialNowcastNetBackend
from rainpulse_algo.nowcast.nowcastnet_profile import load_nowcastnet_profile
from rainpulse_algo.nowcast.nowcastnet_shadow import required_frame_times
from rainpulse_algo.nowcast.nowcastnet_shadow_service import (
    AnalysisReference,
    fetch_catalog,
    load_analysis_frame,
    parse_analysis_catalog,
)
from rainpulse_algo.nowcast.nowcastnet_tiling import (
    NowcastNetTileSelection,
    blend_tile_forecasts,
    select_all_valid_tiles,
)
from rainpulse_algo.products.profile import load_product_builder_profile
from rainpulse_algo.worker.object_store import (
    ArtifactObjectReader,
    minio_client_from_environment,
)

ISSUE_CADENCE_MINUTES = 5
MODEL_TIMESTEP_MINUTES = 10
INPUT_FRAMES = 9
OUTPUT_LEADS = tuple(range(10, 121, 10))


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Generate Fujian NowcastNet shadow products at five-minute issues"
    )
    parser.add_argument("--catalog-url", required=True)
    parser.add_argument("--output-root", type=Path, required=True)
    parser.add_argument("--grid-config", type=Path, required=True)
    parser.add_argument("--model-config", type=Path, required=True)
    parser.add_argument("--product-config", type=Path, required=True)
    parser.add_argument("--capsule-root", type=Path, required=True)
    parser.add_argument("--date-utc", default="2026-08-28")
    parser.add_argument("--device", default="cuda:0")
    parser.add_argument("--minimum-tile-size", type=int, default=64)
    parser.add_argument("--candidate-stride", type=int, default=8)
    parser.add_argument("--maximum-tiles", type=int, default=64)
    parser.add_argument(
        "--minimum-common-valid-coverage-ratio",
        type=float,
        default=0.70,
    )
    parser.add_argument("--issue-time", action="append", default=[])
    args = parser.parse_args()
    if not 0 < args.minimum_common_valid_coverage_ratio <= 1:
        raise ValueError(
            "minimum common-valid coverage ratio must be in (0, 1]"
        )

    legacy = _load_legacy_runner()
    target_date = datetime.strptime(args.date_utc, "%Y-%m-%d").date()
    grid = load_grid_config(args.grid_config)
    parent = load_nowcastnet_profile(args.model_config)
    products = load_product_builder_profile(args.product_config)
    if products.grid_id != grid.grid_id:
        raise ValueError("product and grid configurations differ")

    catalog = fetch_catalog(args.catalog_url)
    references = parse_analysis_catalog(catalog, grid_id=grid.grid_id)
    by_time = {item.analysis_time: item for item in references}
    requested = {legacy._parse_time(value) for value in args.issue_time}
    candidates = comparison_candidates(
        by_time,
        target_date=target_date,
        issue_cadence_minutes=ISSUE_CADENCE_MINUTES,
        input_frames=INPUT_FRAMES,
        input_timestep_minutes=MODEL_TIMESTEP_MINUTES,
        output_lead_minutes=OUTPUT_LEADS,
    )
    if requested:
        candidates = [value for value in candidates if value in requested]
        missing = requested.difference(candidates)
        if missing:
            raise ValueError(
                "requested cycles lack complete 10-minute history or verification truth: "
                + ",".join(sorted(value.isoformat() for value in missing))
            )
    if not candidates:
        raise RuntimeError("no strict five-minute NowcastNet cycle is available")

    reader = ArtifactObjectReader(minio_client_from_environment())
    frame_cache: dict[datetime, tuple[np.ndarray, np.ndarray]] = {}
    prepared: list[dict[str, Any]] = []
    groups: dict[tuple[int, int], list[dict[str, Any]]] = defaultdict(list)
    for issue_time in candidates:
        history = required_frame_times(
            issue_time,
            input_frames=INPUT_FRAMES,
            timestep_minutes=MODEL_TIMESTEP_MINUTES,
            issue_cadence_minutes=ISSUE_CADENCE_MINUTES,
        )
        for frame_time in history:
            if frame_time not in frame_cache:
                frame_cache[frame_time] = load_analysis_frame(
                    reader,
                    by_time[frame_time],
                )
        rates = np.stack([frame_cache[value][0] for value in history], axis=0)
        valid = np.stack([frame_cache[value][1] for value in history], axis=0)
        selection = select_all_valid_tiles(
            np.all(valid == 1, axis=0),
            minimum_tile_size=args.minimum_tile_size,
            candidate_stride=args.candidate_stride,
            maximum_tiles=args.maximum_tiles,
        )
        cycle: dict[str, Any] = {
            "issue_time": issue_time,
            "references": [by_time[value] for value in history],
            "selection": selection,
            "tile_forecasts": [],
            "runtime_seconds": 0.0,
            "clipped_input_pixel_count": 0,
            "clipped_negative_output_pixel_count": 0,
            "runtime_info": None,
        }
        prepared.append(cycle)
        for tile_index, tile in enumerate(selection.tiles):
            ys = slice(tile.y_start, tile.y_start + tile.height)
            xs = slice(tile.x_start, tile.x_start + tile.width)
            groups[(tile.height, tile.width)].append(
                {
                    "cycle": cycle,
                    "tile": tile,
                    "tile_index": tile_index,
                    "rate": np.ascontiguousarray(rates[:, ys, xs], dtype="float32"),
                    "valid": np.ones(
                        (INPUT_FRAMES, tile.height, tile.width),
                        dtype="uint8",
                    ),
                }
            )

    for (height, width), jobs in sorted(groups.items()):
        profile = legacy.experimental_profile(
            parent,
            height=height,
            width=width,
        )
        backend = OfficialNowcastNetBackend(
            args.capsule_root,
            profile=profile,
            device=args.device,
        )
        for job in jobs:
            cycle = job["cycle"]
            issue_time = cycle["issue_time"]
            started = time.perf_counter()
            result = run_nowcastnet_fields(
                job["rate"],
                job["valid"],
                profile=profile,
                backend=backend,
                random_seed=int(issue_time.timestamp()) % (2**32),
            )
            cycle["runtime_seconds"] += time.perf_counter() - started
            ensemble_mean = np.mean(
                result.rain_rate_mm_h[:, : len(OUTPUT_LEADS)],
                axis=0,
            )
            cycle["tile_forecasts"].append((job["tile"], ensemble_mean))
            cycle["clipped_input_pixel_count"] += result.clipped_input_pixel_count
            cycle["clipped_negative_output_pixel_count"] += (
                result.clipped_negative_output_pixel_count
            )
            cycle["runtime_info"] = backend.runtime_info()
        del backend
        gc.collect()
        try:
            import torch

            torch.cuda.empty_cache()
        except (ImportError, RuntimeError):
            pass

    published_ids: set[str] = set()
    for cycle in prepared:
        selection: NowcastNetTileSelection = cycle["selection"]
        tile_forecasts = sorted(
            cycle["tile_forecasts"],
            key=lambda item: selection.tiles.index(item[0]),
        )
        stitched = blend_tile_forecasts(
            tile_forecasts,
            output_shape=grid.shape,
        )
        gate = legacy.stitched_gate(
            stitched,
            selection=selection,
            runtime_seconds=cycle["runtime_seconds"],
            minimum_common_valid_coverage_ratio=(
                args.minimum_common_valid_coverage_ratio
            ),
        )
        report: dict[str, Any] = {
            "issue_time": cycle["issue_time"].isoformat(),
            "issue_cadence_minutes": ISSUE_CADENCE_MINUTES,
            "input_timestep_minutes": MODEL_TIMESTEP_MINUTES,
            "native_output_timestep_minutes": MODEL_TIMESTEP_MINUTES,
            "tile_count": len(selection.tiles),
            "domain_coverage_ratio": round(selection.domain_coverage_ratio, 4),
            "common_valid_coverage_ratio": round(
                selection.common_valid_coverage_ratio,
                4,
            ),
            "runtime_seconds": round(cycle["runtime_seconds"], 3),
            "stitch_gate": gate,
            "operational_eligible": False,
        }
        if gate["passed"]:
            bundle_id, bundle = legacy.build_bundle(
                stitched,
                issue_time=cycle["issue_time"],
                references=cycle["references"],
                selection=selection,
                grid=grid,
                parent_profile=parent,
                palette=products.palette,
                clipped_input_pixel_count=cycle[
                    "clipped_input_pixel_count"
                ],
                clipped_negative_output_pixel_count=cycle[
                    "clipped_negative_output_pixel_count"
                ],
                runtime_seconds=cycle["runtime_seconds"],
                runtime_info=cycle["runtime_info"],
                publication_gate=gate,
            )
            _annotate_bundle_schedule(bundle)
            legacy.write_bundle(args.output_root, bundle_id, bundle)
            published_ids.add(bundle_id)
            report["bundle_id"] = bundle_id
            report["status"] = "published"
        else:
            report["status"] = "retained_previous_product"
        print(json.dumps(report, ensure_ascii=False), flush=True)

    print(
        json.dumps(
            {
                "status": "generated",
                "cycle_count": len(prepared),
                "published_cycle_count": len(published_ids),
                "bundle_ids": sorted(published_ids),
                "issue_cadence_minutes": ISSUE_CADENCE_MINUTES,
                "input_timestep_minutes": MODEL_TIMESTEP_MINUTES,
                "operational_eligible": False,
            },
            ensure_ascii=False,
        )
    )


def _annotate_bundle_schedule(bundle: dict[str, bytes]) -> None:
    manifest = json.loads(bundle["manifest.json"])
    manifest["issue_cadence_minutes"] = ISSUE_CADENCE_MINUTES
    manifest["input_timestep_minutes"] = MODEL_TIMESTEP_MINUTES
    manifest["native_output_timestep_minutes"] = MODEL_TIMESTEP_MINUTES
    manifest["schedule_version"] = "fujian-five-minute-issue-ten-minute-model-v1"
    bundle["manifest.json"] = json.dumps(
        manifest,
        ensure_ascii=False,
        separators=(",", ":"),
        sort_keys=True,
    ).encode()


def _load_legacy_runner() -> ModuleType:
    path = Path(__file__).with_name("backfill_fujian_nowcastnet_shadow.py")
    spec = importlib.util.spec_from_file_location(
        "rainpulse_legacy_fujian_nowcastnet_shadow",
        path,
    )
    if spec is None or spec.loader is None:
        raise RuntimeError(f"cannot load legacy NowcastNet runner: {path}")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


if __name__ == "__main__":
    main()
