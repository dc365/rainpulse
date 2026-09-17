"""Physical polar support, without filling missing observations."""
import hashlib
import json

import numpy as np


def digest_arrays(arrays):
    h = hashlib.sha256()
    for key, value in sorted(arrays.items()):
        a = np.asarray(value)
        if a.dtype.kind not in "bifu":
            raise ValueError("numeric arrays required")
        h.update(key.encode() + str(a.dtype).encode() + json.dumps(a.shape).encode())
        h.update(np.ascontiguousarray(a).tobytes())
    return h.hexdigest()


def measured(n, field):
    if field not in n.fields:
        return np.zeros(n.shape, bool)
    return np.asarray(n.field_available[field], bool) & np.isfinite(n.fields[field])


def runs(mask):
    e = np.diff(np.r_[False, mask, False].astype("int8"))
    return list(zip(np.flatnonzero(e == 1), np.flatnonzero(e == -1), strict=True))


def edge_geometry(n):
    delta = (np.roll(n.azimuth, -1) - n.azimuth) % 360
    x = delta[(delta > 1e-4) & (delta < 90)]
    spacing = float(np.median(x)) if len(x) else 1.0
    good = np.asarray(n.geometry_good, bool)
    safe = good & np.roll(good, -1) & ~np.asarray(n.gap_after, bool)
    safe &= (delta > 1e-4) & (delta <= 1.8 * spacing)
    safe &= abs(n.elevation - np.roll(n.elevation, -1)) <= 0.3
    if not n.full_ppi or n.shape[0] < 2:
        safe[-1] = False
    return safe, delta, spacing


def shifted_measured(n, offset):
    rows = np.arange(n.shape[0])
    source = (rows + offset) % n.shape[0]
    edges, _, _ = edge_geometry(n)
    safe = np.asarray(n.geometry_good, bool).copy() & (source != rows)
    for step in range(abs(offset)):
        edge = (rows + step) % len(rows) if offset > 0 else (rows - step - 1) % len(rows)
        safe &= edges[edge]
    return source, safe


def validate_native(n, maximum_gates=12000000):
    z = np.asarray(n.fields["DBZH"])
    if z.ndim != 2 or min(z.shape) < 1 or z.shape[1] < 2 or z.size > maximum_gates:
        raise ValueError("invalid polar shape or budget")
    for name, value in n.fields.items():
        if np.shape(value) != n.shape or np.isinf(value).any():
            raise ValueError("invalid moment")
        if name not in n.field_available or np.shape(n.field_available[name]) != n.shape:
            raise ValueError("explicit availability required")
    for value in (n.azimuth, n.elevation, n.geometry_good, n.gap_after):
        if np.shape(value) != (n.shape[0],):
            raise ValueError("ray geometry differs")
    if not np.isfinite(n.azimuth).all() or not np.isfinite(n.elevation).all():
        raise ValueError("nonfinite ray geometry")
    r = np.asarray(n.ranges)
    if r.shape != (n.shape[1],) or not np.isfinite(r).all() or r[0] < 0:
        raise ValueError("invalid range")
    if n.gate_spacing_m <= 0 or not np.allclose(np.diff(r), n.gate_spacing_m):
        raise ValueError("uniform positive range spacing required")
