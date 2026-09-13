"""Explicit paper parameters and their evidence level, separate from frozen V3.

A published formula is not the same as a fully specified reference executable.
Unspecified thresholds and graph-digitized knots are named, never hidden defaults.
"""

from __future__ import annotations

from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, model_validator


class PaperConfig(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True, allow_inf_nan=False)


class Curve(PaperConfig):
    x: tuple[float, ...]
    y: tuple[float, ...]

    @model_validator(mode="after")
    def ordered(self):
        if len(self.x) != len(self.y) or len(self.x) < 2:
            raise ValueError("membership knots must have equal lengths >=2")
        if any(a >= b for a, b in zip(self.x[:-1], self.x[1:], strict=True)):
            raise ValueError("membership abscissae must strictly increase")
        if any(not 0 <= item <= 1 for item in self.y):
            raise ValueError("membership ordinates must be in [0,1]")
        return self


class AFLConfig(PaperConfig):
    method: Literal["afl-parameterized-2020-v1"] = "afl-parameterized-2020-v1"
    source_doi: Literal["10.11676/qxxb2020.010"] = "10.11676/qxxb2020.010"
    # Fig.4 is available; its precise numeric knot table and Z_thresh are not.
    fidelity: Literal["published_formulas_digitized_knots_explicit_assumptions"] = (
        "published_formulas_digitized_knots_explicit_assumptions"
    )
    spin_jump_db: float = Field(default=2.0, gt=0, le=30)
    decision_threshold: float = Field(default=0.6, gt=0, lt=1)
    rough_texture_db2: float = Field(default=3.0, gt=0)
    tail_fraction: float = Field(default=0.1, gt=0, le=0.5)
    minimum_tail_support: float = Field(default=0.8, gt=0, le=1)
    tail_policy: Literal["last_geometric_bins_valid_mean"] = "last_geometric_bins_valid_mean"
    stencil_policy: Literal["complete_11_pairs_no_gap_filling"] = "complete_11_pairs_no_gap_filling"
    smooth_weights: tuple[float, float, float, float] = (2, 2, 1, 0)
    rough_weights: tuple[float, float, float, float] = (1, 2, 1, 1)
    smooth_rref: Curve = Curve(x=(90, 95), y=(0, 1))
    rough_rref: Curve = Curve(x=(80, 95), y=(0, 1))
    smooth_db: Curve = Curve(x=(-90, -20, 50), y=(0, 1, 0))
    rough_db: Curve = Curve(x=(-150, -20, 110), y=(0, 1, 0))
    smooth_texture: Curve = Curve(x=(0, 3), y=(1, 0))
    rough_texture: Curve = Curve(x=(0, 5, 30), y=(0, 1, 0))
    smooth_spin: Curve = Curve(x=(0, 4, 11), y=(1, 0.5, 0))
    rough_spin: Curve = Curve(x=(0, 10, 11), y=(0, 1, 0))
    local_window_m: float = Field(default=50000.0, ge=10000, le=200000)

    @model_validator(mode="after")
    def weights_valid(self):
        for weights in (self.smooth_weights, self.rough_weights):
            if any(w < 0 for w in weights) or sum(weights) <= 0:
                raise ValueError("AFL weights must be nonnegative and not all zero")
        return self


class FusionConfig(PaperConfig):
    # Scores sharing DBZH are ONE structure family, regardless of agreement.
    quarantine_quality: float = Field(default=0.25, ge=0, lt=1)
    minimum_echo_dbz: float = 5.0
    minimum_range_m: float = Field(default=15000.0, ge=0)
    minimum_segment_m: float = Field(default=12000.0, gt=0)
    minimum_snr_db: float = Field(default=8.0, ge=0)
    low_rhohv: float = Field(default=0.8, ge=0, le=1)
    protected_rhohv: float = Field(default=0.95, ge=0, le=1)
    phase_pair_jump_deg: float = Field(default=25.0, gt=0, lt=180)
    phase_window_gates: int = Field(default=11, ge=3, le=51)
    phase_minimum_pair_fraction: float = Field(default=0.8, gt=0, le=1)
    phase_bad_pair_fraction: float = Field(default=0.4, gt=0, le=1)
    zdr_outlier_db: tuple[float, float] = (-3.0, 6.0)
    temporal_minimum_samples: int = Field(default=2, ge=2, le=3)
    temporal_minimum_hits: int = Field(default=2, ge=2, le=3)

    @model_validator(mode="after")
    def ranges(self):
        if self.low_rhohv >= self.protected_rhohv:
            raise ValueError("low-rho and protected-rho thresholds must be ordered")
        if self.phase_window_gates % 2 != 1 or self.zdr_outlier_db[0] >= self.zdr_outlier_db[1]:
            raise ValueError("phase window must be odd and ZDR bounds increasing")
        return self


class LiteratureConfig(PaperConfig):
    method: Literal["v3-afl-rdd-fusion-v1"] = "v3-afl-rdd-fusion-v1"
    afl: AFLConfig = Field(default_factory=AFLConfig)
    fusion: FusionConfig = Field(default_factory=FusionConfig)
    rdd_policy: Literal["external_reference_only_record_absence"] = (
        "external_reference_only_record_absence"
    )
