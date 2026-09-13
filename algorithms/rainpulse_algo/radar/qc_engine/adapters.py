"""Native-cut adaptation. No merging, range extrapolation, or synthetic moments."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

import numpy as np
from scipy import ndimage

from .profile import OpenSourceQCProfile

FIELD_NAMES = {
    "DBZH": ("reflectivity", "dBZ"),
    "RHOHV": ("cross_correlation_ratio", "1"),
    "ZDR": ("differential_reflectivity", "dB"),
    "PHIDP": ("differential_phase", "degree"),
    "VR": ("velocity", "m s-1"),
    "SW": ("spectrum_width", "m s-1"),
    "SNR": ("signal_to_noise_ratio", "dB"),
}


@dataclass(frozen=True)
class NativeSweep:
    name: str
    azimuth: np.ndarray
    elevation: np.ndarray
    ranges: np.ndarray
    ray_time: np.ndarray
    fields: dict[str, np.ndarray]
    field_available: dict[str, np.ndarray]
    original_indices: np.ndarray
    full_ppi: bool
    geometry_good: np.ndarray
    gap_after: np.ndarray
    attrs: dict[str, Any]
    audit: dict[str, Any]

    @property
    def shape(self) -> tuple[int, int]:
        return len(self.azimuth), len(self.ranges)

    @property
    def gate_spacing_m(self) -> float:
        return float(np.median(np.diff(self.ranges)))

    def restore(self, values: np.ndarray) -> np.ndarray:
        if values.shape != self.shape:
            raise ValueError("result geometry differs from native sweep")
        result = np.empty_like(values)
        result[self.original_indices] = values
        return result

    def support(self, mask: np.ndarray, ray_radius: int = 0, gate_radius: int = 0) -> np.ndarray:
        """Full measured stencil only, including topology of missing/duplicate rays."""
        if mask.shape != self.shape:
            raise ValueError("support geometry mismatch")
        safe = np.asarray(mask, dtype=bool) & self.geometry_good[:, None]
        rows = np.ones(self.shape[0], dtype=bool)
        for index in np.flatnonzero(self.gap_after):
            for offset in range(-ray_radius + 1, ray_radius + 1):
                j = int(index) + offset
                if self.full_ppi:
                    j %= len(rows)
                if 0 <= j < len(rows):
                    rows[j] = False
        safe &= rows[:, None]
        if ray_radius:
            safe = np.pad(
                safe,
                ((ray_radius, ray_radius), (0, 0)),
                mode="wrap" if self.full_ppi else "constant",
            )
        if gate_radius:
            safe = np.pad(safe, ((0, 0), (gate_radius, gate_radius)), mode="constant")
        result = ndimage.minimum_filter(
            safe.astype("uint8"),
            size=(2 * ray_radius + 1, 2 * gate_radius + 1),
            mode="constant",
            cval=0,
        ).astype(bool)
        return result[
            ray_radius : ray_radius + self.shape[0], gate_radius : gate_radius + self.shape[1]
        ]

    def to_pyart(self):
        import pyart

        fields = {}
        for name, values in self.fields.items():
            target, units = FIELD_NAMES[name]
            fields[target] = {
                "data": np.ma.array(values.copy(), mask=~self.field_available[name]),
                "units": units,
            }
        times = self.ray_time
        if np.issubdtype(times.dtype, np.datetime64):
            times = times.astype("datetime64[ns]").astype("float64") / 1e9
        return pyart.core.Radar(
            time={
                "data": np.asarray(times, dtype="float64"),
                "units": "seconds since 1970-01-01T00:00:00Z",
            },
            _range={"data": self.ranges.copy(), "units": "m"},
            fields=fields,
            metadata={
                "instrument_name": self.attrs.get("radar_id", "unknown"),
                "source_cut": self.name,
            },
            scan_type="ppi",
            latitude={"data": np.array([self.attrs.get("site_latitude_deg", np.nan)])},
            longitude={"data": np.array([self.attrs.get("site_longitude_deg", np.nan)])},
            altitude={"data": np.array([self.attrs.get("antenna_altitude_m", np.nan)])},
            sweep_number={"data": np.array([0], dtype="int32")},
            sweep_mode={"data": np.array(["azimuth_surveillance" if self.full_ppi else "sector"])},
            fixed_angle={"data": np.array([float(np.median(self.elevation))])},
            sweep_start_ray_index={"data": np.array([0], dtype="int32")},
            sweep_end_ray_index={"data": np.array([self.shape[0] - 1], dtype="int32")},
            azimuth={"data": self.azimuth.copy()},
            elevation={"data": self.elevation.copy()},
        )


def adapt_sweep(root, name: str, profile: OpenSourceQCProfile) -> NativeSweep:
    group = root[name]
    az = np.asarray(group["azimuth"][:], dtype="float64")
    el = np.asarray(group["elevation"][:], dtype="float64")
    ranges = np.asarray(group["range"][:], dtype="float64")
    times = group["ray_time"][:]
    if az.ndim != 1 or el.shape != az.shape or times.shape != az.shape or ranges.ndim != 1:
        raise ValueError("invalid native ray/gate coordinates")
    if not 2 <= len(az) <= profile.geometry.maximum_ray_count:
        raise ValueError("native ray count outside configured resource bounds")
    if not 2 <= len(ranges) <= profile.geometry.maximum_gate_count:
        raise ValueError("native gate count outside configured resource bounds")
    if not (
        np.all(np.isfinite(az))
        and np.all(np.isfinite(el))
        and np.all(np.isfinite(ranges))
        and np.all((az >= 0) & (az < 360))
        and np.all(np.diff(ranges) > 0)
        and ranges[0] >= 0
    ):
        raise ValueError("invalid azimuth, elevation or increasing range")
    if np.ptp(el) > profile.geometry.maximum_elevation_spread_deg:
        raise ValueError("incompatible elevations inside one source cut")
    if not np.allclose(np.diff(ranges), np.median(np.diff(ranges)), rtol=0.001, atol=0.001):
        raise ValueError("nonuniform native range is not supported by the library stencil")
    if np.issubdtype(times.dtype, np.datetime64):
        if np.any(np.isnat(times)):
            raise ValueError("missing ray time")
    elif np.any(~np.isfinite(times)):
        raise ValueError("invalid ray time")
    order = np.argsort(az, kind="stable")
    sorted_az = az[order]
    gaps = np.diff(np.r_[sorted_az, sorted_az[0] + 360])
    positive = gaps[gaps > profile.geometry.duplicate_tolerance_deg]
    spacing = float(np.median(positive)) if positive.size else 360.0
    duplicate = gaps <= profile.geometry.duplicate_tolerance_deg
    big = gaps > spacing * profile.geometry.maximum_azimuth_gap_factor
    full = bool(spacing <= 3.0 and not np.any(big) and not np.any(duplicate))
    # Put a sector's largest discontinuity at its outer edge, including north-crossing sectors.
    if not full:
        shift = (int(np.argmax(gaps)) + 1) % len(order)
        order = np.roll(order, -shift)
    azimuth = az[order]
    gap_after = ((np.roll(azimuth, -1) - azimuth) % 360) > (
        spacing * profile.geometry.maximum_azimuth_gap_factor
    )
    duplicates = ((np.roll(azimuth, -1) - azimuth) % 360) <= (
        profile.geometry.duplicate_tolerance_deg
    )
    good = ~(duplicates | np.roll(duplicates, 1))
    fields, available, audit = {}, {}, {}
    lower, upper = profile.echo.dbzh_valid_range_dbz
    for field, (_, expected_unit) in FIELD_NAMES.items():
        if field not in group:
            continue
        source = group[field]
        values = np.asarray(source[:], dtype="float32")
        if values.shape != (len(az), len(ranges)):
            raise ValueError(f"{field}: different moment geometry; cross-cut merging is forbidden")
        units = source.attrs.get("units")
        if units and units != expected_unit:
            raise ValueError(f"{field}: units {units!r} differ from {expected_unit!r}")
        for attr, coordinate in (("azimuth", az), ("range", ranges), ("elevation", el)):
            own = source.attrs.get(f"{attr}_coordinates")
            if own is not None and not np.array_equal(np.asarray(own), coordinate):
                raise ValueError(f"{field}: declared {attr} coordinates are not co-registered")
        values = values[order].copy()
        finite = np.isfinite(values)
        valid = finite.copy()
        if field == "DBZH":
            valid &= (values >= lower) & (values <= upper)
        elif field == "RHOHV":
            valid &= (values >= 0) & (values <= 1)
        elif field == "SW":
            valid &= values >= 0
        valid &= good[:, None]
        values.flags.writeable = False
        fields[field], available[field] = values, valid
        audit[field] = {
            "available_gates": int(np.count_nonzero(valid)),
            "invalid_finite_gates": int(np.count_nonzero(finite & ~valid)),
            "units": units or "canonical_contract",
            "source_cut": name,
        }
    if "DBZH" not in fields:
        fields["DBZH"] = np.full((len(az), len(ranges)), np.nan, dtype="float32")
        available["DBZH"] = np.zeros((len(az), len(ranges)), dtype=bool)
    return NativeSweep(
        name,
        azimuth,
        el[order],
        ranges,
        times[order],
        fields,
        available,
        order,
        full,
        good,
        gap_after,
        dict(root.attrs),
        {
            "fields": audit,
            "source_cut": name,
            "cut_metadata": {
                key: group.attrs.get(key)
                for key in (
                    "cut_index",
                    "source_cut_index",
                    "waveform",
                    "waveform_type",
                    "moment_family",
                )
            },
            "full_ppi": full,
            "duplicate_ray_count": int(np.count_nonzero(~good)),
            "gap_count": int(np.count_nonzero(gap_after)),
            "azimuth_spacing_deg": spacing,
            "range_spacing_m": float(np.median(np.diff(ranges))),
            "missing_moments": sorted(set(FIELD_NAMES) - set(fields)),
        },
    )
