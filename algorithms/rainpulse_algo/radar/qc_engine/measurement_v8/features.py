"""Continuous, physically scaled features; NaN means uncomputed, not a negative vote."""

import hashlib
from dataclasses import dataclass
from pathlib import Path

import numpy as np
from scipy import ndimage

from . import FEATURE_SCHEMA
from .io import digest


@dataclass
class Features:
    matrix: np.ndarray
    names: tuple[str, ...]
    shape: tuple[int, int]
    metadata: dict


def window_moments(values, observed, gates, minimum_fraction):
    kernel = np.ones(gates, dtype="float64")
    count = ndimage.convolve1d(observed.astype(float), kernel, axis=1, mode="constant")
    # Multiplication is performed on a private statistical accumulator only.
    # No missing observation is exposed as zero to a detector or a rain field.
    work = np.where(observed, values, 0.0).astype("float64")
    total = ndimage.convolve1d(work, kernel, axis=1, mode="constant")
    total2 = ndimage.convolve1d(work * work, kernel, axis=1, mode="constant")
    supported = observed & (count >= np.ceil(gates * minimum_fraction))
    mean = np.divide(total, count, out=np.full(values.shape, np.nan), where=supported)
    second = np.divide(total2, count, out=np.full(values.shape, np.nan), where=supported)
    return mean, np.sqrt(np.maximum(second - mean * mean, 0)), count / gates


def shoulders(native, offset):
    nr = native.shape[0]
    left, right = np.zeros(nr, int), np.zeros(nr, int)
    good = native.geometry_good.copy()
    spacing = native.audit["azimuth_spacing_deg"]
    for ray, az in enumerate(native.azimuth):
        for direction, target_array in ((-1, left), (1, right)):
            target = (az + direction * offset) % 360
            differences = np.abs((native.azimuth - target + 180) % 360 - 180)
            other = int(np.argmin(differences))
            target_array[ray] = other
            if other == ray or differences[other] > spacing * 0.55:
                good[ray] = False
                continue
            start, end = (other, ray) if direction < 0 else (ray, other)
            steps = (end - start) % nr
            if not native.full_ppi and end < start:
                good[ray] = False
            if np.any(native.gap_after[(start + np.arange(steps)) % nr]):
                good[ray] = False
            good[ray] &= native.geometry_good[other]
    return left, right, good


def extract(native, cfg, native_result=None, context=None):
    shape = native.shape
    expected_columns = 17 + 16 * len(cfg.radial_windows_m) + 2 * len(cfg.shoulder_offsets_deg)
    if int(np.prod(shape)) * expected_columns * 4 > 768 * 1024 * 1024:
        raise ValueError("feature matrix exceeds per-cut budget before allocation")
    dr = native.gate_spacing_m
    observed = native.field_available["DBZH"] & native.geometry_good[:, None]
    z = native.fields["DBZH"]
    empty = np.full(shape, np.nan, "float32")
    columns = {}
    for key in ("DBZH", "RHOHV", "ZDR", "SNR", "VR", "SW"):
        mask = native.field_available.get(key, np.zeros(shape, bool)) & observed
        columns[key] = np.where(mask, native.fields.get(key, empty), np.nan).astype("float32")
    # Range matters for footprint/sensitivity; radar ID/direction are NOT model features.
    columns["range_km"] = np.broadcast_to(native.ranges[None, :] / 1000, shape).copy()
    columns["gate_width_km"] = np.full(shape, dr / 1000)
    columns["azimuth_spacing_deg"] = np.full(shape, native.audit["azimuth_spacing_deg"])
    columns["elevation_deg"] = np.broadcast_to(native.elevation[:, None], shape).copy()
    corrected = z - 20 * np.log10(np.maximum(native.ranges, dr / 2)[None, :] / 1000)
    phase = native.fields.get("PHIDP", empty)
    phase_ok = native.field_available.get("PHIDP", np.zeros(shape, bool)) & observed
    delta = np.full(shape, np.nan)
    pair = phase_ok[:, 1:] & phase_ok[:, :-1]
    jump = (phase[:, 1:] - phase[:, :-1] + cfg.phase_period_deg / 2) % cfg.phase_period_deg
    jump -= cfg.phase_period_deg / 2
    delta[:, 1:] = np.where(pair, jump, np.nan)
    columns["phase_pair_deg_per_km"] = delta / (dr / 1000)
    for metres in cfg.radial_windows_m:
        gates = max(3, int(round(metres / dr)))
        gates += gates % 2 == 0
        key = f"r{metres:g}m"
        for name, values, mask in (
            ("dbzh", z, observed),
            ("range_corrected", corrected, observed),
            (
                "rhohv",
                native.fields.get("RHOHV", empty),
                native.field_available.get("RHOHV", np.zeros(shape, bool)) & observed,
            ),
            (
                "zdr",
                native.fields.get("ZDR", empty),
                native.field_available.get("ZDR", np.zeros(shape, bool)) & observed,
            ),
            ("phase_abs_pair", np.abs(delta), np.isfinite(delta)),
        ):
            mean, std, support = window_moments(values, mask, gates, cfg.minimum_support_fraction)
            columns[f"{name}_mean_{key}"] = mean
            columns[f"{name}_std_{key}"] = std
            columns[f"{name}_support_{key}"] = np.where(observed, support, np.nan)
        angle = phase * (2 * np.pi / cfg.phase_period_deg)
        cos_mean, _, _ = window_moments(
            np.cos(angle), phase_ok, gates, cfg.minimum_support_fraction
        )
        sin_mean, _, _ = window_moments(
            np.sin(angle), phase_ok, gates, cfg.minimum_support_fraction
        )
        columns[f"phase_coherence_{key}"] = np.hypot(cos_mean, sin_mean)
    for offset in cfg.shoulder_offsets_deg:
        left, right, good = shoulders(native, offset)
        a = observed[left] & good[:, None] & observed
        b = observed[right] & good[:, None] & observed
        columns[f"shoulder_contrast_{offset:g}deg"] = np.where(
            a & b, z - np.maximum(z[left], z[right]), np.nan
        )
        columns[f"shoulder_support_{offset:g}deg"] = np.where(
            observed, a.astype(float) + b.astype(float), np.nan
        )
    for detector in (1, 2):
        columns[f"native_emitter{detector}"] = (
            native_result.scores[str(detector)].copy() if native_result else empty.copy()
        )
    modes = empty.copy()
    if native_result is not None:
        has_score = np.isfinite(native_result.scores["1"]) & np.isfinite(native_result.scores["2"])
        tiles = native_result.summary.get("tiles", [])
        modes[has_score] = 1 if tiles and tiles[0]["mode"] == "full_ppi" else 2
    columns["native_support_mode"] = modes
    # Only recorded geometry-vetted support can enter here. Missing stays NaN.
    context = context or {}
    for key in ("weather_support", "temporal_persistence", "temporal_samples"):
        value = np.asarray(context.get(key, empty), dtype=float)
        if np.isinf(value).any():
            raise ValueError("infinite context values are not unavailable evidence")
        if value.shape != shape:
            raise ValueError("context feature geometry mismatch")
        if key != "temporal_samples" and np.any(np.isfinite(value) & ((value < 0) | (value > 1))):
            raise ValueError("context score outside [0,1]")
        if key == "temporal_samples" and np.any(
            np.isfinite(value) & ((value < 0) | (value > 3) | (value != np.rint(value)))
        ):
            raise ValueError("invalid independent temporal sample count")
        columns[key] = value.copy()
    count = columns["temporal_samples"]
    columns["temporal_persistence"][~np.isfinite(count) | (count == 0)] = np.nan
    names = tuple(columns)
    if observed.size * len(names) * 4 > 768 * 1024 * 1024:
        raise ValueError("feature matrix exceeds configured 768 MiB per-cut budget")
    matrix = np.empty((observed.size, len(names)), "float32")
    for i, values in enumerate(columns.values()):
        matrix[:, i] = np.where(observed, values, np.nan).reshape(-1)
    metadata = {
        "schema_version": FEATURE_SCHEMA,
        "names": names,
        "feature_config": cfg.model_dump(mode="json"),
        "missing_semantics": "nan_uncomputed_not_no_rain",
        "native_semantics": "no_native"
        if native_result is None
        else native_result.summary.get("semantic_identity", "native_unavailable"),
        "raw_mutated": False,
        "feature_code_sha256": hashlib.sha256(Path(__file__).read_bytes()).hexdigest(),
    }
    metadata["feature_identity"] = digest({k: v for k, v in metadata.items() if k != "raw_mutated"})
    return Features(matrix, names, shape, metadata)
