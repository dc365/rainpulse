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
from rainpulse_algo.nowcast.steps_profile import (
    StepsSupportConfig,
    load_pysteps_steps_profile,
)

from .test_pysteps_lk import INPUT_ASSET_IDS, ISSUE_TIME, tiny_grid
from .test_pysteps_lk import profile as lk_profile

REPOSITORY_ROOT = Path(__file__).resolve().parents[2]
PROFILE_PATH = REPOSITORY_ROOT / "configs" / "nowcast" / "rp022-pysteps-steps-v1.yaml"
RP024_PROFILE_PATH = (
    REPOSITORY_ROOT / "configs" / "nowcast" / "rp024-pysteps-steps-v1.yaml"
)
RP039_PROFILE_PATH = (
    REPOSITORY_ROOT / "configs" / "nowcast" / "rp039-pysteps-steps-history-v1.yaml"
)
RP039_V2_PROFILE_PATH = (
    REPOSITORY_ROOT / "configs" / "nowcast" / "rp039-pysteps-steps-history-v2.yaml"
)
RP039_V3_PROFILE_PATH = (
    REPOSITORY_ROOT / "configs" / "nowcast" / "rp039-pysteps-steps-history-v3.yaml"
)
RP039_V4_PROFILE_PATH = (
    REPOSITORY_ROOT / "configs" / "nowcast" / "rp039-pysteps-steps-history-v4.yaml"
)
RP049_PROFILE_PATH = (
    REPOSITORY_ROOT / "configs" / "nowcast" / "rp049-pysteps-steps-history-v5.yaml"
)
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
    rp024_raw = yaml.safe_load(RP024_PROFILE_PATH.read_text())
    Draft202012Validator(schema).validate(rp024_raw)
    rp024 = load_pysteps_steps_profile(RP024_PROFILE_PATH)
    assert rp024.ensemble.minimum_trackable_precipitation_pixels == 64
    rp039_raw = yaml.safe_load(RP039_PROFILE_PATH.read_text())
    Draft202012Validator(schema).validate(rp039_raw)
    rp039 = load_pysteps_steps_profile(RP039_PROFILE_PATH)
    assert rp039.support.input_missing_policy.startswith("dry_floor_working_copy")
    rp039_v2_raw = yaml.safe_load(RP039_V2_PROFILE_PATH.read_text())
    Draft202012Validator(schema).validate(rp039_v2_raw)
    rp039_v2 = load_pysteps_steps_profile(RP039_V2_PROFILE_PATH)
    assert rp039_v2.ensemble.noise_stddev_adjustment == "none"
    rp039_v3_raw = yaml.safe_load(RP039_V3_PROFILE_PATH.read_text())
    Draft202012Validator(schema).validate(rp039_v3_raw)
    rp039_v3 = load_pysteps_steps_profile(RP039_V3_PROFILE_PATH)
    assert rp039_v3.ensemble.autoregressive_order == 1
    rp039_v4_raw = yaml.safe_load(RP039_V4_PROFILE_PATH.read_text())
    Draft202012Validator(schema).validate(rp039_v4_raw)
    rp039_v4 = load_pysteps_steps_profile(RP039_V4_PROFILE_PATH)
    assert rp039_v4.ensemble.precipitation_noise_method == "parametric"
    rp049_raw = yaml.safe_load(RP049_PROFILE_PATH.read_text())
    Draft202012Validator(schema).validate(rp049_raw)
    rp049 = load_pysteps_steps_profile(RP049_PROFILE_PATH)
    assert rp049.support.output_support_policy.endswith("minimum_members_finite")
    assert rp049.support.minimum_valid_members == 9


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


def test_historical_missing_policy_uses_finite_working_copy_and_preserves_support() -> None:
    calls: list[dict[str, object]] = []

    def finite_backend(precip, velocity, timesteps, **kwargs):
        assert np.all(np.isfinite(precip))
        return seeded_backend(calls)(precip, velocity, timesteps, **kwargs)

    configured = replace(
        steps_profile(),
        support=StepsSupportConfig(
            "dry_floor_working_copy_preserve_deterministic_support",
            "deterministic_support_intersect_all_members_finite",
        ),
    )
    result = run_pysteps_steps_fields(
        steps_fields(missing=True),
        profile=configured,
        lk_profile=lk_profile(),
        grid=tiny_grid(),
        backend=finite_backend,
    )

    assert calls
    assert np.any(result.output_valid_mask == 0)
    assert np.all(np.isnan(result.rain_rate[result.member_valid_mask == 0]))


def test_historical_missing_policy_runs_frozen_backend_on_partial_radar_domain() -> None:
    fields = steps_fields()
    valid = np.zeros_like(fields.valid_mask, dtype="uint8")
    valid[:, :, :32] = 1
    partial = PystepsLKFields(
        reflectivity_dbz=np.where(valid == 1, fields.reflectivity_dbz, np.nan),
        rate_mm_h=np.where(valid == 1, fields.rate_mm_h, np.nan),
        quality_index=np.where(valid == 1, fields.quality_index, np.nan),
        valid_mask=valid,
        low_quality_mask=fields.low_quality_mask,
    )
    configured = load_pysteps_steps_profile(RP039_V4_PROFILE_PATH)
    configured = replace(
        configured,
        grid_id=tiny_grid().grid_id,
        grid_config_version=tiny_grid().config_version,
        ensemble=replace(configured.ensemble, member_count=2, cascade_levels=3),
    )

    result = run_pysteps_steps_fields(
        partial,
        profile=configured,
        lk_profile=lk_profile(),
        grid=tiny_grid(),
    )

    assert result.rain_rate.shape == (2, 24, 64, 64)
    assert np.all(np.isnan(result.rain_rate[result.member_valid_mask == 0]))


def test_minimum_member_support_keeps_probabilistic_products_inside_deterministic_domain() -> None:
    def member_dropout_backend(_precip, _velocity, timesteps, **kwargs):
        values = np.full(
            (kwargs["n_ens_members"], timesteps, 64, 64),
            10.0,
            dtype="float32",
        )
        # Three perturbed members leave this otherwise valid deterministic cell.
        values[-3:, :, 30, 30] = np.nan
        return values

    configured = replace(
        steps_profile(),
        support=StepsSupportConfig(
            "reject_any_missing",
            "deterministic_support_minimum_members_finite",
            9,
        ),
    )
    result = run_pysteps_steps_fields(
        steps_fields(),
        profile=configured,
        lk_profile=lk_profile(),
        grid=tiny_grid(),
        backend=member_dropout_backend,
    )

    assert result.member_valid_mask[:, 0, 30, 30].sum() == 9
    assert result.output_valid_mask[0, 30, 30] == 1
    assert result.probability_exceedance[5.0][0, 30, 30] == pytest.approx(1.0)
    assert result.quantiles[0.5][0, 30, 30] == pytest.approx(10.0)
    objects = build_ensemble_forecast_output_zarr_store(
        result,
        run_id=UUID("9a000000-0000-4000-8000-000000000003"),
        job_id=UUID("9a000000-0000-4000-8000-000000000004"),
        issue_time=ISSUE_TIME,
        input_uri="s3://rainpulse/nowcast-input/rp049/input.zarr",
        input_asset_ids=INPUT_ASSET_IDS,
        profile=configured,
        grid=tiny_grid(),
        runtime_ms=321,
    )
    validation = validate_ensemble_forecast_output_zarr_store(objects)
    all_member_coverage = np.mean(
        np.all(result.member_valid_mask[:, 0] == 1, axis=0)
    )
    assert validation["first_lead_valid_coverage_ratio"] > all_member_coverage


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


def test_sparse_precipitation_uses_persistence_ensemble_without_stochastic_backend() -> None:
    def unexpected_backend(*_args, **_kwargs):
        raise AssertionError("STEPS backend must not run for a sparse non-trackable field")

    configured = load_pysteps_steps_profile(RP024_PROFILE_PATH)
    grid = tiny_grid()
    configured = replace(
        configured,
        grid_id=grid.grid_id,
        grid_config_version=grid.config_version,
    )
    fields = steps_fields(rain=False)
    fields.rate_mm_h[:, 30, 30] = 0.1
    fields.reflectivity_dbz[:, 30, 30] = 8.0

    result = run_pysteps_steps_fields(
        fields,
        profile=configured,
        lk_profile=lk_profile(),
        grid=grid,
        backend=unexpected_backend,
    )

    assert result.ensemble_fallback_used is True
    assert result.ensemble_fallback_reason == "insufficient_trackable_precipitation"
    np.testing.assert_allclose(
        result.rain_rate,
        np.repeat(result.deterministic.persistence_rain_rate[np.newaxis], 12, axis=0),
        equal_nan=True,
    )
