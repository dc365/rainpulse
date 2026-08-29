from __future__ import annotations

import json
from dataclasses import replace
from pathlib import Path
from uuid import UUID

import numpy as np
import pytest
import yaml
from jsonschema import Draft202012Validator

from rainpulse_algo.nowcast.ensemble_zarr import (
    build_ensemble_forecast_output_zarr_store,
    validate_ensemble_forecast_output_zarr_store,
)
from rainpulse_algo.nowcast.pysteps_lk import PystepsLKFields
from rainpulse_algo.nowcast.pysteps_steps import (
    PystepsStepsInputError,
    run_pysteps_steps_fields,
)
from rainpulse_algo.nowcast.steps_profile import load_pysteps_steps_profile

from .test_pysteps_lk import INPUT_ASSET_IDS, ISSUE_TIME, tiny_grid
from .test_pysteps_lk import profile as lk_profile

REPOSITORY_ROOT = Path(__file__).resolve().parents[2]
PROFILE_PATH = REPOSITORY_ROOT / "configs" / "nowcast" / "rp022-pysteps-steps-v1.yaml"
SCHEMA_PATH = REPOSITORY_ROOT / "configs" / "schemas" / "pysteps-steps-profile.schema.json"
PRODUCT_PROFILE_PATH = (
    REPOSITORY_ROOT / "configs" / "products" / "rp022-ensemble-products-v1.yaml"
)
PRODUCT_SCHEMA_PATH = (
    REPOSITORY_ROOT / "configs" / "schemas" / "ensemble-product-profile.schema.json"
)


def steps_profile():
    configured = load_pysteps_steps_profile(PROFILE_PATH)
    grid = tiny_grid()
    return replace(
        configured,
        grid_id=grid.grid_id,
        grid_config_version=grid.config_version,
    )


def steps_fields(*, rain: bool = True, missing: bool = False) -> PystepsLKFields:
    grid = tiny_grid()
    shape = (3, *grid.shape)
    reflectivity = np.zeros(shape, dtype="float32")
    rate = np.zeros(shape, dtype="float32")
    if rain:
        for frame, x_start in enumerate((18, 20, 22)):
            reflectivity[frame, 20:40, x_start : x_start + 20] = 35.0
            rate[frame, 20:40, x_start : x_start + 20] = 8.0
    quality = np.full(shape, 0.9, dtype="float32")
    valid = np.ones(shape, dtype="uint8")
    low_quality = np.zeros(shape, dtype="uint8")
    if missing:
        valid[:, :, -2:] = 0
        for values in (reflectivity, rate, quality):
            values[valid == 0] = np.nan
    return PystepsLKFields(
        reflectivity_dbz=reflectivity,
        rate_mm_h=rate,
        quality_index=quality,
        valid_mask=valid,
        low_quality_mask=low_quality,
    )


def seeded_backend(calls: list[dict[str, object]]):
    def forecast(precip, velocity, timesteps, **kwargs):
        calls.append(dict(kwargs))
        assert precip.shape == (3, 64, 64)
        assert velocity.shape == (2, 64, 64)
        rng = np.random.default_rng(kwargs["seed"])
        latest = np.asarray(precip[-1], dtype="float32")
        noise = rng.normal(
            0.0,
            0.35,
            size=(kwargs["n_ens_members"], timesteps, *latest.shape),
        ).astype("float32")
        return latest[np.newaxis, np.newaxis, ...] + noise

    return forecast


def test_profile_conforms_to_schema_and_freezes_probability_semantics() -> None:
    schema = json.loads(SCHEMA_PATH.read_text())
    raw = yaml.safe_load(PROFILE_PATH.read_text())

    Draft202012Validator.check_schema(schema)
    Draft202012Validator(schema).validate(raw)
    configured = load_pysteps_steps_profile(PROFILE_PATH)

    assert configured.ensemble.member_count == 12
    assert configured.ensemble.random_seed == 20260829
    assert configured.probability_products.event_operator == "greater_than"
    assert configured.probability_products.calibration_status.endswith("_uncalibrated")


def test_probability_product_profile_matches_the_model_profile_and_stays_offline() -> None:
    schema = json.loads(PRODUCT_SCHEMA_PATH.read_text())
    products = yaml.safe_load(PRODUCT_PROFILE_PATH.read_text())
    configured = load_pysteps_steps_profile(PROFILE_PATH)

    Draft202012Validator.check_schema(schema)
    Draft202012Validator(schema).validate(products)
    assert tuple(products["rain_rate_exceedance"]["thresholds_mm_h"]) == (
        configured.probability_products.rain_rate_thresholds_mm_h
    )
    assert tuple(products["rain_rate_quantiles"]["quantiles"]) == (
        configured.probability_products.quantiles
    )
    assert products["publication"]["offline_evaluation_enabled"] is True
    assert products["publication"]["operational_enabled"] is False


def test_seeded_ensemble_is_reproducible_and_derives_probabilities() -> None:
    calls: list[dict[str, object]] = []
    configured = steps_profile()
    first = run_pysteps_steps_fields(
        steps_fields(),
        profile=configured,
        lk_profile=lk_profile(),
        grid=tiny_grid(),
        backend=seeded_backend(calls),
    )
    second = run_pysteps_steps_fields(
        steps_fields(),
        profile=configured,
        lk_profile=lk_profile(),
        grid=tiny_grid(),
        backend=seeded_backend(calls),
    )

    assert first.rain_rate.shape == (12, 24, 64, 64)
    assert first.member_valid_mask.shape == first.rain_rate.shape
    np.testing.assert_array_equal(
        first.output_valid_mask,
        np.all(first.member_valid_mask == 1, axis=0).astype("uint8"),
    )
    np.testing.assert_allclose(first.rain_rate, second.rain_rate, equal_nan=True)
    assert calls[0]["seed"] == 20260829
    assert calls[0]["n_ens_members"] == 12
    assert calls[0]["n_cascade_levels"] == 6
    assert calls[0]["noise_method"] == "nonparametric"
    assert calls[0]["vel_pert_method"] == "bps"
    assert calls[0]["mask_method"] == "incremental"
    assert first.probability_exceedance[1.0].shape == (24, 64, 64)
    valid = first.output_valid_mask == 1
    assert np.all(first.probability_exceedance[1.0][valid] >= 0.0)
    assert np.all(first.probability_exceedance[1.0][valid] <= 1.0)
    assert np.all(np.isnan(first.probability_exceedance[1.0][~valid]))
    assert first.quantiles[0.1].shape == (24, 64, 64)
    assert first.accum_60.shape == (12, 64, 64)
    assert first.accum_120.shape == (12, 64, 64)


def test_writes_and_validates_forecast_output_v12() -> None:
    configured = steps_profile()
    result = run_pysteps_steps_fields(
        steps_fields(),
        profile=configured,
        lk_profile=lk_profile(),
        grid=tiny_grid(),
        backend=seeded_backend([]),
    )
    objects = build_ensemble_forecast_output_zarr_store(
        result,
        run_id=UUID("9a000000-0000-4000-8000-000000000001"),
        job_id=UUID("9a000000-0000-4000-8000-000000000002"),
        issue_time=ISSUE_TIME,
        input_uri="s3://rainpulse/nowcast-input/rp022/input.zarr",
        input_asset_ids=INPUT_ASSET_IDS,
        profile=configured,
        grid=tiny_grid(),
        runtime_ms=321,
    )

    validation = validate_ensemble_forecast_output_zarr_store(objects)

    assert validation["shape"] == (12, 24, 64, 64)
    assert validation["member_count"] == 12
    assert validation["random_seed"] == 20260829
    assert validation["probability_calibration_status"].endswith("_uncalibrated")


def test_frozen_pysteps_steps_backend_runs_without_an_injected_stub() -> None:
    configured = steps_profile()
    configured = replace(
        configured,
        ensemble=replace(configured.ensemble, member_count=2, cascade_levels=3),
    )

    result = run_pysteps_steps_fields(
        steps_fields(),
        profile=configured,
        lk_profile=lk_profile(),
        grid=tiny_grid(),
    )

    assert result.rain_rate.shape == (2, 24, 64, 64)
    assert np.any(result.member_valid_mask == 1)
    assert np.all(np.isnan(result.rain_rate[result.member_valid_mask == 0]))
    assert result.ensemble_fallback_used is False


def test_rejects_missing_input_instead_of_turning_it_into_no_rain() -> None:
    with pytest.raises(PystepsStepsInputError, match="rejects missing input"):
        run_pysteps_steps_fields(
            steps_fields(missing=True),
            profile=steps_profile(),
            lk_profile=lk_profile(),
            grid=tiny_grid(),
            backend=seeded_backend([]),
        )


def test_wraps_backend_failure_for_independent_lk_fallback_orchestration() -> None:
    def failing_backend(*_args, **_kwargs):
        raise RuntimeError("nonstationary AR(p) process")

    with pytest.raises(
        PystepsStepsInputError,
        match=r"backend failed: RuntimeError: nonstationary AR\(p\) process",
    ):
        run_pysteps_steps_fields(
            steps_fields(),
            profile=steps_profile(),
            lk_profile=lk_profile(),
            grid=tiny_grid(),
            backend=failing_backend,
        )


def test_no_rain_uses_explicit_zero_ensemble_without_calling_stochastic_backend() -> None:
    def unexpected_backend(*_args, **_kwargs):
        raise AssertionError("STEPS backend must not run for no-rain input")

    result = run_pysteps_steps_fields(
        steps_fields(rain=False),
        profile=steps_profile(),
        lk_profile=lk_profile(),
        grid=tiny_grid(),
        backend=unexpected_backend,
    )

    assert result.ensemble_fallback_used is True
    assert result.ensemble_fallback_reason == "no_trackable_precipitation"
    assert np.all(result.rain_rate == 0.0)
    assert np.all(result.probability_exceedance[1.0] == 0.0)
