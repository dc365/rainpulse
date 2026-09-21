"""Frozen settings with optional episode assets and no changed parent defaults."""
from typing import Literal
import hashlib
import json
from pydantic import BaseModel, ConfigDict, Field, model_validator
from ..episode_background.config import EpisodeConfig
from . import VERSION


class ClutterFusionConfig(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True, allow_inf_nan=False)
    version: Literal["clutter-fusion-20260921-v1"] = VERSION
    mode: Literal["audit", "cr_withhold", "quarantine"] = "audit"
    operational_eligible: Literal[False] = False
    background: EpisodeConfig = Field(default_factory=EpisodeConfig)
    depolarization_backend: Literal["wradlib", "numpy_reference"] = "wradlib"
    gatefilter_check: Literal["disabled", "pyart"] = "disabled"
    wradlib_version: Literal["2.9.5"] = "2.9.5"
    pyart_version: Literal["2.2.5"] = "2.2.5"
    minimum_range_m: float = Field(default=2000., ge=0, le=10000)
    maximum_range_m: float = Field(default=75000., gt=0, le=150000)
    no_rain_below_dbz: float = Field(default=-10., ge=-32, le=0)
    protected_dbz: float = Field(default=30., ge=25, le=35)
    minimum_snr_db: float = Field(default=8., ge=8, le=20)
    biological_minimum_snr_db: float = Field(default=12., ge=8, le=25)
    biological_maximum_dbz: float = Field(default=25., gt=0, le=30)
    maximum_abs_zdr_db: float = Field(default=7.5, gt=0, le=10)
    neighbourhood_m: float = Field(default=2000., ge=500, le=10000)
    minimum_support_fraction: float = Field(default=.6, ge=.5, le=1)
    minimum_samples: int = Field(default=6, ge=6, le=500)
    neighbour_nonmet_fraction: float = Field(default=.6, ge=.6, le=1)
    maximum_ray_spacing_deg: float = Field(default=3., gt=0, le=5)
    # Phase increments are adjacent native gates. Only this bounded spacing range
    # is accepted; thresholds describe adjacent-gate jitter, never CPA/SQI.
    minimum_phase_spacing_m: float = Field(default=100., ge=50, le=500)
    maximum_phase_spacing_m: float = Field(default=500., ge=100, le=1000)
    polar_structure_threshold: float = Field(default=.65, ge=.6, le=1)
    biological_threshold: float = Field(default=.7, ge=.65, le=1)
    ground_threshold: float = Field(default=.7, ge=.65, le=1)
    minimum_quarantine_families: int = Field(default=2, ge=2, le=4)
    local_conflict_policy: Literal["retain", "cr_withhold"] = "retain"
    vertical_policy: Literal["diagnostic_only", "verified_beam"] = "diagnostic_only"
    paired_doppler_policy: Literal["diagnostic_only", "verified_pair"] = "diagnostic_only"
    maximum_context_seconds: float = Field(default=180., gt=0, le=300)
    maximum_doppler_seconds: float = Field(default=90., gt=0, le=180)
    minimum_vertical_delta_m: float = Field(default=400., ge=200, le=1000)
    maximum_vertical_delta_m: float = Field(default=1800., ge=500, le=3000)
    maximum_doppler_delta_m: float = Field(default=250., gt=0, le=500)
    maximum_doppler_elevation_deg: float = Field(default=.25, gt=0, le=.4)
    maximum_horizontal_error_m: float = Field(default=1000., gt=0, le=2000)
    quarantine_quality: float = Field(default=.2, ge=0, lt=.5)
    maximum_new_cr_loss_fraction: float = Field(default=.2, ge=0, le=1)
    maximum_new_qpe_loss_fraction: float = Field(default=.05, ge=0, le=1)
    maximum_volume_gates: int = Field(default=20000000, ge=1, le=50000000)
    maximum_sweep_gates: int = Field(default=5000000, ge=1, le=10000000)
    maximum_context_pairs: int = Field(default=100000000, ge=1, le=500000000)

    @model_validator(mode="after")
    def checks(self):
        if not self.minimum_range_m < self.maximum_range_m:
            raise ValueError("invalid clutter domain")
        if not self.no_rain_below_dbz < self.biological_maximum_dbz <= self.protected_dbz:
            raise ValueError("no-rain/biological/strong boundaries overlap")
        if not self.minimum_vertical_delta_m < self.maximum_vertical_delta_m:
            raise ValueError("vertical limits overlap")
        if self.minimum_phase_spacing_m > self.maximum_phase_spacing_m:
            raise ValueError("phase spacing bounds overlap")
        b = self.background
        if b.mode != "audit" or b.review_local_weather_conflicts or b.withhold_mixed:
            raise ValueError("nested episode must supply evidence only; no separate EBG actions")
        if b.no_rain_below_dbz != self.no_rain_below_dbz or b.protected_dbz != self.protected_dbz:
            raise ValueError("background and fusion reflectivity semantics differ")
        return self

    @property
    def digest(self):
        return hashlib.sha256(json.dumps(self.model_dump(mode="json"), sort_keys=True,
                                         separators=(",", ":")).encode()).hexdigest()
