"""Measured two-sided narrow spike hypothesis, independent of wide angle support."""

import numpy as np

from .radial_opening import radial_opening


def narrow_source(native, reference, residual, *, weather=None, conflicts=None):
    from skimage.morphology import footprint_rectangle, opening

    shape = native.shape
    arrays = [reference, residual]
    weather = np.zeros(shape, bool) if weather is None else np.asarray(weather, bool)
    conflicts = np.zeros(shape, bool) if conflicts is None else np.asarray(conflicts, bool)
    if any(np.shape(x) != shape for x in [*arrays, weather, conflicts]):
        raise ValueError("narrow source geometry mismatch")
    z = native.fields["DBZH"]
    valid = native.field_available["DBZH"] & np.isfinite(z) & native.geometry_good[:, None]
    side = valid & np.roll(valid, 1, axis=0) & np.roll(valid, -1, axis=0)
    rows = ~native.gap_after & ~np.roll(native.gap_after, 1)
    side &= rows[:, None]
    if not native.full_ppi:
        side[[0, -1]] = False
    contrast = side & (z - np.roll(z, 1, axis=0) >= 10) & (z - np.roll(z, -1, axis=0) >= 10)
    length = int(np.ceil(20000 / native.gate_spacing_m)) | 1
    continuity = opening(contrast, footprint_rectangle((1, length)), mode="constant", cval=0)
    long = radial_opening(z, valid, native.gate_spacing_m)
    source = np.asarray(reference, bool) & np.isfinite(residual) & (abs(residual) <= 2.5)
    result = long & continuity & source & ~weather & ~conflicts
    reason = np.zeros(shape, "uint16")
    for mask, bit in [
        (~source, 1),
        (~side, 2),
        (side & ~contrast, 4),
        (~continuity, 8),
        (weather | conflicts, 16),
        (long, 32),
        (result, 64),
    ]:
        reason[mask] |= bit
    return result, reason
