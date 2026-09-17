"""Opt-in policies, not new global reflectivity/rho/length thresholds."""

from typing import Literal

from pydantic import BaseModel, ConfigDict, Field

from .broad_source import BroadSourceConfig


class GeneralizationConfig(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True, allow_inf_nan=False)
    method: Literal["measurement-admission-broad-source-v1"] = (
        "measurement-admission-broad-source-v1"
    )
    split_admission_health: bool = True
    retain_broad_measurements: bool = True
    resolve_coherent_self_protection: bool = True
    require_bracketed_reference: bool = True
    bounded_edge_reference: bool = False
    edge_maximum_extension_m: float = Field(default=100000.0, gt=0, le=100000)
    edge_minimum_anchor_span_m: float = Field(default=25000.0, gt=0, le=100000)
    broad_source: BroadSourceConfig | None = None
    prevent_budget_reversion: bool = True
    quarantine_quality: float = Field(default=0.2, ge=0, lt=0.5)
    maximum_new_eligible_loss_fraction: float = Field(default=0.1, gt=0, le=1)
    budget_overflow: Literal["retain_isolation_require_review"] = "retain_isolation_require_review"
