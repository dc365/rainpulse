"""Cautious additional measurement decisions from independent paper candidates.

V3 remains a reproducible baseline. AFL and RDD both inspect DBZH: agreeing is
not two independent physical evidence families. New rejections need reliable
polarimetric anomalies; withheld uncertainty is counted separately. Scores are
never used to scale reflectivity, manufacture clear air, or fill missing data.
"""

from __future__ import annotations

from enum import IntFlag

import numpy as np
from scipy import ndimage

from .afl import PaperEvidence, afl_evidence, shifted
from .decision import Action, Decision
from .rdd_reference import unavailable_rdd


class LiteratureReason(IntFlag):
    AFL = 1
    AFL_LOCAL = 2
    RDD = 4
    STRUCTURE = 8
    LOW_RHO = 16
    PHASE_VARIATION = 32
    ZDR_OUTLIER = 64
    RELIABLE_POL = 128
    TEMPORAL_SUPPORT = 256
    WEATHER_CONFLICT = 512
    CONFIRMED_ADDITION = 1024
    QUARANTINED_ADDITION = 2048
    WEATHER_PROTECTED = 4096
    INSUFFICIENT_CAPABILITY = 8192


def paper_evidence(native, profile, rdd: PaperEvidence | None = None) -> PaperEvidence:
    afl = afl_evidence(native, profile.literature.afl)
    ref = rdd or unavailable_rdd(native.shape)
    return PaperEvidence(
        {**afl.arrays, **ref.arrays},
        {
            "method": profile.literature.method,
            "afl": afl.metadata,
            "rdd": ref.metadata,
            "decision_policy": "additional_candidates_need_independent_measurement_evidence",
            "operational_eligible": False,
        },
    )


def _long_measured_runs(mask, minimum_gates):
    result = np.zeros(mask.shape, bool)
    for ray, row in enumerate(mask):
        edges = np.diff(np.r_[False, row, False].astype("int8"))
        for lo, hi in zip(np.flatnonzero(edges == 1), np.flatnonzero(edges == -1), strict=True):
            if hi - lo >= minimum_gates:
                result[ray, lo:hi] = True
    return result


def fuse_paper_decision(
    native,
    evidence,
    baseline: Decision,
    papers: PaperEvidence,
    profile,
    *,
    weather_support=None,
    temporal_persistence=None,
    temporal_samples=None,
) -> Decision:
    cfg = profile.literature.fusion
    shape = native.shape
    observed = native.field_available["DBZH"]
    z = native.fields["DBZH"]
    rain = observed & (z >= cfg.minimum_echo_dbz)
    rain &= native.ranges[None, :] >= cfg.minimum_range_m
    data = papers.arrays
    afl = data["AFL_CANDIDATE_MASK"] == 1
    local = data["AFL_LOCAL_CANDIDATE_MASK"] == 1
    rdd = data["RDD_CANDIDATE_MASK"] == 1
    candidate = (afl | local | rdd) & rain
    # Candidate support and bounded connected runs; no closing through absent observations.
    structure = _long_measured_runs(
        candidate, max(1, int(np.ceil(cfg.minimum_segment_m / native.gate_spacing_m)))
    )
    snr_ok = native.field_available.get("SNR", np.zeros(shape, bool))
    snr = native.fields.get("SNR", np.full(shape, np.nan))
    rho_ok = native.field_available.get("RHOHV", np.zeros(shape, bool))
    rho = native.fields.get("RHOHV", np.full(shape, np.nan))
    zdr_ok = native.field_available.get("ZDR", np.zeros(shape, bool))
    zdr = native.fields.get("ZDR", np.full(shape, np.nan))
    raw_count = evidence.arrays["OS_POL_RAW_MOMENT_COUNT"]
    reliable = snr_ok & (snr >= cfg.minimum_snr_db) & (raw_count >= 2)
    low = rho_ok & (rho < cfg.low_rhohv)
    zdr_bad = zdr_ok & ((zdr < cfg.zdr_outlier_db[0]) | (zdr > cfg.zdr_outlier_db[1]))
    phase = native.fields.get("PHIDP", np.full(shape, np.nan))
    phase_ok = native.field_available.get("PHIDP", np.zeros(shape, bool))
    period = profile.geometry.phase_period_deg
    delta = (shifted(phase, 1) - phase + period / 2) % period - period / 2
    pair = phase_ok & shifted(phase_ok, 1, fill=False)
    kernel = np.ones(cfg.phase_window_gates)
    count = ndimage.convolve1d(pair.astype(float), kernel, axis=1, mode="constant")
    bad_count = ndimage.convolve1d(
        (pair & (np.abs(delta) > cfg.phase_pair_jump_deg)).astype(float),
        kernel,
        axis=1,
        mode="constant",
    )
    phase_available = (
        observed
        & phase_ok
        & (count >= np.ceil(cfg.phase_window_gates * cfg.phase_minimum_pair_fraction))
    )
    fraction = np.divide(bad_count, count, out=np.full(shape, np.nan), where=phase_available)
    phase_bad = phase_available & (fraction >= cfg.phase_bad_pair_fraction)
    weather = np.zeros(shape, bool)
    if weather_support is not None:
        if np.shape(weather_support) != shape:
            raise ValueError("literature weather geometry differs")
        weather = np.isfinite(weather_support) & (weather_support >= profile.context.strong_support)
    supported_time = np.zeros(shape, bool)
    # Count discrete independent scans; 2/3 is not approximated by 0.67.
    if temporal_persistence is not None and temporal_samples is not None:
        if np.shape(temporal_persistence) != shape or np.shape(temporal_samples) != shape:
            raise ValueError("literature temporal geometry differs")
        persistence = np.asarray(temporal_persistence)
        samples = np.asarray(temporal_samples)
        if (
            np.any(~np.isfinite(samples))
            or np.any(samples != np.rint(samples))
            or np.any(samples < 0)
            or np.any(samples > 3)
            or np.any(~np.isfinite(persistence[samples > 0]))
            or np.any(persistence[samples > 0] < 0)
            or np.any(persistence[samples > 0] > 1)
        ):
            raise ValueError("invalid literature temporal vote counts")
        votes = persistence * samples
        integer_votes = np.rint(votes)
        supported_time = (
            np.isfinite(votes)
            & (np.abs(votes - integer_votes) <= 1e-5)
            & (np.asarray(temporal_samples) >= cfg.temporal_minimum_samples)
            & (integer_votes >= cfg.temporal_minimum_hits)
            & observed
        )
    # Coherent high correlation alone is not immune, nor sufficient for a rejection:
    # this independent path requires BOTH reliable phase inconsistency and a ZDR anomaly.
    protected = rho_ok & (rho >= cfg.protected_rhohv) & ~phase_bad & ~zdr_bad
    dual_anomaly = phase_bad & zdr_bad
    confirmed = structure & reliable & (dual_anomaly | (low & (phase_bad | zdr_bad)))
    # Mixed weather does not restore a strongly polluted measurement.
    uncertain = (
        structure
        & ~confirmed
        & ~protected
        & ~weather
        & (
            (reliable & (low | phase_bad | zdr_bad))
            | (supported_time & (low | phase_bad | zdr_bad))
        )
    )
    original_reject = baseline.arrays["QC_ACTION"] == Action.REJECT
    original_quarantine = baseline.arrays["RFI_QUARANTINE_MASK"] == 1
    add_reject = confirmed & ~original_reject
    add_quarantine = uncertain & ~original_reject & ~original_quarantine
    reject = original_reject | confirmed
    quarantine = (original_quarantine | uncertain) & ~reject
    arrays = {key: value.copy() for key, value in baseline.arrays.items()}
    flags, quality = baseline.flags.copy(), baseline.quality.copy()
    action = arrays["QC_ACTION"]
    action[quarantine] = Action.DOWNWEIGHT
    action[reject] = Action.REJECT
    quality[quarantine] = np.minimum(quality[quarantine], cfg.quarantine_quality)
    quality[reject] = 0
    flags[confirmed] |= profile.flag_masks["RADIAL_INTERFERENCE"]
    flags[confirmed] |= profile.flag_masks["NON_METEOROLOGICAL"]
    flags[confirmed | quarantine] |= profile.flag_masks["LOW_QUALITY"]
    trusted = observed & ~reject & ~quarantine
    eligible = (arrays["QPE_ELIGIBLE_MASK"] == 1) & trusted
    arrays["REFLECTIVITY_TRUST_MASK"] = trusted.astype("uint8")
    arrays["QPE_ELIGIBLE_MASK"] = eligible.astype("uint8")
    arrays["DBZH_USABLE"] = np.where(eligible, z, np.nan).astype("float32")
    arrays["RFI_QUARANTINE_MASK"] = quarantine.astype("uint8")
    arrays["RFI_RISK_STATE"][confirmed] = 3
    arrays["RFI_RISK_STATE"][quarantine] = 2
    arrays["RFI_MIXED_MASK"] |= (weather & (confirmed | quarantine)).astype("uint8")
    for field in ("RHOHV", "ZDR", "PHIDP", "VR", "SW", "SNR"):
        arrays[field + "_TRUST_MASK"] &= trusted.astype("uint8")
    reason = np.zeros(shape, "uint16")
    for mask, bit in (
        (afl, LiteratureReason.AFL),
        (local, LiteratureReason.AFL_LOCAL),
        (rdd, LiteratureReason.RDD),
        (structure, LiteratureReason.STRUCTURE),
        (low, LiteratureReason.LOW_RHO),
        (phase_bad, LiteratureReason.PHASE_VARIATION),
        (zdr_bad, LiteratureReason.ZDR_OUTLIER),
        (reliable, LiteratureReason.RELIABLE_POL),
        (supported_time, LiteratureReason.TEMPORAL_SUPPORT),
        (weather & (confirmed | uncertain), LiteratureReason.WEATHER_CONFLICT),
        (add_reject, LiteratureReason.CONFIRMED_ADDITION),
        (add_quarantine, LiteratureReason.QUARANTINED_ADDITION),
        (candidate & (protected | weather) & ~confirmed, LiteratureReason.WEATHER_PROTECTED),
        (candidate & ~reliable, LiteratureReason.INSUFFICIENT_CAPABILITY),
    ):
        reason[mask & observed] |= np.uint16(bit)
    arrays.update(data)
    arrays.update(
        {
            "PAPER_CANDIDATE_MASK": candidate.astype("uint8"),
            "PAPER_STRUCTURE_MASK": structure.astype("uint8"),
            "PAPER_CONFIRMED_ADDITION_MASK": add_reject.astype("uint8"),
            "PAPER_QUARANTINED_ADDITION_MASK": add_quarantine.astype("uint8"),
            "PAPER_DECISION_REASON": reason,
            "PAPER_PHASE_BAD_PAIR_FRACTION": fraction.astype("float32"),
            "PAPER_PHASE_AVAILABLE_MASK": phase_available.astype("uint8"),
            "PAPER_TEMPORAL_SUPPORT_MASK": supported_time.astype("uint8"),
            "PAPER_BASELINE_REJECT_MASK": original_reject.astype("uint8"),
            "PAPER_BASELINE_QUARANTINE_MASK": original_quarantine.astype("uint8"),
        }
    )
    return Decision(arrays, flags, quality)
