"""Frozen candidate settings. Absent config never changes parent behaviour."""
import hashlib
import json
from typing import Literal
from pydantic import BaseModel, ConfigDict, Field, model_validator


class NearMeasurementConfig(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True, allow_inf_nan=False)
    version: Literal["near-measurement-20260919-v1"] = "near-measurement-20260919-v1"
    mode: Literal["audit", "experiment"] = "audit"
    nonmet_policy: Literal["diagnostic_only", "cr_withhold", "quarantine"] = "cr_withhold"
    uncertainty_policy: Literal["diagnostic_only", "cr_withhold"] = "cr_withhold"
    depolarization_backend: Literal["wradlib", "numpy_reference"] = "wradlib"
    gatefilter_check: Literal["disabled", "pyart"] = "disabled"
    wradlib_version: Literal["2.9.5"] = "2.9.5"
    pyart_version: Literal["2.2.5"] = "2.2.5"
    near_min_m: float = Field(default=2000., ge=0, lt=75000)
    near_max_m: float = Field(default=75000., gt=0, le=150000)
    no_rain_below_dbz: float = Field(default=-10., ge=-32, le=0)
    protected_dbz: float = Field(default=30., ge=25, le=35)
    uncertainty_max_dbz: float = Field(default=20., ge=0, le=25)
    uncertainty_snr_db: Literal[3., 6., 8., 10.] = 8.
    reliable_pol_snr_db: float = Field(default=8., ge=8, le=20)
    dr_nonmet_db: float = Field(default=-12., ge=-15, le=-6)
    maximum_abs_zdr_db: float = Field(default=7.5, gt=0, le=10)
    maximum_ray_spacing_deg: float = Field(default=3., gt=0, le=5)
    neighbourhood_m: float = Field(default=2000., ge=500, le=10000)
    minimum_pol_samples: int = Field(default=6, ge=6, le=500)
    nonmet_fraction: float = Field(default=.6, ge=.6, le=1)
    weather_seed_snr_db: float = Field(default=15., ge=12, le=30)
    weather_seed_rho: float = Field(default=.97, ge=.95, le=1)
    weather_seed_dr_db: float = Field(default=-20., ge=-30, le=-16)
    weather_phase_std_deg: float = Field(default=10., gt=0, le=20)
    weather_phase_coverage: float = Field(default=.6, ge=.5, le=1)
    maximum_weather_fraction: float = Field(default=.2, gt=0, le=.3)
    quarantine_quality: float = Field(default=.2, ge=0, lt=.5)
    maximum_new_qpe_loss_fraction: float = Field(default=.05, ge=0, le=1)
    maximum_new_cr_loss_fraction: float = Field(default=.2, ge=0, le=1)
    maximum_volume_gates: int = Field(default=20000000, ge=1, le=50000000)
    # Limits temporary feature computation; diagnostics are still full native shape.
    maximum_sweep_gates: int = Field(default=5000000, ge=1, le=10000000)
    operational_eligible: Literal[False] = False

    @model_validator(mode="after")
    def validate_bounds(self):
        if not self.near_min_m < self.near_max_m:
            raise ValueError("near range must increase")
        if not self.no_rain_below_dbz < self.uncertainty_max_dbz < self.protected_dbz:
            raise ValueError("no-rain/uncertainty/strong thresholds overlap")
        if self.weather_seed_snr_db < self.reliable_pol_snr_db:
            raise ValueError("weather seed cannot be less reliable than polarization evidence")
        return self

    @property
    def digest(self):
        return hashlib.sha256(json.dumps(self.model_dump(mode="json"), sort_keys=True,
                                         separators=(",", ":")).encode()).hexdigest()
