"""Versioned, opt-in settings. Short-episode support is not climate confidence."""
from __future__ import annotations

import hashlib
import json
from datetime import date
from typing import Literal
from pydantic import BaseModel, ConfigDict, Field, model_validator
from . import VERSION


class Frozen(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True, allow_inf_nan=False)

    @property
    def digest(self) -> str:
        data = json.dumps(self.model_dump(mode="json"), sort_keys=True, separators=(",", ":"))
        return hashlib.sha256(data.encode()).hexdigest()


class BuildConfig(Frozen):
    version: Literal["short-episode-background-20260920-v1"] = VERSION
    grade: Literal["short_episode"] = "short_episode"
    block_seconds: int = Field(default=1800, ge=600, le=3600)
    minimum_samples: int = Field(default=12, ge=8, le=128)
    minimum_blocks: int = Field(default=3, ge=3, le=8)
    minimum_samples_per_block: int = Field(default=2, ge=2, le=12)
    minimum_episode_span_seconds: int = Field(default=3600, ge=1800, le=14400)
    minimum_measured_fraction: float = Field(default=.75, ge=.6, le=1)
    maximum_dbzh_block_drift: float = Field(default=4., gt=0, le=10)
    maximum_snr_block_drift: float = Field(default=4., gt=0, le=10)
    no_rain_below_dbz: float = Field(default=-10., ge=-32, le=0)
    maximum_range_m: float = Field(default=75000., gt=1000, le=150000)
    maximum_azimuth_error_deg: float = Field(default=.5, gt=0, le=1)
    maximum_elevation_error_deg: float = Field(default=.2, gt=0, le=.3)
    maximum_range_error_m: float = Field(default=250., gt=0, le=500)
    zdr_tail_db: float = Field(default=7.5, ge=5, le=10)
    minimum_polar_snr_db: float = Field(default=8., ge=8, le=20)
    maximum_samples: int = Field(default=128, ge=12, le=256)
    maximum_sweep_cells: int = Field(default=1000000, ge=1, le=4000000)
    maximum_asset_array_bytes: int = Field(default=512 * 1024**2, ge=1024, le=2 * 1024**3)
    maximum_stack_elements: int = Field(default=32000000, ge=1000, le=64000000)

    @model_validator(mode="after")
    def check(self):
        if self.minimum_samples > self.maximum_samples:
            raise ValueError("sample bounds overlap")
        return self


class AssetBinding(Frozen):
    path: str = Field(min_length=1)
    sha256: str = Field(pattern=r"^[a-f0-9]{64}$")


class EpisodeConfig(Frozen):
    version: Literal["short-episode-background-20260920-v1"] = VERSION
    mode: Literal["audit", "experiment_cr"] = "audit"
    assets: dict[str, AssetBinding] = Field(default_factory=dict)
    temporal_policy: Literal["causal", "retrospective"] = "causal"
    retrospective_target_dates: tuple[str, ...] = ()
    maximum_age_hours: float = Field(default=12., gt=0, le=168)
    # A positive, attributable context remains protected in all modes. Local
    # conflict review may label mixed, but never relabel it as confirmed clutter.
    review_local_weather_conflicts: bool = False
    withhold_mixed: bool = False
    minimum_range_m: float = Field(default=2000., ge=0, le=10000)
    maximum_range_m: float = Field(default=75000., gt=0, le=150000)
    no_rain_below_dbz: float = Field(default=-10., ge=-32, le=0)
    protected_dbz: float = Field(default=30., ge=25, le=35)
    minimum_snr_db: float = Field(default=8., ge=8, le=20)
    nonmet_rho: float = Field(default=.85, gt=0, le=.9)
    minimum_feature_samples: int = Field(default=8, ge=6, le=64)
    maximum_normalized_distance: float = Field(default=3., ge=1, le=4)
    maximum_dbzh_enhancement_db: float = Field(default=3., gt=0, le=6)
    tail_match_fraction: float = Field(default=.7, ge=.6, le=1)
    maximum_new_cr_loss_fraction: float = Field(default=.2, ge=0, le=1)
    maximum_volume_gates: int = Field(default=20000000, ge=1, le=50000000)
    maximum_asset_bytes: int = Field(default=512 * 1024**2, ge=1024, le=2 * 1024**3)
    operational_eligible: Literal[False] = False

    @model_validator(mode="after")
    def check(self):
        if not self.minimum_range_m < self.maximum_range_m:
            raise ValueError("invalid background application range")
        if self.withhold_mixed and not self.review_local_weather_conflicts:
            raise ValueError("mixed withholding requires explicit local conflict review")
        if self.temporal_policy == "retrospective" and not self.retrospective_target_dates:
            raise ValueError("retrospective use requires explicit target dates")
        if self.temporal_policy == "causal" and self.retrospective_target_dates:
            raise ValueError("causal policy cannot declare retrospective dates")
        for item in self.retrospective_target_dates:
            if date.fromisoformat(item).isoformat() != item:
                raise ValueError("target date must be ISO YYYY-MM-DD")
        if any(not name or name != name.lower() for name in self.assets):
            raise ValueError("registry keys must be stable lowercase station IDs")
        return self
