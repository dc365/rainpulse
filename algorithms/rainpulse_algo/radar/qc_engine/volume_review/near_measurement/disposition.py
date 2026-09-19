"""One-way exact delta, with distinct nonmet and CR-only uncertainty actions."""
import numpy as np
from .core import binary
from ..disposition import DERIVED_FIELDS, derived_invalidation

CR = "REFLECTIVITY_ELIGIBLE_FOR_CR"
FIXED = {"QC_ACTION", "QC_FLAGS", "QUALITY_INDEX", "LOW_QUALITY_MASK", "QI_METEO", "QI_INTERFERENCE",
         "QPE_ELIGIBLE_MASK", "DBZH_USABLE", "P2_ADMIN_PENALTY_REMOVED_MASK", CR,
         "CR_UNCERTAIN_MASK", "CR_QUALIFICATION_REASON"}


def mutable_names(group):
    return sorted(k for k in group if not k.startswith(("NMR_", "VOR_")) and
                  (k in FIXED or k in DERIVED_FIELDS or k.endswith("_TRUST_MASK")))


def apply(group, evidence, cfg, *, low_quality_flag):
    if any(k.startswith("NMR_") for k in group):
        raise ValueError("near review already applied; use the frozen pre-stage input")
    a = {k: np.array(v, copy=True) for k, v in group.items()}
    shape = a["DBZH_RAW"].shape
    obs = binary(a["VALID_MASK"], shape, "VALID_MASK")
    trust = binary(a["REFLECTIVITY_TRUST_MASK"], shape, "TRUST")
    qpe = binary(a["QPE_ELIGIBLE_MASK"], shape, "QPE")
    cr = binary(a[CR], shape, "CR")
    action = a["QC_ACTION"]
    if (not np.array_equal(action == 3, ~obs) or np.any(trust & ~obs) or
        np.any(qpe & ~trust) or np.any(cr & (~trust | ~obs))):
        raise ValueError("invalid parent measurement qualification")
    if int(low_quality_flag) <= 0 or int(low_quality_flag) > 2**32 - 1:
        raise ValueError("LOW_QUALITY flag is required")
    flag = np.uint32(low_quality_flag)
    if int(flag) & (int(flag) - 1):
        raise ValueError("LOW_QUALITY must be one bit")
    nm = binary(evidence["NMR_NONMET_CANDIDATE_MASK"], shape, "nonmet candidate")
    un = binary(evidence["NMR_LOW_SNR_UNCERTAIN_MASK"], shape, "uncertainty candidate")
    domain = binary(evidence["NMR_DOMAIN_MASK"], shape, "domain")
    protected = binary(evidence["NMR_PROTECTED_MASK"], shape, "protection")
    no_rain = binary(evidence["NMR_VALID_NO_RAIN_MASK"], shape, "no_rain")
    if np.any((nm | un) & (~domain | protected | ~obs | no_rain)):
        raise ValueError("proposal not supported by an unprotected observed echo")
    enabled = cfg.mode == "experiment"
    nm_action = nm & enabled & (cfg.nonmet_policy != "diagnostic_only")
    un_action = un & enabled & (cfg.uncertainty_policy == "cr_withhold")
    quarantine = nm_action & trust & (action != 2) & (cfg.nonmet_policy == "quarantine")
    nm_loss = nm_action & cr
    # Attribute net removal to nonmet first; never double count masks' overlap.
    un_loss = un_action & cr & ~nm_action
    loss = nm_loss | un_loss
    for key in mutable_names(a): a["NMR_BEFORE_" + key] = a[key].copy()
    a.update({k: np.array(v, copy=True) for k, v in evidence.items()})
    a["NMR_QUARANTINE_MASK"] = quarantine.astype("uint8")
    a["NMR_NONMET_CR_WITHHELD_MASK"] = nm_loss.astype("uint8")
    a["NMR_UNCERTAINTY_CR_WITHHELD_MASK"] = un_loss.astype("uint8")
    a["NMR_CR_WITHHELD_MASK"] = loss.astype("uint8")
    derived = derived_invalidation(trust, quarantine)
    a["NMR_DERIVED_INVALIDATION_MASK"] = derived.astype("uint8")
    for key in DERIVED_FIELDS:
        if key in a: a[key][derived] = 0 if key.endswith("_MASK") else np.nan
    for key in mutable_names(a):
        if key.endswith("_TRUST_MASK"): a[key][quarantine] = 0
    a["QC_ACTION"][quarantine] = 1
    a["QC_FLAGS"][quarantine] |= flag
    for key in ("QUALITY_INDEX", "QI_METEO", "QI_INTERFERENCE"):
        if key in a: a[key][quarantine] = np.minimum(a[key][quarantine], cfg.quarantine_quality)
    a["LOW_QUALITY_MASK"][quarantine] = 1
    a["QPE_ELIGIBLE_MASK"][quarantine] = 0
    a["DBZH_USABLE"][quarantine] = np.nan
    if "P2_ADMIN_PENALTY_REMOVED_MASK" in a: a["P2_ADMIN_PENALTY_REMOVED_MASK"][quarantine] = 0
    a[CR][loss] = 0
    # Risk is separate from confirmed nonmet. No-rain/missing never changes here.
    a["CR_UNCERTAIN_MASK"][(nm_action | un_action) & obs] = 1
    a["CR_QUALIFICATION_REASON"][nm_action & obs] |= np.uint16(32)
    a["CR_QUALIFICATION_REASON"][un_action & obs] |= np.uint16(64)
    # Bit1 means currently eligible, not "was eligible".
    a["CR_QUALIFICATION_REASON"][loss] &= np.uint16(65534)
    qpe_loss = qpe & quarantine
    qfrac = float(qpe_loss.sum() / max(1, qpe.sum()))
    cfrac = float(loss.sum() / max(1, cr.sum()))
    return a, {"status": "AUDIT_ONLY" if not enabled else "EXPERIMENT_APPLIED",
        "nonmet_candidate_gates": int(nm.sum()), "uncertainty_candidate_gates": int(un.sum()),
        "added_quarantine_gates": int(quarantine.sum()), "qpe_loss_gates": int(qpe_loss.sum()),
        "cr_nonmet_loss_gates": int(nm_loss.sum()), "cr_uncertainty_only_loss_gates": int(un_loss.sum()),
        "cr_loss_gates": int(loss.sum()), "qpe_loss_fraction": qfrac, "cr_loss_fraction": cfrac,
        "review_required": qfrac > cfg.maximum_new_qpe_loss_fraction or cfrac > cfg.maximum_new_cr_loss_fraction,
        "budget_behavior": "retain_isolation_require_review", "confirmed_gates": 0, "filled_gates": 0,
        "valid_no_rain_changes": 0, "operational_eligible": False}
