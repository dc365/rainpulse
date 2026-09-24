# ruff: noqa: E501, I001
"""Conservative X moment QC and frequency-specific phase-linear attenuation.

Only reliable liquid-path phase is corrected. Unknown propagation is retained as
an uncertain measurement, never fabricated rain/no-rain or an unlimited correction.
"""
from __future__ import annotations

from enum import IntFlag
import copy

import numpy as np
from scipy.ndimage import median_filter, minimum_filter1d

from .model import Station, Sweep, Volume, XProfile


class Flag(IntFlag):
    MISSING = 1
    LOW_SNR = 2
    NONMET_CONFIRMED = 4
    NONMET_CANDIDATE = 8
    ATTENUATION_UNKNOWN = 16
    ATTENUATION_LIMIT = 32
    CALIBRATION_UNKNOWN = 64
    BLOCKED = 128
    PHASE_CORRECTED = 256
    UPSTREAM_CORRECTED = 512
    INCOMPLETE_POLARIMETRY = 1024
    INVALID_MOMENT = 2048


def _mask(fields: dict, name: str, shape: tuple[int, ...], default: bool = False) -> np.ndarray:
    return np.asarray(fields.get(name, np.full(shape, default)), dtype=bool)


def phase_linear(s: Sweep, profile: XProfile, *, anchor_verified: bool, initial_pia_db: float | None) -> tuple[np.ndarray, np.ndarray, np.ndarray]:
    """PIA=alpha*(PhiDP-PhiDP_anchor)+known initial PIA, alpha in dB/degree.

    PhiDP is two-way differential phase: unlike the KDP integral there is no
    additional factor of two. A missing/non-liquid/invalid phase gate breaks the
    propagation path for all gates behind it. No restart at a distant segment.
    """
    shape = s.fields["DBZH"].shape
    pia = np.full(shape, np.nan, dtype=np.float32)
    kdp = np.full(shape, np.nan, dtype=np.float32)
    limited = np.zeros(shape, bool)
    if not anchor_verified or initial_pia_db is None or not np.isfinite(initial_pia_db) or initial_pia_db < 0:
        return pia, kdp, limited
    if "PHIDP" not in s.fields or s.range_m[0] > profile.phase_anchor_max_range_m or len(s.range_m) < 3:
        return pia, kdp, limited
    phase = s.fields["PHIDP"]
    support = (_mask(s.fields, "PHASE_VALID_MASK", shape) & _mask(s.fields, "LIQUID_MASK", shape)
               & _mask(s.fields, "OBSERVED_MASK", shape) & np.isfinite(phase)
               & ~_mask(s.fields, "CONFIRMED_NONMET_MASK", shape)
               & ~_mask(s.fields, "ATTENUATION_UNRELIABLE_MASK", shape))
    if profile.require_snr:
        snr = s.fields.get("SNRH")
        support &= False if snr is None else (np.isfinite(snr) & (snr >= profile.snr_min_db))
    # This simple physical-window implementation refuses nonuniform radial bins.
    dr = np.diff(s.range_m).astype(float)
    if not np.allclose(dr, dr[0], rtol=1e-4, atol=.001):
        raise ValueError("phase baseline requires uniformly spaced range gates; no silent resampling")
    window = max(3, int(round(profile.phase_window_m/dr[0])))
    if window % 2 == 0:
        window += 1
    window = min(window, 501)
    for ray in range(shape[0]):
        false = np.flatnonzero(~support[ray])
        end = int(false[0]) if len(false) else shape[1]
        if end < 3:
            continue
        p = np.rad2deg(np.unwrap(np.deg2rad(phase[ray, :end].astype(float))))
        bad = np.flatnonzero(np.diff(p) < -profile.max_negative_phase_step_deg)
        if len(bad):
            end = int(bad[0]) + 1
            p = p[:end]
        if end < 3:
            continue
        w = min(window, end if end % 2 else end-1)
        # Smoothing acts inside the verified segment only, not across a gap.
        smoothed = median_filter(p, size=w, mode="nearest")
        delta = smoothed - smoothed[0]
        bad_delta = np.flatnonzero(delta < -profile.max_negative_phase_step_deg)
        if len(bad_delta):
            end = int(bad_delta[0])
            smoothed, delta = smoothed[:end], delta[:end]
        correction = initial_pia_db + float(profile.alpha_db_per_degree) * np.maximum(delta, 0)
        too_large = np.flatnonzero(correction > profile.max_pia_db)
        if len(too_large):
            cut = int(too_large[0])
            limited[ray, cut:] = True
            end = cut
        pia[ray, :end] = correction[:end]
        # KDP is a diagnostic derivative of smoothed phase, never a substitute
        # for a missing reliable path and not an X rainfall retrieval in v1.
        if end >= 3:
            kdp[ray, :end] = (0.5*np.gradient(smoothed[:end], s.range_m[:end]/1000)).astype(np.float32)
    return pia, kdp, limited


def x_qc(volume: Volume, station: Station, release_sha256: str) -> Volume:
    volume.validate(station)
    if station.band != "X":
        raise ValueError("X QC cannot be applied to an S observation")
    cfg = station.x_qc
    upstream = volume.metadata.get("attenuation_status", "unknown")
    if upstream not in {"raw", "corrected", "unknown"}:
        raise ValueError("unknown upstream correction provenance")
    if cfg.attenuation == "phidp_linear" and upstream != "raw":
        raise ValueError("refusing double or unknown upstream attenuation correction")
    if cfg.attenuation == "upstream_verified" and upstream != "corrected":
        raise ValueError("upstream-corrected mode requires explicit observed correction provenance")
    result = Volume(copy.deepcopy(volume.metadata), [])
    for sweep in volume.sweeps:
        f = {k: np.array(v, copy=True) for k, v in sweep.fields.items()}
        shape = f["DBZH"].shape
        flags = np.zeros(shape, np.uint16)
        observed = f["OBSERVED_MASK"].astype(bool)
        noecho = f["NO_ECHO_MASK"].astype(bool)
        echo = observed & ~noecho & np.isfinite(f["DBZH"])
        invalid = echo & ((f["DBZH"] < -50) | (f["DBZH"] > 100))
        flags[~observed] |= int(Flag.MISSING)
        flags[invalid] |= int(Flag.INVALID_MOMENT)
        confirmed = _mask(f, "CONFIRMED_NONMET_MASK", shape)
        flags[confirmed & observed] |= int(Flag.NONMET_CONFIRMED)
        base = observed & ~invalid & ~confirmed
        snr = f.get("SNRH")
        if snr is None:
            snr_good = np.full(shape, not cfg.require_snr)
            flags[observed] |= int(Flag.INCOMPLETE_POLARIMETRY)
        else:
            snr_good = np.isfinite(snr) & (snr >= cfg.snr_min_db)
            # A verified valid-no-echo observation is not invalid merely because
            # echo SNR is below detection. It still needs a reliable clear path.
            snr_good |= noecho
        flags[observed & ~snr_good] |= int(Flag.LOW_SNR)
        blockage = f.get("BLOCKAGE_FRACTION")
        blocked = np.zeros(shape, bool)
        if blockage is not None:
            if np.any(np.isfinite(blockage) & ((blockage < 0) | (blockage > 1))):
                raise ValueError("invalid blockage fraction")
            blocked = ~np.isfinite(blockage) | (blockage >= cfg.max_blockage_fraction)
            flags[observed & blocked] |= int(Flag.BLOCKED)
        pia = np.full(shape, np.nan, np.float32)
        limited = np.zeros(shape, bool)
        if cfg.attenuation == "phidp_linear":
            pia, kdp, limited = phase_linear(
                sweep, cfg, anchor_verified=volume.metadata.get("phase_anchor_verified") is True,
                initial_pia_db=volume.metadata.get("pia_at_first_gate_db"))
            f["KDP_EST"] = kdp
            flags[echo & np.isfinite(pia)] |= int(Flag.PHASE_CORRECTED)
            corrected = f["DBZH"].astype(np.float32) + pia
            propagation_good = np.isfinite(pia)
        elif cfg.attenuation == "upstream_verified":
            propagation_good = _mask(f, "ATTENUATION_VALID_MASK", shape)
            corrected = f["DBZH"].astype(np.float32).copy()
            flags[observed & propagation_good] |= int(Flag.UPSTREAM_CORRECTED)
            # Unknown correction magnitude stays NaN, not a fabricated 0 dB.
            if "PIA_DB" in f:
                pia = f["PIA_DB"].astype(np.float32).copy()
                if np.any(np.isfinite(pia) & (pia < 0)):
                    raise ValueError("upstream PIA cannot be negative")
                limited = np.isfinite(pia) & (pia > cfg.max_pia_db)
        else:
            corrected = f["DBZH"].astype(np.float32).copy()
            propagation_good = np.zeros(shape, bool)
        propagation_good &= ~_mask(f, "ATTENUATION_UNRELIABLE_MASK", shape) & ~limited
        flags[observed & ~propagation_good] |= int(Flag.ATTENUATION_UNKNOWN)
        flags[observed & limited] |= int(Flag.ATTENUATION_LIMIT)
        # Independent low-rho + high-texture evidence nominates uncertain
        # contamination. Low rho alone (e.g. hail) never hard-deletes weather.
        candidate = np.zeros(shape, bool)
        rho = f.get("RHOHV")
        if rho is not None and shape[1] >= 3:
            dr = float(np.median(np.diff(sweep.range_m)))
            n = min(501, max(3, int(round(cfg.phase_window_m/dr))))
            n += n % 2 == 0
            measured = np.where(echo, f["DBZH"], np.nan)
            # Finite support around the target is required; NaN is not zero.
            med = median_filter(np.where(echo, measured, 0.0), size=(1, n), mode="nearest")
            supported = minimum_filter1d(echo.astype(np.uint8), size=n, axis=1, mode="nearest") == 1
            texture = np.abs(measured-med)
            candidate = (echo & supported & snr_good & np.isfinite(rho)
                         & (rho < cfg.rho_candidate_max) & (texture > cfg.texture_candidate_db)
                         & ~_mask(f, "WEATHER_PROTECTED_MASK", shape))
        else:
            flags[echo] |= int(Flag.INCOMPLETE_POLARIMETRY)
        flags[candidate] |= int(Flag.NONMET_CANDIDATE)
        calibrated = station.calibration_verified and volume.metadata.get("calibration_id") == station.calibration_id
        if not calibrated:
            flags[observed] |= int(Flag.CALIBRATION_UNKNOWN)
        eligible = base & snr_good & ~blocked & propagation_good & ~candidate & calibrated
        eligible &= noecho | np.isfinite(corrected)
        score = np.where(eligible, station.quality_scale, 0.0).astype(np.float32)
        if blockage is not None:
            score *= 1-np.nan_to_num(blockage, nan=1)
        if rho is None:
            score *= .8
        f.update(DBZH_RAW=f["DBZH"].astype(np.float32).copy(),
                 DBZH_QC=np.where(echo & np.isfinite(corrected), corrected, np.where(echo, f["DBZH"], np.nan)).astype(np.float32),
                 PIA_DB=pia, MB_QC_FLAGS=flags, REFLECTIVITY_ELIGIBLE_FOR_CR=eligible.astype(np.uint8),
                 CR_UNCERTAIN_MASK=(observed & ~eligible & ~confirmed).astype(np.uint8),
                 QUALITY_SCORE=score, QPE_ELIGIBLE_MASK=np.zeros(shape, np.uint8))
        result.sweeps.append(Sweep(sweep.number, sweep.azimuth_deg.copy(), sweep.range_m.copy(),
                                   sweep.elevation_deg.copy(), sweep.ray_time_epoch.copy(), f))
    result.metadata.update(processing="x-moment-qc-v1", network_sha256=release_sha256,
                           quality_semantics="candidate-heuristic-not-probability-v1",
                           dbzh_raw_semantics="unchanged_input_moment_may_be_vendor_corrected", operational_eligible=False,
                           qpe_enabled=False, attenuation_method=cfg.attenuation)
    return result


def accept_s_qc(volume: Volume, station: Station, network_sha256: str) -> Volume:
    """Translate an already-QC S volume without changing its reflectivity/masks."""
    volume.validate(station)
    if station.band != "S" or volume.metadata.get("qc_pipeline_version") not in station.allowed_s_qc_versions:
        raise ValueError("S QC version is not approved in the selected network release")
    result = Volume(copy.deepcopy(volume.metadata), [])
    for s in volume.sweeps:
        f = {k: np.array(v, copy=True) for k, v in s.fields.items()}
        for key in ("DBZH_QC", "REFLECTIVITY_ELIGIBLE_FOR_CR", "QUALITY_INDEX"):
            if key not in f:
                raise ValueError("S QC must supply explicit CR eligibility and quality, not raw DBZH")
        if not np.all(np.isin(f["REFLECTIVITY_ELIGIBLE_FOR_CR"], (0, 1))):
            raise ValueError("invalid S CR eligibility")
        quality = f["QUALITY_INDEX"]
        if np.any(np.isfinite(quality) & ((quality < 0) | (quality > 1))):
            raise ValueError("invalid S quality index")
        eligible = f["REFLECTIVITY_ELIGIBLE_FOR_CR"].astype(bool) & (f["OBSERVED_MASK"] == 1)
        for name in ("CONFIRMED_NONMET_MASK", "CR_WITHHELD_MASK"):
            if name in f:
                eligible &= f[name] == 0
        f["REFLECTIVITY_ELIGIBLE_FOR_CR"] = eligible.astype(np.uint8)
        # This is an explicit network heuristic, not an assertion that legacy
        # S QI is probabilistically comparable to an X classifier's probability.
        f["QUALITY_SCORE"] = np.where(eligible & np.isfinite(quality), quality * station.quality_scale, 0).astype(np.float32)
        result.sweeps.append(Sweep(s.number, s.azimuth_deg.copy(), s.range_m.copy(), s.elevation_deg.copy(), s.ray_time_epoch.copy(), f))
    result.metadata.update(network_sha256=network_sha256, quality_semantics="candidate-heuristic-not-probability-v1", operational_eligible=False)
    return result
