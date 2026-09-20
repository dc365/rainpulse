"""Versioned research settings, not empirically accepted operational thresholds."""
from typing import Literal
from pydantic import BaseModel, ConfigDict, Field, model_validator


class FragmentLineConfig(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True, allow_inf_nan=False)
    version: Literal["fragment-line-v1", "fragment-line-v2", "fragment-line-v3"] = "fragment-line-v1"
    levels_dbz: tuple[float, ...] = (10., 20., 35., 45.)
    maximum_width_rays: int = Field(default=3, ge=1, le=5)
    maximum_width_deg: float = Field(default=3., gt=0, le=5)
    minimum_range_m: float = Field(default=50000., ge=0)
    minimum_span_m: float = Field(default=40000., ge=20000.)
    minimum_support_m: float = Field(default=20000., ge=10000.)
    maximum_gap_m: float = Field(default=10000., ge=0, le=20000.)
    detection_bin_m: float = Field(default=1000., ge=250., le=2000.)
    maximum_lines: int = Field(default=2000, ge=1, le=10000)
    coherent_source_enabled: bool = True
    morphology_quarantine_enabled: bool = False
    minimum_edge_db: float = Field(default=6., ge=6.)
    isolated_quarantine_enabled: bool = False
    sparse_isolated_enabled: bool = False
    group_morphology_enabled: bool = False
    window_tracks_enabled: bool = False
    power_fan_enabled: bool = False
    residual_objects_enabled: bool = False
    residual_span_enabled: bool = False
    residual_span_minimum_m: float = Field(default=15000., ge=5000., le=60000.)
    residual_span_flank_rays: int = Field(default=3, ge=1, le=5)
    residual_span_flank_delta_db: float = Field(default=3., ge=1., le=12.)
    residual_span_flank_fraction: float = Field(default=.7, gt=0., le=1.)
    residual_span_minimum_dbz: float = Field(default=12., ge=-10., le=40.)
    residual_span_weather_snr_db: float = Field(default=25., ge=10.)
    residual_span_weather_fraction: float = Field(default=.6, gt=0., le=1.)
    antenna_beam_width_deg: float | None = Field(default=None, gt=0., le=5.)
    isolated_link_gap_m: float = Field(default=30000., ge=0, le=30000.)

    @model_validator(mode="after")
    def check(self):
        import math
        if self.power_fan_enabled and not self.group_morphology_enabled:
            raise ValueError("power fans require group morphology")
        if self.window_tracks_enabled and not self.group_morphology_enabled:
            raise ValueError("window tracks require group morphology")
        if self.residual_span_enabled and not self.residual_objects_enabled:
            raise ValueError("whole-ray isolation requires residual objects")
        if self.group_morphology_enabled and not self.sparse_isolated_enabled:
            raise ValueError("group morphology requires sparse isolation")
        if self.sparse_isolated_enabled and not self.isolated_quarantine_enabled:
            raise ValueError("sparse association requires isolated policy")
        if self.morphology_quarantine_enabled and self.version not in ('fragment-line-v2', 'fragment-line-v3'):
            raise ValueError('direct morphology disposition requires fragment-line-v2')
        if self.isolated_quarantine_enabled and self.version != 'fragment-line-v3':
            raise ValueError('isolated morphology disposition requires fragment-line-v3')
        if not self.levels_dbz or len(self.levels_dbz) > 8 or any(not math.isfinite(x) or not -10 <= x <= 70 for x in self.levels_dbz):
            raise ValueError("one to eight finite DBZH contours required")
        if tuple(sorted(set(self.levels_dbz))) != self.levels_dbz:
            raise ValueError("DBZH contours must be increasing and unique")
        if self.maximum_gap_m >= self.minimum_span_m or self.minimum_support_m > self.minimum_span_m:
            raise ValueError("fragment support/gap must fit within line span")
        return self


class RadialRevisionConfig(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True, allow_inf_nan=False)
    version: Literal["radial-source-v2-20260918"] = "radial-source-v2-20260918"
    step: Literal[1, 2, 3] = 1
    mode: Literal["audit", "experiment_quarantine"] = "audit"
    allow_segmented_quarantine: bool = False
    weak_policy: Literal["diagnostic_only"] = "diagnostic_only"
    fragment_line: FragmentLineConfig | None = None
    scales_m: tuple[int, ...] = (10000, 20000, 50000)
    support_fraction: float = Field(default=.4, gt=0, le=1)
    outside_support_fraction: float = Field(default=.2, ge=0, lt=1)
    maximum_width_deg: float = Field(default=7., gt=0, le=15)
    maximum_width_m: float = Field(default=60000., gt=0, le=100000)
    minimum_range_m: float = Field(default=50000., ge=0)
    block_m: float = Field(default=50000., gt=0)
    guard_blocks: int = Field(default=1, ge=1, le=3)
    maximum_states: int = Field(default=3, ge=1, le=3)
    state_separation_db: float = Field(default=8., ge=6, le=15)
    minimum_reference_blocks: int = Field(default=3, ge=3)
    minimum_reference_span_m: float = Field(default=100000., ge=100000)
    minimum_reference_samples: int = Field(default=100, ge=100)
    minimum_block_support_m: float = Field(default=7500., ge=5000)
    minimum_group_samples: int = Field(default=30, ge=20)
    maximum_power_residual_db: float = Field(default=2.5, gt=0, le=2.5)
    maximum_snr_dispersion_db: float = Field(default=2., gt=0, le=2)
    minimum_coherent_snr_db: float = Field(default=20., ge=20)
    minimum_diagnostic_snr_db: float = Field(default=3., ge=0)
    maximum_phase_p90_deg: float = Field(default=10., gt=0, le=10)
    maximum_zdr_p90_db: float = Field(default=1., gt=0, le=1)
    minimum_polar_samples: int = Field(default=100, ge=100)
    require_measured_range_term: Literal[True] = True
    bundle_maximum_rays: int = Field(default=11, ge=3, le=21)
    bundle_flank_search_rays: int = Field(default=3, ge=1, le=5)
    bundle_measured_fraction: float = Field(default=.5, gt=0, le=1)
    bundle_internal_fraction: float = Field(default=.6, gt=.5, le=1)
    bundle_contrast_db: float = Field(default=6., ge=6)
    minimum_fragment_m: float = Field(default=500., gt=0)
    maximum_link_gap_m: float = Field(default=3000., ge=0, le=5000)
    maximum_identity_span_m: float = Field(default=50000., gt=0, le=100000)
    maximum_link_delta_db: float = Field(default=3., gt=0, le=3)
    maximum_gates: int = Field(default=4000000, ge=1, le=10000000)
    maximum_folds: int = Field(default=20000, ge=1, le=50000)
    maximum_objects: int = Field(default=20000, ge=1, le=100000)

    @model_validator(mode="after")
    def check(self):
        if (not self.scales_m or len(self.scales_m) > 8 or
                tuple(sorted(set(self.scales_m))) != self.scales_m or
                any(type(x) is not int or not 1000 <= x <= 100000 for x in self.scales_m)):
            raise ValueError("one to eight increasing integer scales (1000..100000 m) required")
        if self.outside_support_fraction >= self.support_fraction:
            raise ValueError("outside support must be below central support")
        if self.minimum_fragment_m > self.scales_m[0]:
            raise ValueError("raw fragment must not exceed the shortest scale")
        if self.maximum_link_gap_m >= self.maximum_identity_span_m:
            raise ValueError("identity may not be wholly spanned by a link")
        if self.step == 1 and self.allow_segmented_quarantine:
            raise ValueError("segmented quarantine requires step two or three")
        if self.minimum_diagnostic_snr_db >= self.minimum_coherent_snr_db:
            raise ValueError("diagnostic SNR boundary must be below coherent SNR")
        return self
