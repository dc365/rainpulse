"""Complete receiver-lobe candidates, preserving every parent disposition."""

import copy

import numpy as np

from rainpulse_algo.radar.qc_engine.volume_review.data import ResourceLimit

from .core import Evidence, Reason
from .evidence_tables import expand
from .geometry import mask
from .polar_window_integration import bounded
from .source_family_geometry import source_view
from .source_fans import detect_complete


class ProtectionUnavailable(ValueError):
    """A missing scientific prerequisite, distinct from resource exhaustion."""


def extend(cut, cfg, parent):
    if not cfg.complete_source_families_enabled:
        return parent
    shape = cut.fields["DBZH"].shape
    candidate = np.zeros(shape, bool)
    records = expand(copy.deepcopy(parent.record))
    state = 1
    try:
        if "XQC_HARD_WEATHER_MASK" not in parent.arrays:
            raise ProtectionUnavailable("completed parent hard-weather support unavailable")
        view = source_view(cut, cfg)
        protected = mask(parent.arrays, "XQC_HARD_WEATHER_MASK", shape)
        protected |= mask(parent.arrays, "XQC_CONTEXT_WEATHER_MASK", shape)
        if cfg.radial_source_local_policy == "protect":
            protected |= mask(parent.arrays, "XQC_LOCAL_WEATHER_MASK", shape)
        proposed, proof = detect_complete(view.sweep, cfg, protected=protected[view.order])
        candidate = view.restore(proposed) & ~protected
        candidate &= mask(cut.fields, "OBSERVED_MASK", shape)
        parent_proposed = mask(parent.arrays, "XQC_PROPOSED_MASK", shape)
        existing = parent_proposed.copy()
        existing |= (parent.arrays["XQC_REASON"] & int(Reason.ACTION_BUDGET)) != 0
        candidate &= ~existing
        censor = mask(parent.arrays, "XQC_NOISE_FLOOR_MASK", shape)
        observed = mask(cut.fields, "OBSERVED_MASK", shape)
        count = int(((existing | candidate) & ~censor & observed).sum())
        state = 4 if count > cfg.maximum_new_exclusion_fraction * max(int(observed.sum()), 1) else 1
        record = dict(
            status="ACTION_BUDGET_ABSTAINED" if state == 4 else "EVALUATED",
            source_proof=proof,
            reference_mode=cfg.complete_source_family_reference_mode,
            qualified_gates=int(candidate.sum()),
            accepted_proposal_gates=int(candidate.sum()) if state == 1 else 0,
            budget_review_withheld_gates=int(candidate.sum()) if state == 4 else 0,
            action_semantics="candidate_withheld_not_confirmed",
            budget_semantics="unchanged_rejection_cap_and_explicit_uncertainty_withholding",
            weather_truth=False,
            rf_source_confirmed=False,
            local_proxy_conflicts=int(
                (candidate & mask(parent.arrays, "XQC_LOCAL_WEATHER_MASK", shape)).sum()
            ),
            compact_shape_conflicts=int(
                (candidate & mask(parent.arrays, "XQC_MORPHOLOGY_COUNTEREXAMPLE_MASK", shape)).sum()
            ),
        )
    except ProtectionUnavailable as exc:
        candidate[:] = False
        state = 2
        record = dict(status="PROTECTION_UNAVAILABLE", reason=str(exc), qualified_gates=0)
    except ResourceLimit as exc:
        candidate[:] = False
        state = 3
        record = dict(status="RESOURCE_LIMIT_ABSTAINED", reason=str(exc), qualified_gates=0)
    records.setdefault("module_records", {})["complete_families"] = record
    if state != 1:
        if records.get("status") == "EVALUATED":
            records["status"] = "DEGRADED_COMPLETE_FAMILY_" + record["status"]
        records.setdefault("degraded_modules", []).append("complete_families")
    try:
        records = bounded(records, cfg.maximum_evidence_bytes)
    except ResourceLimit:
        candidate[:] = False
        state = 5
        records = copy.deepcopy(parent.record)
    arrays = dict(parent.arrays)
    arrays["XQC_COMPLETE_FAMILY_MASK"] = candidate.astype("uint8")
    arrays["XQC_COMPLETE_FAMILY_STATE"] = np.full(shape, state, "uint8")
    if candidate.any():
        arrays["XQC_REASON"] = parent.arrays["XQC_REASON"].copy()
        arrays["XQC_REASON"][candidate] |= int(Reason.COMPLETE_FAMILY)
        if state == 4:
            # Same semantics as the existing budget writer: review candidates
            # remain uncertain and excluded from display/CR, never rejected.
            arrays["XQC_REASON"][candidate] |= int(Reason.ACTION_BUDGET)
        else:
            # Budget-review gates count toward admission, but are not accepted
            # proposals. Preserve that distinction when accepting the increment.
            arrays["XQC_PROPOSED_MASK"] = (parent_proposed | candidate).astype("uint8")
    # Source-kind, confirmed/source/quarantine masks remain the completed
    # parent's arrays; the sole normal writer applies action=3 to additions.
    return Evidence(arrays, records)
