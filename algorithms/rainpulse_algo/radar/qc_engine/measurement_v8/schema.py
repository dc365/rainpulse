"""All defaults are explicit research assumptions, not validated weather thresholds."""

from typing import Literal

import yaml
from pydantic import BaseModel, ConfigDict, Field, model_validator


class Frozen(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True, allow_inf_nan=False)


class Ref(Frozen):
    path: str
    sha256: str = Field(pattern=r"^[a-f0-9]{64}$")


class NativeConfig(Frozen):
    angular_mapping: bool = False
    angular_step_deg: float = Field(default=1.0, ge=0.1, le=3.0)
    maximum_angular_offset_fraction: float = Field(default=0.25, gt=0, le=0.25)
    enabled: bool = True
    required: bool = False
    emitter1_minimum_contrast_db: float | None = Field(default=None, ge=0.5, le=127)
    minimum_dbz: float = Field(default=-10, ge=-31, le=80)
    minimum_length_m: float = Field(default=4000, gt=0, le=100000)
    width_rays: int = Field(default=3, ge=1, le=8)
    tile_rays: int = Field(default=32, ge=8, le=128)
    minimum_tile_gates: int = Field(default=64, ge=16, le=2000)
    maximum_calls: int = Field(default=160, ge=2, le=2000)
    timeout_seconds: float = Field(default=20, gt=0, le=300)


class FeatureConfig(Frozen):
    radial_windows_m: tuple[float, ...] = (1750, 7000)
    shoulder_offsets_deg: tuple[float, ...] = (1, 2, 4, 8)
    minimum_support_fraction: float = Field(default=0.8, gt=0.5, le=1)
    phase_period_deg: float = Field(default=360, gt=0, le=360)

    @model_validator(mode="after")
    def limits(self):
        if not 1 <= len(self.radial_windows_m) <= 4:
            raise ValueError("one to four radial scales required")
        if any(not 0 < x <= 20000 for x in self.radial_windows_m):
            raise ValueError("radial scales must be in (0,20000] metres")
        if not 1 <= len(self.shoulder_offsets_deg) <= 8:
            raise ValueError("one to eight shoulder scales required")
        if any(not 0 < x <= 45 for x in self.shoulder_offsets_deg):
            raise ValueError("shoulder scales must be in (0,45] degrees")
        for seq in (self.radial_windows_m, self.shoulder_offsets_deg):
            if list(seq) != sorted(set(seq)):
                raise ValueError("scales must be unique and increasing")
        return self


class TrainConfig(Frozen):
    n_estimators: int = Field(default=64, ge=4, le=256)
    max_depth: int = Field(default=8, ge=2, le=16)
    min_samples_leaf: int = Field(default=5, ge=1, le=10000)
    random_state: int = 20260915
    min_processes_per_split: int = Field(default=2, ge=2)
    min_class_samples: int = Field(default=10, ge=3)
    maximum_samples: int = Field(default=500000, gt=0, le=2000000)


class StateConfig(Frozen):
    enabled: bool = True
    transition_cost: float = Field(default=1.5, ge=0, le=20)
    minimum_supported_m: float = Field(default=1000, ge=0, le=20000)
    # Missing gates are always barriers. No recursive action propagation.


class Policy(Frozen):
    mode: Literal["audit", "experimental"] = "audit"
    confirm_probability: float = Field(default=0.99, gt=0.5, lt=1)
    quarantine_probability: float = Field(default=0.85, gt=0.5, lt=1)
    mixed_probability: float = Field(default=0.7, gt=0.5, lt=1)
    maximum_missing_feature_fraction: float = Field(default=0.5, ge=0, lt=1)
    no_echo_below_dbz: float = -10
    strong_weather_support: float = Field(default=0.7, ge=0, le=1)
    quarantine_quality: float = Field(default=0.2, ge=0, lt=0.5)
    restore_baseline_rejection: Literal[False] = False

    @model_validator(mode="after")
    def ordered(self):
        if self.confirm_probability <= self.quarantine_probability:
            raise ValueError("confirmation must be stricter than quarantine")
        return self


class Config(Frozen):
    schema_version: Literal["rainpulse.measurement-experiment-config.v1"] = (
        "rainpulse.measurement-experiment-config.v1"
    )
    native: NativeConfig = NativeConfig()
    features: FeatureConfig = FeatureConfig()
    training: TrainConfig = TrainConfig()
    state: StateConfig = StateConfig()
    policy: Policy = Policy()


class Case(Frozen):
    schema_version: Literal["rainpulse.measurement-case.v1"] = "rainpulse.measurement-case.v1"
    case_id: str = Field(min_length=1)
    process_id: str = Field(min_length=1)
    partition: Literal["train", "calibrate", "validate", "inspect"]
    data_kind: Literal["real", "synthetic"]
    normalized: Ref
    qc: Ref
    profile: Ref
    flags: Ref
    expected_qc_asset_id: str
    expected_scan_id: str
    expected_radar_id: str
    expected_context_fingerprint: str = Field(pattern=r"^[a-f0-9]{64}$")
    sweeps: tuple[str, ...]
    # Optional supplementary stage-B matrices MUST be content-bound and source-declared.
    context: Ref | None = None


def load_config(path):
    with open(path, encoding="utf-8") as f:
        return Config.model_validate(yaml.safe_load(f))
