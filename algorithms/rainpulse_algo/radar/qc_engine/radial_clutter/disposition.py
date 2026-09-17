"""Bounded policy after legacy finalization; raw values are never corrected."""
import numpy as np

from ..decision import Action, Decision
from .engine import infer
from .geometry import measured
from .rules import Reason


BASE_FIELDS = ("QC_ACTION", "QPE_ELIGIBLE_MASK", "RFI_QUARANTINE_MASK", "RFI_RISK_STATE",
               "REFLECTIVITY_TRUST_MASK", "DBZH_USABLE", "RHOHV_TRUST_MASK",
               "ZDR_TRUST_MASK", "PHIDP_TRUST_MASK", "VR_TRUST_MASK", "SW_TRUST_MASK", "SNR_TRUST_MASK")


def apply(n, decision, quality, flags, cfg, low_quality_flag, *, prior=None, weather_support=None, context=None):
    arrays = {k: v.copy() for k, v in decision.arrays.items()}
    for key in BASE_FIELDS:
        if key in arrays:
            arrays["RC1_BASE_" + key] = arrays[key].copy()
    arrays["RC1_BASE_QUALITY_INDEX"] = quality.copy()
    arrays["RC1_BASE_QC_FLAGS"] = flags.copy()
    arrays["RC1_BASE_LOW_QUALITY_MASK"] = (measured(n, "DBZH") & (quality < 0.5)).astype("uint8")
    diagnostic = infer(n, cfg, prior=prior, weather_support=weather_support, context=context,
                       source_reference=arrays.get("BWS_SELF_REFERENCE_MASK"),
                       source_residual=arrays.get("BWS_RANGE_RESIDUAL_DB"))
    arrays.update(diagnostic)
    observed = measured(n, "DBZH")
    oldq = arrays["RFI_QUARANTINE_MASK"] == 1
    reject = arrays["QC_ACTION"] == Action.REJECT
    proposed = (arrays["RC1_PROPOSED_MASK"] == 1) & observed & ~oldq & ~reject
    eligible = arrays["QPE_ELIGIBLE_MASK"] == 1
    fraction = float(np.sum(proposed & eligible) / max(1, int(eligible.sum())))
    blocked = cfg.mode != "audit" and fraction > cfg.maximum_new_loss_fraction
    added = proposed if cfg.mode != "audit" and not blocked else np.zeros(n.shape, bool)
    q, f = quality.copy(), flags.copy()
    arrays["RC1_ADDED_QUARANTINE_MASK"] = added.astype("uint8")
    arrays["RC1_BUDGET_STOP_MASK"] = (observed & blocked).astype("uint8")
    arrays["RC1_MODE"] = np.full(n.shape, int(cfg.mode != "audit"), "uint8")
    arrays["QC_ACTION"][added] = Action.DOWNWEIGHT
    arrays["RFI_QUARANTINE_MASK"][added] = 1
    arrays["RFI_RISK_STATE"][added] = 2
    arrays["QPE_ELIGIBLE_MASK"][added] = 0
    arrays["DBZH_USABLE"][added] = np.nan
    for name in list(arrays):
        if name.endswith("_TRUST_MASK") and not name.startswith("RC1_BASE_"):
            arrays[name][added] = 0
    q[added] = np.minimum(q[added], cfg.quarantine_quality)
    f[added] |= low_quality_flag
    arrays["RC1_REASON"][added] |= int(Reason.EXPERIMENTAL_QUARANTINE)
    record = {"method": cfg.method, "mode": cfg.mode, "manual_review_required": bool(blocked),
              "new_eligible_loss_fraction_if_applied": fraction, "added_quarantine_gates": int(added.sum()),
              "confirmed_additions": 0, "operational_eligible": False,
              "prior_status": "unavailable" if prior is None else "verified_content_and_geometry",
              "class_counts": {str(i): int(np.sum(arrays["RC1_CLASS"] == i)) for i in range(8)}}
    return Decision(arrays, f, q), q, observed, observed & (q < 0.5), f, record
