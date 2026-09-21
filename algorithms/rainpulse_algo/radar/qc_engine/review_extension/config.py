from typing import Literal
import numpy as np
from pydantic import BaseModel, ConfigDict, Field, model_validator, model_serializer


class Frozen(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True, allow_inf_nan=False)


from .radial_revision.config import RadialRevisionConfig


class SourceReviewConfig(Frozen):
    radial_revision: RadialRevisionConfig | None = None
    mode: Literal["audit", "experiment_quarantine"] = "audit"
    narrow_enabled: bool = True
    multiscale_enabled: bool = True
    scales_m: tuple[float, ...] = (5000., 10000., 20000., 50000.)
    minimum_fragment_m: float = Field(default=1500., gt=0)
    maximum_link_gap_m: float = Field(default=3000., ge=0)
    maximum_identity_span_m: float = Field(default=50000., gt=0)
    minimum_contrast_db: float = Field(default=6., gt=0)
    full_score_contrast_db: float = Field(default=12., gt=0)
    maximum_link_delta_db: float = Field(default=3., gt=0)
    bundle_half_widths: tuple[int, ...] = (0, 1, 2)
    maximum_bundle_width_deg: float = Field(default=5., gt=0, le=10)
    flank_rays: int = Field(default=2, ge=1, le=4)
    maximum_objects: int = Field(default=20000, gt=0, le=100000)
    maximum_source_residual_db: float = Field(default=2.5, gt=0, le=2.5)

    @model_validator(mode="after")
    def bounds(self):
        if not self.scales_m or len(self.scales_m) > 8:
            raise ValueError("one to eight physical scales required")
        if tuple(sorted(set(self.scales_m))) != self.scales_m or any(not np.isfinite(x) or x <= 0 or x != int(x) for x in self.scales_m):
            raise ValueError("scales must be unique, increasing, finite integer metres")
        if self.minimum_fragment_m > self.scales_m[0]:
            raise ValueError("fragment scale must not exceed the shortest continuous scale")
        if self.maximum_link_gap_m >= self.maximum_identity_span_m:
            raise ValueError("link cannot span the entire bounded identity")
        if self.full_score_contrast_db <= self.minimum_contrast_db:
            raise ValueError("full contrast must exceed the candidate threshold")
        if not self.bundle_half_widths or any(type(x) is not int or x < 0 or x > 3 for x in self.bundle_half_widths):
            raise ValueError("bundle widths must be integers from zero to three")
        if tuple(sorted(set(self.bundle_half_widths))) != self.bundle_half_widths:
            raise ValueError("bundle widths must be unique and increasing")
        return self


class NearBackgroundAsset(Frozen):
    path: str = Field(min_length=1)
    sha256: str = Field(pattern=r"^[0-9a-f]{64}$")


class NearBackgroundPolicy(Frozen):
    version: Literal["near-background-20260919-v1"]
    target_date: str = Field(pattern=r"^\d{4}-\d{2}-\d{2}$")
    assets: dict[str, NearBackgroundAsset]


from .near_reliability import NearReliabilityConfig


class NonPrecipConfig(Frozen):
    near_reliability: NearReliabilityConfig | None = None
    near_background: NearBackgroundPolicy | None = None
    paired_doppler_enabled: bool = False
    temporal_spatial_matching: bool = False
    match_maximum_azimuth_deg: float = Field(default=.6, gt=0, le=1)
    match_maximum_elevation_deg: float = Field(default=.2, gt=0, le=.3)
    match_maximum_horizontal_m: float = Field(default=750., gt=0, le=1000)
    match_maximum_vertical_m: float = Field(default=250., gt=0, le=500)
    paired_doppler_maximum_seconds: float = Field(default=90., gt=0, le=180)
    mode: Literal["audit", "experiment_quarantine"] = "audit"
    quarantine_classes: tuple[Literal["fixed_ground", "anomalous_propagation", "sea_clutter", "biological", "near_nonmet"], ...] = ("fixed_ground",)
    near_enabled: bool = False
    near_maximum_range_m: float = Field(default=75000., gt=0, le=150000)
    near_maximum_dbz: float = Field(default=30., le=35)
    near_maximum_rhohv: float = Field(default=.8, gt=0, le=.85)
    near_range_window_m: float = Field(default=2000., gt=0, le=10000)
    near_minimum_coverage: float = Field(default=.8, ge=.7, le=1)
    near_minimum_fraction: float = Field(default=.7, ge=.6, le=1)
    quarantine_quality: float = Field(default=0.25, ge=0, lt=0.5)
    maximum_new_eligible_loss_fraction: float = Field(default=0.05, ge=0, le=1)
    minimum_history_lower_bound: float = Field(default=0.80, gt=0.5, lt=1)
    minimum_history_days: int = Field(default=20, ge=20)
    minimum_history_observations: int = Field(default=200, ge=200)
    maximum_history_enhancement_db: float = Field(default=6., gt=0)
    maximum_abs_velocity_ms: float = Field(default=1., gt=0)
    maximum_spectrum_width_ms: float = Field(default=1.5, gt=0)
    minimum_pol_snr_db: float = Field(default=8., ge=8)
    nonmet_rhohv: float = Field(default=0.85, gt=0, lt=0.95)
    weather_rhohv: float = Field(default=0.97, gt=0.95, le=1)
    minimum_temporal_samples: int = Field(default=2, ge=2, le=3)
    temporal_match_db: float = Field(default=3., gt=0)
    temporal_match_fraction: float = Field(default=0.8, gt=0.5, le=1)
    minimum_evidence_families: int = Field(default=3, ge=3, le=5)
    small_object_max_area_km2: float = Field(default=2., gt=0)
    strong_echo_dbz: float = Field(default=35.)

    @model_serializer(mode="wrap")
    def serialize_optional_near(self, handler):
        data = handler(self)
        if self.near_reliability is None:
            data.pop("near_reliability", None)
        return data


class BackgroundPolicy(Frozen):
    minimum_days: int = Field(default=20, ge=20)
    minimum_observations: int = Field(default=200, ge=200)
    echo_threshold_dbz: float = 10.
    confidence_z: float = Field(default=1.96, ge=1.96)
    valid_dbzh_range: tuple[float, float] = (-32., 80.)
    @model_validator(mode="after")
    def check(self):
        if not self.valid_dbzh_range[0] < self.echo_threshold_dbz < self.valid_dbzh_range[1]:
            raise ValueError("echo threshold must lie inside the physical DBZH range")
        return self
