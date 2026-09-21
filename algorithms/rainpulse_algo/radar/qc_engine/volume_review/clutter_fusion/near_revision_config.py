"""Frozen opt-in near-site current/causal/terrain policy. Scores are not probabilities."""
from typing import Literal
from pydantic import BaseModel, ConfigDict, Field, model_validator


class NearRevisionConfig(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True, allow_inf_nan=False)
    version: Literal["near-joint-20260921-v1"] = "near-joint-20260921-v1"
    mode: Literal["audit", "cr_withhold"] = "audit"
    partial_enabled: bool = True
    minimum_snr_db: float = Field(default=12., ge=12, le=25)
    maximum_rhohv: float = Field(default=.90, ge=.5, le=.90)
    minimum_jitter_deg: float = Field(default=20., ge=20, le=90)
    maximum_dbz: float = Field(default=25., gt=0, le=30)
    minimum_samples: int = Field(default=6, ge=6, le=500)
    minimum_neighbour_fraction: float = Field(default=.6, ge=.6, le=1)
    # This path is one polarization family, irrespective of the number of
    # correlated features. It cannot autonomously quarantine QPE.
    temporal_enabled: bool = True
    temporal_minimum_jitter_deg: float = Field(default=15., ge=15, le=90)
    maximum_age_seconds: float = Field(default=900., ge=60, le=1200)
    maximum_temporal_sweeps: int = Field(default=40, ge=1, le=100)
    maximum_previous_gates: int = Field(default=20000000, ge=1, le=50000000)
    maximum_horizontal_error_m: float = Field(default=750., gt=0, le=1000)
    maximum_vertical_error_m: float = Field(default=250., gt=0, le=500)
    maximum_elevation_error_deg: float = Field(default=.2, gt=0, le=.3)
    maximum_dbzh_change_db: float = Field(default=3., gt=0, le=5)
    maximum_snr_change_db: float = Field(default=3., gt=0, le=5)
    # Measured local interception is a prior; accumulated obstruction is a
    # reliability limit, NOT a species/ground-clutter classifier.
    dem_policy: Literal["disabled", "audit", "cr_withhold"] = "audit"
    maximum_usable_cbb: float = Field(default=.70, ge=.5, le=.9)
    local_interception_pbb: float = Field(default=.10, gt=0, le=.5)
    maximum_dem_range_m: float = Field(default=75000., gt=0, le=150000)
    maximum_dem_gates: int = Field(default=5000000, ge=1, le=10000000)

    @model_validator(mode="after")
    def check(self):
        if self.temporal_minimum_jitter_deg > self.minimum_jitter_deg:
            raise ValueError("temporal prerequisite cannot be stricter than the seed by accident")
        return self
