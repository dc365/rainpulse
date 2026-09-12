"""Opt-in local radial evidence; never a station/azimuth deletion list."""

from __future__ import annotations

import numpy as np
from scipy import ndimage

from ..qc_geometry import nearest_azimuth_matches
from .adapters import NativeSweep
from .profile import RFIConfig


def local_radial_candidates(native: NativeSweep, config: RFIConfig) -> tuple[np.ndarray, dict]:
    candidate = np.zeros(native.shape, bool)
    if not config.enabled:
        return candidate, {"status": "disabled", "method": "local-radial-v1"}
    values = native.fields["DBZH"]
    observed = native.field_available["DBZH"]
    width = max(3, int(round(config.local_window_m / native.gate_spacing_m)) | 1)
    if width > native.shape[1]:
        return candidate, {"status": "unavailable", "reason": "short_range_axis"}
    weights = ndimage.uniform_filter1d(observed.astype(float), width, axis=1, mode="constant")
    # Stable distance-corrected shape is auxiliary; contrast alone is not a final rejection.
    corrected = (
        values - 20 * np.log10(np.maximum(native.ranges, native.gate_spacing_m / 2))[None, :]
    )
    working = np.where(observed, corrected, 0.0)
    first = ndimage.uniform_filter1d(working, width, axis=1, mode="constant")
    second = ndimage.uniform_filter1d(working * working, width, axis=1, mode="constant")
    mean = np.divide(first, weights, out=np.zeros_like(first), where=weights > 0)
    variance = np.divide(second, weights, out=np.zeros_like(second), where=weights > 0) - mean**2
    stable = (weights >= config.minimum_measured_support) & (
        np.sqrt(np.maximum(variance, 0)) <= config.maximum_axial_std_db
    )
    background_contrast = np.zeros(native.shape, bool)
    spacing = native.audit["azimuth_spacing_deg"]
    for offset in config.azimuth_offsets_deg:
        left, delta_l, ok_l = nearest_azimuth_matches(
            (native.azimuth - offset) % 360, native.azimuth
        )
        right, delta_r, ok_r = nearest_azimuth_matches(
            (native.azimuth + offset) % 360, native.azimuth
        )
        comparable = ok_l & ok_r & (delta_l <= spacing * 0.55) & (delta_r <= spacing * 0.55)
        comparable &= (left != np.arange(native.shape[0])) & (right != np.arange(native.shape[0]))
        # Both sides must be actual quieter observations; a missing side is not clear air.
        valid = observed & observed[left] & observed[right] & comparable[:, None]
        background_contrast |= valid & (
            values - np.maximum(values[left], values[right]) >= config.minimum_contrast_db
        )
    raw = observed & stable & background_contrast & (values >= config.minimum_echo_dbz)
    for ray in range(native.shape[0]):
        edges = np.diff(np.r_[False, raw[ray], False].astype("int8"))
        for start, end in zip(np.flatnonzero(edges == 1), np.flatnonzero(edges == -1), strict=True):
            if (end - start) * native.gate_spacing_m >= config.minimum_segment_m:
                candidate[ray, start:end] = True
    return candidate, {
        "status": "applied",
        "method": "local-radial-v1",
        "role": "experimental_candidate_not_standalone_reject",
        "parameters": config.model_dump(mode="json"),
        "window_gates": width,
        "candidate_gates": int(candidate.sum()),
    }
