"""Explicit research policy; no implicit activation or old-profile hash changes."""
from typing import Literal
import hashlib
import json
from pydantic import BaseModel, ConfigDict, Field, model_validator


class VolumeReviewConfig(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True, allow_inf_nan=False)
    version: Literal["volume-object-review-20260919-v1"] = "volume-object-review-20260919-v1"
    mode: Literal["audit", "experiment_quarantine"] = "audit"
    phase: Literal[0, 1, 2, 3] = 3
    operational_eligible: Literal[False] = False
    levels_dbz: tuple[float, ...] = (0., 15., 30., 45.)
    scales_m: tuple[int, ...] = (5000, 10000, 20000, 40000)
    minimum_window_support: float = Field(default=.4, gt=0, le=1)
    minimum_object_gates: int = Field(default=4, ge=2)
    minimum_object_span_m: float = Field(default=2000., ge=1000)
    maximum_source_width_deg: float = Field(default=45., gt=0, le=90)
    minimum_source_aspect: float = Field(default=3., ge=2)
    source_block_m: float = Field(default=10000., ge=5000, le=25000)
    minimum_reference_blocks: int = Field(default=3, ge=3)
    guard_blocks: int = Field(default=1, ge=1, le=3)
    minimum_reference_span_m: float = Field(default=20000., ge=20000)
    minimum_reference_samples: int = Field(default=60, ge=30)
    minimum_samples_per_block: int = Field(default=8, ge=5)
    maximum_source_residual_db: float = Field(default=2.5, gt=0, le=2.5)
    maximum_snr_dispersion_db: float = Field(default=2., gt=0, le=2)
    minimum_snr_db: float = Field(default=12., ge=8)
    coherent_snr_db: float = Field(default=20., ge=20)
    maximum_phase_p90_deg: float = Field(default=10., gt=0, le=10)
    maximum_zdr_p90_db: float = Field(default=1., gt=0, le=1)
    noisy_rho: float = Field(default=.8, gt=0, lt=.95)
    maximum_states: int = Field(default=3, ge=1, le=3)
    state_separation_db: float = Field(default=8., ge=6)
    minimum_link_elevation_deg: float = Field(default=.2, gt=0, le=2)
    maximum_link_angle_deg: float = Field(default=2., gt=0, le=5)
    maximum_link_range_m: float = Field(default=1500., gt=0, le=5000)
    maximum_link_seconds: float = Field(default=300., gt=0, le=600)
    maximum_weather_horizontal_m: float = Field(default=1500., gt=0, le=3000)
    maximum_weather_vertical_m: float = Field(default=750., gt=0, le=1500)
    weather_rho: float = Field(default=.97, gt=.95, le=1)
    minimum_weather_run_m: float = Field(default=2000., ge=1000)
    source_corroboration_required: Literal[True] = True
    weak_policy: Literal["diagnostic_only"] = "diagnostic_only"
    unknown_cr_policy: Literal["withhold", "retain_with_risk"] = "withhold"
    quarantine_quality: float = Field(default=.2, ge=0, lt=.5)
    maximum_new_eligible_loss_fraction: float = Field(default=.1, gt=0, le=1)
    maximum_gates: int = Field(default=20000000, ge=1, le=50000000)
    maximum_objects: int = Field(default=20000, ge=1, le=100000)
    maximum_folds: int = Field(default=20000, ge=1, le=100000)
    maximum_links: int = Field(default=40000, ge=1, le=200000)
    export_evidence: bool = True

    @model_validator(mode="after")
    def checks(self):
        import math
        if (not self.levels_dbz or len(self.levels_dbz) > 8 or
            tuple(sorted(set(self.levels_dbz))) != self.levels_dbz or
            any(not math.isfinite(x) or not -20 <= x <= 70 for x in self.levels_dbz)):
            raise ValueError("one to eight ordered finite contours required")
        if (not self.scales_m or len(self.scales_m) > 8 or
            tuple(sorted(set(self.scales_m))) != self.scales_m or
            any(type(x) is not int or not 1000 <= x <= 100000 for x in self.scales_m)):
            raise ValueError("ordered physical integer scales required")
        if self.phase < 2 and self.mode != "audit":
            raise ValueError("P0/P1 are audit only")
        return self

    @property
    def digest(self):
        return hashlib.sha256(json.dumps(self.model_dump(mode="json"), sort_keys=True,
                                         separators=(",", ":")).encode()).hexdigest()
