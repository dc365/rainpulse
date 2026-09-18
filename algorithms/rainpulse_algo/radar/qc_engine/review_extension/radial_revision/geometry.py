"""Raw measured support utilities. Unknown support is not an observation of zero."""
import numpy as np
from scipy.ndimage import uniform_filter1d
from ..arrays import runs


class ResourceLimit(RuntimeError):
    """Abort only the new extension; never return partial accepted proposals."""


def angular_stencil(native, start, stop, good, gaps):
    """Contiguous non-wrapping stencil. Conservative at the native array seam."""
    if start < 0 or stop > native.shape[0] or stop <= start:
        return None
    ix = np.arange(start, stop)
    if not good[ix].all() or gaps[ix[:-1]].any():
        return None
    return ix


def angular_span(azimuth, ix):
    a = np.rad2deg(np.unwrap(np.deg2rad(azimuth[ix])))
    return float(np.ptp(a))


def window_fields(z, observed, blocked, dr, scale_m):
    """Physical support fraction + conditional measured mean, cut at barriers.

    The denominator describes possible native coordinates, NOT measured clear
    air. Mean uses actual observations only. Partial boundary windows must still
    cover at least half the requested physical scale.
    """
    width = max(3, int(np.ceil(scale_m / dr)) | 1)
    fraction = np.zeros(z.shape, dtype="float32")
    mean = np.full(z.shape, np.nan, dtype="float32")
    for row in range(z.shape[0]):
        for lo, hi in runs(~blocked[row]):
            if (hi-lo)*dr < scale_m/2:
                continue
            hit = observed[row, lo:hi].astype(float)
            denominator = uniform_filter1d(np.ones(hi-lo), width, mode="constant")
            count = uniform_filter1d(hit, width, mode="constant")
            # Never nan_to_num the raw values into apparent zero observations.
            values = np.where(observed[row, lo:hi], z[row, lo:hi], 0.)
            total = uniform_filter1d(values, width, mode="constant")
            enough_extent = denominator * width * dr >= scale_m/2
            f = np.divide(count, denominator, out=np.zeros_like(count), where=denominator > 0)
            m = np.divide(total, count, out=np.full_like(total, np.nan), where=count > 1e-9)
            fraction[row, lo:hi] = np.where(enough_extent, f, 0)
            mean[row, lo:hi] = np.where(enough_extent & (count > 1e-9), m, np.nan)
    return fraction, mean


def numeric_plateaus(z, observed, ranges, *, blocks=None):
    """Diagnostic ambiguity, not knowledge of vendor saturation semantics.

    Supplying blocks confines reference decisions to each reference block, so
    target or guard changes cannot create a plateau inside a training block.
    """
    out = np.zeros(z.shape, bool)
    groups = [np.arange(z.shape[1])] if blocks is None else [
        np.flatnonzero(blocks == b) for b in np.unique(blocks)]
    for row in range(z.shape[0]):
        for ix in groups:
            same = (observed[row, ix[1:]] & observed[row, ix[:-1]] &
                    (np.abs(np.diff(z[row, ix])) < 1e-6) & (z[row, ix[1:]] >= 55))
            for lo, hi in runs(same):
                if ranges[ix[hi]] - ranges[ix[lo]] >= 25000:
                    out[row, ix[lo:hi+1]] = True
    return out
