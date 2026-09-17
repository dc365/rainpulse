"""Experimental wide-sector source model. Raw-only, target/guard blocks held out.

A range law and stationary receiver-scale distribution describe a source hypothesis,
not independent votes or a calibrated RFI probability. The caller owns disposition.
"""

import warnings
from enum import IntFlag
from typing import Literal

import numpy as np
from pydantic import BaseModel, ConfigDict, Field

from .distance_polar import distance_polar_reference


class BroadSourceConfig(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True, allow_inf_nan=False)
    mode: Literal["audit", "experiment_quarantine"] = "audit"
    shared_range_term: bool = False
    observed_range: bool = False
    distance_polar_reference: bool = False
    radial_opening: bool = False
    source_edge: bool = False
    near_range_targets: bool = False
    near_sector_consensus: bool = False
    block_m: float = Field(default=50000, gt=0)
    minimum_range_m: float = Field(default=50000, gt=0)
    maximum_range_m: float = Field(default=450000, gt=50000)
    minimum_blocks: int = Field(default=5, ge=5)
    minimum_block_span_m: float = Field(default=7500, gt=0)
    minimum_reference_span_m: float = Field(default=200000, ge=200000)
    range_residual_db: float = Field(default=2.5, gt=0, le=3.5)
    snr_stationarity_db: float = Field(default=2, gt=0, le=3)
    minimum_snr_db: float = Field(default=20, ge=8)
    minimum_polar_samples: int = Field(default=100, ge=100)
    maximum_phase_p90_deg: float = Field(default=10, gt=0, le=10)
    maximum_zdr_p90_db: float = Field(default=1, gt=0, le=1)
    maximum_neighbour_angle_deg: float = Field(default=5, gt=0, le=10)
    minimum_angular_span_deg: float = Field(default=3, gt=0, le=10)
    minimum_neighbour_rays: int = Field(default=3, ge=3)
    neighbour_intercept_tolerance_db: float = Field(default=3, gt=0, le=3)


class Reason(IntFlag):
    REFERENCE_FIT = 1
    WIDE_REFERENCE_AGREEMENT = 2
    TARGET_MATCH = 4
    WEATHER_PROTECTED = 8
    TARGET_CONFLICT = 16
    NUMERIC_PLATEAU = 32
    RADIAL_MORPHOLOGY = 64
    SOURCE_EDGE = 128
    NEAR_RANGE_TARGET = 256
    NEAR_SECTOR_CONSENSUS = 512


def wrap(x):
    return (x + 180) % 360 - 180


def shared_range_term(native, valid, reference):
    """Estimate a shared processing relation, never independent RFI evidence.

    The caller must exclude target AND guard blocks in reference. Both disjoint
    ray groups must support the same relation. Unavailable calibration falls back
    to the original zero-term model; diagnostics preserve the reason.
    """
    r = np.asarray(native.ranges, float)
    if np.shape(valid) != native.shape or np.shape(reference) != r.shape:
        raise ValueError("range term reference geometry differs")
    values = native.fields["DBZH"] - native.fields["SNR"] - 20 * np.log10(np.maximum(r, 1) / 1000)
    estimates = []
    for parity in (0, 1):
        rows = np.arange(native.shape[0]) % 2 == parity
        use = valid[rows] & reference[None, :]
        count = use.sum(axis=0)
        with warnings.catch_warnings():
            warnings.simplefilter("ignore", RuntimeWarning)
            y = np.nanmedian(np.where(use, values[rows], np.nan), axis=0)
        ok = (count >= 5) & np.isfinite(y)
        if ok.sum() < 100 or np.ptp(r[ok]) < 200000:
            return 0.0, {"status": "insufficient_paired_support", "groups": estimates}
        slope, offset = np.polyfit(r[ok] / 1000, y[ok], 1)
        error = float(np.percentile(abs(y[ok] - slope * r[ok] / 1000 - offset), 90))
        estimates.append(
            {
                "slope_db_per_km": float(slope),
                "residual_p90_db": error,
                "range_gates": int(ok.sum()),
                "span_m": float(np.ptp(r[ok])),
            }
        )
        if not 0 <= slope <= 0.03 or error > 0.5:
            return 0.0, {"status": "unsupported_range_relation", "groups": estimates}
    if abs(estimates[0]["slope_db_per_km"] - estimates[1]["slope_db_per_km"]) > 0.002:
        return 0.0, {"status": "inconsistent_ray_groups", "groups": estimates}
    coefficient = float(np.mean([x["slope_db_per_km"] for x in estimates]))
    return coefficient, {
        "status": "measured_consistent",
        "coefficient_db_per_km": coefficient,
        "groups": estimates,
    }


def infer_broad_source(native, cfg, *, weather=None, conflicts=None):
    if (
        cfg.mode not in {"audit", "experiment_quarantine"}
        or cfg.maximum_range_m <= cfg.minimum_range_m
    ):
        raise ValueError("invalid broad source policy/range")
    shape = native.shape
    empty = np.zeros(shape, bool)
    protected = empty.copy() if weather is None else np.asarray(weather, bool)
    conflict = empty.copy() if conflicts is None else np.asarray(conflicts, bool)
    if protected.shape != shape or conflict.shape != shape:
        raise ValueError("broad source protection geometry differs")
    reason = np.zeros(shape, "uint16")
    fold_ids = np.zeros(shape, "uint32")
    residual = np.full(shape, np.nan, "float32")
    candidate = empty.copy()
    required = ["DBZH", "SNR", "RHOHV", "ZDR", "PHIDP"]
    if any(k not in native.fields for k in required):
        return {
            "BWS_CANDIDATE_MASK": candidate.astype("uint8"),
            "BWS_REASON": reason,
            "BWS_FOLD_ID": fold_ids,
            "BWS_RANGE_RESIDUAL_DB": residual,
        }, {"status": "missing_moments", "candidate_gates": 0, "qualified_folds": 0}
    r = np.asarray(native.ranges, float)
    if r.shape != (shape[1],) or not np.isfinite(r).all() or np.any(np.diff(r) <= 0):
        raise ValueError("invalid broad source range geometry")
    f = native.fields
    a = native.field_available
    obs = a["DBZH"] & native.geometry_good[:, None]
    in_range = (r >= cfg.minimum_range_m) & (
        True if cfg.observed_range else r < cfg.maximum_range_m
    )
    target_range = in_range | (cfg.near_range_targets & (r > 0) & (r < cfg.minimum_range_m))
    valid = obs & target_range[None, :]
    for k in required:
        valid &= a[k] & np.isfinite(f[k])
    valid &= f["SNR"] >= 8
    blocks = np.floor((r - cfg.minimum_range_m) / cfg.block_m).astype(int)
    law = 20 * np.log10(np.maximum(r, 1) / 1000)
    power = f["DBZH"] - law[None, :]
    plateau = empty.copy()
    # Flat numeric tails cannot supply verified censor semantics by themselves.
    for ray in range(shape[0]):
        same = (
            obs[ray, 1:]
            & obs[ray, :-1]
            & (abs(np.diff(f["DBZH"][ray])) < 1e-6)
            & (f["DBZH"][ray, 1:] >= 55)
        )
        edges = np.diff(np.r_[False, same, False].astype("int8"))
        for lo, hi in zip(np.flatnonzero(edges == 1), np.flatnonzero(edges == -1)):
            if r[hi] - r[lo] >= 25000:
                plateau[ray, lo : hi + 1] = True
    # Reference plateau decisions are confined to individual reference blocks.
    # Target/guard mutations cannot turn a short reference run into a long plateau.
    reference_plateau = empty.copy()
    for ray in range(shape[0]):
        for b in np.unique(blocks[in_range]):
            indices = np.flatnonzero(in_range & (blocks == b))
            same = (
                obs[ray, indices[1:]]
                & obs[ray, indices[:-1]]
                & (abs(np.diff(f["DBZH"][ray, indices])) < 1e-6)
                & (f["DBZH"][ray, indices[1:]] >= 55)
            )
            edges = np.diff(np.r_[False, same, False].astype("int8"))
            for lo, hi in zip(np.flatnonzero(edges == 1), np.flatnonzero(edges == -1)):
                if r[indices[hi]] - r[indices[lo]] >= 25000:
                    reference_plateau[ray, indices[lo : hi + 1]] = True
    polar_veto = protected | conflict | plateau
    morphology = empty.copy()
    if cfg.radial_opening:
        from .radial_opening import radial_opening

        morphology = radial_opening(f["DBZH"], obs, native.gate_spacing_m)
    near_reference = empty.copy()
    edge_reference = empty.copy()
    range_term_folds = []
    records = []
    qualified = 0
    for block in np.unique(blocks[target_range]):
        target_geometry = blocks == block
        # No target or guard values enter any reference statistic or membership.
        train_geometry = in_range & (abs(blocks - block) > 1)
        if cfg.shared_range_term:
            coefficient, term_diag = shared_range_term(native, valid, train_geometry)
            range_term_folds.append(dict(term_diag, target_block=int(block)))
            power = f["DBZH"] - law[None, :] - coefficient * r[None, :] / 1000
        fits = {}
        for ray in range(shape[0]):
            train = valid[ray] & train_geometry & ~reference_plateau[ray]
            supported = []
            for b in np.unique(blocks[train]):
                idx = np.flatnonzero(train & (blocks == b))
                if len(idx) * native.gate_spacing_m >= cfg.minimum_block_span_m:
                    supported.append(b)
            train &= np.isin(blocks, supported)
            ix = np.flatnonzero(train)
            if (
                len(supported) < cfg.minimum_blocks
                or len(ix) < cfg.minimum_polar_samples
                or np.ptp(r[ix]) < cfg.minimum_reference_span_m
            ):
                continue
            intercept = float(np.median(power[ray, ix]))
            err = abs(power[ray, ix] - intercept)
            medians = np.array([np.median(f["SNR"][ray, train & (blocks == b)]) for b in supported])
            if (
                np.percentile(err, 90) > cfg.range_residual_db
                or np.median(medians) < cfg.minimum_snr_db
                or np.percentile(abs(medians - np.median(medians)), 90) > cfg.snr_stationarity_db
            ):
                continue
            phase = f["PHIDP"][ray]
            center = float(np.angle(np.mean(np.exp(1j * np.deg2rad(phase[ix]))), deg=True))
            pc = wrap(phase[ix] - center)
            zdr = f["ZDR"][ray, ix]
            rho = f["RHOHV"][ray, ix]
            snr = f["SNR"][ray, ix]
            if (
                np.percentile(abs(pc), 90) > cfg.maximum_phase_p90_deg
                or np.percentile(abs(zdr - np.median(zdr)), 90) > cfg.maximum_zdr_p90_db
            ):
                continue
            fits[ray] = (
                intercept,
                center,
                np.percentile(pc, [5, 95]),
                np.percentile(zdr, [5, 95]),
                np.percentile(rho, [5, 95]),
                np.percentile(snr, [5, 95]),
            )
        for ray, fit in fits.items():
            # Walk connected azimuths; never bridge a geometry gap or use target values.
            neighbours = {ray}
            for direction in (-1, 1):
                current = ray
                for _ in range(shape[0] - 1):
                    nxt = current + direction
                    if nxt < 0 or nxt >= shape[0]:
                        if not native.full_ppi:
                            break
                        nxt %= shape[0]
                    edge = current if direction == 1 else nxt
                    if native.gap_after[edge] or nxt not in fits:
                        break
                    angle = abs((native.azimuth[nxt] - native.azimuth[ray] + 180) % 360 - 180)
                    if (
                        angle > cfg.maximum_neighbour_angle_deg
                        or abs(fits[nxt][0] - fit[0]) > cfg.neighbour_intercept_tolerance_db
                    ):
                        break
                    neighbours.add(nxt)
                    current = nxt
            target = valid[ray] & target_geometry
            reason[ray, target] |= int(Reason.REFERENCE_FIT)
            reason[ray, target & ~in_range] |= int(Reason.NEAR_RANGE_TARGET)
            if cfg.near_sector_consensus:
                near_reference[ray] |= (
                    target & (f["SNR"][ray] >= fit[5][0] - 1) & (f["SNR"][ray] <= fit[5][1] + 1)
                )
            if cfg.source_edge:
                residual[ray, target] = (power[ray] - fit[0])[target]
                edge_reference[ray] |= (
                    target
                    & in_range
                    & (f["SNR"][ray] >= fit[5][0] - 1)
                    & (f["SNR"][ray] <= fit[5][1] + 1)
                )
            angular_span = np.ptp(
                [(native.azimuth[k] - native.azimuth[ray] + 180) % 360 - 180 for k in neighbours]
            )
            if (
                len(neighbours) < cfg.minimum_neighbour_rays
                or angular_span < cfg.minimum_angular_span_deg
            ):
                continue
            qualified += 1
            fid = qualified
            fold_ids[ray, target] = fid
            reason[ray, target] |= int(Reason.WIDE_REFERENCE_AGREEMENT)
            intercept, center, p, z, h, s = fit
            delta = power[ray] - intercept
            residual[ray, target] = delta[target]
            pc = wrap(f["PHIDP"][ray] - center)
            matched = (
                target
                & (abs(delta) <= cfg.range_residual_db)
                & (f["SNR"][ray] >= s[0] - 1)
                & (f["SNR"][ray] <= s[1] + 1)
                & (pc >= p[0] - 0.5)
                & (pc <= p[1] + 0.5)
                & (f["ZDR"][ray] >= z[0] - 0.125)
                & (f["ZDR"][ray] <= z[1] + 0.125)
                & (f["RHOHV"][ray] >= max(0, h[0] - 0.01))
                & (f["RHOHV"][ray] <= min(1, h[1] + 0.01))
            )
            polar_diag = {"status": "disabled"}
            if cfg.distance_polar_reference:
                bounds, polar_diag = distance_polar_reference(
                    native,
                    cfg,
                    ray,
                    neighbours,
                    fits,
                    power,
                    target_geometry,
                    valid,
                    polar_veto,
                )
                if bounds is not None:
                    pp, zz, hh = bounds
                    matched |= (
                        target
                        & (abs(delta) <= cfg.range_residual_db)
                        & (f["SNR"][ray] >= s[0] - 1)
                        & (f["SNR"][ray] <= s[1] + 1)
                        & (pc >= pp[0] - 0.5)
                        & (pc <= pp[1] + 0.5)
                        & (f["ZDR"][ray] >= zz[0] - 0.125)
                        & (f["ZDR"][ray] <= zz[1] + 0.125)
                        & (f["RHOHV"][ray] >= max(0, hh[0] - 0.01))
                        & (f["RHOHV"][ray] <= min(1, hh[1] + 0.01))
                    )
            if cfg.radial_opening:
                morph_match = (
                    target
                    & in_range
                    & morphology[ray]
                    & (abs(delta) <= cfg.range_residual_db)
                    & (f["SNR"][ray] >= s[0] - 1)
                    & (f["SNR"][ray] <= s[1] + 1)
                )
                reason[ray, morph_match] |= int(Reason.RADIAL_MORPHOLOGY)
                reason[ray, morph_match & ~matched] |= int(Reason.TARGET_CONFLICT)
                matched |= morph_match
            reason[ray, matched] |= int(Reason.TARGET_MATCH)
            reason[ray, target & ~matched] |= int(Reason.TARGET_CONFLICT)
            candidate[ray] = candidate[ray] | (
                matched & ~protected[ray] & ~conflict[ray] & ~plateau[ray]
            )
            records.append(
                {
                    "fold_id": fid,
                    "ray": ray,
                    "target_block": int(block),
                    "intercept_db": intercept,
                    "phase_center_deg": center,
                    "support_rays": sorted(neighbours),
                    **({"distance_polar": polar_diag} if cfg.distance_polar_reference else {}),
                }
            )
    edge_summary = {"status": "disabled", "candidate_gates": 0}
    if cfg.source_edge:
        from .source_edge import source_edge

        edge_added, _ = source_edge(
            native,
            candidate,
            edge_reference,
            residual,
            weather=protected,
            conflicts=conflict | plateau,
        )
        candidate |= edge_added
        reason[edge_added] |= int(Reason.SOURCE_EDGE | Reason.TARGET_MATCH)
        edge_summary = {"status": "experimental_one_hop", "candidate_gates": int(edge_added.sum())}
    sector_summary = {"status": "disabled", "candidate_gates": 0}
    if cfg.near_sector_consensus:
        from .near_sector import near_sector

        measured_source = (
            near_reference & ((reason & 3) == 3) & (abs(residual) <= cfg.range_residual_db)
        )
        sector_added = near_sector(
            native, measured_source, weather=protected, conflicts=conflict | plateau
        )
        sector_added &= ~candidate
        candidate |= sector_added
        reason[sector_added] |= int(Reason.NEAR_SECTOR_CONSENSUS | Reason.TARGET_MATCH)
        sector_summary = {
            "status": "experimental_source_morphology",
            "candidate_gates": int(sector_added.sum()),
            "minimum_rays": 3,
            "minimum_range_span_m": 10000,
        }
    reason[obs & protected] |= int(Reason.WEATHER_PROTECTED)
    reason[obs & conflict] |= int(Reason.TARGET_CONFLICT)
    reason[obs & plateau] |= int(Reason.NUMERIC_PLATEAU)
    return {
        "BWS_CANDIDATE_MASK": candidate.astype("uint8"),
        "BWS_REASON": reason,
        "BWS_FOLD_ID": fold_ids,
        "BWS_RANGE_RESIDUAL_DB": residual,
    }, {
        "status": "experimental_source_hypothesis",
        "candidate_gates": int(candidate.sum()),
        "qualified_folds": qualified,
        "operational_eligible": False,
        "folds": records,
        "range_term_folds": range_term_folds,
        **({"near_sector": sector_summary} if cfg.near_sector_consensus else {}),
        **({"source_edge": edge_summary} if cfg.source_edge else {}),
        **(
            {
                "radial_opening": {
                    "algorithm": "skimage.morphology.opening",
                    "version": "0.26.0",
                    "threshold_dbz_exclusive": 35,
                    "minimum_length_m": 50000,
                    "footprint_gates": int(np.ceil(50000 / native.gate_spacing_m)) | 1,
                    "raw_candidate_gates": int(morphology.sum()),
                    "source_supported_gates": int(((reason & 64) != 0).sum()),
                }
            }
            if cfg.radial_opening
            else {}
        ),
    }
