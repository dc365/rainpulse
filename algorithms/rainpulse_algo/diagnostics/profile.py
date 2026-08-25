from __future__ import annotations

from pathlib import Path
from typing import Literal

import yaml
from pydantic import BaseModel, ConfigDict, Field, model_validator


class DiagnosticConfigError(ValueError):
    pass


class StrictModel(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)


class GridRender(StrictModel):
    pixel_scale: int = Field(ge=1, le=4)
    north_up: Literal[True]
    missing_alpha: Literal[0]


class PolarRender(StrictModel):
    image_size: int = Field(ge=256, le=1200)
    sweep_selection: Literal["lowest_dbzh_sweep"]
    missing_alpha: Literal[0]


LayerName = Literal[
    "grid_dbzh_qc",
    "grid_rate_qpe",
    "grid_quality_index",
    "grid_source_radar",
    "grid_beam_height",
    "grid_qc_flags",
    "grid_state_mask",
    "polar_dbzh_raw",
    "polar_dbzh_qc",
    "polar_quality_index",
    "polar_qc_flags",
]


class DiagnosticProfile(StrictModel):
    schema_version: Literal["1.0"]
    profile_version: str = Field(min_length=3)
    renderer_version: str = Field(min_length=3)
    bundle_contract_version: Literal["1.0"]
    radar_analysis_contract_version: Literal["1.2"]
    qc_radar_volume_contract_version: Literal["1.0"]
    flag_definition_version: str = Field(min_length=3)
    palette_version: Literal["rainpulse-meteorological-v1"]
    grid_render: GridRender
    polar_render: PolarRender
    layers: list[LayerName] = Field(min_length=11)

    @model_validator(mode="after")
    def validate_layers(self) -> DiagnosticProfile:
        required = set(LayerName.__args__)  # type: ignore[attr-defined]
        if set(self.layers) != required or len(self.layers) != len(required):
            raise ValueError("diagnostic profile must contain every frozen layer exactly once")
        return self


def load_diagnostic_profile(path: Path | str) -> DiagnosticProfile:
    try:
        raw = yaml.safe_load(Path(path).read_text())
        return DiagnosticProfile.model_validate(raw)
    except (OSError, ValueError, yaml.YAMLError) as error:
        raise DiagnosticConfigError(f"invalid diagnostic profile: {error}") from error
