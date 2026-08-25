from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

import yaml


class QPEConfigError(ValueError):
    """Raised when an RP-011 QPE profile is incomplete or inconsistent."""


@dataclass(frozen=True)
class BasicQPEConfig:
    input_field: str
    coefficient_a: float
    exponent_b: float
    no_rain_below_dbz: float
    maximum_rate_mm_h: float


@dataclass(frozen=True)
class GaugeAdjustmentConfig:
    enabled: bool
    method: str
    observation_qc_version: str | None


@dataclass(frozen=True)
class QPEProfile:
    profile_version: str
    algorithm_version: str
    radar_analysis_contract_version: str
    mosaic_contract_version: str
    flag_definition_version: str
    grid_id: str
    grid_config_version: str
    qpe: BasicQPEConfig
    gauge_adjustment: GaugeAdjustmentConfig


def load_qpe_profile(path: str | Path) -> QPEProfile:
    profile_path = Path(path)
    raw = yaml.safe_load(profile_path.read_text())
    if not isinstance(raw, dict) or raw.get("schema_version") != "1.0":
        raise QPEConfigError("unsupported QPE profile schema")
    try:
        qpe = raw["qpe"]
        gauge = raw["gauge_adjustment"]
        if qpe["relation"] != "power_law_z_r":
            raise QPEConfigError("unsupported QPE relation")
        if qpe["overflow_policy"] != "cap_and_report":
            raise QPEConfigError("unsupported QPE overflow policy")
        profile = QPEProfile(
            profile_version=str(raw["profile_version"]),
            algorithm_version=str(raw["algorithm_version"]),
            radar_analysis_contract_version=str(
                raw["radar_analysis_contract_version"]
            ),
            mosaic_contract_version=str(raw["mosaic_contract_version"]),
            flag_definition_version=str(raw["flag_definition_version"]),
            grid_id=str(raw["grid_id"]),
            grid_config_version=str(raw["grid_config_version"]),
            qpe=BasicQPEConfig(
                input_field=str(qpe["input_field"]),
                coefficient_a=float(qpe["coefficient_a"]),
                exponent_b=float(qpe["exponent_b"]),
                no_rain_below_dbz=float(qpe["no_rain_below_dbz"]),
                maximum_rate_mm_h=float(qpe["maximum_rate_mm_h"]),
            ),
            gauge_adjustment=GaugeAdjustmentConfig(
                enabled=bool(gauge["enabled"]),
                method=str(gauge["method"]),
                observation_qc_version=(
                    None
                    if gauge["observation_qc_version"] is None
                    else str(gauge["observation_qc_version"])
                ),
            ),
        )
    except (KeyError, TypeError, ValueError) as exc:
        if isinstance(exc, QPEConfigError):
            raise
        raise QPEConfigError(f"invalid QPE profile {profile_path}: {exc}") from exc
    _validate_profile(profile)
    return profile


def _validate_profile(profile: QPEProfile) -> None:
    if profile.radar_analysis_contract_version != "1.2":
        raise QPEConfigError("RP-011 requires RadarAnalysis contract 1.2")
    if profile.mosaic_contract_version != "1.0":
        raise QPEConfigError("RP-011 requires RadarMosaic contract 1.0")
    if profile.qpe.input_field != "DBZH_QC":
        raise QPEConfigError("Phase-1 QPE must consume DBZH_QC")
    if profile.qpe.coefficient_a <= 0 or profile.qpe.exponent_b <= 0:
        raise QPEConfigError("Z-R coefficient and exponent must be positive")
    if profile.qpe.maximum_rate_mm_h <= 0:
        raise QPEConfigError("maximum QPE rate must be positive")
    if (
        profile.gauge_adjustment.enabled
        or profile.gauge_adjustment.method != "none"
        or profile.gauge_adjustment.observation_qc_version is not None
    ):
        raise QPEConfigError(
            "RP-011 gauge adjustment must remain disabled without gauge QC input"
        )
