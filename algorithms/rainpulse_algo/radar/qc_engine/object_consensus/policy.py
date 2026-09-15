"""Explicit audit/quarantine policy. Statistical association never confirms RFI."""
from dataclasses import dataclass, asdict
import numpy as np
from .config import Policy, digest
from .data import array_sha

# Matches the existing Action values, verified again by the adapter at runtime.
KEEP, DOWNWEIGHT, REJECT, MISSING = 0, 1, 2, 3


@dataclass
class Outcome:
    arrays: dict
    proposed_quarantine: np.ndarray
    added_quarantine: np.ndarray
    summary: dict


def validate_baseline(b, observed):
    required = ("QC_ACTION", "QPE_ELIGIBLE_MASK", "RFI_QUARANTINE_MASK", "QUALITY_INDEX", "DBZH_USABLE")
    for name in required:
        if name not in b or b[name].shape != observed.shape:
            raise ValueError(f"missing/invalid baseline {name}")
    a, e, q, quality = b["QC_ACTION"], b["QPE_ELIGIBLE_MASK"], b["RFI_QUARANTINE_MASK"], b["QUALITY_INDEX"]
    if not np.isin(a, [KEEP, DOWNWEIGHT, REJECT, MISSING]).all() or not np.array_equal(a == MISSING, ~observed):
        raise ValueError("baseline observation/action identity mismatch")
    for name in ("QPE_ELIGIBLE_MASK", "RFI_QUARANTINE_MASK"):
        if not np.isin(b[name], [0,1]).all():
            raise ValueError("nonbinary baseline mask")
    if np.any((e == 1) & (~observed | (a == REJECT) | (q == 1))) or np.any((q == 1) & (a != DOWNWEIGHT)):
        raise ValueError("baseline eligibility/quarantine mismatch")
    if np.any(observed & ~np.isfinite(quality)) or np.isinf(quality).any() or np.any((quality < 0) | (quality > 1)):
        raise ValueError("invalid baseline quality")
    u = b["DBZH_USABLE"]
    if np.any(~np.isfinite(u[e == 1])) or np.any(~np.isnan(u[e != 1])):
        raise ValueError("baseline quantitative value/eligibility mismatch")
    if "business_visible" in b:
        if not np.isin(b["business_visible"], [0,1]).all() or np.any(b["business_visible"] & (e != 1)):
            raise ValueError("baseline display visibility contradicts eligibility")
    for name, value in b.items():
        if value.shape != observed.shape:
            raise ValueError(f"baseline geometry differs: {name}")


def baseline_from_npz(d):
    names = ("DBZH_QC", "DBZH_USABLE", "QC_ACTION", "QPE_ELIGIBLE_MASK",
             "RFI_QUARANTINE_MASK", "QUALITY_INDEX", "business_visible", "QC_FLAGS",
             "RFI_RISK_STATE", "REFLECTIVITY_TRUST_MASK", "RHOHV_TRUST_MASK",
             "ZDR_TRUST_MASK", "PHIDP_TRUST_MASK", "VR_TRUST_MASK", "SW_TRUST_MASK", "SNR_TRUST_MASK")
    return {name: np.asarray(d["baseline_"+name]) for name in names if "baseline_"+name in d}


def apply_policy(raw, evidence, baseline, policy=Policy(), *, low_quality_flag=None):
    if evidence.identity["input_sha256"] != raw.identity:
        raise ValueError("evidence and raw identity differ")
    expected = digest({"identity": {k:v for k,v in evidence.identity.items() if k != "evidence_sha256"},
                       "arrays": {k:array_sha(v) for k,v in sorted(evidence.arrays.items())},
                       "folds": evidence.folds})
    if expected != evidence.identity["evidence_sha256"]:
        raise ValueError("evidence content changed after inference")
    observed = raw.observed
    validate_baseline(baseline, observed)
    # Input is never mutated, including disabled/audit mode.
    out = {name: value.copy() for name, value in baseline.items()}
    values = evidence.arrays
    proposal = (values["state"] == 5) & observed
    if not policy.allow_coherent_quarantine:
        proposal &= values["family_code"] != 2
    if policy.require_bracketed_reference:
        proposal &= values["bracketed_reference_mask"] == 1
    old_reject = baseline["QC_ACTION"] == REJECT
    oldq = baseline["RFI_QUARANTINE_MASK"] == 1
    eligible = baseline["QPE_ELIGIBLE_MASK"] == 1
    candidates = proposal & ~old_reject & ~oldq
    new_loss = candidates & eligible
    fraction = float(new_loss.sum()/max(1, int(eligible.sum())))
    status = "audit_baseline_unchanged"
    added = np.zeros(observed.shape, bool)
    if policy.mode == "experiment_quarantine":
        if fraction > policy.maximum_new_eligible_loss_fraction:
            status = "blocked_budget_whole_cut_reverted"
        else:
            status = "experimental_quarantine_applied_not_confirmed_rfi"
            added = candidates
            out["QC_ACTION"][added] = DOWNWEIGHT
            out["RFI_QUARANTINE_MASK"][added] = 1
            out["QPE_ELIGIBLE_MASK"][added] = 0
            out["QUALITY_INDEX"][added] = np.minimum(out["QUALITY_INDEX"][added], policy.quarantine_quality)
            out["DBZH_USABLE"][added] = np.nan
            if "DBZH_QC" in out:
                out["DBZH_QC"][added] = np.nan
            if "business_visible" in out:
                out["business_visible"][added] = False
            if "RFI_RISK_STATE" in out:
                out["RFI_RISK_STATE"][added] = 2
            for name in out:
                if name.endswith("_TRUST_MASK"):
                    out[name][added] = 0
            if "QC_FLAGS" in out:
                if low_quality_flag is None or low_quality_flag <= 0 or low_quality_flag & (low_quality_flag-1):
                    raise ValueError("versioned LOW_QUALITY flag required for production Decision")
                out["QC_FLAGS"][added] |= np.asarray(low_quality_flag, dtype=out["QC_FLAGS"].dtype)
    validate_baseline(out, observed)
    if np.any(added & ~observed) or np.any((out["QPE_ELIGIBLE_MASK"] == 1) & ~eligible):
        raise AssertionError("policy created/restored observations")
    baseline_id = digest({k: array_sha(v) for k, v in sorted(baseline.items())})
    summary = {"schema": "rainpulse.object-consensus.outcome.v1", "mode": policy.mode, "status": status,
               "model_evidence_sha256": evidence.identity["evidence_sha256"], "baseline_sha256": baseline_id,
               "policy": asdict(policy), "policy_sha256": digest(asdict(policy)),
               "proposed_quarantine_gates": int(proposal.sum()), "confirmed_additions": 0,
               "added_quarantine_gates": int(added.sum()), "eligible_before": int(eligible.sum()),
               "eligible_loss_if_applied": int(new_loss.sum()), "eligible_loss_fraction_if_applied": fraction,
               "eligible_after": int(out["QPE_ELIGIBLE_MASK"].sum()),
               "raw_values_changed": False, "operational_eligible": False,
               "precision": None, "recall": None, "weather_false_removal_rate": None,
               "truth_status": "no_independent_weather_labels"}
    summary["outcome_sha256"] = digest({"identity": summary, "arrays": {k: array_sha(v) for k,v in sorted(out.items())}})
    return Outcome(out, proposal, added, summary)
