from __future__ import annotations

import argparse
import hashlib
import json
import os
import shutil
import subprocess
import sys
import tempfile
import time
from collections.abc import Mapping, Sequence
from concurrent.futures import ProcessPoolExecutor
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

import numpy as np
import yaml
import zarr
from numcodecs import Blosc

from .mrms_archive import sha256_file
from .mrms_pilot import (
    MRMSPilotError,
    _tree_sha256,
    _tree_size,
    _write_json_atomic,
    build_window_shard_arrays,
    crop_coordinates,
    select_temporal_windows,
)
from .mrms_training import MRMSTrainingProfile, load_mrms_training_profile


class MRMSFullSampleError(MRMSPilotError):
    """Raised when the frozen full-sample contract or storage boundary is unsafe."""


@dataclass(frozen=True)
class MRMSFullSampleProfile:
    dataset_version: str
    profile_sha256: str
    training_profile: MRMSTrainingProfile
    training_profile_path: Path
    audit_evidence_path: Path
    audit_evidence_sha256: str
    capacity_evidence_path: Path
    capacity_evidence_sha256: str
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
    raw_root_env: str
    output_root_env: str
    allowed_filesystem_types: tuple[str, ...]
    sample_count: int
    shard_count: int
    samples_per_shard: int
    compression_level: int
    minimum_free_bytes: int
    maximum_logical_output_bytes: int
    maximum_physical_output_bytes: int


def _resolve_local(repository_root: Path, value: str) -> Path:
    relative = Path(value)
    if relative.is_absolute() or ".." in relative.parts:
        raise MRMSFullSampleError("full-sample references must remain repository-local")
    root = repository_root.resolve()
    resolved = (root / relative).resolve()
    if not resolved.is_relative_to(root):
        raise MRMSFullSampleError("full-sample reference escapes the repository")
    return resolved


def _load_json_object(path: Path, description: str) -> dict[str, Any]:
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise MRMSFullSampleError(f"cannot load {description} {path}: {exc}") from exc
    if not isinstance(value, dict):
        raise MRMSFullSampleError(f"{description} is not an object: {path}")
    return value


def load_mrms_full_sample_profile(
    path: Path,
    *,
    repository_root: Path,
) -> MRMSFullSampleProfile:
    try:
        payload = path.read_bytes()
        raw = yaml.safe_load(payload)
        if not isinstance(raw, dict):
            raise MRMSFullSampleError("full-sample profile must be an object")
        training_ref = raw["training_profile"]
        audit_ref = raw["audit_evidence"]
        capacity_ref = raw["capacity_evidence"]
        temporal = raw["temporal_sampling"]
        spatial = raw["spatial_sampling"]
        native = raw["native_grid"]
        storage = raw["storage"]
        output = raw["output"]
    except (KeyError, OSError, TypeError, yaml.YAMLError) as exc:
        raise MRMSFullSampleError(f"cannot load full-sample profile {path}: {exc}") from exc

    training_path = _resolve_local(repository_root, str(training_ref["path"]))
    audit_path = _resolve_local(repository_root, str(audit_ref["path"]))
    capacity_path = _resolve_local(repository_root, str(capacity_ref["path"]))
    if sha256_file(training_path) != str(training_ref["sha256"]):
        raise MRMSFullSampleError("full-sample training-profile SHA-256 differs")
    if sha256_file(audit_path) != str(audit_ref["sha256"]):
        raise MRMSFullSampleError("full-sample audit-evidence SHA-256 differs")
    if sha256_file(capacity_path) != str(capacity_ref["sha256"]):
        raise MRMSFullSampleError("full-sample capacity-evidence SHA-256 differs")

    training_profile = load_mrms_training_profile(training_path)
    audit = _load_json_object(audit_path, "full-sample audit evidence")
    capacity = _load_json_object(capacity_path, "full-sample capacity evidence")
    asset_inventory_sha256 = str(audit_ref["asset_inventory_sha256"])
    window_index_sha256 = str(audit_ref["window_index_sha256"])
    capacity_policy = capacity.get("frozen_capacity_policy", {})
    if (
        audit.get("profile_sha256") != training_profile.profile_sha256
        or audit.get("artifacts", {}).get("asset_inventory", {}).get("sha256")
        != asset_inventory_sha256
        or audit.get("artifacts", {}).get("window_index", {}).get("sha256")
        != window_index_sha256
        or audit.get("integrity", {}).get("transport_integrity") is not True
        or audit.get("splits", {}).get("independent_holdout", {}).get(
            "indexed_window_count"
        )
        != 0
        or audit.get("operational_eligible") is not False
        or capacity.get("status") != "passed"
        or capacity.get("decision", {}).get("capacity_policy_frozen") is not True
        or capacity.get("decision", {}).get("full_sample_materialization_allowed")
        is not True
    ):
        raise MRMSFullSampleError("full-sample evidence differs from the accepted boundary")

    years = tuple(int(value) for value in temporal["years"])
    windows_per_year = int(temporal["windows_per_year"])
    crops_per_window = int(spatial["crops_per_window"])
    importance_crops = int(spatial["importance_crops_per_window"])
    uniform_crops = int(spatial["uniform_crops_per_window"])
    sample_count = int(output["sample_count"])
    shard_count = int(output["shard_count"])
    samples_per_shard = int(output["samples_per_shard"])
    minimum_free_bytes = int(output["minimum_free_bytes"])
    maximum_logical_output_bytes = int(output["maximum_logical_output_bytes"])
    maximum_physical_output_bytes = int(output["maximum_physical_output_bytes"])
    raw_root_env = str(storage["raw_root_env"])
    output_root_env = str(storage["output_root_env"])
    if (
        raw.get("schema_version") != "1.0"
        or raw.get("dataset_version") != "nowcastnet-mrms-full-samples-v1"
        or raw.get("lifecycle") != "offline_training_full_samples"
        or raw.get("operational_eligible") is not False
        or temporal.get("split") != "training"
        or int(temporal["random_seed"]) != 2026083101
        or years != (2019, 2020, 2021, 2022, 2023)
        or windows_per_year != 800
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
        or raw_root_env != "RAINPULSE_MRMS_RAW_ROOT"
        or output_root_env != "RAINPULSE_MRMS_FULL_ROOT"
        or storage.get("output_storage_class") != "external_network_filesystem"
        or tuple(storage["allowed_filesystem_types"]) != ("nfs", "nfs4")
        or storage.get("raw_root_immutable") is not True
        or storage.get("roots_must_not_overlap") is not True
        or output.get("format") != "zarr-v2-window-shards"
        or output.get("rain_rate_dtype") != "float16"
        or output.get("valid_mask_dtype") != "uint8"
        or output.get("compressor") != "blosc-zstd-bitshuffle"
        or sample_count != 100000
        or shard_count != len(years) * windows_per_year
        or samples_per_shard != crops_per_window
        or sample_count != shard_count * samples_per_shard
        or minimum_free_bytes != training_profile.full_sample_minimum_free_bytes
        or minimum_free_bytes
        != int(capacity_policy.get("minimum_free_bytes_before_start", -1))
        or maximum_logical_output_bytes
        != int(capacity_policy.get("maximum_logical_output_bytes", -1))
        or maximum_physical_output_bytes
        != int(capacity_policy.get("maximum_physical_output_bytes", -1))
        or raw_root_env != capacity_policy.get("raw_root_env")
        or output_root_env != capacity_policy.get("output_root_env")
        or capacity_policy.get("local_runtime_filesystem_allowed") is not False
        or capacity_policy.get("raw_root_immutable") is not True
        or capacity_policy.get("holdout_windows_allowed") is not False
    ):
        raise MRMSFullSampleError("full-sample profile differs from frozen v1 invariants")

    return MRMSFullSampleProfile(
        dataset_version=str(raw["dataset_version"]),
        profile_sha256=hashlib.sha256(payload).hexdigest(),
        training_profile=training_profile,
        training_profile_path=training_path,
        audit_evidence_path=audit_path,
        audit_evidence_sha256=str(audit_ref["sha256"]),
        capacity_evidence_path=capacity_path,
        capacity_evidence_sha256=str(capacity_ref["sha256"]),
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
        raw_root_env=raw_root_env,
        output_root_env=output_root_env,
        allowed_filesystem_types=tuple(str(value) for value in storage["allowed_filesystem_types"]),
        sample_count=sample_count,
        shard_count=shard_count,
        samples_per_shard=samples_per_shard,
        compression_level=int(output["compression_level"]),
        minimum_free_bytes=minimum_free_bytes,
        maximum_logical_output_bytes=maximum_logical_output_bytes,
        maximum_physical_output_bytes=maximum_physical_output_bytes,
    )


def create_full_sample_plan(
    profile: MRMSFullSampleProfile,
    *,
    audit_root: Path,
    output_path: Path,
) -> dict[str, Any]:
    inventory_path = audit_root / "asset-inventory.jsonl"
    window_path = audit_root / "window-index.jsonl"
    if output_path.exists():
        raise MRMSFullSampleError(f"full-sample plan already exists: {output_path}")
    if sha256_file(inventory_path) != profile.asset_inventory_sha256:
        raise MRMSFullSampleError("full-sample asset-inventory SHA-256 differs")
    if sha256_file(window_path) != profile.window_index_sha256:
        raise MRMSFullSampleError("full-sample window-index SHA-256 differs")
    with window_path.open(encoding="utf-8") as handle:
        selected = select_temporal_windows((json.loads(line) for line in handle), profile)
    if len(selected) != profile.shard_count:
        raise MRMSFullSampleError("full-sample selected temporal-window count differs")
    selected = [{**row, "shard_index": index} for index, row in enumerate(selected)]
    identity = {
        "full_sample_profile_sha256": profile.profile_sha256,
        "asset_inventory_sha256": profile.asset_inventory_sha256,
        "window_index_sha256": profile.window_index_sha256,
        "selected_windows": selected,
    }
    plan_id = hashlib.sha256(
        json.dumps(identity, sort_keys=True, separators=(",", ":")).encode()
    ).hexdigest()
    plan = {
        "schema_version": "1.0",
        "plan_id": plan_id,
        "created_at": datetime.now(UTC).isoformat(),
        "dataset_version": profile.dataset_version,
        "full_sample_profile_sha256": profile.profile_sha256,
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


def load_full_sample_plan(
    path: Path,
    profile: MRMSFullSampleProfile,
) -> dict[str, Any]:
    plan = _load_json_object(path, "full-sample plan")
    selected = plan.get("selected_windows")
    if not isinstance(selected, list):
        raise MRMSFullSampleError("full-sample selected windows are invalid")
    identity = {
        "full_sample_profile_sha256": profile.profile_sha256,
        "asset_inventory_sha256": profile.asset_inventory_sha256,
        "window_index_sha256": profile.window_index_sha256,
        "selected_windows": selected,
    }
    expected_plan_id = hashlib.sha256(
        json.dumps(identity, sort_keys=True, separators=(",", ":")).encode()
    ).hexdigest()
    if (
        plan.get("schema_version") != "1.0"
        or plan.get("plan_id") != expected_plan_id
        or plan.get("dataset_version") != profile.dataset_version
        or plan.get("full_sample_profile_sha256") != profile.profile_sha256
        or plan.get("training_profile_sha256")
        != profile.training_profile.profile_sha256
        or plan.get("asset_inventory_sha256") != profile.asset_inventory_sha256
        or plan.get("window_index_sha256") != profile.window_index_sha256
        or int(plan.get("selected_window_count", -1)) != profile.shard_count
        or int(plan.get("planned_sample_count", -1)) != profile.sample_count
        or int(plan.get("holdout_windows_selected", -1)) != 0
        or any(
            row.get("split") != "training" or int(row.get("shard_index", -1)) != index
            for index, row in enumerate(selected)
        )
    ):
        raise MRMSFullSampleError("full-sample plan identity or leakage boundary differs")
    return plan


def _nearest_existing_path(path: Path) -> Path:
    candidate = path
    while not candidate.exists() and candidate != candidate.parent:
        candidate = candidate.parent
    if not candidate.exists():
        raise MRMSFullSampleError(f"no existing parent for output root: {path}")
    return candidate


def _filesystem_type(path: Path) -> str:
    probe = _nearest_existing_path(path)
    try:
        result = subprocess.run(
            ["findmnt", "-n", "-o", "FSTYPE", "-T", str(probe)],
            check=True,
            capture_output=True,
            text=True,
        )
    except (FileNotFoundError, subprocess.CalledProcessError) as exc:
        raise MRMSFullSampleError(f"cannot determine output filesystem type: {exc}") from exc
    value = result.stdout.strip().lower()
    if not value:
        raise MRMSFullSampleError("output filesystem type is empty")
    return value


def resolve_storage_roots(
    profile: MRMSFullSampleProfile,
    *,
    environ: Mapping[str, str] | None = None,
    filesystem_type: str | None = None,
) -> tuple[Path, Path]:
    values = os.environ if environ is None else environ
    raw_value = values.get(profile.raw_root_env, "")
    output_value = values.get(profile.output_root_env, "")
    if not raw_value or not output_value:
        raise MRMSFullSampleError(
            f"set {profile.raw_root_env} and {profile.output_root_env} explicitly"
        )
    raw_root = Path(raw_value).expanduser()
    output_root = Path(output_value).expanduser()
    if not raw_root.is_absolute() or not output_root.is_absolute():
        raise MRMSFullSampleError("full-sample storage roots must be absolute")
    raw_root = raw_root.resolve()
    output_root = output_root.resolve()
    if not raw_root.is_dir():
        raise MRMSFullSampleError(f"MRMS raw root is not a directory: {raw_root}")
    if (
        raw_root == output_root
        or output_root.is_relative_to(raw_root)
        or raw_root.is_relative_to(output_root)
    ):
        raise MRMSFullSampleError("MRMS raw and full-sample roots must not overlap")
    detected = (filesystem_type or _filesystem_type(output_root)).lower()
    if detected not in profile.allowed_filesystem_types:
        raise MRMSFullSampleError(
            "full-sample output must use an approved network filesystem: "
            f"allowed={profile.allowed_filesystem_types} actual={detected}"
        )
    return raw_root, output_root


def _logical_bytes_per_shard(profile: MRMSFullSampleProfile) -> int:
    return (
        profile.samples_per_shard
        * profile.training_profile.total_frames
        * profile.crop_size
        * profile.crop_size
        * (np.dtype("float16").itemsize + np.dtype("uint8").itemsize)
    )


def enforce_capacity_gate(
    profile: MRMSFullSampleProfile,
    *,
    output_root: Path,
    in_flight_shards: int = 0,
) -> dict[str, int]:
    probe = _nearest_existing_path(output_root)
    free_bytes = shutil.disk_usage(probe).free
    physical_bytes = _tree_size(output_root) if output_root.exists() else 0
    reserved_bytes = in_flight_shards * _logical_bytes_per_shard(profile)
    if free_bytes < profile.minimum_free_bytes:
        raise MRMSFullSampleError(
            "full-sample free-space gate failed: "
            f"required={profile.minimum_free_bytes} actual={free_bytes}"
        )
    if physical_bytes + reserved_bytes > profile.maximum_physical_output_bytes:
        raise MRMSFullSampleError(
            "full-sample physical-output gate failed: "
            f"current={physical_bytes} reserved={reserved_bytes} "
            f"maximum={profile.maximum_physical_output_bytes}"
        )
    return {
        "free_bytes": free_bytes,
        "physical_bytes": physical_bytes,
        "reserved_bytes": reserved_bytes,
    }


def write_full_sample_shard(
    output_path: Path,
    *,
    rain_rate: np.ndarray,
    valid_mask: np.ndarray,
    samples: Sequence[dict[str, Any]],
    attributes: dict[str, Any],
    profile: MRMSFullSampleProfile,
) -> dict[str, Any]:
    expected_shape = (
        profile.samples_per_shard,
        profile.training_profile.total_frames,
        profile.crop_size,
        profile.crop_size,
    )
    if rain_rate.shape != expected_shape or valid_mask.shape != expected_shape:
        raise MRMSFullSampleError("full-sample shard shape differs from profile")
    if rain_rate.dtype != np.dtype("float16") or valid_mask.dtype != np.dtype("uint8"):
        raise MRMSFullSampleError("full-sample shard dtype differs from profile")
    if np.any(valid_mask != 1) or np.any(~np.isfinite(rain_rate)):
        raise MRMSFullSampleError("full-sample shard contains missing values")
    if np.any(rain_rate < 0) or np.any(rain_rate > profile.rain_rate_cap_mm_h):
        raise MRMSFullSampleError("full-sample shard rain rate is outside the frozen range")
    if len(samples) != profile.samples_per_shard:
        raise MRMSFullSampleError("full-sample shard sample metadata count differs")
    logical_bytes = rain_rate.nbytes + valid_mask.nbytes
    if logical_bytes > profile.maximum_logical_output_bytes:
        raise MRMSFullSampleError("full-sample shard exceeds the logical output cap")
    if output_path.exists():
        raise MRMSFullSampleError(f"full-sample shard already exists: {output_path}")

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
        chunks = (
            1,
            profile.training_profile.total_frames,
            profile.crop_size,
            profile.crop_size,
        )
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
                "schema_version": "rainpulse.nowcastnet-mrms-full-sample-shard/1.0",
                "dataset_version": profile.dataset_version,
                "full_sample_profile_sha256": profile.profile_sha256,
                "training_profile_sha256": profile.training_profile.profile_sha256,
                "unit": "mm/h",
                "missing_value_policy": "reject_any_missing",
                "latitude_order": "ascending",
                "operational_eligible": False,
            }
        )
        with (temporary / "samples.jsonl").open("w", encoding="utf-8") as handle:
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
        report = {
            "schema_version": "1.0",
            "sample_count": len(samples),
            "logical_bytes": logical_bytes,
            "content_sha256": _tree_sha256(temporary),
            "created_at": datetime.now(UTC).isoformat(),
            "all_samples_valid": True,
            "operational_eligible": False,
            "payload_stored_bytes": _tree_size(temporary),
        }
        (temporary / "shard-report.json").write_text(
            json.dumps(report, ensure_ascii=False, indent=2, sort_keys=True) + "\n",
            encoding="utf-8",
        )
        os.replace(temporary, output_path)
        return {**report, "stored_bytes": _tree_size(output_path)}
    except Exception:
        shutil.rmtree(temporary, ignore_errors=True)
        raise


def _load_asset_inventory(
    path: Path,
    profile: MRMSFullSampleProfile,
) -> list[dict[str, Any]]:
    if sha256_file(path) != profile.asset_inventory_sha256:
        raise MRMSFullSampleError("full-sample asset inventory changed before preprocessing")
    assets: list[dict[str, Any]] = []
    with path.open(encoding="utf-8") as handle:
        for line in handle:
            row = json.loads(line)
            if int(row.get("asset_index", -1)) != len(assets):
                raise MRMSFullSampleError("full-sample asset indices are not contiguous")
            row = {
                **row,
                "relative_path": normalize_inventory_relative_path(row["relative_path"]),
            }
            assets.append(row)
    return assets


def normalize_inventory_relative_path(value: Any) -> str:
    """Resolve the audit inventory's historical ``raw/`` prefix against raw root."""
    relative = Path(str(value))
    if relative.is_absolute() or ".." in relative.parts:
        raise MRMSFullSampleError("full-sample inventory asset path is unsafe")
    parts = relative.parts[1:] if relative.parts[:1] == ("raw",) else relative.parts
    if not parts:
        raise MRMSFullSampleError("full-sample inventory asset path is empty")
    return Path(*parts).as_posix()


def _process_window_shard(
    profile: MRMSFullSampleProfile,
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
    report = write_full_sample_shard(
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
    report.update(
        {
            "shard_index": int(window["shard_index"]),
            "window_id": window["window_id"],
            "duration_seconds": (datetime.now(UTC) - started_at).total_seconds(),
            "issue_time": window["issue_time"],
        }
    )
    return report


def _load_existing_shard_report(
    path: Path,
    window: dict[str, Any],
    profile: MRMSFullSampleProfile,
) -> dict[str, Any]:
    report = _load_json_object(path / "shard-report.json", "full-sample shard report")
    try:
        group = zarr.open_group(str(path), mode="r")
    except (OSError, KeyError, ValueError) as exc:
        raise MRMSFullSampleError(f"cannot resume full-sample shard {path}: {exc}") from exc
    if (
        int(report.get("sample_count", -1)) != profile.samples_per_shard
        or group.attrs.get("full_sample_profile_sha256") != profile.profile_sha256
        or group.attrs.get("window_id") != window["window_id"]
        or int(group.attrs.get("shard_index", -1)) != int(window["shard_index"])
    ):
        raise MRMSFullSampleError(f"existing full-sample shard identity differs: {path}")
    return {
        **report,
        "stored_bytes": _tree_size(path),
        "shard_index": int(window["shard_index"]),
        "resumed": True,
    }


def _aggregate_output(
    output_root: Path,
    profile: MRMSFullSampleProfile,
    plan: dict[str, Any],
    windows: Sequence[dict[str, Any]],
    shard_reports: Sequence[dict[str, Any]],
    *,
    complete: bool,
    started_at: datetime,
) -> dict[str, Any]:
    sample_path = output_root / ("samples.jsonl" if complete else "partial-samples.jsonl")
    sample_count = 0
    branch_counts = {"importance": 0, "uniform": 0}
    rain_max = 0.0
    with sample_path.open("w", encoding="utf-8") as target:
        for window in sorted(windows, key=lambda value: int(value["shard_index"])):
            shard_path = (
                output_root / "shards" / f"shard-{int(window['shard_index']):05d}.zarr"
            )
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
        "dataset_version": profile.dataset_version,
        "full_sample_profile_sha256": profile.profile_sha256,
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
        "logical_bytes": sum(int(value["logical_bytes"]) for value in shard_reports),
        "stored_bytes": sum(int(value["stored_bytes"]) for value in shard_reports),
        "sample_index_sha256": sha256_file(sample_path),
        "operational_eligible": False,
    }
    name = "full-sample-report.json" if complete else "partial-full-sample-report.json"
    _write_json_atomic(output_root / name, report)
    if complete:
        (output_root / "COMPLETED").write_text(report["sample_index_sha256"] + "\n")
    return report


def run_full_sample_materialization(
    profile: MRMSFullSampleProfile,
    *,
    plan_path: Path,
    audit_root: Path,
    dataset_root: Path,
    output_root: Path,
    workers: int,
    max_windows: int | None = None,
) -> dict[str, Any]:
    if workers < 1 or workers > 4:
        raise MRMSFullSampleError("full-sample workers must be between 1 and 4")
    plan = load_full_sample_plan(plan_path, profile)
    selected = list(plan["selected_windows"])
    if max_windows is not None:
        if max_windows < 1 or max_windows > len(selected):
            raise MRMSFullSampleError("full-sample max-windows is outside the plan")
        selected = selected[:max_windows]
    complete = len(selected) == profile.shard_count
    planned_logical_bytes = len(selected) * _logical_bytes_per_shard(profile)
    if planned_logical_bytes > profile.maximum_logical_output_bytes:
        raise MRMSFullSampleError("planned logical bytes exceed the full-sample output cap")

    enforce_capacity_gate(profile, output_root=output_root)
    if not output_root.exists():
        output_root.parent.mkdir(parents=True, exist_ok=True)
        output_root.mkdir()
        _write_json_atomic(
            output_root / "run-state.json",
            {
                "schema_version": "1.0",
                "dataset_version": profile.dataset_version,
                "full_sample_profile_sha256": profile.profile_sha256,
                "plan_id": plan["plan_id"],
                "status": "running",
                "created_at": datetime.now(UTC).isoformat(),
                "operational_eligible": False,
            },
        )
    else:
        state = _load_json_object(output_root / "run-state.json", "full-sample run state")
        if (
            state.get("full_sample_profile_sha256") != profile.profile_sha256
            or state.get("plan_id") != plan["plan_id"]
        ):
            raise MRMSFullSampleError("full-sample resume identity differs")
        if (output_root / "COMPLETED").exists():
            raise MRMSFullSampleError("full-sample output is already complete")

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
            reports.append(_load_existing_shard_report(target, window, profile))
        else:
            pending.append((window, asset_rows, target))
    enforce_capacity_gate(profile, output_root=output_root)

    completed = len(reports)
    if pending:
        with ProcessPoolExecutor(max_workers=workers) as executor:
            for offset in range(0, len(pending), workers):
                chunk = pending[offset : offset + workers]
                enforce_capacity_gate(
                    profile,
                    output_root=output_root,
                    in_flight_shards=len(chunk),
                )
                futures = [
                    executor.submit(
                        _process_window_shard,
                        profile,
                        dataset_root,
                        asset_rows,
                        window,
                        target,
                    )
                    for window, asset_rows, target in chunk
                ]
                for (window, _, _), future in zip(chunk, futures, strict=True):
                    try:
                        report = future.result()
                    except Exception as exc:  # noqa: BLE001 - normalize worker failures
                        raise MRMSFullSampleError(
                            f"full-sample shard failed for {window['window_id']}: {exc}"
                        ) from exc
                    reports.append(report)
                    completed += 1
                    print(
                        f"full-sample shard {completed}/{len(selected)} "
                        f"index={window['shard_index']} "
                        f"duration={report['duration_seconds']:.2f}s",
                        flush=True,
                    )
                enforce_capacity_gate(profile, output_root=output_root)

    if len(reports) != len(selected):
        raise MRMSFullSampleError("full-sample shard count does not close")
    report = _aggregate_output(
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
            "dataset_version": profile.dataset_version,
            "full_sample_profile_sha256": profile.profile_sha256,
            "plan_id": plan["plan_id"],
            "status": report["status"],
            "updated_at": datetime.now(UTC).isoformat(),
            "operational_eligible": False,
        },
    )
    return report


def _validate_shard(
    path: Path,
    *,
    window: dict[str, Any],
    profile: MRMSFullSampleProfile,
    verify_content_hash: bool,
) -> tuple[dict[str, Any], list[dict[str, Any]]]:
    report = _load_json_object(path / "shard-report.json", "full-sample shard report")
    try:
        group = zarr.open_group(str(path), mode="r")
        rain = group["rain_rate"]
        mask = group["valid_mask"]
    except (OSError, KeyError, ValueError) as exc:
        raise MRMSFullSampleError(f"cannot open full-sample shard {path}: {exc}") from exc
    expected_shape = (
        profile.samples_per_shard,
        profile.training_profile.total_frames,
        profile.crop_size,
        profile.crop_size,
    )
    if (
        rain.shape != expected_shape
        or mask.shape != expected_shape
        or rain.dtype != np.dtype("float16")
        or mask.dtype != np.dtype("uint8")
        or group.attrs.get("schema_version")
        != "rainpulse.nowcastnet-mrms-full-sample-shard/1.0"
        or group.attrs.get("full_sample_profile_sha256") != profile.profile_sha256
        or group.attrs.get("window_id") != window["window_id"]
        or int(group.attrs.get("shard_index", -1)) != int(window["shard_index"])
        or group.attrs.get("missing_value_policy") != "reject_any_missing"
        or group.attrs.get("operational_eligible") is not False
        or int(report.get("sample_count", -1)) != profile.samples_per_shard
        or report.get("all_samples_valid") is not True
    ):
        raise MRMSFullSampleError(f"full-sample shard contract differs: {path}")
    if verify_content_hash and _tree_sha256(
        path, excluded_names=frozenset({"shard-report.json"})
    ) != str(report.get("content_sha256")):
        raise MRMSFullSampleError(f"full-sample shard content SHA-256 differs: {path}")
    try:
        samples = [
            json.loads(line)
            for line in (path / "samples.jsonl").read_text(encoding="utf-8").splitlines()
        ]
    except (OSError, json.JSONDecodeError) as exc:
        raise MRMSFullSampleError(f"cannot load full-sample metadata {path}: {exc}") from exc
    if len(samples) != profile.samples_per_shard:
        raise MRMSFullSampleError(f"full-sample shard sample count differs: {path}")
    branches = {"importance": 0, "uniform": 0}
    for index, sample in enumerate(samples):
        branch = str(sample.get("branch"))
        coordinates = sample.get("coordinates")
        x_start = int(sample.get("x_start", -1))
        y_start = int(sample.get("y_start", -1))
        if (
            branch not in branches
            or not isinstance(coordinates, dict)
            or int(sample.get("sample_index_in_shard", -1)) != index
            or sample.get("window_id") != window["window_id"]
            or sample.get("all_valid") is not True
            or coordinates != crop_coordinates(profile, y_start=y_start, x_start=x_start)
        ):
            raise MRMSFullSampleError(f"full-sample metadata differs: {path}")
        branches[branch] += 1
    if branches != {
        "importance": profile.importance_crops_per_window,
        "uniform": profile.uniform_crops_per_window,
    }:
        raise MRMSFullSampleError(f"full-sample branch counts differ: {path}")
    return {**report, "stored_bytes": _tree_size(path)}, samples


def validate_full_sample_output(
    profile: MRMSFullSampleProfile,
    *,
    plan: dict[str, Any],
    output_root: Path,
    random_sample_count: int = 64,
    allow_partial: bool = False,
    expected_windows: int | None = None,
    verify_content_hash: bool = True,
) -> dict[str, Any]:
    if allow_partial:
        report_path = output_root / "partial-full-sample-report.json"
        sample_path = output_root / "partial-samples.jsonl"
        report = _load_json_object(report_path, "partial full-sample report")
        window_count = int(report.get("processed_window_count", -1))
        if expected_windows is None or window_count != expected_windows:
            raise MRMSFullSampleError("partial validation requires the exact expected window count")
        windows = list(plan["selected_windows"][:window_count])
        expected_status = "partial_smoke"
    else:
        if not (output_root / "COMPLETED").is_file():
            raise MRMSFullSampleError("full-sample output does not have a COMPLETED marker")
        report_path = output_root / "full-sample-report.json"
        sample_path = output_root / "samples.jsonl"
        report = _load_json_object(report_path, "full-sample report")
        window_count = profile.shard_count
        windows = list(plan["selected_windows"])
        expected_status = "complete"
    expected_samples = window_count * profile.samples_per_shard
    if random_sample_count < 1 or random_sample_count > expected_samples:
        raise MRMSFullSampleError("full-sample random read count is outside the dataset")
    sample_index_sha256 = sha256_file(sample_path)
    if (
        report.get("status") != expected_status
        or report.get("full_sample_profile_sha256") != profile.profile_sha256
        or report.get("plan_id") != plan["plan_id"]
        or int(report.get("processed_window_count", -1)) != window_count
        or int(report.get("sample_count", -1)) != expected_samples
        or int(report.get("holdout_windows_processed", -1)) != 0
        or report.get("all_samples_valid") is not True
        or report.get("sample_index_sha256") != sample_index_sha256
    ):
        raise MRMSFullSampleError("full-sample aggregate report differs")
    if not allow_partial:
        marker = (output_root / "COMPLETED").read_text(encoding="utf-8").strip()
        if marker != sample_index_sha256:
            raise MRMSFullSampleError("full-sample completion marker differs")

    shard_root = output_root / "shards"
    indexed_samples: list[dict[str, Any]] = []
    logical_bytes = 0
    stored_bytes = 0
    for window in windows:
        index = int(window["shard_index"])
        shard_report, samples = _validate_shard(
            shard_root / f"shard-{index:05d}.zarr",
            window=window,
            profile=profile,
            verify_content_hash=verify_content_hash,
        )
        logical_bytes += int(shard_report["logical_bytes"])
        stored_bytes += int(shard_report["stored_bytes"])
        indexed_samples.extend({**sample, "shard_index": index} for sample in samples)
    expected_lines = [
        json.dumps(sample, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
        for sample in indexed_samples
    ]
    if sample_path.read_text(encoding="utf-8").splitlines() != expected_lines:
        raise MRMSFullSampleError("full-sample root sample index differs")
    if (
        logical_bytes != int(report.get("logical_bytes", -1))
        or stored_bytes != int(report.get("stored_bytes", -1))
        or logical_bytes > profile.maximum_logical_output_bytes
        or stored_bytes > profile.maximum_physical_output_bytes
    ):
        raise MRMSFullSampleError("full-sample aggregate capacity differs")

    generator = np.random.default_rng(profile.random_seed)
    selected_indices = sorted(
        int(value)
        for value in generator.choice(expected_samples, size=random_sample_count, replace=False)
    )
    opened: dict[int, zarr.Group] = {}
    read_bytes = 0
    rain_min = float("inf")
    rain_max = 0.0
    started = time.perf_counter()
    for sample_index in selected_indices:
        shard_index, offset = divmod(sample_index, profile.samples_per_shard)
        group = opened.get(shard_index)
        if group is None:
            group = zarr.open_group(
                str(shard_root / f"shard-{shard_index:05d}.zarr"), mode="r"
            )
            opened[shard_index] = group
        rain = np.asarray(group["rain_rate"][offset], dtype="float16")
        mask = np.asarray(group["valid_mask"][offset], dtype="uint8")
        if np.any(~np.isfinite(rain)) or np.any(mask != 1):
            raise MRMSFullSampleError("full-sample random read contains missing data")
        rain_min = min(rain_min, float(np.min(rain)))
        rain_max = max(rain_max, float(np.max(rain)))
        read_bytes += rain.nbytes + mask.nbytes
    elapsed = max(time.perf_counter() - started, np.finfo("float64").eps)
    validation = {
        "schema_version": "1.0",
        "status": "passed",
        "validation_scope": "partial_smoke" if allow_partial else "complete_library",
        "validated_at": datetime.now(UTC).isoformat(),
        "dataset_version": profile.dataset_version,
        "full_sample_profile_sha256": profile.profile_sha256,
        "plan_id": plan["plan_id"],
        "shard_count": window_count,
        "sample_count": expected_samples,
        "logical_bytes": logical_bytes,
        "stored_bytes": stored_bytes,
        "content_hash_verified": verify_content_hash,
        "random_read_sample_count": random_sample_count,
        "random_read_logical_mib_s": read_bytes / elapsed / (1024 * 1024),
        "sampled_rain_min_mm_h": rain_min,
        "sampled_rain_max_mm_h": rain_max,
        "holdout_windows_processed": 0,
        "sample_index_sha256": sample_index_sha256,
        "operational_eligible": False,
    }
    name = "partial-validation-report.json" if allow_partial else "validation-report.json"
    _write_json_atomic(output_root / name, validation)
    return validation


def _build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="Prepare the frozen MRMS NowcastNet full training sample library"
    )
    subparsers = parser.add_subparsers(dest="command", required=True)
    for command in ("plan", "run", "validate"):
        child = subparsers.add_parser(command)
        child.add_argument("--repository-root", type=Path, required=True)
        child.add_argument("--profile", type=Path, required=True)
        if command == "plan":
            child.add_argument("--audit-root", type=Path, required=True)
            child.add_argument("--output", type=Path, required=True)
        elif command == "run":
            child.add_argument("--audit-root", type=Path, required=True)
            child.add_argument("--plan", type=Path, required=True)
            child.add_argument("--workers", type=int, default=1)
            child.add_argument("--max-windows", type=int)
        else:
            child.add_argument("--plan", type=Path, required=True)
            child.add_argument("--random-samples", type=int, default=64)
            child.add_argument("--allow-partial", action="store_true")
            child.add_argument("--expected-windows", type=int)
            child.add_argument("--skip-content-hash", action="store_true")
    return parser


def main(argv: list[str] | None = None) -> int:
    args = _build_parser().parse_args(argv)
    try:
        profile = load_mrms_full_sample_profile(
            args.profile,
            repository_root=args.repository_root,
        )
        if args.command == "plan":
            result = create_full_sample_plan(
                profile,
                audit_root=args.audit_root,
                output_path=args.output,
            )
        else:
            dataset_root, output_root = resolve_storage_roots(profile)
            if args.command == "run":
                result = run_full_sample_materialization(
                    profile,
                    plan_path=args.plan,
                    audit_root=args.audit_root,
                    dataset_root=dataset_root,
                    output_root=output_root,
                    workers=args.workers,
                    max_windows=args.max_windows,
                )
            else:
                plan = load_full_sample_plan(args.plan, profile)
                result = validate_full_sample_output(
                    profile,
                    plan=plan,
                    output_root=output_root,
                    random_sample_count=args.random_samples,
                    allow_partial=args.allow_partial,
                    expected_windows=args.expected_windows,
                    verify_content_hash=not args.skip_content_hash,
                )
    except MRMSPilotError as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 1
    print(json.dumps(result, ensure_ascii=False, indent=2, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
