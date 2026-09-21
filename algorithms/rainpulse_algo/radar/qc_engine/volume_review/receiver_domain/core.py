"""Independent moment supports, held-out fits, gate-local source qualification.

No old-QC, station, angle-of-interest or pixel input. SNR is not SQI. A processing
relation is not an independent physical vote. This model only covers stable,
coherent receiver signatures; other source families remain with existing paths.
"""
from collections import Counter
from dataclasses import dataclass
import numpy as np
from ..data import ResourceLimit, array_digest, checked_mask
from ..geometry import wrap, runs

MOMENTS = ("DBZH", "SNR", "RHOHV", "ZDR", "PHIDP")
DTYPES = {
    **{k: "uint8" for k in ("OBSERVED_MASK", "FULL_MATCH_MASK", "PARTIAL_MATCH_MASK",
        "MODEL_AVAILABLE_MASK", "TARGET_POLAR_CONFLICT_MASK", "TARGET_POWER_MATCH_MASK",
        "TARGET_POLAR_COUNT", "TARGET_TAIL_MASK", "TARGET_SIDE_CONFLICT_MASK", "SOURCE_MASK", "MIXED_MASK",
        "INDEPENDENT_WEATHER_MASK", "LOCAL_COHERENCE_MASK", "UNKNOWN_PROTECTION_MASK",
        "LOCAL_REVIEWED_MASK", "STATE")},
    "MODEL_ID": "uint32", "RESIDUAL_DB": "float32", "SNR_DELTA_DB": "float32",
}


SEGMENT_DTYPES = {
    "SEGMENT_REFERENCE_MASK": "uint8", "SEGMENT_MATCH_COUNT": "uint8",
    "SEGMENT_AMBIGUOUS_MASK": "uint8", "SEGMENT_SIDE_MEASURED_MASK": "uint8",
    "SEGMENT_REFERENCE_DISTANCE_M": "float32",
}


def evidence_dtypes(cfg):
    extra = {}
    if cfg.source_family is not None:
        from .source_family import DTYPES as FAMILY_DTYPES
        extra = FAMILY_DTYPES
    return {**DTYPES, **(SEGMENT_DTYPES if cfg.segment_reference is not None else {}), **extra}


@dataclass(frozen=True)
class Evidence:
    arrays: dict
    models: list
    summary: dict


def domains(s, cfg):
    f, a = {}, {}
    for k in MOMENTS:
        v, ok = s.moment(k)
        f[k] = v
        a[k] = ok & np.isfinite(v) & s.good[:, None]
    a["DBZH"] &= (f["DBZH"] >= -32) & (f["DBZH"] <= 80)
    # Numeric tails are NOT vendor censor semantics. They cannot train precise DR/ZDR.
    a["ZDR"] &= abs(f["ZDR"]) < cfg.maximum_abs_reference_zdr_db
    return f, a


def supported_indices(indices, blocks, minimum):
    if not len(indices):
        return indices
    ids, counts = np.unique(blocks[indices], return_counts=True)
    return indices[np.isin(blocks[indices], ids[counts >= minimum])]


def intervals(indices, n):
    mask = np.zeros(n, bool)
    mask[indices] = True
    return [[int(lo), int(hi)] for lo, hi in runs(mask)]


def fit_fold(s, row, block, cfg, prepared=None, restrict_snr_to_dbzh=False):
    """All membership, shoulders, statistics and digests exclude target AND guards."""
    f, a = domains(s, cfg) if prepared is None else prepared
    r, az = s.ranges, s.azimuth
    bix = (r // cfg.block_m).astype(int)
    train = (r >= cfg.minimum_range_m) & (abs(bix-block) > cfg.guard_blocks)
    sa = a["SNR"] if not restrict_snr_to_dbzh else a["SNR"] & a["DBZH"]
    idx = supported_indices(np.flatnonzero(train & sa[row]), bix, cfg.minimum_samples_per_reference_block)
    if (len(idx) < cfg.minimum_pair_samples or len(np.unique(bix[idx])) < cfg.minimum_reference_blocks
            or np.ptp(r[idx]) < cfg.minimum_reference_span_m):
        return None, "SNR_SUPPORT"
    sn = f["SNR"]
    mu = float(np.median(sn[row, idx]))
    spread = float(np.percentile(abs(sn[row, idx]-mu), 90))
    if mu < cfg.minimum_snr_db or spread > cfg.maximum_snr_p90_db:
        return None, "SNR_NONSTATIONARY"
    shoulders = []
    shoulder_data = {}
    for direction in (-1, 1):
        found = None
        current = row
        for step in range(1, s.shape[0]):
            nxt = (row+direction*step) % s.shape[0]
            edge = current if direction == 1 else nxt
            if s.gap_after[edge] or not s.good[nxt]:
                break
            angle = abs(float(wrap(az[nxt]-az[row])))
            if angle > cfg.maximum_flank_angle_deg:
                break
            jj = supported_indices(idx[sa[nxt, idx]], bix, cfg.minimum_samples_per_reference_block)
            if (len(jj) >= cfg.minimum_pair_samples and
                    np.ptp(r[jj]) >= cfg.minimum_reference_span_m):
                contrast = float(np.median(sn[row, jj]-sn[nxt, jj]))
                if contrast >= cfg.angular_contrast_db:
                    found = {"ray": int(nxt), "angle_offset_deg": float(wrap(az[nxt]-az[row])),
                             "contrast_db": contrast, "samples": int(len(jj)),
                             "blocks": np.unique(bix[jj]).tolist(), "intervals": intervals(jj, s.shape[1])}
                    shoulder_data[str(direction)+"_indices"] = jj
                    shoulder_data[str(direction)+"_snr"] = sn[nxt, jj]
                    break
            current = nxt
        if found is None:
            return None, "NO_MEASURED_SNR_SHOULDERS"
        shoulders.append(found)
    if shoulders[0]["ray"] == shoulders[1]["ray"]:
        return None, "REUSED_SHOULDER"
    pair = idx[a["DBZH"][row, idx] & (abs(sn[row, idx]-mu) <= cfg.maximum_snr_p90_db)]
    pair = supported_indices(pair, bix, cfg.minimum_samples_per_reference_block)
    if (len(pair) < cfg.minimum_pair_samples or len(np.unique(bix[pair])) < cfg.minimum_pair_blocks
            or np.ptp(r[pair]) < cfg.minimum_pair_span_m):
        return None, "PAIRED_SUPPORT"
    x = r[pair]/1000.
    y = f["DBZH"][row, pair]-sn[row, pair]-20*np.log10(x)
    beta, offset = np.polyfit(x, y, 1)
    error = float(np.percentile(abs(y-beta*x-offset), 90))
    if not 0 <= beta <= .03 or error > cfg.maximum_relation_error_db:
        return None, "PAIRED_RELATION"
    block_ids = np.unique(bix[pair])
    centers = []
    for group in (block_ids[::2], block_ids[1::2]):
        use = np.isin(bix[pair], group)
        if use.sum() < cfg.minimum_samples_per_reference_block:
            return None, "PAIR_SPLIT_SUPPORT"
        centers.append(float(np.median(y[use]-beta*x[use])))
    if abs(centers[0]-centers[1]) > cfg.maximum_relation_error_db:
        return None, "PAIR_SPLIT_CONFLICT"
    pol = a["RHOHV"][row] & a["ZDR"][row] & a["PHIDP"][row]
    pp = supported_indices(pair[pol[pair]], bix, cfg.minimum_samples_per_reference_block)
    if len(pp) < cfg.minimum_polar_samples or len(np.unique(bix[pp])) < 2:
        return None, "POLAR_REFERENCE"
    phase = float(np.angle(np.mean(np.exp(1j*np.deg2rad(f["PHIDP"][row, pp]))), deg=True))
    ph90 = float(np.percentile(abs(wrap(f["PHIDP"][row, pp]-phase)), 90))
    zdr = float(np.median(f["ZDR"][row, pp]))
    zd90 = float(np.percentile(abs(f["ZDR"][row, pp]-zdr), 90))
    if ph90 > cfg.maximum_phase_p90_deg or zd90 > cfg.maximum_zdr_p90_db:
        return None, "NONCOHERENT_REFERENCE"
    digest = array_digest({"snr_indices": idx, "pair_indices": pair, "polar_indices": pp,
        "snr": sn[row, idx], "pair_dbzh": f["DBZH"][row, pair],
        **{k: f[k][row, pp] for k in ("PHIDP", "ZDR", "RHOHV")}, **shoulder_data})
    return {"ray": int(row), "target_block": int(block), "guard_blocks": cfg.guard_blocks,
        "snr_median_db": mu, "snr_p90_db": spread,
        "snr_reference_without_dbzh": int((~a["DBZH"][row, idx]).sum()),
        "snr_samples": int(len(idx)), "pair_samples": int(len(pair)), "polar_samples": int(len(pp)),
        "reference_min_m": float(r[idx].min()), "reference_max_m": float(r[idx].max()),
        "snr_blocks": np.unique(bix[idx]).tolist(), "pair_blocks": block_ids.tolist(),
        "polar_blocks": np.unique(bix[pp]).tolist(), "snr_intervals": intervals(idx, s.shape[1]),
        "pair_intervals": intervals(pair, s.shape[1]), "polar_intervals": intervals(pp, s.shape[1]),
        "beta_db_per_km": float(beta), "offset_db": float(offset), "pair_error_db": error,
        "pair_split_centers": centers, "phase_center_deg": phase, "phase_p90_deg": ph90,
        "zdr_center_db": zdr, "zdr_p90_db": zd90,
        "rho_bounds": np.percentile(f["RHOHV"][row, pp], [5, 95]).tolist(),
        "shoulders": shoulders, "reference_sha256": digest,
        "reference_policy": "raw_separate_support_target_guard_excluded"}, "FITTED"


def evaluate(s, cfg, *, independent_weather=None, local_coherence=None,
             unknown_protection=None, restrict_snr_to_dbzh=False):
    """Return source compatibility and joint state; disposition is separate.

    No whole-ray target-dependent prefilter: a fold can exist even when target
    values/availability change. Candidate target membership itself stays measured.
    """
    if np.prod(s.shape) > cfg.maximum_sweep_gates:
        raise ResourceLimit("receiver-domain sweep budget exceeded")
    f, a = domains(s, cfg)
    obs = a["DBZH"]
    domain = obs & (s.ranges[None, :] >= cfg.minimum_range_m) & (f["DBZH"] >= cfg.no_rain_below_dbz)
    def protection(value, name):
        return (np.zeros(s.shape, bool) if value is None else checked_mask(value, s.shape, name)) & obs
    hard = protection(independent_weather, "independent weather")
    local = protection(local_coherence, "local coherence")
    unknown = protection(unknown_protection, "unattributed protection")
    out = {"RDR_"+k: np.full(s.shape, np.nan if dt == "float32" else 0, dt) for k, dt in evidence_dtypes(cfg).items()}
    out["RDR_OBSERVED_MASK"] = obs.astype("uint8")
    out["RDR_INDEPENDENT_WEATHER_MASK"] = hard.astype("uint8")
    out["RDR_LOCAL_COHERENCE_MASK"] = local.astype("uint8")
    out["RDR_UNKNOWN_PROTECTION_MASK"] = unknown.astype("uint8")
    records = []; failures = Counter(); trials = 0
    segment_failures = Counter(); state_trials = [0]
    bix = (s.ranges//cfg.block_m).astype(int)
    if "SNR" in s.fields:
        for row in np.flatnonzero(s.good & domain.any(axis=1)):
            for block in np.unique(bix[domain[row]]):
                trials += 1
                if trials > cfg.maximum_folds:
                    raise ResourceLimit("receiver-domain fold budget exceeded; no partial result")
                model, status = fit_fold(s, row, int(block), cfg, (f, a), restrict_snr_to_dbzh)
                if model is None:
                    failures[status] += 1
                    if cfg.segment_reference is not None:
                        from .segment_reference import fit_segment_models, project_segment_models
                        models, diagnostics = fit_segment_models(s, row, int(block), cfg,
                            prepared=(f, a), state_trials=state_trials,
                            restrict_snr_to_dbzh=restrict_snr_to_dbzh)
                        segment_failures.update(diagnostics)
                        if models:
                            project_segment_models(s, row, int(block), cfg, (f, a),
                                domain, models, records, out)
                    continue
                records.append({"id": len(records)+1, **model})
                j = np.flatnonzero(domain[row] & (bix == block) & a["SNR"][row])
                predicted = (model["snr_median_db"]+20*np.log10(s.ranges[j]/1000.)+
                             model["beta_db_per_km"]*s.ranges[j]/1000.+model["offset_db"])
                delta = f["DBZH"][row, j]-predicted
                ds = f["SNR"][row, j]-model["snr_median_db"]
                power = (abs(delta) <= cfg.maximum_target_residual_db) & (abs(ds) <= cfg.maximum_snr_p90_db)
                count = np.zeros(len(j), "uint8"); compatible = np.ones(len(j), bool)
                tail = (s.available.get("ZDR", np.zeros(s.shape, bool))[row, j] &
                        (abs(f["ZDR"][row, j]) >= cfg.maximum_abs_reference_zdr_db))
                for k in ("PHIDP", "ZDR", "RHOHV"):
                    available = a[k][row, j]; count += available
                    value = f[k][row, j]
                    if k == "PHIDP":
                        ok = abs(wrap(value-model["phase_center_deg"])) <= cfg.target_phase_tolerance_deg
                    elif k == "ZDR":
                        ok = abs(value-model["zdr_center_db"]) <= cfg.target_zdr_tolerance_db
                    else:
                        ok = (value >= max(0, model["rho_bounds"][0]-.05)) & (value <= min(1, model["rho_bounds"][1]+.05))
                    compatible &= ~available | ok
                side_conflict = np.zeros(len(j), bool)
                for side in model["shoulders"]:
                    sr = side["ray"]
                    side_conflict |= a["SNR"][sr, j] & ((f["SNR"][row, j]-f["SNR"][sr, j]) < cfg.angular_contrast_db/2.)
                full = power & compatible & (count == 3) & ~tail
                # A numeric tail is not missing-at-random: never promote it via partial mode.
                partial = power & compatible & (count < 3) & ~tail
                for key, value in (("FULL_MATCH_MASK", full), ("PARTIAL_MATCH_MASK", partial),
                    ("MODEL_AVAILABLE_MASK", True), ("MODEL_ID", len(records)), ("RESIDUAL_DB", delta),
                    ("SNR_DELTA_DB", ds), ("TARGET_POLAR_COUNT", count), ("TARGET_SIDE_CONFLICT_MASK", side_conflict),
                    ("TARGET_POLAR_CONFLICT_MASK", ~compatible), ("TARGET_POWER_MATCH_MASK", power), ("TARGET_TAIL_MASK", tail)):
                    out["RDR_"+key][row, j] = value
    family_report = None
    if cfg.source_family is not None:
        from .source_family import extend
        family_report = extend(s, cfg, (f, a), domain, out, records)
    full = out["RDR_FULL_MATCH_MASK"] == 1
    partial = out["RDR_PARTIAL_MATCH_MASK"] == 1
    side_conflict = out["RDR_TARGET_SIDE_CONFLICT_MASK"] == 1
    blocked = hard | unknown
    local_conflict = local & (cfg.local_policy == "retain_conflict")
    source = full & ~blocked & ~local_conflict & ~side_conflict
    mixed = (full | partial) & (blocked | local | side_conflict)
    state = np.where(obs, 1, 0).astype("uint8")
    state[partial] = 3; state[full] = 2; state[mixed] = 4; state[blocked] = 5; state[source] = 2
    out["RDR_SOURCE_MASK"] = source.astype("uint8")
    out["RDR_MIXED_MASK"] = (mixed & ~source).astype("uint8")
    out["RDR_LOCAL_REVIEWED_MASK"] = (source & local).astype("uint8")
    out["RDR_STATE"] = state
    if cfg.source_family is not None:
        from .source_family import finish
        family_report.update(finish(out, cfg))
    return Evidence(out, records, {"status": "EVALUATED" if "SNR" in s.fields else "MISSING_SNR",
        "config_sha256": cfg.digest, "fold_trials": trials, "failure_counts": dict(failures),
        "models": len(records), "full_matches": int(full.sum()), "partial_matches": int(partial.sum()),
        "source_qualified": int(source.sum()), "mixed": int(out["RDR_MIXED_MASK"].sum()),
        "local_reviewed": int(out["RDR_LOCAL_REVIEWED_MASK"].sum()), "confirmed_gates": 0,
        "filled_gates": 0, "operational_eligible": False,
        "family": "coherent_receiver_only", "scores_are_probabilities": False,
        **({"source_family": family_report} if family_report is not None else {}),
        **({"segment_reference": {
            "version": cfg.segment_reference.version, "mode": cfg.segment_reference.mode,
            "state_trials": state_trials[0], "failure_counts": dict(segment_failures),
            "models": sum(m.get("reference_route") == "finite_receiver_state" for m in records),
            "full_matches": int((full & (out["RDR_SEGMENT_REFERENCE_MASK"] == 1)).sum()),
            "source_qualified": int((source & (out["RDR_SEGMENT_REFERENCE_MASK"] == 1)).sum()),
            "partial_matches": int((partial & (out["RDR_SEGMENT_REFERENCE_MASK"] == 1)).sum()),
            "ambiguous_gates": int(out["RDR_SEGMENT_AMBIGUOUS_MASK"].sum()),
        }} if cfg.segment_reference is not None else {})})


def abstained(s, cfg, reason):
    """Complete zero-action evidence after a resource limit, never partial results."""
    _, a = domains(s, cfg)
    obs = a["DBZH"]
    out = {"RDR_"+k: np.full(s.shape, np.nan if dt == "float32" else 0, dt) for k,dt in evidence_dtypes(cfg).items()}
    out["RDR_OBSERVED_MASK"] = obs.astype("uint8")
    out["RDR_STATE"] = obs.astype("uint8")
    return Evidence(out, [], {"status":"RESOURCE_ABSTAINED", "reason":reason,
        "config_sha256":cfg.digest, "models":0, "full_matches":0, "partial_matches":0,
        "source_qualified":0, "mixed":0, "local_reviewed":0, "confirmed_gates":0, "filled_gates":0})
