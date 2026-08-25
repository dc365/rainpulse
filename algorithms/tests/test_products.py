from __future__ import annotations

import hashlib
import io
import json
from dataclasses import replace
from pathlib import Path
from uuid import UUID

import numpy as np
from rasterio.io import MemoryFile
from scipy.io import netcdf_file

from rainpulse_algo.diagnostics.png import png_dimensions
from rainpulse_algo.nowcast.forecast_zarr import build_forecast_output_zarr_store
from rainpulse_algo.nowcast.pysteps_lk import run_pysteps_lk
from rainpulse_algo.products.builder import (
    COG_MEDIA_TYPE,
    NETCDF_MEDIA_TYPE,
    POINT_INDEX_MEDIA_TYPE,
    build_application_product_bundle,
    rainfall_rgba,
    validate_application_product_bundle,
)
from rainpulse_algo.products.point_index import HEADER, validate_point_query_index
from rainpulse_algo.products.profile import load_product_builder_profile
from rainpulse_algo.worker.object_store import artifact_sha256

from .test_pysteps_lk import INPUT_ASSET_IDS, ISSUE_TIME, nowcast_input, profile, tiny_grid

REPOSITORY_ROOT = Path(__file__).resolve().parents[2]
PRODUCT_CONFIG = (
    REPOSITORY_ROOT / "configs" / "products" / "rp015-application-products-v1.yaml"
)
RUN_ID = UUID("96000000-0000-4000-8000-000000000001")
MODEL_JOB_ID = UUID("96000000-0000-4000-8000-000000000002")
PRODUCT_JOB_ID = UUID("96000000-0000-4000-8000-000000000003")
MODEL_RUN_ID = UUID("96000000-0000-4000-8000-000000000004")
PRODUCT_IDS = {
    "rain_rate": UUID("96000000-0000-4000-8000-000000000005"),
    "accumulation_60": UUID("96000000-0000-4000-8000-000000000006"),
    "accumulation_120": UUID("96000000-0000-4000-8000-000000000007"),
}


def forecast_fixture() -> dict[str, bytes]:
    grid = tiny_grid()
    model_profile = profile()
    result = run_pysteps_lk(nowcast_input(), profile=model_profile, grid=grid)
    return build_forecast_output_zarr_store(
        result,
        run_id=RUN_ID,
        job_id=MODEL_JOB_ID,
        issue_time=ISSUE_TIME,
        input_uri="s3://rainpulse/nowcast-input/fixture/input.zarr",
        input_asset_ids=INPUT_ASSET_IDS,
        profile=model_profile,
        grid=grid,
        runtime_ms=125,
    )


def product_profile():
    configured = load_product_builder_profile(PRODUCT_CONFIG)
    grid = tiny_grid()
    return replace(
        configured,
        grid_id=grid.grid_id,
        grid_config_version=grid.config_version,
    )


def product_bundle(forecast: dict[str, bytes] | None = None) -> dict[str, bytes]:
    forecast = forecast or forecast_fixture()
    return build_application_product_bundle(
        forecast,
        source_forecast_uri="s3://rainpulse/products/fixture/forecast.zarr",
        source_forecast_sha256=artifact_sha256(forecast),
        run_id=RUN_ID,
        job_id=PRODUCT_JOB_ID,
        model_run_id=MODEL_RUN_ID,
        product_ids=PRODUCT_IDS,
        profile=product_profile(),
        grid=tiny_grid(),
    )


def test_builds_atomic_three_product_distribution_suite() -> None:
    forecast = forecast_fixture()
    objects = product_bundle(forecast)
    validation = validate_application_product_bundle(objects)
    manifest = validation["manifest"]
    products = {item["product_type"]: item for item in manifest["products"]}

    assert validation["product_count"] == 3
    assert validation["asset_count"] == 79
    assert validation["object_count"] == 80
    assert len(products["rain_rate"]["valid_times"]) == 24
    assert len(products["rain_rate"]["assets"]) == 73
    assert len(products["accumulation_60"]["assets"]) == 3
    assert len(products["accumulation_120"]["assets"]) == 3
    assert manifest["source_forecast"]["sha256"] == artifact_sha256(forecast)

    png_asset = next(
        asset
        for asset in products["rain_rate"]["assets"]
        if asset["media_type"] == "image/png" and asset["lead_time_minutes"] == 5
    )
    assert png_dimensions(objects[png_asset["object_path"]]) == (64, 64)
    assert png_asset["coverage_ratio"] < 1
    assert png_asset["missing_cell_count"] > 0


def test_distribution_formats_preserve_geolocation_and_missing_state() -> None:
    objects = product_bundle()
    manifest = json.loads(objects["manifest.json"])
    rain = next(item for item in manifest["products"] if item["product_type"] == "rain_rate")
    by_media = {
        asset["media_type"]: asset
        for asset in rain["assets"]
        if asset["lead_time_minutes"] == 5
    }

    with MemoryFile(objects[by_media[COG_MEDIA_TYPE]["object_path"]]) as memory:
        with memory.open() as dataset:
            assert dataset.crs.to_epsg() == 4326
            assert dataset.tags(ns="IMAGE_STRUCTURE")["LAYOUT"] == "COG"
            assert tuple(dataset.bounds) == tuple(manifest["pixel_edge_bounds"])
            assert dataset.nodata == -9999.0

    with netcdf_file(
        io.BytesIO(objects[by_media[NETCDF_MEDIA_TYPE]["object_path"]]),
        mode="r",
        mmap=False,
    ) as dataset:
        assert dataset.dimensions == {"lat": 64, "lon": 64}
        assert dataset.variables["rain_rate"].shape == (64, 64)
        assert np.all(np.diff(dataset.variables["lat"][:]) > 0)
        values = dataset.variables["rain_rate"][:].copy()
        assert np.any(values == -9999.0)
        assert np.any(values == 0.0)
        assert dataset._attributes["ElementCode"] == b"rain_rate"


def test_point_index_is_fixed_record_and_keeps_invalid_as_nan() -> None:
    objects = product_bundle()
    manifest = json.loads(objects["manifest.json"])
    rain = next(item for item in manifest["products"] if item["product_type"] == "rain_rate")
    asset = next(item for item in rain["assets"] if item["media_type"] == POINT_INDEX_MEDIA_TYPE)
    data = objects[asset["object_path"]]
    index = validate_point_query_index(data)

    assert index["lead_count"] == 24
    assert index["cell_bytes"] == 120
    last_cell = HEADER.size + (64 * 64 - 1) * int(index["cell_bytes"])
    first_rate = np.frombuffer(data[last_cell : last_cell + 4], dtype=">f4")[0]
    assert np.isnan(first_rate)


def test_render_keeps_both_no_rain_and_missing_transparent_without_changing_values() -> None:
    configured = product_profile()
    values = np.asarray([[0.0, 1.0, np.nan]], dtype="float32")
    valid = np.asarray([[True, True, False]])
    original = values.copy()

    rgba = rainfall_rgba(
        values,
        valid,
        configured.palette.rain_rate,
        transparent_below=configured.palette.transparent_below_mm,
        opacity=configured.palette.opacity,
    )

    assert rgba[0, 0, 3] == 0
    assert rgba[0, 1, 3] == configured.palette.opacity
    assert rgba[0, 2, 3] == 0
    assert hashlib.sha256(values.tobytes()).digest() == hashlib.sha256(original.tobytes()).digest()
