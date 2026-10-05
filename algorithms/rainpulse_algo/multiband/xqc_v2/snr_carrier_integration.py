"""Opt-in original SNR candidates; the existing X writer owns every action."""

import copy

import numpy as np

from rainpulse_algo.radar.qc_engine.volume_review.data import ResourceLimit

from .core import Evidence, Reason
from .evidence_tables import expand
from .geometry import mask
from .polar_window_integration import bounded
from .snr_carriers import review
from .source_family_geometry import source_view


def attach(records, record):
    records.setdefault("module_records", {})["snr_carriers"] = record
    if record["status"] != "EVALUATED":
        if records.get("status") == "EVALUATED":
            records["status"] = "DEGRADED_SNR_CARRIER_" + record["status"]
        degraded = records.setdefault("degraded_modules", [])
        if "snr_carriers" not in degraded:
            degraded.append("snr_carriers")


def extend(cut, cfg, parent):
    if cfg.snr_carrier_policy is None:
        return parent
    shape = cut.fields["DBZH"].shape
    candidate, excess, unknown = (np.zeros(shape, bool) for _ in range(3))
    budget_review = np.zeros(shape, bool)
    records = expand(copy.deepcopy(parent.record))
    state = 1
    required = tuple(
        "XQC_" + name + "_MASK"
        for name in (
            "HARD_WEATHER",
            "LOCAL_WEATHER",
            "CONTEXT_WEATHER",
            "MORPHOLOGY_COUNTEREXAMPLE",
        )
    )
    if records.get("module_records", {}).get("morphology", {}).get("status") != "EVALUATED" or any(
        name not in parent.arrays for name in required
    ):
        state = 2
        record = dict(status="PROTECTION_UNAVAILABLE", candidate_gates=0)
    else:
        try:
            protected = mask(parent.arrays, "XQC_HARD_WEATHER_MASK", shape)
            protected |= mask(parent.arrays, "XQC_CONTEXT_WEATHER_MASK", shape)
            if (
                cfg.radial_source_local_policy == "protect"
                or cfg.morphology.local_weather_policy == "protect"
            ):
                protected |= mask(parent.arrays, "XQC_LOCAL_WEATHER_MASK", shape)
            view = source_view(cut, cfg)
            _, snr_available = view.sweep.moment("SNR")
            old_proposed = mask(parent.arrays, "XQC_PROPOSED_MASK", shape)
            existing = old_proposed | (
                (parent.arrays["XQC_REASON"] & int(Reason.ACTION_BUDGET)) != 0
            )
            if not snr_available.any():
                state = 2
                record = dict(status="SOURCE_SNR_UNAVAILABLE", candidate_gates=0)
            else:
                observed = mask(cut.fields, "OBSERVED_MASK", shape)
                target = observed & ~existing & ~protected
                result = review(
                    view.sweep,
                    cfg.snr_carrier_policy,
                    target=target[view.order],
                    protected=protected[view.order],
                )
                candidate = view.restore(result.candidate) & target
                excess = view.restore(result.local_excess)
                unknown = view.restore(result.unknown)
                record = dict(
                    result.record,
                    status="EVALUATED",
                    integration_enabled=True,
                    action_semantics="candidate_withheld_not_confirmed",
                    qualified_increment_gates=int(candidate.sum()),
                )
                censor = mask(parent.arrays, "XQC_NOISE_FLOOR_MASK", shape)
                count = int(((existing | candidate) & ~censor & observed).sum())
                if count > cfg.maximum_new_exclusion_fraction * max(int(observed.sum()), 1):
                    budget_review = candidate.copy()
                    candidate[:] = False
                    state = 4
                    record["status"] = "ACTION_BUDGET_ABSTAINED"
        except ResourceLimit as exc:
            candidate[:] = excess[:] = unknown[:] = False
            state = 3
            record = dict(status="RESOURCE_LIMIT_ABSTAINED", reason=str(exc))
    record["accepted_increment_gates"] = int(candidate.sum())
    record["budget_review_gates"] = int(budget_review.sum())
    attach(records, record)
    if candidate.any():
        records["candidate_gates"] = int((old_proposed | candidate).sum())
    try:
        records = bounded(records, cfg.maximum_evidence_bytes)
    except ResourceLimit:
        candidate[:] = excess[:] = unknown[:] = False
        budget_review[:] = False
        state = 5
        records = expand(copy.deepcopy(parent.record))
        attach(records, dict(status="EVIDENCE_BUDGET_ABSTAINED", accepted_increment_gates=0))
        try:
            records = bounded(records, cfg.maximum_evidence_bytes)
        except ResourceLimit:
            records = copy.deepcopy(parent.record)
    arrays = dict(parent.arrays)
    arrays.update(
        XQC_SNR_CARRIER_MASK=candidate.astype("uint8"),
        XQC_SNR_CARRIER_BUDGET_REVIEW_MASK=budget_review.astype("uint8"),
        XQC_SNR_CARRIER_EXCESS_MASK=excess.astype("uint8"),
        XQC_SNR_CARRIER_UNKNOWN_MASK=unknown.astype("uint8"),
        XQC_SNR_CARRIER_STATE=np.full(shape, state, "uint8"),
    )
    if candidate.any():
        arrays["XQC_PROPOSED_MASK"] = (old_proposed | candidate).astype("uint8")
    if candidate.any() or budget_review.any():
        arrays["XQC_REASON"] = parent.arrays["XQC_REASON"].copy()
        arrays["XQC_REASON"][candidate] |= int(Reason.SNR_CARRIER)
        arrays["XQC_REASON"][budget_review] |= int(Reason.SNR_CARRIER | Reason.ACTION_BUDGET)
    return Evidence(arrays, records)
