from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

import numpy as np

RADIAL_CANDIDATE_REASON_NEIGHBOUR = np.uint16(1 << 0)
RADIAL_CANDIDATE_REASON_SATURATED = np.uint16(1 << 1)
RADIAL_CANDIDATE_REASON_SEGMENT = np.uint16(1 << 2)
RADIAL_CANDIDATE_REASON_EXTENT = np.uint16(1 << 3)
RADIAL_CANDIDATE_REASON_MULTISCALE = np.uint16(1 << 4)
RADIAL_CANDIDATE_REASON_FAN = np.uint16(1 << 5)
RADIAL_CANDIDATE_REASON_EDGE = np.uint16(1 << 6)
RADIAL_CANDIDATE_REASON_RESIDUAL = np.uint16(1 << 7)

RADIAL_FINAL_REASON_NONE = np.uint8(0)
RADIAL_FINAL_REASON_STRONG_SEED = np.uint8(1)
RADIAL_FINAL_REASON_CONTEXT_PROMOTED = np.uint8(2)
RADIAL_FINAL_REASON_VETO_SOFT = np.uint8(3)
RADIAL_FINAL_REASON_WEAK_SOFT = np.uint8(4)
RADIAL_FINAL_REASON_STRONG_OVERRIDE = np.uint8(5)

RADIAL_FINAL_REASON_NAMES = {
    int(RADIAL_FINAL_REASON_NONE): "none",
    int(RADIAL_FINAL_REASON_STRONG_SEED): "strong_seed",
    int(RADIAL_FINAL_REASON_CONTEXT_PROMOTED): "context_promoted",
    int(RADIAL_FINAL_REASON_VETO_SOFT): "meteo_veto_soft",
    int(RADIAL_FINAL_REASON_WEAK_SOFT): "weak_soft",
    int(RADIAL_FINAL_REASON_STRONG_OVERRIDE): "strong_seed_overrides_cross_support",
}


@dataclass(frozen=True)
class RadialDecisionV2:
    observed_mask: np.ndarray
    candidate_mask: np.ndarray
    candidate_reason_bits: np.ndarray
    strong_seed_mask: np.ndarray
    meteo_veto_mask: np.ndarray
    hard_mask: np.ndarray
    soft_mask: np.ndarray
    override_mask: np.ndarray
    final_reason: np.ndarray
    decision_summary: dict[str, Any] = field(default_factory=dict)


def summarize_radial_decision(decision: RadialDecisionV2) -> dict[str, Any]:
    final_counts = {
        name: int(np.count_nonzero(decision.final_reason == code))
        for code, name in RADIAL_FINAL_REASON_NAMES.items()
        if code != int(RADIAL_FINAL_REASON_NONE)
    }
    return {
        "candidate_gate_count": int(np.count_nonzero(decision.candidate_mask)),
        "candidate_ray_count": int(np.count_nonzero(np.any(decision.candidate_mask, axis=1))),
        "hard_gate_count": int(np.count_nonzero(decision.hard_mask)),
        "hard_ray_count": int(np.count_nonzero(np.any(decision.hard_mask, axis=1))),
        "soft_gate_count": int(np.count_nonzero(decision.soft_mask)),
        "soft_ray_count": int(np.count_nonzero(np.any(decision.soft_mask, axis=1))),
        "veto_gate_count": int(np.count_nonzero(decision.meteo_veto_mask)),
        "veto_ray_count": int(np.count_nonzero(np.any(decision.meteo_veto_mask, axis=1))),
        "strong_seed_override_gate_count": int(np.count_nonzero(decision.override_mask)),
        "strong_seed_override_ray_count": int(
            np.count_nonzero(np.any(decision.override_mask, axis=1))
        ),
        "final_reason_gate_counts": final_counts,
    }
