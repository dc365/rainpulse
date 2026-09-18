"""Stage 1: nominate raw-support ridges, without a missing-flank-as-clear claim."""
import numpy as np
from ..arrays import native_geometry, moment, runs
from .geometry import angular_stencil, angular_span, window_fields


def support_topology(native, cfg, blocked):
    r, az, dr, good, gaps = native_geometry(native)
    z, observed = moment(native, "DBZH")
    observed = observed & good[:, None]
    blocked = blocked | ~good[:, None]
    candidate = np.zeros(native.shape, bool)
    bits = np.zeros(native.shape, "uint16")
    flank_unknown = np.zeros(native.shape, bool)
    strength = np.full(native.shape, np.nan, "float32")
    width = np.full(native.shape, np.nan, "float32")
    for bit, scale in enumerate(cfg.scales_m):
        fraction, _ = window_fields(z, observed, blocked, dr, scale)
        solid = (fraction >= cfg.support_fraction) & ~blocked
        for gate in range(native.shape[1]):
            rows = np.flatnonzero(solid[:, gate])
            splits = np.flatnonzero((np.diff(rows) != 1) | gaps[rows[:-1]]) + 1
            for run in np.split(rows, splits):
                if not len(run):
                    continue
                lo, hi = int(run[0]), int(run[-1])
                ix = angular_stencil(native, lo-1, hi+2, good, gaps)
                if ix is None or blocked[lo-1, gate] or blocked[hi+1, gate]:
                    continue
                span = angular_span(az, run)
                if span > cfg.maximum_width_deg or r[gate]*np.deg2rad(span) > cfg.maximum_width_m:
                    continue
                if (fraction[lo-1, gate] > cfg.outside_support_fraction or
                        fraction[hi+1, gate] > cfg.outside_support_fraction):
                    continue
                use = run[observed[run, gate] & ~blocked[run, gate]]
                candidate[use, gate] = True
                bits[use, gate] |= 1 << bit
                flank_unknown[use, gate] |= not (observed[lo-1, gate] and observed[hi+1, gate])
                strength[use, gate] = np.fmax(strength[use, gate], fraction[use, gate])
                old = width[use, gate]
                width[use, gate] = np.where(np.isfinite(old), np.minimum(old, span), span)
    return {
        "RV2_TOPOLOGY_MASK": candidate.astype("uint8"),
        "RV2_SCALE_BITS": bits,
        "RV2_UNKNOWN_FLANK_MASK": flank_unknown.astype("uint8"),
        "RV2_SUPPORT_FRACTION": strength,
        "RV2_TOPOLOGY_WIDTH_DEG": width,
    }
