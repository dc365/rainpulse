"""Immutable native arrays with explicit observation semantics and provenance."""
from dataclasses import dataclass
from types import MappingProxyType
import hashlib
import json
import numpy as np


class ResourceLimit(RuntimeError):
    """The whole enhancement abstains; no partial action may escape."""


def json_bytes(value):
    return json.dumps(value, sort_keys=True, separators=(",", ":"),
                      ensure_ascii=False, allow_nan=False).encode("utf-8")


def array_digest(arrays):
    h = hashlib.sha256()
    for key in sorted(arrays):
        a = np.asarray(arrays[key])
        if a.dtype.hasobject:
            raise ValueError("object arrays are not allowed")
        # Canonical endian and NaN payloads; numeric identity, not NPZ zip metadata.
        a = np.array(a, dtype=a.dtype.newbyteorder("<"), order="C", copy=True)
        if a.dtype.kind in "fc":
            a[np.isnan(a)] = np.nan
        header = json_bytes([key, a.dtype.str, list(a.shape)])
        h.update(len(header).to_bytes(8, "little")); h.update(header); h.update(a.tobytes())
    return h.hexdigest()


def checked_mask(value, shape, name):
    a = np.asarray(value)
    if a.shape != shape or not np.isin(a, (0, 1)).all():
        raise ValueError(f"{name}: binary native mask required")
    return a.astype(bool, copy=True)


def frozen(value, dtype=None):
    a = np.array(value, dtype=dtype, copy=True)
    if a.dtype.hasobject:
        raise ValueError("object arrays forbidden")
    a.flags.writeable = False
    return a


@dataclass(frozen=True)
class Sweep:
    name: str
    azimuth: np.ndarray
    elevation: np.ndarray
    ranges: np.ndarray
    fields: dict
    available: dict
    good: np.ndarray
    gap_after: np.ndarray
    ray_time_s: np.ndarray | None = None
    no_echo: np.ndarray | None = None
    original_indices: np.ndarray | None = None

    def __post_init__(self):
        nr, ng = len(self.azimuth), len(self.ranges)
        if nr < 2 or ng < 2 or not self.name:
            raise ValueError("at least two rays/gates and an identity required")
        az = np.asarray(self.azimuth, float); r = np.asarray(self.ranges, float)
        el = np.asarray(self.elevation, float)
        if el.ndim == 0:
            el = np.full(nr, el)
        if (az.shape != (nr,) or r.shape != (ng,) or el.shape != (nr,) or
            not np.isfinite(np.r_[az, r, el]).all() or np.any((az < 0) | (az >= 360)) or
            np.any((el < -5) | (el > 90)) or r[0] < 0 or np.any(np.diff(r) <= 0)):
            raise ValueError("invalid native coordinates")
        if not np.allclose(np.diff(r), np.median(np.diff(r)), rtol=.001, atol=.001):
            raise ValueError("nonuniform range stencil not supported")
        good = checked_mask(self.good, (nr,), "good rays")
        gaps = checked_mask(self.gap_after, (nr,), "ray edges")
        if self.original_indices is not None:
            indices = np.asarray(self.original_indices)
            if (indices.shape != (nr,) or indices.dtype.kind not in "iu"
                    or not np.array_equal(np.sort(indices), np.arange(nr))):
                raise ValueError("original ray indices must be a permutation")
        # Accept native sorted/rotated order only: never infer adjacency by raw row index.
        da = (np.roll(az, -1)-az) % 360
        if np.any((da <= .01) & good & np.roll(good, -1)):
            raise ValueError("duplicate rays must be marked unusable")
        if np.any((da > 10) & ~gaps):
            raise ValueError("large angular discontinuity must be declared")
        fields, available = {}, {}
        for key, value in self.fields.items():
            a = np.asarray(value, "float32")
            if a.shape != (nr, ng) or key not in self.available:
                raise ValueError("moment or availability geometry mismatch")
            ok = checked_mask(self.available[key], (nr, ng), key) & good[:, None]
            if np.any(ok & ~np.isfinite(a)):
                raise ValueError("available moment is nonfinite")
            if key == "RHOHV":
                ok &= (a >= 0) & (a <= 1)
            fields[key] = frozen(a); available[key] = frozen(ok)
        if "DBZH" not in fields:
            raise ValueError("explicit DBZH, even if wholly missing, required")
        no = np.zeros((nr, ng), bool) if self.no_echo is None else checked_mask(self.no_echo, (nr, ng), "no echo")
        if np.any(no & available["DBZH"]):
            raise ValueError("no-echo and detected reflectivity overlap")
        time = self.ray_time_s
        if time is not None:
            time = np.asarray(time, float)
            if time.shape != (nr,) or not np.isfinite(time).all():
                raise ValueError("relative ray times must be finite seconds")
        for key, value in (("azimuth", az), ("ranges", r), ("elevation", el),
                           ("good", good), ("gap_after", gaps), ("no_echo", no)):
            object.__setattr__(self, key, frozen(value))
        object.__setattr__(self, "fields", MappingProxyType(fields))
        object.__setattr__(self, "available", MappingProxyType(available))
        object.__setattr__(self, "ray_time_s", None if time is None else frozen(time))

    @property
    def shape(self):
        return len(self.azimuth), len(self.ranges)

    @property
    def dr(self):
        return float(np.median(np.diff(self.ranges)))

    @property
    def observed(self):
        return self.available["DBZH"]

    def moment(self, key):
        if key not in self.fields:
            return np.full(self.shape, np.nan), np.zeros(self.shape, bool)
        return self.fields[key], self.available[key]

    def arrays(self):
        out = {"azimuth": self.azimuth, "elevation": self.elevation, "range": self.ranges,
               "GEOMETRY_GOOD": self.good, "GAP_AFTER": self.gap_after, "VALID_NO_ECHO": self.no_echo}
        if self.ray_time_s is not None:
            out["ray_time_s"] = self.ray_time_s
        for k, a in self.fields.items():
            out[k+"_RAW"] = a; out[k+"_AVAILABLE"] = self.available[k]
        return out

    @property
    def digest(self):
        return array_digest(self.arrays())
