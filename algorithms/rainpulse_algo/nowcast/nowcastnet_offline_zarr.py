from __future__ import annotations

import json
from collections.abc import Mapping
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from typing import Any
from uuid import UUID

import numpy as np
import zarr
from numcodecs import Blosc
from zarr.storage import MemoryStore

from .nowcastnet_adapter import NowcastNetInputError, NowcastNetResult
from .nowcastnet_profile import NowcastNetProfile

INPUT_CONTRACT_NAME = "rainpulse.nowcastnet-offline-input"
INPUT_CONTRACT_VERSION = "1.0"
OUTPUT_CONTRACT_NAME = "rainpulse.nowcastnet-offline-output"
OUTPUT_CONTRACT_VERSION = "1.0"


@dataclass(frozen=True)
class NowcastNetOfflineInputFields:
    rain_rate_mm_h: np.ndarray
    valid_mask: np.ndarray
    latitude: np.ndarray
    longitude: np.ndarray
    issue_time: datetime
    grid_id: str
    input_asset_ids: tuple[UUID, ...]
    source_group: str


def build_nowcastnet_offline_input_zarr_store(
    rain_rate_mm_h: np.ndarray,
    valid_mask: np.ndarray,
    *,
    latitude: np.ndarray,
    longitude: np.ndarray,
    issue_time: datetime,
    grid_id: str,
    input_asset_ids: list[UUID],
    source_group: str,
    profile: NowcastNetProfile,
) -> dict[str, bytes]:
    issue_time = _utc(issue_time)
    rate = np.asarray(rain_rate_mm_h, dtype="float32")
    valid = np.asarray(valid_mask, dtype="uint8")
    lat = np.asarray(latitude, dtype="float32")
    lon = np.asarray(longitude, dtype="float32")
    _validate_input_arrays(rate, valid, lat, lon, profile)
    if len(input_asset_ids) != profile.protocol.input_frames or len(set(input_asset_ids)) != len(
        input_asset_ids
    ):
        raise NowcastNetInputError("NowcastNet offline input requires nine unique asset IDs")
    if not grid_id or not source_group:
        raise NowcastNetInputError(
            "NowcastNet offline grid and source-group identities are required"
        )

    store = MemoryStore()
    root = zarr.group(store=store, overwrite=True)
    root.attrs.update(
        {
            "contract_name": INPUT_CONTRACT_NAME,
            "contract_version": INPUT_CONTRACT_VERSION,
            "issue_time": issue_time.isoformat(),
            "grid_id": grid_id,
            "input_asset_ids": [str(value) for value in input_asset_ids],
            "source_group": source_group,
            "field": profile.protocol.input_field,
            "units": profile.protocol.units,
            "timestep_minutes": profile.protocol.timestep_minutes,
            "missing_policy": profile.protocol.missing_policy,
        }
    )
    compressor = Blosc(cname="zstd", clevel=3, shuffle=Blosc.BITSHUFFLE)
    root.create_dataset(
        "rain_rate",
        data=rate,
        chunks=(1, 128, 128),
        compressor=compressor,
        fill_value=np.nan,
    ).attrs["units"] = profile.protocol.units
    root.create_dataset(
        "valid_mask",
        data=valid,
        chunks=(1, 128, 128),
        compressor=compressor,
        fill_value=0,
    )
    root.create_dataset("lat", data=lat, chunks=(len(lat),))
    root.create_dataset("lon", data=lon, chunks=(len(lon),))
    zarr.consolidate_metadata(store)
    objects = {str(key): bytes(value) for key, value in store.items()}
    load_nowcastnet_offline_input(objects, profile=profile)
    return objects


def load_nowcastnet_offline_input(
    objects: Mapping[str, bytes],
    *,
    profile: NowcastNetProfile,
) -> NowcastNetOfflineInputFields:
    required = {".zgroup", ".zattrs", ".zmetadata"}
    if not required <= objects.keys():
        raise NowcastNetInputError("NowcastNet offline input is missing Zarr metadata")
    store = MemoryStore()
    store.update({key: bytes(value) for key, value in objects.items()})
    root = zarr.open_consolidated(store=store, mode="r")
    expected_attributes = (
        ("contract_name", INPUT_CONTRACT_NAME),
        ("contract_version", INPUT_CONTRACT_VERSION),
        ("field", profile.protocol.input_field),
        ("units", profile.protocol.units),
        ("timestep_minutes", profile.protocol.timestep_minutes),
        ("missing_policy", profile.protocol.missing_policy),
    )
    for name, expected in expected_attributes:
        if root.attrs.get(name) != expected:
            raise NowcastNetInputError(
                f"NowcastNet offline input {name} differs from the profile"
            )
    for name in ("rain_rate", "valid_mask", "lat", "lon"):
        if name not in root:
            raise NowcastNetInputError(f"NowcastNet offline input is missing {name}")
    rate = root["rain_rate"][:]
    valid = root["valid_mask"][:]
    lat = root["lat"][:]
    lon = root["lon"][:]
    _validate_input_arrays(rate, valid, lat, lon, profile)
    if rate.dtype != np.dtype("float32") or valid.dtype != np.dtype("uint8"):
        raise NowcastNetInputError("NowcastNet offline arrays have invalid dtypes")
    raw_asset_ids = root.attrs.get("input_asset_ids")
    if not isinstance(raw_asset_ids, list) or len(raw_asset_ids) != profile.protocol.input_frames:
        raise NowcastNetInputError("NowcastNet offline input asset identity is invalid")
    try:
        asset_ids = tuple(UUID(str(value)) for value in raw_asset_ids)
    except ValueError as exc:
        raise NowcastNetInputError("NowcastNet offline input asset UUID is invalid") from exc
    if len(set(asset_ids)) != len(asset_ids):
        raise NowcastNetInputError("NowcastNet offline input asset IDs are not unique")
    grid_id = root.attrs.get("grid_id")
    source_group = root.attrs.get("source_group")
    if not isinstance(grid_id, str) or not grid_id:
        raise NowcastNetInputError("NowcastNet offline grid identity is invalid")
    if not isinstance(source_group, str) or not source_group:
        raise NowcastNetInputError("NowcastNet offline source-group identity is invalid")
    issue_time = _parse_time(root.attrs.get("issue_time"))
    if issue_time.timestamp() % (profile.protocol.timestep_minutes * 60):
        raise NowcastNetInputError("NowcastNet issue time is not on a ten-minute boundary")
    return NowcastNetOfflineInputFields(
        rain_rate_mm_h=rate,
        valid_mask=valid,
        latitude=lat,
        longitude=lon,
        issue_time=issue_time,
        grid_id=grid_id,
        input_asset_ids=asset_ids,
        source_group=source_group,
    )


def build_nowcastnet_offline_output_zarr_store(
    result: NowcastNetResult,
    *,
    run_id: UUID,
    job_id: UUID,
    issue_time: datetime,
    input_uri: str,
    input_asset_ids: list[UUID],
    grid_id: str,
    latitude: np.ndarray,
    longitude: np.ndarray,
    source_group: str,
    profile: NowcastNetProfile,
    runtime_ms: int,
    runtime_info: Mapping[str, Any],
) -> dict[str, bytes]:
    issue_time = _utc(issue_time)
    if runtime_ms < 0:
        raise NowcastNetInputError("NowcastNet runtime cannot be negative")
    rain_rate = np.asarray(result.rain_rate_mm_h, dtype="float32")
    member_valid = np.asarray(result.valid_mask, dtype="uint8")
    lat = np.asarray(latitude, dtype="float32")
    lon = np.asarray(longitude, dtype="float32")
    expected = (
        profile.protocol.ensemble_members,
        profile.protocol.output_frames,
        profile.protocol.input_height,
        profile.protocol.input_width,
    )
    if rain_rate.shape != expected or member_valid.shape != expected:
        raise NowcastNetInputError("NowcastNet offline output arrays have invalid shapes")
    if np.any((member_valid != 0) & (member_valid != 1)):
        raise NowcastNetInputError("NowcastNet offline member-valid mask is not binary")
    if np.any(~np.isfinite(rain_rate[member_valid == 1])) or np.any(
        rain_rate[member_valid == 1] < 0
    ):
        raise NowcastNetInputError("NowcastNet offline valid output values are invalid")
    output_valid = np.all(member_valid == 1, axis=0).astype("uint8")
    lead_minutes = np.arange(
        profile.protocol.timestep_minutes,
        profile.protocol.timestep_minutes * (profile.protocol.output_frames + 1),
        profile.protocol.timestep_minutes,
        dtype="int32",
    )
    valid_times = np.asarray(
        [issue_time.replace(tzinfo=None) + timedelta(minutes=int(value)) for value in lead_minutes],
        dtype="datetime64[ns]",
    )
    summary = {
        "schema_version": "1.0",
        "run_id": str(run_id),
        "job_id": str(job_id),
        "issue_time": issue_time.isoformat(),
        "grid_id": grid_id,
        "model_id": profile.model_id,
        "model_version": profile.model_version,
        "config_version": profile.profile_version,
        "input_uri": input_uri,
        "input_asset_ids": [str(value) for value in input_asset_ids],
        "source_group": source_group,
        "member_count": profile.protocol.ensemble_members,
        "lead_count": profile.protocol.output_frames,
        "lead_step_minutes": profile.protocol.timestep_minutes,
        "random_seed": result.random_seed,
        "clipped_input_pixel_count": result.clipped_input_pixel_count,
        "clipped_negative_output_pixel_count": result.clipped_negative_output_pixel_count,
        "maximum_forecast_rate_mm_h": float(np.max(rain_rate)),
        "runtime_ms": runtime_ms,
        "operational_eligible": False,
        "product_publication_enabled": False,
    }

    store = MemoryStore()
    root = zarr.group(store=store, overwrite=True)
    root.attrs.update(
        {
            "contract_name": OUTPUT_CONTRACT_NAME,
            "contract_version": OUTPUT_CONTRACT_VERSION,
            "run_id": str(run_id),
            "job_id": str(job_id),
            "issue_time": issue_time.isoformat(),
            "grid_id": grid_id,
            "model_id": profile.model_id,
            "model_version": profile.model_version,
            "config_version": profile.profile_version,
            "input_uri": input_uri,
            "input_asset_ids": [str(value) for value in input_asset_ids],
            "source_group": source_group,
            "ensemble_member_count": profile.protocol.ensemble_members,
            "random_seed": result.random_seed,
            "weights_uri": profile.artifact.weights_uri,
            "weights_sha256": profile.artifact.weights_sha256,
            "capsule_archive_sha256": profile.artifact.capsule_archive_sha256,
            "missing_policy": profile.protocol.missing_policy,
            "output_negative_policy": profile.protocol.output_negative_policy,
            "clipped_input_pixel_count": result.clipped_input_pixel_count,
            "clipped_negative_output_pixel_count": result.clipped_negative_output_pixel_count,
            "operational_eligible": False,
            "product_publication_enabled": False,
            "runtime_ms": runtime_ms,
            "runtime_info": dict(runtime_info),
            "created_at": datetime.now(UTC).isoformat(),
        }
    )
    root.create_dataset(
        "member", data=np.arange(profile.protocol.ensemble_members, dtype="int16")
    )
    root.create_dataset("lead_time", data=lead_minutes).attrs["units"] = "minute"
    root.create_dataset("valid_time", data=valid_times).attrs.update(
        {"standard_name": "time", "timezone": "UTC"}
    )
    root.create_dataset("lat", data=lat, chunks=(len(lat),))
    root.create_dataset("lon", data=lon, chunks=(len(lon),))
    compressor = Blosc(cname="zstd", clevel=3, shuffle=Blosc.BITSHUFFLE)
    root.create_dataset(
        "rain_rate",
        data=rain_rate,
        chunks=(1, 1, 128, 128),
        compressor=compressor,
        fill_value=np.nan,
    ).attrs["units"] = profile.protocol.units
    root.create_dataset(
        "member_valid_mask",
        data=member_valid,
        chunks=(1, 1, 128, 128),
        compressor=compressor,
        fill_value=0,
    )
    root.create_dataset(
        "output_valid_mask",
        data=output_valid,
        chunks=(1, 128, 128),
        compressor=compressor,
        fill_value=0,
    )
    store["forecast/summary.json"] = json.dumps(
        summary, separators=(",", ":"), sort_keys=True
    ).encode()
    zarr.consolidate_metadata(store)
    objects = {str(key): bytes(value) for key, value in store.items()}
    validate_nowcastnet_offline_output(objects, profile=profile)
    return objects


def validate_nowcastnet_offline_output(
    objects: Mapping[str, bytes],
    *,
    profile: NowcastNetProfile,
) -> dict[str, Any]:
    required = {".zgroup", ".zattrs", ".zmetadata", "forecast/summary.json"}
    if not required <= objects.keys():
        raise NowcastNetInputError("NowcastNet offline output is missing metadata or summary")
    store = MemoryStore()
    store.update({key: bytes(value) for key, value in objects.items()})
    root = zarr.open_consolidated(store=store, mode="r")
    expected_attributes = (
        ("contract_name", OUTPUT_CONTRACT_NAME),
        ("contract_version", OUTPUT_CONTRACT_VERSION),
        ("model_id", profile.model_id),
        ("model_version", profile.model_version),
        ("config_version", profile.profile_version),
        ("weights_uri", profile.artifact.weights_uri),
        ("weights_sha256", profile.artifact.weights_sha256),
        ("operational_eligible", False),
        ("product_publication_enabled", False),
    )
    for name, expected in expected_attributes:
        if root.attrs.get(name) != expected:
            raise NowcastNetInputError(f"NowcastNet offline output {name} is invalid")
    expected_shape = (
        profile.protocol.ensemble_members,
        profile.protocol.output_frames,
        profile.protocol.input_height,
        profile.protocol.input_width,
    )
    expected_datasets = {
        "rain_rate": (expected_shape, np.dtype("float32")),
        "member_valid_mask": (expected_shape, np.dtype("uint8")),
        "output_valid_mask": (expected_shape[1:], np.dtype("uint8")),
        "lat": ((expected_shape[2],), np.dtype("float32")),
        "lon": ((expected_shape[3],), np.dtype("float32")),
    }
    for name, (shape, dtype) in expected_datasets.items():
        if name not in root or root[name].shape != shape or root[name].dtype != dtype:
            raise NowcastNetInputError(
                f"NowcastNet offline output {name} has invalid shape or dtype"
            )
    lead = root["lead_time"][:]
    expected_lead = np.arange(10, 210, 10, dtype="int32")
    if lead.dtype != np.dtype("int32") or not np.array_equal(lead, expected_lead):
        raise NowcastNetInputError("NowcastNet offline output lead times are invalid")
    issue_time = _parse_time(root.attrs.get("issue_time"))
    expected_valid_times = np.asarray(
        [issue_time.replace(tzinfo=None) + timedelta(minutes=int(value)) for value in lead],
        dtype="datetime64[ns]",
    )
    if not np.array_equal(root["valid_time"][:], expected_valid_times):
        raise NowcastNetInputError("NowcastNet offline valid times differ from lead times")
    member = root["member"][:]
    if member.dtype != np.dtype("int16") or not np.array_equal(
        member, np.arange(profile.protocol.ensemble_members, dtype="int16")
    ):
        raise NowcastNetInputError("NowcastNet offline member coordinate is invalid")
    member_valid = root["member_valid_mask"][:]
    output_valid = root["output_valid_mask"][:]
    if np.any((member_valid != 0) & (member_valid != 1)) or np.any(
        (output_valid != 0) & (output_valid != 1)
    ):
        raise NowcastNetInputError("NowcastNet offline output masks are not binary")
    if not np.array_equal(output_valid, np.all(member_valid == 1, axis=0).astype("uint8")):
        raise NowcastNetInputError("NowcastNet offline common support differs from members")
    rain_rate = root["rain_rate"][:]
    valid = member_valid == 1
    if np.any(~np.isnan(rain_rate[~valid])) or np.any(~np.isfinite(rain_rate[valid])) or np.any(
        rain_rate[valid] < 0
    ):
        raise NowcastNetInputError("NowcastNet offline rain-rate missing state is invalid")
    summary = json.loads(objects["forecast/summary.json"])
    if (
        summary.get("run_id") != root.attrs.get("run_id")
        or summary.get("job_id") != root.attrs.get("job_id")
        or summary.get("model_id") != profile.model_id
        or summary.get("lead_count") != profile.protocol.output_frames
        or summary.get("lead_step_minutes") != profile.protocol.timestep_minutes
        or summary.get("random_seed") != root.attrs.get("random_seed")
        or summary.get("operational_eligible") is not False
        or summary.get("product_publication_enabled") is not False
    ):
        raise NowcastNetInputError("NowcastNet offline summary identity is invalid")
    return {
        "object_count": len(objects),
        "size_bytes": sum(len(value) for value in objects.values()),
        "member_count": expected_shape[0],
        "lead_count": expected_shape[1],
        "maximum_forecast_rate_mm_h": float(np.max(rain_rate[valid])),
        "common_valid_coverage_ratio": float(np.mean(output_valid == 1)),
    }


def _validate_input_arrays(
    rate: np.ndarray,
    valid: np.ndarray,
    lat: np.ndarray,
    lon: np.ndarray,
    profile: NowcastNetProfile,
) -> None:
    expected = (
        profile.protocol.input_frames,
        profile.protocol.input_height,
        profile.protocol.input_width,
    )
    if rate.shape != expected or valid.shape != expected:
        raise NowcastNetInputError("NowcastNet offline input arrays have invalid shapes")
    if lat.shape != (expected[1],) or lon.shape != (expected[2],):
        raise NowcastNetInputError("NowcastNet offline coordinates have invalid shapes")
    if rate.dtype != np.dtype("float32") or valid.dtype != np.dtype("uint8"):
        raise NowcastNetInputError("NowcastNet offline input arrays have invalid dtypes")
    if np.any((valid != 0) & (valid != 1)):
        raise NowcastNetInputError("NowcastNet offline input valid mask is not binary")
    if profile.protocol.missing_policy == "reject_any_missing" and not np.all(valid == 1):
        raise NowcastNetInputError("RP-026 NowcastNet rejects any missing input cell")
    if np.any(~np.isfinite(rate[valid == 1])) or np.any(rate[valid == 1] < 0):
        raise NowcastNetInputError("NowcastNet offline valid input rain rates are invalid")
    for name, coordinate in (("latitude", lat), ("longitude", lon)):
        if not np.all(np.isfinite(coordinate)) or not np.all(np.diff(coordinate) > 0):
            raise NowcastNetInputError(
                f"NowcastNet offline {name} coordinate must be finite and ascending"
            )


def _utc(value: datetime) -> datetime:
    if value.utcoffset() is None:
        raise NowcastNetInputError("NowcastNet timestamp must include a UTC offset")
    return value.astimezone(UTC)


def _parse_time(value: object) -> datetime:
    if not isinstance(value, str):
        raise NowcastNetInputError("NowcastNet timestamp is missing")
    try:
        parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError as exc:
        raise NowcastNetInputError("NowcastNet timestamp is invalid") from exc
    return _utc(parsed)
