"""Incremental original-window candidates; the existing writer owns actions."""

import copy
import json

import numpy as np

from rainpulse_algo.radar.qc_engine.volume_review.data import ResourceLimit

from .core import Evidence, Reason
from .evidence_tables import compact, expand, pack_details
from .geometry import adapt, mask
from .polar_windows import WindowPolicy, detect


def policy(cfg):
    """Derive a distinct identity without introducing looser runtime thresholds."""
    m = cfg.morphology
    return WindowPolicy(
        version="x-polar-window-objects-20261003-v2",
        reference_mode="known_joint_contrast",
        scale_m=m.scales_m[0],
        minimum_range_m=m.minimum_range_m,
        minimum_span_m=max(20000.0, m.minimum_span_m),
        minimum_support_fraction=max(0.4, m.minimum_support_fraction),
        minimum_known_fraction=max(0.8, m.minimum_flank_fraction),
        minimum_reference_fraction=max(0.8, m.minimum_flank_fraction),
        maximum_rhohv=cfg.radial_maximum_rhohv,
        minimum_snr_db=cfg.radial_minimum_snr_db,
        maximum_abs_zdr_db=cfg.clutter.maximum_abs_zdr_db,
        maximum_dbzh=cfg.radial_maximum_dbzh,
        maximum_width_deg=min(60.0, m.maximum_width_deg),
        boundary_tolerance_rays=m.boundary_tolerance_rays,
        minimum_fan_range_ratio=m.minimum_fan_range_ratio,
        maximum_sweep_gates=min(2000000, cfg.maximum_sweep_gates),
        maximum_work=min(50000000, m.maximum_work),
        maximum_objects=min(20000, m.maximum_objects),
        maximum_evidence_bytes=cfg.maximum_evidence_bytes,
    )


def bounded(record, limit):
    for candidate in (record, compact(record), pack_details(compact(record))):
        if (
            len(
                json.dumps(
                    candidate, sort_keys=True, separators=(",", ":"), allow_nan=False
                ).encode()
            )
            <= limit
        ):
            return candidate
    raise ResourceLimit("combined polar-window evidence exceeds existing allowance")


def attach(records, record):
    records.setdefault("module_records", {})["polar_windows"] = record
    if record["status"] != "EVALUATED":
        if records.get("status") == "EVALUATED":
            records["status"] = "DEGRADED_POLAR_WINDOW_" + record["status"]
        degraded = records.setdefault("degraded_modules", [])
        if "polar_windows" not in degraded:
            degraded.append("polar_windows")


def extend(cut, cfg, parent):
    if not cfg.polar_window_candidates_enabled:
        return parent
    shape = cut.fields["DBZH"].shape
    candidate = np.zeros(shape, bool)
    # Explicit native status survives even if a parent fills its entire JSON
    # allowance. 1 evaluated, 2 protection unavailable, 3 resource, 4 action
    # budget, 5 evidence budget. No new ACTION_BUDGET bit is set on refused gates:
    # the existing pipeline interprets that bit as actual withholding.
    status_code = 1
    records = expand(copy.deepcopy(parent.record))
    modules = records.setdefault("module_records", {})
    required = (
        "XQC_HARD_WEATHER_MASK",
        "XQC_LOCAL_WEATHER_MASK",
        "XQC_MORPHOLOGY_COUNTEREXAMPLE_MASK",
    )
    if modules.get("morphology", {}).get("status") != "EVALUATED" or any(
        key not in parent.arrays for key in required
    ):
        record = dict(status="UNAVAILABLE_COMPACT_PROTECTION", qualified_gates=0)
        status_code = 2
    else:
        try:
            view = adapt(cut, cfg)
            protected = np.zeros(shape, bool)
            for name in (
                "HARD_WEATHER",
                "LOCAL_WEATHER",
                "MORPHOLOGY_COUNTEREXAMPLE",
                "CONTEXT_WEATHER",
            ):
                protected |= mask(parent.arrays, "XQC_" + name + "_MASK", shape)
            result = detect(view.sweep, policy(cfg), protected=protected[view.order])
            candidate = view.restore(result.mask)
            candidate &= mask(cut.fields, "OBSERVED_MASK", shape) & ~protected
            record = dict(
                result.record,
                diagnostic_only=False,
                action_semantics="candidate_withheld_not_confirmed",
                qualified_gates=int(candidate.sum()),
                weather_truth=False,
            )
            existing = mask(parent.arrays, "XQC_PROPOSED_MASK", shape)
            existing |= (parent.arrays["XQC_REASON"] & int(Reason.ACTION_BUDGET)) != 0
            censor = mask(parent.arrays, "XQC_NOISE_FLOOR_MASK", shape)
            observed = mask(cut.fields, "OBSERVED_MASK", shape)
            count = int(((existing | candidate) & ~censor & observed).sum())
            if count > cfg.maximum_new_exclusion_fraction * max(int(observed.sum()), 1):
                candidate[:] = False
                record["status"] = "ACTION_BUDGET_ABSTAINED"
                status_code = 4
        except ResourceLimit as exc:
            candidate[:] = False
            record = dict(status="RESOURCE_LIMIT_ABSTAINED", reason=str(exc), qualified_gates=0)
            status_code = 3
    record["candidate_gates"] = int(candidate.sum())
    attach(records, record)
    if candidate.any():
        records["candidate_gates"] = int(
            (mask(parent.arrays, "XQC_PROPOSED_MASK", shape) | candidate).sum()
        )
    try:
        records = bounded(records, cfg.maximum_evidence_bytes)
    except ResourceLimit:
        candidate[:] = False
        status_code = 5
        records = expand(copy.deepcopy(parent.record))
        attach(
            records, dict(status="EVIDENCE_BUDGET_ABSTAINED", qualified_gates=0, candidate_gates=0)
        )
        try:
            records = bounded(records, cfg.maximum_evidence_bytes)
        except ResourceLimit:
            # Retain the bounded completed parent exactly; native state 5 is
            # still exported and cannot authorize an action.
            records = copy.deepcopy(parent.record)
    arrays = dict(parent.arrays)
    arrays["XQC_POLAR_WINDOW_MASK"] = candidate.astype("uint8")
    arrays["XQC_POLAR_WINDOW_STATE"] = np.full(shape, status_code, "uint8")
    if candidate.any():
        arrays["XQC_PROPOSED_MASK"] = (
            mask(parent.arrays, "XQC_PROPOSED_MASK", shape) | candidate
        ).astype("uint8")
        arrays["XQC_REASON"] = parent.arrays["XQC_REASON"].copy()
        arrays["XQC_REASON"][candidate] |= int(Reason.POLAR_WINDOW)
    # No confirmed quarantine/source mask is changed, nor a parent disposition
    # withdrawn. Only the sole normal pipeline writer applies candidate actions.
    return Evidence(arrays, records)
