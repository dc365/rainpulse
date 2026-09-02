#!/usr/bin/env python3
"""Generate non-operational NowcastNet comparison layers for Fujian 8/28.

The public MRMS checkpoint rejects missing input cells.  This command therefore
selects only model-aligned cycles with complete 9 x 10-minute history and full
+10..+120-minute verification truth. It runs overlapping all-valid tiles and
blends them back onto the full product grid without ever filling missing radar
coverage.
"""

from __future__ import annotations

import argparse
import gc
import hashlib
import json
import shutil
import tempfile
import time
from collections import defaultdict
from dataclasses import replace
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any
from uuid import uuid4

import numpy as np

from rainpulse_algo.diagnostics.png import encode_rgba_png
from rainpulse_algo.grid import RegularLatLonGrid, load_grid_config
from rainpulse_algo.nowcast.nowcastnet_adapter import run_nowcastnet_fields
from rainpulse_algo.nowcast.nowcastnet_official_backend import OfficialNowcastNetBackend
from rainpulse_algo.nowcast.nowcastnet_profile import NowcastNetProfile, load_nowcastnet_profile
from rainpulse_algo.nowcast.nowcastnet_shadow import required_frame_times
from rainpulse_algo.nowcast.nowcastnet_shadow_service import (
    AnalysisReference,
    fetch_catalog,
    load_analysis_frame,
    parse_analysis_catalog,
)
from rainpulse_algo.nowcast.nowcastnet_tiling import (
    NowcastNetStitchedResult,
    NowcastNetTileSelection,
    blend_tile_forecasts,
    select_all_valid_tiles,
)
from rainpulse_algo.products.builder import rainfall_rgba
from rainpulse_algo.products.profile import load_product_builder_profile
from rainpulse_algo.worker.object_store import ArtifactObjectReader, minio_client_from_environment

CONTRACT_NAME = "rainpulse.nowcastnet-shadow-product-bundle"
CONTRACT_VERSION = "1.1"
MODEL_CADENCE_MINUTES = 10
INPUT_FRAMES = 9
OUTPUT_LEADS = tuple(range(10, 121, 10))


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Generate Fujian NowcastNet public-weight shadow products"
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
        "--minimum-common-valid-coverage-ratio", type=float, default=0.70
    )
    parser.add_argument("--issue-time", action="append", default=[])
    args = parser.parse_args()
    if not 0 < args.minimum_common_valid_coverage_ratio <= 1:
        raise ValueError("minimum common-valid coverage ratio must be in (0, 1]")

    target_date = datetime.strptime(args.date_utc, "%Y-%m-%d").date()
    grid = load_grid_config(args.grid_config)
    parent = load_nowcastnet_profile(args.model_config)
    products = load_product_builder_profile(args.product_config)
    if products.grid_id != grid.grid_id:
        raise ValueError("product and grid configurations differ")

    catalog = fetch_catalog(args.catalog_url)
    references = parse_analysis_catalog(catalog, grid_id=grid.grid_id)
    by_time = {item.analysis_time: item for item in references}
    requested = {_parse_time(value) for value in args.issue_time}
    candidates = comparison_candidates(by_time, target_date=target_date)
    if requested:
        candidates = [value for value in candidates if value in requested]
        missing = requested.difference(candidates)
        if missing:
            raise ValueError(
                "requested cycles lack complete history or verification truth: "
                + ",".join(sorted(value.isoformat() for value in missing))
            )
    if not candidates:
        raise RuntimeError("no strict NowcastNet comparison cycle is available")

    reader = ArtifactObjectReader(minio_client_from_environment())
    frame_cache: dict[datetime, tuple[np.ndarray, np.ndarray]] = {}
    prepared: list[dict[str, Any]] = []
    groups: dict[tuple[int, int], list[dict[str, Any]]] = defaultdict(list)
    for issue_time in candidates:
        history = required_frame_times(
            issue_time, input_frames=INPUT_FRAMES, timestep_minutes=MODEL_CADENCE_MINUTES
        )
        for frame_time in history:
            if frame_time not in frame_cache:
                frame_cache[frame_time] = load_analysis_frame(reader, by_time[frame_time])
        rates = np.stack([frame_cache[value][0] for value in history], axis=0)
        valid = np.stack([frame_cache[value][1] for value in history], axis=0)
        common_valid = np.all(valid == 1, axis=0)
        selection = select_all_valid_tiles(
            common_valid,
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
            y_start, x_start = tile.y_start, tile.x_start
            height, width = tile.height, tile.width
            job = {
                "cycle": cycle,
                "tile": tile,
                "tile_index": tile_index,
                "rate": np.ascontiguousarray(
                    rates[:, y_start : y_start + height, x_start : x_start + width],
                    dtype="float32",
                ),
                "valid": np.ones((INPUT_FRAMES, height, width), dtype="uint8"),
            }
            groups[(height, width)].append(job)
    if not prepared:
        raise RuntimeError("strict cycles have no sufficiently large all-valid model tile")

    reports: list[dict[str, Any]] = []
    published_ids: set[str] = set()
    for (height, width), jobs in sorted(groups.items()):
        profile = experimental_profile(parent, height=height, width=width)
        backend = OfficialNowcastNetBackend(
            args.capsule_root, profile=profile, device=args.device
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
            runtime_seconds = time.perf_counter() - started
            ensemble_mean = np.mean(result.rain_rate_mm_h[:, : len(OUTPUT_LEADS)], axis=0)
            cycle["tile_forecasts"].append((job["tile"], ensemble_mean))
            cycle["runtime_seconds"] += runtime_seconds
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

    for cycle in prepared:
        selection: NowcastNetTileSelection = cycle["selection"]
        tile_forecasts = sorted(
            cycle["tile_forecasts"],
            key=lambda item: selection.tiles.index(item[0]),
        )
        stitched = blend_tile_forecasts(tile_forecasts, output_shape=grid.shape)
        gate = stitched_gate(
            stitched,
            selection=selection,
            runtime_seconds=cycle["runtime_seconds"],
            minimum_common_valid_coverage_ratio=(
                args.minimum_common_valid_coverage_ratio
            ),
        )
        report: dict[str, Any] = {
            "issue_time": cycle["issue_time"].isoformat(),
            "tile_count": len(selection.tiles),
            "domain_coverage_ratio": round(selection.domain_coverage_ratio, 4),
            "common_valid_coverage_ratio": round(
                selection.common_valid_coverage_ratio, 4
            ),
            "coverage_gain_over_primary": round(
                selection.covered_cell_count / selection.tiles[0].area, 4
            ),
            "runtime_seconds": round(cycle["runtime_seconds"], 3),
            "stitch_gate": gate,
            "operational_eligible": False,
        }
        if gate["passed"]:
            bundle_id, bundle = build_bundle(
                stitched,
                issue_time=cycle["issue_time"],
                references=cycle["references"],
                selection=selection,
                grid=grid,
                parent_profile=parent,
                palette=products.palette,
                clipped_input_pixel_count=cycle["clipped_input_pixel_count"],
                clipped_negative_output_pixel_count=cycle[
                    "clipped_negative_output_pixel_count"
                ],
                runtime_seconds=cycle["runtime_seconds"],
                runtime_info=cycle["runtime_info"],
                publication_gate=gate,
            )
            write_bundle(args.output_root, bundle_id, bundle)
            prune_cycle_versions(
                args.output_root,
                issue_time=cycle["issue_time"],
                grid_id=grid.grid_id,
                keep_bundle_id=bundle_id,
            )
            published_ids.add(bundle_id)
            report["bundle_id"] = bundle_id
            report["status"] = "published"
        else:
            report["status"] = "retained_previous_product"
        reports.append(report)
        print(json.dumps(report, ensure_ascii=False), flush=True)

    print(
        json.dumps(
            {
                "status": "generated",
                "cycle_count": len(prepared),
                "published_cycle_count": len(published_ids),
                "bundle_ids": sorted(published_ids),
                "operational_eligible": False,
            },
            ensure_ascii=False,
        )
    )


def comparison_candidates(
    by_time: dict[datetime, AnalysisReference], *, target_date: Any
) -> list[datetime]:
    candidates: list[datetime] = []
    for issue_time in sorted(by_time):
        if issue_time.date() != target_date or issue_time.minute % MODEL_CADENCE_MINUTES:
            continue
        history = required_frame_times(
            issue_time, input_frames=INPUT_FRAMES, timestep_minutes=MODEL_CADENCE_MINUTES
        )
        truth = tuple(issue_time + timedelta(minutes=lead) for lead in OUTPUT_LEADS)
        if all(value in by_time for value in (*history, *truth)):
            candidates.append(issue_time)
    return candidates


def experimental_profile(
    parent: NowcastNetProfile, *, height: int, width: int
) -> NowcastNetProfile:
    return replace(
        parent,
        profile_version=f"fujian-nowcastnet-public-shadow-v1-{height}x{width}",
        protocol=replace(parent.protocol, input_height=height, input_width=width),
    )


def stitched_gate(
    stitched: NowcastNetStitchedResult,
    *,
    selection: NowcastNetTileSelection,
    runtime_seconds: float,
    minimum_common_valid_coverage_ratio: float = 0.70,
) -> dict[str, Any]:
    primary_cell_count = selection.tiles[0].area
    coverage_gain = selection.covered_cell_count / primary_cell_count
    checks = {
        "common_valid_coverage_ratio": (
            selection.common_valid_coverage_ratio
            >= minimum_common_valid_coverage_ratio
        ),
        "coverage_gain_over_primary": coverage_gain >= 1.20,
        "primary_consistency_mae_mm_h": (
            stitched.primary_consistency_mae_mm_h <= 1.0
        ),
        "primary_consistency_p95_mm_h": (
            stitched.primary_consistency_p95_mm_h <= 5.0
        ),
        "seam_gradient_ratio": (
            np.isfinite(stitched.seam_gradient_ratio)
            and stitched.seam_gradient_ratio <= 1.25
        ),
        "runtime_seconds": runtime_seconds <= 30.0,
    }
    return {
        "passed": all(checks.values()),
        "checks": checks,
        "thresholds": {
            "minimum_common_valid_coverage_ratio": (
                minimum_common_valid_coverage_ratio
            ),
            "minimum_coverage_gain_over_primary": 1.20,
            "maximum_primary_consistency_mae_mm_h": 1.0,
            "maximum_primary_consistency_p95_mm_h": 5.0,
            "maximum_seam_gradient_ratio": 1.25,
            "maximum_runtime_seconds": 30.0,
        },
        "measurements": {
            "common_valid_coverage_ratio": selection.common_valid_coverage_ratio,
            "coverage_gain_over_primary": coverage_gain,
            "primary_consistency_mae_mm_h": (
                stitched.primary_consistency_mae_mm_h
            ),
            "primary_consistency_p95_mm_h": (
                stitched.primary_consistency_p95_mm_h
            ),
            "seam_gradient_ratio": stitched.seam_gradient_ratio,
            "runtime_seconds": runtime_seconds,
        },
    }


def build_bundle(
    stitched: NowcastNetStitchedResult,
    *,
    issue_time: datetime,
    references: list[AnalysisReference],
    selection: NowcastNetTileSelection,
    grid: RegularLatLonGrid,
    parent_profile: NowcastNetProfile,
    palette: Any,
    clipped_input_pixel_count: int,
    clipped_negative_output_pixel_count: int,
    runtime_seconds: float,
    runtime_info: dict[str, object] | None,
    publication_gate: dict[str, Any],
) -> tuple[str, dict[str, bytes]]:
    values = np.asarray(stitched.rain_rate_mm_h, dtype="float32")
    valid = np.asarray(stitched.valid_mask, dtype="uint8")
    if values.shape != (len(OUTPUT_LEADS), *grid.shape) or valid.shape != values.shape:
        raise ValueError("stitched NowcastNet output dimensions differ")
    if not np.array_equal(valid, np.broadcast_to(valid[0], valid.shape)):
        raise ValueError("stitched NowcastNet support must remain fixed across leads")
    bounds = grid.pixel_edge_bounds
    bundle_id = str(uuid4())
    created_at = datetime.now(UTC)
    objects: dict[str, bytes] = {}
    frames: list[dict[str, Any]] = []
    support = valid[0] == 1
    valid_count = int(np.count_nonzero(support))
    cell_count = int(support.size)
    for index, lead in enumerate(OUTPUT_LEADS):
        png = encode_rgba_png(
            rainfall_rgba(
                values[index],
                support,
                palette.rain_rate,
                transparent_below=palette.transparent_below_mm,
                opacity=palette.opacity,
            )
        )
        asset_id = f"ensemble-mean-lead-{lead:03d}-png"
        object_path = f"lead-{lead:03d}/ensemble-mean.png"
        objects[object_path] = png
        frames.append(
            {
                "asset_id": asset_id,
                "object_path": object_path,
                "media_type": "image/png",
                "sha256": hashlib.sha256(png).hexdigest(),
                "size_bytes": len(png),
                "lead_time_minutes": lead,
                "valid_time": (issue_time + timedelta(minutes=lead)).isoformat(),
                "unit": "mm/h",
                "coverage_ratio": valid_count / cell_count,
                "valid_cell_count": valid_count,
                "missing_cell_count": cell_count - valid_count,
                "minimum": float(np.min(values[index][support])),
                "maximum": float(np.max(values[index][support])),
                "pixel_edge_bounds": list(bounds),
            }
        )
    manifest = {
        "contract_name": CONTRACT_NAME,
        "contract_version": CONTRACT_VERSION,
        "bundle_id": bundle_id,
        "issue_time": issue_time.isoformat(),
        "grid_id": grid.grid_id,
        "grid_config_version": grid.config_version,
        "width": grid.longitude_count,
        "height": grid.latitude_count,
        "pixel_edge_bounds": list(bounds),
        "model_id": "nowcastnet",
        "model_version": parent_profile.model_version,
        "profile_version": "fujian-nowcastnet-public-shadow-stitched-v1",
        "member_count": parent_profile.protocol.ensemble_members,
        "cadence_minutes": MODEL_CADENCE_MINUTES,
        "lifecycle": "shadow",
        "operational_eligible": False,
        "preprocessing": "native-0.01-degree-engineering-feasibility-v1",
        "missing_policy": "reject_any_missing",
        "stitching": {
            "algorithm": "all-valid-greedy-tiles-cosine-weighted-v1",
            "minimum_tile_size": min(
                min(tile.height, tile.width) for tile in selection.tiles
            ),
            "tile_count": len(selection.tiles),
            "common_valid_cell_count": selection.common_valid_cell_count,
            "covered_cell_count": selection.covered_cell_count,
            "common_valid_coverage_ratio": selection.common_valid_coverage_ratio,
            "domain_coverage_ratio": selection.domain_coverage_ratio,
            "overlap_cell_count": stitched.overlap_cell_count,
            "overlap_difference_p95_mm_h": stitched.overlap_difference_p95_mm_h,
            "primary_consistency_mae_mm_h": stitched.primary_consistency_mae_mm_h,
            "primary_consistency_p95_mm_h": stitched.primary_consistency_p95_mm_h,
            "seam_gradient_ratio": stitched.seam_gradient_ratio,
            "tiles": [
                {
                    "tile_index": index,
                    "y_start": tile.y_start,
                    "x_start": tile.x_start,
                    "height": tile.height,
                    "width": tile.width,
                    "profile_version": (
                        f"fujian-nowcastnet-public-shadow-v1-{tile.height}x{tile.width}"
                    ),
                }
                for index, tile in enumerate(selection.tiles)
            ],
        },
        "publication_gate": publication_gate,
        "input_analysis": [
            {
                "analysis_id": item.analysis_id,
                "analysis_time": item.analysis_time.isoformat(),
                "analysis_uri": item.analysis_uri,
            }
            for item in references
        ],
        "legend_unit": "mm/h",
        "legend": [
            {"minimum": float(stop.minimum), "color": stop.color}
            for stop in palette.rain_rate
        ],
        "frames": frames,
        "diagnostics": {
            "clipped_input_pixel_count": clipped_input_pixel_count,
            "clipped_negative_output_pixel_count": clipped_negative_output_pixel_count,
            "runtime_seconds": runtime_seconds,
            "runtime": runtime_info,
        },
        "created_at": created_at.isoformat(),
    }
    objects["manifest.json"] = json.dumps(
        manifest, ensure_ascii=False, separators=(",", ":"), sort_keys=True
    ).encode()
    return bundle_id, objects


def write_bundle(root: Path, bundle_id: str, objects: dict[str, bytes]) -> None:
    root = root.resolve()
    root.mkdir(parents=True, exist_ok=True)
    destination = root / bundle_id
    with tempfile.TemporaryDirectory(prefix=f".{bundle_id}-", dir=root) as temporary:
        staging = Path(temporary)
        for relative_path, data in objects.items():
            path = staging / relative_path
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_bytes(data)
        staging.chmod(0o755)
        staging.rename(destination)


def prune_cycle_versions(
    root: Path, *, issue_time: datetime, grid_id: str, keep_bundle_id: str
) -> None:
    for path, manifest in bundle_manifests(root):
        try:
            manifest_issue_time = _parse_time(str(manifest.get("issue_time")))
        except ValueError:
            continue
        if (
            path.name != keep_bundle_id
            and manifest.get("grid_id") == grid_id
            and manifest_issue_time == issue_time
        ):
            shutil.rmtree(path)


def bundle_manifests(root: Path) -> list[tuple[Path, dict[str, Any]]]:
    if not root.is_dir():
        return []
    result: list[tuple[Path, dict[str, Any]]] = []
    for path in root.iterdir():
        manifest_path = path / "manifest.json"
        if not path.is_dir() or not manifest_path.is_file():
            continue
        try:
            manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            continue
        if manifest.get("contract_name") == CONTRACT_NAME:
            result.append((path, manifest))
    return result


def _parse_time(value: str) -> datetime:
    try:
        parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError as exc:
        raise ValueError(f"invalid UTC timestamp {value!r}") from exc
    if parsed.utcoffset() is None:
        raise ValueError(f"timestamp lacks UTC offset: {value!r}")
    return parsed.astimezone(UTC).replace(microsecond=0)


if __name__ == "__main__":
    main()
