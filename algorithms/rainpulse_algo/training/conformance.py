from __future__ import annotations

import argparse
import hashlib
import json
import os
import shutil
import tempfile
from collections import Counter, defaultdict
from collections.abc import Sequence
from concurrent.futures import ProcessPoolExecutor, as_completed
from dataclasses import replace
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

import numpy as np
import zarr
from numcodecs import Blosc

from rainpulse_algo.datasets.mrms_archive import sha256_file
from rainpulse_algo.datasets.mrms_pilot import (
    MRMSPilotError,
    MRMSPilotProfile,
    _tree_sha256,
    build_window_shard_arrays,
    load_mrms_pilot_profile,
    load_pilot_plan,
)

from .data import downsample_all_valid_2x2
from .profile import load_nowcastnet_training_run_profile


class ConformanceDataError(RuntimeError):
    """Raised when the isolated 0.02-degree protocol sample cannot be built."""


def select_conformance_windows(
    windows: Sequence[dict[str, Any]],
    *,
    sample_count: int,
) -> list[dict[str, Any]]:
    if sample_count < 1:
        raise ConformanceDataError("conformance sample count must be positive")
    by_year: dict[int, list[dict[str, Any]]] = defaultdict(list)
    for window in windows:
        if window.get("split") != "training":
            raise ConformanceDataError("conformance source contains a non-training window")
        try:
            year = int(str(window["issue_time"])[:4])
        except (KeyError, TypeError, ValueError) as exc:
            raise ConformanceDataError("conformance source time is invalid") from exc
        by_year[year].append(dict(window))
    years = (2019, 2020, 2021, 2022, 2023)
    if set(by_year) != set(years):
        raise ConformanceDataError("conformance source years differ from the training split")

    selected: list[dict[str, Any]] = []
    offset = 0
    while len(selected) < sample_count:
        progressed = False
        for year in years:
            if len(selected) == sample_count:
                break
            candidates = by_year[year]
            if offset < len(candidates):
                selected.append(dict(candidates[offset]))
                progressed = True
        if not progressed:
            raise ConformanceDataError("not enough windows for the conformance sample")
        offset += 1
    for shard_index, window in enumerate(selected):
        window["source_pilot_shard_index"] = int(window["shard_index"])
        window["shard_index"] = shard_index
    return selected


def materialize_conformance_arrays(
    native_rain_rate: np.ndarray,
    native_valid_mask: np.ndarray,
    *,
    native_crop_size: int,
    model_crop_size: int,
) -> tuple[np.ndarray, np.ndarray]:
    if native_crop_size != model_crop_size * 2:
        raise ConformanceDataError("conformance crop sizes do not close at factor two")
    if native_rain_rate.shape[-2:] != (native_crop_size, native_crop_size):
        raise ConformanceDataError("conformance native array shape differs")
    reduced, reduced_mask = downsample_all_valid_2x2(
        native_rain_rate,
        native_valid_mask,
    )
    return reduced.astype("float16"), reduced_mask


def _read_asset_inventory(path: Path) -> list[dict[str, Any]]:
    assets: list[dict[str, Any]] = []
    try:
        with path.open(encoding="utf-8") as handle:
            for line in handle:
                row = json.loads(line)
                if int(row.get("asset_index", -1)) != len(assets):
                    raise ConformanceDataError("asset inventory indices are not contiguous")
                assets.append(row)
    except (OSError, json.JSONDecodeError) as exc:
        raise ConformanceDataError(f"cannot read asset inventory: {exc}") from exc
    return assets


def _write_conformance_shard(
    output_path: Path,
    *,
    rain_rate: np.ndarray,
    valid_mask: np.ndarray,
    sample: dict[str, Any],
    window: dict[str, Any],
    pilot_profile: MRMSPilotProfile,
    run_profile_sha256: str,
) -> dict[str, Any]:
    if output_path.exists():
        report_path = output_path / "shard-report.json"
        try:
            report = json.loads(report_path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError) as exc:
            raise ConformanceDataError(f"cannot resume conformance shard: {exc}") from exc
        if (
            report.get("status") != "complete"
            or report.get("content_sha256")
            != _tree_sha256(output_path, excluded_names=frozenset({"shard-report.json"}))
        ):
            raise ConformanceDataError("existing conformance shard integrity differs")
        return {**report, "sample": sample, "resumed": True}

    expected_shape = (1, 29, 256, 256)
    if (
        rain_rate.shape != expected_shape
        or valid_mask.shape != expected_shape
        or rain_rate.dtype != np.dtype("float16")
        or valid_mask.dtype != np.dtype("uint8")
        or np.any(valid_mask != 1)
        or np.any(~np.isfinite(rain_rate))
        or np.any(rain_rate < 0.0)
        or np.any(rain_rate > 128.0)
    ):
        raise ConformanceDataError("conformance shard values violate the frozen contract")
    output_path.parent.mkdir(parents=True, exist_ok=True)
    temporary = Path(
        tempfile.mkdtemp(prefix=f".{output_path.name}.tmp-", dir=output_path.parent)
    )
    try:
        group = zarr.open_group(str(temporary), mode="w")
        compressor = Blosc(cname="zstd", clevel=3, shuffle=Blosc.BITSHUFFLE)
        chunks = (1, 29, 256, 256)
        group.create_dataset("rain_rate", data=rain_rate, chunks=chunks, compressor=compressor)
        group.create_dataset("valid_mask", data=valid_mask, chunks=chunks, compressor=compressor)
        group.attrs.update(
            {
                "schema_version": "rainpulse.nowcastnet-mrms-conformance-shard/1.0",
                "run_profile_sha256": run_profile_sha256,
                "pilot_profile_sha256": pilot_profile.profile_sha256,
                "window_id": window["window_id"],
                "shard_index": int(window["shard_index"]),
                "source_pilot_shard_index": int(window["source_pilot_shard_index"]),
                "unit": "mm/h",
                "crs": "EPSG:4326",
                "resolution_deg": 0.02,
                "source_resolution_deg": 0.01,
                "source_native_crop_size": 512,
                "model_crop_size": 256,
                "downsample_method": "block_mean_2x2",
                "downsample_rule_provenance": "rainpulse_conservative_area_mean",
                "official_kernel_published": False,
                "missing_value_policy": "reject_any_missing",
                "latitude_order": "ascending",
                "operational_eligible": False,
            }
        )
        (temporary / "samples.jsonl").write_text(
            json.dumps(sample, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
            + "\n",
            encoding="utf-8",
        )
        content_sha256 = _tree_sha256(temporary)
        report = {
            "schema_version": "1.0",
            "status": "complete",
            "sample_count": 1,
            "logical_bytes": rain_rate.nbytes + valid_mask.nbytes,
            "content_sha256": content_sha256,
            "created_at": datetime.now(UTC).isoformat(),
            "all_samples_valid": True,
            "operational_eligible": False,
        }
        (temporary / "shard-report.json").write_text(
            json.dumps(report, ensure_ascii=False, indent=2, sort_keys=True) + "\n",
            encoding="utf-8",
        )
        os.replace(temporary, output_path)
        return {**report, "sample": sample, "resumed": False}
    except Exception:
        shutil.rmtree(temporary, ignore_errors=True)
        raise


def _process_window(
    pilot_profile: MRMSPilotProfile,
    run_profile_sha256: str,
    dataset_root: Path,
    asset_rows: list[dict[str, Any]],
    window: dict[str, Any],
    output_path: Path,
) -> dict[str, Any]:
    native_profile = replace(
        pilot_profile,
        crop_size=512,
        crops_per_window=1,
        importance_crops_per_window=1,
        uniform_crops_per_window=0,
        minimum_origin_separation_cells=0,
        sample_count=16,
        shard_count=16,
        samples_per_shard=1,
    )
    try:
        native_rain, native_mask, native_samples, diagnostics = build_window_shard_arrays(
            native_profile,
            dataset_root=dataset_root,
            asset_rows=asset_rows,
            window=window,
        )
    except MRMSPilotError as exc:
        raise ConformanceDataError(f"cannot decode conformance window: {exc}") from exc
    rain, mask = materialize_conformance_arrays(
        native_rain,
        native_mask,
        native_crop_size=512,
        model_crop_size=256,
    )
    source = native_samples[0]
    identity = {
        "source_sample_id": source["sample_id"],
        "downsample_method": "block_mean_2x2",
        "resolution_deg": 0.02,
    }
    sample = {
        "sample_id": hashlib.sha256(
            json.dumps(identity, sort_keys=True, separators=(",", ":")).encode()
        ).hexdigest(),
        "sample_index_in_shard": 0,
        "branch": "importance",
        "importance_score": source["importance_score"],
        "x_start_native": source["x_start"],
        "y_start_native": source["y_start"],
        "coordinates": source["coordinates"],
        "source_sample_id": source["sample_id"],
        "source_resolution_deg": 0.01,
        "resolution_deg": 0.02,
        "downsample_method": "block_mean_2x2",
        "rain_rate_mean_mm_h": float(np.mean(rain, dtype="float64")),
        "rain_rate_max_mm_h": float(np.max(rain)),
        "rain_pixel_fraction": float(np.mean(rain > 0.0)),
        "all_valid": True,
        "window_id": window["window_id"],
    }
    report = _write_conformance_shard(
        output_path,
        rain_rate=rain,
        valid_mask=mask,
        sample=sample,
        window=window,
        pilot_profile=pilot_profile,
        run_profile_sha256=run_profile_sha256,
    )
    return {
        **report,
        "shard_index": int(window["shard_index"]),
        "source_times": diagnostics["source_times"],
    }


def build_conformance_dataset(
    *,
    run_profile_path: Path,
    pilot_profile_path: Path,
    repository_root: Path,
    pilot_plan_path: Path,
    audit_root: Path,
    dataset_root: Path,
    output_root: Path,
    workers: int,
) -> dict[str, Any]:
    if workers < 1 or workers > 4:
        raise ConformanceDataError("conformance workers must be between 1 and 4")
    run_profile = load_nowcastnet_training_run_profile(
        run_profile_path,
        repository_root=repository_root,
    )
    pilot_profile = load_mrms_pilot_profile(
        pilot_profile_path,
        repository_root=repository_root,
    )
    try:
        plan = load_pilot_plan(pilot_plan_path, pilot_profile)
    except MRMSPilotError as exc:
        raise ConformanceDataError(f"cannot load pilot plan: {exc}") from exc
    if sha256_file(audit_root / "asset-inventory.jsonl") != pilot_profile.asset_inventory_sha256:
        raise ConformanceDataError("conformance asset inventory SHA-256 differs")
    assets = _read_asset_inventory(audit_root / "asset-inventory.jsonl")
    windows = select_conformance_windows(
        plan["selected_windows"],
        sample_count=run_profile.paper_conformance.expected_sample_count,
    )
    output_root.mkdir(parents=True, exist_ok=True)
    started_at = datetime.now(UTC)
    reports: list[dict[str, Any]] = []
    with ProcessPoolExecutor(max_workers=workers) as pool:
        futures = {}
        for window in windows:
            start = int(window["asset_start_index"])
            end = int(window["asset_end_index"])
            asset_rows = assets[start : end + 1]
            future = pool.submit(
                _process_window,
                pilot_profile,
                run_profile.profile_sha256,
                dataset_root,
                asset_rows,
                window,
                output_root / "shards" / f"shard-{int(window['shard_index']):05d}.zarr",
            )
            futures[future] = window
        for future in as_completed(futures):
            reports.append(future.result())

    samples_path = output_root / "samples.jsonl"
    branch_counts: Counter[str] = Counter()
    with samples_path.open("w", encoding="utf-8") as target:
        for report in sorted(reports, key=lambda value: int(value["shard_index"])):
            sample = dict(report["sample"])
            sample["shard_index"] = int(report["shard_index"])
            branch_counts[str(sample["branch"])] += 1
            target.write(
                json.dumps(sample, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
                + "\n"
            )
    sample_index_sha256 = sha256_file(samples_path)
    finished_at = datetime.now(UTC)
    report = {
        "schema_version": "1.0",
        "status": "complete",
        "dataset_version": "nowcastnet-mrms-paper-conformance-0p02-v1",
        "run_profile_sha256": run_profile.profile_sha256,
        "pilot_profile_sha256": pilot_profile.profile_sha256,
        "pilot_plan_sha256": sha256_file(pilot_plan_path),
        "asset_inventory_sha256": pilot_profile.asset_inventory_sha256,
        "started_at": started_at.isoformat(),
        "finished_at": finished_at.isoformat(),
        "duration_seconds": (finished_at - started_at).total_seconds(),
        "sample_count": len(reports),
        "branch_counts": dict(branch_counts),
        "sample_index_sha256": sample_index_sha256,
        "logical_bytes": sum(int(value["logical_bytes"]) for value in reports),
        "all_samples_valid": True,
        "holdout_windows_processed": 0,
        "source_resolution_deg": 0.01,
        "resolution_deg": 0.02,
        "source_native_crop_size": 512,
        "model_crop_size": 256,
        "downsample_method": "block_mean_2x2",
        "downsample_rule_provenance": "rainpulse_conservative_area_mean",
        "official_kernel_published": False,
        "operational_eligible": False,
    }
    (output_root / "pilot-report.json").write_text(
        json.dumps(report, ensure_ascii=False, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )
    (output_root / "COMPLETED").write_text(sample_index_sha256 + "\n", encoding="utf-8")
    return report


def _build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Build the isolated 0.02-degree sample")
    parser.add_argument("--run-profile", type=Path, required=True)
    parser.add_argument("--pilot-profile", type=Path, required=True)
    parser.add_argument("--repository-root", type=Path, required=True)
    parser.add_argument("--pilot-plan", type=Path, required=True)
    parser.add_argument("--audit-root", type=Path, required=True)
    parser.add_argument("--dataset-root", type=Path, required=True)
    parser.add_argument("--output-root", type=Path, required=True)
    parser.add_argument("--workers", type=int, default=4)
    return parser


def main() -> int:
    args = _build_parser().parse_args()
    result = build_conformance_dataset(
        run_profile_path=args.run_profile,
        pilot_profile_path=args.pilot_profile,
        repository_root=args.repository_root,
        pilot_plan_path=args.pilot_plan,
        audit_root=args.audit_root,
        dataset_root=args.dataset_root,
        output_root=args.output_root,
        workers=args.workers,
    )
    print(json.dumps(result, ensure_ascii=False, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
