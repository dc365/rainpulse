from __future__ import annotations

import hashlib
import io
import json
from pathlib import Path
from uuid import UUID

import numpy as np
import yaml
from jsonschema import Draft202012Validator
from scipy.io import netcdf_file

from rainpulse_algo.nowcast.ensemble_zarr import (
    build_ensemble_forecast_output_zarr_store,
)
from rainpulse_algo.nowcast.pysteps_steps import run_pysteps_steps_fields
from rainpulse_algo.products.builder import NETCDF_MEDIA_TYPE
from rainpulse_algo.products.ensemble_builder import (
    build_ensemble_application_product_bundle,
    validate_ensemble_application_product_bundle,
)
from rainpulse_algo.products.ensemble_profile import (
    load_ensemble_application_product_profile,
)
from rainpulse_algo.worker.object_store import artifact_sha256

from .test_pysteps_lk import INPUT_ASSET_IDS, ISSUE_TIME, tiny_grid
from .test_pysteps_lk import profile as lk_profile
from .test_pysteps_steps import seeded_backend, steps_fields, steps_profile

REPOSITORY_ROOT = Path(__file__).resolve().parents[2]
PROFILE_PATH = (
    REPOSITORY_ROOT
    / "configs"
    / "products"
    / "rp023-ensemble-application-products-v1.yaml"
)
SCHEMA_PATH = (
    REPOSITORY_ROOT
    / "configs"
    / "schemas"
    / "ensemble-application-product-profile.schema.json"
)
RUN_ID = UUID("9b000000-0000-4000-8000-000000000001")
MODEL_JOB_ID = UUID("9b000000-0000-4000-8000-000000000002")
PRODUCT_JOB_ID = UUID("9b000000-0000-4000-8000-000000000003")


def ensemble_forecast_fixture() -> dict[str, bytes]:
    configured = steps_profile()
    result = run_pysteps_steps_fields(
        steps_fields(),
        profile=configured,
        lk_profile=lk_profile(),
        grid=tiny_grid(),
        backend=seeded_backend([]),
    )
    return build_ensemble_forecast_output_zarr_store(
        result,
        run_id=RUN_ID,
        job_id=MODEL_JOB_ID,
        issue_time=ISSUE_TIME,
        input_uri="s3://rainpulse/nowcast-input/rp023/input.zarr",
        input_asset_ids=INPUT_ASSET_IDS,
        profile=configured,
        grid=tiny_grid(),
        runtime_ms=321,
    )


def test_profile_schema_freezes_offline_probability_boundary() -> None:
    schema = json.loads(SCHEMA_PATH.read_text())
    raw = yaml.safe_load(PROFILE_PATH.read_text())

    Draft202012Validator.check_schema(schema)
    Draft202012Validator(schema).validate(raw)
    profile = load_ensemble_application_product_profile(PROFILE_PATH)

    assert profile.thresholds_mm_h == (1.0, 5.0, 10.0, 20.0, 50.0)
    assert profile.quantiles == (0.1, 0.5, 0.9)
    assert profile.operational_enabled is False
    assert profile.calibration_status.endswith("_uncalibrated")


def test_builds_complete_png_and_netcdf_layer_suite() -> None:
    forecast = ensemble_forecast_fixture()
    objects = build_ensemble_application_product_bundle(
        forecast,
        source_forecast_uri="s3://rainpulse/forecast/rp023/forecast.zarr",
        source_forecast_sha256=artifact_sha256(forecast),
        run_id=RUN_ID,
        job_id=PRODUCT_JOB_ID,
        profile=load_ensemble_application_product_profile(PROFILE_PATH),
        grid=tiny_grid(),
    )
    validation = validate_ensemble_application_product_bundle(objects)
    manifest = validation["manifest"]

    assert validation["layer_count"] == 8
    assert validation["asset_count"] == 384
    assert validation["object_count"] == 385
    assert manifest["member_count"] == 12
    assert manifest["operational_eligible"] is False
    assert manifest["source_forecast"]["sha256"] == artifact_sha256(forecast)

    probability = next(
        layer for layer in manifest["layers"] if layer["layer_id"] == "probability-gt-5"
    )
    quantile = next(
        layer for layer in manifest["layers"] if layer["layer_id"] == "quantile-p90"
    )
    assert probability["threshold_mm_h"] == 5.0
    assert probability["quantile"] is None
    assert quantile["threshold_mm_h"] is None
    assert quantile["quantile"] == 0.9
    assert len(probability["valid_times"]) == 24


def test_netcdf_preserves_probability_semantics_and_missing_cells() -> None:
    forecast = ensemble_forecast_fixture()
    objects = build_ensemble_application_product_bundle(
        forecast,
        source_forecast_uri="s3://rainpulse/forecast/rp023/forecast.zarr",
        source_forecast_sha256=artifact_sha256(forecast),
        run_id=RUN_ID,
        job_id=PRODUCT_JOB_ID,
        profile=load_ensemble_application_product_profile(PROFILE_PATH),
        grid=tiny_grid(),
    )
    manifest = json.loads(objects["manifest.json"])
    layer = next(
        item for item in manifest["layers"] if item["layer_id"] == "probability-gt-1"
    )
    asset = next(
        item
        for item in layer["assets"]
        if item["lead_time_minutes"] == 5 and item["media_type"] == NETCDF_MEDIA_TYPE
    )
    data = objects[asset["object_path"]]

    with netcdf_file(io.BytesIO(data), mode="r", mmap=False) as dataset:
        field = dataset.variables["prob_gt_1"]
        values = field[:].copy()
        assert dataset.dimensions == {"lat": 64, "lon": 64}
        assert field._attributes["units"] == b"1"
        assert field._attributes["event_operator"] == b"greater_than"
        assert float(field._attributes["threshold_mm_h"]) == 1.0
        assert np.all((values == -9999.0) | ((values >= 0.0) & (values <= 1.0)))
        assert dataset._attributes["operational_eligible"] == b"false"


def test_png_rendering_is_derived_without_mutating_source_values() -> None:
    forecast = ensemble_forecast_fixture()
    before = hashlib.sha256(b"".join(forecast[key] for key in sorted(forecast))).digest()
    objects = build_ensemble_application_product_bundle(
        forecast,
        source_forecast_uri="s3://rainpulse/forecast/rp023/forecast.zarr",
        source_forecast_sha256=artifact_sha256(forecast),
        run_id=RUN_ID,
        job_id=PRODUCT_JOB_ID,
        profile=load_ensemble_application_product_profile(PROFILE_PATH),
        grid=tiny_grid(),
    )
    after = hashlib.sha256(b"".join(forecast[key] for key in sorted(forecast))).digest()

    assert before == after
    assert any(path.endswith("layer.png") for path in objects)
