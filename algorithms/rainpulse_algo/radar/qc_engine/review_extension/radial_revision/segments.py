"""Stage 2: training-only SNR states, blocked references and strict target tests.

A paired DBZH/SNR range relation is receiver processing, not an independent vote
for RFI. Weak/incomplete-polar states are diagnostics, never censor actions.
This extends reference construction; it does not replace OC1's noisy classifier.
"""
from __future__ import annotations
import hashlib
import json
import warnings
import numpy as np
from ..arrays import native_geometry, moment
from .geometry import numeric_plateaus, ResourceLimit


def wrap(value):
    return (value + 180.) % 360. - 180.


def array_digest(*arrays):
    h = hashlib.sha256()
    for a in arrays:
        a = np.ascontiguousarray(a)
        h.update(str(a.dtype).encode())
        h.update(str(a.shape).encode())
        h.update(a.tobytes())
    return h.hexdigest()


def range_relation(ranges, z, snr, paired, train):
    """Same paired processing relation as the existing broad-source calibration.

    Target+guard are removed across ALL rays before estimation. Independent ray
    parity groups must agree. Failure is explicit; zero is not an inferred fit.
    """
    law = 20.*np.log10(np.maximum(ranges, 1.)/1000.)
    values = z-snr-law[None, :]
    estimates = []
    available = paired & train[None, :]
    reference_digest = array_digest(ranges[train], available[:, train],
                                    np.where(available[:, train], values[:, train], np.nan))
    for parity in (0, 1):
        use = available[parity::2]
        with warnings.catch_warnings():
            warnings.simplefilter("ignore", RuntimeWarning)
            y = np.nanmedian(np.where(use, values[parity::2], np.nan), axis=0)
        ok = (use.sum(axis=0) >= 5) & np.isfinite(y)
        if ok.sum() < 100 or np.ptp(ranges[ok]) < 200000:
            return 0., {"status": "insufficient_paired_support", "sha256": reference_digest}
        slope, offset = np.polyfit(ranges[ok]/1000., y[ok], 1)
        error = float(np.percentile(np.abs(y[ok]-slope*ranges[ok]/1000.-offset), 90))
        if not -1e-9 <= slope <= .03 or error > .5:
            return 0., {"status": "unsupported_range_relation", "sha256": reference_digest}
        estimates.append(max(0., float(slope)))
    if abs(estimates[0]-estimates[1]) > .002:
        return 0., {"status": "inconsistent_ray_groups", "sha256": reference_digest}
    return float(np.mean(estimates)), {"status": "measured_consistent", "sha256": reference_digest}


def discover_states(values, cfg):
    """Split only large training SNR gaps; never choose a mode using a target."""
    values = np.sort(np.asarray(values, float))
    if len(values) < cfg.minimum_reference_samples:
        return []
    intervals = [(0, len(values))]
    while len(intervals) < cfg.maximum_states:
        choices = []
        for i, (lo, hi) in enumerate(intervals):
            for cut in range(lo+cfg.minimum_reference_samples, hi-cfg.minimum_reference_samples+1):
                gap = values[cut]-values[cut-1]
                if gap >= cfg.state_separation_db:
                    choices.append((float(gap), -cut, i, cut))
        if not choices:
            break
        _, _, i, cut = max(choices)
        lo, hi = intervals.pop(i)
        intervals.extend([(lo, cut), (cut, hi)])
        intervals.sort()
    boundaries = [float((values[b-1]+values[b])/2.) for _, b in intervals[:-1]]
    # Finite physical bounds make exported JSON strict (no Infinity).
    cuts = [-1.e6, *boundaries, 1.e6]
    return list(zip(cuts[:-1], cuts[1:]))


def _fit_state(native, cfg, row, train_indices, blocks, power, values, availability):
    """A model is accepted only after disjoint block-group agreement."""
    r = native.ranges
    snr = values["SNR"][row]
    keep = []
    for b in np.unique(blocks[train_indices]):
        ix = train_indices[blocks[train_indices] == b]
        if len(ix)*native.gate_spacing_m >= cfg.minimum_block_support_m:
            keep.extend(ix.tolist())
    ix = np.asarray(sorted(keep), dtype=int)
    used_blocks = np.unique(blocks[ix])
    record = {"status": "insufficient_reference_support", "reference_samples": int(len(ix)),
              "reference_blocks": [int(b) for b in used_blocks]}
    if (len(ix) < cfg.minimum_reference_samples or len(used_blocks) < cfg.minimum_reference_blocks or
            np.ptp(r[ix]) < cfg.minimum_reference_span_m):
        return None, record
    # Alternating sorted reference blocks; both groups carry independent samples.
    group = [ix[np.isin(blocks[ix], used_blocks[p::2])] for p in (0, 1)]
    if any(len(g) < cfg.minimum_group_samples for g in group):
        record["status"] = "insufficient_disjoint_groups"
        return None, record
    offsets = [float(np.median(power[row, g])) for g in group]
    intercept = float(np.mean(offsets))
    error = float(np.percentile(abs(power[row, ix]-intercept), 90))
    snr_medians = np.array([np.median(snr[ix[blocks[ix] == b]]) for b in used_blocks])
    snr_center = float(np.median(snr_medians))
    dispersion = float(np.percentile(abs(snr_medians-snr_center), 90))
    record.update(intercept_db=intercept, power_p90_db=error,
                  snr_median_db=snr_center, snr_dispersion_db=dispersion,
                  reference_min_m=float(r[ix[0]]), reference_max_m=float(r[ix[-1]]),
                  group_intercepts_db=offsets,
                  reference_sha256=array_digest(ix, *[
                      values[k][row, ix] for k in ("DBZH", "SNR", "PHIDP", "ZDR", "RHOHV")],
                      *[availability[k][row, ix] for k in ("PHIDP", "ZDR", "RHOHV")]))
    power_verified = (error <= cfg.maximum_power_residual_db and
                      abs(offsets[0]-offsets[1]) <= cfg.maximum_power_residual_db)
    record["strong_power_verified"] = power_verified
    if dispersion > cfg.maximum_snr_dispersion_db:
        record["status"] = "snr_nonstationary"
        return None, record
    polar = ix[np.logical_and.reduce([availability[k][row, ix] for k in ("PHIDP", "ZDR", "RHOHV")])]
    model = {"intercept": intercept, "snr_bounds": np.percentile(snr[ix], [5, 95]),
             "family": 3, "polar": None, "require_power_match": power_verified,
             "reference_min_m": float(r[ix[0]]),
             "reference_max_m": float(r[ix[-1]])}
    record["family"] = "incomplete_polar_diagnostic"
    if len(polar) >= cfg.minimum_polar_samples:
        phase = values["PHIDP"][row, polar]
        center = float(np.angle(np.mean(np.exp(1j*np.deg2rad(phase))), deg=True))
        phase_residual = wrap(phase-center)
        zdr = values["ZDR"][row, polar]
        phase_error = float(np.percentile(abs(phase_residual), 90))
        zdr_error = float(np.percentile(abs(zdr-np.median(zdr)), 90))
        record.update(phase_center_deg=center, phase_p90_deg=phase_error, zdr_p90_db=zdr_error)
        # Receiver-scale weak evidence is a separate diagnostic path. It does
        # not acquire the strict coherent family's power claim or QC action.
        coherent = (power_verified and snr_center >= cfg.minimum_coherent_snr_db and
                    phase_error <= cfg.maximum_phase_p90_deg and zdr_error <= cfg.maximum_zdr_p90_db)
        model["family"] = 1 if coherent else 2
        record["family"] = "segmented_coherent" if coherent else "weak_or_incoherent_diagnostic"
        if coherent:
            model["polar"] = (center, np.percentile(phase_residual, [5, 95]),
                              np.percentile(zdr, [5, 95]),
                              np.percentile(values["RHOHV"][row, polar], [5, 95]))
    if model["family"] != 1:
        model["require_power_match"] = False
        # Same raw nonmeteorological features used by OC1's noisy hypothesis;
        # these are exported as diagnostics, NOT a new calibrated classifier.
        pair = polar[(np.isin(polar-1, polar)) & (blocks[polar] == blocks[np.maximum(polar-1, 0)])]
        if len(polar):
            record["low_rho_fraction"] = float(np.mean(values["RHOHV"][row, polar] < .85))
        if len(pair):
            increments = wrap(values["PHIDP"][row, pair]-values["PHIDP"][row, pair-1])
            record["phase_increment_variance"] = float(1.-abs(np.mean(np.exp(1j*np.deg2rad(increments)))))
        record["diagnostic_evidence"] = "receiver_snr_distribution_not_verified_dbzh_source"
    record["status"] = "reference_fitted" if model["family"] == 1 else "weak_receiver_reference"
    return model, record


def segmented_references(native, cfg, candidate, blocked, *, records_out=None):
    r, az, dr, good, gaps = native_geometry(native)
    values, availability = {}, {}
    for key in ("DBZH", "SNR", "PHIDP", "ZDR", "RHOHV"):
        values[key], availability[key] = moment(native, key)
    z, snr = values["DBZH"], values["SNR"]
    observed = availability["DBZH"] & good[:, None]
    paired = observed & availability["SNR"] & ~blocked & (snr >= cfg.minimum_diagnostic_snr_db)
    blocks = np.floor((r-cfg.minimum_range_m)/cfg.block_m).astype(int)
    ref_plateau = numeric_plateaus(z, observed, r, blocks=blocks)
    target_valid = paired & candidate & (r[None, :] >= cfg.minimum_range_m)
    shape = native.shape
    arrays = {
        "RV2_SEGMENT_MATCH_MASK": np.zeros(shape, "uint8"),
        "RV2_WEAK_MATCH_MASK": np.zeros(shape, "uint8"),
        "RV2_FIT_AVAILABLE_MASK": np.zeros(shape, "uint8"),
        "RV2_AMBIGUOUS_STATE_MASK": np.zeros(shape, "uint8"),
        "RV2_SEGMENT_FOLD_ID": np.zeros(shape, "uint32"),
        "RV2_MODEL_ID": np.zeros(shape, "uint32"),
        "RV2_STATE_FAMILY": np.zeros(shape, "uint8"),
        "RV2_SEGMENT_RESIDUAL_DB": np.full(shape, np.nan, "float32"),
        "RV2_REFERENCE_MIN_M": np.full(shape, np.nan, "float32"),
        "RV2_REFERENCE_MAX_M": np.full(shape, np.nan, "float32"),
        "RV2_RANGE_TERM_MEASURED_MASK": np.zeros(shape, "uint8"),
    }
    reference_hash = hashlib.sha256()
    folds, model_count, statuses = 0, 0, {}
    for block in np.unique(blocks[np.any(target_valid, axis=0)]):
        target_geometry = blocks == block
        train_geometry = (r >= cfg.minimum_range_m) & (abs(blocks-block) > cfg.guard_blocks)
        coefficient, calibration = range_relation(r, z, snr, paired & ~ref_plateau, train_geometry)
        power = z-20.*np.log10(np.maximum(r, 1.)/1000.)[None, :]-coefficient*r[None, :]/1000.
        for row in np.flatnonzero(np.any(target_valid & target_geometry[None, :], axis=1)):
            folds += 1
            if folds > cfg.maximum_folds:
                raise ResourceLimit("segmented reference fold budget")
            target = target_valid[row] & target_geometry
            arrays["RV2_SEGMENT_FOLD_ID"][row, target] = folds
            train = paired[row] & train_geometry & ~ref_plateau[row]
            # Candidate topology/old QC are never used to select reference gates.
            states = discover_states(snr[row, train], cfg)
            proposals = []
            models = []
            for lower, upper in states:
                ix = np.flatnonzero(train & (snr[row] >= lower) & (snr[row] < upper))
                model, record = _fit_state(native, cfg, row, ix, blocks, power, values, availability)
                record.update(fold_id=folds, ray=int(row), target_block=int(block),
                              guard_blocks=cfg.guard_blocks, state_interval_db=[lower, upper],
                              range_coefficient_db_per_km=coefficient, calibration=calibration)
                statuses[record["status"]] = statuses.get(record["status"], 0)+1
                if model is not None:
                    model_count += 1
                    model["id"] = model_count
                    record["model_id"] = model_count
                    delta = power[row]-model["intercept"]
                    s = model["snr_bounds"]
                    match = (target & (snr[row] >= lower) & (snr[row] < upper) &
                             (snr[row] >= s[0]-1.) & (snr[row] <= s[1]+1.))
                    if model["require_power_match"]:
                        match &= abs(delta) <= cfg.maximum_power_residual_db
                    if model["family"] == 1:
                        center, phase_bounds, zdr_bounds, rho_bounds = model["polar"]
                        phase = wrap(values["PHIDP"][row]-center)
                        match &= np.logical_and.reduce([availability[k][row] for k in ("PHIDP", "ZDR", "RHOHV")])
                        match &= ((phase >= phase_bounds[0]-.5) & (phase <= phase_bounds[1]+.5) &
                                  (values["ZDR"][row] >= zdr_bounds[0]-.125) & (values["ZDR"][row] <= zdr_bounds[1]+.125) &
                                  (values["RHOHV"][row] >= max(0, rho_bounds[0]-.01)) &
                                  (values["RHOHV"][row] <= min(1, rho_bounds[1]+.01)))
                    proposals.append(match)
                    models.append((model, delta))
                    arrays["RV2_FIT_AVAILABLE_MASK"][row, target] = 1
                raw_record = json.dumps(record, sort_keys=True, separators=(",", ":"), allow_nan=False).encode()
                reference_hash.update(raw_record)
                if records_out is not None:
                    records_out.append(record)
            if not proposals:
                continue
            number = np.sum(proposals, axis=0)
            arrays["RV2_AMBIGUOUS_STATE_MASK"][row, target & (number > 1)] = 1
            calibrated = calibration["status"] == "measured_consistent"
            for match, (model, delta) in zip(proposals, models, strict=True):
                unique = match & (number == 1)
                arrays["RV2_MODEL_ID"][row, unique] = model["id"]
                arrays["RV2_STATE_FAMILY"][row, unique] = model["family"]
                arrays["RV2_SEGMENT_RESIDUAL_DB"][row, unique] = delta[unique]
                arrays["RV2_REFERENCE_MIN_M"][row, unique] = model["reference_min_m"]
                arrays["RV2_REFERENCE_MAX_M"][row, unique] = model["reference_max_m"]
                arrays["RV2_RANGE_TERM_MEASURED_MASK"][row, unique] = calibrated
                if model["family"] == 1 and (calibrated or not cfg.require_measured_range_term):
                    arrays["RV2_SEGMENT_MATCH_MASK"][row, unique] = 1
                else:
                    arrays["RV2_WEAK_MATCH_MASK"][row, unique] = 1
    return arrays, {"reference_folds": folds, "reference_models": model_count,
                    "reference_models_sha256": reference_hash.hexdigest(),
                    "reference_status_counts": statuses, "weak_actions": 0}
