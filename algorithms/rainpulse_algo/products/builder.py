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
from rasterio.io import MemoryFile
from rasterio.transform import from_bounds
from scipy.io import netcdf_file
from zarr.storage import MemoryStore

from rainpulse_algo.diagnostics.png import encode_rgba_png, png_dimensions
from rainpulse_algo.grid import RegularLatLonGrid
from rainpulse_algo.nowcast.forecast_zarr import validate_forecast_output_zarr_store

from .point_index import encode_point_query_index, validate_point_query_index
from .profile import PaletteStop, ProductBuilderProfile

CONTRACT_NAME = "rainpulse.application-product-bundle"
CONTRACT_VERSION = "1.0"
FILL_VALUE = np.float32(-9999.0)
COG_MEDIA_TYPE = "image/tiff; application=geotiff; profile=cloud-optimized"
NETCDF_MEDIA_TYPE = "application/x-netcdf"
POINT_INDEX_MEDIA_TYPE = "application/vnd.rainpulse.point-index"


class ProductBuildInputError(ValueError):
    """Raised when a ForecastOutput cannot produce an RP-015 bundle."""


def build_application_product_bundle(
    forecast_objects: Mapping[str, bytes],
    *,
    source_forecast_uri: str,
    source_forecast_sha256: str,
    run_id: UUID,
    job_id: UUID,
    model_run_id: UUID,
    product_ids: Mapping[str, UUID],
    profile: ProductBuilderProfile,
    grid: RegularLatLonGrid,
) -> dict[str, bytes]:
    validate_forecast_output_zarr_store(forecast_objects)
    forecast = _open_group(forecast_objects)
    _validate_source_identity(
        forecast,
        source_forecast_sha256=source_forecast_sha256,
        run_id=run_id,
        profile=profile,
        grid=grid,
        product_ids=product_ids,
    )
    issue_time = _parse_time(str(forecast.attrs["issue_time"]))
    lead_minutes = forecast["lead_time"][:].astype("int32")
    valid_times = [issue_time + timedelta(minutes=int(lead)) for lead in lead_minutes]
    rain_rate = forecast["rain_rate"][0, :].astype("float32")
    valid_mask = forecast["output_valid_mask"][:].astype("uint8")
    confidence = forecast["confidence"][:].astype("float32")
    accumulations = {
        "accumulation_60": (
            forecast["accum_60"][0, :].astype("float32"),
            np.all(valid_mask[:12] == 1, axis=0),
            60,
        ),
        "accumulation_120": (
            forecast["accum_120"][0, :].astype("float32"),
            np.all(valid_mask[:24] == 1, axis=0),
            120,
        ),
    }
    created_at = datetime.now(UTC)
    objects: dict[str, bytes] = {}
    products: list[dict[str, Any]] = []

    rain_assets: list[dict[str, Any]] = []
    for index, (lead, valid_time) in enumerate(zip(lead_minutes, valid_times, strict=True)):
        values = rain_rate[index]
        valid = valid_mask[index] == 1
        relative_root = f"rain_rate/lead-{int(lead):03d}"
        rain_assets.extend(
            _build_field_assets(
                objects,
                relative_root=relative_root,
                product_type="rain_rate",
                values=values,
                valid=valid,
                issue_time=issue_time,
                valid_time=valid_time,
                lead_minutes=int(lead),
                interval_minutes=None,
                unit="mm h-1",
                variable_name="rain_rate",
                profile=profile,
                grid=grid,
                source_forecast_uri=source_forecast_uri,
                source_forecast_sha256=source_forecast_sha256,
                run_id=run_id,
                model_id=str(forecast.attrs["model_id"]),
                model_version=str(forecast.attrs["model_version"]),
                model_config_version=str(forecast.attrs["config_version"]),
                created_at=created_at,
            )
        )
    point_path = "rain_rate/query/point-index.bin"
    point_bytes = encode_point_query_index(
        rain_rate,
        confidence,
        valid_mask,
        west=grid.west,
        south=grid.south,
        longitude_interval=grid.longitude_interval_deg,
        latitude_interval=grid.latitude_interval_deg,
    )
    objects[point_path] = point_bytes
    rain_assets.append(
        _asset(
            point_path,
            point_bytes,
            asset_type="point_query_index",
            media_type=POINT_INDEX_MEDIA_TYPE,
            lead_minutes=None,
            valid_time=None,
            unit="mm h-1",
            state=None,
            extra={
                "contract_version": profile.point_query_contract_version,
                "lead_count": len(lead_minutes),
                "lead_step_minutes": 5,
                "header_bytes": 64,
                "record_bytes": 5,
            },
        )
    )
    products.append(
        {
            "product_id": str(product_ids["rain_rate"]),
            "product_type": "rain_rate",
            "valid_times": [value.isoformat() for value in valid_times],
            "member_count": 1,
            "assets": rain_assets,
        }
    )

    for product_type, (values, valid, lead) in accumulations.items():
        valid_time = issue_time + timedelta(minutes=lead)
        assets = _build_field_assets(
            objects,
            relative_root=f"{product_type}/lead-{lead:03d}",
            product_type=product_type,
            values=values,
            valid=valid,
            issue_time=issue_time,
            valid_time=valid_time,
            lead_minutes=lead,
            interval_minutes=lead,
            unit="mm",
            variable_name="rainfall_amount",
            profile=profile,
            grid=grid,
            source_forecast_uri=source_forecast_uri,
            source_forecast_sha256=source_forecast_sha256,
            run_id=run_id,
            model_id=str(forecast.attrs["model_id"]),
            model_version=str(forecast.attrs["model_version"]),
            model_config_version=str(forecast.attrs["config_version"]),
            created_at=created_at,
        )
        products.append(
            {
                "product_id": str(product_ids[product_type]),
                "product_type": product_type,
                "valid_times": [valid_time.isoformat()],
                "member_count": 1,
                "assets": assets,
            }
        )

    manifest = {
        "contract_name": CONTRACT_NAME,
        "contract_version": CONTRACT_VERSION,
        "run_id": str(run_id),
        "job_id": str(job_id),
        "model_run_id": str(model_run_id),
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
        "renderer_version": profile.builder_version,
        "palette_version": profile.palette.version,
        "products": products,
        "created_at": created_at.isoformat(),
    }
    objects["manifest.json"] = json.dumps(
        manifest, separators=(",", ":"), sort_keys=True
    ).encode()
    validate_application_product_bundle(objects)
    return dict(sorted(objects.items()))


def validate_application_product_bundle(objects: Mapping[str, bytes]) -> dict[str, Any]:
    if "manifest.json" not in objects:
        raise ProductBuildInputError("application product bundle has no manifest")
    try:
        manifest = json.loads(objects["manifest.json"])
    except (TypeError, json.JSONDecodeError) as exc:
        raise ProductBuildInputError("application product manifest is invalid JSON") from exc
    if (
        manifest.get("contract_name") != CONTRACT_NAME
        or manifest.get("contract_version") != CONTRACT_VERSION
        or manifest.get("width", 0) <= 0
        or manifest.get("height", 0) <= 0
    ):
        raise ProductBuildInputError("application product manifest identity is invalid")
    products = manifest.get("products")
    if not isinstance(products, list) or {
        product.get("product_type") for product in products if isinstance(product, dict)
    } != {"rain_rate", "accumulation_60", "accumulation_120"}:
        raise ProductBuildInputError("application product suite must contain three product types")

    paths: set[str] = set()
    asset_count = 0
    for product in products:
        valid_times = product.get("valid_times")
        assets = product.get("assets")
        if not isinstance(valid_times, list) or not valid_times or not isinstance(assets, list):
            raise ProductBuildInputError("application product valid times or assets are invalid")
        expected_valid_count = 24 if product["product_type"] == "rain_rate" else 1
        expected_asset_count = 73 if product["product_type"] == "rain_rate" else 3
        if len(valid_times) != expected_valid_count or len(assets) != expected_asset_count:
            raise ProductBuildInputError("application product lead or asset count is invalid")
        for asset in assets:
            path = asset.get("object_path")
            if (
                not isinstance(path, str)
                or not _safe_path(path)
                or path in paths
                or path not in objects
            ):
                raise ProductBuildInputError("application product asset path is invalid")
            paths.add(path)
            asset_count += 1
            data = objects[path]
            actual_sha256 = hashlib.sha256(data).hexdigest()
            if len(data) != asset.get("size_bytes") or actual_sha256 != asset.get("sha256"):
                raise ProductBuildInputError("application product asset checksum differs")
            media_type = asset.get("media_type")
            if media_type == "image/png":
                if png_dimensions(data) != (manifest["width"], manifest["height"]):
                    raise ProductBuildInputError("rendered rainfall PNG dimensions differ")
            elif media_type == COG_MEDIA_TYPE:
                _validate_cog(data, manifest)
            elif media_type == NETCDF_MEDIA_TYPE:
                _validate_netcdf(data, manifest, product["product_type"])
            elif media_type == POINT_INDEX_MEDIA_TYPE:
                point = validate_point_query_index(data)
                if point["width"] != manifest["width"] or point["height"] != manifest["height"]:
                    raise ProductBuildInputError("point-query index grid differs")
            else:
                raise ProductBuildInputError("application product asset media type is invalid")
    if set(objects) != paths | {"manifest.json"}:
        raise ProductBuildInputError("application product bundle has unregistered objects")
    return {
        "manifest": manifest,
        "product_count": len(products),
        "asset_count": asset_count,
        "object_count": len(objects),
        "size_bytes": sum(len(value) for value in objects.values()),
    }


def rainfall_rgba(
    values: np.ndarray,
    valid: np.ndarray,
    stops: tuple[PaletteStop, ...],
    *,
    transparent_below: float,
    opacity: int,
) -> np.ndarray:
    field = np.asarray(values, dtype="float32")
    support = np.asarray(valid, dtype=bool)
    if field.ndim != 2 or field.shape != support.shape:
        raise ProductBuildInputError("rainfall render arrays must be matching 2-D fields")
    rgba = np.zeros((*field.shape, 4), dtype="uint8")
    visible = support & np.isfinite(field) & (field >= transparent_below)
    if np.any(visible):
        minima = np.asarray([stop.minimum for stop in stops], dtype="float32")
        colors = np.asarray([_hex_rgb(stop.color) for stop in stops], dtype="uint8")
        indices = np.searchsorted(minima, field[visible], side="right") - 1
        rgba[visible, :3] = colors[np.clip(indices, 0, len(colors) - 1)]
        rgba[visible, 3] = np.uint8(opacity)
    return np.flipud(rgba)


def _build_field_assets(
    objects: dict[str, bytes],
    *,
    relative_root: str,
    product_type: str,
    values: np.ndarray,
    valid: np.ndarray,
    issue_time: datetime,
    valid_time: datetime,
    lead_minutes: int,
    interval_minutes: int | None,
    unit: str,
    variable_name: str,
    profile: ProductBuilderProfile,
    grid: RegularLatLonGrid,
    source_forecast_uri: str,
    source_forecast_sha256: str,
    run_id: UUID,
    model_id: str,
    model_version: str,
    model_config_version: str,
    created_at: datetime,
) -> list[dict[str, Any]]:
    state = _state_summary(values, valid)
    stops = (
        profile.palette.rain_rate
        if product_type == "rain_rate"
        else profile.palette.rainfall_amount
    )
    png = encode_rgba_png(
        rainfall_rgba(
            values,
            valid,
            stops,
            transparent_below=profile.palette.transparent_below_mm,
            opacity=profile.palette.opacity,
        )
    )
    cog = _encode_cog(
        values,
        valid,
        grid=grid,
        fill_value=profile.netcdf.fill_value,
        compression=profile.cog.compression,
        block_size=profile.cog.block_size,
        product_type=product_type,
        unit=unit,
        issue_time=issue_time,
        valid_time=valid_time,
        lead_minutes=lead_minutes,
    )
    netcdf = _encode_netcdf(
        values,
        valid,
        grid=grid,
        fill_value=profile.netcdf.fill_value,
        variable_name=variable_name,
        product_type=product_type,
        unit=unit,
        issue_time=issue_time,
        valid_time=valid_time,
        lead_minutes=lead_minutes,
        interval_minutes=interval_minutes,
        source_forecast_uri=source_forecast_uri,
        source_forecast_sha256=source_forecast_sha256,
        run_id=run_id,
        model_id=model_id,
        model_version=model_version,
        model_config_version=model_config_version,
        product_config_version=profile.profile_version,
        created_at=created_at,
    )
    paths = {
        "rendered_png": (f"{relative_root}/layer.png", png, "image/png"),
        "cloud_optimized_geotiff": (f"{relative_root}/field.tif", cog, COG_MEDIA_TYPE),
        "application_netcdf": (f"{relative_root}/field.nc", netcdf, NETCDF_MEDIA_TYPE),
    }
    entries: list[dict[str, Any]] = []
    for asset_type, (path, data, media_type) in paths.items():
        objects[path] = data
        extra: dict[str, Any] = {}
        if asset_type == "rendered_png":
            extra = {
                "palette_version": profile.palette.version,
                "value_breaks": [
                    {"minimum": stop.minimum, "color": stop.color} for stop in stops
                ],
                "opacity": profile.palette.opacity,
                "transparent_below": profile.palette.transparent_below_mm,
                "pixel_edge_bounds": list(grid.pixel_edge_bounds),
            }
        entries.append(
            _asset(
                path,
                data,
                asset_type=asset_type,
                media_type=media_type,
                lead_minutes=lead_minutes,
                valid_time=valid_time,
                unit=unit,
                state=state,
                extra=extra,
            )
        )
    return entries


def _asset(
    path: str,
    data: bytes,
    *,
    asset_type: str,
    media_type: str,
    lead_minutes: int | None,
    valid_time: datetime | None,
    unit: str,
    state: Mapping[str, Any] | None,
    extra: Mapping[str, Any],
) -> dict[str, Any]:
    entry: dict[str, Any] = {
        "object_path": path,
        "asset_type": asset_type,
        "media_type": media_type,
        "sha256": hashlib.sha256(data).hexdigest(),
        "size_bytes": len(data),
        "lead_time_minutes": lead_minutes,
        "valid_time": valid_time.isoformat() if valid_time else None,
        "unit": unit,
    }
    if state is not None:
        entry.update(state)
    entry.update(extra)
    return entry


def _state_summary(values: np.ndarray, valid: np.ndarray) -> dict[str, Any]:
    support = np.asarray(valid, dtype=bool)
    field = np.asarray(values)
    if field.shape != support.shape or np.any(~np.isfinite(field[support])):
        raise ProductBuildInputError("application product valid cells are not finite")
    cell_count = int(field.size)
    valid_count = int(np.count_nonzero(support))
    return {
        "cell_count": cell_count,
        "valid_cell_count": valid_count,
        "missing_cell_count": cell_count - valid_count,
        "no_rain_cell_count": int(np.count_nonzero(support & (field == 0))),
        "coverage_ratio": valid_count / cell_count,
        "minimum": float(np.min(field[support])) if valid_count else None,
        "maximum": float(np.max(field[support])) if valid_count else None,
    }


def _encode_cog(
    values: np.ndarray,
    valid: np.ndarray,
    *,
    grid: RegularLatLonGrid,
    fill_value: float,
    compression: str,
    block_size: int,
    product_type: str,
    unit: str,
    issue_time: datetime,
    valid_time: datetime,
    lead_minutes: int,
) -> bytes:
    data = np.where(valid, values, np.float32(fill_value)).astype("float32")
    north_up = np.flipud(data)
    west, south, east, north = grid.pixel_edge_bounds
    with MemoryFile() as memory:
        with memory.open(
            driver="COG",
            width=grid.longitude_count,
            height=grid.latitude_count,
            count=1,
            dtype="float32",
            crs="EPSG:4326",
            transform=from_bounds(
                west,
                south,
                east,
                north,
                grid.longitude_count,
                grid.latitude_count,
            ),
            nodata=fill_value,
            compress=compression,
            blocksize=block_size,
            overview_resampling="AVERAGE",
        ) as dataset:
            dataset.write(north_up, 1)
            dataset.set_band_description(1, product_type)
            dataset.update_tags(
                AREA_OR_POINT="Point",
                grid_id=grid.grid_id,
                product_type=product_type,
                units=unit,
                issue_time=issue_time.isoformat(),
                valid_time=valid_time.isoformat(),
                lead_time_minutes=str(lead_minutes),
            )
        return memory.read()


def _encode_netcdf(
    values: np.ndarray,
    valid: np.ndarray,
    *,
    grid: RegularLatLonGrid,
    fill_value: float,
    variable_name: str,
    product_type: str,
    unit: str,
    issue_time: datetime,
    valid_time: datetime,
    lead_minutes: int,
    interval_minutes: int | None,
    source_forecast_uri: str,
    source_forecast_sha256: str,
    run_id: UUID,
    model_id: str,
    model_version: str,
    model_config_version: str,
    product_config_version: str,
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
        rainfall = dataset.createVariable(variable_name, "f", ("lat", "lon"))

        latitude[:] = grid.latitude
        longitude[:] = grid.longitude
        latitude.standard_name = "latitude"
        latitude.long_name = "latitude"
        latitude.units = "degrees_north"
        latitude.axis = "Y"
        latitude.Lat_Name = "Latitude"
        latitude.Units = "Degrees_North"
        longitude.standard_name = "longitude"
        longitude.long_name = "longitude"
        longitude.units = "degrees_east"
        longitude.axis = "X"
        longitude.Long_Name = "Longitude"
        longitude.Units = "Degrees_East"
        crs.grid_mapping_name = "latitude_longitude"
        crs.longitude_of_prime_meridian = np.float64(0.0)
        crs.semi_major_axis = np.float64(6378137.0)
        crs.inverse_flattening = np.float64(298.257223563)
        crs.epsg_code = "EPSG:4326"

        rainfall._FillValue = np.float32(fill_value)
        rainfall.missing_value = np.float32(fill_value)
        rainfall.units = unit
        rainfall.coordinates = "lat lon"
        rainfall.grid_mapping = "crs"
        rainfall.long_name = (
            "precipitation rate" if variable_name == "rain_rate" else "rainfall amount"
        )
        rainfall.standard_name = (
            "rainfall_rate"
            if variable_name == "rain_rate"
            else "lwe_thickness_of_precipitation_amount"
        )
        if interval_minutes is not None:
            rainfall.accumulation_interval_minutes = np.int32(interval_minutes)
        rainfall[:] = np.where(valid, values, np.float32(fill_value)).astype("float32")

        valid_values = np.asarray(values)[np.asarray(valid, dtype=bool)]
        dataset.Conventions = "CF-1.8"
        dataset.title = "RainPulse short-term rainfall product"
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
        dataset.product_config_version = product_config_version
        dataset.source_forecast_sha256 = source_forecast_sha256
        dataset.DataTime = issue_time.strftime("%Y%m%d%H%M%S")
        dataset.ElementCode = variable_name
        dataset.StartLon = f"{grid.west:.2f}"
        dataset.EndLon = f"{grid.east:.2f}"
        dataset.StartLat = f"{grid.south:.2f}"
        dataset.EndLat = f"{grid.north:.2f}"
        dataset.LonInterval = f"{grid.longitude_interval_deg:.2f}"
        dataset.LatInterval = f"{grid.latitude_interval_deg:.2f}"
        dataset.LonNum = str(grid.longitude_count)
        dataset.LatNum = str(grid.latitude_count)
        dataset.Year = issue_time.strftime("%Y")
        dataset.Month = issue_time.strftime("%m")
        dataset.Day = issue_time.strftime("%d")
        dataset.Hour = issue_time.strftime("%H")
        dataset.Minute = issue_time.strftime("%M")
        dataset.Units = unit
        dataset.MissingValue = f"{fill_value:.1f}"
        dataset.FixedValue = str(int(fill_value))
        dataset.MinValue = (
            f"{float(np.min(valid_values)):.6g}" if valid_values.size else f"{fill_value:.1f}"
        )
        dataset.MaxValue = (
            f"{float(np.max(valid_values)):.6g}" if valid_values.size else f"{fill_value:.1f}"
        )
        dataset.Version = "1.0"
        dataset.ForecastMode = model_id
        dataset.flush()
        return buffer.getvalue()
    finally:
        dataset.close()


def _validate_cog(data: bytes, manifest: Mapping[str, Any]) -> None:
    try:
        with MemoryFile(data) as memory, memory.open() as dataset:
            if (
                dataset.width != manifest["width"]
                or dataset.height != manifest["height"]
                or dataset.crs is None
                or dataset.crs.to_epsg() != 4326
                or dataset.tags(ns="IMAGE_STRUCTURE").get("LAYOUT") != "COG"
                or dataset.nodata != float(FILL_VALUE)
            ):
                raise ProductBuildInputError("COG geospatial metadata differs")
    except ProductBuildInputError:
        raise
    except Exception as exc:
        raise ProductBuildInputError("application product COG is unreadable") from exc


def _validate_netcdf(
    data: bytes,
    manifest: Mapping[str, Any],
    product_type: str,
) -> None:
    try:
        with netcdf_file(io.BytesIO(data), mode="r", mmap=False) as dataset:
            variable_name = "rain_rate" if product_type == "rain_rate" else "rainfall_amount"
            if (
                dataset.version_byte != 1
                or dataset.dimensions.get("lat") != manifest["height"]
                or dataset.dimensions.get("lon") != manifest["width"]
                or variable_name not in dataset.variables
                or dataset.variables[variable_name].shape
                != (manifest["height"], manifest["width"])
            ):
                raise ProductBuildInputError("application NetCDF dimensions differ")
            field = dataset.variables[variable_name]
            if float(field._attributes.get("_FillValue")) != float(FILL_VALUE):
                raise ProductBuildInputError("application NetCDF fill value differs")
    except ProductBuildInputError:
        raise
    except Exception as exc:
        raise ProductBuildInputError("application product NetCDF is unreadable") from exc


def _validate_source_identity(
    forecast: zarr.Group,
    *,
    source_forecast_sha256: str,
    run_id: UUID,
    profile: ProductBuilderProfile,
    grid: RegularLatLonGrid,
    product_ids: Mapping[str, UUID],
) -> None:
    if (
        forecast.attrs.get("contract_version") != profile.forecast_output_contract_version
        or forecast.attrs.get("run_id") != str(run_id)
        or forecast.attrs.get("grid_id") != grid.grid_id
        or forecast.attrs.get("grid_config_version") != grid.config_version
        or profile.grid_id != grid.grid_id
        or profile.grid_config_version != grid.config_version
        or forecast.attrs.get("coordinate_sha256") != grid.coordinate_sha256
        or not _sha256(source_forecast_sha256)
        or not np.array_equal(forecast["lat"][:], grid.latitude)
        or not np.array_equal(forecast["lon"][:], grid.longitude)
    ):
        raise ProductBuildInputError("ForecastOutput identity differs from product request")
    expected_types = {"rain_rate", "accumulation_60", "accumulation_120"}
    if set(product_ids) != expected_types or any(value.int == 0 for value in product_ids.values()):
        raise ProductBuildInputError("RP-015 product identities are incomplete")
    if len(set(product_ids.values())) != 3:
        raise ProductBuildInputError("RP-015 product identities must be unique")


def _open_group(objects: Mapping[str, bytes]) -> zarr.Group:
    store = MemoryStore()
    store.update({key: bytes(value) for key, value in objects.items()})
    return zarr.open_group(store=store, mode="r")


def _safe_path(value: str) -> bool:
    path = PurePosixPath(value)
    return bool(value) and not path.is_absolute() and ".." not in path.parts and str(path) == value


def _sha256(value: str) -> bool:
    return len(value) == 64 and all(character in "0123456789abcdef" for character in value)


def _hex_rgb(value: str) -> tuple[int, int, int]:
    return tuple(int(value[index : index + 2], 16) for index in (1, 3, 5))  # type: ignore[return-value]


def _parse_time(value: str) -> datetime:
    parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    if parsed.utcoffset() is None:
        raise ProductBuildInputError("product issue time must include a UTC offset")
    return parsed.astimezone(UTC)
