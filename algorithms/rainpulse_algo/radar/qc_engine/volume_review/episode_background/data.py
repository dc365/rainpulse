"""Raw input contracts and bounded, footprint-aware nearest observation lookup."""
from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timezone
import hashlib
import json
import numpy as np

MOMENTS = ("DBZH", "SNR", "RHOHV", "ZDR", "PHIDP", "VR", "SW")


def utc(value: str) -> datetime:
    t = datetime.fromisoformat(value.replace("Z", "+00:00"))
    if t.tzinfo is None:
        raise ValueError("explicit UTC/timezone is required")
    return t.astimezone(timezone.utc)


def json_bytes(value) -> bytes:
    return json.dumps(value, sort_keys=True, separators=(",", ":"), allow_nan=False).encode()


def sha(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def is_hash(value) -> bool:
    return isinstance(value, str) and len(value) == 64 and all(c in "0123456789abcdef" for c in value)


def binary(value, shape, name):
    a = np.asarray(value)
    if a.shape != shape or not np.isin(a, (0, 1)).all():
        raise ValueError("invalid binary geometry: " + name)
    return a.astype(bool)


def wrap(value):
    return (value + 180.) % 360. - 180.


@dataclass(frozen=True)
class Sample:
    radar_id: str
    scan_id: str
    sweep_id: str
    processing_id: str
    observed_at: str
    source_sha256: str
    azimuth: np.ndarray
    elevation: np.ndarray
    ranges: np.ndarray
    fields: dict[str, np.ndarray]
    available: dict[str, np.ndarray]
    geometry_good: np.ndarray
    acquired: np.ndarray | None = None
    no_echo: np.ndarray | None = None

    def __post_init__(self):
        for key in ("radar_id", "scan_id", "sweep_id", "processing_id"):
            if not isinstance(getattr(self, key), str) or not getattr(self, key):
                raise ValueError("missing input identity: " + key)
        utc(self.observed_at)
        if not is_hash(self.source_sha256):
            raise ValueError("source hash missing or invalid")
        az, r, el = map(np.asarray, (self.azimuth, self.ranges, self.elevation))
        if (az.ndim != 1 or r.ndim != 1 or len(az) < 2 or len(r) < 2 or
                el.shape != az.shape or not np.isfinite(az).all() or not np.isfinite(el).all() or
                not np.isfinite(r).all() or np.any(r < 0) or np.any(np.diff(r) <= 0) or
                np.any(abs(el) > 90)):
            raise ValueError("invalid raw sweep geometry")
        if "DBZH" not in self.fields or "DBZH" not in self.available:
            raise ValueError("raw DBZH and explicit availability required")
        binary(self.geometry_good, az.shape, "geometry_good")
        for key, value in self.fields.items():
            if key not in MOMENTS or np.asarray(value).shape != self.shape:
                raise ValueError("unexpected raw moment or geometry: " + key)
            if key not in self.available:
                raise ValueError("explicit availability required: " + key)
            a = binary(self.available[key], self.shape, key)
            if np.any(a & ~np.isfinite(value)):
                raise ValueError("available moment contains nonfinite values: " + key)
        if set(self.available) != set(self.fields):
            raise ValueError("availability/moment keys differ")
        if (self.acquired is None) != (self.no_echo is None):
            raise ValueError("acquired and confirmed no-echo must be supplied together")
        if self.acquired is not None:
            acq = binary(self.acquired, self.shape, "acquired")
            ne = binary(self.no_echo, self.shape, "no_echo")
            if np.any(ne & ~acq) or np.any(self.available["DBZH"] & ~acq):
                raise ValueError("measurement/no-echo outside declared acquisition")

    @property
    def shape(self):
        return len(self.azimuth), len(self.ranges)

    def moment(self, key):
        value = np.asarray(self.fields.get(key, np.full(self.shape, np.nan)), float)
        ok = np.asarray(self.available.get(key, np.zeros(self.shape, bool)), bool).copy()
        az = np.asarray(self.azimuth) % 360
        order = np.argsort(az, kind="stable")
        delta = np.diff(np.r_[az[order], az[order[0]] + 360])
        duplicate = delta <= .01
        geometric = np.asarray(self.geometry_good, bool).copy()
        geometric[order] &= ~(duplicate | np.roll(duplicate, 1))
        ok &= geometric[:, None] & np.isfinite(value)
        if key == "DBZH": ok &= (value >= -32) & (value <= 80)
        if key == "RHOHV": ok &= (value >= 0) & (value <= 1)
        if key == "SW": ok &= value >= 0
        return value, ok

    @property
    def raw_digest(self):
        h = hashlib.sha256()
        arrays = {"azimuth": self.azimuth, "elevation": self.elevation, "range": self.ranges,
                  "geometry_good": self.geometry_good, **self.fields,
                  **{"available_" + k: v for k, v in self.available.items()}}
        if self.acquired is not None:
            arrays.update(acquired=self.acquired, no_echo=self.no_echo)
        for k, v in sorted(arrays.items()):
            a = np.ascontiguousarray(v)
            h.update(json_bytes([k, str(a.dtype), list(a.shape)])); h.update(a.tobytes())
        return h.hexdigest()


def map_footprints(source_az, source_el, source_r, source_good, target_az, target_el, target_r, cfg):
    """Nearest actual footprint, never extrapolate across gaps or missing rays.

    Returns row and gate indices, available footprint mask and mapping offsets.
    A many-to-one match is descriptive; it never increases temporal sample count.
    """
    az = np.asarray(source_az, float) % 360
    r = np.asarray(source_r, float)
    order = np.argsort(az, kind="stable"); az = az[order]
    gaps = np.diff(np.r_[az, az[0] + 360])
    positive = gaps[gaps > .01]
    nominal = float(np.median(positive)) if len(positive) else 0.
    duplicate = gaps <= .01
    good = np.asarray(source_good, bool)[order] & ~duplicate & ~np.roll(duplicate, 1)
    half_after = np.minimum(np.where(gaps > 1.8 * nominal, nominal, gaps) / 2., cfg.maximum_azimuth_error_deg)
    half_before = np.roll(half_after, 1)
    ta = np.asarray(target_az, float) % 360
    pos = np.searchsorted(az, ta)
    left, right = (pos - 1) % len(az), pos % len(az)
    j = np.where(abs(wrap(ta - az[left])) <= abs(wrap(ta - az[right])), left, right)
    diff = wrap(ta - az[j])
    row_ok = good[j] & (np.where(diff >= 0, diff <= half_after[j] + 1e-9, -diff <= half_before[j] + 1e-9))
    rows = order[j]
    elev_error = abs(np.asarray(source_el)[rows] - np.asarray(target_el))
    row_ok &= elev_error <= cfg.maximum_elevation_error_deg
    tr = np.asarray(target_r, float)
    right = np.searchsorted(r, tr).clip(0, len(r) - 1); left = np.maximum(0, right - 1)
    gates = np.where(abs(tr - r[left]) <= abs(tr - r[right]), left, right)
    dr = np.diff(r); nominal_r = float(np.median(dr))
    sides = np.minimum(np.where(dr > 1.8 * nominal_r, nominal_r, dr) / 2., cfg.maximum_range_error_m)
    before = np.r_[min(nominal_r / 2., cfg.maximum_range_error_m), sides]
    after = np.r_[sides, min(nominal_r / 2., cfg.maximum_range_error_m)]
    rd = tr - r[gates]
    gate_ok = np.where(rd >= 0, rd <= after[gates] + 1e-9, -rd <= before[gates] + 1e-9)
    gate_ok &= (tr >= max(0, r[0] - before[0])) & (tr <= r[-1] + after[-1])
    ok = row_ok[:, None] & gate_ok[None, :]
    return rows, gates, ok, abs(diff), abs(rd)
