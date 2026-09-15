"""Blocked, target-excluded source signatures with explicit weather counterevidence.

This is an original uncalibrated research model, not a MIT/MRMS port. It never
reads old QC/ROI/anchors. Reference association is conditional on raw observation
topology, not independent weather truth. No missing observation is created.
"""
from __future__ import annotations
from dataclasses import dataclass, asdict
from enum import IntFlag
import hashlib
import time
from pathlib import Path
import numpy as np

from .config import Config, digest
from .data import RawScan, array_sha
from .geometry import build_domains, local_angular_width, edge_geometry, runs, wrap


class Reason(IntFlag):
    OUTSIDE_RANGE = 1
    NO_RAW_DOMAIN = 2
    SNR_MISSING = 4
    REFERENCE_BLOCKS = 8
    REFERENCE_SPAN = 16
    NONSTATIONARY = 32
    REFERENCE_POL = 64
    REFERENCE_PHASE_PAIRS = 128
    SOURCE_FAMILY_UNRESOLVED = 256
    TARGET_POWER_MISMATCH = 512
    TARGET_POL_MISSING = 1024
    TARGET_COHERENCE_MISMATCH = 2048
    LOCAL_HEALTHY_WEATHER = 4096
    LOCAL_POWER_ENHANCEMENT = 8192
    BROAD_WEATHER_CONTINUITY = 16384
    EXTERNAL_WEATHER_SUPPORT = 32768
    WIDE_SOURCE_HYPOTHESIS = 65536
    UNBRACKETED_REFERENCE = 131072
    SOURCE_MODEL_COMPATIBLE = 262144
    WEATHER_SOURCE_CONFLICT = 524288
    GEOMETRY_UNAVAILABLE = 1048576


@dataclass(frozen=True)
class WeatherSupport:
    score: np.ndarray
    comparable: np.ndarray
    resource_receipt_sha256: str
    independently_vetted: bool
    threshold: float = 0.7

    def mask(self, shape):
        if not self.independently_vetted or len(self.resource_receipt_sha256) != 64:
            raise ValueError("external weather requires a vetted resource/content receipt")
        if any(c not in '0123456789abcdef' for c in self.resource_receipt_sha256):
            raise ValueError("invalid weather receipt digest")
        score, a = np.asarray(self.score), np.asarray(self.comparable)
        if score.shape != shape or a.shape != shape or not np.isin(a, [0, 1]).all():
            raise ValueError("weather support geometry mismatch")
        a = a.astype(bool)
        if np.any(a & (~np.isfinite(score) | (score < 0) | (score > 1))):
            raise ValueError("comparable weather score invalid")
        if not 0 < self.threshold <= 1:
            raise ValueError("weather threshold invalid")
        return a.astype(bool) & (score >= self.threshold)


@dataclass
class Evidence:
    arrays: dict
    folds: list
    objects: list
    links: list
    identity: dict
    timing: dict


def _weather_local(raw, cfg):
    f, a, obs = raw.fields, raw.available, raw.observed
    reliable = obs & raw.good[:, None] & a["SNR"] & (f["SNR"] >= cfg.reliable_snr_db) & a["RHOHV"] & a["PHIDP"]
    increment = np.full(raw.shape, np.nan)
    pair = np.zeros(raw.shape, bool)
    pair[:, 1:] = reliable[:, 1:] & reliable[:, :-1]
    increment[:, 1:] = wrap(f["PHIDP"][:, 1:] - f["PHIDP"][:, :-1], raw.phase_period)
    high = reliable & (f["RHOHV"] >= cfg.healthy_rho)
    stable_pair = pair & high & np.column_stack((np.zeros(raw.shape[0], bool), high[:, :-1]))
    stable_pair &= abs(increment) <= cfg.healthy_increment_deg
    healthy = np.zeros(raw.shape, bool)
    for ray in range(raw.shape[0]):
        for lo, hi in runs(stable_pair[ray]):
            # A stable-pair run beginning at lo includes original sample lo-1.
            if (hi-lo+1)*raw.dr >= cfg.healthy_run_m:
                healthy[ray, max(0, lo-1):hi] = True
    edges, _, _ = edge_geometry(raw, cfg)
    n = raw.shape[0]
    left, right = np.zeros(raw.shape, "uint8"), np.zeros(raw.shape, "uint8")
    for direction, counts in ((-1, left), (1, right)):
        for offset in range(1, cfg.weather_lateral_rays+1):
            rows = np.arange(n)
            src = (rows + direction*offset) % n
            row_ok = raw.good & raw.good[src] & (rows != src)
            for step in range(offset):
                edge = (rows-step-1) % n if direction < 0 else (rows+step) % n
                row_ok &= edges[edge]
            angle = abs(wrap(raw.azimuth[src]-raw.azimuth))
            distance = raw.ranges[None, :]*np.deg2rad(angle[:, None])
            valid = row_ok[:, None] & healthy[src] & healthy
            valid &= distance <= cfg.weather_lateral_max_m
            valid &= abs(f["DBZH"][src]-f["DBZH"]) <= cfg.weather_lateral_difference_db
            counts += valid.astype("uint8")
    broad = healthy & (left >= cfg.weather_lateral_rays) & (right >= cfg.weather_lateral_rays)
    return reliable, pair, increment, healthy, broad


def infer(raw: RawScan, cfg: Config = Config(), weather: WeatherSupport | None = None) -> Evidence:
    if np.prod(raw.shape) > cfg.maximum_gates:
        raise ValueError("gate budget exceeded")
    start = time.perf_counter()
    domains, ids, bundle_ids, objects, links = build_domains(raw, cfg)
    after_objects = time.perf_counter()
    reliable, pairs, increment, healthy, broad = _weather_local(raw, cfg)
    external = np.zeros(raw.shape, bool) if weather is None else weather.mask(raw.shape) & raw.observed
    after_weather = time.perf_counter()
    f, a, obs = raw.fields, raw.available, raw.observed
    bix = np.floor((raw.ranges-cfg.range_min_m)/cfg.block_m).astype(int)
    in_range = (raw.ranges >= cfg.range_min_m) & (raw.ranges < cfg.range_max_m)
    family = np.zeros(raw.shape, "uint8")
    state = np.where(obs, 1, 0).astype("uint8")
    reason = np.zeros(raw.shape, "uint32")
    reason[obs & ~raw.good[:, None]] |= int(Reason.GEOMETRY_UNAVAILABLE)
    reason[obs & ~in_range[None, :]] |= int(Reason.OUTSIDE_RANGE)
    reason[obs & in_range[None, :] & (ids == 0)] |= int(Reason.NO_RAW_DOMAIN)
    reason[obs & ~a["SNR"]] |= int(Reason.SNR_MISSING)
    fold_id = np.zeros(raw.shape, "uint32")
    bracketed = np.zeros(raw.shape, "uint8")
    source_fit = np.zeros(raw.shape, "uint8")
    power_residual = np.full(raw.shape, np.nan, "float32")
    ref_snr = power_residual.copy()
    source_score = power_residual.copy()
    # Scores are dimensionless compatibility diagnostics, NOT probabilities.
    folds = []
    for domain in domains:
        ray = domain.ray
        index = domain.indices
        stats = {}
        snr_good = a["SNR"][ray, index]
        for block in domain.blocks:
            idx = index[(bix[index] == block) & snr_good]
            if len(idx)*raw.dr >= cfg.minimum_block_support_m:
                stats[block] = np.percentile(f["SNR"][ray, idx], [5, 50, 95])
        for block in domain.blocks:
            target_all = index[bix[index] == block]
            target = target_all[a["SNR"][ray, target_all]]
            if not len(target):
                continue
            if len(folds) >= cfg.maximum_folds:
                raise ValueError("fold budget exceeded; no partial inference")
            train_blocks = sorted(b for b in stats if abs(b-block) > cfg.guard_blocks)
            # This pool includes only original same-domain measured gates OUTSIDE
            # target+guard. No target statistic may select/fit reference values.
            train = index[np.isin(bix[index], train_blocks) & snr_good]
            poly = train[reliable[ray, train]]
            train_mask = np.zeros(raw.shape[1], bool)
            train_mask[train] = True
            pair_indices = np.flatnonzero(pairs[ray] & train_mask & np.r_[False, train_mask[:-1]])
            pair_indices = pair_indices[bix[pair_indices] == bix[pair_indices-1]]
            rec = {"fold_id": len(folds)+1, "domain_id": domain.identity, "ray": ray,
                   "target_block": int(block), "target_bounds_m": [cfg.range_min_m+block*cfg.block_m,
                                                                      cfg.range_min_m+(block+1)*cfg.block_m],
                   "guard_blocks": cfg.guard_blocks, "train_blocks": train_blocks,
                   "reference_intervals": runs(train_mask), "reference_digest": None,
                   "family": 0, "status": "insufficient_disjoint_blocks", "target_measured": len(target)}
            state[ray, target] = 2
            fold_id[ray, target] = rec["fold_id"]
            if len(train_blocks) < cfg.minimum_train_blocks:
                reason[ray, target] |= int(Reason.REFERENCE_BLOCKS)
                folds.append(rec)
                continue
            if (train_blocks[-1]-train_blocks[0])*cfg.block_m < cfg.minimum_train_span_m:
                rec["status"] = "insufficient_reference_span"
                reason[ray, target] |= int(Reason.REFERENCE_SPAN)
                folds.append(rec)
                continue
            reference_values = np.vstack([f[k][ray, train] for k in ("SNR", "RHOHV", "PHIDP", "ZDR")])
            rec["reference_digest"] = digest({"indices": array_sha(train), "moments": array_sha(reference_values),
                                               "masks": {k: array_sha(a[k][ray, train]) for k in ("SNR","RHOHV","PHIDP","ZDR")}})
            medians = np.array([stats[b][1] for b in train_blocks])
            mu = float(np.median(medians))
            dispersion = float(np.percentile(abs(medians-mu), 90))
            lo = float(np.median([stats[b][0] for b in train_blocks])-cfg.target_snr_allowance_db)
            hi = float(np.median([stats[b][2] for b in train_blocks])+cfg.target_snr_allowance_db)
            rec.update(snr_median_db=mu, median_dispersion_p90_db=dispersion,
                       snr_interval_db=[lo, hi], pol_samples=len(poly), phase_pairs=len(pair_indices))
            ref_snr[ray, target] = mu
            power_residual[ray, target] = f["SNR"][ray, target]-mu
            if dispersion > cfg.median_stationarity_p90_db:
                rec["status"] = "nonstationary_receiver_scale"
                reason[ray, target] |= int(Reason.NONSTATIONARY)
                folds.append(rec)
                continue
            pol_blocks = [b for b in train_blocks if np.sum(bix[poly] == b) >= 3]
            low_fraction = float(np.mean(f["RHOHV"][ray, poly] < cfg.low_rho)) if len(poly) else None
            incvar = float(1-abs(np.mean(np.exp(2j*np.pi*increment[ray, pair_indices]/raw.phase_period)))) if len(pair_indices) else None
            noisy_blocks = [b for b in pol_blocks if np.mean(f["RHOHV"][ray, poly[bix[poly] == b]] < cfg.low_rho) >= 0.5]
            rec.update(pol_blocks=pol_blocks, low_rho_fraction=low_fraction,
                       increment_variance=incvar, noisy_blocks=noisy_blocks)
            if len(poly) < cfg.minimum_pol_samples or len(pol_blocks) < cfg.minimum_pol_blocks:
                reason[ray, target] |= int(Reason.REFERENCE_POL)
            if len(pair_indices) < cfg.minimum_pair_samples:
                reason[ray, target] |= int(Reason.REFERENCE_PHASE_PAIRS)
            noisy = (len(poly) >= cfg.minimum_pol_samples and len(noisy_blocks) >= cfg.minimum_pol_blocks
                     and low_fraction >= cfg.minimum_low_rho_fraction and len(pair_indices) >= cfg.minimum_pair_samples
                     and incvar >= cfg.minimum_increment_variance)
            coherent, phase_center, zdr_center = False, None, None
            pz = poly[a["ZDR"][ray, poly]]
            if (len(poly) >= cfg.coherent_minimum_samples and len(pol_blocks) >= cfg.minimum_pol_blocks
                    and len(pz) >= cfg.coherent_minimum_samples and mu >= cfg.coherent_snr_db):
                center = float(np.angle(np.mean(np.exp(2j*np.pi*f["PHIDP"][ray, poly]/raw.phase_period)))*raw.phase_period/(2*np.pi))
                pp90 = float(np.percentile(abs(wrap(f["PHIDP"][ray, poly]-center, raw.phase_period)), 90))
                dc = float(np.median(f["ZDR"][ray, pz]))
                dp90 = float(np.percentile(abs(f["ZDR"][ray, pz]-dc), 90))
                hf = float(np.mean(f["RHOHV"][ray, poly] >= cfg.coherent_rho))
                coherent = pp90 <= cfg.coherent_phase_p90_deg and dp90 <= cfg.coherent_zdr_p90_db and hf >= cfg.coherent_fraction
                phase_center, zdr_center = center, dc
                rec.update(coherent_phase_center_deg=center, coherent_phase_p90_deg=pp90,
                           coherent_zdr_median_db=dc, coherent_zdr_p90_db=dp90, coherent_high_rho_fraction=hf)
            kind = 1 if noisy else (2 if coherent else 0)
            if not kind:
                rec["status"] = "source_family_unresolved"
                reason[ray, target] |= int(Reason.SOURCE_FAMILY_UNRESOLVED)
                folds.append(rec)
                continue
            rec["family"] = kind
            rec["status"] = "source_fitted_target_heldout"
            source_fit[ray, target] = kind
            target_snr = f["SNR"][ray, target]
            match = (target_snr >= lo) & (target_snr <= hi)
            reason[ray, target[~match]] |= int(Reason.TARGET_POWER_MISMATCH)
            if kind == 2:
                pol_ok = reliable[ray, target] & a["ZDR"][ray, target]
                reason[ray, target[~pol_ok]] |= int(Reason.TARGET_POL_MISSING)
                coh_match = (abs(wrap(f["PHIDP"][ray, target]-phase_center, raw.phase_period)) <= cfg.coherent_phase_p90_deg)
                coh_match &= (abs(f["ZDR"][ray, target]-zdr_center) <= cfg.coherent_zdr_p90_db) & (f["RHOHV"][ray, target] >= cfg.coherent_rho)
                reason[ray, target[pol_ok & ~coh_match]] |= int(Reason.TARGET_COHERENCE_MISMATCH)
                match &= pol_ok & coh_match
            state[ray, target] = 3
            chosen = target[match]
            family[ray, chosen] = kind
            bracket = train_blocks[0] < block < train_blocks[-1]
            bracketed[ray, chosen] = int(bracket)
            if not bracket:
                reason[ray, chosen] |= int(Reason.UNBRACKETED_REFERENCE)
            scale = max(cfg.target_snr_allowance_db, (hi-lo)/2, 0.1)
            source_score[ray, chosen] = np.clip(1-abs(f["SNR"][ray, chosen]-mu)/scale, 0, 1)
            reason[ray, chosen] |= int(Reason.SOURCE_MODEL_COMPATIBLE)
            state[ray, chosen] = 5
            # Weather competitor: continuous local weather protects NOISY family;
            # coherent family additionally needs actual broad lateral continuity.
            protected = external[ray, chosen] | broad[ray, chosen]
            if kind == 1:
                protected |= healthy[ray, chosen]
                reason[ray, chosen[healthy[ray, chosen]]] |= int(Reason.LOCAL_HEALTHY_WEATHER)
            reason[ray, chosen[broad[ray, chosen]]] |= int(Reason.BROAD_WEATHER_CONTINUITY)
            reason[ray, chosen[external[ray, chosen]]] |= int(Reason.EXTERNAL_WEATHER_SUPPORT)
            # Power enhancements remain unresolved weather/source mixtures, even
            # if a broad source distribution could otherwise include them.
            enhancement = f["SNR"][ray, chosen] > mu+cfg.local_snr_excess_db
            reason[ray, chosen[enhancement]] |= int(Reason.LOCAL_POWER_ENHANCEMENT)
            protected |= enhancement
            state[ray, chosen[protected]] = 4
            reason[ray, chosen[protected]] |= int(Reason.WEATHER_SOURCE_CONFLICT)
            folds.append(rec)
    after_fit = time.perf_counter()
    width = local_angular_width(family > 0, raw, cfg)
    # Width selects an alternative structural hypothesis, not a universal narrow
    # veto. The jointly fitted polarimetric signature can describe a bundle too.
    # Only an unbounded sector is left unresolved; broad local weather was checked
    # separately, from measured support. These are not multiple independent votes.
    kind = np.zeros(raw.shape, "uint8")
    kind[(family > 0) & (width <= cfg.maximum_coherent_width_deg)] = 1
    kind[(family > 0) & (width > cfg.maximum_coherent_width_deg) & (width <= cfg.maximum_noisy_width_deg)] = 2
    kind[(family > 0) & (width > cfg.maximum_noisy_width_deg)] = 3
    wide = (family > 0) & (width > cfg.maximum_sector_width_deg)
    state[(state == 5) & wide] = 6
    reason[wide] |= int(Reason.WIDE_SOURCE_HYPOTHESIS)
    arrays = {"domain_id": ids, "bundle_id": bundle_ids, "fold_id": fold_id,
              "source_fit_family": source_fit, "family_code": family, "state": state,
              "reason": reason, "bracketed_reference_mask": bracketed,
              "source_width_deg": width, "structural_kind": kind, "reference_snr_db": ref_snr,
              "target_snr_residual_db": power_residual, "source_compatibility_score": source_score,
              "local_weather_mask": healthy.astype("uint8"), "broad_weather_mask": broad.astype("uint8"),
              "external_weather_mask": external.astype("uint8")}
    if np.any((family > 0) & ~obs) or np.any((state == 0) != ~obs):
        raise AssertionError("inference changed original support")
    for rec in folds:
        fid = rec["fold_id"]
        m = fold_id[rec["ray"]] == fid
        rec["state_counts"] = {str(i): int(np.sum(state[rec["ray"], m] == i)) for i in range(7)}
    identity = {"schema": "rainpulse.object-consensus.evidence.v1", "input_sha256": raw.identity,
                "model_sha256": digest(asdict(cfg)), "model": asdict(cfg),
                "code_family": "oc1", "code_sha256": digest({p.name: hashlib.sha256(p.read_bytes()).hexdigest() for p in sorted(Path(__file__).parent.glob("*.py"))}),
                "phase_period_deg": raw.phase_period,
                "weather_receipt": weather.resource_receipt_sha256 if weather else None,
                "weather_arrays_sha256": None if weather is None else digest({"score": array_sha(weather.score), "comparable": array_sha(weather.comparable), "threshold": weather.threshold}),
                "calibrated_probability": False, "operational_eligible": False}
    identity["evidence_sha256"] = digest({"identity": identity, "arrays": {k: array_sha(v) for k,v in sorted(arrays.items())}, "folds": folds})
    return Evidence(arrays, folds, objects, links, identity,
                    {"objects_seconds": after_objects-start, "weather_features_seconds": after_weather-after_objects,
                     "crossfit_seconds": after_fit-after_weather,
                     "width_and_finalize_seconds": time.perf_counter()-after_fit,
                     "core_seconds": time.perf_counter()-start, "context_seconds": None, "upload_seconds": None})
