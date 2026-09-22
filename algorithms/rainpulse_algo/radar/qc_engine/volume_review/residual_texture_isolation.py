"""Audit-only residual object evidence for textured blobs and isolated speckle.

The module operates on native polar gates, preserves every input array, and
returns masks only.  A future CR integration must consume these masks through a
separate, explicitly reviewed disposition step.
"""

from __future__ import annotations

from dataclasses import dataclass

import numpy as np
from scipy import ndimage

from .near_temporal_object import _labels_with_wrap

RESIDUAL_TEXTURE_ISOLATION_POLICY = {
    "version": "residual-texture-isolation-20260922-v1",
    "mode": "audit",
    "minimum_range_m": 2_000.0,
    "maximum_range_m": 75_000.0,
    "minimum_dbz": -10.0,
    "maximum_dbz": 30.0,
    "minimum_snr_db": 8.0,
    "maximum_rhohv": 0.97,
    "texture_radius_m": (500.0, 1_000.0, 2_000.0),
    "texture_gate_radius_m": (500.0, 1_000.0, 2_000.0),
    "minimum_texture_db": 1.0,
    "minimum_texture_score": 0.05,
    "minimum_blob_gates": 8,
    "maximum_blob_gates": 5_000,
    "minimum_blob_edge_fraction": 0.03,
    "minimum_blob_neighborhood_fraction": 0.05,
    "minimum_blob_polar_fraction": 0.10,
    "minimum_blob_nonmet_fraction": 0.30,
    "maximum_isolated_gates": 8,
    "maximum_isolated_area_m2": 10_000_000.0,
    "maximum_isolated_neighborhood_fraction": 0.50,
    "maximum_isolated_echo_fraction": 0.30,
    "minimum_isolated_polar_fraction": 0.10,
    "isolation_ring_radius_m": 3_000.0,
    "maximum_weather_fraction": 0.10,
    "maximum_objects": 50_000,
    "maximum_sweep_gates": 5_000_000,
}


@dataclass(frozen=True)
class ResidualTextureIsolationEvidence:
    arrays: dict
    summary: dict


def _binary(a: dict, key: str, shape: tuple[int, int], *, default: bool = False) -> np.ndarray:
    if key not in a:
        return np.full(shape, default, dtype=bool)
    value = np.asarray(a[key])
    if value.shape != shape or not np.isin(value, (0, 1)).all():
        raise ValueError(f"invalid residual binary field: {key}")
    return value.astype(bool, copy=False)


def _ramp(x: np.ndarray | float, low: float, high: float) -> np.ndarray:
    return np.clip((np.asarray(x, float) - low) / max(high - low, 1e-9), 0.0, 1.0)


def _masked_std(
    values: np.ndarray, valid: np.ndarray, ray_radius: int, gate_radius: int
) -> np.ndarray:
    window = (2 * ray_radius + 1, 2 * gate_radius + 1)
    count = ndimage.uniform_filter(valid.astype(float), window, mode=("wrap", "constant"))
    mean = ndimage.uniform_filter(np.where(valid, values, 0.0), window, mode=("wrap", "constant"))
    mean = np.divide(mean, count, out=np.full(count.shape, np.nan), where=count > 0)
    square = ndimage.uniform_filter(
        np.where(valid, values * values, 0.0), window, mode=("wrap", "constant")
    )
    square = np.divide(square, count, out=np.full(count.shape, np.nan), where=count > 0)
    variance = np.maximum(square - mean * mean, 0.0)
    return np.sqrt(variance).astype("float32")


def _spacing_degrees(azimuth: np.ndarray) -> float:
    ordered = np.sort(np.asarray(azimuth, float) % 360.0)
    delta = np.diff(ordered)
    positive = delta[delta > 0.01]
    return float(np.median(positive)) if positive.size else 1.0


def _edge_fraction(mask: np.ndarray) -> float:
    boundary = mask & ~ndimage.binary_erosion(mask, structure=np.ones((3, 3), dtype=bool))
    return float(boundary.sum() / max(1, mask.sum()))


def evaluate_residual_texture_isolation(current: dict) -> ResidualTextureIsolationEvidence:
    """Evaluate one native sweep without changing measurements or qualification."""
    shape = np.asarray(current["DBZH_RAW"]).shape
    if int(np.prod(shape)) > RESIDUAL_TEXTURE_ISOLATION_POLICY["maximum_sweep_gates"]:
        raise ValueError("residual texture sweep gate budget exceeded")

    z = np.asarray(current["DBZH_RAW"], float)
    rho = np.asarray(current.get("RHOHV_RAW", np.full(shape, np.nan)), float)
    snr = np.asarray(current.get("SNR_RAW", np.full(shape, np.nan)), float)
    ranges = np.asarray(current["range"], float)
    azimuth = np.asarray(current["azimuth"], float)
    valid = _binary(current, "VALID_MASK", shape)
    eligible = _binary(current, "REFLECTIVITY_ELIGIBLE_FOR_CR", shape)
    range_mask = (ranges[None, :] >= RESIDUAL_TEXTURE_ISOLATION_POLICY["minimum_range_m"]) & (
        ranges[None, :] <= RESIDUAL_TEXTURE_ISOLATION_POLICY["maximum_range_m"]
    )
    observed = valid & np.isfinite(z)
    polar_available = np.isfinite(rho) & np.isfinite(snr)
    polar_score = _ramp(RESIDUAL_TEXTURE_ISOLATION_POLICY["maximum_rhohv"] - rho, 0.0, 0.12)
    if "CF_POLAR_SCORE" in current:
        optional = np.asarray(current["CF_POLAR_SCORE"], float)
        polar_score = np.where(np.isfinite(optional), optional, polar_score)
    weather = (
        _binary(current, "CF_HARD_WEATHER_MASK", shape)
        | _binary(current, "CF_LOCAL_WEATHER_MASK", shape)
        | _binary(current, "CF_LEGACY_PROTECTED_MASK", shape)
        | _binary(current, "CF_WEATHER_PROXY_MASK", shape)
    )
    weather |= polar_available & (rho >= 0.97) & (snr >= 12.0)
    domain = (
        observed
        & eligible
        & range_mask
        & np.isfinite(rho)
        & np.isfinite(snr)
        & (z >= RESIDUAL_TEXTURE_ISOLATION_POLICY["minimum_dbz"])
        & (z <= RESIDUAL_TEXTURE_ISOLATION_POLICY["maximum_dbz"])
        & (rho <= RESIDUAL_TEXTURE_ISOLATION_POLICY["maximum_rhohv"])
        & (snr >= RESIDUAL_TEXTURE_ISOLATION_POLICY["minimum_snr_db"])
        & ~weather
    )

    dr = float(np.median(np.diff(ranges))) if ranges.size > 1 else 1.0
    ray_spacing = _spacing_degrees(azimuth)
    texture_values = []
    for radius_m in RESIDUAL_TEXTURE_ISOLATION_POLICY["texture_radius_m"]:
        gate_radius = max(1, int(round(radius_m / max(dr, 1e-6))))
        ray_radius = max(
            1, int(round(radius_m / max(float(np.median(ranges)) * np.deg2rad(ray_spacing), 1e-6)))
        )
        texture_values.append(_masked_std(z, domain, min(ray_radius, 32), min(gate_radius, 32)))
    stacked = np.stack(texture_values)
    texture = np.full(shape, np.nan, dtype="float32")
    finite = np.any(np.isfinite(stacked), axis=0)
    np.max(stacked, axis=0, out=texture, where=finite, initial=-np.inf)
    texture[~finite] = np.nan
    texture_score = _ramp(
        texture,
        RESIDUAL_TEXTURE_ISOLATION_POLICY["minimum_texture_db"],
        RESIDUAL_TEXTURE_ISOLATION_POLICY["minimum_texture_db"] + 2.5,
    )

    labels, _ = _labels_with_wrap(domain, azimuth)
    present = np.unique(labels)
    present = present[present > 0]
    if len(present) > RESIDUAL_TEXTURE_ISOLATION_POLICY["maximum_objects"]:
        raise ValueError("residual object budget exceeded")

    object_mask = np.zeros(shape, dtype=bool)
    blob_mask = np.zeros(shape, dtype=bool)
    isolated_mask = np.zeros(shape, dtype=bool)
    object_id = np.zeros(shape, dtype=np.int32)
    object_kind = np.zeros(shape, dtype=np.uint8)
    object_size = np.zeros(shape, dtype=np.uint32)
    object_area = np.zeros(shape, dtype=np.float32)
    edge_score = np.full(shape, np.nan, dtype="float32")
    isolation_score = np.full(shape, np.nan, dtype="float32")
    polar_score_array = np.where(polar_available, polar_score, np.nan).astype("float32")
    ring_radius_m = RESIDUAL_TEXTURE_ISOLATION_POLICY["isolation_ring_radius_m"]
    ring_ray_radius = max(
        1,
        min(
            32,
            int(
                round(ring_radius_m / max(float(np.median(ranges)) * np.deg2rad(ray_spacing), 1e-6))
            ),
        ),
    )
    ring_gate_radius = max(1, min(32, int(round(ring_radius_m / max(dr, 1e-6)))))
    density_window = (2 * ring_ray_radius + 1, 2 * ring_gate_radius + 1)
    domain_density = ndimage.uniform_filter(
        domain.astype(float), density_window, mode=("wrap", "constant")
    )
    echo_mask = observed & (z >= RESIDUAL_TEXTURE_ISOLATION_POLICY["minimum_dbz"])
    echo_density = ndimage.uniform_filter(
        echo_mask.astype(float), density_window, mode=("wrap", "constant")
    )
    slices = ndimage.find_objects(labels)
    for label_id in present.tolist():
        base_slice = slices[label_id - 1] if label_id - 1 < len(slices) else None
        if base_slice is None:
            continue
        base_component = labels[base_slice] == label_id
        base_columns = np.nonzero(base_component)[1]
        base_global_columns = base_slice[1].start + base_columns
        size = int(base_component.sum())
        mean_range = float(np.median(ranges[base_global_columns]))
        range_step = max(dr, 1e-6)
        angular_step = max(mean_range * np.deg2rad(ray_spacing), 1e-6)
        area = size * range_step * angular_step
        ring_ray_radius = max(1, min(32, int(round(ring_radius_m / angular_step))))
        ring_gate_radius = max(1, min(32, int(round(ring_radius_m / range_step))))
        slice_ = (
            slice(
                max(0, base_slice[0].start - ring_ray_radius),
                min(labels.shape[0], base_slice[0].stop + ring_ray_radius),
            ),
            slice(
                max(0, base_slice[1].start - ring_gate_radius),
                min(labels.shape[1], base_slice[1].stop + ring_gate_radius),
            ),
        )
        component = labels[slice_] == label_id
        local_domain = domain_density[slice_]
        local_echo = echo_density[slice_]
        neighborhood_fraction = float(local_domain[component].mean())
        echo_fraction = float(local_echo[component].mean())
        context_fraction = max(neighborhood_fraction, echo_fraction)
        edge_fraction = _edge_fraction(component)
        polar_values = polar_score_array[slice_][component]
        polar_fraction = (
            float(np.mean(polar_values >= 0.5)) if np.isfinite(polar_values).any() else 0.0
        )
        nonmet_fraction = (
            float(np.asarray(current["CF_NEIGHBOUR_FRACTION"], float)[slice_][component].mean())
            if "CF_NEIGHBOUR_FRACTION" in current
            else polar_fraction
        )
        weather_fraction = float(weather[slice_][component].mean())
        texture_values = texture[slice_][component]
        texture_p90 = (
            float(np.nanpercentile(texture_values, 90))
            if np.isfinite(texture_values).any()
            else 0.0
        )
        edge_score[slice_][component] = edge_fraction
        isolation = (1.0 - neighborhood_fraction) * (1.0 - echo_fraction)
        isolation_score[slice_][component] = isolation
        object_id[slice_][component] = label_id
        object_size[slice_][component] = size
        object_area[slice_][component] = area

        protected = weather_fraction > RESIDUAL_TEXTURE_ISOLATION_POLICY[
            "maximum_weather_fraction"
        ] or (
            weather[slice_][component].any()
            and size <= RESIDUAL_TEXTURE_ISOLATION_POLICY["maximum_isolated_gates"]
        )
        score_values = texture_score[slice_][component]
        blob = (
            not protected
            and RESIDUAL_TEXTURE_ISOLATION_POLICY["minimum_blob_gates"]
            <= size
            <= RESIDUAL_TEXTURE_ISOLATION_POLICY["maximum_blob_gates"]
            and area >= 2_000_000.0
            and texture_p90 >= RESIDUAL_TEXTURE_ISOLATION_POLICY["minimum_texture_db"]
            and max(
                np.nanmax(score_values) if np.isfinite(score_values).any() else 0.0, edge_fraction
            )
            >= RESIDUAL_TEXTURE_ISOLATION_POLICY["minimum_texture_score"]
            and edge_fraction >= RESIDUAL_TEXTURE_ISOLATION_POLICY["minimum_blob_edge_fraction"]
            and context_fraction
            >= RESIDUAL_TEXTURE_ISOLATION_POLICY["minimum_blob_neighborhood_fraction"]
            and polar_fraction >= RESIDUAL_TEXTURE_ISOLATION_POLICY["minimum_blob_polar_fraction"]
            and nonmet_fraction >= RESIDUAL_TEXTURE_ISOLATION_POLICY["minimum_blob_nonmet_fraction"]
        )
        isolated = (
            not protected
            and size <= RESIDUAL_TEXTURE_ISOLATION_POLICY["maximum_isolated_gates"]
            and area <= RESIDUAL_TEXTURE_ISOLATION_POLICY["maximum_isolated_area_m2"]
            and neighborhood_fraction
            <= RESIDUAL_TEXTURE_ISOLATION_POLICY["maximum_isolated_neighborhood_fraction"]
            and echo_fraction <= RESIDUAL_TEXTURE_ISOLATION_POLICY["maximum_isolated_echo_fraction"]
            and polar_fraction
            >= RESIDUAL_TEXTURE_ISOLATION_POLICY["minimum_isolated_polar_fraction"]
        )
        if blob:
            object_kind[slice_][component] = 1
            blob_mask[slice_][component] = True
        elif isolated:
            object_kind[slice_][component] = 2
            isolated_mask[slice_][component] = True
        if (object_kind[slice_][component] > 0).all():
            object_mask[slice_][component] = True

    arrays = {
        "RTI_CANDIDATE_MASK": domain.astype("uint8"),
        "RTI_LOCAL_TEXTURE_DB": texture.astype("float32"),
        "RTI_TEXTURE_SCORE": texture_score.astype("float32"),
        "RTI_EDGE_SCORE": edge_score,
        "RTI_ISOLATION_SCORE": isolation_score,
        "RTI_POLAR_SCORE": polar_score_array,
        "RTI_OBJECT_MASK": object_mask.astype("uint8"),
        "RTI_BLOB_MASK": blob_mask.astype("uint8"),
        "RTI_ISOLATED_MASK": isolated_mask.astype("uint8"),
        "RTI_OBJECT_ID": object_id,
        "RTI_OBJECT_KIND": object_kind,
        "RTI_OBJECT_SIZE": object_size,
        "RTI_OBJECT_AREA_M2": object_area,
    }
    summary = {
        "status": "EVALUATED",
        "mode": RESIDUAL_TEXTURE_ISOLATION_POLICY["mode"],
        "policy": RESIDUAL_TEXTURE_ISOLATION_POLICY,
        "candidate_gates": int(domain.sum()),
        "object_count": int(len(present)),
        "blob_gates": int(blob_mask.sum()),
        "isolated_gates": int(isolated_mask.sum()),
        "action_gates": 0,
        "changes_measurement_qualification": False,
    }
    return ResidualTextureIsolationEvidence(arrays, summary)
