"""Opt-in complete-carrier candidates; the existing X writer owns actions."""

import copy

import numpy as np

from rainpulse_algo.radar.qc_engine.volume_review.data import ResourceLimit

from .core import Evidence, Reason
from .evidence_tables import expand
from .fragmented_carriers import review
from .geometry import adapt, mask
from .polar_window_integration import bounded


def attach(records, record):
    records.setdefault("module_records", {})["fragmented_carriers"] = record
    if record["status"] != "EVALUATED":
        if records.get("status") == "EVALUATED":
            records["status"] = "DEGRADED_FRAGMENTED_CARRIER_" + record["status"]
        degraded = records.setdefault("degraded_modules", [])
        if "fragmented_carriers" not in degraded:
            degraded.append("fragmented_carriers")


def extend(cut, cfg, parent):
    if not cfg.fragmented_carrier_candidates_enabled:
        return parent
    shape = cut.fields["DBZH"].shape
    candidate, excess, unknown = (np.zeros(shape, bool) for _ in range(3))
    records = expand(copy.deepcopy(parent.record))
    state = 1
    required = tuple("XQC_" + name + "_MASK" for name in (
        "HARD_WEATHER", "LOCAL_WEATHER", "CONTEXT_WEATHER", "MORPHOLOGY_COUNTEREXAMPLE",
    ))
    if records.get("module_records", {}).get("morphology", {}).get("status") != "EVALUATED" or any(
        name not in parent.arrays for name in required
    ):
        state = 2
        record = dict(status="PROTECTION_UNAVAILABLE", candidate_gates=0)
    else:
        try:
            protected = mask(parent.arrays, "XQC_HARD_WEATHER_MASK", shape)
            protected |= mask(parent.arrays, "XQC_CONTEXT_WEATHER_MASK", shape)
            if (cfg.radial_source_local_policy == "protect"
                    or cfg.morphology.local_weather_policy == "protect"):
                protected |= mask(parent.arrays, "XQC_LOCAL_WEATHER_MASK", shape)
            view = adapt(cut, cfg)
            result = review(view.sweep, cfg.morphology, protected=protected[view.order])
            candidate = view.restore(result.candidate) & ~protected
            candidate &= mask(cut.fields, "OBSERVED_MASK", shape)
            # Existing budget reviews count toward allowance, but never become
            # accepted parent proposals merely because an increment succeeds.
            old_proposed = mask(parent.arrays, "XQC_PROPOSED_MASK", shape)
            existing = old_proposed | (
                (parent.arrays["XQC_REASON"] & int(Reason.ACTION_BUDGET)) != 0
            )
            candidate &= ~existing
            excess = view.restore(result.local_excess)
            unknown = view.restore(result.unknown)
            censor = mask(parent.arrays, "XQC_NOISE_FLOOR_MASK", shape)
            observed = mask(cut.fields, "OBSERVED_MASK", shape)
            record = dict(
                result.record, status="EVALUATED", production_enabled=False,
                integration_enabled=True, action_semantics="candidate_withheld_not_confirmed",
                qualified_increment_gates=int(candidate.sum()),
                compact_proxy_override_gates=int((candidate & mask(
                    parent.arrays, "XQC_MORPHOLOGY_COUNTEREXAMPLE_MASK", shape,
                )).sum()),
            )
            count = int(((existing | candidate) & ~censor & observed).sum())
            if count > cfg.maximum_new_exclusion_fraction * max(int(observed.sum()), 1):
                candidate[:] = False
                state = 4
                record["status"] = "ACTION_BUDGET_ABSTAINED"
        except ResourceLimit as exc:
            candidate[:] = excess[:] = unknown[:] = False
            state = 3
            record = dict(status="RESOURCE_LIMIT_ABSTAINED", reason=str(exc))
    record["accepted_increment_gates"] = int(candidate.sum())
    attach(records, record)
    if candidate.any():
        records["candidate_gates"] = int((old_proposed | candidate).sum())
    try:
        records = bounded(records, cfg.maximum_evidence_bytes)
    except ResourceLimit:
        candidate[:] = excess[:] = unknown[:] = False
        state = 5
        records = expand(copy.deepcopy(parent.record))
        attach(records, dict(status="EVIDENCE_BUDGET_ABSTAINED", accepted_increment_gates=0))
        try:
            records = bounded(records, cfg.maximum_evidence_bytes)
        except ResourceLimit:
            records = copy.deepcopy(parent.record)
    arrays = dict(parent.arrays)
    arrays.update(
        XQC_FRAGMENTED_CARRIER_MASK=candidate.astype("uint8"),
        XQC_FRAGMENTED_CARRIER_EXCESS_MASK=excess.astype("uint8"),
        XQC_FRAGMENTED_CARRIER_UNKNOWN_MASK=unknown.astype("uint8"),
        XQC_FRAGMENTED_CARRIER_STATE=np.full(shape, state, "uint8"),
    )
    if candidate.any():
        arrays["XQC_PROPOSED_MASK"] = (old_proposed | candidate).astype("uint8")
        arrays["XQC_REASON"] = parent.arrays["XQC_REASON"].copy()
        arrays["XQC_REASON"][candidate] |= int(Reason.FRAGMENTED_CARRIER)
    return Evidence(arrays, records)
