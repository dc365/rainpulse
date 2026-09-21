"""Frozen opt-in near-site current/causal/terrain policy. Scores are not probabilities."""
from typing import Literal
from pydantic import BaseModel, ConfigDict, Field, model_serializer, model_validator


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
    strong_near: "StrongNearConfig | None" = None

    @model_serializer(mode="wrap")
    def serialize_optional_strong(self, handler):
        value = handler(self)
        if self.strong_near is None:
            value.pop("strong_near", None)
        return value

    @model_validator(mode="after")
    def check(self):
        if self.temporal_minimum_jitter_deg > self.minimum_jitter_deg:
            raise ValueError("temporal prerequisite cannot be stricter than the seed by accident")
        return self


class TemporalLowRhoConfig(BaseModel):
    """Conservative causal recurrence family; default is off."""
    model_config = ConfigDict(extra="forbid", frozen=True, allow_inf_nan=False)
    version: Literal["temporal-low-rho-20260922-v1"] = "temporal-low-rho-20260922-v1"
    maximum_range_m: float = Field(default=15000., gt=0, le=30000)
    minimum_dbz: float = Field(default=7., ge=0, le=20)
    maximum_dbz: float = Field(default=30., gt=5, le=40)
    maximum_rhohv: float = Field(default=.90, ge=.5, le=.95)
    minimum_snr_db: float = Field(default=12., ge=8, le=25)
    maximum_age_seconds: float = Field(default=900., ge=60, le=1200)
    maximum_horizontal_error_m: float = Field(default=750., gt=0, le=1000)
    maximum_vertical_error_m: float = Field(default=250., gt=0, le=500)
    maximum_elevation_error_deg: float = Field(default=.30, gt=0, le=.5)
    minimum_prior_snapshots: int = Field(default=2, ge=2, le=2)
    minimum_object_gates: int = Field(default=10, ge=3, le=1000)
    maximum_object_gates: int = Field(default=5000, ge=1, le=50000)
    minimum_object_recurrence_fraction: float = Field(default=.50, ge=.3, le=1)
    maximum_temporal_objects: int = Field(default=20000, ge=1, le=100000)


class StrongNearConfig(BaseModel):
    """Bounded strong-echo exception; two measured CF families remain required."""
    model_config = ConfigDict(extra="forbid", frozen=True, allow_inf_nan=False)
    version: Literal["strong-near-20260922-v1"] = "strong-near-20260922-v1"
    mode: Literal["audit", "quarantine"] = "audit"
    minimum_dbz: float = Field(default=30., ge=25, le=35)
    maximum_dbz: float = Field(default=50., gt=30, le=55)
    maximum_rhohv: float = Field(default=.85, ge=.5, le=.90)
    minimum_snr_db: float = Field(default=12., ge=8, le=25)
    minimum_texture_score: float = Field(default=.40, ge=.25, le=1)
    minimum_family_count: int = Field(default=2, ge=2, le=4)
    maximum_range_m: float = Field(default=75000., gt=0, le=150000)
    object_propagation: bool = False
    minimum_object_dbz: float = Field(default=10., ge=0, le=30)
    maximum_object_dbz: float = Field(default=55., gt=30, le=60)
    minimum_object_seed_gates: int = Field(default=3, ge=1, le=100)
    minimum_object_seed_fraction: float = Field(default=.30, ge=.1, le=1)
    maximum_object_gates: int = Field(default=5000, ge=1, le=50000)
    maximum_strong_objects: int = Field(default=20000, ge=1, le=100000)
    object_dilation_iterations: int = Field(default=0, ge=0, le=3)
    object_dilation_rays: int = Field(default=3, ge=3, le=11)
    object_dilation_gates: int = Field(default=9, ge=3, le=31)
    temporal_low_rho: "TemporalLowRhoConfig | None" = None

    @model_serializer(mode="wrap")
    def serialize_optional_temporal(self, handler):
        value = handler(self)
        if self.temporal_low_rho is None:
            value.pop("temporal_low_rho", None)
        return value

    @model_validator(mode="after")
    def check(self):
        if self.minimum_dbz >= self.maximum_dbz:
            raise ValueError("strong near reflectivity bounds overlap")
        if self.minimum_object_dbz >= self.maximum_object_dbz:
            raise ValueError("strong near object reflectivity bounds overlap")
        if self.minimum_object_dbz > self.minimum_dbz:
            raise ValueError("strong near object domain must include its seed domain")
        if self.object_dilation_rays % 2 == 0 or self.object_dilation_gates % 2 == 0:
            raise ValueError("strong near dilation stencil dimensions must be odd")
        temporal = self.temporal_low_rho
        if temporal is not None and temporal.minimum_dbz >= temporal.maximum_dbz:
            raise ValueError("temporal low-rho reflectivity bounds overlap")
        return self
