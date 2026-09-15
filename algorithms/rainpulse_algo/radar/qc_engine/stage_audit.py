"""Compact first-exclusion provenance. Tracking never mutates a Decision."""
from enum import IntEnum

import numpy as np


class Decider(IntEnum):
    NONE = 0
    ORIGINAL_INVALID = 1
    BASE_MOMENT_OBJECT = 2
    PAPER_FUSION = 3
    RANGE_SIGNATURE = 4
    RESIDUAL = 5
    GRAPH = 6
    HEALTH_QUALITY = 7
    OBJECT_CONSENSUS = 8


class StageAudit:
    def __init__(self, shape):
        self.first_action = np.zeros(shape, "uint8")
        self.first_eligibility = np.zeros(shape, "uint8")
        self.latest_action = np.zeros(shape, "uint8")
        self.records = []

    def observe(self, stage, decision):
        code = int(Decider(stage))
        arrays = decision.arrays
        action = np.asarray(arrays["QC_ACTION"])
        quarantine = arrays.get("RFI_QUARANTINE_MASK", np.zeros(action.shape)) == 1
        excluded = (action == 2) | quarantine
        invalid = action == 3
        first = (self.first_action == 0) & (excluded | invalid)
        self.first_action[first] = np.where(invalid[first], Decider.ORIGINAL_INVALID, code)
        current = np.where(excluded, 2, np.where(invalid, 3, action)).astype("uint8")
        self.latest_action[(current != self.latest_action) & (excluded | invalid)] = code
        eligible = arrays["QPE_ELIGIBLE_MASK"] == 1
        newly_ineligible = (self.first_eligibility == 0) & ~eligible
        self.first_eligibility[newly_ineligible] = np.where(
            invalid[newly_ineligible], Decider.ORIGINAL_INVALID, code
        )
        self.records.append({
            "stage": Decider(stage).name,
            "rejected": int((action == 2).sum()),
            "quarantined": int(quarantine.sum()),
            "eligible": int(eligible.sum()),
            "first_exclusions": int(first.sum()),
        })

    def arrays(self):
        return {
            "V7_FIRST_DECIDER": self.first_action.copy(),
            "V7_FIRST_INELIGIBLE_DECIDER": self.first_eligibility.copy(),
        }

    def summary(self):
        return {"stages": list(self.records), "codes": {s.name: int(s) for s in Decider}}


def gate_routes(fields):
    """Missing arrays mean NOT EVALUATED, never a negative scientific finding."""
    routes = {}
    for name, candidate, reason in (
        ("legacy_object", "RFI_OBJECT_ID", None),
        ("paper", "PAPER_CANDIDATE_MASK", "PAPER_DECISION_REASON"),
        ("range", "V5_RANGE_CANDIDATE_MASK", "V5_RANGE_REASON"),
        ("narrow", "V6_NARROW_CANDIDATE_MASK", "V61_NARROW_STAGE_REASON"),
        ("graph", "V7_GRAPH_REVIEW_MASK", "V7_GRAPH_STAGE_REASON"),
    ):
        value = fields.get(candidate)
        routes[name] = {"evaluated": value is not None,
                        "proposed": None if value is None else bool(value > 0),
                        "reason_code": fields.get(reason) if reason else None,
                        "unavailable_reason": "field_not_recorded" if value is None else None}
    return routes
