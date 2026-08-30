from __future__ import annotations

import json
import math
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Literal
from uuid import UUID

import numpy as np
import yaml
import zarr
from pyproj import Geod
from zarr.storage import MemoryStore

from rainpulse_algo.nowcast.forecast_zarr import validate_forecast_output_zarr_store

from .deterministic import (
    DeterministicForecast,
    score_accumulation_forecasts,
    score_deterministic_forecasts,
)


class OperationalVerificationInputError(ValueError):
    """Raised when forecast and truth artifacts cannot be compared safely."""


@dataclass(frozen=True)
class OperationalVerificationProfile:
    profile_version: str
    forecast_contract_version: Literal["1.1"]
    truth_contract_version: Literal["1.2"]
    result_contract_version: Literal["1.0"]
    lead_minutes: tuple[int, ...]
    models: tuple[str, ...]
    thresholds_mm_h: tuple[float, ...]
    fss_windows_km: tuple[float, ...]
    accumulation_windows_minutes: tuple[int, ...]
    accumulation_thresholds_mm: tuple[float, ...]
    validity_domain: Literal["common"]
    promotion_eligible: bool


def load_operational_verification_profile(
    path: Path,
) -> OperationalVerificationProfile:
    raw = yaml.safe_load(path.read_text())
    try:
        profile = OperationalVerificationProfile(
            profile_version=str(raw["profile_version"]),
            forecast_contract_version=str(raw["forecast_contract_version"]),  # type: ignore[arg-type]
            truth_contract_version=str(raw["truth_contract_version"]),  # type: ignore[arg-type]
            result_contract_version=str(raw["result_contract_version"]),  # type: ignore[arg-type]
            lead_minutes=tuple(int(value) for value in raw["lead_minutes"]),
            models=tuple(str(value) for value in raw["models"]),
            thresholds_mm_h=tuple(float(value) for value in raw["thresholds_mm_h"]),
            fss_windows_km=tuple(float(value) for value in raw["fss_windows_km"]),
            accumulation_windows_minutes=tuple(
                int(value) for value in raw["accumulation_windows_minutes"]
            ),
            accumulation_thresholds_mm=tuple(
                float(value) for value in raw["accumulation_thresholds_mm"]
            ),
            validity_domain=str(raw["validity_domain"]),  # type: ignore[arg-type]
            promotion_eligible=bool(raw["promotion_eligible"]),
        )
    except (KeyError, TypeError, ValueError) as error:
        raise OperationalVerificationInputError(
            f"invalid operational verification profile {path}: {error}"
        ) from error
    expected_leads = tuple(range(5, 125, 5))
    if raw.get("schema_version") != "1.0" or raw.get("lifecycle") != "automatic_verification":
        raise OperationalVerificationInputError("unsupported verification profile identity")
    if (
        profile.forecast_contract_version != "1.1"
        or profile.truth_contract_version != "1.2"
        or profile.result_contract_version != "1.0"
        or profile.lead_minutes != expected_leads
        or profile.models != ("lk", "persistence", "translation")
        or profile.validity_domain != "common"
        or profile.promotion_eligible
    ):
        raise OperationalVerificationInputError("verification profile changes the RP-031 boundary")
    if (
        not profile.thresholds_mm_h
        or not profile.fss_windows_km
        or profile.accumulation_windows_minutes != (60, 120)
        or not profile.accumulation_thresholds_mm
    ):
        raise OperationalVerificationInputError("verification profile score dimensions are invalid")
    return profile


def build_operational_verification_result(
    forecast_objects: Mapping[str, bytes],
    truth_object_sets: Sequence[Mapping[str, bytes]],
    *,
    profile: OperationalVerificationProfile,
    run_id: UUID,
    job_id: UUID,
    forecast_uri: str,
    truth_uris: Sequence[str],
) -> dict[str, bytes]:
    validate_forecast_output_zarr_store(forecast_objects)
    forecast = _open(forecast_objects)
    _validate_forecast_identity(forecast, profile, run_id)
    if len(truth_object_sets) != 24 or len(truth_uris) != 24:
        raise OperationalVerificationInputError("verification requires exactly 24 truth frames")

    latitude = np.asarray(forecast["lat"][:], dtype="float32")
    longitude = np.asarray(forecast["lon"][:], dtype="float32")
    expected_valid_times = forecast["valid_time"][:]
    truth_rates: list[np.ndarray] = []
    truth_masks: list[np.ndarray] = []
    truth_ids: list[str] = []
    truth_operational = True
    for index, objects in enumerate(truth_object_sets):
        truth = _open(objects)
        analysis_id, operational = _validate_truth(
            truth,
            profile=profile,
            grid_id=str(forecast.attrs["grid_id"]),
            expected_valid_time=expected_valid_times[index],
            latitude=latitude,
            longitude=longitude,
        )
        truth_ids.append(analysis_id)
        truth_operational &= operational
        truth_rates.append(np.asarray(truth["RATE_QPE"][:], dtype="float32"))
        truth_masks.append(np.asarray(truth["VALID_MASK"][:], dtype="uint8"))
    if len(set(truth_ids)) != 24:
        raise OperationalVerificationInputError("truth analysis IDs must be unique")

    truth_rate = np.stack(truth_rates)
    truth_valid = np.stack(truth_masks)
    forecasts = {
        "lk": DeterministicForecast(
            np.asarray(forecast["rain_rate"][0], dtype="float32"),
            np.asarray(forecast["output_valid_mask"][:], dtype="uint8"),
        ),
        "persistence": DeterministicForecast(
            np.asarray(forecast["persistence_rain_rate"][:], dtype="float32"),
            np.asarray(forecast["persistence_valid_mask"][:], dtype="uint8"),
        ),
        "translation": DeterministicForecast(
            np.asarray(forecast["translation_rain_rate"][:], dtype="float32"),
            np.asarray(forecast["translation_valid_mask"][:], dtype="uint8"),
        ),
    }
    pixel_spacing_km = _nominal_pixel_spacing_km(latitude, longitude)
    metrics = score_deterministic_forecasts(
        truth_rate,
        truth_valid,
        forecasts,
        lead_minutes=profile.lead_minutes,
        thresholds_mm_h=profile.thresholds_mm_h,
        windows_pixels=(),
        windows_km=profile.fss_windows_km,
        pixel_spacing_km=pixel_spacing_km,
        validity_domain=profile.validity_domain,
    )
    accumulation_metrics = score_accumulation_forecasts(
        truth_rate,
        truth_valid,
        forecasts,
        lead_minutes=profile.lead_minutes,
        accumulation_windows_minutes=profile.accumulation_windows_minutes,
        thresholds_mm=profile.accumulation_thresholds_mm,
        windows_pixels=(),
        windows_km=profile.fss_windows_km,
        pixel_spacing_km=pixel_spacing_km,
        validity_domain=profile.validity_domain,
    )
    summary = {
        "contract_name": "rainpulse.forecast-verification-result",
        "contract_version": profile.result_contract_version,
        "profile_version": profile.profile_version,
        "run_id": str(run_id),
        "job_id": str(job_id),
        "forecast_uri": forecast_uri,
        "forecast_contract_version": profile.forecast_contract_version,
        "model_id": str(forecast.attrs["model_id"]),
        "model_version": str(forecast.attrs["model_version"]),
        "issue_time": str(forecast.attrs["issue_time"]),
        "grid_id": str(forecast.attrs["grid_id"]),
        "truth_kind": "radar_analysis_rate_qpe",
        "truth_contract_version": profile.truth_contract_version,
        "truth_frame_count": len(truth_ids),
        "truth_analysis_ids": truth_ids,
        "truth_uris": list(truth_uris),
        "lead_count": len(profile.lead_minutes),
        "lead_minutes": list(profile.lead_minutes),
        "models": list(profile.models),
        "metric_row_count": len(metrics),
        "accumulation_metric_row_count": len(accumulation_metrics),
        "nominal_pixel_spacing_km": pixel_spacing_km,
        "validity_domain": profile.validity_domain,
        "truth_operational_eligible": truth_operational,
        "promotion_eligible": profile.promotion_eligible,
        "headline": _headline(metrics),
    }
    return {
        "summary.json": _json_bytes(summary),
        "metrics.json": _json_bytes(metrics),
        "accumulation-metrics.json": _json_bytes(accumulation_metrics),
    }


def _open(objects: Mapping[str, bytes]) -> zarr.Group:
    store = MemoryStore()
    store.update({str(key): bytes(value) for key, value in objects.items()})
    return zarr.open_group(store=store, mode="r")


def _validate_forecast_identity(
    forecast: zarr.Group,
    profile: OperationalVerificationProfile,
    run_id: UUID,
) -> None:
    if (
        forecast.attrs.get("contract_name") != "rainpulse.forecast-output"
        or forecast.attrs.get("contract_version") != profile.forecast_contract_version
        or forecast.attrs.get("run_id") != str(run_id)
        or forecast.attrs.get("model_id") != "pysteps-lk"
    ):
        raise OperationalVerificationInputError(
            "forecast identity differs from verification request"
        )


def _validate_truth(
    truth: zarr.Group,
    *,
    profile: OperationalVerificationProfile,
    grid_id: str,
    expected_valid_time: np.datetime64,
    latitude: np.ndarray,
    longitude: np.ndarray,
) -> tuple[str, bool]:
    analysis_id = truth.attrs.get("analysis_id")
    if (
        truth.attrs.get("contract_name") != "rainpulse.radar-analysis"
        or truth.attrs.get("contract_version") != profile.truth_contract_version
        or truth.attrs.get("grid_id") != grid_id
        or not isinstance(analysis_id, str)
        or not analysis_id
        or truth.attrs.get("crs") != "EPSG:4326"
        or truth.attrs.get("registration") != "point"
    ):
        raise OperationalVerificationInputError("RadarAnalysis truth identity is invalid")
    try:
        actual_time = np.datetime64(str(truth.attrs["analysis_time"]).replace("+00:00", ""))
    except (KeyError, ValueError) as error:
        raise OperationalVerificationInputError("RadarAnalysis truth time is invalid") from error
    if actual_time != expected_valid_time:
        raise OperationalVerificationInputError(
            "RadarAnalysis truth time differs from forecast lead"
        )
    if (
        not np.array_equal(truth["lat"][:], latitude)
        or not np.array_equal(truth["lon"][:], longitude)
    ):
        raise OperationalVerificationInputError("RadarAnalysis coordinates differ from forecast")
    rate = np.asarray(truth["RATE_QPE"][:], dtype="float32")
    valid = np.asarray(truth["VALID_MASK"][:])
    if rate.shape != (len(latitude), len(longitude)) or valid.shape != rate.shape:
        raise OperationalVerificationInputError("RadarAnalysis truth fields have invalid shape")
    if np.any(~np.isin(valid, (0, 1))):
        raise OperationalVerificationInputError("RadarAnalysis truth validity mask is not binary")
    selected = valid == 1
    if np.any(~np.isfinite(rate[selected])) or np.any(rate[selected] < 0.0):
        raise OperationalVerificationInputError("RadarAnalysis valid truth rates are invalid")
    if np.any(~np.isnan(rate[~selected])):
        raise OperationalVerificationInputError("RadarAnalysis missing truth is not NaN")
    return analysis_id, bool(truth.attrs.get("operational_eligible", False))


def _nominal_pixel_spacing_km(latitude: np.ndarray, longitude: np.ndarray) -> float:
    if len(latitude) < 2 or len(longitude) < 2:
        raise OperationalVerificationInputError("verification grid must have at least two cells")
    geod = Geod(ellps="WGS84")
    reference_latitude = float(np.median(latitude))
    _, _, x_spacing = geod.inv(
        float(longitude[0]), reference_latitude, float(longitude[1]), reference_latitude
    )
    _, _, y_spacing = geod.inv(
        float(longitude[0]), float(latitude[0]), float(longitude[0]), float(latitude[1])
    )
    value = math.sqrt(abs(float(x_spacing) * float(y_spacing))) / 1000.0
    if not math.isfinite(value) or value <= 0.0:
        raise OperationalVerificationInputError("verification grid spacing is invalid")
    return value


def _headline(rows: Sequence[Mapping[str, Any]]) -> dict[str, Any]:
    selected = [
        row
        for row in rows
        if 5 <= int(row["lead_minutes"]) <= 60
        and float(row["threshold_mm_h"]) == 5.0
        and float(row["window_target_km"]) == 10.0
    ]
    fss = {
        model: _finite_mean(
            [float(row["fss"]) for row in selected if row["model"] == model]
        )
        for model in ("lk", "persistence", "translation")
    }
    return {
        "band": "5-60_minutes",
        "threshold_mm_h": 5.0,
        "fss_window_target_km": 10.0,
        "mean_fss": fss,
        "lk_minus_persistence_fss": _difference(fss["lk"], fss["persistence"]),
        "lk_minus_translation_fss": _difference(fss["lk"], fss["translation"]),
    }


def _finite_mean(values: Sequence[float]) -> float | None:
    finite = [value for value in values if math.isfinite(value)]
    return float(np.mean(finite)) if finite else None


def _difference(left: float | None, right: float | None) -> float | None:
    return None if left is None or right is None else left - right


def _json_bytes(value: Any) -> bytes:
    return json.dumps(
        _json_safe(value), separators=(",", ":"), sort_keys=True, allow_nan=False
    ).encode()


def _json_safe(value: Any) -> Any:
    if isinstance(value, dict):
        return {str(key): _json_safe(item) for key, item in value.items()}
    if isinstance(value, (list, tuple)):
        return [_json_safe(item) for item in value]
    if isinstance(value, (float, np.floating)):
        return float(value) if math.isfinite(float(value)) else None
    if isinstance(value, np.integer):
        return int(value)
    return value
