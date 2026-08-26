from __future__ import annotations

import re
from collections.abc import Mapping
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from enum import IntEnum
from pathlib import Path

import numpy as np
import rasterio
from rasterio.windows import from_bounds

from rainpulse_algo.grid import RegularLatLonGrid

MRMS_PRECIP_FILENAME = re.compile(
    r"^(?:MRMS_)?PrecipRate_00\.00_(?P<valid_time>\d{8}-\d{6})\.grib2\.gz$"
)


class MRMSPrecipError(ValueError):
    """Raised when an MRMS precipitation-rate frame violates the validation contract."""


class MRMSSourceState(IntEnum):
    """Stable state codes retaining the MRMS missing-data distinctions."""

    NO_COVERAGE = -3
    MISSING = -1
    NO_RAIN = 0
    RAIN = 1


@dataclass(frozen=True)
class MRMSPrecipFrame:
    valid_time: datetime
    rate_mm_h: np.ndarray
    valid_mask: np.ndarray
    source_state: np.ndarray
    source_path: str


@dataclass(frozen=True)
class MRMSValidationSequence:
    issue_time: datetime
    frame_times: tuple[datetime, ...]
    source_basis_times: tuple[tuple[datetime, ...], ...]
    rate_mm_h: np.ndarray
    reflectivity_dbz: np.ndarray
    quality_index: np.ndarray
    valid_mask: np.ndarray
    low_quality_mask: np.ndarray
    data_age_minutes: np.ndarray
    source_state: np.ndarray
    interpolated_frames: np.ndarray
    operational_eligible: bool = False
    reflectivity_provenance: str = "surrogate_from_mrms_rate_z200_r1p6"


def _valid_time_from_path(path: Path) -> datetime:
    match = MRMS_PRECIP_FILENAME.match(path.name)
    if match is None:
        raise MRMSPrecipError(f"unexpected MRMS precipitation filename: {path.name}")
    return datetime.strptime(match.group("valid_time"), "%Y%m%d-%H%M%S").replace(tzinfo=UTC)


def _validate_product(tags: dict[str, str], valid_time: datetime) -> None:
    if tags.get("GRIB_DISCIPLINE") != "209":
        raise MRMSPrecipError("GRIB discipline is not MRMS local discipline 209")
    if tags.get("GRIB_ELEMENT") != "PrecipRate" or tags.get("GRIB_UNIT") != "[mm/hr]":
        raise MRMSPrecipError("GRIB band is not MRMS PrecipRate in mm/hr")
    try:
        tagged_valid_time = datetime.fromtimestamp(int(tags["GRIB_VALID_TIME"]), tz=UTC)
    except (KeyError, TypeError, ValueError) as exc:
        raise MRMSPrecipError("GRIB band has no valid MRMS valid time") from exc
    if tagged_valid_time != valid_time:
        raise MRMSPrecipError("GRIB valid time differs from the filename")


def _crop_window(dataset, grid: RegularLatLonGrid):
    transform = dataset.transform
    if (
        transform.a <= 0
        or transform.e >= 0
        or not np.isclose(transform.a, grid.longitude_interval_deg, atol=1e-6)
        or not np.isclose(-transform.e, grid.latitude_interval_deg, atol=1e-6)
        or not np.isclose(transform.b, 0.0, atol=1e-12)
        or not np.isclose(transform.d, 0.0, atol=1e-12)
    ):
        raise MRMSPrecipError("MRMS raster spacing or scan direction is incompatible with grid")

    source_left = transform.c
    source_top = transform.f
    source_right = source_left + transform.a * dataset.width
    source_bottom = source_top + transform.e * dataset.height
    west, south, east, north = grid.pixel_edge_bounds
    tolerance = 1e-5
    if (
        west < source_left - tolerance
        or east > source_right + tolerance
        or south < source_bottom - tolerance
        or north > source_top + tolerance
    ):
        raise MRMSPrecipError("requested validation grid is outside the MRMS CONUS raster")

    window = from_bounds(west, south, east, north, transform=transform)
    window = window.round_offsets().round_lengths()
    if (int(window.height), int(window.width)) != grid.shape:
        raise MRMSPrecipError("MRMS crop shape differs from the immutable validation grid")
    return window


def read_mrms_precip_frame(path: Path, grid: RegularLatLonGrid) -> MRMSPrecipFrame:
    """Read one gzip-compressed MRMS frame directly into an ascending-latitude ROI."""

    valid_time = _valid_time_from_path(path)
    source = f"/vsigzip/{path.resolve()}"
    try:
        with rasterio.open(source) as dataset:
            if dataset.driver != "GRIB" or dataset.count != 1:
                raise MRMSPrecipError("MRMS asset must contain one GRIB raster band")
            _validate_product(dataset.tags(1), valid_time)
            values = dataset.read(1, window=_crop_window(dataset, grid))
    except MRMSPrecipError:
        raise
    except Exception as exc:  # noqa: BLE001 - normalize raster driver failures at the seam
        raise MRMSPrecipError(f"cannot read MRMS asset {path}: {exc}") from exc

    values = np.flipud(np.asarray(values, dtype="float32"))
    if values.shape != grid.shape:
        raise MRMSPrecipError("decoded MRMS crop shape differs from the validation grid")
    no_coverage = np.isclose(values, -3.0, atol=1e-6)
    missing = np.isclose(values, -1.0, atol=1e-6)
    valid = np.isfinite(values) & (values >= 0.0)
    if np.any(~(no_coverage | missing | valid)):
        raise MRMSPrecipError("MRMS frame contains an undocumented precipitation-rate state")

    rate = np.where(valid, values, np.nan).astype("float32")
    state = np.full(grid.shape, MRMSSourceState.NO_RAIN, dtype="int8")
    state[values > 0.0] = MRMSSourceState.RAIN
    state[missing] = MRMSSourceState.MISSING
    state[no_coverage] = MRMSSourceState.NO_COVERAGE
    return MRMSPrecipFrame(
        valid_time=valid_time,
        rate_mm_h=rate,
        valid_mask=valid.astype("uint8"),
        source_state=state,
        source_path=str(path),
    )


def rate_to_surrogate_reflectivity(rate: np.ndarray, valid: np.ndarray) -> np.ndarray:
    """Create the frozen Z=200R^1.6 motion proxy used only by MRMS validation."""

    reflectivity = np.full(rate.shape, np.nan, dtype="float32")
    dry = valid & (rate == 0.0)
    rain = valid & (rate > 0.0)
    reflectivity[dry] = 0.0
    reflectivity[rain] = (
        10.0 * np.log10(200.0 * np.power(rate[rain].astype("float64"), 1.6))
    ).astype("float32")
    return reflectivity


def _validate_observed_frames(
    frames: Mapping[datetime, MRMSPrecipFrame],
    issue_time: datetime,
    grid: RegularLatLonGrid,
) -> tuple[tuple[datetime, ...], tuple[MRMSPrecipFrame, ...]]:
    if issue_time.tzinfo is None or issue_time.utcoffset() != timedelta(0):
        raise MRMSPrecipError("MRMS validation issue time must be timezone-aware UTC")
    if issue_time.second or issue_time.microsecond or issue_time.minute % 10:
        raise MRMSPrecipError("MRMS validation issue time must align to a 10-minute source slot")

    observed_times = tuple(issue_time - timedelta(minutes=value) for value in (20, 10, 0))
    try:
        observed = tuple(frames[value] for value in observed_times)
    except KeyError as exc:
        missing = exc.args[0]
        raise MRMSPrecipError(f"missing required causal MRMS frame {missing.isoformat()}") from exc
    for expected_time, frame in zip(observed_times, observed, strict=True):
        if frame.valid_time != expected_time:
            raise MRMSPrecipError("MRMS frame key and embedded valid time differ")
        if (
            frame.rate_mm_h.shape != grid.shape
            or frame.valid_mask.shape != grid.shape
            or frame.source_state.shape != grid.shape
        ):
            raise MRMSPrecipError("MRMS frame shape differs from the validation grid")
    return observed_times, observed


def build_mrms_observed_sequence(
    frames: Mapping[datetime, MRMSPrecipFrame],
    issue_time: datetime,
    grid: RegularLatLonGrid,
) -> MRMSValidationSequence:
    """Build the native three-frame, ten-minute sequence without temporal interpolation."""

    observed_times, observed = _validate_observed_frames(frames, issue_time, grid)
    rate_sequence = np.stack([frame.rate_mm_h for frame in observed]).astype("float32")
    valid_sequence = np.stack([frame.valid_mask == 1 for frame in observed])
    state_sequence = np.stack([frame.source_state for frame in observed]).astype("int8")
    quality = np.where(valid_sequence, 1.0, np.nan).astype("float32")
    age = np.zeros(rate_sequence.shape, dtype="float32")
    age[~valid_sequence] = np.nan
    return MRMSValidationSequence(
        issue_time=issue_time,
        frame_times=observed_times,
        source_basis_times=tuple((value,) for value in observed_times),
        rate_mm_h=rate_sequence,
        reflectivity_dbz=rate_to_surrogate_reflectivity(rate_sequence, valid_sequence),
        quality_index=quality,
        valid_mask=valid_sequence.astype("uint8"),
        low_quality_mask=np.zeros(rate_sequence.shape, dtype="uint8"),
        data_age_minutes=age,
        source_state=state_sequence,
        interpolated_frames=np.zeros(len(observed_times), dtype="uint8"),
    )


def build_mrms_validation_sequence(
    frames: Mapping[datetime, MRMSPrecipFrame],
    issue_time: datetime,
    grid: RegularLatLonGrid,
) -> MRMSValidationSequence:
    """Build a causal five-frame, five-minute sequence from three 10-minute MRMS frames."""

    _, observed = _validate_observed_frames(frames, issue_time, grid)
    rates: list[np.ndarray] = []
    valid_masks: list[np.ndarray] = []
    states: list[np.ndarray] = []
    basis_times: list[tuple[datetime, ...]] = []
    interpolated: list[int] = []
    for index, frame in enumerate(observed):
        rates.append(frame.rate_mm_h.astype("float32", copy=True))
        valid_masks.append(frame.valid_mask.astype(bool, copy=True))
        states.append(frame.source_state.astype("int8", copy=True))
        basis_times.append((frame.valid_time,))
        interpolated.append(0)
        if index == len(observed) - 1:
            continue

        following = observed[index + 1]
        common_valid = (frame.valid_mask == 1) & (following.valid_mask == 1)
        midpoint_rate = np.full(grid.shape, np.nan, dtype="float32")
        midpoint_rate[common_valid] = (
            (frame.rate_mm_h[common_valid] + following.rate_mm_h[common_valid]) / 2.0
        ).astype("float32")
        midpoint_state = np.full(grid.shape, MRMSSourceState.MISSING, dtype="int8")
        no_coverage = (frame.source_state == MRMSSourceState.NO_COVERAGE) | (
            following.source_state == MRMSSourceState.NO_COVERAGE
        )
        midpoint_state[no_coverage] = MRMSSourceState.NO_COVERAGE
        midpoint_state[common_valid & (midpoint_rate == 0.0)] = MRMSSourceState.NO_RAIN
        midpoint_state[common_valid & (midpoint_rate > 0.0)] = MRMSSourceState.RAIN
        rates.append(midpoint_rate)
        valid_masks.append(common_valid)
        states.append(midpoint_state)
        basis_times.append((frame.valid_time, following.valid_time))
        interpolated.append(1)

    order = (0, 1, 2, 3, 4)
    rate_sequence = np.stack([rates[index] for index in order]).astype("float32")
    valid_sequence = np.stack([valid_masks[index] for index in order])
    state_sequence = np.stack([states[index] for index in order]).astype("int8")
    interpolated_frames = np.asarray([interpolated[index] for index in order], dtype="uint8")
    frame_times = tuple(issue_time - timedelta(minutes=value) for value in (20, 15, 10, 5, 0))
    quality = np.where(valid_sequence, 1.0, np.nan).astype("float32")
    age = np.broadcast_to(
        (interpolated_frames * 5.0)[:, np.newaxis, np.newaxis],
        rate_sequence.shape,
    ).astype("float32", copy=True)
    age[~valid_sequence] = np.nan
    return MRMSValidationSequence(
        issue_time=issue_time,
        frame_times=frame_times,
        source_basis_times=tuple(basis_times[index] for index in order),
        rate_mm_h=rate_sequence,
        reflectivity_dbz=rate_to_surrogate_reflectivity(rate_sequence, valid_sequence),
        quality_index=quality,
        valid_mask=valid_sequence.astype("uint8"),
        low_quality_mask=np.zeros(rate_sequence.shape, dtype="uint8"),
        data_age_minutes=age,
        source_state=state_sequence,
        interpolated_frames=interpolated_frames,
    )
