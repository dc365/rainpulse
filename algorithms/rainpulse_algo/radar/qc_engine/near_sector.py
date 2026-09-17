"""Conservative connected source morphology; experimental candidates only."""

import numpy as np
from skimage.morphology import footprint_rectangle, opening


def near_sector(native, measured_source, *, weather, conflicts):
    if any(np.shape(a) != native.shape for a in (measured_source, weather, conflicts)):
        raise ValueError("near-sector geometry mismatch")
    if not np.isfinite(native.gate_spacing_m) or native.gate_spacing_m <= 0:
        raise ValueError("invalid gate spacing")
    source = (
        np.asarray(measured_source, bool)
        & ~np.asarray(weather, bool)
        & ~np.asarray(conflicts, bool)
    )
    source &= ((native.ranges > 0) & (native.ranges < 50000))[None, :]
    result = np.zeros(native.shape, bool)
    length = int(np.ceil(10000 / native.gate_spacing_m)) | 1
    # Conservative seam handling: no wrap across first/last ray, even for full PPI.
    edges = np.unique(np.r_[0, np.flatnonzero(native.gap_after) + 1, native.shape[0]])
    for lo, hi in zip(edges[:-1], edges[1:]):
        if hi - lo < 3 or native.shape[1] < length:
            continue
        result[lo:hi] = opening(
            source[lo:hi], footprint_rectangle((3, length)), mode="constant", cval=0
        )
    return result & source
