from __future__ import annotations

import json
import warnings
from collections.abc import Mapping
from datetime import UTC, datetime, timedelta
from typing import Any
from uuid import UUID

import numpy as np
import zarr
from numcodecs import Blosc
from zarr.storage import MemoryStore

from rainpulse_algo.grid import RegularLatLonGrid

from .forecast_zarr import CONFIDENCE_KIND
from .pysteps_steps import PystepsStepsInputError, PystepsStepsResult
from .steps_profile import PystepsStepsProfile

CONTRACT_NAME = "rainpulse.forecast-output"
CONTRACT_VERSION = "1.2"


def build_ensemble_forecast_output_zarr_store(
    result: PystepsStepsResult,
    *,
    run_id: UUID,
    job_id: UUID,
    issue_time: datetime,
    input_uri: str,
    input_asset_ids: list[UUID],
    profile: PystepsStepsProfile,
    grid: RegularLatLonGrid,
    runtime_ms: int,
) -> dict[str, bytes]:
    issue_time = _utc(issue_time)
    if runtime_ms < 0:
        raise PystepsStepsInputError("ensemble runtime cannot be negative")
    if result.random_seed != profile.ensemble.random_seed:
        raise PystepsStepsInputError("ensemble result seed differs from the frozen profile")
    member_count = profile.ensemble.member_count
    lead_minutes = np.arange(5, 125, 5, dtype="int32")
    valid_times = np.asarray(
        [issue_time.replace(tzinfo=None) + timedelta(minutes=int(value)) for value in lead_minutes],
        dtype="datetime64[ns]",
    )
    deterministic = result.deterministic
    motion_valid_fraction = float(np.mean(deterministic.motion_valid_mask == 1))
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
        "member_count": member_count,
        "output_support_policy": profile.support.output_support_policy,
        "minimum_valid_members": profile.support.minimum_valid_members,
        "lead_count": 24,
        "lead_step_minutes": 5,
        "random_seed": result.random_seed,
        "ensemble_fallback_used": result.ensemble_fallback_used,
        "ensemble_fallback_reason": result.ensemble_fallback_reason,
        "probability_calibration_status": (
            profile.probability_products.calibration_status
        ),
        "probability_event_operator": profile.probability_products.event_operator,
        "probability_thresholds_mm_h": list(
            profile.probability_products.rain_rate_thresholds_mm_h
        ),
        "quantiles": list(profile.probability_products.quantiles),
        "nominal_pixel_spacing_km": result.nominal_pixel_spacing_km,
        "motion_fallback_used": deterministic.motion_fallback_used,
        "motion_fallback_reason": deterministic.motion_fallback_reason,
        "motion_feature_count": deterministic.motion_feature_count,
        "motion_valid_fraction": motion_valid_fraction,
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
            "ensemble_member_count": member_count,
            "random_seed": result.random_seed,
            "ensemble_fallback_used": result.ensemble_fallback_used,
            "ensemble_fallback_reason": result.ensemble_fallback_reason,
            "input_missing_policy": profile.support.input_missing_policy,
            "output_support_policy": profile.support.output_support_policy,
            "minimum_valid_members": profile.support.minimum_valid_members,
            "probability_calibration_status": (
                profile.probability_products.calibration_status
            ),
            "probability_event_operator": profile.probability_products.event_operator,
            "probability_thresholds_mm_h": list(
                profile.probability_products.rain_rate_thresholds_mm_h
            ),
            "quantiles": list(profile.probability_products.quantiles),
            "nominal_pixel_spacing_km": result.nominal_pixel_spacing_km,
            "motion_fallback_used": deterministic.motion_fallback_used,
            "motion_fallback_reason": deterministic.motion_fallback_reason,
            "motion_feature_count": deterministic.motion_feature_count,
            "motion_valid_fraction": motion_valid_fraction,
            "confidence_kind": CONFIDENCE_KIND,
            "baseline_models": ["persistence", "translation"],
            "runtime_ms": runtime_ms,
            "created_at": datetime.now(UTC).isoformat(),
        }
    )
    root.create_dataset("member", data=np.arange(member_count, dtype="int16"))
    root.create_dataset("lead_time", data=lead_minutes).attrs["units"] = "minute"
    root.create_dataset("valid_time", data=valid_times).attrs.update(
        {"standard_name": "time", "timezone": "UTC"}
    )
    for name, values in (("lat", grid.latitude), ("lon", grid.longitude)):
        root.create_dataset(name, data=values, chunks=(min(512, len(values)),))

    confidence = np.asarray(deterministic.confidence, dtype="float32").copy()
    confidence[result.output_valid_mask != 1] = np.nan
    fields: dict[str, np.ndarray] = {
        "rain_rate": result.rain_rate,
        "member_valid_mask": result.member_valid_mask,
        "accum_60": result.accum_60,
        "accum_120": result.accum_120,
        "output_valid_mask": result.output_valid_mask,
        "confidence": confidence,
        "motion_u": deterministic.motion_u,
        "motion_v": deterministic.motion_v,
        "motion_valid_mask": deterministic.motion_valid_mask,
        "persistence_rain_rate": deterministic.persistence_rain_rate,
        "persistence_valid_mask": deterministic.persistence_valid_mask,
        "translation_rain_rate": deterministic.translation_rain_rate,
        "translation_valid_mask": deterministic.translation_valid_mask,
    }
    for threshold, values in result.probability_exceedance.items():
        fields[_probability_name(threshold)] = values
    for quantile, values in result.quantiles.items():
        fields[_quantile_name(quantile)] = values

    compressor = Blosc(cname="zstd", clevel=3, shuffle=Blosc.BITSHUFFLE)
    for name, values in fields.items():
        array = root.create_dataset(
            name,
            data=values,
            chunks=_chunks(values.shape),
            compressor=compressor,
            fill_value=np.nan if np.issubdtype(values.dtype, np.floating) else 0,
        )
        array.attrs.update(_attributes(name))
    store["forecast/summary.json"] = json.dumps(
        summary, separators=(",", ":"), sort_keys=True
    ).encode()
    zarr.consolidate_metadata(store)
    objects = {str(key): bytes(value) for key, value in store.items()}
    validate_ensemble_forecast_output_zarr_store(objects)
    return objects


def validate_ensemble_forecast_output_zarr_store(
    objects: Mapping[str, bytes],
) -> dict[str, Any]:
    required = {".zgroup", ".zattrs", "forecast/summary.json"}
    if not required <= objects.keys():
        raise PystepsStepsInputError("ensemble ForecastOutput is missing metadata or summary")
    store = MemoryStore()
    store.update({key: bytes(value) for key, value in objects.items()})
    root = zarr.open_group(store=store, mode="r")
    if root.attrs.get("contract_name") != CONTRACT_NAME:
        raise PystepsStepsInputError("ensemble ForecastOutput contract name is invalid")
    if root.attrs.get("contract_version") != CONTRACT_VERSION:
        raise PystepsStepsInputError("ensemble ForecastOutput contract version is invalid")
    members = root["member"][:]
    if (
        members.dtype != np.dtype("int16")
        or len(members) < 2
        or not np.array_equal(members, np.arange(len(members), dtype="int16"))
    ):
        raise PystepsStepsInputError("ensemble ForecastOutput members are invalid")
    leads = root["lead_time"][:]
    if leads.dtype != np.dtype("int32") or not np.array_equal(
        leads, np.arange(5, 125, 5, dtype="int32")
    ):
        raise PystepsStepsInputError("ensemble ForecastOutput leads must be 5..120 minutes")
    issue_time = _parse_time(str(root.attrs.get("issue_time")))
    expected_times = np.asarray(
        [issue_time.replace(tzinfo=None) + timedelta(minutes=int(value)) for value in leads],
        dtype="datetime64[ns]",
    )
    if not np.array_equal(root["valid_time"][:], expected_times):
        raise PystepsStepsInputError("ensemble ForecastOutput valid times are invalid")
    latitude = root["lat"][:]
    longitude = root["lon"][:]
    if latitude.dtype != np.dtype("float32") or longitude.dtype != np.dtype("float32"):
        raise PystepsStepsInputError("ensemble ForecastOutput coordinates must be float32")

    lead_shape = (24, len(latitude), len(longitude))
    member_shape = (len(members), *lead_shape)
    expected = {
        "rain_rate": (member_shape, np.dtype("float32")),
        "member_valid_mask": (member_shape, np.dtype("uint8")),
        "accum_60": ((len(members), len(latitude), len(longitude)), np.dtype("float32")),
        "accum_120": ((len(members), len(latitude), len(longitude)), np.dtype("float32")),
        "output_valid_mask": (lead_shape, np.dtype("uint8")),
        "confidence": (lead_shape, np.dtype("float32")),
        "motion_u": ((len(latitude), len(longitude)), np.dtype("float32")),
        "motion_v": ((len(latitude), len(longitude)), np.dtype("float32")),
        "motion_valid_mask": ((len(latitude), len(longitude)), np.dtype("uint8")),
        "persistence_rain_rate": (lead_shape, np.dtype("float32")),
        "persistence_valid_mask": (lead_shape, np.dtype("uint8")),
        "translation_rain_rate": (lead_shape, np.dtype("float32")),
        "translation_valid_mask": (lead_shape, np.dtype("uint8")),
    }
    thresholds = tuple(float(value) for value in root.attrs["probability_thresholds_mm_h"])
    quantiles = tuple(float(value) for value in root.attrs["quantiles"])
    for threshold in thresholds:
        expected[_probability_name(threshold)] = (lead_shape, np.dtype("float32"))
    for quantile in quantiles:
        expected[_quantile_name(quantile)] = (lead_shape, np.dtype("float32"))
    for name, (shape, dtype) in expected.items():
        if name not in root or root[name].shape != shape or root[name].dtype != dtype:
            raise PystepsStepsInputError(
                f"ensemble ForecastOutput field {name} has invalid shape or dtype"
            )

    output_valid = root["output_valid_mask"][:] == 1
    member_valid_raw = root["member_valid_mask"][:]
    member_valid = member_valid_raw == 1
    if np.any(~np.isin(root["output_valid_mask"][:], (0, 1))):
        raise PystepsStepsInputError("ensemble output support is not binary")
    if np.any(~np.isin(member_valid_raw, (0, 1))):
        raise PystepsStepsInputError("ensemble member support is not binary")
    output_support_policy = root.attrs.get("output_support_policy")
    minimum_valid_members = root.attrs.get("minimum_valid_members")
    if output_support_policy == "deterministic_support_intersect_all_members_finite":
        if minimum_valid_members is not None:
            raise PystepsStepsInputError(
                "all-member ensemble support cannot define a minimum member count"
            )
        expected_output_valid = np.all(member_valid, axis=0)
    elif output_support_policy == "deterministic_support_minimum_members_finite":
        if (
            not isinstance(minimum_valid_members, int)
            or not 2 <= minimum_valid_members <= len(members)
        ):
            raise PystepsStepsInputError(
                "minimum-member ensemble support has an invalid member count"
            )
        expected_output_valid = (
            np.count_nonzero(member_valid, axis=0) >= minimum_valid_members
        )
    else:
        raise PystepsStepsInputError("ensemble output support policy is invalid")
    if not np.array_equal(output_valid, expected_output_valid):
        raise PystepsStepsInputError(
            "ensemble output support differs from the configured member policy"
        )
    rates = root["rain_rate"][:]
    if np.any(~np.isnan(rates[~member_valid])):
        raise PystepsStepsInputError("invalid ensemble cells are not NaN")
    if np.any(~np.isfinite(rates[member_valid])) or np.any(rates[member_valid] < 0.0):
        raise PystepsStepsInputError("valid ensemble cells contain invalid rain rates")

    for name, count in (("accum_60", 12), ("accum_120", 24)):
        values = root[name][:]
        valid = np.all(member_valid[:, :count], axis=1)
        expected_values = np.sum(
            np.where(member_valid[:, :count], rates[:, :count], 0.0), axis=1
        ) * np.float32(5.0 / 60.0)
        if np.any(~np.isnan(values[~valid])) or not np.allclose(
            values[valid], expected_values[valid], rtol=1e-5, atol=1e-6
        ):
            raise PystepsStepsInputError(f"ensemble ForecastOutput {name} is invalid")

    for threshold in thresholds:
        name = _probability_name(threshold)
        values = root[name][:]
        valid_count = np.count_nonzero(member_valid, axis=0)
        exceedance_count = np.count_nonzero(
            member_valid & (rates > threshold), axis=0
        )
        expected_values = np.divide(
            exceedance_count,
            valid_count,
            out=np.zeros(valid_count.shape, dtype="float32"),
            where=valid_count > 0,
        )
        if not _masked_allclose(values, expected_values, output_valid):
            raise PystepsStepsInputError(f"ensemble probability {name} differs from members")
    for quantile in quantiles:
        name = _quantile_name(quantile)
        values = root[name][:]
        with warnings.catch_warnings():
            warnings.simplefilter("ignore", RuntimeWarning)
            expected_values = np.nanquantile(
                np.where(member_valid, rates, np.nan), quantile, axis=0
            )
        if not _masked_allclose(values, expected_values, output_valid):
            raise PystepsStepsInputError(f"ensemble quantile {name} differs from members")

    if root.attrs.get("probability_event_operator") != "greater_than":
        raise PystepsStepsInputError("ensemble probability event operator is invalid")
    if root.attrs.get("ensemble_member_count") != len(members):
        raise PystepsStepsInputError("ensemble member-count attribute is invalid")
    if root.attrs.get("input_missing_policy") not in {
        "reject_any_missing",
        "dry_floor_working_copy_preserve_deterministic_support",
    }:
        raise PystepsStepsInputError("ensemble input missing policy is invalid")
    if (
        root.attrs.get("probability_calibration_status")
        != "raw_ensemble_relative_frequency_uncalibrated"
    ):
        raise PystepsStepsInputError("ensemble probability calibration status is invalid")
    confidence = root["confidence"][:]
    if np.any(~np.isnan(confidence[~output_valid])) or np.any(
        ~np.isfinite(confidence[output_valid])
    ):
        raise PystepsStepsInputError("ensemble technical confidence support is invalid")
    summary = json.loads(objects["forecast/summary.json"])
    if (
        summary.get("run_id") != root.attrs.get("run_id")
        or summary.get("job_id") != root.attrs.get("job_id")
        or summary.get("member_count") != len(members)
        or summary.get("random_seed") != root.attrs.get("random_seed")
        or summary.get("lead_count") != 24
    ):
        raise PystepsStepsInputError("ensemble ForecastOutput summary identity is invalid")
    return {
        "shape": member_shape,
        "member_count": len(members),
        "lead_count": 24,
        "random_seed": int(root.attrs["random_seed"]),
        "probability_calibration_status": root.attrs["probability_calibration_status"],
        "first_lead_valid_coverage_ratio": float(np.mean(output_valid[0])),
        "last_lead_valid_coverage_ratio": float(np.mean(output_valid[-1])),
        "object_count": len(objects),
        "size_bytes": sum(len(value) for value in objects.values()),
    }


def _masked_allclose(values: np.ndarray, expected: np.ndarray, valid: np.ndarray) -> bool:
    return bool(
        np.all(np.isnan(values[~valid]))
        and np.all(np.isfinite(values[valid]))
        and np.allclose(values[valid], expected[valid], rtol=1e-5, atol=1e-6)
    )


def _probability_name(threshold: float) -> str:
    if not float(threshold).is_integer():
        raise PystepsStepsInputError("RP-022 probability thresholds must be integer mm/h")
    return f"prob_gt_{int(threshold)}"


def _quantile_name(quantile: float) -> str:
    percentile = int(round(quantile * 100))
    if not np.isclose(quantile, percentile / 100.0):
        raise PystepsStepsInputError("RP-022 quantiles must map to integer percentiles")
    return f"p{percentile}"


def _chunks(shape: tuple[int, ...]) -> tuple[int, ...]:
    if len(shape) == 4:
        return 1, 1, min(128, shape[-2]), min(256, shape[-1])
    if len(shape) == 3:
        return 1, min(128, shape[-2]), min(256, shape[-1])
    return min(128, shape[-2]), min(256, shape[-1])


def _attributes(name: str) -> dict[str, Any]:
    if name.startswith("prob_gt_"):
        return {
            "units": "1",
            "missing_value": "NaN",
            "calibration_status": "raw_ensemble_relative_frequency_uncalibrated",
        }
    if name == "rain_rate" or name.endswith("_rain_rate") or name in {"p10", "p50", "p90"}:
        return {"units": "mm h-1", "missing_value": "NaN"}
    if name.startswith("accum_"):
        return {"units": "mm", "missing_value": "NaN"}
    if name in {"motion_u", "motion_v"}:
        return {"units": "m s-1"}
    if name == "confidence":
        return {
            "units": "1",
            "missing_value": "NaN",
            "comment": "Technical forecast quality index, not calibrated probability.",
        }
    return {"units": "1", "missing_value": 0}


def _utc(value: datetime) -> datetime:
    if value.tzinfo is None:
        raise PystepsStepsInputError("ensemble issue time must include a UTC offset")
    return value.astimezone(UTC)


def _parse_time(value: str) -> datetime:
    try:
        parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError as exc:
        raise PystepsStepsInputError("ensemble issue time is invalid") from exc
    return _utc(parsed)
