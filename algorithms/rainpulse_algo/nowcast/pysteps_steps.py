from __future__ import annotations

import sys
import warnings
from collections.abc import Callable, Mapping
from dataclasses import dataclass
from importlib import import_module
from typing import Any

import numpy as np
import zarr
from zarr.storage import MemoryStore

from rainpulse_algo.grid import RegularLatLonGrid

from .input_zarr import validate_nowcast_input_zarr_store
from .pysteps_lk import (
    PystepsLKFields,
    PystepsLKResult,
    _pysteps_function,
    run_pysteps_lk_fields,
)
from .pysteps_profile import PystepsLKProfile
from .steps_profile import PystepsStepsProfile


class PystepsStepsInputError(ValueError):
    """Raised when an input cannot safely produce an RP-022 STEPS ensemble."""


@dataclass(frozen=True)
class PystepsStepsResult:
    rain_rate: np.ndarray
    member_valid_mask: np.ndarray
    output_valid_mask: np.ndarray
    accum_60: np.ndarray
    accum_120: np.ndarray
    probability_exceedance: Mapping[float, np.ndarray]
    quantiles: Mapping[float, np.ndarray]
    deterministic: PystepsLKResult
    random_seed: int
    ensemble_fallback_used: bool
    ensemble_fallback_reason: str | None
    nominal_pixel_spacing_km: float


StepsBackend = Callable[..., np.ndarray]


def run_pysteps_steps(
    input_objects: Mapping[str, bytes],
    *,
    profile: PystepsStepsProfile,
    lk_profile: PystepsLKProfile,
    grid: RegularLatLonGrid,
    backend: StepsBackend | None = None,
) -> PystepsStepsResult:
    validate_nowcast_input_zarr_store(input_objects)
    store = MemoryStore()
    store.update({key: bytes(value) for key, value in input_objects.items()})
    root = zarr.open_group(store=store, mode="r")
    return run_pysteps_steps_fields(
        PystepsLKFields(
            reflectivity_dbz=root[lk_profile.motion.input_field][:],
            rate_mm_h=root["RATE_QPE"][:],
            quality_index=root["QUALITY_INDEX"][:],
            valid_mask=root["VALID_MASK"][:],
            low_quality_mask=root["LOW_QUALITY_MASK"][:],
        ),
        profile=profile,
        lk_profile=lk_profile,
        grid=grid,
        backend=backend,
    )


def run_pysteps_steps_fields(
    fields: PystepsLKFields,
    *,
    profile: PystepsStepsProfile,
    lk_profile: PystepsLKProfile,
    grid: RegularLatLonGrid,
    backend: StepsBackend | None = None,
) -> PystepsStepsResult:
    _validate_profile_pair(profile, lk_profile, grid)
    rate = np.asarray(fields.rate_mm_h, dtype="float32")
    valid = np.asarray(fields.valid_mask) == 1
    if rate.ndim != 3 or valid.shape != rate.shape:
        raise PystepsStepsInputError("STEPS input must be time x lat x lon")
    if rate.shape[1:] != grid.shape:
        raise PystepsStepsInputError("STEPS input shape differs from the configured grid")
    if not profile.sequence.minimum_frames <= rate.shape[0] <= profile.sequence.maximum_frames:
        raise PystepsStepsInputError("STEPS input frame count is outside profile bounds")
    if profile.support.input_missing_policy == "reject_any_missing" and not np.all(valid):
        raise PystepsStepsInputError(
            "RP-022 STEPS rejects missing input until member-specific support is implemented"
        )
    if np.any(~np.isfinite(rate[valid])) or np.any(rate[valid] < 0.0):
        raise PystepsStepsInputError("STEPS input contains invalid precipitation rates")
    working_rate = rate
    if (
        profile.support.input_missing_policy
        == "dry_floor_working_copy_preserve_deterministic_support"
    ):
        # pySTEPS requires finite rectangular arrays. Missing source cells are
        # dry only in this private compute copy; publication support continues
        # to come from the deterministic advected validity mask below.
        working_rate = np.where(valid, rate, 0.0).astype("float32")

    deterministic = run_pysteps_lk_fields(
        fields,
        profile=lk_profile,
        grid=grid,
    )
    member_count = profile.ensemble.member_count
    lead_count = lk_profile.extrapolation.lead_count
    nominal_spacing = _nominal_pixel_spacing_km(grid)
    fallback = False
    fallback_reason: str | None = None

    latest_trackable = valid[-1] & (
        rate[-1] >= profile.ensemble.precipitation_threshold_mm_h
    )
    latest_trackable_count = int(np.count_nonzero(latest_trackable))
    if latest_trackable_count == 0:
        members = np.zeros((member_count, lead_count, *grid.shape), dtype="float32")
        fallback = True
        fallback_reason = "no_trackable_precipitation"
    elif latest_trackable_count < profile.ensemble.minimum_trackable_precipitation_pixels:
        members = np.repeat(
            deterministic.persistence_rain_rate[np.newaxis, ...],
            member_count,
            axis=0,
        ).astype("float32")
        fallback = True
        fallback_reason = "insufficient_trackable_precipitation"
    else:
        ar_frames = profile.ensemble.autoregressive_order + 1
        transformed, metadata = _transform_to_db(
            working_rate[-ar_frames:],
            profile.ensemble.precipitation_threshold_mm_h,
        )
        forecast = backend or _load_steps_backend()
        kwargs: dict[str, Any] = {
            "n_ens_members": member_count,
            "n_cascade_levels": profile.ensemble.cascade_levels,
            "precip_thr": float(metadata["threshold"]),
            "kmperpixel": nominal_spacing,
            "timestep": profile.sequence.timestep_minutes,
            "extrap_method": "semilagrangian",
            "decomp_method": profile.ensemble.decomposition_method,
            "bandpass_filter_method": profile.ensemble.bandpass_filter_method,
            "noise_method": profile.ensemble.precipitation_noise_method,
            "noise_stddev_adj": _none_value(profile.ensemble.noise_stddev_adjustment),
            "ar_order": profile.ensemble.autoregressive_order,
            "vel_pert_method": profile.ensemble.velocity_perturbation_method,
            "conditional": False,
            "probmatching_method": _none_value(
                profile.ensemble.probability_matching_method
            ),
            "mask_method": profile.ensemble.mask_method,
            "seed": profile.ensemble.random_seed,
            "num_workers": profile.ensemble.num_workers,
            "fft_method": "numpy",
            "domain": profile.ensemble.domain,
            "return_output": True,
        }
        try:
            backend_output = forecast(
                transformed,
                deterministic.velocity_pixels_per_step,
                lead_count,
                **kwargs,
            )
        except Exception as exc:
            raise PystepsStepsInputError(
                f"pySTEPS-STEPS backend failed: {type(exc).__name__}: {exc}"
            ) from exc
        transformed_members = np.asarray(backend_output, dtype="float32")
        expected = (member_count, lead_count, *grid.shape)
        if transformed_members.shape != expected:
            raise PystepsStepsInputError(
                f"pySTEPS-STEPS returned {transformed_members.shape}, expected {expected}"
            )
        members = _transform_from_db(transformed_members, metadata)

    members = np.maximum(np.asarray(members, dtype="float32"), 0.0)
    deterministic_valid = np.asarray(deterministic.output_valid_mask) == 1
    member_valid = np.broadcast_to(deterministic_valid, members.shape) & np.isfinite(members)
    members[~member_valid] = np.nan
    output_valid = _output_support(member_valid, profile)
    if np.any(~np.isfinite(members[member_valid])):
        raise PystepsStepsInputError("STEPS ensemble is invalid inside member support")

    probability_exceedance = {
        threshold: _probability(members, member_valid, output_valid, threshold)
        for threshold in profile.probability_products.rain_rate_thresholds_mm_h
    }
    quantiles = {
        quantile: _quantile(members, member_valid, output_valid, quantile)
        for quantile in profile.probability_products.quantiles
    }
    return PystepsStepsResult(
        rain_rate=members,
        member_valid_mask=member_valid.astype("uint8"),
        output_valid_mask=output_valid.astype("uint8"),
        accum_60=_accumulate_members(members[:, :12], member_valid[:, :12]),
        accum_120=_accumulate_members(members, member_valid),
        probability_exceedance=probability_exceedance,
        quantiles=quantiles,
        deterministic=deterministic,
        random_seed=profile.ensemble.random_seed,
        ensemble_fallback_used=fallback,
        ensemble_fallback_reason=fallback_reason,
        nominal_pixel_spacing_km=nominal_spacing,
    )


def _validate_profile_pair(
    profile: PystepsStepsProfile,
    lk_profile: PystepsLKProfile,
    grid: RegularLatLonGrid,
) -> None:
    if profile.grid_id != grid.grid_id or profile.grid_config_version != grid.config_version:
        raise PystepsStepsInputError("STEPS profile grid differs from the runtime grid")
    if lk_profile.grid_id != grid.grid_id or lk_profile.grid_config_version != grid.config_version:
        raise PystepsStepsInputError("LK motion profile grid differs from the runtime grid")
    if profile.sequence.timestep_minutes != lk_profile.sequence.timestep_minutes:
        raise PystepsStepsInputError("STEPS and LK profile timesteps differ")
    if lk_profile.extrapolation.lead_count != 24:
        raise PystepsStepsInputError("RP-022 requires 24 five-minute lead times")


def _transform_to_db(values: np.ndarray, threshold: float) -> tuple[np.ndarray, dict[str, Any]]:
    transform = _pysteps_function("utils.transformation", "dB_transform")
    transformed, metadata = transform(
        np.asarray(values, dtype="float32"),
        metadata={"transform": None},
        threshold=threshold,
    )
    return np.asarray(transformed, dtype="float32"), dict(metadata)


def _transform_from_db(values: np.ndarray, metadata: Mapping[str, Any]) -> np.ndarray:
    transform = _pysteps_function("utils.transformation", "dB_transform")
    restored, _ = transform(
        np.asarray(values, dtype="float32"),
        metadata=dict(metadata),
        inverse=True,
    )
    return np.asarray(restored, dtype="float32")


def _nominal_pixel_spacing_km(grid: RegularLatLonGrid) -> float:
    metric = grid.metric()
    x = float(np.median(metric.x_spacing_m_by_latitude))
    y = float(np.median(metric.y_spacing_m_by_latitude))
    value = float(np.sqrt(x * y) / 1000.0)
    if not np.isfinite(value) or value <= 0.0:
        raise PystepsStepsInputError("grid has invalid nominal pixel spacing")
    return value


def _none_value(value: str) -> str | None:
    return None if value == "none" else value


def _load_steps_backend() -> StepsBackend:
    """Hydrate interfaces needed by STEPS after the minimal LK package loader.

    The deterministic adapter intentionally imports individual pure-Python
    pySTEPS modules. STEPS itself expects selected package-level interfaces, so
    attach only those interfaces without changing the deterministic loader.
    """

    for package_name in ("cascade", "extrapolation", "noise", "utils"):
        get_method = _pysteps_function(f"{package_name}.interface", "get_method")
        package = sys.modules[f"pysteps.{package_name}"]
        setattr(package, "get_method", get_method)
    noise_package = sys.modules["pysteps.noise"]
    for child_name in ("utils", "motion", "fftgenerators"):
        setattr(
            noise_package,
            child_name,
            import_module(f"pysteps.noise.{child_name}"),
        )
    return _pysteps_function("nowcasts.steps", "forecast")


def _output_support(
    member_valid: np.ndarray,
    profile: PystepsStepsProfile,
) -> np.ndarray:
    policy = profile.support.output_support_policy
    if policy == "deterministic_support_intersect_all_members_finite":
        return np.all(member_valid, axis=0)
    if policy == "deterministic_support_minimum_members_finite":
        minimum = profile.support.minimum_valid_members
        if minimum is None:
            raise PystepsStepsInputError(
                "minimum-member STEPS support requires a member threshold"
            )
        return np.count_nonzero(member_valid, axis=0) >= minimum
    raise PystepsStepsInputError("unsupported STEPS output support policy")


def _probability(
    members: np.ndarray,
    member_valid: np.ndarray,
    output_valid: np.ndarray,
    threshold: float,
) -> np.ndarray:
    valid_count = np.count_nonzero(member_valid, axis=0)
    exceedance_count = np.count_nonzero(
        member_valid & (members > threshold), axis=0
    )
    values = np.divide(
        exceedance_count,
        valid_count,
        out=np.zeros(valid_count.shape, dtype="float32"),
        where=valid_count > 0,
    )
    values[~output_valid] = np.nan
    return values


def _quantile(
    members: np.ndarray,
    member_valid: np.ndarray,
    output_valid: np.ndarray,
    quantile: float,
) -> np.ndarray:
    with warnings.catch_warnings():
        warnings.simplefilter("ignore", RuntimeWarning)
        values = np.nanquantile(
            np.where(member_valid, members, np.nan), quantile, axis=0
        ).astype("float32")
    values[~output_valid] = np.nan
    return values


def _accumulate_members(rates: np.ndarray, valid_masks: np.ndarray) -> np.ndarray:
    valid = np.all(valid_masks, axis=1)
    values = np.sum(np.where(valid_masks, rates, 0.0), axis=1)
    values = (values * np.float32(5.0 / 60.0)).astype("float32")
    values[~valid] = np.nan
    return values
