"""Explicit bounded V6 experiments; absent in every frozen pre-V6 profile."""

from __future__ import annotations

from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, model_validator


class ResidualConfig(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True, allow_inf_nan=False)
    method: Literal["native-residual-v6"] = "native-residual-v6"
    repair_range_links: bool = True
    narrow_enabled: bool = True
    association_enabled: bool = True
    speckle_enabled: bool = True
    link_maximum_residual_db: float = Field(default=8, gt=0, le=15)
    link_maximum_gap_m: float = Field(default=1000, ge=0, le=3000)
    link_maximum_fraction: float = Field(default=0.1, ge=0, le=0.2)
    minimum_range_m: float = Field(default=10000, ge=0)
    minimum_echo_dbz: float = -5
    narrow_offsets_deg: tuple[float, ...] = (1, 2, 4, 8)
    narrow_minimum_contrast_db: float = Field(default=8, gt=0)
    narrow_minimum_span_m: float = Field(default=12000, ge=2000)
    narrow_minimum_measured_m: float = Field(default=8000, ge=1000)
    narrow_maximum_gap_m: float = Field(default=1500, ge=0, le=5000)
    narrow_maximum_gap_fraction: float = Field(default=0.2, ge=0, le=0.35)
    narrow_minimum_evidence_fraction: float = Field(default=0.5, gt=0, le=1)
    narrow_maximum_width_deg: float = Field(default=5, gt=0, le=12)
    narrow_minimum_aspect: float = Field(default=4, gt=1)
    narrow_model_p90_db: float = Field(default=5, gt=0, le=10)
    single_field_minimum_m: float = Field(default=60000, ge=20000)
    single_field_maximum_width_deg: float = Field(default=2.5, gt=0, le=5)
    peripheral_range_m: float = Field(default=1000, ge=0, le=3000)
    peripheral_angle_deg: float = Field(default=1.5, ge=0, le=3)
    peripheral_cross_range_m: float = Field(default=1500, ge=0, le=5000)
    peripheral_difference_db: float = Field(default=4, gt=0, le=10)
    speckle_maximum_area_km2: float = Field(default=5, gt=0, le=50)
    speckle_maximum_span_m: float = Field(default=5000, ge=250, le=15000)
    speckle_maximum_dbz: float = Field(default=30, le=35)
    speckle_observation_fraction: float = Field(default=0.85, gt=0, le=1)
    speckle_maximum_raw_echo_fraction: float = Field(default=0.25, ge=0, le=0.5)
    speckle_window_gates: int = Field(default=7, ge=3, le=31)
    minimum_snr_db: float = Field(default=8, ge=0)
    suspect_rhohv: float = Field(default=0.9, gt=0, le=1)
    quarantine_quality: float = Field(default=0.2, ge=0, lt=1)
    maximum_objects: int = Field(default=10000, ge=1, le=100000)
    maximum_segments: int = Field(default=20000, ge=1, le=100000)

    @model_validator(mode="after")
    def consistent(self):
        if self.narrow_minimum_measured_m > self.narrow_minimum_span_m:
            raise ValueError("narrow measured support cannot exceed minimum span")
        if self.narrow_maximum_gap_m >= self.narrow_minimum_measured_m:
            raise ValueError("gaps cannot replace measured narrow support")
        if not self.narrow_offsets_deg or any(not 0 < x < 90 for x in self.narrow_offsets_deg):
            raise ValueError("explicit finite shoulder offsets in (0,90) required")
        if self.speckle_window_gates % 2 != 1:
            raise ValueError("speckle window must be odd")
        return self
