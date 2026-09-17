"""One-hop measured source adjacency; research candidate, never a deletion rule."""

import numpy as np

from .radial_opening import radial_opening


def source_edge(native, seed, reference, residual, *, weather, conflicts):
    shape = native.shape
    if any(np.shape(x) != shape for x in (seed, reference, residual, weather, conflicts)):
        raise ValueError("source edge geometry mismatch")
    z = native.fields["DBZH"]
    valid = native.field_available["DBZH"] & np.isfinite(z) & native.geometry_good[:, None]
    blocked = np.asarray(weather, bool) | np.asarray(conflicts, bool)
    frozen = np.asarray(seed, bool) & valid & ~blocked
    linked = np.zeros(shape, bool)
    for d in (-1, 1):
        good = ~(np.roll(native.gap_after, 1) if d == 1 else native.gap_after)
        if not native.full_ppi:
            good[0 if d == 1 else -1] = False
        angle = abs((native.azimuth - np.roll(native.azimuth, d) + 180) % 360 - 180)
        linked |= np.roll(frozen, d, axis=0) & (good & (angle > 0) & (angle <= 1.5))[:, None]
    own = np.asarray(reference, bool) & np.isfinite(residual) & (abs(residual) <= 2.5)
    long = radial_opening(z, valid, native.gate_spacing_m)
    proposal = valid & linked & own & long & ~blocked & ~np.asarray(seed, bool)
    reason = np.zeros(shape, "uint16")
    for mask, bit in ((linked, 1), (own, 2), (long, 4), (blocked, 8), (proposal, 16)):
        reason[mask] |= bit
    return proposal, reason
