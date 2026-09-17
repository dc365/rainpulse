"""Project experiment settings, not calibrated MRMS/MIT/SWAN thresholds."""
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, model_validator


class RadialClutterConfig(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True, allow_inf_nan=False)
    method: Literal["radial-clutter-rc1"] = "radial-clutter-rc1"
    mode: Literal["audit", "experiment_quarantine"] = "audit"
    acknowledge_uncalibrated: bool = False
    radial_enabled: bool = True
    clutter_enabled: bool = True
    radial_echo_dbz: float = Field(default=5, ge=-10, le=35)
    radial_lengths_m: tuple[float, ...] = (5000, 10000, 20000, 50000)
    minimum_fragment_m: float = Field(default=500, gt=0)
    minimum_measured_m: float = Field(default=5000, gt=0)
    maximum_gap_m: float = Field(default=1000, ge=0, le=5000)
    maximum_gap_fraction: float = Field(default=0.15, ge=0, lt=0.5)
    maximum_radial_width_deg: float = Field(default=12, gt=0, le=30)
    shoulder_offsets_deg: tuple[float, ...] = (1, 2, 4, 8, 16)
    shoulder_contrast_db: float = Field(default=6, gt=0)
    minimum_reliable_snr_db: float = Field(default=8, ge=3, le=20)
    low_rho: float = Field(default=0.8, gt=0, lt=1)
    phase_increment_std_deg: float = Field(default=20, gt=0, le=90)
    feature_window_m: float = Field(default=2000, gt=0)
    ground_velocity_mps: float = Field(default=1, gt=0, le=3)
    ground_width_mps: float = Field(default=1, gt=0, le=3)
    strong_prior_lower_bound: float = Field(default=0.9, gt=0.5, lt=1)
    prior_minimum_days: int = Field(default=20, ge=2)
    prior_minimum_observations: int = Field(default=200, ge=2)
    prior_azimuth_tolerance_fraction: float = Field(default=0.4, gt=0, lt=0.5)
    healthy_rho: float = Field(default=0.97, gt=0.8, le=1)
    healthy_phase_increment_deg: float = Field(default=5, gt=0, le=15)
    small_object_area_km2: float = Field(default=4, gt=0)
    maximum_new_loss_fraction: float = Field(default=0.1, gt=0, le=1)
    quarantine_quality: float = Field(default=0.25, ge=0, lt=0.5)
    maximum_objects: int = Field(default=20000, ge=1)
    maximum_gates: int = Field(default=12000000, ge=100)

    @model_validator(mode="after")
    def checked(self):
        if self.mode != "audit" and not self.acknowledge_uncalibrated:
            raise ValueError("experimental quarantine requires explicit acknowledgement")
        for name in ("radial_lengths_m", "shoulder_offsets_deg"):
            values = getattr(self, name)
            if not values or tuple(sorted(set(values))) != values or min(values) <= 0:
                raise ValueError(f"{name} must be finite unique increasing positive values")
        if max(self.shoulder_offsets_deg) >= 90:
            raise ValueError("shoulder search must be bounded below 90 degrees")
        if self.minimum_fragment_m > self.minimum_measured_m:
            raise ValueError("fragment threshold cannot exceed object measured support")
        if self.low_rho >= self.healthy_rho:
            raise ValueError("incompatible low/healthy correlation settings")
        return self
