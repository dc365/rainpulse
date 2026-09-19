"""Target/guard-excluded receiver signatures; original uncalibrated hypothesis.

All model membership and parameters depend only on train gates, not object
labels, target values, or final QC. Odd/even reference blocks are cross-checked.
A DBZH/SNR processing relation is not an independent physical vote.
"""
import numpy as np
from .data import array_digest, ResourceLimit
from .geometry import wrap, runs


def local_weather(s, cfg):
    """Conservative protection, never a non-weather test when unavailable."""
    rho, ra = s.moment("RHOHV"); ph, pa = s.moment("PHIDP"); snr, sa = s.moment("SNR")
    ok = s.observed & ra & pa & sa & (rho >= cfg.weather_rho) & (snr >= cfg.minimum_snr_db)
    pair = np.zeros(s.shape, bool)
    pair[:, 1:] = ok[:, 1:] & ok[:, :-1] & (abs(wrap(np.diff(ph, axis=1))) <= 10.)
    out = np.zeros(s.shape, bool)
    for row in range(s.shape[0]):
        for lo, hi in runs(pair[row]):
            if (hi-lo+1)*s.dr >= cfg.minimum_weather_run_m:
                out[row, max(0, lo-1):hi] = True
    return out


def _fit(s, row, indices, bix, cfg):
    f = s.fields
    r = s.ranges[indices]/1000.
    relation = f["DBZH"][row, indices]-f["SNR"][row, indices]-20*np.log10(np.maximum(r, .001))
    if np.ptp(r)*1000 < cfg.minimum_reference_span_m:
        return None
    coef, intercept = np.polyfit(r, relation, 1)
    rel90 = float(np.percentile(abs(relation-coef*r-intercept), 90))
    if not 0 <= coef <= .03 or rel90 > .5:
        return None
    power = f["DBZH"][row, indices]-20*np.log10(np.maximum(r, .001))-coef*r
    mu = float(np.median(power)); sn = float(np.median(f["SNR"][row, indices]))
    block_ids = sorted(set(bix[indices].tolist()))
    smed = np.array([np.median(f["SNR"][row, indices[bix[indices] == b]]) for b in block_ids])
    if (np.percentile(abs(power-mu), 90) > cfg.maximum_source_residual_db or
        np.percentile(abs(smed-np.median(smed)), 90) > cfg.maximum_snr_dispersion_db):
        return None
    groups = []
    for blocks in (block_ids[::2], block_ids[1::2]):
        j = np.isin(bix[indices], blocks)
        if j.sum() < cfg.minimum_samples_per_block:
            return None
        groups.append(float(np.median(power[j])))
    if abs(groups[0]-groups[1]) > cfg.maximum_source_residual_db:
        return None
    ph = f["PHIDP"][row, indices]; rho = f["RHOHV"][row, indices]; zdr = f["ZDR"][row, indices]
    pc = float(np.angle(np.mean(np.exp(1j*np.deg2rad(ph))), deg=True))
    pp90 = float(np.percentile(abs(wrap(ph-pc)), 90))
    zc = float(np.median(zdr)); zp90 = float(np.percentile(abs(zdr-zc), 90))
    # Only phase pairs inside the SAME retained reference block count.
    pairs = (np.diff(indices) == 1) & (np.diff(bix[indices]) == 0)
    inc = wrap(np.diff(ph))[pairs]
    variance = float(1-abs(np.mean(np.exp(1j*np.deg2rad(inc))))) if len(inc) >= 10 else 0.
    noisy = sn >= cfg.minimum_snr_db and np.mean(rho < cfg.noisy_rho) >= .7 and variance >= .3
    coherent = sn >= cfg.coherent_snr_db and pp90 <= cfg.maximum_phase_p90_deg and zp90 <= cfg.maximum_zdr_p90_db
    if noisy == coherent:  # None or competing families: abstain.
        return None
    return {"family": 1 if noisy else 2, "coefficient_db_per_km": float(coef),
            "relation_p90_db": rel90, "power_median_db": mu, "snr_median_db": sn,
            "phase_center_deg": pc, "phase_p90_deg": pp90, "zdr_center_db": zc,
            "zdr_p90_db": zp90, "rho_interval": np.percentile(rho, [5, 95]).tolist(),
            "snr_interval": np.percentile(f["SNR"][row, indices], [5, 95]).tolist(),
            "reference_blocks": block_ids, "reference_group_power_medians": groups,
            "reference_min_m": float(s.ranges[indices].min()), "reference_max_m": float(s.ranges[indices].max()),
            "reference_gates": int(len(indices)), "reference_intervals": [[int(a), int(b)] for a, b in runs(np.isin(np.arange(s.shape[1]), indices))],
            "reference_digest": array_digest({"indices": indices, **{k: f[k][row, indices] for k in ("DBZH","SNR","RHOHV","ZDR","PHIDP")}})}


def fit_sources(s, cfg, candidates):
    names = ("DBZH", "SNR", "RHOHV", "ZDR", "PHIDP")
    fields = {"VOR_SOURCE_MATCH_MASK": np.zeros(s.shape, "uint8"),
              "VOR_SOURCE_FAMILY": np.zeros(s.shape, "uint8"),
              "VOR_MODEL_ID": np.zeros(s.shape, "uint32"),
              "VOR_MODEL_AVAILABLE_MASK": np.zeros(s.shape, "uint8"),
              "VOR_SOURCE_RESIDUAL_DB": np.full(s.shape, np.nan, "float32")}
    if any(k not in s.fields for k in names):
        return fields, [], "INSUFFICIENT_MOMENTS"
    valid = np.logical_and.reduce([s.available[k] for k in names]) & (s.fields["SNR"] >= cfg.minimum_snr_db)
    bix = (s.ranges//cfg.source_block_m).astype(int)
    records = []; trials = 0
    # Geometry nominates targets but never selects reference members.
    for row in np.flatnonzero(np.any(candidates, axis=1)):
        for target_block in np.unique(bix[candidates[row]]):
            trials += 1
            if trials > cfg.maximum_folds:
                raise ResourceLimit("source folds")
            target = np.flatnonzero((bix == target_block) & candidates[row] & valid[row])
            train = np.flatnonzero(valid[row] & (abs(bix-target_block) > cfg.guard_blocks))
            if len(target) == 0 or len(train) < cfg.minimum_reference_samples:
                continue
            # Fixed-gap state segmentation on TRAIN SNR only. No target nearest-state fitting.
            order = train[np.argsort(s.fields["SNR"][row, train], kind="stable")]
            cuts = np.flatnonzero(np.diff(s.fields["SNR"][row, order]) >= cfg.state_separation_db)+1
            groups = np.split(order, cuts)
            if len(groups) > cfg.maximum_states:
                continue
            match_count = np.zeros(len(target), "uint8")
            assigned = np.zeros(len(target), "uint32"); families = np.zeros(len(target), "uint8")
            residuals = np.full(len(target), np.nan)
            for group in groups:
                indices = np.sort(group)
                good_blocks = [b for b in np.unique(bix[indices]) if (bix[indices] == b).sum() >= cfg.minimum_samples_per_block]
                indices = indices[np.isin(bix[indices], good_blocks)]
                if len(good_blocks) < cfg.minimum_reference_blocks or len(indices) < cfg.minimum_reference_samples:
                    continue
                model = _fit(s, row, indices, bix, cfg)
                if model is None:
                    continue
                model.update(id=len(records)+1, ray=int(row), target_block=int(target_block),
                             guard_blocks=cfg.guard_blocks, status="REFERENCE_QUALIFIED",
                             evidence_group="same_receiver_chain", target_in_reference=False)
                records.append(model)
                fields["VOR_MODEL_AVAILABLE_MASK"][row, target] = 1
                rr = s.ranges[target]/1000.
                delta = s.fields["DBZH"][row, target]-20*np.log10(np.maximum(rr,.001))-model["coefficient_db_per_km"]*rr-model["power_median_db"]
                matched = (abs(delta) <= cfg.maximum_source_residual_db)
                snr = s.fields["SNR"][row, target]
                matched &= (snr >= model["snr_interval"][0]-1) & (snr <= model["snr_interval"][1]+1)
                if model["family"] == 2:
                    matched &= abs(wrap(s.fields["PHIDP"][row, target]-model["phase_center_deg"])) <= cfg.maximum_phase_p90_deg
                    matched &= abs(s.fields["ZDR"][row, target]-model["zdr_center_db"]) <= cfg.maximum_zdr_p90_db
                    matched &= (s.fields["RHOHV"][row, target] >= model["rho_interval"][0]-.01)
                else:
                    matched &= s.fields["RHOHV"][row, target] < cfg.noisy_rho
                match_count += matched
                assigned[matched] = model["id"]; residuals[matched] = delta[matched]; families[matched] = model["family"]
            unique = match_count == 1
            fields["VOR_SOURCE_MATCH_MASK"][row, target[unique]] = 1
            fields["VOR_MODEL_ID"][row, target[unique]] = assigned[unique]
            fields["VOR_SOURCE_FAMILY"][row, target[unique]] = families[unique]
            fields["VOR_SOURCE_RESIDUAL_DB"][row, target[unique]] = residuals[unique]
    return fields, records, "EVALUATED" if records else "INSUFFICIENT_REFERENCE"
