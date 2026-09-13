"""V3 raw-moment evidence. Scores are diagnostics, never calibrated probabilities.

Circular second differences distinguish a phase branch cut or a smooth rain
phase ramp from oscillation. No cross-gap interpolation, velocity proxy, guessed
SNR, or fabricated receiver signal is used. Distinct polarimetric features are
corroborating measurements within ONE family, not independent library votes.
"""

from __future__ import annotations

from enum import IntFlag

import numpy as np
from scipy import ndimage

from .adapters import NativeSweep
from .profile import OpenSourceQCProfile


class PolarEvidence(IntFlag):
    LOW_RHO = 1
    SEVERE_RHO = 2
    PHASE_IRREGULAR = 4
    ZDR_OUTLIER = 8
    RELIABLE_SNR = 16
    JOINT_PHASE_ZDR = 32
    WEATHER_BARRIER = 64


def circular_delta(value, period):
    return (value + period / 2) % period - period / 2


def _local_fraction(mask, available, window):
    count = (
        ndimage.uniform_filter1d(available.astype("float64"), window, axis=1, mode="constant")
        * window
    )
    total = (
        ndimage.uniform_filter1d(
            (mask & available).astype("float64"), window, axis=1, mode="constant"
        )
        * window
    )
    value = np.divide(total, count, out=np.full(count.shape, np.nan), where=count > 0)
    return np.clip(value, 0, 1), count


def joint_moment_evidence(native: NativeSweep, profile: OpenSourceQCProfile):
    cfg, obj = profile.rfi_refinement, profile.rfi_objects
    if cfg is None or obj is None:
        raise ValueError("joint moment evidence requires the explicit V3 profile")
    shape = native.shape
    observed = native.field_available["DBZH"]
    empty = np.zeros(shape, bool)
    absent = np.full(shape, np.nan, dtype="float32")
    rho = native.fields.get("RHOHV", absent)
    rho_ok = native.field_available.get("RHOHV", empty)
    snr_ok = native.field_available.get("SNR", empty)
    snr = native.fields.get("SNR", absent)
    snr_reliable = observed & snr_ok & (snr >= obj.minimum_polarimetric_snr_db)
    phase = native.fields.get("PHIDP", absent).astype("float64")
    phase_ok = observed & native.field_available.get("PHIDP", empty)
    lag = max(1, int(round(cfg.phase_lag_m / native.gate_spacing_m)))
    window = max(3, int(round(cfg.phase_window_m / native.gate_spacing_m)) | 1)
    curvature = np.full(shape, np.nan, "float32")
    pair_ok = np.zeros(shape, bool)
    if 2 * lag < shape[1]:
        pair_ok = native.support(phase_ok, 0, lag)
        period = profile.geometry.phase_period_deg
        left = circular_delta(phase[:, lag:-lag] - phase[:, : -2 * lag], period)
        right = circular_delta(phase[:, 2 * lag :] - phase[:, lag:-lag], period)
        # Express at the configured physical lag, allowing for gate rounding.
        scaling = (cfg.phase_lag_m / (lag * native.gate_spacing_m)) ** 2
        curvature[:, lag:-lag] = np.abs(circular_delta(right - left, period)) * scaling
        curvature[~pair_ok] = np.nan
    fraction, count = _local_fraction(curvature >= cfg.phase_curvature_deg, pair_ok, window)
    phase_supported = native.support(phase_ok, 0, window // 2)
    phase_supported &= count >= cfg.phase_minimum_pairs
    phase_supported &= count / window >= cfg.phase_minimum_support
    # Missing and range edges do not imply smooth phase.
    fraction[~phase_supported] = np.nan
    phase_anomaly = phase_supported & (fraction >= cfg.phase_noise_fraction)
    zdr = native.fields.get("ZDR", absent)
    zdr_ok = observed & native.field_available.get("ZDR", empty)
    lower, upper = cfg.zdr_plausible_range_db
    zdr_outside = zdr_ok & ((zdr < lower) | (zdr > upper))
    zdr_fraction, zdr_count = _local_fraction(zdr_outside, zdr_ok, window)
    zdr_supported = native.support(zdr_ok, 0, window // 2)
    zdr_supported &= zdr_count / window >= cfg.phase_minimum_support
    zdr_fraction[~zdr_supported] = np.nan
    # At least the actual current gate must corroborate its neighbourhood.
    zdr_anomaly = zdr_outside & zdr_supported & (zdr_fraction >= cfg.zdr_anomaly_fraction)
    severe = observed & rho_ok & (rho < obj.severe_rhohv)
    suspect = observed & rho_ok & (rho < obj.suspect_rhohv)
    raw_count = sum(
        native.field_available.get(field, empty).astype("uint8")
        for field in ("RHOHV", "ZDR", "PHIDP")
    )
    joint = phase_anomaly & zdr_anomaly
    enough = raw_count >= obj.minimum_raw_pol_moments
    # A high correlation value is not an unconditional weather veto. This barrier
    # additionally requires a reliable, locally regular phase and plausible ZDR.
    barrier = observed & rho_ok & (rho >= obj.protected_rhohv) & snr_reliable
    barrier &= phase_supported & ~phase_anomaly & zdr_ok & ~zdr_outside
    strong = observed & enough & (severe | joint)
    anomaly = observed & (suspect | phase_anomaly | zdr_anomaly)
    bits = np.zeros(shape, "uint16")
    for mask, code in (
        (suspect, PolarEvidence.LOW_RHO),
        (severe, PolarEvidence.SEVERE_RHO),
        (phase_anomaly, PolarEvidence.PHASE_IRREGULAR),
        (zdr_anomaly, PolarEvidence.ZDR_OUTLIER),
        (snr_reliable, PolarEvidence.RELIABLE_SNR),
        (joint, PolarEvidence.JOINT_PHASE_ZDR),
        (barrier, PolarEvidence.WEATHER_BARRIER),
    ):
        bits[mask] |= np.uint16(code)
    return {
        "RFI_PHASE_CURVATURE_DEG": curvature,
        "RFI_PHASE_PAIR_AVAILABLE_MASK": pair_ok.astype("uint8"),
        "RFI_PHASE_LOCAL_AVAILABLE_MASK": phase_supported.astype("uint8"),
        "RFI_PHASE_NOISE_FRACTION": fraction.astype("float32"),
        "RFI_PHASE_ANOMALY_MASK": phase_anomaly.astype("uint8"),
        "RFI_ZDR_LOCAL_AVAILABLE_MASK": zdr_supported.astype("uint8"),
        "RFI_ZDR_OUTLIER_FRACTION": zdr_fraction.astype("float32"),
        "RFI_ZDR_ANOMALY_MASK": zdr_anomaly.astype("uint8"),
        "RFI_SNR_RELIABLE_MASK": snr_reliable.astype("uint8"),
        "RFI_POLARIMETRIC_ANOMALY_MASK": anomaly.astype("uint8"),
        "RFI_POLARIMETRIC_STRONG_MASK": strong.astype("uint8"),
        "RFI_WEATHER_BARRIER_MASK": barrier.astype("uint8"),
        "RFI_V3_EVIDENCE_BITS": bits,
    }
