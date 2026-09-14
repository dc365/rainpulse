"""Explicit opt-in topology repair; old profiles and their hashes remain frozen."""

from typing import Literal

from pydantic import BaseModel, ConfigDict


class ResidualRepairConfig(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)
    method: Literal["local-topology-v6.1"] = "local-topology-v6.1"
    local_narrow_width: bool = True
    footprint_peripheral: bool = True
