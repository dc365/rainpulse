"""Demand-driven evaluation of the unchanged independent radial median candidate.

The median/minimum windows have shape (1,n): different rays never contribute to
one another. Potential-target rays keep their ENTIRE range axis and original
nearest-edge rule. This is not sparse gate filtering or meteorological tuning.
"""

from __future__ import annotations

import numpy as np
from scipy.ndimage import median_filter, minimum_filter1d

from rainpulse_algo.performance import observe, timed


@timed("x.candidate")
def nonmet_candidate(fields, echo, snr_good, cfg, range_m):
    shape = echo.shape
    rho = fields["RHOHV"]
    weather = (
        np.asarray(fields["WEATHER_PROTECTED_MASK"], dtype=bool)
        if "WEATHER_PROTECTED_MASK" in fields
        else np.zeros(shape, bool)
    )
    potential = echo & snr_good & np.isfinite(rho) & (rho < cfg.rho_candidate_max) & ~weather
    rows = np.flatnonzero(potential.any(axis=1))
    observe("x.candidate_total_rays", int(shape[0]))
    observe("x.candidate_potential_rays", int(len(rows)))
    if not len(rows):
        observe("x.candidate_skipped_rays", int(shape[0]))
        return np.zeros(shape, bool)
    dr = float(np.median(np.diff(range_m)))
    n = min(501, max(3, int(round(cfg.phase_window_m / dr))))
    n += n % 2 == 0
    # Dense inputs avoid an unnecessary advanced-index copy. Both branches use
    # the original equations, dtypes, smoothing, support and thresholds.
    all_rows = len(rows) * 2 >= shape[0]
    index = slice(None) if all_rows else rows
    supported_echo = echo[index]
    measured = np.where(supported_echo, fields["DBZH"][index], np.nan)
    med = median_filter(np.where(supported_echo, measured, 0.0), size=(1, n), mode="nearest")
    supported = (
        minimum_filter1d(supported_echo.astype(np.uint8), size=n, axis=1, mode="nearest") == 1
    )
    texture = np.abs(measured - med)
    chosen = potential[index] & supported & (texture > cfg.texture_candidate_db)
    observe("x.candidate_filtered_rays", int(shape[0] if all_rows else len(rows)))
    observe("x.candidate_skipped_rays", int(0 if all_rows else shape[0] - len(rows)))
    if all_rows:
        return chosen
    result = np.zeros(shape, bool)
    result[rows] = chosen
    return result
