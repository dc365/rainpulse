from __future__ import annotations

import argparse
import hashlib
import json
import os
import shutil
import sys
import tempfile
import time
from collections.abc import Iterable, Sequence
from concurrent.futures import ProcessPoolExecutor, as_completed
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any

import numpy as np
import yaml
import zarr
from numcodecs import Blosc

from .mrms_archive import sha256_file
from .mrms_precip import MRMSNativePrecipFrame, read_mrms_native_precip_frame
from .mrms_training import MRMSTrainingProfile, load_mrms_training_profile


class MRMSPilotError(RuntimeError):
    """Raised when the frozen MRMS pilot contract or output is unsafe."""


@dataclass(frozen=True)
class MRMSPilotProfile:
    pilot_version: str
    profile_sha256: str
    training_profile: MRMSTrainingProfile
    training_profile_path: Path
    audit_evidence_path: Path
    audit_evidence_sha256: str
    asset_inventory_sha256: str
    window_index_sha256: str
    holdout_window_index_emitted: bool
    random_seed: int
    years: tuple[int, ...]
    windows_per_year: int
    minimum_separation_minutes: int
    crop_size: int
    candidate_stride_cells: int
    crops_per_window: int
    importance_crops_per_window: int
    uniform_crops_per_window: int
    importance_epsilon: float
    minimum_origin_separation_cells: int
    rain_rate_cap_mm_h: float
    longitude_count: int
    latitude_count: int
    west_edge_deg: float
    east_edge_deg: float
    south_edge_deg: float
    north_edge_deg: float
    resolution_deg: float
    sample_count: int
    shard_count: int
    samples_per_shard: int
    compression_level: int
    minimum_free_bytes: int
    maximum_logical_output_bytes: int


def _resolve_local(repository_root: Path, value: str) -> Path:
    relative = Path(value)
    if relative.is_absolute() or ".." in relative.parts:
        raise MRMSPilotError("pilot references must remain repository-local")
    root = repository_root.resolve()
    resolved = (root / relative).resolve()
    if not resolved.is_relative_to(root):
        raise MRMSPilotError("pilot reference escapes the repository")
    return resolved


def load_mrms_pilot_profile(
    path: Path,
    *,
    repository_root: Path,
) -> MRMSPilotProfile:
    try:
        payload = path.read_bytes()
        raw = yaml.safe_load(payload)
        if not isinstance(raw, dict):
            raise MRMSPilotError("pilot profile must be an object")
        training_ref = raw["training_profile"]
        evidence_ref = raw["audit_evidence"]
        temporal = raw["temporal_sampling"]
        spatial = raw["spatial_sampling"]
        native = raw["native_grid"]
        output = raw["output"]
    except (KeyError, OSError, TypeError, yaml.YAMLError) as exc:
        raise MRMSPilotError(f"cannot load pilot profile {path}: {exc}") from exc

    training_path = _resolve_local(repository_root, str(training_ref["path"]))
    evidence_path = _resolve_local(repository_root, str(evidence_ref["path"]))
    if sha256_file(training_path) != str(training_ref["sha256"]):
        raise MRMSPilotError("pilot training-profile SHA-256 differs")
    if sha256_file(evidence_path) != str(evidence_ref["sha256"]):
        raise MRMSPilotError("pilot audit-evidence SHA-256 differs")
    training_profile = load_mrms_training_profile(training_path)
    try:
        evidence = json.loads(evidence_path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise MRMSPilotError(f"cannot load pilot audit evidence: {exc}") from exc

    asset_inventory_sha256 = str(evidence_ref["asset_inventory_sha256"])
    window_index_sha256 = str(evidence_ref["window_index_sha256"])
    if (
        evidence.get("profile_sha256") != training_profile.profile_sha256
        or evidence.get("artifacts", {}).get("asset_inventory", {}).get("sha256")
        != asset_inventory_sha256
        or evidence.get("artifacts", {}).get("window_index", {}).get("sha256")
        != window_index_sha256
        or evidence.get("integrity", {}).get("transport_integrity") is not True
        or evidence.get("splits", {}).get("independent_holdout", {}).get(
            "indexed_window_count"
        )
        != 0
        or evidence.get("operational_eligible") is not False
    ):
        raise MRMSPilotError("pilot audit evidence differs from the accepted boundary")

    years = tuple(int(value) for value in temporal["years"])
    windows_per_year = int(temporal["windows_per_year"])
    crops_per_window = int(spatial["crops_per_window"])
    importance_crops = int(spatial["importance_crops_per_window"])
    uniform_crops = int(spatial["uniform_crops_per_window"])
    sample_count = int(output["sample_count"])
    shard_count = int(output["shard_count"])
    samples_per_shard = int(output["samples_per_shard"])
    if (
        raw.get("schema_version") != "1.0"
        or raw.get("pilot_version") != "nowcastnet-mrms-pilot-v1"
        or raw.get("lifecycle") != "offline_training_pilot"
        or raw.get("operational_eligible") is not False
        or temporal.get("split") != "training"
        or years != (2019, 2020, 2021, 2022, 2023)
        or int(temporal["random_seed"]) != 2026083001
        or windows_per_year != 80
        or int(temporal["minimum_separation_minutes"]) != 360
        or int(spatial["crop_size"]) != 256
        or int(spatial["candidate_stride_cells"]) != 32
        or crops_per_window != 25
        or importance_crops != 20
        or uniform_crops != 5
        or importance_crops + uniform_crops != crops_per_window
        or float(spatial["importance_epsilon"]) != 1.0
        or int(spatial["minimum_origin_separation_cells"]) != 128
        or float(spatial["rain_rate_cap_mm_h"])
        != training_profile.rain_rate_cap_mm_h
        or spatial.get("reject_any_missing") is not True
        or int(native["longitude_count"]) != 7000
        or int(native["latitude_count"]) != 3500
        or float(native["resolution_deg"]) != 0.01
        or native.get("latitude_order") != "ascending_after_decode"
        or output.get("format") != "zarr-v2-window-shards"
        or output.get("rain_rate_dtype") != "float16"
        or output.get("valid_mask_dtype") != "uint8"
        or output.get("compressor") != "blosc-zstd-bitshuffle"
        or sample_count != 10000
        or shard_count != len(years) * windows_per_year
        or samples_per_shard != crops_per_window
        or sample_count != shard_count * samples_per_shard
        or int(output["minimum_free_bytes"])
        != training_profile.pilot_minimum_free_bytes
        or int(output["maximum_logical_output_bytes"])
        != training_profile.pilot_maximum_output_bytes
    ):
        raise MRMSPilotError("pilot profile differs from frozen v1 invariants")

    return MRMSPilotProfile(
        pilot_version=str(raw["pilot_version"]),
        profile_sha256=hashlib.sha256(payload).hexdigest(),
        training_profile=training_profile,
        training_profile_path=training_path,
        audit_evidence_path=evidence_path,
        audit_evidence_sha256=str(evidence_ref["sha256"]),
        asset_inventory_sha256=asset_inventory_sha256,
        window_index_sha256=window_index_sha256,
        holdout_window_index_emitted=False,
        random_seed=int(temporal["random_seed"]),
        years=years,
        windows_per_year=windows_per_year,
        minimum_separation_minutes=int(temporal["minimum_separation_minutes"]),
        crop_size=int(spatial["crop_size"]),
        candidate_stride_cells=int(spatial["candidate_stride_cells"]),
        crops_per_window=crops_per_window,
        importance_crops_per_window=importance_crops,
        uniform_crops_per_window=uniform_crops,
        importance_epsilon=float(spatial["importance_epsilon"]),
        minimum_origin_separation_cells=int(spatial["minimum_origin_separation_cells"]),
        rain_rate_cap_mm_h=float(spatial["rain_rate_cap_mm_h"]),
        longitude_count=int(native["longitude_count"]),
        latitude_count=int(native["latitude_count"]),
        west_edge_deg=float(native["west_edge_deg"]),
        east_edge_deg=float(native["east_edge_deg"]),
        south_edge_deg=float(native["south_edge_deg"]),
        north_edge_deg=float(native["north_edge_deg"]),
        resolution_deg=float(native["resolution_deg"]),
        sample_count=sample_count,
        shard_count=shard_count,
        samples_per_shard=samples_per_shard,
        compression_level=int(output["compression_level"]),
        minimum_free_bytes=int(output["minimum_free_bytes"]),
        maximum_logical_output_bytes=int(output["maximum_logical_output_bytes"]),
    )


def _parse_utc(value: str) -> datetime:
    try:
        parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError as exc:
        raise MRMSPilotError(f"invalid UTC timestamp: {value}") from exc
    if parsed.tzinfo is None or parsed.utcoffset() != timedelta(0):
        raise MRMSPilotError(f"timestamp is not UTC: {value}")
    return parsed.astimezone(UTC)


def select_temporal_windows(
    rows: Iterable[dict[str, Any]],
    profile: MRMSPilotProfile,
) -> list[dict[str, Any]]:
    by_year: dict[int, list[tuple[datetime, dict[str, Any]]]] = {
        year: [] for year in profile.years
    }
    for row in rows:
        if row.get("split") != "training":
            continue
        issue_time = _parse_utc(str(row["issue_time"]))
        if issue_time.year not in by_year:
            continue
        if int(row.get("asset_count", -1)) != profile.training_profile.total_frames:
            raise MRMSPilotError("pilot window does not contain 29 assets")
        by_year[issue_time.year].append((issue_time, dict(row)))

    selected: list[dict[str, Any]] = []
    minimum_delta = timedelta(minutes=profile.minimum_separation_minutes)
    for year in profile.years:
        candidates = sorted(by_year[year], key=lambda value: value[0])
        generator = np.random.default_rng(profile.random_seed + year)
        order = generator.permutation(len(candidates))
        chosen: list[tuple[datetime, dict[str, Any]]] = []
        for index in order:
            candidate = candidates[int(index)]
            if all(abs(candidate[0] - existing[0]) >= minimum_delta for existing in chosen):
                chosen.append(candidate)
            if len(chosen) == profile.windows_per_year:
                break
        if len(chosen) != profile.windows_per_year:
            raise MRMSPilotError(f"insufficient separated training windows for {year}")
        selected.extend(row for _, row in chosen)

    generator = np.random.default_rng(profile.random_seed)
    order = generator.permutation(len(selected))
    return [selected[int(index)] for index in order]


def _origin_axis(length: int, crop_size: int, stride: int) -> np.ndarray:
    maximum = length - crop_size
    if maximum < 0:
        raise MRMSPilotError("crop is larger than the native grid")
    values = list(range(0, maximum + 1, stride))
    if values[-1] != maximum:
        values.append(maximum)
    return np.asarray(values, dtype="int32")


def _patch_sums(values: np.ndarray, y: np.ndarray, x: np.ndarray, size: int) -> np.ndarray:
    integral = np.zeros((values.shape[0] + 1, values.shape[1] + 1), dtype=values.dtype)
    np.cumsum(values, axis=0, dtype=values.dtype, out=integral[1:, 1:])
    np.cumsum(integral[1:, 1:], axis=1, dtype=values.dtype, out=integral[1:, 1:])
    return (
        integral[y + size, x + size]
        - integral[y, x + size]
        - integral[y + size, x]
        + integral[y, x]
    )


def _remove_nearby(
    available: np.ndarray,
    y: np.ndarray,
    x: np.ndarray,
    selected_index: int,
    minimum_separation: int,
) -> None:
    if minimum_separation <= 0:
        available[selected_index] = False
        return
    available &= (
        (np.abs(y - y[selected_index]) >= minimum_separation)
        | (np.abs(x - x[selected_index]) >= minimum_separation)
    )


def select_spatial_crops(
    aggregate_score: np.ndarray,
    common_valid: np.ndarray,
    profile: MRMSPilotProfile,
    *,
    seed: int,
) -> list[dict[str, Any]]:
    if aggregate_score.shape != common_valid.shape:
        raise MRMSPilotError("pilot score and valid mask shapes differ")
    if aggregate_score.shape != (profile.latitude_count, profile.longitude_count):
        raise MRMSPilotError("pilot score shape differs from the frozen native grid")

    y_axis = _origin_axis(
        profile.latitude_count, profile.crop_size, profile.candidate_stride_cells
    )
    x_axis = _origin_axis(
        profile.longitude_count, profile.crop_size, profile.candidate_stride_cells
    )
    y, x = np.meshgrid(y_axis, x_axis, indexing="ij")
    y = y.ravel()
    x = x.ravel()
    invalid = (~common_valid).astype("int32")
    invalid_counts = _patch_sums(invalid, y, x, profile.crop_size)
    scores = _patch_sums(aggregate_score.astype("float64"), y, x, profile.crop_size)
    eligible = invalid_counts == 0
    if int(np.count_nonzero(eligible)) < profile.crops_per_window:
        raise MRMSPilotError("not enough all-valid spatial crops for pilot window")

    generator = np.random.default_rng(seed)
    available = eligible.copy()
    selected: list[dict[str, Any]] = []
    for _ in range(profile.importance_crops_per_window):
        indices = np.flatnonzero(available)
        if not len(indices):
            raise MRMSPilotError("spatial separation exhausted importance candidates")
        weights = scores[indices] + profile.importance_epsilon
        if np.any(~np.isfinite(weights)) or np.any(weights <= 0):
            raise MRMSPilotError("importance weights are not finite and positive")
        chosen = int(generator.choice(indices, p=weights / weights.sum()))
        selected.append(
            {
                "branch": "importance",
                "importance_score": float(scores[chosen]),
                "x_start": int(x[chosen]),
                "y_start": int(y[chosen]),
            }
        )
        _remove_nearby(
            available,
            y,
            x,
            chosen,
            profile.minimum_origin_separation_cells,
        )

    for _ in range(profile.uniform_crops_per_window):
        indices = np.flatnonzero(available)
        if not len(indices):
            raise MRMSPilotError("spatial separation exhausted uniform candidates")
        chosen = int(generator.choice(indices))
        selected.append(
            {
                "branch": "uniform",
                "importance_score": float(scores[chosen]),
                "x_start": int(x[chosen]),
                "y_start": int(y[chosen]),
            }
        )
        _remove_nearby(
            available,
            y,
            x,
            chosen,
            profile.minimum_origin_separation_cells,
        )
    return selected


def crop_coordinates(
    profile: MRMSPilotProfile,
    *,
    y_start: int,
    x_start: int,
) -> dict[str, float]:
    resolution = profile.resolution_deg
    return {
        "west_edge_deg": round(profile.west_edge_deg + x_start * resolution, 10),
        "east_edge_deg": round(
            profile.west_edge_deg + (x_start + profile.crop_size) * resolution, 10
        ),
        "south_edge_deg": round(profile.south_edge_deg + y_start * resolution, 10),
        "north_edge_deg": round(
            profile.south_edge_deg + (y_start + profile.crop_size) * resolution, 10
        ),
    }


def _tree_sha256(root: Path, *, excluded_names: frozenset[str] = frozenset()) -> str:
    digest = hashlib.sha256()
    for path in sorted(value for value in root.rglob("*") if value.is_file()):
        relative = path.relative_to(root).as_posix()
        if relative in excluded_names:
            continue
        digest.update(relative.encode())
        digest.update(b"\0")
        with path.open("rb") as handle:
            for chunk in iter(lambda: handle.read(8 * 1024 * 1024), b""):
                digest.update(chunk)
    return digest.hexdigest()


def _tree_size(root: Path) -> int:
    return sum(path.stat().st_size for path in root.rglob("*") if path.is_file())


def write_zarr_shard(
    output_path: Path,
    *,
    rain_rate: np.ndarray,
    valid_mask: np.ndarray,
    samples: Sequence[dict[str, Any]],
    attributes: dict[str, Any],
    profile: MRMSPilotProfile,
) -> dict[str, Any]:
    expected_shape = (
        profile.samples_per_shard,
        profile.training_profile.total_frames,
        profile.crop_size,
        profile.crop_size,
    )
    if rain_rate.shape != expected_shape or valid_mask.shape != expected_shape:
        raise MRMSPilotError("pilot shard shape differs from profile")
    if rain_rate.dtype != np.dtype("float16") or valid_mask.dtype != np.dtype("uint8"):
        raise MRMSPilotError("pilot shard dtype differs from profile")
    if np.any(valid_mask != 1) or np.any(~np.isfinite(rain_rate)):
        raise MRMSPilotError("pilot shard contains missing values")
    if np.any(rain_rate < 0) or np.any(rain_rate > profile.rain_rate_cap_mm_h):
        raise MRMSPilotError("pilot shard rain rate is outside the frozen range")
    if len(samples) != profile.samples_per_shard:
        raise MRMSPilotError("pilot shard sample metadata count differs")
    logical_bytes = rain_rate.nbytes + valid_mask.nbytes
    if logical_bytes > profile.maximum_logical_output_bytes:
        raise MRMSPilotError("pilot shard exceeds the logical output cap")
    if output_path.exists():
        raise MRMSPilotError(f"pilot shard already exists: {output_path}")

    output_path.parent.mkdir(parents=True, exist_ok=True)
    temporary = Path(
        tempfile.mkdtemp(prefix=f".{output_path.name}.tmp-", dir=output_path.parent)
    )
    try:
        group = zarr.open_group(str(temporary), mode="w")
        compressor = Blosc(
            cname="zstd",
            clevel=profile.compression_level,
            shuffle=Blosc.BITSHUFFLE,
        )
        chunks = (1, profile.training_profile.total_frames, profile.crop_size, profile.crop_size)
        group.create_dataset(
            "rain_rate",
            data=rain_rate,
            chunks=chunks,
            compressor=compressor,
            overwrite=False,
        )
        group.create_dataset(
            "valid_mask",
            data=valid_mask,
            chunks=chunks,
            compressor=compressor,
            overwrite=False,
        )
        group.attrs.update(
            {
                **attributes,
                "schema_version": "rainpulse.nowcastnet-mrms-pilot-shard/1.0",
                "pilot_version": profile.pilot_version,
                "pilot_profile_sha256": profile.profile_sha256,
                "training_profile_sha256": profile.training_profile.profile_sha256,
                "unit": "mm/h",
                "missing_value_policy": "reject_any_missing",
                "latitude_order": "ascending",
                "operational_eligible": False,
            }
        )
        sample_path = temporary / "samples.jsonl"
        with sample_path.open("w", encoding="utf-8") as handle:
            for sample in samples:
                handle.write(
                    json.dumps(
                        sample,
                        ensure_ascii=False,
                        sort_keys=True,
                        separators=(",", ":"),
                    )
                    + "\n"
                )
        content_sha256 = _tree_sha256(temporary)
        report = {
            "schema_version": "1.0",
            "sample_count": len(samples),
            "logical_bytes": logical_bytes,
            "content_sha256": content_sha256,
            "created_at": datetime.now(UTC).isoformat(),
            "all_samples_valid": True,
            "operational_eligible": False,
            "stored_bytes": _tree_size(temporary),
        }
        (temporary / "shard-report.json").write_text(
            json.dumps(report, ensure_ascii=False, indent=2, sort_keys=True) + "\n",
            encoding="utf-8",
        )
        os.replace(temporary, output_path)
        return report
    except Exception:
        shutil.rmtree(temporary, ignore_errors=True)
        raise


def _write_json_atomic(path: Path, value: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_name(f".{path.name}.tmp-{os.getpid()}")
    temporary.write_text(
        json.dumps(value, ensure_ascii=False, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )
    os.replace(temporary, path)


def create_pilot_plan(
    profile: MRMSPilotProfile,
    *,
    audit_root: Path,
    output_path: Path,
) -> dict[str, Any]:
    inventory_path = audit_root / "asset-inventory.jsonl"
    window_path = audit_root / "window-index.jsonl"
    if output_path.exists():
        raise MRMSPilotError(f"pilot plan already exists: {output_path}")
    if sha256_file(inventory_path) != profile.asset_inventory_sha256:
        raise MRMSPilotError("pilot asset-inventory SHA-256 differs")
    if sha256_file(window_path) != profile.window_index_sha256:
        raise MRMSPilotError("pilot window-index SHA-256 differs")
    with window_path.open(encoding="utf-8") as handle:
        selected = select_temporal_windows((json.loads(line) for line in handle), profile)
    if len(selected) != profile.shard_count:
        raise MRMSPilotError("pilot selected temporal-window count differs")
    selected = [
        {**row, "shard_index": shard_index}
        for shard_index, row in enumerate(selected)
    ]
    identity_payload = {
        "pilot_profile_sha256": profile.profile_sha256,
        "asset_inventory_sha256": profile.asset_inventory_sha256,
        "window_index_sha256": profile.window_index_sha256,
        "selected_windows": selected,
    }
    plan_id = hashlib.sha256(
        json.dumps(identity_payload, sort_keys=True, separators=(",", ":")).encode()
    ).hexdigest()
    plan = {
        "schema_version": "1.0",
        "plan_id": plan_id,
        "created_at": datetime.now(UTC).isoformat(),
        "pilot_version": profile.pilot_version,
        "pilot_profile_sha256": profile.profile_sha256,
        "training_profile_sha256": profile.training_profile.profile_sha256,
        "asset_inventory_sha256": profile.asset_inventory_sha256,
        "window_index_sha256": profile.window_index_sha256,
        "random_seed": profile.random_seed,
        "selected_window_count": len(selected),
        "planned_sample_count": len(selected) * profile.crops_per_window,
        "selected_windows": selected,
        "holdout_windows_selected": 0,
        "operational_eligible": False,
    }
    _write_json_atomic(output_path, plan)
    return plan


def load_pilot_plan(path: Path, profile: MRMSPilotProfile) -> dict[str, Any]:
    try:
        plan = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise MRMSPilotError(f"cannot load pilot plan: {exc}") from exc
    selected = plan.get("selected_windows")
    if not isinstance(selected, list):
        raise MRMSPilotError("pilot plan selected windows are invalid")
    identity_payload = {
        "pilot_profile_sha256": profile.profile_sha256,
        "asset_inventory_sha256": profile.asset_inventory_sha256,
        "window_index_sha256": profile.window_index_sha256,
        "selected_windows": selected,
    }
    expected_plan_id = hashlib.sha256(
        json.dumps(identity_payload, sort_keys=True, separators=(",", ":")).encode()
    ).hexdigest()
    if (
        plan.get("schema_version") != "1.0"
        or plan.get("plan_id") != expected_plan_id
        or plan.get("pilot_profile_sha256") != profile.profile_sha256
        or plan.get("training_profile_sha256")
        != profile.training_profile.profile_sha256
        or plan.get("asset_inventory_sha256") != profile.asset_inventory_sha256
        or plan.get("window_index_sha256") != profile.window_index_sha256
        or int(plan.get("selected_window_count", -1)) != profile.shard_count
        or int(plan.get("planned_sample_count", -1)) != profile.sample_count
        or int(plan.get("holdout_windows_selected", -1)) != 0
        or any(row.get("split") != "training" for row in selected)
    ):
        raise MRMSPilotError("pilot plan identity or leakage boundary differs")
    return plan


def _load_asset_inventory(path: Path, profile: MRMSPilotProfile) -> list[dict[str, Any]]:
    if sha256_file(path) != profile.asset_inventory_sha256:
        raise MRMSPilotError("pilot asset inventory changed before preprocessing")
    assets: list[dict[str, Any]] = []
    with path.open(encoding="utf-8") as handle:
        for line in handle:
            row = json.loads(line)
            if int(row.get("asset_index", -1)) != len(assets):
                raise MRMSPilotError("pilot asset inventory indices are not contiguous")
            assets.append(row)
    return assets


def _safe_dataset_path(dataset_root: Path, relative_value: Any) -> Path:
    relative = Path(str(relative_value))
    if relative.is_absolute() or ".." in relative.parts:
        raise MRMSPilotError("pilot asset path is unsafe")
    root = dataset_root.resolve()
    target = (root / relative).resolve()
    if not target.is_relative_to(root):
        raise MRMSPilotError("pilot asset escapes the dataset root")
    return target


def _validate_native_frame(
    frame: MRMSNativePrecipFrame,
    profile: MRMSPilotProfile,
) -> None:
    expected_shape = (profile.latitude_count, profile.longitude_count)
    if frame.rate_mm_h.shape != expected_shape or frame.valid_mask.shape != expected_shape:
        raise MRMSPilotError("native MRMS frame shape differs from pilot profile")
    if (
        not np.isclose(frame.longitude_interval_deg, profile.resolution_deg, atol=1e-6)
        or not np.isclose(frame.latitude_interval_deg, profile.resolution_deg, atol=1e-6)
        or not np.isclose(
            frame.longitudes[0],
            profile.west_edge_deg + profile.resolution_deg / 2,
            atol=1e-5,
        )
        or not np.isclose(
            frame.longitudes[-1],
            profile.east_edge_deg - profile.resolution_deg / 2,
            atol=1e-5,
        )
        or not np.isclose(
            frame.latitudes[0],
            profile.south_edge_deg + profile.resolution_deg / 2,
            atol=1e-5,
        )
        or not np.isclose(
            frame.latitudes[-1],
            profile.north_edge_deg - profile.resolution_deg / 2,
            atol=1e-5,
        )
        or np.any(np.diff(frame.longitudes) <= 0)
        or np.any(np.diff(frame.latitudes) <= 0)
    ):
        raise MRMSPilotError("native MRMS coordinates differ from the pilot grid")


def _window_seed(profile: MRMSPilotProfile, window_id: str) -> int:
    digest = hashlib.sha256(f"{profile.random_seed}:{window_id}".encode()).digest()
    return int.from_bytes(digest[:8], "big", signed=False)


def build_window_shard_arrays(
    profile: MRMSPilotProfile,
    *,
    dataset_root: Path,
    asset_rows: Sequence[dict[str, Any]],
    window: dict[str, Any],
    reader=read_mrms_native_precip_frame,
) -> tuple[np.ndarray, np.ndarray, list[dict[str, Any]], dict[str, Any]]:
    if len(asset_rows) != profile.training_profile.total_frames:
        raise MRMSPilotError("pilot temporal window does not resolve to 29 assets")
    if window.get("split") != "training":
        raise MRMSPilotError("pilot preprocessing refuses non-training windows")

    stack: np.ndarray | None = None
    common_valid: np.ndarray | None = None
    aggregate_score: np.ndarray | None = None
    clipped_full_domain_pixel_count = 0
    expected_step = timedelta(minutes=profile.training_profile.cadence_minutes)
    previous_time: datetime | None = None
    source_times: list[str] = []
    for frame_index, asset in enumerate(asset_rows):
        if asset.get("split") != "training":
            raise MRMSPilotError("pilot asset is outside the training split")
        expected_time = _parse_utc(str(asset["valid_time"]))
        if previous_time is not None and expected_time - previous_time != expected_step:
            raise MRMSPilotError("pilot source assets are not cadence continuous")
        previous_time = expected_time
        source_times.append(expected_time.isoformat().replace("+00:00", "Z"))
        path = _safe_dataset_path(dataset_root, asset["relative_path"])
        frame = reader(path)
        if frame.valid_time != expected_time:
            raise MRMSPilotError("pilot decoded time differs from asset inventory")
        _validate_native_frame(frame, profile)
        if stack is None:
            stack = np.empty(
                (
                    profile.training_profile.total_frames,
                    profile.latitude_count,
                    profile.longitude_count,
                ),
                dtype="float16",
            )
            common_valid = np.ones(
                (profile.latitude_count, profile.longitude_count), dtype=bool
            )
            aggregate_score = np.zeros(
                (profile.latitude_count, profile.longitude_count), dtype="float32"
            )
        valid = frame.valid_mask == 1
        common_valid &= valid
        clipped_full_domain_pixel_count += int(
            np.count_nonzero(valid & (frame.rate_mm_h > profile.rain_rate_cap_mm_h))
        )
        capped = np.where(
            valid,
            np.clip(frame.rate_mm_h, 0.0, profile.rain_rate_cap_mm_h),
            np.nan,
        ).astype("float32")
        stack[frame_index] = capped.astype("float16")
        aggregate_score += np.where(valid, 1.0 - np.exp(-capped), 0.0).astype("float32")

    if stack is None or common_valid is None or aggregate_score is None:
        raise MRMSPilotError("pilot temporal window has no decoded frames")
    crops = select_spatial_crops(
        aggregate_score,
        common_valid,
        profile,
        seed=_window_seed(profile, str(window["window_id"])),
    )
    output = np.empty(
        (
            profile.crops_per_window,
            profile.training_profile.total_frames,
            profile.crop_size,
            profile.crop_size,
        ),
        dtype="float16",
    )
    valid_output = np.ones(output.shape, dtype="uint8")
    samples: list[dict[str, Any]] = []
    for sample_index, crop in enumerate(crops):
        y_start = int(crop["y_start"])
        x_start = int(crop["x_start"])
        crop_values = stack[
            :,
            y_start : y_start + profile.crop_size,
            x_start : x_start + profile.crop_size,
        ]
        if np.any(~np.isfinite(crop_values)):
            raise MRMSPilotError("selected pilot crop contains missing values")
        output[sample_index] = crop_values
        sample_identity = {
            "window_id": window["window_id"],
            "branch": crop["branch"],
            "x_start": x_start,
            "y_start": y_start,
        }
        sample_id = hashlib.sha256(
            json.dumps(sample_identity, sort_keys=True, separators=(",", ":")).encode()
        ).hexdigest()
        samples.append(
            {
                "sample_id": sample_id,
                "sample_index_in_shard": sample_index,
                "branch": crop["branch"],
                "importance_score": crop["importance_score"],
                "x_start": x_start,
                "y_start": y_start,
                "coordinates": crop_coordinates(
                    profile,
                    y_start=y_start,
                    x_start=x_start,
                ),
                "rain_rate_mean_mm_h": float(np.mean(crop_values, dtype="float64")),
                "rain_rate_max_mm_h": float(np.max(crop_values)),
                "rain_pixel_fraction": float(np.mean(crop_values > 0.0)),
                "all_valid": True,
                "window_id": window["window_id"],
            }
        )
    diagnostics = {
        "source_times": source_times,
        "clipped_full_domain_pixel_count": clipped_full_domain_pixel_count,
        "common_valid_fraction": float(np.mean(common_valid)),
    }
    return output, valid_output, samples, diagnostics


def _process_window_shard(
    profile: MRMSPilotProfile,
    dataset_root: Path,
    asset_rows: list[dict[str, Any]],
    window: dict[str, Any],
    output_path: Path,
) -> dict[str, Any]:
    started_at = datetime.now(UTC)
    rain_rate, valid_mask, samples, diagnostics = build_window_shard_arrays(
        profile,
        dataset_root=dataset_root,
        asset_rows=asset_rows,
        window=window,
    )
    report = write_zarr_shard(
        output_path,
        rain_rate=rain_rate,
        valid_mask=valid_mask,
        samples=samples,
        attributes={
            "window_id": window["window_id"],
            "shard_index": int(window["shard_index"]),
            "issue_time": window["issue_time"],
            "first_valid_time": window["first_valid_time"],
            "last_valid_time": window["last_valid_time"],
            "clipped_full_domain_pixel_count": diagnostics[
                "clipped_full_domain_pixel_count"
            ],
            "common_valid_fraction": diagnostics["common_valid_fraction"],
        },
        profile=profile,
    )
    finished_at = datetime.now(UTC)
    report.update(
        {
            "shard_index": int(window["shard_index"]),
            "window_id": window["window_id"],
            "duration_seconds": (finished_at - started_at).total_seconds(),
            "issue_time": window["issue_time"],
        }
    )
    return report


def _load_existing_shard_report(path: Path, window: dict[str, Any]) -> dict[str, Any]:
    try:
        report = json.loads((path / "shard-report.json").read_text(encoding="utf-8"))
        group = zarr.open_group(str(path), mode="r")
    except (OSError, json.JSONDecodeError, KeyError) as exc:
        raise MRMSPilotError(f"cannot resume pilot shard {path}: {exc}") from exc
    if (
        int(report.get("sample_count", -1)) <= 0
        or group.attrs.get("window_id") != window["window_id"]
        or int(group.attrs.get("shard_index", -1)) != int(window["shard_index"])
    ):
        raise MRMSPilotError(f"existing pilot shard identity differs: {path}")
    return {**report, "shard_index": int(window["shard_index"]), "resumed": True}


def _aggregate_pilot_output(
    output_root: Path,
    profile: MRMSPilotProfile,
    plan: dict[str, Any],
    windows: Sequence[dict[str, Any]],
    shard_reports: Sequence[dict[str, Any]],
    *,
    complete: bool,
    started_at: datetime,
) -> dict[str, Any]:
    sample_output = output_root / ("samples.jsonl" if complete else "partial-samples.jsonl")
    sample_count = 0
    branch_counts = {"importance": 0, "uniform": 0}
    rain_max = 0.0
    with sample_output.open("w", encoding="utf-8") as target:
        for window in sorted(windows, key=lambda value: int(value["shard_index"])):
            shard_path = output_root / "shards" / f"shard-{int(window['shard_index']):05d}.zarr"
            with (shard_path / "samples.jsonl").open(encoding="utf-8") as source:
                for line in source:
                    sample = json.loads(line)
                    sample["shard_index"] = int(window["shard_index"])
                    target.write(
                        json.dumps(
                            sample,
                            ensure_ascii=False,
                            sort_keys=True,
                            separators=(",", ":"),
                        )
                        + "\n"
                    )
                    sample_count += 1
                    branch_counts[str(sample["branch"])] += 1
                    rain_max = max(rain_max, float(sample["rain_rate_max_mm_h"]))
    finished_at = datetime.now(UTC)
    report = {
        "schema_version": "1.0",
        "status": "complete" if complete else "partial_smoke",
        "pilot_version": profile.pilot_version,
        "pilot_profile_sha256": profile.profile_sha256,
        "training_profile_sha256": profile.training_profile.profile_sha256,
        "plan_id": plan["plan_id"],
        "started_at": started_at.isoformat(),
        "finished_at": finished_at.isoformat(),
        "duration_seconds": (finished_at - started_at).total_seconds(),
        "processed_window_count": len(windows),
        "sample_count": sample_count,
        "branch_counts": branch_counts,
        "maximum_rain_rate_mm_h": rain_max,
        "all_samples_valid": True,
        "holdout_windows_processed": 0,
        "logical_bytes": sum(int(report["logical_bytes"]) for report in shard_reports),
        "stored_bytes": sum(int(report.get("stored_bytes", 0)) for report in shard_reports),
        "sample_index_sha256": sha256_file(sample_output),
        "operational_eligible": False,
    }
    report_name = "pilot-report.json" if complete else "partial-pilot-report.json"
    _write_json_atomic(output_root / report_name, report)
    if complete:
        (output_root / "COMPLETED").write_text(report["sample_index_sha256"] + "\n")
    return report


def run_pilot(
    profile: MRMSPilotProfile,
    *,
    plan_path: Path,
    audit_root: Path,
    dataset_root: Path,
    output_root: Path,
    workers: int,
    max_windows: int | None = None,
) -> dict[str, Any]:
    if workers < 1 or workers > 4:
        raise MRMSPilotError("pilot workers must be between 1 and 4")
    plan = load_pilot_plan(plan_path, profile)
    selected = list(plan["selected_windows"])
    if max_windows is not None:
        if max_windows < 1 or max_windows > len(selected):
            raise MRMSPilotError("pilot max-windows is outside the plan")
        selected = selected[:max_windows]
    complete = len(selected) == profile.shard_count
    logical_bytes_per_sample = (
        profile.training_profile.total_frames
        * profile.crop_size
        * profile.crop_size
        * (np.dtype("float16").itemsize + np.dtype("uint8").itemsize)
    )
    planned_logical_bytes = len(selected) * profile.crops_per_window * logical_bytes_per_sample
    if planned_logical_bytes > profile.maximum_logical_output_bytes:
        raise MRMSPilotError("pilot planned logical bytes exceed the output cap")

    if not output_root.exists():
        output_root.parent.mkdir(parents=True, exist_ok=True)
        free_bytes = shutil.disk_usage(output_root.parent).free
        if free_bytes < profile.minimum_free_bytes:
            raise MRMSPilotError(
                f"pilot free-space gate failed: required={profile.minimum_free_bytes} "
                f"actual={free_bytes}"
            )
        output_root.mkdir()
        _write_json_atomic(
            output_root / "run-state.json",
            {
                "schema_version": "1.0",
                "pilot_profile_sha256": profile.profile_sha256,
                "plan_id": plan["plan_id"],
                "status": "running",
                "created_at": datetime.now(UTC).isoformat(),
                "operational_eligible": False,
            },
        )
    else:
        try:
            state = json.loads((output_root / "run-state.json").read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError) as exc:
            raise MRMSPilotError(f"cannot resume pilot output: {exc}") from exc
        if (
            state.get("pilot_profile_sha256") != profile.profile_sha256
            or state.get("plan_id") != plan["plan_id"]
        ):
            raise MRMSPilotError("pilot resume identity differs")
        if (output_root / "COMPLETED").exists():
            raise MRMSPilotError("pilot output is already complete")

    assets = _load_asset_inventory(audit_root / "asset-inventory.jsonl", profile)
    shard_root = output_root / "shards"
    shard_root.mkdir(exist_ok=True)
    started_at = datetime.now(UTC)
    reports: list[dict[str, Any]] = []
    pending: list[tuple[dict[str, Any], list[dict[str, Any]], Path]] = []
    for window in selected:
        start_index = int(window["asset_start_index"])
        end_index = int(window["asset_end_index"])
        asset_rows = assets[start_index : end_index + 1]
        target = shard_root / f"shard-{int(window['shard_index']):05d}.zarr"
        if target.exists():
            reports.append(_load_existing_shard_report(target, window))
        else:
            pending.append((window, asset_rows, target))

    if pending:
        with ProcessPoolExecutor(max_workers=workers) as executor:
            futures = {
                executor.submit(
                    _process_window_shard,
                    profile,
                    dataset_root,
                    asset_rows,
                    window,
                    target,
                ): window
                for window, asset_rows, target in pending
            }
            completed = len(reports)
            for future in as_completed(futures):
                window = futures[future]
                try:
                    report = future.result()
                except Exception as exc:  # noqa: BLE001 - worker error normalized at CLI seam
                    raise MRMSPilotError(
                        f"pilot shard failed for {window['window_id']}: {exc}"
                    ) from exc
                reports.append(report)
                completed += 1
                print(
                    f"pilot shard {completed}/{len(selected)} "
                    f"index={window['shard_index']} duration={report['duration_seconds']:.2f}s",
                    flush=True,
                )
    if len(reports) != len(selected):
        raise MRMSPilotError("pilot shard count does not close")
    report = _aggregate_pilot_output(
        output_root,
        profile,
        plan,
        selected,
        reports,
        complete=complete,
        started_at=started_at,
    )
    _write_json_atomic(
        output_root / "run-state.json",
        {
            "schema_version": "1.0",
            "pilot_profile_sha256": profile.profile_sha256,
            "plan_id": plan["plan_id"],
            "status": report["status"],
            "updated_at": datetime.now(UTC).isoformat(),
            "operational_eligible": False,
        },
    )
    return report


def _load_json_object(path: Path, description: str) -> dict[str, Any]:
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise MRMSPilotError(f"cannot load {description} {path}: {exc}") from exc
    if not isinstance(value, dict):
        raise MRMSPilotError(f"{description} is not an object: {path}")
    return value


def _validate_completed_shard(
    path: Path,
    *,
    window: dict[str, Any],
    profile: MRMSPilotProfile,
    verify_content_hash: bool,
) -> tuple[dict[str, Any], list[dict[str, Any]]]:
    report = _load_json_object(path / "shard-report.json", "pilot shard report")
    try:
        group = zarr.open_group(str(path), mode="r")
        rain = group["rain_rate"]
        mask = group["valid_mask"]
    except (OSError, KeyError, ValueError) as exc:
        raise MRMSPilotError(f"cannot open pilot shard {path}: {exc}") from exc
    expected_shape = (
        profile.samples_per_shard,
        profile.training_profile.total_frames,
        profile.crop_size,
        profile.crop_size,
    )
    expected_chunks = (
        1,
        profile.training_profile.total_frames,
        profile.crop_size,
        profile.crop_size,
    )
    if (
        rain.shape != expected_shape
        or mask.shape != expected_shape
        or rain.dtype != np.dtype("float16")
        or mask.dtype != np.dtype("uint8")
        or rain.chunks != expected_chunks
        or mask.chunks != expected_chunks
        or group.attrs.get("pilot_profile_sha256") != profile.profile_sha256
        or group.attrs.get("training_profile_sha256")
        != profile.training_profile.profile_sha256
        or group.attrs.get("window_id") != window["window_id"]
        or int(group.attrs.get("shard_index", -1)) != int(window["shard_index"])
        or group.attrs.get("missing_value_policy") != "reject_any_missing"
        or group.attrs.get("operational_eligible") is not False
        or int(report.get("sample_count", -1)) != profile.samples_per_shard
        or report.get("all_samples_valid") is not True
        or report.get("operational_eligible") is not False
    ):
        raise MRMSPilotError(f"pilot shard contract differs: {path}")
    expected_logical_bytes = (
        int(np.prod(expected_shape))
        * (np.dtype("float16").itemsize + np.dtype("uint8").itemsize)
    )
    if int(report.get("logical_bytes", -1)) != expected_logical_bytes:
        raise MRMSPilotError(f"pilot shard logical byte count differs: {path}")
    if verify_content_hash and _tree_sha256(
        path, excluded_names=frozenset({"shard-report.json"})
    ) != str(report.get("content_sha256")):
        raise MRMSPilotError(f"pilot shard content SHA-256 differs: {path}")

    sample_path = path / "samples.jsonl"
    try:
        samples = [json.loads(line) for line in sample_path.read_text().splitlines()]
    except (OSError, json.JSONDecodeError) as exc:
        raise MRMSPilotError(f"cannot load pilot shard samples {path}: {exc}") from exc
    if len(samples) != profile.samples_per_shard:
        raise MRMSPilotError(f"pilot shard sample count differs: {path}")
    branch_counts = {"importance": 0, "uniform": 0}
    sample_ids: set[str] = set()
    for index, sample in enumerate(samples):
        branch = str(sample.get("branch"))
        coordinates = sample.get("coordinates")
        if branch not in branch_counts or not isinstance(coordinates, dict):
            raise MRMSPilotError(f"pilot shard sample metadata differs: {path}")
        x_start = int(sample.get("x_start", -1))
        y_start = int(sample.get("y_start", -1))
        sample_id = str(sample.get("sample_id", ""))
        if (
            int(sample.get("sample_index_in_shard", -1)) != index
            or sample.get("window_id") != window["window_id"]
            or sample.get("all_valid") is not True
            or x_start < 0
            or y_start < 0
            or x_start + profile.crop_size > profile.longitude_count
            or y_start + profile.crop_size > profile.latitude_count
            or coordinates
            != crop_coordinates(profile, y_start=y_start, x_start=x_start)
            or not sample_id
            or sample_id in sample_ids
            or not np.isfinite(float(sample.get("rain_rate_mean_mm_h", np.nan)))
            or not 0.0
            <= float(sample.get("rain_rate_max_mm_h", np.nan))
            <= profile.rain_rate_cap_mm_h
        ):
            raise MRMSPilotError(f"pilot shard sample contract differs: {path}")
        branch_counts[branch] += 1
        sample_ids.add(sample_id)
    if branch_counts != {
        "importance": profile.importance_crops_per_window,
        "uniform": profile.uniform_crops_per_window,
    }:
        raise MRMSPilotError(f"pilot shard branch counts differ: {path}")
    return report, samples


def validate_pilot_output(
    profile: MRMSPilotProfile,
    *,
    plan: dict[str, Any],
    output_root: Path,
    random_sample_count: int = 64,
    verify_content_hash: bool = True,
) -> dict[str, Any]:
    if random_sample_count < 1 or random_sample_count > profile.sample_count:
        raise MRMSPilotError("pilot validation random-samples is outside the dataset")
    if not (output_root / "COMPLETED").is_file():
        raise MRMSPilotError("pilot output does not have a COMPLETED marker")
    report = _load_json_object(output_root / "pilot-report.json", "pilot report")
    sample_index = output_root / "samples.jsonl"
    sample_index_sha256 = sha256_file(sample_index)
    marker = (output_root / "COMPLETED").read_text(encoding="utf-8").strip()
    if (
        report.get("status") != "complete"
        or report.get("pilot_profile_sha256") != profile.profile_sha256
        or report.get("training_profile_sha256")
        != profile.training_profile.profile_sha256
        or report.get("plan_id") != plan["plan_id"]
        or int(report.get("processed_window_count", -1)) != profile.shard_count
        or int(report.get("sample_count", -1)) != profile.sample_count
        or report.get("all_samples_valid") is not True
        or int(report.get("holdout_windows_processed", -1)) != 0
        or report.get("operational_eligible") is not False
        or report.get("sample_index_sha256") != sample_index_sha256
        or marker != sample_index_sha256
    ):
        raise MRMSPilotError("pilot completion report or marker differs")

    windows = sorted(
        plan["selected_windows"], key=lambda value: int(value["shard_index"])
    )
    expected_names = {
        f"shard-{index:05d}.zarr" for index in range(profile.shard_count)
    }
    shard_root = output_root / "shards"
    actual_names = {path.name for path in shard_root.glob("shard-*.zarr") if path.is_dir()}
    if actual_names != expected_names:
        raise MRMSPilotError("pilot output shard set differs from the frozen plan")

    shard_reports: list[dict[str, Any]] = []
    indexed_samples: list[dict[str, Any]] = []
    all_sample_ids: set[str] = set()
    for window in windows:
        index = int(window["shard_index"])
        shard_path = shard_root / f"shard-{index:05d}.zarr"
        shard_report, samples = _validate_completed_shard(
            shard_path,
            window=window,
            profile=profile,
            verify_content_hash=verify_content_hash,
        )
        shard_reports.append(shard_report)
        for sample in samples:
            sample_id = str(sample["sample_id"])
            if sample_id in all_sample_ids:
                raise MRMSPilotError("pilot sample IDs are not globally unique")
            all_sample_ids.add(sample_id)
            indexed_samples.append({**sample, "shard_index": index})
    if len(indexed_samples) != profile.sample_count:
        raise MRMSPilotError("pilot validated sample count differs")

    expected_index_lines = [
        json.dumps(sample, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
        for sample in indexed_samples
    ]
    if sample_index.read_text(encoding="utf-8").splitlines() != expected_index_lines:
        raise MRMSPilotError("pilot root sample index differs from shard metadata")
    logical_bytes = sum(int(value["logical_bytes"]) for value in shard_reports)
    stored_bytes = sum(int(value["stored_bytes"]) for value in shard_reports)
    if (
        int(report.get("logical_bytes", -1)) != logical_bytes
        or int(report.get("stored_bytes", -1)) != stored_bytes
        or logical_bytes > profile.maximum_logical_output_bytes
    ):
        raise MRMSPilotError("pilot aggregate byte counts differ")

    generator = np.random.default_rng(profile.random_seed)
    selected_indices = sorted(
        int(value)
        for value in generator.choice(
            profile.sample_count,
            size=random_sample_count,
            replace=False,
        )
    )
    opened: dict[int, zarr.Group] = {}
    logical_read_bytes = 0
    sampled_rain_min = float("inf")
    sampled_rain_max = 0.0
    started = time.perf_counter()
    for sample_index_value in selected_indices:
        shard_index, offset = divmod(sample_index_value, profile.samples_per_shard)
        group = opened.get(shard_index)
        if group is None:
            group = zarr.open_group(
                str(shard_root / f"shard-{shard_index:05d}.zarr"), mode="r"
            )
            opened[shard_index] = group
        rain = np.asarray(group["rain_rate"][offset], dtype="float16")
        mask = np.asarray(group["valid_mask"][offset], dtype="uint8")
        if (
            rain.shape
            != (
                profile.training_profile.total_frames,
                profile.crop_size,
                profile.crop_size,
            )
            or mask.shape != rain.shape
            or np.any(~np.isfinite(rain))
            or np.any(mask != 1)
            or np.any(rain < 0)
            or np.any(rain > profile.rain_rate_cap_mm_h)
            or rain[: profile.training_profile.input_frames].shape[0]
            != profile.training_profile.input_frames
            or rain[profile.training_profile.input_frames :].shape[0]
            != profile.training_profile.target_frames
        ):
            raise MRMSPilotError("pilot random sample readback differs")
        sampled_rain_min = min(sampled_rain_min, float(np.min(rain)))
        sampled_rain_max = max(sampled_rain_max, float(np.max(rain)))
        logical_read_bytes += rain.nbytes + mask.nbytes
    elapsed = max(time.perf_counter() - started, np.finfo("float64").eps)
    validation = {
        "schema_version": "1.0",
        "status": "passed",
        "validated_at": datetime.now(UTC).isoformat(),
        "pilot_version": profile.pilot_version,
        "pilot_profile_sha256": profile.profile_sha256,
        "training_profile_sha256": profile.training_profile.profile_sha256,
        "plan_id": plan["plan_id"],
        "shard_count": profile.shard_count,
        "sample_count": profile.sample_count,
        "branch_counts": report["branch_counts"],
        "all_samples_valid": True,
        "holdout_windows_processed": 0,
        "logical_bytes": logical_bytes,
        "stored_bytes": stored_bytes,
        "compression_ratio": logical_bytes / max(stored_bytes, 1),
        "content_hash_verified": verify_content_hash,
        "random_read_sample_count": random_sample_count,
        "random_read_logical_bytes": logical_read_bytes,
        "random_read_duration_seconds": elapsed,
        "random_read_logical_mib_s": logical_read_bytes / elapsed / (1024 * 1024),
        "sampled_rain_min_mm_h": sampled_rain_min,
        "sampled_rain_max_mm_h": sampled_rain_max,
        "sample_index_sha256": sample_index_sha256,
        "operational_eligible": False,
    }
    _write_json_atomic(output_root / "validation-report.json", validation)
    return validation


def _build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Prepare the frozen MRMS NowcastNet pilot")
    subparsers = parser.add_subparsers(dest="command", required=True)
    for command in ("plan", "run", "validate"):
        child = subparsers.add_parser(command)
        child.add_argument("--repository-root", type=Path, required=True)
        child.add_argument("--pilot-profile", type=Path, required=True)
        if command == "plan":
            child.add_argument("--audit-root", type=Path, required=True)
            child.add_argument("--output", type=Path, required=True)
        elif command == "run":
            child.add_argument("--audit-root", type=Path, required=True)
            child.add_argument("--plan", type=Path, required=True)
            child.add_argument("--dataset-root", type=Path, required=True)
            child.add_argument("--output-root", type=Path, required=True)
            child.add_argument("--workers", type=int, default=1)
            child.add_argument("--max-windows", type=int)
        else:
            child.add_argument("--plan", type=Path, required=True)
            child.add_argument("--output-root", type=Path, required=True)
            child.add_argument("--random-samples", type=int, default=64)
            child.add_argument("--skip-content-hash", action="store_true")
    return parser


def main(argv: list[str] | None = None) -> int:
    args = _build_parser().parse_args(argv)
    try:
        profile = load_mrms_pilot_profile(
            args.pilot_profile,
            repository_root=args.repository_root,
        )
        if args.command == "plan":
            result = create_pilot_plan(
                profile,
                audit_root=args.audit_root,
                output_path=args.output,
            )
        elif args.command == "run":
            result = run_pilot(
                profile,
                plan_path=args.plan,
                audit_root=args.audit_root,
                dataset_root=args.dataset_root,
                output_root=args.output_root,
                workers=args.workers,
                max_windows=args.max_windows,
            )
        else:
            plan = load_pilot_plan(args.plan, profile)
            result = validate_pilot_output(
                profile,
                plan=plan,
                output_root=args.output_root,
                random_sample_count=args.random_samples,
                verify_content_hash=not args.skip_content_hash,
            )
    except MRMSPilotError as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 1
    print(json.dumps(result, ensure_ascii=False, indent=2, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
