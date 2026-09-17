"""Strict array contracts. NaN is never converted to a truthy mask."""
import numpy as np


def mask(value, shape, name, *, absent=False):
    if value is None:
        return np.full(shape, absent, bool)
    x = np.asarray(value)
    if x.shape != shape or x.dtype.kind not in "buif" or not np.isfinite(x).all() or np.any((x != 0) & (x != 1)):
        raise ValueError(f"{name}: expected a finite binary array of shape {shape}")
    return x.astype(bool, copy=False)


def numeric(value, shape, name, *, lower=None, upper=None):
    if value is None:
        return np.full(shape, np.nan, dtype="float32")
    x = np.asarray(value, dtype="float32")
    if x.shape != shape or np.isinf(x).any():
        raise ValueError(f"{name}: invalid shape or infinite value")
    finite = np.isfinite(x)
    if lower is not None and np.any(finite & (x < lower)):
        raise ValueError(f"{name}: below lower bound")
    if upper is not None and np.any(finite & (x > upper)):
        raise ValueError(f"{name}: above upper bound")
    return x


def moment(native, name):
    values = numeric(native.fields.get(name), native.shape, name)
    available = mask(native.field_available.get(name), native.shape, name + "_available")
    return values, available & np.isfinite(values)


def runs(row):
    edges = np.diff(np.r_[False, np.asarray(row, bool), False].astype("int8"))
    return list(zip(np.flatnonzero(edges == 1), np.flatnonzero(edges == -1), strict=True))


def native_geometry(native):
    r = np.asarray(native.ranges, dtype=float)
    az = np.asarray(native.azimuth, dtype=float)
    if r.shape != (native.shape[1],) or az.shape != (native.shape[0],) or len(r) < 2:
        raise ValueError("native geometry shape mismatch")
    if not np.isfinite(r).all() or not np.isfinite(az).all() or r[0] < 0 or np.any(np.diff(r) <= 0):
        raise ValueError("native coordinates must be finite, increasing ranges")
    dr = float(np.median(np.diff(r)))
    if not np.allclose(np.diff(r), dr, rtol=.001, atol=.001):
        raise ValueError("nonuniform range spacing requires a physical resampling contract")
    good = mask(native.geometry_good, (native.shape[0],), "geometry_good")
    gaps = mask(native.gap_after, (native.shape[0],), "gap_after")
    return r, az, dr, good, gaps
