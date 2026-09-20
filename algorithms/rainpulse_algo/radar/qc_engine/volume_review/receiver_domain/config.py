"""Opt-in, uncalibrated coherent receiver-domain research policy."""
import hashlib
import json
from typing import Literal
from pydantic import BaseModel, ConfigDict, Field, model_validator


class ReceiverDomainConfig(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True, allow_inf_nan=False)
    version: Literal["receiver-domain-20260921-v1"] = "receiver-domain-20260921-v1"
    mode: Literal["audit", "cr_only", "quarantine"] = "audit"
    partial_policy: Literal["diagnostic_only", "cr_withhold"] = "diagnostic_only"
    # Only local coherence with an identified origin may be reinterpreted.
    local_policy: Literal["retain_conflict", "source_joint_review"] = "retain_conflict"
    minimum_range_m: float = Field(default=50000., ge=10000)
    block_m: float = Field(default=20000., ge=10000, le=40000)
    guard_blocks: int = Field(default=1, ge=1, le=3)
    minimum_reference_span_m: float = Field(default=80000., ge=60000)
    minimum_reference_blocks: int = Field(default=5, ge=4)
    minimum_snr_db: float = Field(default=20., ge=20)
    maximum_snr_p90_db: float = Field(default=2., gt=0, le=2)
    angular_contrast_db: float = Field(default=6., ge=6)
    maximum_flank_angle_deg: float = Field(default=12., gt=0, le=15)
    minimum_pair_samples: int = Field(default=40, ge=40)
    minimum_pair_blocks: int = Field(default=2, ge=2)
    minimum_samples_per_reference_block: int = Field(default=10, ge=10)
    minimum_pair_span_m: float = Field(default=40000., ge=40000)
    maximum_relation_error_db: float = Field(default=.6, gt=0, le=.6)
    maximum_target_residual_db: float = Field(default=3., gt=0, le=3)
    maximum_phase_p90_deg: float = Field(default=5., gt=0, le=5)
    maximum_zdr_p90_db: float = Field(default=.5, gt=0, le=.5)
    target_phase_tolerance_deg: float = Field(default=5., gt=0, le=5)
    target_zdr_tolerance_db: float = Field(default=.5, gt=0, le=.5)
    minimum_polar_samples: int = Field(default=30, ge=30)
    maximum_abs_reference_zdr_db: float = Field(default=7.5, gt=0, le=10)
    no_rain_below_dbz: float = Field(default=-10., ge=-32, le=0)
    quarantine_quality: float = Field(default=.2, ge=0, lt=.5)
    maximum_new_cr_loss_fraction: float = Field(default=.1, ge=0, le=1)
    maximum_new_qpe_loss_fraction: float = Field(default=.05, ge=0, le=1)
    maximum_sweep_gates: int = Field(default=5000000, ge=1, le=10000000)
    maximum_volume_gates: int = Field(default=20000000, ge=1, le=50000000)
    maximum_folds: int = Field(default=40000, ge=1, le=100000)
    operational_eligible: Literal[False] = False

    @model_validator(mode="after")
    def check(self):
        if self.minimum_pair_span_m > self.minimum_reference_span_m:
            raise ValueError("paired span must fit within receiver reference span")
        return self

    @property
    def digest(self):
        return hashlib.sha256(json.dumps(self.model_dump(mode="json"), sort_keys=True,
                                         separators=(",", ":")).encode()).hexdigest()
