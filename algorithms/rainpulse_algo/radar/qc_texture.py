from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass

import numpy as np

TEXTURE_RANGE_WINDOW_METERS = 1_750.0
TEXTURE_AZIMUTH_HALF_WINDOW_DEG = 1.5
TEXTURE_MIN_SUPPORT_FRACTION = 0.70
TEXTURE_MODULE_NAME = "polar_texture"
TEXTURE_INPUT_FIELDS = ("DBZH", "RHOHV", "ZDR", "PHIDP", "azimuth", "range")

TEXTURE_OUTPUT_DTYPES = {
    "DBZH_TEXTURE": np.dtype("float32"),
    "DBZH_TEXTURE_SUPPORT_RATE": np.dtype("float32"),
    "DBZH_TEXTURE_AVAILABLE_MASK": np.dtype("uint8"),
    "RHOHV_TEXTURE": np.dtype("float32"),
    "RHOHV_TEXTURE_SUPPORT_RATE": np.dtype("float32"),
    "RHOHV_TEXTURE_AVAILABLE_MASK": np.dtype("uint8"),
    "ZDR_TEXTURE": np.dtype("float32"),
    "ZDR_TEXTURE_SUPPORT_RATE": np.dtype("float32"),
    "ZDR_TEXTURE_AVAILABLE_MASK": np.dtype("uint8"),
    "PHIDP_CIRCULAR_VARIANCE": np.dtype("float32"),
    "PHIDP_CIRCULAR_VARIANCE_SUPPORT_RATE": np.dtype("float32"),
    "PHIDP_CIRCULAR_VARIANCE_AVAILABLE_MASK": np.dtype("uint8"),
}
TEXTURE_AVAILABLE_MASK_FIELDS = {
    name for name in TEXTURE_OUTPUT_DTYPES if name.endswith("_AVAILABLE_MASK")
}
TEXTURE_SUPPORT_RATE_FIELDS = {
    name for name in TEXTURE_OUTPUT_DTYPES if name.endswith("_SUPPORT_RATE")
}
TEXTURE_PRODUCED_VARIABLES = tuple(TEXTURE_OUTPUT_DTYPES)

_SCALAR_FIELD_OUTPUTS = {
    "DBZH": (
        "DBZH_TEXTURE",
        "DBZH_TEXTURE_SUPPORT_RATE",
        "DBZH_TEXTURE_AVAILABLE_MASK",
    ),
    "RHOHV": (
        "RHOHV_TEXTURE",
        "RHOHV_TEXTURE_SUPPORT_RATE",
        "RHOHV_TEXTURE_AVAILABLE_MASK",
    ),
    "ZDR": (
        "ZDR_TEXTURE",
        "ZDR_TEXTURE_SUPPORT_RATE",
        "ZDR_TEXTURE_AVAILABLE_MASK",
    ),
}
_CIRCULAR_FIELD_OUTPUTS = {
    "PHIDP": (
        "PHIDP_CIRCULAR_VARIANCE",
        "PHIDP_CIRCULAR_VARIANCE_SUPPORT_RATE",
        "PHIDP_CIRCULAR_VARIANCE_AVAILABLE_MASK",
    )
}


@dataclass(frozen=True)
class TextureDiagnostics:
    fields: dict[str, np.ndarray]
    metrics: dict[str, float]


def build_texture_diagnostics(
    azimuth_deg: np.ndarray,
    range_m: np.ndarray,
    fields: Mapping[str, np.ndarray | None],
) -> TextureDiagnostics:
    azimuth = np.asarray(azimuth_deg, dtype="float64")
    ranges = np.asarray(range_m, dtype="float64")
    if azimuth.ndim != 1:
        raise ValueError("texture azimuth coordinates must be one-dimensional")
    if ranges.ndim != 1:
        raise ValueError("texture range coordinates must be one-dimensional")
    if ranges.size == 0:
        raise ValueError("texture range coordinates must not be empty")
    if np.any(~np.isfinite(ranges)):
        raise ValueError("texture range coordinates must be finite")
    if np.any(np.diff(ranges) < 0):
        raise ValueError("texture range coordinates must be monotonic")

    range_indices, range_slot_mask, gate_neighbor_count = _range_window_indices(ranges)
    ray_neighbours = _ray_window_indices(azimuth)
    outputs: dict[str, np.ndarray] = {}
    metrics: dict[str, float] = {}
    expected_shape = (azimuth.size, ranges.size)

    for source_name, names in _SCALAR_FIELD_OUTPUTS.items():
        values = fields.get(source_name)
        if values is None:
            continue
        texture, support_rate, available_mask = _scalar_texture(
            np.asarray(values, dtype="float32"),
            expected_shape=expected_shape,
            ray_neighbours=ray_neighbours,
            range_indices=range_indices,
            range_slot_mask=range_slot_mask,
            gate_neighbor_count=gate_neighbor_count,
        )
        outputs[names[0]] = texture
        outputs[names[1]] = support_rate
        outputs[names[2]] = available_mask
        metrics[f"{names[0].lower()}_available_gate_count"] = float(
            np.count_nonzero(available_mask)
        )

    for source_name, names in _CIRCULAR_FIELD_OUTPUTS.items():
        values = fields.get(source_name)
        if values is None:
            continue
        variance, support_rate, available_mask = _circular_variance(
            np.asarray(values, dtype="float32"),
            expected_shape=expected_shape,
            ray_neighbours=ray_neighbours,
            range_indices=range_indices,
            range_slot_mask=range_slot_mask,
            gate_neighbor_count=gate_neighbor_count,
        )
        outputs[names[0]] = variance
        outputs[names[1]] = support_rate
        outputs[names[2]] = available_mask
        metrics[f"{names[0].lower()}_available_gate_count"] = float(
            np.count_nonzero(available_mask)
        )

    return TextureDiagnostics(fields=outputs, metrics=metrics)


def _scalar_texture(
    values: np.ndarray,
    *,
    expected_shape: tuple[int, int],
    ray_neighbours: list[list[int]],
    range_indices: np.ndarray,
    range_slot_mask: np.ndarray,
    gate_neighbor_count: np.ndarray,
) -> tuple[np.ndarray, np.ndarray, np.ndarray]:
    if values.shape != expected_shape:
        raise ValueError("texture source field shape differs from azimuth/range geometry")
    output = np.full(values.shape, np.nan, dtype="float32")
    support_rate = np.zeros(values.shape, dtype="float32")
    available_mask = np.zeros(values.shape, dtype="uint8")
    for ray_index, neighbours in enumerate(ray_neighbours):
        if not neighbours:
            continue
        support, flat, valid, supported = _window_support(
            values,
            neighbours=neighbours,
            range_indices=range_indices,
            range_slot_mask=range_slot_mask,
            gate_neighbor_count=gate_neighbor_count,
        )
        support_rate[ray_index] = support
        if not np.any(supported):
            continue
        supported_windows = np.where(valid[supported], flat[supported], np.nan)
        median = np.nanmedian(supported_windows, axis=1)
        mad = 1.4826 * np.nanmedian(
            np.abs(supported_windows - median[:, None]),
            axis=1,
        )
        output[ray_index, supported] = mad.astype("float32", copy=False)
        available_mask[ray_index, supported] = 1
    return output, support_rate, available_mask


def _circular_variance(
    values: np.ndarray,
    *,
    expected_shape: tuple[int, int],
    ray_neighbours: list[list[int]],
    range_indices: np.ndarray,
    range_slot_mask: np.ndarray,
    gate_neighbor_count: np.ndarray,
) -> tuple[np.ndarray, np.ndarray, np.ndarray]:
    if values.shape != expected_shape:
        raise ValueError("texture source field shape differs from azimuth/range geometry")
    output = np.full(values.shape, np.nan, dtype="float32")
    support_rate = np.zeros(values.shape, dtype="float32")
    available_mask = np.zeros(values.shape, dtype="uint8")
    for ray_index, neighbours in enumerate(ray_neighbours):
        if not neighbours:
            continue
        support, flat, valid, supported = _window_support(
            values,
            neighbours=neighbours,
            range_indices=range_indices,
            range_slot_mask=range_slot_mask,
            gate_neighbor_count=gate_neighbor_count,
        )
        support_rate[ray_index] = support
        if not np.any(supported):
            continue
        supported_windows = flat[supported]
        supported_valid = valid[supported]
        radians = np.deg2rad(np.where(supported_valid, supported_windows, 0.0))
        complex_mean = np.sum(
            np.where(supported_valid, np.exp(1j * radians), 0.0 + 0.0j),
            axis=1,
        ) / np.count_nonzero(supported_valid, axis=1)
        variance = 1.0 - np.abs(complex_mean)
        output[ray_index, supported] = np.clip(
            variance.real,
            0.0,
            1.0,
        ).astype("float32", copy=False)
        available_mask[ray_index, supported] = 1
    return output, support_rate, available_mask


def _window_support(
    values: np.ndarray,
    *,
    neighbours: list[int],
    range_indices: np.ndarray,
    range_slot_mask: np.ndarray,
    gate_neighbor_count: np.ndarray,
) -> tuple[np.ndarray, np.ndarray, np.ndarray, np.ndarray]:
    ray_window = values[np.asarray(neighbours, dtype="int64")]
    gathered = np.take(ray_window, range_indices, axis=1)
    flat = np.transpose(gathered, (1, 0, 2)).reshape(values.shape[1], -1)
    slot_mask = np.tile(range_slot_mask, (1, len(neighbours)))
    valid = slot_mask & np.isfinite(flat)
    support_denominator = len(neighbours) * gate_neighbor_count.astype("float32", copy=False)
    support_count = np.count_nonzero(valid, axis=1).astype("float32", copy=False)
    support = np.zeros(values.shape[1], dtype="float32")
    nonzero = support_denominator > 0
    support[nonzero] = support_count[nonzero] / support_denominator[nonzero]
    supported = nonzero & (support >= TEXTURE_MIN_SUPPORT_FRACTION)
    return support, flat, valid, supported


def _range_window_indices(range_m: np.ndarray) -> tuple[np.ndarray, np.ndarray, np.ndarray]:
    half_window = 0.5 * TEXTURE_RANGE_WINDOW_METERS
    left = np.searchsorted(range_m, range_m - half_window, side="left")
    right = np.searchsorted(range_m, range_m + half_window, side="right")
    counts = right - left
    max_count = int(np.max(counts))
    indices = np.zeros((range_m.size, max_count), dtype="int64")
    slot_mask = np.zeros((range_m.size, max_count), dtype=bool)
    for gate_index, (start, stop) in enumerate(zip(left, right, strict=True)):
        indices[gate_index, : stop - start] = np.arange(start, stop, dtype="int64")
        slot_mask[gate_index, : stop - start] = True
        if stop - start < max_count:
            indices[gate_index, stop - start :] = gate_index
    return indices, slot_mask, counts.astype("int64", copy=False)


def _ray_window_indices(azimuth_deg: np.ndarray) -> list[list[int]]:
    ray_count = azimuth_deg.size
    if ray_count == 0:
        return []
    finite = np.isfinite(azimuth_deg)
    if ray_count == 1:
        return [[0]] if finite[0] else [[]]
    step = np.full(ray_count, np.inf, dtype="float64")
    for ray_index in range(ray_count):
        next_index = (ray_index + 1) % ray_count
        if finite[ray_index] and finite[next_index]:
            step[ray_index] = _circular_distance_deg(
                azimuth_deg[ray_index],
                azimuth_deg[next_index],
            )
    neighbours: list[list[int]] = []
    for ray_index in range(ray_count):
        if not finite[ray_index]:
            neighbours.append([])
            continue
        current = [ray_index]
        distance = 0.0
        left_index = ray_index
        for _ in range(ray_count - 1):
            previous = (left_index - 1) % ray_count
            step_distance = step[previous]
            if (
                previous in current
                or not np.isfinite(step_distance)
                or distance + step_distance > TEXTURE_AZIMUTH_HALF_WINDOW_DEG
            ):
                break
            distance += float(step_distance)
            current.insert(0, previous)
            left_index = previous
        distance = 0.0
        right_index = ray_index
        for _ in range(ray_count - 1):
            step_distance = step[right_index]
            if (
                not np.isfinite(step_distance)
                or distance + step_distance > TEXTURE_AZIMUTH_HALF_WINDOW_DEG
            ):
                break
            distance += float(step_distance)
            right_index = (right_index + 1) % ray_count
            if right_index in current:
                break
            current.append(right_index)
        neighbours.append(current)
    return neighbours


def _circular_distance_deg(left: float, right: float) -> float:
    return float(abs((right - left + 180.0) % 360.0 - 180.0))
