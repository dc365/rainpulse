"""Callable integration boundary; no automatic worker/planner/version selection.

Return an experimental Decision separately. Never label it as accepted V7 or
write it to an old QC asset whose validator expects the original stage ledger.
"""
from __future__ import annotations
from .data import RawScan
from .engine import infer
from .config import Config, Policy
from .policy import apply_policy


def raw_from_native(native, phase_period):
    d = {"raw_"+k: v for k,v in native.fields.items()}
    d.update({"available_"+k: v for k,v in native.field_available.items()})
    d.update(range_m=native.ranges, azimuth_rotated_deg=native.azimuth,
             elevation_deg=native.elevation, geometry_good=native.geometry_good,
             gap_after=native.gap_after)
    return RawScan.from_arrays(d, full_ppi=bool(native.full_ppi), phase_period=phase_period)


def evaluate_native(native, baseline, *, model=Config(), policy=Policy(),
                    phase_period=360.0, low_quality_flag=None, weather=None):
    # Keep the caller's actual Decision type; no heavy radar stack import is
    # needed by this pure-numpy boundary. The action ABI is validated by policy.
    Decision = type(baseline)
    raw = raw_from_native(native, phase_period)
    evidence = infer(raw, model, weather)
    values = {k: v for k,v in baseline.arrays.items()
              if k in {"QC_ACTION", "RFI_QUARANTINE_MASK", "QPE_ELIGIBLE_MASK", "DBZH_USABLE", "RFI_RISK_STATE"}
              or k.endswith("_TRUST_MASK")}
    values["QUALITY_INDEX"] = baseline.quality
    values["QC_FLAGS"] = baseline.flags
    outcome = apply_policy(raw, evidence, values, policy, low_quality_flag=low_quality_flag)
    result = {k: v.copy() for k,v in baseline.arrays.items()}
    for k,v in outcome.arrays.items():
        if k not in {"QUALITY_INDEX", "QC_FLAGS"}:
            result[k] = v
    # Separate receipt/arrays, not extra fields silently embedded in old QC Zarr.
    decision = Decision(result, outcome.arrays["QC_FLAGS"], outcome.arrays["QUALITY_INDEX"])
    return decision, evidence, outcome


def scalar_completion(evidence, outcome):
    """Bounded event summary. Raw arrays/folds never enter NATS."""
    return {"method": "raw-object-crossfit-competition-1", "operational_eligible": False,
            "evidence_sha256": evidence.identity["evidence_sha256"],
            "outcome_sha256": outcome.summary["outcome_sha256"],
            "status": outcome.summary["status"],
            "added_quarantine_gates": outcome.summary["added_quarantine_gates"],
            "confirmed_additions": 0,
            "details_object_path": "object-consensus/summary.json"}
