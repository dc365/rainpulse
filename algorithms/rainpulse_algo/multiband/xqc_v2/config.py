"""Separate X policy: never relax the existing S policy's validation bounds."""
from __future__ import annotations
import hashlib
import json
from typing import Literal
from pydantic import BaseModel, ConfigDict, Field, model_validator, StrictBool
from rainpulse_algo.radar.qc_engine.volume_review.receiver_domain.config import (
    ReceiverDomainConfig, SegmentReferenceConfig, SourceFamilyConfig,
)
from rainpulse_algo.radar.qc_engine.volume_review.config import VolumeReviewConfig
from rainpulse_algo.radar.qc_engine.volume_review.clutter_fusion.config import ClutterFusionConfig
from rainpulse_algo.radar.qc_engine.volume_review.clutter_fusion.isolation_config import IsolationConfig
from . import VERSION
from .limited_context import ContextPolicy
from .polar_morphology import MorphologyPolicy
from .snr_carriers import SNRCarrierPolicy


class XSegmentConfig(SegmentReferenceConfig):
    reference_minimum_range_m: float = Field(default=1500., ge=500.)
    minimum_reference_span_m: float = Field(default=12000., ge=6000.)
    minimum_reference_support_m: float = Field(default=6000., ge=3000.)
    maximum_reference_distance_m: float = Field(default=20000., gt=0., le=50000.)
    crosscheck_minimum_span_m: float = Field(default=5000., ge=2000.)


class XReceiverConfig(ReceiverDomainConfig):
    """The same fitted receiver core, with independently versioned X distances.

    S defaults and residual/polar tests are inherited unchanged. At least three
    separated raw reference blocks remain after target and guard exclusion.
    """
    local_policy: Literal["retain_conflict", "source_joint_review"] = "source_joint_review"
    minimum_range_m: float = Field(default=1500., ge=500., le=20000.)
    block_m: float = Field(default=5000., ge=1000., le=20000.)
    minimum_reference_span_m: float = Field(default=20000., ge=6000., le=80000.)
    minimum_reference_blocks: int = Field(default=3, ge=3)
    minimum_pair_span_m: float = Field(default=8000., ge=3000., le=40000.)
    segment_reference: XSegmentConfig | None = None
    source_family: SourceFamilyConfig | None = None
    maximum_folds: int = Field(default=12000, ge=1, le=40000)


def default_clutter():
    # Backend is explicit in profiles. No silent fallback from wradlib.
    return ClutterFusionConfig(
        mode="quarantine", minimum_range_m=750., maximum_range_m=75000.,
        minimum_phase_spacing_m=50., maximum_phase_spacing_m=500.,
        neighbourhood_m=1000., maximum_sweep_gates=2_000_000,
        maximum_volume_gates=2_000_000,
        isolated_objects=IsolationConfig(mode="audit"),
    )


class XQCConfig(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True, allow_inf_nan=False)
    version: Literal["x-shared-qc-20260928-v2"] = VERSION
    mode: Literal["audit", "cr_only", "quarantine"] = "audit"
    operational_eligible: Literal[False] = False
    receiver_enabled: StrictBool = True
    radial_objects_enabled: StrictBool = True
    clutter_enabled: StrictBool = True
    isolation_enabled: StrictBool = True
    morphology: MorphologyPolicy | None = None
    receiver: XReceiverConfig = Field(default_factory=XReceiverConfig)
    clutter: ClutterFusionConfig = Field(default_factory=default_clutter)
    objects: VolumeReviewConfig = Field(default_factory=lambda: VolumeReviewConfig(
        levels_dbz=(5., 15., 25., 35.), scales_m=(2000, 5000, 10000, 20000),
    ))
    maximum_sweep_gates: int = Field(default=2_000_000, ge=1, le=5_000_000)
    maximum_evidence_bytes: int = Field(default=4 * 1024**2, ge=4096, le=16 * 1024**2)
    maximum_new_exclusion_fraction: float = Field(default=.35, gt=0., le=1.)
    maximum_gap_deg: float = Field(default=3., gt=.01, le=10.)
    maximum_neighbor_time_s: float = Field(default=120., gt=0., le=300.)
    maximum_neighbor_elevation_deg: float = Field(default=.3, gt=0., le=1.)
    radial_flank_deg: float = Field(default=6., gt=0., le=15.)
    radial_flank_contrast_db: float = Field(default=8., ge=6., le=20.)
    # "both" keeps the S parity contract: a quiet comparison must exist on each
    # side. "either" admits X interference that bleeds into the directly
    # adjacent ray, hiding one side; the polar conjunction still applies.
    radial_flank_mode: Literal["both", "either"] = "both"
    radial_minimum_snr_db: float = Field(default=12., ge=8., le=30.)
    radial_maximum_rhohv: float = Field(default=.8, gt=0., le=.9)
    radial_phase_jitter_deg: float = Field(default=20., ge=10., le=60.)
    radial_maximum_dbzh: float = Field(default=35., ge=25., le=45.)
    radial_source_local_policy: Literal["protect", "joint_evidence"] = "protect"
    radial_source_enabled: StrictBool = False
    radial_source_block_model_enabled: StrictBool = False
    radial_source_fan_model_enabled: StrictBool = False
    near_floor_source_candidates_enabled: StrictBool = False
    native_alternative_source_candidates_enabled: StrictBool = False
    polar_window_candidates_enabled: StrictBool = False
    fragmented_carrier_candidates_enabled: StrictBool = False
    snr_carrier_policy: SNRCarrierPolicy | None = None
    complete_source_families_enabled: StrictBool = False
    complete_source_family_reference_mode: Literal[
        "absolute_noise", "relative_receiver", "heldout_family"
    ] = (
        "absolute_noise"
    )
    radial_source_maximum_width_deg: float = Field(default=3., gt=0., le=7.)
    radial_source_minimum_span_m: float = Field(default=20000., ge=10000., le=50000.)
    radial_source_minimum_fraction: float = Field(default=.7, ge=.7, le=1.)
    radial_source_maximum_gap_m: float = Field(default=1500., ge=0., le=2000.)
    radial_source_maximum_spread_db: float = Field(default=3., gt=0., le=3.)
    source_maximum_trials: int = Field(strict=True, default=500000, ge=1, le=2000000)
    source_maximum_models: int = Field(strict=True, default=50000, ge=1, le=100000)
    source_maximum_summary_bytes: int = Field(strict=True, default=32 * 1024**2, ge=4096, le=128 * 1024**2)
    # Same-ray fragment completion around confirmed anchors (S association
    # contract): 0 disables; targets still need local polarimetric badness and
    # the shared rho/snr/dbzh caps, so rain stays structurally excluded.
    fragment_maximum_distance_m: float = Field(default=0., ge=0., le=30000.)
    fragment_minimum_anchor_gates: int = Field(default=4, ge=2, le=64)
    fragment_association_difference_db: float = Field(default=8., ge=4., le=12.)
    fragment_minimum_echo_dbz: float = Field(default=-5., ge=-20., le=10.)
    fragment_phase_window_gates: int = Field(default=5, ge=3, le=21)
    fragment_phase_minimum_fraction: float = Field(default=.8, gt=.5, le=1.)
    fragment_phase_variance_minimum: float = Field(default=.085, gt=0., lt=1.)
    # Detection-floor censor: below this SNR the DBZH processor emits
    # noise-floor + 20log10(r) values that render as false distant echo.
    # None disables the censor (the default keeps S-parity behaviour).
    # The integrity cap and coverage floor guard a broken SNR field: a censor
    # that would swallow the sweep abstains entirely instead.
    # Independent native gate validity applies even at ambiguous bearings;
    # spatial source algorithms retain their separate angular barriers.
    noise_censor_snr_db: float | None = Field(default=None, ge=0., le=10.)
    noise_censor_maximum_fraction: float = Field(default=.8, gt=0., le=1.)
    noise_censor_minimum_coverage: float = Field(default=.5, gt=0., le=1.)
    # No velocity/waveform contract is inferred from a field name.
    doppler_verified: StrictBool = False
    doppler_verification_id: str | None = None
    doppler_waveform: str | None = None
    nyquist_velocity_mps: float | None = Field(default=None, gt=0., le=200.)
    # This only tightens a provided PHASE_VALID+LIQUID path, never fabricates one.
    phase_minimum_snr_db: float = Field(default=10., ge=3., le=30.)
    phase_minimum_rhohv: float = Field(default=.95, ge=.9, le=1.)
    phase_maximum_abs_zdr_db: float = Field(default=5., gt=0., le=7.5)
    export_native: StrictBool = True
    context: ContextPolicy = Field(default_factory=ContextPolicy)

    @model_validator(mode="after")
    def contracts(self):
        if self.snr_carrier_policy is not None:
            if (not self.complete_source_families_enabled or self.morphology is None
                    or not self.morphology.compact_counterexamples_enabled):
                raise ValueError('SNR carrier review requires complete source and weather protection')
            if self.snr_carrier_policy.geometry != self.morphology:
                raise ValueError('SNR geometry cannot relax the existing morphology contract')
        if self.fragmented_carrier_candidates_enabled and (
            self.morphology is None or not self.morphology.compact_counterexamples_enabled
        ):
            raise ValueError('fragmented carrier candidates require complete compact protection')
        if self.complete_source_families_enabled and not self.radial_source_fan_model_enabled:
            raise ValueError('complete source families require the held-out receiver-fan model')
        if (self.complete_source_family_reference_mode != 'absolute_noise'
                and not self.complete_source_families_enabled):
            raise ValueError('relative receiver families require explicit candidate activation')
        if self.polar_window_candidates_enabled and (
            self.morphology is None or not self.morphology.compact_counterexamples_enabled
        ):
            raise ValueError('polar window candidates require complete compact protection')
        if (self.native_alternative_source_candidates_enabled
                and not self.near_floor_source_candidates_enabled):
            raise ValueError("native alternative candidates require complete near-floor evidence")
        if self.near_floor_source_candidates_enabled and (
            self.noise_censor_snr_db is None or self.morphology is None
            or not self.morphology.compact_counterexamples_enabled
        ):
            raise ValueError(
                'near-floor source candidates require receiver floor and complete compact protection'
            )
        if (self.radial_source_block_model_enabled or self.radial_source_fan_model_enabled) and not self.radial_source_enabled:
            raise ValueError("block/fan source model requires radial source evidence")
        if self.radial_source_enabled and self.noise_censor_snr_db is None:
            raise ValueError("radial source evidence requires an explicit receiver noise floor")
        for name in ("receiver_enabled", "radial_objects_enabled", "clutter_enabled", "isolation_enabled",
                     "doppler_verified", "export_native"):
            if type(getattr(self, name)) is not bool:
                raise ValueError("explicit boolean required: " + name)
        if self.doppler_verified and (not self.doppler_verification_id or not self.doppler_waveform or self.nyquist_velocity_mps is None):
            raise ValueError("Doppler use requires a verification identity and Nyquist velocity")
        if self.clutter.gatefilter_check == "pyart":
            raise ValueError("X-v2 uses its native admission validator; S NativeSweep GateFilter is not adapted")
        if self.clutter.near_revision is not None:
            raise ValueError("X-v2 does not silently import the S near/history/DEM runtime")
        if self.objects.receiver_domain or self.objects.clutter_fusion or self.objects.near_measurement:
            raise ValueError("object proposer cannot run nested S dispositions")
        if self.clutter.no_rain_below_dbz != self.receiver.no_rain_below_dbz:
            raise ValueError("shared branches disagree on reflectivity semantics")
        if self.isolation_enabled and not self.clutter_enabled:
            raise ValueError("isolated-object review requires current clutter features")
        # CF computes evidence; the X finalizer is the sole action owner.
        return self

    @property
    def digest(self):
        data = self.model_dump(mode="json")
        if self.snr_carrier_policy is None:
            data.pop('snr_carrier_policy')
        if not self.fragmented_carrier_candidates_enabled:
            data.pop('fragmented_carrier_candidates_enabled')
        if not self.polar_window_candidates_enabled:
            # Default-off addition must not invalidate frozen legacy products.
            data.pop('polar_window_candidates_enabled')
        if not self.complete_source_families_enabled:
            data.pop('complete_source_families_enabled')
            data.pop('complete_source_family_reference_mode')
        return hashlib.sha256(json.dumps(data, sort_keys=True,
            separators=(",", ":"), allow_nan=False).encode()).hexdigest()
