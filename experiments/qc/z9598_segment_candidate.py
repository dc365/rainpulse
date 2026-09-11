"""Offline candidate only; production profiles do not import this module."""
import numpy as np


def segment_mask(dbzh, valid, rhohv, ranges):
    """Require multiple locally consistent low-correlation range windows."""
    out = np.zeros(dbzh.shape, dtype=bool)
    for ray in range(len(dbzh)):
        observed = valid[ray] & np.isfinite(dbzh[ray])
        rho = rhohv[ray]
        support = np.zeros(len(ranges), dtype=bool)
        windows = 0
        for start in np.arange(100000., 360001., 50000.):
            window = (ranges >= start) & (ranges < start + 100000.)
            if window.sum() < 2 or np.ptp(ranges[window]) < 99000:
                continue
            echo = window & observed & (dbzh[ray] >= 10)
            available = echo & np.isfinite(rho) & (rho >= 0) & (rho <= 1)
            if echo.sum() < .80 * window.sum() or available.sum() < .90 * echo.sum():
                continue
            if np.median(rho[available]) > .65 or np.mean(rho[available] < .9) < .9:
                continue
            values = dbzh[ray, echo]
            corrected = values - 20 * np.log10(ranges[echo])
            if np.percentile(corrected, 75) - np.percentile(corrected, 25) > 6:
                continue
            n = max(1, len(values) // 5)
            if np.median(values[-n:]) - np.median(values[:n]) < 1:
                continue
            support |= window
            windows += 1
        if windows < 3 or not support.any() or np.ptp(ranges[support]) < 249000:
            continue
        protected = observed & np.isfinite(rho) & (rho >= .95)
        margin = protected.copy()
        for shift in (1, 2):
            margin[shift:] |= protected[:-shift]
            margin[:-shift] |= protected[shift:]
        out[ray] = support & observed & np.isfinite(rho) & (rho >= 0) & (rho < .9) & ~margin
    return out
