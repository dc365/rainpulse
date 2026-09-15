"""OC1 research parameters; not MIT/MRMS standards or calibrated probabilities."""
from __future__ import annotations

from dataclasses import asdict, dataclass, fields
import hashlib
import json
import math
from typing import Any


@dataclass(frozen=True)
class Config:
    method: str = "raw-object-crossfit-competition-1"
    range_min_m: float = 50000.0
    range_max_m: float = 450000.0
    minimum_echo_dbz: float = -5.0
    maximum_identity_gap_m: float = 6000.0
    maximum_identity_gap_fraction: float = 0.4
    minimum_domain_span_m: float = 200000.0
    block_m: float = 50000.0
    guard_blocks: int = 1
    minimum_train_blocks: int = 5
    minimum_train_span_m: float = 200000.0
    minimum_block_support_m: float = 7500.0
    median_stationarity_p90_db: float = 2.0
    target_snr_allowance_db: float = 1.0
    reliable_snr_db: float = 8.0
    low_rho: float = 0.8
    minimum_pol_samples: int = 20
    minimum_pol_blocks: int = 3
    minimum_pair_samples: int = 12
    minimum_low_rho_fraction: float = 0.6
    minimum_increment_variance: float = 0.1
    coherent_snr_db: float = 20.0
    coherent_minimum_samples: int = 100
    coherent_phase_p90_deg: float = 1.0
    coherent_zdr_p90_db: float = 0.25
    coherent_rho: float = 0.98
    coherent_fraction: float = 0.95
    maximum_coherent_width_deg: float = 2.5
    maximum_noisy_width_deg: float = 35.0
    maximum_sector_width_deg: float = 70.0
    healthy_rho: float = 0.97
    healthy_increment_deg: float = 5.0
    healthy_run_m: float = 2500.0
    local_snr_excess_db: float = 6.0
    weather_lateral_rays: int = 2
    weather_lateral_max_m: float = 15000.0
    weather_lateral_difference_db: float = 6.0
    azimuth_gap_factor: float = 1.8
    maximum_elevation_difference_deg: float = 0.3
    bundle_maximum_angle_deg: float = 2.0
    bundle_minimum_overlap_m: float = 2000.0
    maximum_domains: int = 25000
    maximum_folds: int = 25000
    maximum_gates: int = 12000000

    def __post_init__(self):
        if self.method != "raw-object-crossfit-competition-1":
            raise ValueError("unsupported model version")
        ints = {f.name for f in fields(self) if f.type in (int, "int")}
        for f in fields(self):
            v = getattr(self, f.name)
            if f.name == "method":
                continue
            if isinstance(v, bool) or not isinstance(v, (float, int)) or not math.isfinite(v):
                raise ValueError(f"finite numeric parameter required: {f.name}")
            if f.name in ints and (not isinstance(v, int) or v < 1):
                raise ValueError(f"positive integer required: {f.name}")
        if not 0 <= self.range_min_m < self.range_max_m or self.block_m <= 0:
            raise ValueError("invalid range/block")
        if self.maximum_identity_gap_m < 0 or self.minimum_domain_span_m <= 0:
            raise ValueError("invalid support limits")
        if not 0 <= self.maximum_identity_gap_fraction < 0.5:
            raise ValueError("identity gap fraction must be in [0,0.5)")
        if self.maximum_identity_gap_m >= self.block_m:
            raise ValueError("identity gap cannot exceed the reference block")
        if self.minimum_train_blocks < 3 or self.minimum_pol_blocks < 2:
            raise ValueError("multiple reference blocks required")
        if self.minimum_block_support_m <= 0 or self.minimum_block_support_m > self.block_m:
            raise ValueError("invalid measured block support")
        for name in ("low_rho", "minimum_low_rho_fraction", "minimum_increment_variance",
                     "coherent_rho", "coherent_fraction", "healthy_rho"):
            if not 0 < getattr(self, name) <= 1:
                raise ValueError(f"invalid fraction: {name}")
        for name in ("maximum_coherent_width_deg", "maximum_noisy_width_deg",
                     "bundle_maximum_angle_deg", "maximum_sector_width_deg"):
            if not 0 < getattr(self, name) < 90:
                raise ValueError(f"invalid angle: {name}")
        if not (0 <= self.reliable_snr_db <= self.coherent_snr_db <= 100):
            raise ValueError("invalid reliability/coherence SNR settings")
        if not (self.low_rho < self.healthy_rho <= self.coherent_rho):
            raise ValueError("correlation families must be ordered")
        if not (self.maximum_coherent_width_deg <= self.maximum_noisy_width_deg <= self.maximum_sector_width_deg):
            raise ValueError("structural widths must increase")
        if self.target_snr_allowance_db < 0 or self.local_snr_excess_db <= 0:
            raise ValueError("invalid target/competitor tolerances")
        if self.minimum_pol_blocks > self.minimum_train_blocks:
            raise ValueError("reference polarization block requirement exceeds reference blocks")
        if self.azimuth_gap_factor < 1 or self.guard_blocks < 1:
            raise ValueError("invalid geometry/guard")
        if min(self.median_stationarity_p90_db, self.coherent_phase_p90_deg,
               self.coherent_zdr_p90_db) <= 0:
            raise ValueError("positive tolerances required")


@dataclass(frozen=True)
class Policy:
    mode: str = "audit"
    allow_coherent_quarantine: bool = False
    require_bracketed_reference: bool = True
    quarantine_quality: float = 0.2
    maximum_new_eligible_loss_fraction: float = 0.1
    acknowledge_uncalibrated_model: bool = False

    def __post_init__(self):
        if self.mode not in {"audit", "experiment_quarantine"}:
            raise ValueError("unsupported action policy")
        for n in ("allow_coherent_quarantine", "require_bracketed_reference",
                  "acknowledge_uncalibrated_model"):
            if type(getattr(self, n)) is not bool:
                raise ValueError(f"boolean required: {n}")
        if not 0 <= self.quarantine_quality < 0.5:
            raise ValueError("quarantine quality must be below 0.5")
        if not 0 < self.maximum_new_eligible_loss_fraction <= 1:
            raise ValueError("invalid experiment loss budget")
        if self.mode != "audit" and not self.acknowledge_uncalibrated_model:
            raise ValueError("experimental action requires explicit acknowledgement")


def digest(value: Any) -> str:
    return hashlib.sha256(json.dumps(value, sort_keys=True, separators=(",", ":"),
                                    allow_nan=False).encode()).hexdigest()


def load_settings(path):
    with open(path, encoding="utf-8") as f:
        d = json.load(f)
    if set(d) != {"model", "policy"}:
        raise ValueError("settings require exactly model and policy")
    return Config(**d["model"]), Policy(**d["policy"])


def settings_dict(model: Config, policy: Policy) -> dict:
    return {"model": asdict(model), "policy": asdict(policy)}
