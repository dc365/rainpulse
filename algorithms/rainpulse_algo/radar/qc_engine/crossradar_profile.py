"""Capability-specific V5 policy. No station-name or azimuth deletion rules."""

from __future__ import annotations

from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, model_validator


class Config(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True, allow_inf_nan=False)


class VerifiedCeiling(Config):
    radar_config_version: str = Field(min_length=1)
    sweep: str = Field(pattern=r"^sweep_\d{3}$")
    value_dbz: float = Field(ge=40, le=80)
    source_sha256: str = Field(pattern=r"^[0-9a-f]{64}$")
    evidence_uri: str = Field(min_length=1)
    semantics: Literal["verified_reflectivity_output_ceiling"]


class CrossRadarConfig(Config):
    method: Literal["capability-range-signature-v5"] = "capability-range-signature-v5"
    minimum_range_m: float = Field(default=20000, ge=1000)
    minimum_echo_dbz: float = 10.0
    minimum_span_m: float = Field(default=100000, ge=30000)
    minimum_measured_length_m: float = Field(default=80000, ge=20000)
    minimum_growth_db: float = Field(default=10, ge=3)
    minimum_log_range_span_db: float = Field(default=8, ge=3)
    minimum_high_dbz: float = 45.0
    minimum_high_fraction: float = Field(default=0.6, gt=0, le=1)
    maximum_gap_m: float = Field(default=1000, ge=0, le=5000)
    maximum_gap_fraction: float = Field(default=0.10, ge=0, le=0.25)
    residual_p90_db: float = Field(default=2.5, gt=0, le=8)
    gate_residual_db: float = Field(default=3.5, gt=0, le=12)
    minimum_fit_span_m: float = Field(default=50000, ge=10000)
    minimum_fit_samples: int = Field(default=30, ge=10)
    minimum_plateau_length_m: float = Field(default=20000, ge=5000)
    plateau_tolerance_db: float = Field(default=0.26, gt=0, le=1)
    numeric_plateau_spread_db: float = Field(default=0.02, gt=0, le=0.1)
    plateau_minimum_dbz: float = Field(default=60, ge=40)
    maximum_width_deg: float = Field(default=70, gt=0, lt=180)
    minimum_aspect: float = Field(default=2, gt=1)
    maximum_segments: int = Field(default=20000, ge=1, le=100000)
    minimum_snr_db: float = Field(default=8, ge=0)
    quarantine_quality: float = Field(default=0.2, ge=0, lt=1)
    single_field_action: Literal["quarantine", "research_reject"] = "quarantine"
    single_field_validation_sha256: str | None = Field(default=None, pattern=r"^[0-9a-f]{64}$")
    verified_ceilings: tuple[VerifiedCeiling, ...] = ()
    paper_gap_m: float = Field(default=1000, ge=0, le=3000)
    paper_gap_fraction: float = Field(default=0.15, ge=0, le=0.25)
    distance_bands_m: tuple[float, ...] = (0, 100000, 200000, 300000, 500000)

    @model_validator(mode="after")
    def consistent(self):
        if self.minimum_measured_length_m > self.minimum_span_m:
            raise ValueError("measured length cannot exceed minimum span")
        if self.minimum_fit_span_m > self.minimum_span_m:
            raise ValueError("fit span cannot exceed minimum span")
        if self.gate_residual_db < self.residual_p90_db:
            raise ValueError("gate residual must cover fit tolerance")
        if self.maximum_gap_m >= self.minimum_fit_span_m:
            raise ValueError("gaps cannot replace a measured fit")
        if (
            self.single_field_action == "research_reject"
            and not self.single_field_validation_sha256
        ):
            raise ValueError(
                "single-field research confirmation requires a frozen validation receipt"
            )
        keys = [(x.radar_config_version, x.sweep) for x in self.verified_ceilings]
        if len(keys) != len(set(keys)):
            raise ValueError("ambiguous output ceiling metadata")
        if (
            len(self.distance_bands_m) < 2
            or self.distance_bands_m[0] != 0
            or any(
                a >= b
                for a, b in zip(self.distance_bands_m[:-1], self.distance_bands_m[1:], strict=True)
            )
        ):
            raise ValueError("distance bands must increase from zero")
        return self
