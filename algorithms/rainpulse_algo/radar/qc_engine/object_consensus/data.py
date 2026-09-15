"""Read-only raw polar input. No baseline/ROI/anchor reaches inference."""
from __future__ import annotations

from dataclasses import dataclass
import hashlib
import json
from typing import Mapping
from types import MappingProxyType
import numpy as np

MOMENTS = ("DBZH", "SNR", "RHOHV", "PHIDP", "ZDR", "VR", "SW")


def array_sha(a):
    a = np.asarray(a)
    h = hashlib.sha256(str(a.dtype).encode() + json.dumps(a.shape).encode())
    h.update(np.ascontiguousarray(a).tobytes())
    return h.hexdigest()


@dataclass(frozen=True)
class RawScan:
    fields: dict
    available: dict
    ranges: np.ndarray
    azimuth: np.ndarray
    elevation: np.ndarray
    good: np.ndarray
    gaps: np.ndarray
    full_ppi: bool
    phase_period: float
    identity: str

    @property
    def shape(self):
        return self.fields["DBZH"].shape

    @property
    def dr(self):
        return float(self.ranges[1] - self.ranges[0])

    @property
    def observed(self):
        return self.available["DBZH"]

    @classmethod
    def from_arrays(cls, d: Mapping, *, full_ppi: bool, phase_period: float = 360.0,
                    maximum_gates: int = 12000000):
        if type(full_ppi) is not bool or not np.isfinite(phase_period) or phase_period <= 0:
            raise ValueError("explicit PPI topology and finite positive phase period required")
        z = np.asarray(d["raw_DBZH"])
        if z.ndim != 2 or z.shape[0] < 1 or z.shape[1] < 2 or z.size > maximum_gates:
            raise ValueError("unsupported polar shape or gate budget")
        if z.dtype.kind not in "fiu":
            raise ValueError("numeric raw reflectivity required")
        shape = z.shape
        geometry = {}
        for key, size, dtype in (("range_m", shape[1], "float64"),
                                 ("azimuth_rotated_deg", shape[0], "float64"),
                                 ("elevation_deg", shape[0], "float64"),
                                 ("geometry_good", shape[0], "bool"),
                                 ("gap_after", shape[0], "bool")):
            x = np.asarray(d[key])
            if x.shape != (size,) or x.dtype.kind not in "bfiu" or not np.isfinite(x).all():
                raise ValueError(f"invalid geometry: {key}")
            if dtype == "bool" and not np.isin(x, [0, 1]).all():
                raise ValueError(f"invalid geometry mask: {key}")
            geometry[key] = x.astype(dtype, copy=True)
        r = geometry["range_m"]
        if r[0] < 0 or np.any(np.diff(r) <= 0) or not np.allclose(np.diff(r), r[1]-r[0], rtol=1e-6):
            raise ValueError("strictly increasing uniform physical ranges required")
        if not ((geometry["elevation_deg"] >= -10) & (geometry["elevation_deg"] <= 90)).all():
            raise ValueError("invalid elevation")
        geometry["azimuth_rotated_deg"] %= 360.0
        fields, available = {}, {}
        for name in MOMENTS:
            field, mask = "raw_" + name, "available_" + name
            if field not in d and mask in d and np.any(d[mask]):
                raise ValueError(f"availability without measurement: {name}")
            if field not in d:
                x, a = np.full(shape, np.nan), np.zeros(shape, bool)
            else:
                if mask not in d:
                    raise ValueError(f"explicit availability required: {name}")
                x, a = np.asarray(d[field]), np.asarray(d[mask])
                if x.shape != shape or a.shape != shape or x.dtype.kind not in "fiu" or a.dtype.kind not in "biu":
                    raise ValueError(f"invalid field or availability: {name}")
                if not np.isin(a, [0, 1]).all() or np.isinf(x).any():
                    raise ValueError(f"invalid values/mask: {name}")
                x, a = x.astype("float64", copy=True), a.astype(bool, copy=True)
                a &= np.isfinite(x)
            if name == "RHOHV" and np.any(a & ((x < 0) | (x > 1.05))):
                raise ValueError("RHOHV outside measurement domain")
            fields[name], available[name] = x, a
        for x in [*geometry.values(), *fields.values(), *available.values()]:
            x.setflags(write=False)
        values = {**{k: array_sha(v) for k, v in geometry.items()},
                  "fields": {k: array_sha(v) for k, v in fields.items()},
                  "availability": {k: array_sha(v) for k, v in available.items()},
                  "full_ppi": full_ppi, "period": float(phase_period)}
        identity = hashlib.sha256(json.dumps(values, sort_keys=True).encode()).hexdigest()
        return cls(MappingProxyType(fields), MappingProxyType(available), r, geometry["azimuth_rotated_deg"],
                   geometry["elevation_deg"], geometry["geometry_good"],
                   geometry["gap_after"], full_ppi, float(phase_period), identity)
