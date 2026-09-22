"""Opt-in physical-support review; weak-echo responses never grant an action."""
from typing import Literal
from pydantic import BaseModel, ConfigDict, Field, model_validator


class IsolationConfig(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True, allow_inf_nan=False)
    version: Literal["isolated-objects-20260923-v1"] = "isolated-objects-20260923-v1"
    mode: Literal["audit", "cr_withhold", "quarantine"] = "audit"
    operational_eligible: Literal[False] = False
    # Segmentation includes ALL original echoes at this structural threshold,
    # including already-rejected measurements and strong/credible weather.
    echo_threshold_dbz: float = Field(default=-10., ge=-32., le=10.)
    maximum_area_km2: float = Field(default=4., gt=0., le=20.)
    maximum_diameter_m: float = Field(default=4000., ge=500., le=10000.)
    maximum_elevation_deg: float = Field(default=25., ge=0., le=45.)
    maximum_ray_spacing_deg: float = Field(default=2., gt=0., le=3.)
    maximum_gate_footprint_m: float = Field(default=1500., ge=250., le=3000.)
    # Annuli are outside a containing circle of the ORIGINAL object's footprints.
    # Both scales must pass, in one non-iterative review of frozen raw support.
    ring_gap_m: float = Field(default=250., ge=0., le=2000.)
    ring_widths_m: tuple[float, ...] = (1000., 2000.)
    quadrature_step_m: float = Field(default=250., ge=100., le=500.)
    minimum_known_fraction: float = Field(default=.8, ge=.75, le=1.)
    minimum_quadrant_known_fraction: float = Field(default=.65, ge=.5, le=1.)
    maximum_possible_echo_fraction: float = Field(default=.2, ge=0., le=.25)
    minimum_unique_ring_gates: int = Field(default=12, ge=8, le=1000)
    minimum_object_evidence_fraction: float = Field(default=.6, ge=.5, le=1.)
    minimum_nonmet_snr_db: float = Field(default=12., ge=8., le=25.)
    minimum_polar_score: float = Field(default=.65, ge=.6, le=1.)
    minimum_nonmet_rho_score: float = Field(default=.6, ge=.5, le=1.)
    weak_diagnostic_enabled: bool = True
    weak_offset_m: float = Field(default=1000., ge=250., le=3000.)
    weak_score_threshold: float = Field(default=40., ge=0., le=200.)
    # Bounded resource costs. Limit exhaustion withdraws only this optional review
    # for every sweep, not parent decisions or a partially completed sweep list.
    maximum_sweep_gates: int = Field(default=5000000, ge=1, le=10000000)
    maximum_objects: int = Field(default=20000, ge=1, le=50000)
    maximum_sample_points: int = Field(default=8000000, ge=1, le=30000000)
    maximum_action_gates_per_object: int = Field(default=2000, ge=1, le=10000)

    @model_validator(mode="after")
    def check(self):
        import math
        if (not self.ring_widths_m or len(self.ring_widths_m)>3
                or tuple(sorted(set(self.ring_widths_m))) != self.ring_widths_m
                or any(not math.isfinite(v) or v<500 or v>5000 for v in self.ring_widths_m)):
            raise ValueError("one to three increasing finite physical ring widths required")
        if self.quadrature_step_m > min(self.ring_widths_m)/2:
            raise ValueError("ring quadrature is too coarse")
        return self
