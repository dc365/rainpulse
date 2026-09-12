from __future__ import annotations

import hashlib
import json
from pathlib import Path
from typing import Literal

import numpy as np
import yaml
from pydantic import BaseModel, ConfigDict, Field, PrivateAttr, model_validator


class FrozenConfig(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True, allow_inf_nan=False)


class HealthConfig(FrozenConfig):
    reject_states: tuple[str, ...] = ("UNAVAILABLE",)
    degraded_quality_multiplier: float = Field(default=0.8, gt=0, le=1)


class EchoConfig(FrozenConfig):
    dbzh_valid_range_dbz: tuple[float, float] = (-32.0, 80.0)
    no_rain_below_dbz: float = -10.0
    low_snr_db: float = 3.0
    strong_echo_dbz: float = 35.0


class QualityConfig(FrozenConfig):
    aggregation: Literal["minimum"] = "minimum"
    components: tuple[str, ...] = ("QI_METEO", "QI_INTERFERENCE")
    low_quality_threshold: float = Field(default=0.5, gt=0, lt=1)
    quantitative_minimum: float = Field(default=0.5, gt=0, lt=1)
    suspect_quality: float = Field(default=0.6, gt=0, le=1)
    incomplete_capability_quality: float = Field(default=0.7, gt=0, le=1)
    low_snr_quality: float = Field(default=0.3, ge=0, lt=1)
    unavailable_component_policy: Literal["penalize_and_record"] = "penalize_and_record"


class ClutterConfig(FrozenConfig):
    asset_uri: str | None = None
    asset_version: str | None = None
    asset_sha256: str | None = Field(default=None, pattern=r"^[a-f0-9]{64}$")
    flag_probability: float = Field(default=0.9, ge=0, le=1)


class SeaConfig(FrozenConfig):
    coastline_asset_uri: str | None = None
    asset_version: str | None = None
    # A coastline never establishes a sea-clutter cause by itself.


class GeometryConfig(FrozenConfig):
    maximum_azimuth_gap_factor: float = Field(default=1.8, gt=1, le=3)
    duplicate_tolerance_deg: float = Field(default=0.01, gt=0, le=0.1)
    maximum_elevation_spread_deg: float = Field(default=0.3, gt=0, le=1)
    maximum_ray_count: int = Field(default=2400, gt=0)
    maximum_gate_count: int = Field(default=5000, gt=0)
    phase_period_deg: float = Field(default=360.0, gt=0)


class PyArtConfig(FrozenConfig):
    texture_window_m: float = Field(default=1750.0, gt=0)
    max_textrefl: float = Field(default=8.0, gt=0)
    max_textzdr: float = Field(default=2.85, gt=0)
    max_textphi: float = Field(default=20.0, gt=0)
    max_textrhv: float = Field(default=0.3, gt=0)
    min_rhv: float = Field(default=0.6, ge=0, le=1)
    object_threshold_dbz: float = 0.0
    small_object_gates: int = Field(default=10, ge=1)


class WradlibConfig(FrozenConfig):
    gabella_window: int = Field(default=5, ge=3, le=31)
    gabella_thrsnorain: float = 0.0
    gabella_tr1: float = Field(default=6.0, gt=0)
    gabella_n_p: int = Field(default=6, ge=1)
    gabella_tr2: float = Field(default=1.3, gt=0)
    low_meteo_score: float = Field(default=0.25, ge=0, le=1)
    minimum_pol_moments: int = Field(default=2, ge=2, le=3)
    fuzzy_weights: dict[str, float] = Field(
        default_factory=lambda: {
            "zdr": 0.4,
            "rho": 0.4,
            "phi": 0.1,
            "dop": 0.1,
            "map": 0.5,
            "rho2": 0.4,
            "dr": 0.4,
            "cpa": 0.4,
        }
    )
    fuzzy_trapezoids: dict[str, tuple[float, float, float, float]] = Field(
        default_factory=lambda: {
            "zdr": (0.7, 1.0, 9999.0, 9999.0),
            "rho": (0.1, 0.15, 9999.0, 9999.0),
            "phi": (15.0, 20.0, 10000.0, 10000.0),
            "dop": (-0.2, -0.1, 0.1, 0.2),
            "map": (1.0, 1.0, 9999.0, 9999.0),
            "rho2": (-9999.0, -9999.0, 0.95, 0.98),
            "dr": (-20.0, -12.0, 9999.0, 9999.0),
            "cpa": (0.6, 0.9, 9999.0, 9999.0),
        }
    )

    @model_validator(mode="after")
    def validate_parameters(self):
        if self.gabella_window % 2 != 1:
            raise ValueError("Gabella window must be odd")
        keys = {"zdr", "rho", "phi", "dop", "map", "rho2", "dr", "cpa"}
        if set(self.fuzzy_weights) != keys or set(self.fuzzy_trapezoids) != keys:
            raise ValueError("all frozen fuzzy input parameters must be explicit")
        if any(not np.isfinite(w) or w < 0 for w in self.fuzzy_weights.values()):
            raise ValueError("invalid fuzzy weight")
        if sum(self.fuzzy_weights.values()) <= 0:
            raise ValueError("all weights are zero")
        for points in self.fuzzy_trapezoids.values():
            if not all(np.isfinite(points)) or tuple(sorted(points)) != points:
                raise ValueError("invalid trapezoid endpoints")
        return self


class RFIConfig(FrozenConfig):
    # Experimental supplement; the unmodified library baselines are always retained.
    enabled: bool = False
    minimum_segment_m: float = Field(default=12000.0, gt=0)
    local_window_m: float = Field(default=8000.0, gt=0)
    azimuth_offsets_deg: tuple[float, ...] = (2.0, 5.0, 12.0, 25.0, 45.0)
    minimum_echo_dbz: float = 10.0
    minimum_contrast_db: float = Field(default=10.0, gt=0)
    maximum_axial_std_db: float = Field(default=4.0, gt=0)
    severe_rhohv: float = Field(default=0.8, gt=0, lt=1)
    minimum_measured_support: float = Field(default=0.85, gt=0, le=1)


class PhaseConfig(FrozenConfig):
    enabled: bool = True
    band: Literal["S", "C", "X"] = "S"
    window_gates: int = Field(default=7, ge=3, le=31)
    minimum_segment_m: float = Field(default=5000.0, gt=0)
    minimum_rhohv: float = Field(default=0.9, gt=0, le=1)
    niter: int = Field(default=2, ge=1, le=10)
    attenuation_enabled: bool = False
    maximum_correction_db: float = Field(default=10.0, gt=0, le=20)


class ContextConfig(FrozenConfig):
    max_temporal_scans: int = Field(default=3, ge=0, le=3)
    enabled: bool = True
    max_age_seconds: int = Field(default=900, gt=0)
    max_cross_offset_seconds: int = Field(default=300, gt=0)
    past_only: Literal[True] = True
    echo_threshold_dbz: float = 10.0
    strong_support: float = Field(default=0.7, ge=0, le=1)


class OpenSourceQCProfile(FrozenConfig):
    schema_version: Literal["1.1"] = "1.1"
    engine: Literal["open_source"] = "open_source"
    profile_version: str = "fujian-qc-opensource-v1"
    pipeline_version: Literal["qc-opensource-1.0.0"] = "qc-opensource-1.0.0"
    decision_version: Literal["type-specific-v1"] = "type-specific-v1"
    flag_definition_version: Literal["qc-flags-v2"] = "qc-flags-v2"
    operational_eligible: Literal[False] = False
    arm_pyart_version: Literal["2.2.5"] = "2.2.5"
    wradlib_version: Literal["2.9.5"] = "2.9.5"
    health_gate: HealthConfig = Field(default_factory=HealthConfig)
    echo: EchoConfig = Field(default_factory=EchoConfig)
    quality_index: QualityConfig = Field(default_factory=QualityConfig)
    static_ground_clutter: ClutterConfig = Field(default_factory=ClutterConfig)
    sea_ap: SeaConfig = Field(default_factory=SeaConfig)
    geometry: GeometryConfig = Field(default_factory=GeometryConfig)
    pyart: PyArtConfig = Field(default_factory=PyArtConfig)
    wradlib: WradlibConfig = Field(default_factory=WradlibConfig)
    rfi: RFIConfig = Field(default_factory=RFIConfig)
    phase: PhaseConfig = Field(default_factory=PhaseConfig)
    context: ContextConfig = Field(default_factory=ContextConfig)
    _flag_masks: dict[str, np.uint32] = PrivateAttr(default_factory=dict)

    @property
    def flag_masks(self) -> dict[str, np.uint32]:
        return dict(self._flag_masks)

    @property
    def parameters_hash(self) -> str:
        data = json.dumps(self.model_dump(mode="json"), sort_keys=True, separators=(",", ":"))
        return hashlib.sha256(data.encode()).hexdigest()

    @model_validator(mode="after")
    def validate_profile(self):
        if self.echo.dbzh_valid_range_dbz[0] >= self.echo.dbzh_valid_range_dbz[1]:
            raise ValueError("reflectivity bounds must increase")
        if not self.rfi.azimuth_offsets_deg or any(
            not np.isfinite(x) or not 0 < x < 180 for x in self.rfi.azimuth_offsets_deg
        ):
            raise ValueError("RFI offsets must be finite angles between 0 and 180 degrees")
        if self.phase.window_gates % 2 != 1:
            raise ValueError("phase window must be odd")
        asset = self.static_ground_clutter
        if sum(bool(v) for v in (asset.asset_uri, asset.asset_version, asset.asset_sha256)) not in (
            0,
            3,
        ):
            raise ValueError("clutter URI, version and SHA256 must be supplied together")
        if self.sea_ap.coastline_asset_uri:
            raise ValueError("coastline alone is not a classified sea-clutter asset")
        return self


def load_open_source_profile(path: str | Path, flag_path: str | Path) -> OpenSourceQCProfile:
    profile = OpenSourceQCProfile.model_validate(yaml.safe_load(Path(path).read_text()))
    flags = yaml.safe_load(Path(flag_path).read_text())
    if flags.get("definition_version") != profile.flag_definition_version:
        raise ValueError("open-source QC requires the v2 flag definition")
    frozen_names = (
        "GROUND_CLUTTER",
        "SEA_CLUTTER",
        "ANOMALOUS_PROPAGATION",
        "RADIAL_INTERFERENCE",
        "HARDWARE_ANOMALY",
        "BIOLOGICAL_ECHO",
        "BEAM_BLOCKED",
        "ATTENUATED",
        "WET_RADOME",
        "BRIGHT_BAND",
        "VELOCITY_ALIASED",
        "LOW_SNR",
        "MISSING",
        "CORRECTED",
        "LOW_QUALITY",
        "NON_METEOROLOGICAL",
    )
    entries = flags["flags"]
    if len(entries) != len(frozen_names) or flags.get("storage_dtype") != "uint32":
        raise ValueError("v2 flag definition must preserve the complete frozen uint32 layout")
    mapping = {}
    for item in entries:
        name = item["name"]
        if name not in frozen_names or name in mapping:
            raise ValueError("unknown or duplicate v2 QC flag")
        bit = frozen_names.index(name)
        if item.get("bit") != bit or item["mask"] != 1 << bit:
            raise ValueError("v2 flag bit/mask differs from the frozen layout")
        mapping[name] = np.uint32(item["mask"])
    required = {
        "MISSING",
        "HARDWARE_ANOMALY",
        "RADIAL_INTERFERENCE",
        "NON_METEOROLOGICAL",
        "GROUND_CLUTTER",
        "LOW_QUALITY",
        "LOW_SNR",
    }
    if not required <= mapping.keys() or mapping["NON_METEOROLOGICAL"] != 1 << 15:
        raise ValueError("open-source QC flag definition is incomplete")
    profile._flag_masks = mapping
    return profile
