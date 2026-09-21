"""Weak near-site nonmeteorological candidates; no inferred missing moments.

Polarization and reflectivity structure are two evidence families. Neighbourhood
agreement is a coverage/coherence check, NOT a third independent measurement.
This does not distinguish biological echoes from ground clutter.
"""
import numpy as np
from scipy.ndimage import uniform_filter
from .arrays import moment, native_geometry


def candidates(native, cfg, texture):
    shape = native.shape
    empty = np.zeros(shape, bool)
    if not cfg.near_enabled:
        return empty, empty, np.full(shape, np.nan, "float32")
    r, az, dr, good, gaps = native_geometry(native)
    z, obs = moment(native, "DBZH")
    rho, arho = moment(native, "RHOHV")
    zdr, azdr = moment(native, "ZDR")
    snr, asnr = moment(native, "SNR")
    # Never join across sector gaps, duplicate azimuths or coarse scan geometry.
    delta = (np.roll(az, -1) - az) % 360
    edges = gaps | (delta <= 0) | (delta > 2.)
    safe = good & ~edges & ~np.roll(edges, 1)
    ready = obs & arho & azdr & asnr & (snr >= cfg.minimum_pol_snr_db) & safe[:, None]
    physical = (rho >= 0) & (rho <= 1) & (zdr >= -10) & (zdr <= 10)
    ready &= physical
    policy = getattr(cfg, "near_reliability", None)
    if policy is not None:
        # A numeric ZDR tail is not a reliable out-of-range physical value.
        # Exclude it from BOTH target decisions and neighbour voting. Missing
        # samples reduce support; they never become votes for no-rain.
        ready &= abs(zdr) < policy.maximum_abs_zdr_db
        ready &= (z >= policy.no_rain_below_dbz) & (r[None, :] >= policy.minimum_range_m)
    abnormal = ready & (rho <= cfg.near_maximum_rhohv) & ((zdr < -1.) | (zdr > 4.))
    width = max(3, int(round(cfg.near_range_window_m / dr)) | 1)
    # Constant boundaries: outside coverage is unavailable, never zero echo.
    coverage = uniform_filter(ready.astype("float32"), size=(3, width), mode="constant")
    votes = uniform_filter(abnormal.astype("float32"), size=(3, width), mode="constant")
    fraction = np.divide(votes, coverage, out=np.full(shape, np.nan, "float32"), where=coverage > 0)
    available = ready & (coverage >= cfg.near_minimum_coverage)
    selected = available & abnormal & texture & (fraction >= cfg.near_minimum_fraction)
    selected &= (r[None, :] <= cfg.near_maximum_range_m) & (z <= cfg.near_maximum_dbz)
    return selected, available, fraction
