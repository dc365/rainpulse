"""V5 capability routing, bounded paper segments and separate rejection/withholding."""

from __future__ import annotations

from enum import IntFlag

import numpy as np
from scipy import ndimage

from .afl import shifted
from .decision import Action, Decision
from .segments import associated_support


class Reason(IntFlag):
    RANGE_SIGNATURE = 1
    UNVERIFIED_PLATEAU = 2
    VERIFIED_CEILING = 4
    PAPER_SEGMENT = 8
    RELIABLE_POL = 16
    POL_CORROBORATED = 32
    SNR_UNAVAILABLE = 64
    POL_INCOMPLETE = 128
    WEATHER_CONFLICT = 256
    BASELINE_REJECT = 512
    BASELINE_QUARANTINE = 1024
    CONFIRMED_ADDITION = 2048
    QUARANTINED_ADDITION = 4096
    PAPER_LINKED_OBSERVATION = 8192
    RESEARCH_SINGLE_FIELD_CONFIRMATION = 16384


def measurement_capability(native, cfg):
    observed = native.field_available["DBZH"]
    empty = np.zeros(native.shape, bool)
    count = sum(
        native.field_available.get(k, empty).astype("uint8") for k in ("RHOHV", "ZDR", "PHIDP")
    )
    snr_available = native.field_available.get("SNR", empty) & observed
    snr = native.fields.get("SNR", np.full(native.shape, np.nan))
    reliable = snr_available & (snr >= cfg.minimum_snr_db) & (count >= 2)
    code = np.zeros(native.shape, "uint8")
    code[observed] = 1
    code[observed & (count > 0)] = 2
    code[reliable] = 3
    return code, count, snr_available, reliable


def pol_corroboration(native, profile):
    cfg = profile.literature.fusion
    available = native.field_available
    empty = np.zeros(native.shape, bool)
    rho = native.fields.get("RHOHV", np.full(native.shape, np.nan))
    low = available.get("RHOHV", empty) & (rho < cfg.low_rhohv)
    zdr = native.fields.get("ZDR", np.full(native.shape, np.nan))
    zdr_bad = available.get("ZDR", empty) & (
        (zdr < cfg.zdr_outlier_db[0]) | (zdr > cfg.zdr_outlier_db[1])
    )
    phase = native.fields.get("PHIDP", np.full(native.shape, np.nan))
    ok = available.get("PHIDP", empty)
    period = profile.geometry.phase_period_deg
    delta = (shifted(phase, 1) - phase + period / 2) % period - period / 2
    pairs = ok & shifted(ok, 1, fill=False)
    count = ndimage.convolve1d(
        pairs.astype(float), np.ones(cfg.phase_window_gates), axis=1, mode="constant"
    )
    jumps = ndimage.convolve1d(
        (pairs & (np.abs(delta) > cfg.phase_pair_jump_deg)).astype(float),
        np.ones(cfg.phase_window_gates),
        axis=1,
        mode="constant",
    )
    supported = ok & (count >= np.ceil(cfg.phase_window_gates * cfg.phase_minimum_pair_fraction))
    phase_bad = supported & (jumps >= cfg.phase_bad_pair_fraction * count)
    # All of these are polarimetric evidence, not three independent instruments.
    combined = (phase_bad & zdr_bad) | (low & (phase_bad | zdr_bad))
    return combined, phase_bad, zdr_bad


def fuse_crossradar(native, baseline: Decision, signatures, profile, *, weather_support=None):
    cfg = profile.cross_radar
    observed, z = native.field_available["DBZH"], native.fields["DBZH"]
    code, count, snr_ok, reliable = measurement_capability(native, cfg)
    pol_bad, phase_bad, zdr_bad = pol_corroboration(native, profile)
    pol_bad &= reliable & observed
    weather = np.zeros(native.shape, bool)
    if weather_support is not None:
        if np.shape(weather_support) != native.shape:
            raise ValueError("V5 weather context geometry mismatch")
        weather = np.isfinite(weather_support) & (weather_support >= profile.context.strong_support)
    original = baseline.arrays
    range_candidate = signatures.arrays["V5_RANGE_CANDIDATE_MASK"] == 1
    modes = signatures.arrays["V5_RANGE_MODEL_CODE"]
    paper_candidate = (original["PAPER_CANDIDATE_MASK"] == 1) & observed
    # A weather-like measured gap is never bridged, nor is a long missing interval.
    rho = native.fields.get("RHOHV", np.full(native.shape, np.nan))
    healthy = reliable & (rho >= profile.literature.fusion.protected_rhohv) & ~phase_bad & ~zdr_bad
    bridgeable = ~observed | ((z >= profile.literature.fusion.minimum_echo_dbz) & ~healthy)
    paper_structure, paper_linked = associated_support(
        paper_candidate,
        observed,
        bridgeable,
        native.gate_spacing_m,
        profile.literature.fusion.minimum_segment_m,
        cfg.paper_gap_m,
        cfg.paper_gap_fraction,
    )
    domain = range_candidate | paper_structure | paper_linked
    confirmed = (range_candidate | paper_structure) & pol_bad
    # Never convert an unverified numerical plateau into confirmation, even with pol support.
    confirmed &= modes != 3
    research_single = np.zeros(native.shape, bool)
    if cfg.single_field_action == "research_reject":
        # Explicitly receipt-bound experiment. Unverified plateau remains withheld.
        research_single = range_candidate & (modes != 3) & ~weather
        confirmed |= research_single
    # A long, well-fitted signal hypothesis can withhold a high-RHOHV measurement;
    # it cannot claim "confirmed RFI" from geometry alone. Missing pol does not disable it.
    uncertain = range_candidate | (paper_linked & pol_bad)
    uncertain &= ~confirmed
    old_reject = original["QC_ACTION"] == Action.REJECT
    old_quarantine = original["RFI_QUARANTINE_MASK"] == 1
    add_reject = confirmed & ~old_reject
    add_quarantine = uncertain & ~old_reject & ~old_quarantine
    reject = old_reject | confirmed
    quarantine = (old_quarantine | uncertain) & ~reject
    arrays = {k: v.copy() for k, v in original.items()}
    flags, quality = baseline.flags.copy(), baseline.quality.copy()
    arrays["QC_ACTION"][reject] = Action.REJECT
    arrays["QC_ACTION"][quarantine] = Action.DOWNWEIGHT
    quality[reject] = 0
    quality[quarantine] = np.minimum(quality[quarantine], cfg.quarantine_quality)
    flags[confirmed] |= (
        profile.flag_masks["RADIAL_INTERFERENCE"] | profile.flag_masks["NON_METEOROLOGICAL"]
    )
    flags[confirmed | quarantine] |= profile.flag_masks["LOW_QUALITY"]
    trusted = observed & ~reject & ~quarantine
    eligible = (arrays["QPE_ELIGIBLE_MASK"] == 1) & trusted
    arrays["REFLECTIVITY_TRUST_MASK"] = trusted.astype("uint8")
    arrays["QPE_ELIGIBLE_MASK"] = eligible.astype("uint8")
    arrays["DBZH_USABLE"] = np.where(eligible, z, np.nan).astype("float32")
    arrays["RFI_QUARANTINE_MASK"] = quarantine.astype("uint8")
    arrays["RFI_RISK_STATE"][confirmed] = 3
    arrays["RFI_RISK_STATE"][quarantine] = 2
    arrays["RFI_MIXED_MASK"] |= (weather & (confirmed | uncertain)).astype("uint8")
    for field in ("RHOHV", "ZDR", "PHIDP", "VR", "SW", "SNR"):
        arrays[field + "_TRUST_MASK"] &= trusted.astype("uint8")
    reason = np.zeros(native.shape, "uint32")
    for mask, bit in (
        (range_candidate, Reason.RANGE_SIGNATURE),
        (modes == 3, Reason.UNVERIFIED_PLATEAU),
        (modes == 2, Reason.VERIFIED_CEILING),
        (paper_structure, Reason.PAPER_SEGMENT),
        (reliable, Reason.RELIABLE_POL),
        (pol_bad, Reason.POL_CORROBORATED),
        (~snr_ok, Reason.SNR_UNAVAILABLE),
        (count < 2, Reason.POL_INCOMPLETE),
        (weather & domain, Reason.WEATHER_CONFLICT),
        (old_reject, Reason.BASELINE_REJECT),
        (old_quarantine, Reason.BASELINE_QUARANTINE),
        (add_reject, Reason.CONFIRMED_ADDITION),
        (add_quarantine, Reason.QUARANTINED_ADDITION),
        (paper_linked, Reason.PAPER_LINKED_OBSERVATION),
        (research_single, Reason.RESEARCH_SINGLE_FIELD_CONFIRMATION),
    ):
        reason[mask & observed] |= np.uint32(bit)
    arrays.update(signatures.arrays)
    arrays.update(
        {
            "V5_CAPABILITY_CODE": code,
            "V5_RAW_POL_COUNT": np.where(observed, count, 0).astype("uint8"),
            "V5_SNR_AVAILABLE_MASK": snr_ok.astype("uint8"),
            "V5_RELIABLE_POL_MASK": reliable.astype("uint8"),
            "V5_POL_CORROBORATED_MASK": pol_bad.astype("uint8"),
            "V5_PAPER_STRUCTURE_MASK": paper_structure.astype("uint8"),
            "V5_PAPER_LINKED_MASK": paper_linked.astype("uint8"),
            "V5_CANDIDATE_MASK": domain.astype("uint8"),
            "V5_WEATHER_CONFLICT_MASK": (weather & domain).astype("uint8"),
            "V5_BASELINE_REJECT_MASK": old_reject.astype("uint8"),
            "V5_BASELINE_QUARANTINE_MASK": old_quarantine.astype("uint8"),
            "V5_BASELINE_ELIGIBLE_MASK": original["QPE_ELIGIBLE_MASK"].copy(),
            "V5_CONFIRMED_ADDITION_MASK": add_reject.astype("uint8"),
            "V5_QUARANTINED_ADDITION_MASK": add_quarantine.astype("uint8"),
            "V5_DECISION_REASON": reason,
        }
    )
    return Decision(arrays, flags, quality)


def sweep_funnel(native, arrays, cfg):
    """Counts on a fixed observed domain, by true distance and capability, not skill."""
    observed = native.field_available["DBZH"]
    capability = arrays["V5_CAPABILITY_CODE"]
    bands = list(cfg.distance_bands_m)
    if bands[-1] <= native.ranges[-1]:
        bands.append(float(native.ranges[-1] + native.gate_spacing_m))
    rows = []
    for lo, hi in zip(bands[:-1], bands[1:], strict=True):
        domain = observed & (native.ranges[None, :] >= lo) & (native.ranges[None, :] < hi)
        for cap in (1, 2, 3):
            chosen = domain & (capability == cap)
            if not chosen.any():
                continue
            row = dict(range_m=[lo, hi], capability_code=cap, observed_gates=int(chosen.sum()))
            for key in (
                "V5_RANGE_CANDIDATE_MASK",
                "PAPER_CANDIDATE_MASK",
                "V5_PAPER_STRUCTURE_MASK",
                "V5_RELIABLE_POL_MASK",
                "V5_POL_CORROBORATED_MASK",
                "V5_WEATHER_CONFLICT_MASK",
                "V5_CONFIRMED_ADDITION_MASK",
                "V5_QUARANTINED_ADDITION_MASK",
                "V5_BASELINE_REJECT_MASK",
                "V5_BASELINE_QUARANTINE_MASK",
                "RFI_QUARANTINE_MASK",
                "QPE_ELIGIBLE_MASK",
            ):
                row[key] = int((chosen & (arrays[key] == 1)).sum())
            row["final_reject_gates"] = int((chosen & (arrays["QC_ACTION"] == Action.REJECT)).sum())
            row["candidate_still_eligible_gates"] = int(
                (
                    chosen & (arrays["V5_CANDIDATE_MASK"] == 1) & (arrays["QPE_ELIGIBLE_MASK"] == 1)
                ).sum()
            )
            rows.append(row)
    return dict(
        schema_version="rainpulse.qc-crossradar-funnel.v1",
        interpretation="diagnostic_counts_not_skill",
        radar_id=native.attrs.get("radar_id"),
        sweep=native.name,
        rows=rows,
    )
