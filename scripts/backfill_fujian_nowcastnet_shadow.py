#!/usr/bin/env python3
"""Generate non-operational NowcastNet comparison layers for Fujian 8/28.

The public MRMS checkpoint rejects missing input cells.  This command therefore
selects only model-aligned cycles with complete 9 x 10-minute history and full
+10..+120-minute verification truth, then runs the largest all-valid rectangular
ROI whose dimensions are multiples of 32.  It never fills missing radar coverage.
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
from rainpulse_algo.products.builder import rainfall_rgba
from rainpulse_algo.products.profile import load_product_builder_profile
from rainpulse_algo.worker.object_store import ArtifactObjectReader, minio_client_from_environment

CONTRACT_NAME = "rainpulse.nowcastnet-shadow-product-bundle"
CONTRACT_VERSION = "1.0"
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
    parser.add_argument("--minimum-height", type=int, default=64)
    parser.add_argument("--minimum-width", type=int, default=128)
    parser.add_argument("--issue-time", action="append", default=[])
    args = parser.parse_args()

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
        roi = largest_aligned_valid_rectangle(
            common_valid,
            multiple=32,
            minimum_height=args.minimum_height,
            minimum_width=args.minimum_width,
        )
        if roi is None:
            continue
        y_start, x_start, height, width = roi
        prepared.append(
            {
                "issue_time": issue_time,
                "history": history,
                "references": [by_time[value] for value in history],
                "rate": np.ascontiguousarray(
                    rates[:, y_start : y_start + height, x_start : x_start + width],
                    dtype="float32",
                ),
                "valid": np.ones((INPUT_FRAMES, height, width), dtype="uint8"),
                "roi": roi,
            }
        )
    if not prepared:
        raise RuntimeError("strict cycles have no sufficiently large all-valid model ROI")

    reports: list[dict[str, Any]] = []
    published_ids: set[str] = set()
    groups: dict[tuple[int, int], list[dict[str, Any]]] = defaultdict(list)
    for item in prepared:
        groups[(item["roi"][2], item["roi"][3])].append(item)

    for (height, width), items in sorted(groups.items()):
        profile = experimental_profile(parent, height=height, width=width)
        backend = OfficialNowcastNetBackend(
            args.capsule_root, profile=profile, device=args.device
        )
        for item in items:
            issue_time = item["issue_time"]
            started = time.perf_counter()
            result = run_nowcastnet_fields(
                item["rate"],
                item["valid"],
                profile=profile,
                backend=backend,
                random_seed=int(issue_time.timestamp()) % (2**32),
            )
            runtime_seconds = time.perf_counter() - started
            ensemble_mean = np.mean(result.rain_rate_mm_h[:, : len(OUTPUT_LEADS)], axis=0)
            bundle_id, bundle = build_bundle(
                ensemble_mean,
                issue_time=issue_time,
                references=item["references"],
                roi=item["roi"],
                grid=grid,
                profile=profile,
                palette=products.palette,
                clipped_input_pixel_count=result.clipped_input_pixel_count,
                clipped_negative_output_pixel_count=result.clipped_negative_output_pixel_count,
                runtime_seconds=runtime_seconds,
                runtime_info=backend.runtime_info(),
            )
            write_bundle(args.output_root, bundle_id, bundle)
            prune_cycle_versions(
                args.output_root,
                issue_time=issue_time,
                grid_id=grid.grid_id,
                keep_bundle_id=bundle_id,
            )
            published_ids.add(bundle_id)
            report = {
                "bundle_id": bundle_id,
                "issue_time": issue_time.isoformat(),
                "roi": {
                    "y_start": item["roi"][0],
                    "x_start": item["roi"][1],
                    "height": height,
                    "width": width,
                },
                "member_count": profile.protocol.ensemble_members,
                "lead_minutes": list(OUTPUT_LEADS),
                "runtime_seconds": round(runtime_seconds, 3),
                "maximum_mean_rate_mm_h": round(float(np.max(ensemble_mean)), 3),
                "operational_eligible": False,
            }
            reports.append(report)
            print(json.dumps(report, ensure_ascii=False), flush=True)
        del backend
        gc.collect()
        try:
            import torch

            torch.cuda.empty_cache()
        except (ImportError, RuntimeError):
            pass

    if not requested:
        prune_date_versions(
            args.output_root,
            target_date=target_date,
            grid_id=grid.grid_id,
            keep_bundle_ids=published_ids,
        )
    print(
        json.dumps(
            {
                "status": "generated",
                "cycle_count": len(reports),
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


def largest_aligned_valid_rectangle(
    valid: np.ndarray,
    *,
    multiple: int,
    minimum_height: int,
    minimum_width: int,
) -> tuple[int, int, int, int] | None:
    support = np.asarray(valid, dtype=bool)
    if support.ndim != 2 or multiple < 1:
        raise ValueError("valid support must be a 2-D array with a positive alignment")
    heights = range(_round_up(minimum_height, multiple), support.shape[0] + 1, multiple)
    widths = range(_round_up(minimum_width, multiple), support.shape[1] + 1, multiple)
    shapes = sorted(
        ((height, width) for height in heights for width in widths),
        key=lambda value: (value[0] * value[1], min(value), value[1]),
        reverse=True,
    )
    missing = (~support).astype("int32")
    integral = np.pad(missing, ((1, 0), (1, 0))).cumsum(0).cumsum(1)
    for height, width in shapes:
        window_missing = (
            integral[height:, width:]
            - integral[:-height, width:]
            - integral[height:, :-width]
            + integral[:-height, :-width]
        )
        positions = np.argwhere(window_missing == 0)
        if positions.size:
            y_start, x_start = (int(value) for value in positions[0])
            return y_start, x_start, height, width
    return None


def experimental_profile(
    parent: NowcastNetProfile, *, height: int, width: int
) -> NowcastNetProfile:
    return replace(
        parent,
        profile_version=f"fujian-nowcastnet-public-shadow-v1-{height}x{width}",
        protocol=replace(parent.protocol, input_height=height, input_width=width),
    )


def build_bundle(
    ensemble_mean: np.ndarray,
    *,
    issue_time: datetime,
    references: list[AnalysisReference],
    roi: tuple[int, int, int, int],
    grid: RegularLatLonGrid,
    profile: NowcastNetProfile,
    palette: Any,
    clipped_input_pixel_count: int,
    clipped_negative_output_pixel_count: int,
    runtime_seconds: float,
    runtime_info: dict[str, object],
) -> tuple[str, dict[str, bytes]]:
    values = np.asarray(ensemble_mean, dtype="float32")
    y_start, x_start, height, width = roi
    if values.shape != (len(OUTPUT_LEADS), height, width):
        raise ValueError("NowcastNet comparison output dimensions differ")
    bounds = roi_bounds(grid, roi)
    bundle_id = str(uuid4())
    created_at = datetime.now(UTC)
    objects: dict[str, bytes] = {}
    frames: list[dict[str, Any]] = []
    valid = np.ones((height, width), dtype="uint8")
    for index, lead in enumerate(OUTPUT_LEADS):
        png = encode_rgba_png(
            rainfall_rgba(
                values[index],
                valid,
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
                "coverage_ratio": 1.0,
                "domain_coverage_ratio": height * width / (grid.latitude_count * grid.longitude_count),
                "valid_cell_count": height * width,
                "missing_cell_count": 0,
                "minimum": float(np.min(values[index])),
                "maximum": float(np.max(values[index])),
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
        "model_id": "nowcastnet",
        "model_version": profile.model_version,
        "profile_version": profile.profile_version,
        "member_count": profile.protocol.ensemble_members,
        "cadence_minutes": MODEL_CADENCE_MINUTES,
        "lifecycle": "shadow",
        "operational_eligible": False,
        "preprocessing": "native-0.01-degree-engineering-feasibility-v1",
        "missing_policy": "reject_any_missing",
        "roi": {
            "y_start": y_start,
            "x_start": x_start,
            "height": height,
            "width": width,
            "pixel_edge_bounds": list(bounds),
        },
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


def roi_bounds(
    grid: RegularLatLonGrid, roi: tuple[int, int, int, int]
) -> tuple[float, float, float, float]:
    y_start, x_start, height, width = roi
    half_x = grid.longitude_interval_deg / 2
    half_y = grid.latitude_interval_deg / 2
    west = grid.west + x_start * grid.longitude_interval_deg - half_x
    east = grid.west + (x_start + width - 1) * grid.longitude_interval_deg + half_x
    south = grid.south + y_start * grid.latitude_interval_deg - half_y
    north = grid.south + (y_start + height - 1) * grid.latitude_interval_deg + half_y
    return west, south, east, north


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
        if (
            path.name != keep_bundle_id
            and manifest.get("grid_id") == grid_id
            and _parse_time(str(manifest.get("issue_time"))) == issue_time
        ):
            shutil.rmtree(path)


def prune_date_versions(
    root: Path, *, target_date: Any, grid_id: str, keep_bundle_ids: set[str]
) -> None:
    for path, manifest in bundle_manifests(root):
        if path.name in keep_bundle_ids or manifest.get("grid_id") != grid_id:
            continue
        try:
            issue_time = _parse_time(str(manifest.get("issue_time")))
        except ValueError:
            continue
        if issue_time.date() == target_date:
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


def _round_up(value: int, multiple: int) -> int:
    return ((value + multiple - 1) // multiple) * multiple


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
