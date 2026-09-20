"""Finite receiver-state references, used only when the legacy whole-ray fit fails.

State discovery, shoulders and parameters use original samples OUTSIDE the target
and guards. Low-state observations are not called missing or no-rain. No previous
QC mask, target amplitude, angle of interest, screenshot or station ID selects a
state. Reappearing measured targets must pass their own polar/side/distance tests.
"""
from collections import Counter
from types import SimpleNamespace
import numpy as np
from ..data import ResourceLimit, array_digest
from ..geometry import wrap
from .core import domains, fit_fold, supported_indices


def _indices(intervals):
    return np.concatenate([np.arange(lo, hi, dtype="int64") for lo, hi in intervals])


def _split_prediction(s, row, model, f, cfg):
    """Fit on alternating retained blocks, predict the OTHER blocks, both ways."""
    ii = _indices(model["pair_intervals"])
    blocks = (s.ranges[ii] // cfg.block_m).astype(int)
    unique = np.unique(blocks)
    x = s.ranges[ii] / 1000.
    y = f["DBZH"][row, ii] - f["SNR"][row, ii] - 20. * np.log10(x)
    records = []
    for ids in (unique[::2], unique[1::2]):
        train = np.isin(blocks, ids)
        if (train.sum() < cfg.minimum_samples_per_reference_block
                or (~train).sum() < cfg.minimum_samples_per_reference_block
                or np.ptp(x[train]) * 1000 < cfg.segment_reference.crosscheck_minimum_span_m):
            return None
        beta, offset = np.polyfit(x[train], y[train], 1)
        residual = float(np.percentile(abs(y[~train] - beta*x[~train] - offset), 90))
        if not 0 <= beta <= .03 or residual > cfg.maximum_relation_error_db:
            return None
        records.append({"train_blocks": ids.tolist(), "held_out_blocks": np.unique(blocks[~train]).tolist(),
                        "beta_db_per_km": float(beta), "offset_db": float(offset),
                        "held_out_p90_db": residual})
    return records


def fit_segment_models(s, row, block, cfg, *, prepared=None, state_trials=None,
                       restrict_snr_to_dbzh=False):
    """Reuse strict fit_fold on train-defined state supports with physical limits.

    Multiple states may pass reference checks; targets are tested against ALL of
    them later. No best/nearest target selects its own reference. A continuous
    broad SNR distribution is deliberately not broken into arbitrary little bins.
    """
    seg = cfg.segment_reference
    if seg is None:
        return [], {"DISABLED": 1}
    f, a = domains(s, cfg) if prepared is None else prepared
    failures = Counter()
    r, sn = s.ranges, f["SNR"][row]
    bix = (r // cfg.block_m).astype(int)
    train = (r >= seg.reference_minimum_range_m) & (abs(bix - block) > cfg.guard_blocks)
    usable = a["SNR"][row] & train
    if restrict_snr_to_dbzh:
        usable &= a["DBZH"][row]
    indices = np.flatnonzero(usable & (sn >= cfg.minimum_snr_db))
    if len(indices) < cfg.minimum_pair_samples:
        return [], {"STATE_SUPPORT": 1}
    ordered = indices[np.argsort(sn[indices], kind="stable")]
    groups = np.split(ordered, np.flatnonzero(np.diff(sn[ordered]) >= seg.state_separation_db) + 1)
    groups = [g for g in groups if len(g) >= cfg.minimum_samples_per_reference_block]
    if len(groups) > seg.maximum_states:
        return [], {"TOO_MANY_STATES": 1}
    # fit_fold consumes only numerical fitting limits. Keep the public parent
    # config immutable; these lower-level physical limits are versioned in seg.
    limits = cfg.model_dump(mode="python")
    limits.update(minimum_range_m=seg.reference_minimum_range_m,
                  minimum_reference_span_m=seg.minimum_reference_span_m,
                  minimum_reference_blocks=seg.minimum_reference_blocks)
    fit_limits = SimpleNamespace(**limits)
    models = []
    for state_number, group in enumerate(groups, 1):
        if state_trials is not None:
            state_trials[0] += 1
            if state_trials[0] > seg.maximum_state_trials:
                raise ResourceLimit("receiver segment state trial budget; no partial results")
        ii = supported_indices(np.sort(group), bix, cfg.minimum_samples_per_reference_block)
        if (len(ii) < cfg.minimum_pair_samples or len(ii)*s.dr < seg.minimum_reference_support_m
                or len(np.unique(bix[ii])) < seg.minimum_reference_blocks
                or np.ptp(r[ii]) < seg.minimum_reference_span_m):
            failures["STATE_SUPPORT"] += 1
            continue
        center = float(np.median(sn[ii]))
        if np.percentile(abs(sn[ii]-center), 90) > cfg.maximum_snr_p90_db:
            failures["STATE_NONSTATIONARY"] += 1
            continue
        # Only center-row SNR support changes. Side observations remain original;
        # fit_fold itself excludes target AND guard for every side statistic.
        reference_a = dict(a)
        reference_a["SNR"] = a["SNR"].copy()
        reference_a["SNR"][row] = False
        reference_a["SNR"][row, ii] = True
        model, status = fit_fold(s, row, block, fit_limits, (f, reference_a), False)
        if model is None:
            failures[status] += 1
            continue
        checks = _split_prediction(s, row, model, f, cfg)
        if checks is None:
            failures["HELD_OUT_RELATION_CONFLICT"] += 1
            continue
        paired = _indices(model["pair_intervals"])
        if len(paired)*s.dr < seg.minimum_reference_support_m:
            failures["PAIRED_PHYSICAL_SUPPORT"] += 1
            continue
        # Identity includes even rejected SNR states in the train-domain discovery
        # pool: they can affect state partitioning. Target/guard never contribute.
        pool = np.flatnonzero(usable)
        model.update(reference_route="finite_receiver_state", state_number=state_number,
            state_discovery_sha256=array_digest({"indices": pool, "snr": sn[pool]}),
            state_partition_bounds_db=[float(sn[group].min()), float(sn[group].max())],
            reference_policy="train_only_state_target_guard_excluded_bounded_original_targets",
            reference_limits={"minimum_range_m": seg.reference_minimum_range_m,
                              "minimum_blocks": seg.minimum_reference_blocks,
                              "minimum_span_m": seg.minimum_reference_span_m,
                              "minimum_support_m": seg.minimum_reference_support_m},
            reference_cross_predictions=checks,
            nearest_reference_kind="actual_paired_observation_not_bbox")
        models.append(model)
    return models, dict(failures)


def project_segment_models(s, row, block, cfg, prepared, domain, models, records, out):
    """Project original measured targets; a unique compatible state is mandatory."""
    f, a = prepared
    j = np.flatnonzero(domain[row] & ((s.ranges//cfg.block_m).astype(int) == block) & a["SNR"][row])
    base_id = len(records)
    records.extend({"id": base_id+i+1, **m} for i, m in enumerate(models))
    if not len(j):
        return
    values = []
    for i, model in enumerate(models):
        r = s.ranges[j]
        prediction = (model["snr_median_db"] + 20.*np.log10(r/1000.)
                      + model["beta_db_per_km"]*r/1000. + model["offset_db"])
        delta = f["DBZH"][row, j]-prediction
        ds = f["SNR"][row, j]-model["snr_median_db"]
        pi = _indices(model["pair_intervals"])
        p = np.searchsorted(s.ranges[pi], r)
        distance = np.minimum(abs(r-s.ranges[pi[np.clip(p, 0, len(pi)-1)]]),
                              abs(r-s.ranges[pi[np.clip(p-1, 0, len(pi)-1)]]))
        power = ((abs(delta) <= cfg.maximum_target_residual_db)
                 & (abs(ds) <= cfg.maximum_snr_p90_db)
                 & (distance <= cfg.segment_reference.maximum_reference_distance_m))
        count = np.zeros(len(j), "uint8"); compatible = np.ones(len(j), bool)
        tail = (s.available.get("ZDR", np.zeros(s.shape, bool))[row, j]
                & (abs(f["ZDR"][row, j]) >= cfg.maximum_abs_reference_zdr_db))
        for k in ("PHIDP", "ZDR", "RHOHV"):
            valid = a[k][row, j]; v = f[k][row, j]; count += valid
            if k == "PHIDP": ok = abs(wrap(v-model["phase_center_deg"])) <= cfg.target_phase_tolerance_deg
            elif k == "ZDR": ok = abs(v-model["zdr_center_db"]) <= cfg.target_zdr_tolerance_db
            else: ok = ((v >= max(0., model["rho_bounds"][0]-.05))
                        & (v <= min(1., model["rho_bounds"][1]+.05)))
            compatible &= ~valid | ok
        measured = np.ones(len(j), bool); side_conflict = np.zeros(len(j), bool)
        for side in model["shoulders"]:
            sr = side["ray"]; valid = a["SNR"][sr, j]
            measured &= valid
            # Stronger than the long-source route: BOTH target flanks must exist
            # and show the FULL configured contrast. Unknown never means clear.
            side_conflict |= ~valid | ((f["SNR"][row, j]-f["SNR"][sr, j]) < cfg.angular_contrast_db)
        values.append({"FULL_MATCH_MASK": power & compatible & (count == 3) & ~tail,
            "PARTIAL_MATCH_MASK": power & compatible & (count < 3) & ~tail,
            "MODEL_AVAILABLE_MASK": np.ones(len(j), bool), "MODEL_ID": np.full(len(j), base_id+i+1),
            "RESIDUAL_DB": delta, "SNR_DELTA_DB": ds, "TARGET_POLAR_COUNT": count,
            "TARGET_SIDE_CONFLICT_MASK": side_conflict, "TARGET_POLAR_CONFLICT_MASK": ~compatible,
            "TARGET_POWER_MATCH_MASK": power, "TARGET_TAIL_MASK": tail,
            "SEGMENT_SIDE_MEASURED_MASK": measured, "SEGMENT_REFERENCE_DISTANCE_M": distance})
    match = np.stack([v["FULL_MATCH_MASK"] | v["PARTIAL_MATCH_MASK"] for v in values])
    number = match.sum(axis=0).astype("uint8")
    # For unmatched/ambiguous diagnostics select the FIRST TRAIN-defined model,
    # not whichever target residual happens to be smallest. Both get no match.
    selected = np.where(number == 1, np.argmax(match, axis=0), 0)
    for key in values[0]:
        v = np.stack([x[key] for x in values])[selected, np.arange(len(j))]
        if key in ("FULL_MATCH_MASK", "PARTIAL_MATCH_MASK"):
            v &= number == 1
        out["RDR_"+key][row, j] = v
    out["RDR_SEGMENT_REFERENCE_MASK"][row, j] = 1
    out["RDR_SEGMENT_MATCH_COUNT"][row, j] = number
    out["RDR_SEGMENT_AMBIGUOUS_MASK"][row, j] = number > 1
