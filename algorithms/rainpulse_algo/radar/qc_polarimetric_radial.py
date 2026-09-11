"""Conservative range/polarimetric evidence for extended radial interference.

Versioned through the QC profile. No missing gate counts as negative weather
support. High-correlation gates remain protected, including a two-gate margin.
This detector never expands seeds into neighbouring azimuths.
"""

from __future__ import annotations

import numpy as np


def polarimetric_extent_masks(
    dbzh: np.ndarray,
    valid: np.ndarray,
    rhohv: np.ndarray,
    ranges_m: np.ndarray,
) -> tuple[np.ndarray, np.ndarray]:
    if rhohv.shape != dbzh.shape or valid.shape != dbzh.shape:
        raise ValueError("polarimetric radial evidence differs from reflectivity shape")
    ranges = np.asarray(ranges_m, dtype="float64")
    if (
        ranges.shape != (dbzh.shape[1],)
        or not np.all(np.isfinite(ranges))
        or np.any(np.diff(ranges) <= 0)
    ):
        raise ValueError("polarimetric radial evidence requires increasing finite gate ranges")
    candidate = np.zeros(dbzh.shape, dtype=bool)
    hard = np.zeros(dbzh.shape, dtype=bool)
    far = ranges >= 100_000.0
    if np.count_nonzero(far) < 2 or np.ptp(ranges[far]) < 250_000.0:
        return candidate, hard
    for ray in range(dbzh.shape[0]):
        observed = valid[ray] & np.isfinite(dbzh[ray])
        echo = observed & far & (dbzh[ray] >= 10.0)
        if np.count_nonzero(echo) < 0.90 * np.count_nonzero(far):
            continue
        # Allow only short dropouts in continuity evidence. Coverage is still
        # measured on actual echoes above; missing gates never enter the output.
        continuity = echo.copy()
        gap_edges = np.diff(np.r_[False, ~echo & far, False].astype("int8"))
        for start, end in zip(
            np.flatnonzero(gap_edges == 1), np.flatnonzero(gap_edges == -1), strict=True
        ):
            if (
                start > 0
                and end < len(ranges)
                and echo[start - 1]
                and echo[end]
                and ranges[end] - ranges[start - 1] <= 1000.0
            ):
                continuity[start:end] = True
        edges = np.diff(np.r_[False, continuity, False].astype("int8"))
        starts, ends = np.flatnonzero(edges == 1), np.flatnonzero(edges == -1)
        if not any(
            ranges[end - 1] - ranges[start] >= 250_000.0
            for start, end in zip(starts, ends, strict=True)
        ):
            continue
        values = dbzh[ray, echo]
        corrected = values - 20.0 * np.log10(ranges[echo])
        if np.percentile(corrected, 75) - np.percentile(corrected, 25) > 8.0:
            continue
        n = max(1, len(values) // 5)
        if np.median(values[-n:]) - np.median(values[:n]) < 3.0:
            continue
        correlation = rhohv[ray]
        available = echo & np.isfinite(correlation) & (correlation >= 0) & (correlation <= 1)
        if np.count_nonzero(available) < 0.90 * np.count_nonzero(echo):
            continue
        if (
            np.median(correlation[available]) > 0.85
            or np.mean(correlation[available] < 0.90) < 0.80
        ):
            continue
        protected = observed & np.isfinite(correlation) & (correlation >= 0.95)
        margin = protected.copy()
        for shift in (1, 2):
            margin[shift:] |= protected[:-shift]
            margin[:-shift] |= protected[shift:]
        candidate[ray] = (
            observed
            & (ranges >= 50_000.0)
            & np.isfinite(correlation)
            & (correlation >= 0)
            & (correlation < 0.90)
            & ~margin
        )
        hard[ray] = candidate[ray]
    return candidate, hard
