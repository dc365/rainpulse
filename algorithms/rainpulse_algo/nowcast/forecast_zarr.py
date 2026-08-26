from __future__ import annotations

import json
from collections.abc import Mapping
from datetime import UTC, datetime, timedelta
from typing import Any
from uuid import UUID

import numpy as np
import zarr
from numcodecs import Blosc
from zarr.storage import MemoryStore

from rainpulse_algo.grid import RegularLatLonGrid

from .pysteps_lk import PystepsLKInputError, PystepsLKResult
from .pysteps_profile import PystepsLKProfile

CONTRACT_NAME = "rainpulse.forecast-output"
CONTRACT_VERSION = "1.1"
CONFIDENCE_KIND = "technical_forecast_quality_index_not_calibrated_probability"


def build_forecast_output_zarr_store(
    result: PystepsLKResult,
    *,
    run_id: UUID,
    job_id: UUID,
    issue_time: datetime,
    input_uri: str,
    input_asset_ids: list[UUID],
    profile: PystepsLKProfile,
    grid: RegularLatLonGrid,
    runtime_ms: int,
) -> dict[str, bytes]:
    issue_time = _utc(issue_time)
    if runtime_ms < 0:
        raise PystepsLKInputError("forecast runtime cannot be negative")
    lead_minutes = np.arange(
        profile.extrapolation.lead_step_minutes,
        profile.extrapolation.lead_step_minutes * (profile.extrapolation.lead_count + 1),
        profile.extrapolation.lead_step_minutes,
        dtype="int32",
    )
    valid_times = np.asarray(
        [issue_time.replace(tzinfo=None) + timedelta(minutes=int(value)) for value in lead_minutes],
        dtype="datetime64[ns]",
    )
    motion_valid_fraction = float(np.mean(result.motion_valid_mask == 1))
    summary = {
        "schema_version": "1.0",
        "run_id": str(run_id),
        "job_id": str(job_id),
        "issue_time": issue_time.isoformat(),
        "grid_id": grid.grid_id,
        "model_id": profile.model_id,
        "model_version": profile.model_version,
        "config_version": profile.profile_version,
        "input_uri": input_uri,
        "input_asset_ids": [str(value) for value in input_asset_ids],
        "lead_count": profile.extrapolation.lead_count,
        "lead_step_minutes": profile.extrapolation.lead_step_minutes,
        "valid_from": (issue_time + timedelta(minutes=5)).isoformat(),
        "valid_to": (issue_time + timedelta(minutes=120)).isoformat(),
        "motion_fallback_used": result.motion_fallback_used,
        "motion_fallback_reason": result.motion_fallback_reason,
        "motion_feature_count": result.motion_feature_count,
        "motion_valid_fraction": motion_valid_fraction,
        "missing_buffer_pixels": profile.motion.missing_buffer_pixels,
        "trackable_rain_pixel_count": result.trackable_rain_pixel_count,
        "global_translation_x_pixels_per_step": result.global_translation_pixels_per_step[0],
        "global_translation_y_pixels_per_step": result.global_translation_pixels_per_step[1],
        "mean_motion_u_m_s": float(np.mean(result.motion_u)),
        "mean_motion_v_m_s": float(np.mean(result.motion_v)),
        "first_lead_valid_coverage_ratio": float(np.mean(result.output_valid_mask[0])),
        "last_lead_valid_coverage_ratio": float(np.mean(result.output_valid_mask[-1])),
        "maximum_forecast_rate_mm_h": _finite_max(result.rain_rate),
        "baseline_models": list(profile.extrapolation.baselines),
        "missing_policy": profile.motion.missing_policy,
        "confidence_kind": CONFIDENCE_KIND,
        "runtime_ms": runtime_ms,
    }

    store = MemoryStore()
    root = zarr.group(store=store, overwrite=True)
    root.attrs.update(
        {
            "contract_name": CONTRACT_NAME,
            "contract_version": CONTRACT_VERSION,
            "run_id": str(run_id),
            "job_id": str(job_id),
            "model_id": profile.model_id,
            "model_version": profile.model_version,
            "config_version": profile.profile_version,
            "input_uri": input_uri,
            "input_asset_ids": [str(value) for value in input_asset_ids],
            "issue_time": issue_time.isoformat(),
            "grid_id": grid.grid_id,
            "grid_config_version": grid.config_version,
            "coordinate_sha256": grid.coordinate_sha256,
            "grid_metric_version": grid.metric().version,
            "pysteps_version": profile.pysteps_version,
            "opencv_version": profile.opencv_version,
            "motion_method": profile.motion.method,
            "extrapolation_method": profile.extrapolation.method,
            "missing_policy": profile.motion.missing_policy,
            "missing_buffer_pixels": profile.motion.missing_buffer_pixels,
            "motion_fallback_used": result.motion_fallback_used,
            "motion_fallback_reason": result.motion_fallback_reason,
            "motion_feature_count": result.motion_feature_count,
            "motion_valid_fraction": motion_valid_fraction,
            "confidence_kind": CONFIDENCE_KIND,
            "baseline_models": list(profile.extrapolation.baselines),
            "runtime_ms": runtime_ms,
            "created_at": datetime.now(UTC).isoformat(),
        }
    )
    root.create_dataset("member", data=np.asarray([0], dtype="int16"))
    root.create_dataset("lead_time", data=lead_minutes).attrs["units"] = "minute"
    root.create_dataset("valid_time", data=valid_times).attrs.update(
        {"standard_name": "time", "timezone": "UTC"}
    )
    for name, values in (("lat", grid.latitude), ("lon", grid.longitude)):
        root.create_dataset(name, data=values, chunks=(min(512, len(values)),))

    compressor = Blosc(cname="zstd", clevel=3, shuffle=Blosc.BITSHUFFLE)
    fields = {
        "rain_rate": result.rain_rate,
        "accum_60": result.accum_60,
        "accum_120": result.accum_120,
        "output_valid_mask": result.output_valid_mask,
        "confidence": result.confidence,
        "motion_u": result.motion_u,
        "motion_v": result.motion_v,
        "motion_valid_mask": result.motion_valid_mask,
        "persistence_rain_rate": result.persistence_rain_rate,
        "persistence_valid_mask": result.persistence_valid_mask,
        "translation_rain_rate": result.translation_rain_rate,
        "translation_valid_mask": result.translation_valid_mask,
    }
    for name, values in fields.items():
        chunks = _chunks(values.shape)
        array = root.create_dataset(
            name,
            data=values,
            chunks=chunks,
            compressor=compressor,
            fill_value=np.nan if np.issubdtype(values.dtype, np.floating) else 0,
        )
        array.attrs.update(_attributes(name))
    store["forecast/summary.json"] = json.dumps(
        summary, separators=(",", ":"), sort_keys=True
    ).encode()
    zarr.consolidate_metadata(store)
    objects = {str(key): bytes(value) for key, value in store.items()}
    validate_forecast_output_zarr_store(objects)
    return objects


def validate_forecast_output_zarr_store(
    objects: Mapping[str, bytes],
) -> dict[str, Any]:
    required = {".zgroup", ".zattrs", "forecast/summary.json"}
    if not required <= objects.keys():
        raise PystepsLKInputError("ForecastOutput Zarr is missing metadata or summary")
    store = MemoryStore()
    store.update({key: bytes(value) for key, value in objects.items()})
    root = zarr.open_group(store=store, mode="r")
    if root.attrs.get("contract_name") != CONTRACT_NAME:
        raise PystepsLKInputError("ForecastOutput contract name is invalid")
    if root.attrs.get("contract_version") != CONTRACT_VERSION:
        raise PystepsLKInputError("ForecastOutput contract version is invalid")
    lead = root["lead_time"][:]
    expected_lead = np.arange(5, 125, 5, dtype="int32")
    if lead.dtype != np.dtype("int32") or not np.array_equal(lead, expected_lead):
        raise PystepsLKInputError("ForecastOutput lead times must be 5..120 minutes")
    if root["member"].dtype != np.dtype("int16") or not np.array_equal(
        root["member"][:], np.asarray([0], dtype="int16")
    ):
        raise PystepsLKInputError("ForecastOutput deterministic member is invalid")
    issue_time = _parse_time(str(root.attrs.get("issue_time")))
    expected_times = np.asarray(
        [issue_time.replace(tzinfo=None) + timedelta(minutes=int(value)) for value in lead],
        dtype="datetime64[ns]",
    )
    if not np.array_equal(root["valid_time"][:], expected_times):
        raise PystepsLKInputError("ForecastOutput valid times differ from lead times")
    latitude = root["lat"][:]
    longitude = root["lon"][:]
    if latitude.dtype != np.dtype("float32") or longitude.dtype != np.dtype("float32"):
        raise PystepsLKInputError("ForecastOutput coordinates must be float32")
    shape = (24, len(latitude), len(longitude))
    expected = {
        "rain_rate": ((1, *shape), np.dtype("float32")),
        "accum_60": ((1, len(latitude), len(longitude)), np.dtype("float32")),
        "accum_120": ((1, len(latitude), len(longitude)), np.dtype("float32")),
        "output_valid_mask": (shape, np.dtype("uint8")),
        "confidence": (shape, np.dtype("float32")),
        "motion_u": ((len(latitude), len(longitude)), np.dtype("float32")),
        "motion_v": ((len(latitude), len(longitude)), np.dtype("float32")),
        "persistence_rain_rate": (shape, np.dtype("float32")),
        "persistence_valid_mask": (shape, np.dtype("uint8")),
        "translation_rain_rate": (shape, np.dtype("float32")),
        "translation_valid_mask": (shape, np.dtype("uint8")),
    }
    for name, (field_shape, dtype) in expected.items():
        if name not in root or root[name].shape != field_shape or root[name].dtype != dtype:
            raise PystepsLKInputError(f"ForecastOutput field {name} has invalid shape or dtype")
    if "motion_valid_mask" in root:
        motion_mask = root["motion_valid_mask"][:]
        if motion_mask.shape != latitude.shape + longitude.shape:
            raise PystepsLKInputError("ForecastOutput motion-valid mask has invalid shape")
        if motion_mask.dtype != np.dtype("uint8") or np.any(
            (motion_mask != 0) & (motion_mask != 1)
        ):
            raise PystepsLKInputError("ForecastOutput motion-valid mask is not binary")
    else:
        motion_mask = np.ones((len(latitude), len(longitude)), dtype="uint8")
    masks = {
        "output_valid_mask": root["output_valid_mask"][:],
        "persistence_valid_mask": root["persistence_valid_mask"][:],
        "translation_valid_mask": root["translation_valid_mask"][:],
    }
    for name, mask in masks.items():
        if np.any((mask != 0) & (mask != 1)):
            raise PystepsLKInputError(f"ForecastOutput {name} is not binary")
    for field, mask_name in (
        ("rain_rate", "output_valid_mask"),
        ("persistence_rain_rate", "persistence_valid_mask"),
        ("translation_rain_rate", "translation_valid_mask"),
    ):
        values = root[field][:]
        if values.ndim == 4:
            values = values[0]
        valid = masks[mask_name] == 1
        if np.any(~np.isnan(values[~valid])):
            raise PystepsLKInputError(f"ForecastOutput invalid {field} cells are not NaN")
        if np.any(~np.isfinite(values[valid])) or np.any(values[valid] < 0):
            raise PystepsLKInputError(f"ForecastOutput valid {field} cells are invalid")
    output_valid = masks["output_valid_mask"] == 1
    confidence = root["confidence"][:]
    if np.any(~np.isnan(confidence[~output_valid])) or np.any(
        ~np.isfinite(confidence[output_valid])
    ):
        raise PystepsLKInputError("ForecastOutput confidence missing state is invalid")
    if np.any((confidence[output_valid] < 0) | (confidence[output_valid] > 1)):
        raise PystepsLKInputError("ForecastOutput confidence is outside [0, 1]")
    confidence_kind = root.attrs.get("confidence_kind")
    if confidence_kind not in {None, CONFIDENCE_KIND}:
        raise PystepsLKInputError("ForecastOutput confidence semantic is invalid")
    if np.any(~np.isfinite(root["motion_u"][:])) or np.any(~np.isfinite(root["motion_v"][:])):
        raise PystepsLKInputError("ForecastOutput motion vectors must be finite")
    for name, count in (("accum_60", 12), ("accum_120", 24)):
        values = root[name][0]
        required_valid = np.all(output_valid[:count], axis=0)
        if np.any(~np.isnan(values[~required_valid])) or np.any(
            ~np.isfinite(values[required_valid])
        ):
            raise PystepsLKInputError(f"ForecastOutput {name} support is invalid")
        expected_accum = np.sum(root["rain_rate"][0, :count], axis=0) * (5.0 / 60.0)
        if not np.allclose(
            values[required_valid], expected_accum[required_valid], rtol=1e-5, atol=1e-6
        ):
            raise PystepsLKInputError(f"ForecastOutput {name} differs from rain rates")
    summary = json.loads(objects["forecast/summary.json"])
    if (
        summary.get("run_id") != root.attrs.get("run_id")
        or summary.get("job_id") != root.attrs.get("job_id")
        or summary.get("model_id") != root.attrs.get("model_id")
        or summary.get("model_version") != root.attrs.get("model_version")
        or summary.get("config_version") != root.attrs.get("config_version")
        or summary.get("lead_count") != 24
        or summary.get("lead_step_minutes") != 5
    ):
        raise PystepsLKInputError("ForecastOutput summary identity is invalid")
    if "motion_valid_mask" in root:
        actual_fraction = float(np.mean(motion_mask == 1))
        if not np.isclose(summary.get("motion_valid_fraction"), actual_fraction):
            raise PystepsLKInputError("ForecastOutput motion-valid summary differs")
        if summary.get("confidence_kind") != CONFIDENCE_KIND:
            raise PystepsLKInputError("ForecastOutput confidence kind differs from contract")
    return {
        "shape": (1, *shape),
        "lead_count": 24,
        "first_lead_valid_coverage_ratio": float(np.mean(output_valid[0])),
        "last_lead_valid_coverage_ratio": float(np.mean(output_valid[-1])),
        "maximum_forecast_rate_mm_h": _finite_max(root["rain_rate"][:]),
        "motion_fallback_used": bool(summary["motion_fallback_used"]),
        "motion_fallback_reason": summary.get("motion_fallback_reason"),
        "motion_feature_count": int(summary.get("motion_feature_count", 0)),
        "motion_valid_fraction": float(np.mean(motion_mask == 1)),
        "object_count": len(objects),
        "size_bytes": sum(len(value) for value in objects.values()),
    }


def _chunks(shape: tuple[int, ...]) -> tuple[int, ...]:
    if len(shape) == 4:
        return 1, 1, min(128, shape[-2]), min(256, shape[-1])
    if len(shape) == 3:
        return 1, min(128, shape[-2]), min(256, shape[-1])
    return min(128, shape[-2]), min(256, shape[-1])


def _attributes(name: str) -> dict[str, Any]:
    if "rain_rate" in name:
        return {"units": "mm h-1", "missing_value": "NaN"}
    if name.startswith("accum_"):
        return {"units": "mm", "missing_value": "NaN"}
    if name in {"motion_u", "motion_v"}:
        return {"units": "m s-1"}
    if name == "motion_valid_mask":
        return {
            "units": "1",
            "long_name": "motion estimation domain after missing-boundary buffering",
        }
    if name == "confidence":
        return {
            "units": "1",
            "missing_value": "NaN",
            "long_name": "technical forecast quality index",
            "comment": "Not a calibrated forecast probability.",
        }
    return {"units": "1", "missing_value": 0}


def _finite_max(values: np.ndarray) -> float:
    finite = values[np.isfinite(values)]
    return float(np.max(finite)) if finite.size else 0.0


def _utc(value: datetime) -> datetime:
    if value.tzinfo is None:
        raise PystepsLKInputError("forecast issue time must include a UTC offset")
    return value.astimezone(UTC)


def _parse_time(value: str) -> datetime:
    try:
        parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError as exc:
        raise PystepsLKInputError("ForecastOutput issue time is invalid") from exc
    return _utc(parsed)
