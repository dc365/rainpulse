"""RAW radial morphology hypothesis; caller supplies source and weather checks."""

import numpy as np


def radial_opening(raw, valid, gate_spacing_m):
    from importlib.metadata import version

    from skimage.morphology import footprint_rectangle, opening

    if version("scikit-image") != "0.26.0":
        raise RuntimeError("radial opening requires frozen scikit-image 0.26.0")
    raw, valid = np.asarray(raw), np.asarray(valid, bool)
    if (
        raw.ndim != 2
        or raw.shape != valid.shape
        or not np.isfinite(gate_spacing_m)
        or gate_spacing_m <= 0
    ):
        raise ValueError("invalid opening geometry")
    length = int(np.ceil(50000 / gate_spacing_m)) | 1
    observed = valid & np.isfinite(raw) & (raw > 35)
    if length > raw.shape[1]:
        return np.zeros(raw.shape, bool)
    return opening(observed, footprint_rectangle((1, length)), mode="constant", cval=0) & observed
