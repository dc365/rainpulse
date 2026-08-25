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


class AnalysisMosaicInputV1(ContractModel):
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


class AnalysisMosaicPayloadV1(ContractModel):
    analysis_id: UUID
    analysis_time: datetime
    grid_id: str = Field(min_length=1)
    inputs: list[AnalysisMosaicInputV1] = Field(min_length=1)
    output_prefix: str
    mosaic_config_version: str = Field(min_length=1)
    qpe_config_version: str = Field(min_length=1)

    @field_validator("output_prefix")
    @classmethod
    def validate_output_prefix(cls, value: str) -> str:
        if not urlparse(value).scheme:
            raise ValueError("URI scheme is required")
        return value


class AnalysisMosaicRequestedV1(DomainRequest):
    event_type: Literal["analysis.mosaic.requested.v1"]
    payload: AnalysisMosaicPayloadV1


class AnalysisMosaicInputV2(AnalysisMosaicInputV1):
    hybrid_scan_version: str = Field(min_length=1)


class AnalysisMosaicPayloadV2(ContractModel):
    analysis_id: UUID
    analysis_time: datetime
    grid_id: str = Field(min_length=1)
    grid_config_version: str = Field(min_length=1)
    inputs: list[AnalysisMosaicInputV2] = Field(min_length=1)
    output_prefix: str
    mosaic_config_version: str = Field(min_length=1)
    mosaic_algorithm_version: str = Field(min_length=1)
    flag_definition_version: str = Field(min_length=1)

    @field_validator("output_prefix")
    @classmethod
    def validate_output_prefix(cls, value: str) -> str:
        if not urlparse(value).scheme:
            raise ValueError("URI scheme is required")
        return value

    @model_validator(mode="after")
    def validate_input_identity(self) -> AnalysisMosaicPayloadV2:
        radar_ids = [item.radar_id for item in self.inputs]
        scan_ids = [item.scan_id for item in self.inputs]
        if len(radar_ids) != len(set(radar_ids)):
            raise ValueError("mosaic radar IDs must be unique")
        if len(scan_ids) != len(set(scan_ids)):
            raise ValueError("mosaic scan IDs must be unique")
        return self


class AnalysisMosaicRequestedV2(DomainRequest):
    event_type: Literal["analysis.mosaic.requested.v2"]
    payload: AnalysisMosaicPayloadV2


class AnalysisQPEPayloadV1(ObjectTaskPayload):
    analysis_id: UUID
    analysis_time: datetime
    grid_id: str = Field(min_length=1)
    grid_config_version: str = Field(min_length=1)
    mosaic_config_version: str = Field(min_length=1)
    mosaic_algorithm_version: str = Field(min_length=1)
    qpe_config_version: str = Field(min_length=1)
    qpe_algorithm_version: str = Field(min_length=1)
    flag_definition_version: str = Field(min_length=1)


class AnalysisQPERequestedV1(DomainRequest):
    event_type: Literal["analysis.qpe.requested.v1"]
    payload: AnalysisQPEPayloadV1


class AnalysisDiagnosticRadarInput(ContractModel):
    radar_id: str = Field(min_length=1)
    scan_id: UUID
    qc_uri: str

    @field_validator("qc_uri")
    @classmethod
    def validate_qc_uri(cls, value: str) -> str:
        if urlparse(value).scheme != "s3":
            raise ValueError("diagnostic QC URI must use s3")
        return value


class AnalysisDiagnosticsPayloadV1(ObjectTaskPayload):
    analysis_id: UUID
    analysis_time: datetime
    grid_id: str = Field(min_length=1)
    radar_inputs: list[AnalysisDiagnosticRadarInput] = Field(min_length=1)
    diagnostic_config_version: str = Field(min_length=1)
    renderer_version: str = Field(min_length=1)
    flag_definition_version: str = Field(min_length=1)

    @model_validator(mode="after")
    def validate_radar_identity(self) -> AnalysisDiagnosticsPayloadV1:
        radar_ids = [item.radar_id for item in self.radar_inputs]
        scan_ids = [item.scan_id for item in self.radar_inputs]
        if len(radar_ids) != len(set(radar_ids)):
            raise ValueError("diagnostic radar IDs must be unique")
        if len(scan_ids) != len(set(scan_ids)):
            raise ValueError("diagnostic scan IDs must be unique")
        return self


class AnalysisDiagnosticsRequestedV1(DomainRequest):
    event_type: Literal["analysis.diagnostics.requested.v1"]
    payload: AnalysisDiagnosticsPayloadV1


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


class PystepsLKPayload(ObjectTaskPayload):
    issue_time: datetime
    grid_id: str = Field(min_length=1)
    input_asset_ids: list[UUID] = Field(min_length=1)
    model_id: Literal["pysteps-lk"]
    model_version: str = Field(min_length=1)
    config_version: str = Field(min_length=1)
    forecast_contract_version: Literal["1.1"]
    baseline_models: tuple[Literal["persistence"], Literal["translation"]]

    @field_validator("input_asset_ids")
    @classmethod
    def validate_input_asset_ids(cls, values: list[UUID]) -> list[UUID]:
        if len(values) != len(set(values)):
            raise ValueError("input asset IDs must be unique")
        return values


class PystepsLKRequested(DomainRequest):
    event_type: Literal["forecast.pysteps_lk.requested.v1"]
    payload: PystepsLKPayload


class ProductIDs(ContractModel):
    rain_rate: UUID
    accumulation_60: UUID
    accumulation_120: UUID

    @model_validator(mode="after")
    def validate_unique_ids(self) -> ProductIDs:
        values = (self.rain_rate, self.accumulation_60, self.accumulation_120)
        if any(value.int == 0 for value in values) or len(set(values)) != len(values):
            raise ValueError("product IDs must be non-nil and unique")
        return self


class ProductBuildPayload(ObjectTaskPayload):
    input_sha256: str = Field(pattern=r"^[a-f0-9]{64}$")
    model_run_id: UUID
    issue_time: datetime
    grid_id: str = Field(min_length=1)
    model_id: Literal["pysteps-lk"]
    model_version: str = Field(min_length=1)
    model_config_version: str = Field(min_length=1)
    product_config_version: str = Field(min_length=1)
    product_bundle_contract_version: Literal["1.0"]
    product_ids: ProductIDs


class ProductBuildRequested(DomainRequest):
    event_type: Literal["product.build.requested.v1"]
    payload: ProductBuildPayload
