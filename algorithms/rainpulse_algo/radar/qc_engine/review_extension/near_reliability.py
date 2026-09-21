"""Opt-in near-site measurement semantics; not a stronger image filter."""
from typing import Literal
from pydantic import BaseModel, ConfigDict, Field


class NearReliabilityConfig(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True, allow_inf_nan=False)
    version: Literal["near-reliability-20260921-v1"] = "near-reliability-20260921-v1"
    maximum_abs_zdr_db: float = Field(default=7.5, gt=0, le=7.5)
    minimum_range_m: float = Field(default=2000., ge=0, le=10000)
    no_rain_below_dbz: float = Field(default=-10., ge=-32, le=0)
