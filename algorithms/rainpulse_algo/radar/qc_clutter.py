from __future__ import annotations

import hashlib
from collections.abc import Iterable, Mapping
from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Any

import numpy as np

MINIMUM_CLEAR_SKY_DAYS = 20
MINIMUM_GATE_OBSERVATIONS = 200
GROUND_CLUTTER_DBZH_THRESHOLD = 10.0
STATIC_GROUND_CLUTTER_ASSET_VERSION = "radar-static-ground-clutter-v1"


class RadarQCClutterInputError(ValueError):
    """Raised when a static ground-clutter prior cannot be built safely."""


@dataclass(frozen=True)
class StaticGroundClutterAsset:
    radar_id: str
    elevation_deg_by_sweep: dict[str, float]
    geometry_by_sweep: dict[str, dict[str, Any]]
    asset_version: str
    dbzh_threshold: float
    minimum_clear_sky_days: int
    minimum_gate_observations: int
    probability_by_sweep: dict[str, np.ndarray]
    support_count_by_sweep: dict[str, np.ndarray]
    clear_sky_day_count_by_sweep: dict[str, int]


def build_static_ground_clutter_asset(
    samples: Iterable[Mapping[str, object]],
) -> StaticGroundClutterAsset:
    observed_count_by_sweep: dict[str, np.ndarray] = {}
    hit_count_by_sweep: dict[str, np.ndarray] = {}
    clear_sky_days_by_sweep: dict[str, set[str]] = {}
    geometry_by_sweep: dict[str, tuple[np.ndarray, np.ndarray]] = {}

    seen_observations: set[tuple[str, str, str]] = set()
    radar_id: str | None = None
    elevation_deg_by_sweep: dict[str, float] = {}
    for sample in samples:
        sample_radar_id = _require_text(sample, "radar_id").lower()
        if radar_id is not None and radar_id != sample_radar_id:
            raise RadarQCClutterInputError("a clutter asset must contain a single radar_id")
        radar_id = sample_radar_id
        sweep_name = _require_text(sample, "sweep_name")
        if _require_text(sample, "case_category") != "clear_sky":
            raise RadarQCClutterInputError("static ground clutter requires clear_sky samples")
        try:
            elevation_deg = float(sample["elevation_deg"])
        except (KeyError, TypeError, ValueError) as error:
            raise RadarQCClutterInputError("elevation_deg is required") from error
        if not np.isfinite(elevation_deg) or not -90 <= elevation_deg <= 90:
            raise RadarQCClutterInputError("elevation_deg must be finite within [-90, 90]")
        if (
            sweep_name in elevation_deg_by_sweep
            and elevation_deg_by_sweep[sweep_name] != elevation_deg
        ):
            raise RadarQCClutterInputError("elevation_deg differs within sweep")
        elevation_deg_by_sweep[sweep_name] = elevation_deg
        azimuth_deg = np.asarray(sample.get("azimuth_deg"), dtype="float32")
        range_m = np.asarray(sample.get("range_m"), dtype="float32")
        dbzh = np.asarray(sample.get("dbzh"), dtype="float32")
        if dbzh.shape != (azimuth_deg.size, range_m.size):
            raise RadarQCClutterInputError("dbzh shape must match [azimuth, range]")

        previous_geometry = geometry_by_sweep.get(sweep_name)
        if previous_geometry is None:
            geometry_by_sweep[sweep_name] = (azimuth_deg.copy(), range_m.copy())
            observed_count_by_sweep[sweep_name] = np.zeros(dbzh.shape, dtype="int32")
            hit_count_by_sweep[sweep_name] = np.zeros(dbzh.shape, dtype="int32")
            clear_sky_days_by_sweep[sweep_name] = set()
        else:
            _validate_geometry(sweep_name, previous_geometry, azimuth_deg, range_m)

        observed_at = _timestamp(_require_text(sample, "observed_at_utc"))
        observation_key = (sample_radar_id, sweep_name, observed_at.isoformat())
        if observation_key in seen_observations:
            raise RadarQCClutterInputError("duplicate clutter observation")
        seen_observations.add(observation_key)
        valid = np.isfinite(dbzh)
        observed_count_by_sweep[sweep_name] += valid.astype("int32")
        hit_count_by_sweep[sweep_name] += (valid & (dbzh >= GROUND_CLUTTER_DBZH_THRESHOLD)).astype(
            "int32"
        )
        clear_sky_days_by_sweep[sweep_name].add(_day_key(_require_text(sample, "observed_at_utc")))

    probability_by_sweep: dict[str, np.ndarray] = {}
    support_count_by_sweep: dict[str, np.ndarray] = {}
    clear_sky_day_count_by_sweep: dict[str, int] = {}
    for sweep_name, observed in observed_count_by_sweep.items():
        hits = hit_count_by_sweep[sweep_name]
        clear_sky_day_count = len(clear_sky_days_by_sweep[sweep_name])
        probability = np.full(observed.shape, np.nan, dtype="float32")
        if clear_sky_day_count >= MINIMUM_CLEAR_SKY_DAYS:
            usable = observed >= MINIMUM_GATE_OBSERVATIONS
            probability[usable] = (hits[usable] + 1.0) / (observed[usable] + 2.0)
        probability_by_sweep[sweep_name] = probability
        support_count_by_sweep[sweep_name] = observed.copy()
        clear_sky_day_count_by_sweep[sweep_name] = clear_sky_day_count

    if radar_id is None:
        raise RadarQCClutterInputError("clutter samples must not be empty")
    return StaticGroundClutterAsset(
        radar_id=radar_id,
        elevation_deg_by_sweep=elevation_deg_by_sweep,
        geometry_by_sweep={
            name: {
                "ray_count": int(azimuth.size),
                "gate_count": int(ranges.size),
                "azimuth_float32_sha256": hashlib.sha256(
                    azimuth.astype("<f4").tobytes()
                ).hexdigest(),
                "range_float32_sha256": hashlib.sha256(ranges.astype("<f4").tobytes()).hexdigest(),
            }
            for name, (azimuth, ranges) in geometry_by_sweep.items()
        },
        asset_version=STATIC_GROUND_CLUTTER_ASSET_VERSION,
        dbzh_threshold=GROUND_CLUTTER_DBZH_THRESHOLD,
        minimum_clear_sky_days=MINIMUM_CLEAR_SKY_DAYS,
        minimum_gate_observations=MINIMUM_GATE_OBSERVATIONS,
        probability_by_sweep=probability_by_sweep,
        support_count_by_sweep=support_count_by_sweep,
        clear_sky_day_count_by_sweep=clear_sky_day_count_by_sweep,
    )


def clutter_asset_npz_arrays(asset: StaticGroundClutterAsset) -> dict[str, np.ndarray]:
    return {
        f"{sweep_name}__ground_clutter": values.astype("float32", copy=False)
        for sweep_name, values in sorted(asset.probability_by_sweep.items())
    }


def _validate_geometry(
    sweep_name: str,
    previous: tuple[np.ndarray, np.ndarray],
    azimuth_deg: np.ndarray,
    range_m: np.ndarray,
) -> None:
    previous_azimuth_deg, previous_range_m = previous
    if previous_azimuth_deg.shape != azimuth_deg.shape or not np.allclose(
        previous_azimuth_deg,
        azimuth_deg,
        equal_nan=True,
    ):
        raise RadarQCClutterInputError(f"azimuth_deg differs within {sweep_name}")
    if previous_range_m.shape != range_m.shape or not np.allclose(
        previous_range_m,
        range_m,
        equal_nan=True,
    ):
        raise RadarQCClutterInputError(f"range_m differs within {sweep_name}")


def _day_key(value: str) -> str:
    return _timestamp(value).date().isoformat()


def _timestamp(value: str) -> datetime:
    try:
        parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError as error:
        raise RadarQCClutterInputError(f"invalid observed_at_utc {value}") from error
    if parsed.tzinfo is None:
        raise RadarQCClutterInputError("observed_at_utc must include timezone")
    return parsed.astimezone(UTC)


def _require_text(sample: Mapping[str, object], key: str) -> str:
    value = sample.get(key)
    if not isinstance(value, str) or not value:
        raise RadarQCClutterInputError(f"{key} must be a non-empty string")
    return value
