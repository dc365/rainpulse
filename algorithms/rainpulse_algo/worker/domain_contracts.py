from __future__ import annotations

from datetime import datetime
from typing import Literal
from urllib.parse import urlparse
from uuid import UUID

from pydantic import Field, field_validator, model_validator

from .contracts import ContractModel


class DomainRequest(ContractModel):
    schema_version: Literal["1.0"]
    event_id: UUID
    occurred_at: datetime
    run_id: UUID
    job_id: UUID
    trace_id: UUID


class ObjectTaskPayload(ContractModel):
    input_uri: str
    output_prefix: str

    @field_validator("input_uri", "output_prefix")
    @classmethod
    def validate_uri(cls, value: str) -> str:
        if not urlparse(value).scheme:
            raise ValueError("URI scheme is required")
        return value


class RadarDecodePayload(ObjectTaskPayload):
    scan_id: UUID
    asset_id: UUID
    radar_id: str = Field(min_length=1)
    source_format: str = Field(min_length=1)
    radar_config_version: str = Field(min_length=1)
    decoder_version: str = Field(min_length=1)


class RadarDecodeRequested(DomainRequest):
    event_type: Literal["radar.decode.requested.v1"]
    payload: RadarDecodePayload


class RadarQCPayload(ObjectTaskPayload):
    scan_id: UUID
    radar_id: str = Field(min_length=1)
    radar_config_version: str = Field(min_length=1)
    qc_profile: str = Field(min_length=1)
    qc_pipeline_version: str = Field(min_length=1)
    flag_definition_version: str = Field(min_length=1)


class RadarQCRequested(DomainRequest):
    event_type: Literal["radar.qc.requested.v1"]
    payload: RadarQCPayload


class RadarGridPayload(ObjectTaskPayload):
    scan_id: UUID
    radar_id: str = Field(min_length=1)
    grid_id: str = Field(min_length=1)
    grid_config_version: str = Field(min_length=1)
    hybrid_scan_version: str = Field(min_length=1)


class RadarGridRequested(DomainRequest):
    event_type: Literal["radar.grid.requested.v1"]
    payload: RadarGridPayload


class AnalysisMosaicInput(ContractModel):
    radar_id: str = Field(min_length=1)
    scan_id: UUID
    grid_uri: str
    time_offset_seconds: int

    @field_validator("grid_uri")
    @classmethod
    def validate_grid_uri(cls, value: str) -> str:
        if not urlparse(value).scheme:
            raise ValueError("URI scheme is required")
        return value


class AnalysisMosaicPayload(ContractModel):
    analysis_id: UUID
    analysis_time: datetime
    grid_id: str = Field(min_length=1)
    inputs: list[AnalysisMosaicInput] = Field(min_length=1)
    output_prefix: str
    mosaic_config_version: str = Field(min_length=1)
    qpe_config_version: str = Field(min_length=1)

    @field_validator("output_prefix")
    @classmethod
    def validate_output_prefix(cls, value: str) -> str:
        if not urlparse(value).scheme:
            raise ValueError("URI scheme is required")
        return value


class AnalysisMosaicRequested(DomainRequest):
    event_type: Literal["analysis.mosaic.requested.v1"]
    payload: AnalysisMosaicPayload


class NowcastInputPayload(ContractModel):
    analysis_ids: list[UUID] = Field(min_length=3, max_length=6)
    input_uris: list[str] = Field(min_length=3, max_length=6)
    output_prefix: str
    issue_time: datetime
    grid_id: str = Field(min_length=1)
    preprocess_version: str = Field(min_length=1)
    gate_config_version: str = Field(min_length=1)

    @field_validator("input_uris")
    @classmethod
    def validate_input_uris(cls, values: list[str]) -> list[str]:
        if any(not urlparse(value).scheme for value in values):
            raise ValueError("every input URI requires a scheme")
        if len(values) != len(set(values)):
            raise ValueError("input URIs must be unique")
        return values

    @field_validator("output_prefix")
    @classmethod
    def validate_output_prefix(cls, value: str) -> str:
        if not urlparse(value).scheme:
            raise ValueError("URI scheme is required")
        return value

    @model_validator(mode="after")
    def validate_frame_identity(self) -> NowcastInputPayload:
        if len(self.analysis_ids) != len(self.input_uris):
            raise ValueError("analysis_ids and input_uris must have equal lengths")
        if len(self.analysis_ids) != len(set(self.analysis_ids)):
            raise ValueError("analysis IDs must be unique")
        return self


class NowcastInputRequested(DomainRequest):
    event_type: Literal["nowcast.input.requested.v1"]
    payload: NowcastInputPayload
