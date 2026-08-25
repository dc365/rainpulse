from __future__ import annotations

import sys
from collections.abc import Callable, Mapping
from dataclasses import dataclass
from importlib import import_module
from importlib.util import find_spec
from pathlib import Path
from types import ModuleType
from typing import Any

import numpy as np
import zarr
from zarr.storage import MemoryStore

from rainpulse_algo.grid import RegularLatLonGrid

from .input_zarr import validate_nowcast_input_zarr_store
from .pysteps_profile import PystepsLKProfile


class PystepsLKInputError(ValueError):
    """Raised when a committed NowcastInput cannot safely run RP-014."""


@dataclass(frozen=True)
class PystepsLKResult:
    rain_rate: np.ndarray
    output_valid_mask: np.ndarray
    confidence: np.ndarray
    motion_u: np.ndarray
    motion_v: np.ndarray
    persistence_rain_rate: np.ndarray
    persistence_valid_mask: np.ndarray
    translation_rain_rate: np.ndarray
    translation_valid_mask: np.ndarray
    accum_60: np.ndarray
    accum_120: np.ndarray
    velocity_pixels_per_step: np.ndarray
    global_translation_pixels_per_step: tuple[float, float]
    motion_fallback_used: bool
    trackable_rain_pixel_count: int


MotionEstimator = Callable[[np.ndarray, PystepsLKProfile], np.ndarray]
Extrapolator = Callable[[np.ndarray, np.ndarray, int, int], np.ndarray]


def run_pysteps_lk(
    input_objects: Mapping[str, bytes],
    *,
    profile: PystepsLKProfile,
    grid: RegularLatLonGrid,
    motion_estimator: MotionEstimator | None = None,
    extrapolator: Extrapolator | None = None,
) -> PystepsLKResult:
    validate_nowcast_input_zarr_store(input_objects)
    store = MemoryStore()
    store.update({key: bytes(value) for key, value in input_objects.items()})
    root = zarr.open_group(store=store, mode="r")
    _validate_identity(root, profile, grid)

    valid_sequence = root["VALID_MASK"][:] == 1
    reflectivity = root[profile.motion.input_field][:].astype("float32", copy=True)
    working_reflectivity = np.where(
        valid_sequence,
        reflectivity,
        np.float32(profile.motion.working_missing_fill_dbz),
    )
    trackable = valid_sequence & (working_reflectivity >= profile.motion.rain_threshold_dbz)
    trackable_count = int(np.count_nonzero(trackable[-2:]))
    fallback = trackable_count < profile.motion.minimum_trackable_rain_pixels
    if fallback:
        velocity = np.zeros((2, *grid.shape), dtype="float32")
    else:
        if motion_estimator is None:
            velocity, has_features = _estimate_dense_lucas_kanade(
                working_reflectivity,
                profile,
            )
            if not has_features:
                fallback = True
                velocity = np.zeros((2, *grid.shape), dtype="float32")
        else:
            velocity = motion_estimator(working_reflectivity, profile)
        velocity = np.asarray(velocity, dtype="float32")
        if velocity.shape != (2, *grid.shape) or np.any(~np.isfinite(velocity)):
            raise PystepsLKInputError("Lucas-Kanade returned an invalid dense motion field")

    extrapolate = extrapolator or _semilagrangian
    lead_count = profile.extrapolation.lead_count
    latest_rate = root["RATE_QPE"][-1].astype("float32")
    latest_valid = valid_sequence[-1]
    latest_quality = root["QUALITY_INDEX"][-1].astype("float32")
    latest_low_quality = root["LOW_QUALITY_MASK"][-1] == 1

    rain_rate, output_valid = _forecast_with_support(
        latest_rate,
        latest_valid,
        velocity,
        lead_count,
        profile.extrapolation.interpolation_order,
        extrapolate,
    )
    persistence_rate = np.repeat(latest_rate[np.newaxis, ...], lead_count, axis=0)
    persistence_valid = np.repeat(latest_valid[np.newaxis, ...], lead_count, axis=0)
    persistence_rate[~persistence_valid] = np.nan

    translation = _global_translation(velocity, trackable[-1])
    translation_velocity = np.empty_like(velocity)
    translation_velocity[0] = translation[0]
    translation_velocity[1] = translation[1]
    translation_rate, translation_valid = _forecast_with_support(
        latest_rate,
        latest_valid,
        translation_velocity,
        lead_count,
        profile.extrapolation.interpolation_order,
        extrapolate,
    )

    confidence = _forecast_confidence(
        latest_quality,
        latest_low_quality,
        latest_valid,
        velocity,
        profile,
        extrapolate,
    )
    confidence[~output_valid] = np.nan
    accum_60 = _accumulate(rain_rate[:12], output_valid[:12])
    accum_120 = _accumulate(rain_rate, output_valid)
    metric = grid.metric()
    seconds_per_step = profile.sequence.timestep_minutes * 60.0
    motion_u = (
        velocity[0] * metric.x_spacing_m_by_latitude[:, np.newaxis] / seconds_per_step
    ).astype("float32")
    motion_v = (
        velocity[1] * metric.y_spacing_m_by_latitude[:, np.newaxis] / seconds_per_step
    ).astype("float32")

    return PystepsLKResult(
        rain_rate=rain_rate[np.newaxis, ...].astype("float32"),
        output_valid_mask=output_valid.astype("uint8"),
        confidence=confidence.astype("float32"),
        motion_u=motion_u,
        motion_v=motion_v,
        persistence_rain_rate=persistence_rate.astype("float32"),
        persistence_valid_mask=persistence_valid.astype("uint8"),
        translation_rain_rate=translation_rate.astype("float32"),
        translation_valid_mask=translation_valid.astype("uint8"),
        accum_60=accum_60[np.newaxis, ...].astype("float32"),
        accum_120=accum_120[np.newaxis, ...].astype("float32"),
        velocity_pixels_per_step=velocity,
        global_translation_pixels_per_step=translation,
        motion_fallback_used=fallback,
        trackable_rain_pixel_count=trackable_count,
    )


def _validate_identity(
    root: zarr.Group,
    profile: PystepsLKProfile,
    grid: RegularLatLonGrid,
) -> None:
    expected: tuple[tuple[str, Any, Any], ...] = (
        ("contract_version", root.attrs.get("contract_version"), "1.2"),
        ("grid_id", root.attrs.get("grid_id"), profile.grid_id),
        ("grid_id", root.attrs.get("grid_id"), grid.grid_id),
        (
            "grid_config_version",
            root.attrs.get("grid_config_version"),
            profile.grid_config_version,
        ),
        ("grid_config_version", root.attrs.get("grid_config_version"), grid.config_version),
        ("coordinate_sha256", root.attrs.get("coordinate_sha256"), grid.coordinate_sha256),
        ("grid_metric_version", root.attrs.get("grid_metric_version"), grid.metric().version),
        ("timestep_minutes", root.attrs.get("timestep_minutes"), 5),
    )
    for name, actual, configured in expected:
        if actual != configured:
            raise PystepsLKInputError(f"NowcastInput {name} differs from the RP-014 profile/grid")
    frame_count = len(root["time"])
    if not profile.sequence.minimum_frames <= frame_count <= profile.sequence.maximum_frames:
        raise PystepsLKInputError("NowcastInput frame count is outside RP-014 bounds")
    if not np.array_equal(root["lat"][:], grid.latitude) or not np.array_equal(
        root["lon"][:], grid.longitude
    ):
        raise PystepsLKInputError("NowcastInput coordinates differ from immutable grid")
    for name in ("input_asset_ids", "analysis_ids"):
        values = root.attrs.get(name)
        if not isinstance(values, list) or len(values) < 3:
            raise PystepsLKInputError(f"NowcastInput is missing {name}")


def _estimate_dense_lucas_kanade(
    images: np.ndarray,
    profile: PystepsLKProfile,
) -> tuple[np.ndarray, bool]:
    dense_lucaskanade = _pysteps_function(
        "motion.lucaskanade",
        "dense_lucaskanade",
    )

    lk = profile.motion.lucas_kanade
    parameters = {
        "fd_method": lk.feature_detection,
        "interp_method": lk.interpolation,
        "nr_std_outlier": lk.outlier_stddev,
        "k_outlier": lk.outlier_neighbours,
        "size_opening": lk.opening_size_pixels,
        "decl_scale": lk.decluster_scale_pixels,
        "verbose": False,
    }
    sparse_xy, _ = dense_lucaskanade(images, dense=False, **parameters)
    if len(sparse_xy) == 0:
        return np.zeros((2, *images.shape[-2:]), dtype="float32"), False
    velocity = dense_lucaskanade(
        images,
        dense=True,
        **parameters,
    )
    return velocity, True


def _semilagrangian(
    field: np.ndarray,
    velocity: np.ndarray,
    lead_count: int,
    interpolation_order: int,
) -> np.ndarray:
    extrapolate = _pysteps_function("extrapolation.semilagrangian", "extrapolate")

    return np.asarray(
        extrapolate(
            field,
            velocity,
            lead_count,
            outval=np.nan,
            allow_nonfinite_values=False,
            interp_order=interpolation_order,
        )
    )


def _pysteps_function(module_name: str, function_name: str) -> Any:
    """Load the used pure-Python modules without importing optional C motion methods."""
    if "pysteps" not in sys.modules:
        spec = find_spec("pysteps")
        if spec is None or not spec.submodule_search_locations:
            raise PystepsLKInputError("the frozen pySTEPS runtime is unavailable")
        package = ModuleType("pysteps")
        package.__path__ = list(spec.submodule_search_locations)  # type: ignore[attr-defined]
        package.__package__ = "pysteps"
        sys.modules["pysteps"] = package
    root = Path(next(iter(sys.modules["pysteps"].__path__)))  # type: ignore[attr-defined]
    parent_name = module_name.split(".", 1)[0]
    qualified_parent = f"pysteps.{parent_name}"
    if qualified_parent not in sys.modules:
        parent = ModuleType(qualified_parent)
        parent.__path__ = [str(root / parent_name)]  # type: ignore[attr-defined]
        parent.__package__ = qualified_parent
        sys.modules[qualified_parent] = parent
    module = import_module(f"pysteps.{module_name}")
    return getattr(module, function_name)


def _forecast_with_support(
    latest_rate: np.ndarray,
    latest_valid: np.ndarray,
    velocity: np.ndarray,
    lead_count: int,
    interpolation_order: int,
    extrapolate: Extrapolator,
) -> tuple[np.ndarray, np.ndarray]:
    working_rate = np.where(latest_valid, latest_rate, 0.0).astype("float32")
    forecast = extrapolate(working_rate, velocity, lead_count, interpolation_order).astype(
        "float32"
    )
    support = extrapolate(latest_valid.astype("float32"), velocity, lead_count, 0)
    valid = (support >= 0.5) & np.isfinite(forecast)
    forecast[~valid] = np.nan
    forecast[valid] = np.maximum(forecast[valid], 0.0)
    return forecast, valid


def _forecast_confidence(
    latest_quality: np.ndarray,
    latest_low_quality: np.ndarray,
    latest_valid: np.ndarray,
    velocity: np.ndarray,
    profile: PystepsLKProfile,
    extrapolate: Extrapolator,
) -> np.ndarray:
    lead_count = profile.extrapolation.lead_count
    quality = extrapolate(
        np.where(latest_valid, latest_quality, 0.0).astype("float32"),
        velocity,
        lead_count,
        profile.extrapolation.interpolation_order,
    )
    low = extrapolate(latest_low_quality.astype("float32"), velocity, lead_count, 0)
    lead_minutes = (
        np.arange(1, lead_count + 1, dtype="float32") * profile.extrapolation.lead_step_minutes
    )
    decay = np.exp(-lead_minutes / profile.confidence.decay_minutes)[:, None, None]
    factors = np.where(low >= 0.5, profile.confidence.low_quality_factor, 1.0)
    return np.clip(quality * decay * factors, 0.0, 1.0)


def _global_translation(
    velocity: np.ndarray,
    trackable: np.ndarray,
) -> tuple[float, float]:
    selection = trackable & np.isfinite(velocity[0]) & np.isfinite(velocity[1])
    if not np.any(selection):
        selection = np.isfinite(velocity[0]) & np.isfinite(velocity[1])
    return float(np.median(velocity[0][selection])), float(np.median(velocity[1][selection]))


def _accumulate(rates: np.ndarray, valid_masks: np.ndarray) -> np.ndarray:
    valid = np.all(valid_masks, axis=0)
    values = np.sum(np.where(valid_masks, rates, 0.0), axis=0) * np.float32(5.0 / 60.0)
    values = values.astype("float32")
    values[~valid] = np.nan
    return values
