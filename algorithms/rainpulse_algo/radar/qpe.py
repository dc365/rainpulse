from __future__ import annotations

from typing import Any

import numpy as np

from .qpe_profile import QPEProfile


class QPEInputError(ValueError):
    """Raised when a mosaic cannot be converted into a valid QPE field."""


def convert_dbzh_to_rate(
    dbzh: np.ndarray,
    valid_mask: np.ndarray,
    profile: QPEProfile,
) -> tuple[np.ndarray, dict[str, Any]]:
    reflectivity = np.asarray(dbzh)
    valid = np.asarray(valid_mask)
    if reflectivity.ndim != 2 or reflectivity.shape != valid.shape:
        raise QPEInputError("DBZH_QC and VALID_MASK must be equal two-dimensional fields")
    if np.any((valid != 0) & (valid != 1)):
        raise QPEInputError("VALID_MASK must be binary")
    present = valid == 1
    if np.any(~np.isfinite(reflectivity[present])):
        raise QPEInputError("valid QPE inputs must contain finite DBZH_QC")
    if np.any(np.isfinite(reflectivity[~present])):
        raise QPEInputError("missing QPE inputs must contain NaN DBZH_QC")

    rate = np.full(reflectivity.shape, np.nan, dtype="float32")
    no_rain = present & (reflectivity < profile.qpe.no_rain_below_dbz)
    rain = present & ~no_rain
    rate[no_rain] = np.float32(0.0)
    uncapped = np.array([], dtype="float64")
    capped_count = 0
    if np.any(rain):
        source = reflectivity[rain].astype("float64")
        log_rate = (
            (source / 10.0) * np.log(10.0)
            - np.log(profile.qpe.coefficient_a)
        ) / profile.qpe.exponent_b
        uncapped = np.exp(
            np.minimum(log_rate, np.log(np.finfo("float64").max))
        )
        capped_count = int(np.count_nonzero(uncapped > profile.qpe.maximum_rate_mm_h))
        rate[rain] = np.minimum(
            uncapped,
            profile.qpe.maximum_rate_mm_h,
        ).astype("float32")

    valid_rates = rate[present].astype("float64")
    diagnostics = {
        "valid_cell_count": int(np.count_nonzero(present)),
        "missing_cell_count": int(np.count_nonzero(~present)),
        "no_rain_cell_count": int(np.count_nonzero(no_rain)),
        "rain_cell_count": int(np.count_nonzero(rain)),
        "capped_cell_count": capped_count,
        "mean_rate_mm_h": float(np.mean(valid_rates)) if valid_rates.size else 0.0,
        "maximum_observed_rate_mm_h": (
            float(np.max(valid_rates)) if valid_rates.size else 0.0
        ),
        "uncapped_max_rate_mm_h": float(np.max(uncapped)) if uncapped.size else 0.0,
        "p95_rate_mm_h": (
            float(np.percentile(valid_rates, 95)) if valid_rates.size else 0.0
        ),
    }
    return rate, diagnostics
