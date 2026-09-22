# ruff: noqa: E501, I001
"""Display eligibility only; never modifies values, QC flags or weather products."""
from __future__ import annotations

import numpy as np

HARD_REJECT = (
    "MISSING", "HARDWARE_ANOMALY", "RADIAL_INTERFERENCE", "NON_METEOROLOGICAL",
    "GROUND_CLUTTER", "SEA_CLUTTER", "ANOMALOUS_PROPAGATION", "BIOLOGICAL_ECHO",
)


def business_support(values, flags, definitions, *, flag_version, eligible=None):
    values, flags = np.asarray(values), np.asarray(flags)
    if values.shape != flags.shape:
        raise ValueError("display values/flags geometry differs")
    mask = 0
    for name in HARD_REJECT:
        mask |= int(definitions.get(name, 0))
    supported = np.isfinite(values) & ((flags & mask) == 0)
    if flag_version == "qc-flags-v2":
        if eligible is None or np.asarray(eligible).shape != values.shape:
            raise ValueError("v2 QC requires quantitative eligibility on original gates")
        supported &= np.asarray(eligible) == 1
    return supported
