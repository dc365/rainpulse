"""Machine-readable contracts for checksum-bound offline network comparison."""

from __future__ import annotations

from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, model_validator

from .network_gate import NetworkLimits


class FrozenFile(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)
    path: str = Field(min_length=1, max_length=4096)
    sha256: str = Field(pattern=r"^[0-9a-f]{64}$")


class FrozenArtifact(FrozenFile):
    uri: str = Field(min_length=1, max_length=4096)


class NetworkCaseManifest(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)
    schema_version: Literal["rainpulse.qc-network-case.v1"]
    case_id: str = Field(min_length=1, max_length=256)
    partition: Literal["development", "validation"]
    process_id: str = Field(min_length=1, max_length=256)
    data_kind: Literal["real", "synthetic"]
    task: FrozenFile
    baseline_profile: FrozenFile
    candidate_profile: FrozenFile
    flags: FrozenFile
    artifacts: list[FrozenArtifact] = Field(min_length=1, max_length=7)
    labels: dict[str, FrozenFile] = Field(default_factory=dict)
    resource_environment: dict[str, FrozenFile] = Field(default_factory=dict)

    @model_validator(mode="after")
    def unique_artifacts(self):
        if set(self.resource_environment) - {
            "RAINPULSE_RADAR_CONFIG_DIR",
            "RAINPULSE_ANCILLARY_CONFIG",
            "RAINPULSE_ANCILLARY_ROOT",
        }:
            raise ValueError("only frozen geometry resource keys are permitted")
        if len({x.uri for x in self.artifacts}) != len(self.artifacts):
            raise ValueError("duplicate frozen artifact URI")
        if not self.process_id.strip() or not self.case_id.strip():
            raise ValueError("empty case/process identity")
        return self


class NetworkManifest(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)
    schema_version: Literal["rainpulse.qc-network.v1"]
    cases: list[FrozenFile] = Field(min_length=1, max_length=32)
    expected_radars: list[str] = Field(min_length=1, max_length=128)
    limits: NetworkLimits = Field(default_factory=NetworkLimits)

    @model_validator(mode="after")
    def distinct_radars(self):
        if len(set(self.expected_radars)) != len(self.expected_radars) or any(
            not x.strip() for x in self.expected_radars
        ):
            raise ValueError("declare distinct nonempty required radar identities")
        return self
