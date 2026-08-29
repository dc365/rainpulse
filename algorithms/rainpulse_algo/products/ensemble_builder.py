from __future__ import annotations

import hashlib
import io
import json
from collections.abc import Mapping
from datetime import UTC, datetime, timedelta
from pathlib import PurePosixPath
from typing import Any
from uuid import UUID

import numpy as np
import zarr
from scipy.io import netcdf_file
from zarr.storage import MemoryStore

from rainpulse_algo.diagnostics.png import encode_rgba_png, png_dimensions
from rainpulse_algo.grid import RegularLatLonGrid
from rainpulse_algo.nowcast.ensemble_zarr import (
    validate_ensemble_forecast_output_zarr_store,
)

from .builder import NETCDF_MEDIA_TYPE, rainfall_rgba
from .ensemble_profile import EnsembleApplicationProductProfile

CONTRACT_NAME = "rainpulse.ensemble-application-product-bundle"
CONTRACT_VERSION = "1.0"


class EnsembleProductBuildInputError(ValueError):
    """Raised when an ensemble ForecastOutput cannot produce an RP-023 bundle."""


def build_ensemble_application_product_bundle(
    forecast_objects: Mapping[str, bytes],
    *,
    source_forecast_uri: str,
    source_forecast_sha256: str,
    run_id: UUID,
    job_id: UUID,
    profile: EnsembleApplicationProductProfile,
    grid: RegularLatLonGrid,
) -> dict[str, bytes]:
    validate_ensemble_forecast_output_zarr_store(forecast_objects)
    forecast = _open_group(forecast_objects)
    _validate_source_identity(
        forecast,
        source_forecast_sha256=source_forecast_sha256,
        run_id=run_id,
        profile=profile,
        grid=grid,
    )
    issue_time = _parse_time(str(forecast.attrs["issue_time"]))
    lead_minutes = forecast["lead_time"][:].astype("int32")
    valid_times = [issue_time + timedelta(minutes=int(value)) for value in lead_minutes]
    valid_mask = forecast["output_valid_mask"][:] == 1
    created_at = datetime.now(UTC)
    objects: dict[str, bytes] = {}
    layers: list[dict[str, Any]] = []

    for threshold in profile.thresholds_mm_h:
        threshold_code = int(threshold)
        layers.append(
            _build_layer(
                objects,
                layer_id=f"probability-gt-{threshold_code}",
                product_type="probability_exceedance",
                variable_name=f"prob_gt_{threshold_code}",
                values=forecast[f"prob_gt_{threshold_code}"][:].astype("float32"),
                valid_mask=valid_mask,
                lead_minutes=lead_minutes,
                valid_times=valid_times,
                unit="1",
                threshold_mm_h=threshold,
                quantile=None,
                palette=profile.probability_palette,
                transparent_below=profile.probability_transparent_below,
                source_forecast_uri=source_forecast_uri,
                source_forecast_sha256=source_forecast_sha256,
                run_id=run_id,
                issue_time=issue_time,
                profile=profile,
                grid=grid,
                model_id=str(forecast.attrs["model_id"]),
                model_version=str(forecast.attrs["model_version"]),
                model_config_version=str(forecast.attrs["config_version"]),
                member_count=int(forecast.attrs["ensemble_member_count"]),
                created_at=created_at,
            )
        )
    for quantile in profile.quantiles:
        percentile = int(round(quantile * 100))
        layers.append(
            _build_layer(
                objects,
                layer_id=f"quantile-p{percentile}",
                product_type="quantile",
                variable_name=f"p{percentile}",
                values=forecast[f"p{percentile}"][:].astype("float32"),
                valid_mask=valid_mask,
                lead_minutes=lead_minutes,
                valid_times=valid_times,
                unit="mm h-1",
                threshold_mm_h=None,
                quantile=quantile,
                palette=profile.quantile_palette,
                transparent_below=profile.quantile_transparent_below_mm_h,
                source_forecast_uri=source_forecast_uri,
                source_forecast_sha256=source_forecast_sha256,
                run_id=run_id,
                issue_time=issue_time,
                profile=profile,
                grid=grid,
                model_id=str(forecast.attrs["model_id"]),
                model_version=str(forecast.attrs["model_version"]),
                model_config_version=str(forecast.attrs["config_version"]),
                member_count=int(forecast.attrs["ensemble_member_count"]),
                created_at=created_at,
            )
        )

    manifest = {
        "contract_name": CONTRACT_NAME,
        "contract_version": CONTRACT_VERSION,
        "bundle_id": str(run_id),
        "run_id": str(run_id),
        "job_id": str(job_id),
        "issue_time": issue_time.isoformat(),
        "grid_id": grid.grid_id,
        "grid_config_version": grid.config_version,
        "coordinate_sha256": grid.coordinate_sha256,
        "coordinate_centre_bounds": [grid.west, grid.south, grid.east, grid.north],
        "pixel_edge_bounds": list(grid.pixel_edge_bounds),
        "width": grid.longitude_count,
        "height": grid.latitude_count,
        "longitude_interval_deg": grid.longitude_interval_deg,
        "latitude_interval_deg": grid.latitude_interval_deg,
        "source_forecast": {
            "uri": source_forecast_uri,
            "sha256": source_forecast_sha256,
            "contract_version": profile.forecast_output_contract_version,
        },
        "model_id": str(forecast.attrs["model_id"]),
        "model_version": str(forecast.attrs["model_version"]),
        "model_config_version": str(forecast.attrs["config_version"]),
        "product_config_version": profile.profile_version,
        "builder_version": profile.builder_version,
        "palette_version": profile.palette_version,
        "member_count": int(forecast.attrs["ensemble_member_count"]),
        "calibration_status": profile.calibration_status,
        "operational_eligible": False,
        "operational_gate": profile.operational_gate,
        "layers": layers,
        "created_at": created_at.isoformat(),
    }
    objects["manifest.json"] = json.dumps(
        manifest, separators=(",", ":"), sort_keys=True
    ).encode()
    validate_ensemble_application_product_bundle(objects)
    return dict(sorted(objects.items()))


def validate_ensemble_application_product_bundle(
    objects: Mapping[str, bytes],
) -> dict[str, Any]:
    try:
        manifest = json.loads(objects["manifest.json"])
    except KeyError as exc:
        raise EnsembleProductBuildInputError("ensemble product bundle has no manifest") from exc
    except (TypeError, json.JSONDecodeError) as exc:
        raise EnsembleProductBuildInputError("ensemble product manifest is invalid JSON") from exc
    if (
        manifest.get("contract_name") != CONTRACT_NAME
        or manifest.get("contract_version") != CONTRACT_VERSION
        or manifest.get("bundle_id") != manifest.get("run_id")
        or manifest.get("width", 0) <= 0
        or manifest.get("height", 0) <= 0
        or manifest.get("member_count", 0) < 2
        or manifest.get("operational_eligible") is not False
        or manifest.get("calibration_status")
        != "raw_ensemble_relative_frequency_uncalibrated"
    ):
        raise EnsembleProductBuildInputError("ensemble product manifest identity is invalid")
    layers = manifest.get("layers")
    expected_layers = {
        "probability-gt-1",
        "probability-gt-5",
        "probability-gt-10",
        "probability-gt-20",
        "probability-gt-50",
        "quantile-p10",
        "quantile-p50",
        "quantile-p90",
    }
    if not isinstance(layers, list) or {
        layer.get("layer_id") for layer in layers if isinstance(layer, dict)
    } != expected_layers:
        raise EnsembleProductBuildInputError("ensemble product layer suite is incomplete")

    paths: set[str] = set()
    asset_ids: set[str] = set()
    for layer in layers:
        assets = layer.get("assets")
        valid_times = layer.get("valid_times")
        if (
            layer.get("product_type") not in {"probability_exceedance", "quantile"}
            or not isinstance(valid_times, list)
            or len(valid_times) != 24
            or not isinstance(assets, list)
            or len(assets) != 48
        ):
            raise EnsembleProductBuildInputError("ensemble layer metadata is invalid")
        expected_media = {"image/png", NETCDF_MEDIA_TYPE}
        for lead in range(5, 125, 5):
            lead_assets = [item for item in assets if item.get("lead_time_minutes") == lead]
            if (
                len(lead_assets) != 2
                or {item.get("media_type") for item in lead_assets} != expected_media
            ):
                raise EnsembleProductBuildInputError("ensemble layer lead assets are incomplete")
        for asset in assets:
            path = asset.get("object_path")
            asset_id = asset.get("asset_id")
            if (
                not isinstance(path, str)
                or not _safe_path(path)
                or path in paths
                or path not in objects
                or not isinstance(asset_id, str)
                or not _safe_segment(asset_id)
                or asset_id in asset_ids
            ):
                raise EnsembleProductBuildInputError("ensemble product asset identity is invalid")
            paths.add(path)
            asset_ids.add(asset_id)
            data = objects[path]
            if (
                len(data) != asset.get("size_bytes")
                or hashlib.sha256(data).hexdigest() != asset.get("sha256")
            ):
                raise EnsembleProductBuildInputError("ensemble product asset checksum differs")
            if asset["media_type"] == "image/png":
                if png_dimensions(data) != (manifest["width"], manifest["height"]):
                    raise EnsembleProductBuildInputError("ensemble product PNG dimensions differ")
            else:
                _validate_netcdf(data, manifest, layer)
    if set(objects) != paths | {"manifest.json"}:
        raise EnsembleProductBuildInputError("ensemble product bundle has unregistered objects")
    return {
        "manifest": manifest,
        "layer_count": len(layers),
        "asset_count": len(asset_ids),
        "object_count": len(objects),
        "size_bytes": sum(len(value) for value in objects.values()),
    }


def _build_layer(
    objects: dict[str, bytes],
    *,
    layer_id: str,
    product_type: str,
    variable_name: str,
    values: np.ndarray,
    valid_mask: np.ndarray,
    lead_minutes: np.ndarray,
    valid_times: list[datetime],
    unit: str,
    threshold_mm_h: float | None,
    quantile: float | None,
    palette: tuple[Any, ...],
    transparent_below: float,
    source_forecast_uri: str,
    source_forecast_sha256: str,
    run_id: UUID,
    issue_time: datetime,
    profile: EnsembleApplicationProductProfile,
    grid: RegularLatLonGrid,
    model_id: str,
    model_version: str,
    model_config_version: str,
    member_count: int,
    created_at: datetime,
) -> dict[str, Any]:
    if values.shape != valid_mask.shape or values.shape[0] != 24:
        raise EnsembleProductBuildInputError("ensemble layer dimensions differ")
    assets: list[dict[str, Any]] = []
    for index, (lead, valid_time) in enumerate(zip(lead_minutes, valid_times, strict=True)):
        field = values[index]
        valid = valid_mask[index]
        state = _state_summary(field, valid)
        root = f"{layer_id}/lead-{int(lead):03d}"
        png = encode_rgba_png(
            rainfall_rgba(
                field,
                valid,
                palette,
                transparent_below=transparent_below,
                opacity=profile.opacity,
            )
        )
        netcdf = _encode_netcdf(
            field,
            valid,
            variable_name=variable_name,
            product_type=product_type,
            unit=unit,
            threshold_mm_h=threshold_mm_h,
            quantile=quantile,
            grid=grid,
            fill_value=profile.netcdf_fill_value,
            source_forecast_uri=source_forecast_uri,
            source_forecast_sha256=source_forecast_sha256,
            run_id=run_id,
            issue_time=issue_time,
            valid_time=valid_time,
            lead_minutes=int(lead),
            profile=profile,
            model_id=model_id,
            model_version=model_version,
            model_config_version=model_config_version,
            member_count=member_count,
            created_at=created_at,
        )
        for asset_type, suffix, data, media_type in (
            ("rendered_png", "png", png, "image/png"),
            ("application_netcdf", "nc", netcdf, NETCDF_MEDIA_TYPE),
        ):
            asset_id = f"{layer_id}-lead-{int(lead):03d}-{suffix}"
            path = f"{root}/layer.{suffix}" if suffix == "png" else f"{root}/field.nc"
            objects[path] = data
            asset = {
                "asset_id": asset_id,
                "object_path": path,
                "asset_type": asset_type,
                "media_type": media_type,
                "sha256": hashlib.sha256(data).hexdigest(),
                "size_bytes": len(data),
                "lead_time_minutes": int(lead),
                "valid_time": valid_time.isoformat(),
                "unit": unit,
                **state,
            }
            if asset_type == "rendered_png":
                asset.update(
                    {
                        "palette_version": profile.palette_version,
                        "opacity": profile.opacity,
                        "transparent_below": transparent_below,
                        "pixel_edge_bounds": list(grid.pixel_edge_bounds),
                    }
                )
            assets.append(asset)
    return {
        "layer_id": layer_id,
        "product_type": product_type,
        "variable_name": variable_name,
        "threshold_mm_h": threshold_mm_h,
        "quantile": quantile,
        "unit": unit,
        "valid_times": [value.isoformat() for value in valid_times],
        "legend": [
            {"minimum": float(stop.minimum), "color": stop.color} for stop in palette
        ],
        "assets": assets,
    }


def _state_summary(values: np.ndarray, valid: np.ndarray) -> dict[str, Any]:
    field = np.asarray(values, dtype="float32")
    support = np.asarray(valid, dtype=bool)
    if field.shape != support.shape or np.any(~np.isfinite(field[support])):
        raise EnsembleProductBuildInputError("ensemble product valid cells are not finite")
    cell_count = int(field.size)
    valid_count = int(np.count_nonzero(support))
    return {
        "cell_count": cell_count,
        "valid_cell_count": valid_count,
        "missing_cell_count": cell_count - valid_count,
        "zero_value_cell_count": int(np.count_nonzero(support & (field == 0))),
        "coverage_ratio": valid_count / cell_count,
        "minimum": float(np.min(field[support])) if valid_count else None,
        "maximum": float(np.max(field[support])) if valid_count else None,
    }


def _encode_netcdf(
    values: np.ndarray,
    valid: np.ndarray,
    *,
    variable_name: str,
    product_type: str,
    unit: str,
    threshold_mm_h: float | None,
    quantile: float | None,
    grid: RegularLatLonGrid,
    fill_value: float,
    source_forecast_uri: str,
    source_forecast_sha256: str,
    run_id: UUID,
    issue_time: datetime,
    valid_time: datetime,
    lead_minutes: int,
    profile: EnsembleApplicationProductProfile,
    model_id: str,
    model_version: str,
    model_config_version: str,
    member_count: int,
    created_at: datetime,
) -> bytes:
    buffer = io.BytesIO()
    dataset = netcdf_file(buffer, mode="w", version=1)
    try:
        dataset.createDimension("lat", grid.latitude_count)
        dataset.createDimension("lon", grid.longitude_count)
        latitude = dataset.createVariable("lat", "f", ("lat",))
        longitude = dataset.createVariable("lon", "f", ("lon",))
        crs = dataset.createVariable("crs", "i", ())
        field = dataset.createVariable(variable_name, "f", ("lat", "lon"))
        latitude[:] = grid.latitude
        longitude[:] = grid.longitude
        latitude.standard_name = "latitude"
        latitude.units = "degrees_north"
        latitude.axis = "Y"
        longitude.standard_name = "longitude"
        longitude.units = "degrees_east"
        longitude.axis = "X"
        crs.grid_mapping_name = "latitude_longitude"
        crs.longitude_of_prime_meridian = np.float64(0.0)
        crs.semi_major_axis = np.float64(6378137.0)
        crs.inverse_flattening = np.float64(298.257223563)
        crs.epsg_code = "EPSG:4326"
        field._FillValue = np.float32(fill_value)
        field.missing_value = np.float32(fill_value)
        field.units = unit
        field.coordinates = "lat lon"
        field.grid_mapping = "crs"
        field.long_name = (
            "raw ensemble probability of precipitation rate exceeding threshold"
            if product_type == "probability_exceedance"
            else "ensemble quantile of precipitation rate"
        )
        field.standard_name = (
            "probability_of_lwe_precipitation_rate_above_threshold"
            if product_type == "probability_exceedance"
            else "lwe_precipitation_rate"
        )
        if threshold_mm_h is not None:
            field.event_operator = profile.event_operator
            field.threshold_mm_h = np.float32(threshold_mm_h)
            field.calibration_status = profile.calibration_status
        if quantile is not None:
            field.quantile = np.float32(quantile)
        field[:] = np.where(valid, values, np.float32(fill_value)).astype("float32")
        dataset.Conventions = "CF-1.8"
        dataset.title = "RainPulse offline ensemble short-term rainfall product"
        dataset.institution = "Fonwee RainPulse"
        dataset.source = source_forecast_uri
        dataset.history = f"created {created_at.isoformat()} by RainPulse"
        dataset.grid_id = grid.grid_id
        dataset.run_id = str(run_id)
        dataset.product_type = product_type
        dataset.issue_time = issue_time.isoformat()
        dataset.valid_time = valid_time.isoformat()
        dataset.lead_time_minutes = np.int32(lead_minutes)
        dataset.model_id = model_id
        dataset.model_version = model_version
        dataset.model_config_version = model_config_version
        dataset.product_config_version = profile.profile_version
        dataset.source_forecast_sha256 = source_forecast_sha256
        dataset.member_count = np.int32(member_count)
        dataset.operational_eligible = "false"
        dataset.operational_gate = profile.operational_gate
        dataset.ElementCode = variable_name
        dataset.DataTime = issue_time.strftime("%Y%m%d%H%M%S")
        dataset.StartLon = f"{grid.west:.2f}"
        dataset.EndLon = f"{grid.east:.2f}"
        dataset.StartLat = f"{grid.south:.2f}"
        dataset.EndLat = f"{grid.north:.2f}"
        dataset.LonInterval = f"{grid.longitude_interval_deg:.2f}"
        dataset.LatInterval = f"{grid.latitude_interval_deg:.2f}"
        dataset.LonNum = str(grid.longitude_count)
        dataset.LatNum = str(grid.latitude_count)
        dataset.Units = unit
        dataset.MissingValue = f"{fill_value:.1f}"
        dataset.flush()
        return buffer.getvalue()
    finally:
        dataset.close()


def _validate_netcdf(
    data: bytes,
    manifest: Mapping[str, Any],
    layer: Mapping[str, Any],
) -> None:
    try:
        with netcdf_file(io.BytesIO(data), mode="r", mmap=False) as dataset:
            variable_name = layer["variable_name"]
            if (
                dataset.version_byte != 1
                or dataset.dimensions.get("lat") != manifest["height"]
                or dataset.dimensions.get("lon") != manifest["width"]
                or variable_name not in dataset.variables
                or dataset.variables[variable_name].shape
                != (manifest["height"], manifest["width"])
                or float(dataset.variables[variable_name]._attributes.get("_FillValue"))
                != -9999.0
            ):
                raise EnsembleProductBuildInputError("ensemble NetCDF metadata differs")
    except EnsembleProductBuildInputError:
        raise
    except Exception as exc:
        raise EnsembleProductBuildInputError("ensemble NetCDF is unreadable") from exc


def _validate_source_identity(
    forecast: zarr.Group,
    *,
    source_forecast_sha256: str,
    run_id: UUID,
    profile: EnsembleApplicationProductProfile,
    grid: RegularLatLonGrid,
) -> None:
    if (
        forecast.attrs.get("contract_version") != profile.forecast_output_contract_version
        or forecast.attrs.get("run_id") != str(run_id)
        or forecast.attrs.get("grid_id") != grid.grid_id
        or forecast.attrs.get("grid_config_version") != grid.config_version
        or forecast.attrs.get("coordinate_sha256") != grid.coordinate_sha256
        or forecast.attrs.get("probability_event_operator") != profile.event_operator
        or tuple(float(value) for value in forecast.attrs["probability_thresholds_mm_h"])
        != profile.thresholds_mm_h
        or tuple(float(value) for value in forecast.attrs["quantiles"])
        != profile.quantiles
        or forecast.attrs.get("probability_calibration_status")
        != profile.calibration_status
        or not _sha256(source_forecast_sha256)
        or not np.array_equal(forecast["lat"][:], grid.latitude)
        or not np.array_equal(forecast["lon"][:], grid.longitude)
    ):
        raise EnsembleProductBuildInputError("ensemble ForecastOutput identity differs")


def _open_group(objects: Mapping[str, bytes]) -> zarr.Group:
    store = MemoryStore()
    store.update({key: bytes(value) for key, value in objects.items()})
    return zarr.open_group(store=store, mode="r")


def _parse_time(value: str) -> datetime:
    parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    if parsed.utcoffset() is None:
        raise EnsembleProductBuildInputError("ensemble product time must include UTC")
    return parsed.astimezone(UTC)


def _safe_path(value: str) -> bool:
    path = PurePosixPath(value)
    return bool(value) and not path.is_absolute() and ".." not in path.parts and str(path) == value


def _safe_segment(value: str) -> bool:
    return bool(value) and len(value) <= 127 and value.replace("-", "").isalnum()


def _sha256(value: str) -> bool:
    return len(value) == 64 and all(character in "0123456789abcdef" for character in value)
