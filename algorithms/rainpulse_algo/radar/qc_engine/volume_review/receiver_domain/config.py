"""Opt-in, uncalibrated coherent receiver-domain research policy."""
import hashlib
import json
from typing import Literal
from pydantic import BaseModel, ConfigDict, Field, model_validator, model_serializer


class SegmentReferenceConfig(BaseModel):
    """Finite, intermittent receiver states; research fallback, not a short-blob filter."""
    model_config = ConfigDict(extra="forbid", frozen=True, allow_inf_nan=False)
    version: Literal["receiver-segment-reference-v1"] = "receiver-segment-reference-v1"
    mode: Literal["audit", "experiment"] = "audit"
    partial_policy: Literal["diagnostic_only", "cr_withhold"] = "diagnostic_only"
    reference_minimum_range_m: float = Field(default=10000., ge=10000.)
    minimum_reference_blocks: int = Field(default=3, ge=3)
    minimum_reference_span_m: float = Field(default=40000., ge=40000.)
    minimum_reference_support_m: float = Field(default=12000., ge=10000.)
    state_separation_db: float = Field(default=6., ge=6.)
    maximum_states: int = Field(default=3, ge=1, le=3)
    maximum_reference_distance_m: float = Field(default=50000., gt=0., le=50000.)
    crosscheck_minimum_span_m: float = Field(default=10000., ge=5000.)
    maximum_state_trials: int = Field(default=50000, ge=1, le=100000)


class SourceFamilyConfig(BaseModel):
    """One-hop shared coherent reference. No station/angle/target-value rules."""
    model_config = ConfigDict(extra="forbid", frozen=True, allow_inf_nan=False)
    version: Literal["receiver-source-family-20260921-v1"] = "receiver-source-family-20260921-v1"
    mode: Literal["audit", "experiment"] = "audit"
    partial_policy: Literal["diagnostic_only", "cr_withhold"] = "diagnostic_only"
    full_policy: Literal["cr_only", "quarantine"] = "cr_only"
    maximum_neighbor_angle_deg: float = Field(default=3., gt=0., le=5.)
    minimum_donors: int = Field(default=2, ge=2, le=4)
    maximum_donors: int = Field(default=6, ge=2, le=12)
    maximum_donor_phase_difference_deg: float = Field(default=2., gt=0., le=5.)
    maximum_donor_zdr_difference_db: float = Field(default=.25, gt=0., le=.5)
    maximum_donor_offset_difference_db: float = Field(default=.5, gt=0., le=1.)
    maximum_donor_relation_difference_db: float = Field(default=1., gt=0., le=1.)
    maximum_ray_bias_db: float = Field(default=1., gt=0., le=1.)
    local_block_m: float = Field(default=2000., ge=1000., le=5000.)
    minimum_local_support_m: float = Field(default=1000., ge=500.)
    minimum_own_support_m: float = Field(default=4000., ge=4000.)
    minimum_own_blocks: int = Field(default=2, ge=2)
    maximum_states: int = Field(default=3, ge=1, le=3)
    maximum_state_center_spread_db: float = Field(default=2., gt=0., le=2.)
    maximum_elevation_difference_deg: float = Field(default=.2, gt=0., le=.3)
    maximum_time_difference_s: float = Field(default=120., gt=0., le=180.)
    require_ray_times: bool = True
    maximum_family_folds: int = Field(default=40000, ge=1, le=100000)
    maximum_donor_trials: int = Field(default=200000, ge=1, le=500000)
    maximum_family_models: int = Field(default=10000, ge=1, le=40000)
    maximum_ordered_blocks: int = Field(default=1000000, ge=1, le=3000000)

    @model_validator(mode="after")
    def bounds(self):
        if self.minimum_donors > self.maximum_donors:
            raise ValueError("minimum donors exceeds bounded donor budget")
        if self.minimum_local_support_m > self.local_block_m:
            raise ValueError("local observed support must fit in the physical block")
        if self.minimum_own_support_m < 2*self.minimum_local_support_m:
            raise ValueError("own support must contain separate reference blocks")
        return self


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

    segment_reference: SegmentReferenceConfig | None = None
    source_family: SourceFamilyConfig | None = None

    @model_serializer(mode="wrap")
    def serialize_optional_segment(self, handler):
        value = handler(self)
        if self.segment_reference is None:
            value.pop("segment_reference", None)
        if self.source_family is None:
            value.pop("source_family", None)
        return value

    @model_validator(mode="after")
    def check(self):
        if self.minimum_pair_span_m > self.minimum_reference_span_m:
            raise ValueError("paired span must fit within receiver reference span")
        if self.segment_reference is not None:
            seg = self.segment_reference
            if seg.reference_minimum_range_m > self.minimum_range_m:
                raise ValueError("segment reference must cover the target minimum range")
            if self.minimum_pair_span_m > seg.minimum_reference_span_m:
                raise ValueError("segment reference must contain the required paired span")
        if self.source_family is not None and self.source_family.maximum_neighbor_angle_deg > self.maximum_flank_angle_deg:
            raise ValueError("source family must fit inside the flank search geometry")
        return self

    @property
    def digest(self):
        return hashlib.sha256(json.dumps(self.model_dump(mode="json"), sort_keys=True,
                                         separators=(",", ":")).encode()).hexdigest()
