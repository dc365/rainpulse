"""Published wradlib beam blockage with explicit missing-upstream propagation."""

from __future__ import annotations

import warnings

import numpy as np

from .algorithms import require_libraries


def wradlib_blockage(terrain_height, beam_height, beam_radius, supported):
    require_libraries("2.2.5", "2.9.5")
    import wradlib

    terrain, beam, radius, observed = np.broadcast_arrays(
        np.asarray(terrain_height, dtype="float64"),
        np.asarray(beam_height, dtype="float64"),
        np.asarray(beam_radius, dtype="float64"),
        np.asarray(supported, dtype=bool),
    )
    if terrain.ndim != 2:
        raise ValueError("blockage requires ray x range arrays")
    available = (
        observed & np.isfinite(terrain) & np.isfinite(beam) & np.isfinite(radius) & (radius > 0)
    )
    with warnings.catch_warnings():
        warnings.simplefilter("ignore", RuntimeWarning)
        partial = np.asarray(wradlib.qual.beam_block_frac(terrain, beam, radius), dtype="float32")
    partial[~available] = np.nan
    # The library's missing-value convention is not a license to see through a
    # missing upstream DEM cell. Fill only this private cumulative working copy.
    cumulative = np.asarray(
        wradlib.qual.cum_beam_block_frac(np.where(available, partial, 0.0)), dtype="float32"
    )
    missing_upstream = np.cumsum(observed & ~available, axis=1) > 0
    cumulative[~observed | missing_upstream] = np.nan
    if np.any(np.isfinite(cumulative) & ((cumulative < 0) | (cumulative > 1))):
        raise ValueError("wradlib returned out-of-range blockage")
    return partial, cumulative
