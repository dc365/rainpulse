"""Versioned V7 experiment switches; values are project hypotheses, not standards."""

from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, model_serializer, model_validator

from .fragment_radials import FragmentConfig


class EvidenceGraphConfig(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True, allow_inf_nan=False)
    method: Literal["independent-evidence-graph-v7"] = "independent-evidence-graph-v7"
    audit_enabled: bool = True
    unified_stage_a: bool = True
    graph_enabled: bool = True
    quarantine_shape_models: bool = True
    restore_legacy_reject: Literal[False] = False
    # Proposal thresholds are intentionally distinct from action thresholds.
    # They enable REVIEW of short tails; they do not lower frozen confirm gates.
    proposal_minimum_measured_m: float = Field(default=2000, gt=0)
    maximum_structure_gap_m: float = Field(default=1000, ge=0, le=4000)
    maximum_structure_gap_fraction: float = Field(default=0.15, ge=0, lt=0.5)
    maximum_bundle_width_deg: float = Field(default=12, gt=0, le=30)
    shoulder_offsets_deg: tuple[float, ...] = (1, 2, 4, 8, 12, 18)
    minimum_shoulder_fraction: float = Field(default=0.8, gt=0.5, le=1)
    maximum_nodes: int = Field(default=20000, ge=1, le=100000)
    maximum_edges: int = Field(default=40000, ge=1, le=200000)
    quarantine_quality: float = Field(default=0.25, ge=0, lt=0.5)
    fragment_radials: FragmentConfig | None = None
    native_reference_required: Literal[False] = False
    # Native references are independent experiments, not an implicit fallback
    # or a dependency of production QC. The strict CLI can require them.

    @model_serializer(mode="wrap")
    def preserve_disabled_identity(self, handler):
        value = handler(self)
        if self.fragment_radials is None:
            value.pop("fragment_radials", None)
        return value

    @model_validator(mode="after")
    def check_limits(self):
        if not self.shoulder_offsets_deg or any(not 0 < x < 90 for x in self.shoulder_offsets_deg):
            raise ValueError("finite, positive shoulder angles required")
        if list(self.shoulder_offsets_deg) != sorted(set(self.shoulder_offsets_deg)):
            raise ValueError("shoulder offsets must be unique and increasing")
        if self.maximum_structure_gap_m >= self.proposal_minimum_measured_m:
            raise ValueError("a structural link cannot exceed a proposal's measured support")
        return self
