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
class VPRCorrectionConfig:
    enabled: bool
    method: str
    precipitation_type_field: str
    stratiform_code: int
    convective_code: int
    melting_layer_bottom_field: str
    melting_layer_top_field: str
    beam_height_field: str
    corrected_reflectivity_field: str
    correction_field: str
    uncertainty_field: str
    applied_mask_field: str
    stratiform_mask_field: str
    overshoot_mask_field: str
    max_bright_band_reduction_db: float
    above_melting_layer_correction_db_per_km: float
    maximum_positive_correction_db: float
    extrapolation_limit_m: float
    base_uncertainty_db: float
    uncertainty_growth_db_per_km: float
    maximum_uncertainty_db: float
    overshoot_policy: str
    write_bright_band_flag: bool


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
    vpr_correction: VPRCorrectionConfig | None = None


def load_qpe_profile(path: str | Path) -> QPEProfile:
    profile_path = Path(path)
    raw = yaml.safe_load(profile_path.read_text())
    if not isinstance(raw, dict) or raw.get("schema_version") != "1.0":
        raise QPEConfigError("unsupported QPE profile schema")
    try:
        qpe = raw["qpe"]
        gauge = raw["gauge_adjustment"]
        vpr = raw.get("vpr_correction")
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
            vpr_correction=(
                None
                if vpr is None
                else VPRCorrectionConfig(
                    enabled=bool(vpr["enabled"]),
                    method=str(vpr["method"]),
                    precipitation_type_field=str(vpr["precipitation_type_field"]),
                    stratiform_code=int(vpr["stratiform_code"]),
                    convective_code=int(vpr["convective_code"]),
                    melting_layer_bottom_field=str(
                        vpr["melting_layer_bottom_field"]
                    ),
                    melting_layer_top_field=str(vpr["melting_layer_top_field"]),
                    beam_height_field=str(vpr["beam_height_field"]),
                    corrected_reflectivity_field=str(
                        vpr["corrected_reflectivity_field"]
                    ),
                    correction_field=str(vpr["correction_field"]),
                    uncertainty_field=str(vpr["uncertainty_field"]),
                    applied_mask_field=str(vpr["applied_mask_field"]),
                    stratiform_mask_field=str(vpr["stratiform_mask_field"]),
                    overshoot_mask_field=str(vpr["overshoot_mask_field"]),
                    max_bright_band_reduction_db=float(
                        vpr["max_bright_band_reduction_db"]
                    ),
                    above_melting_layer_correction_db_per_km=float(
                        vpr["above_melting_layer_correction_db_per_km"]
                    ),
                    maximum_positive_correction_db=float(
                        vpr["maximum_positive_correction_db"]
                    ),
                    extrapolation_limit_m=float(vpr["extrapolation_limit_m"]),
                    base_uncertainty_db=float(vpr["base_uncertainty_db"]),
                    uncertainty_growth_db_per_km=float(
                        vpr["uncertainty_growth_db_per_km"]
                    ),
                    maximum_uncertainty_db=float(vpr["maximum_uncertainty_db"]),
                    overshoot_policy=str(vpr["overshoot_policy"]),
                    write_bright_band_flag=bool(vpr["write_bright_band_flag"]),
                )
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
    if profile.vpr_correction is None:
        return
    vpr = profile.vpr_correction
    if not vpr.enabled:
        raise QPEConfigError("VPR correction block must be enabled when present")
    if vpr.method != "stratiform_piecewise_linear":
        raise QPEConfigError("unsupported VPR correction method")
    if vpr.beam_height_field != "BEAM_HEIGHT":
        raise QPEConfigError("VPR correction must consume BEAM_HEIGHT")
    if vpr.stratiform_code == vpr.convective_code:
        raise QPEConfigError("stratiform and convective codes must differ")
    if vpr.corrected_reflectivity_field == profile.qpe.input_field:
        raise QPEConfigError("VPR corrected reflectivity field must differ from DBZH_QC")
    if vpr.max_bright_band_reduction_db <= 0:
        raise QPEConfigError("VPR bright-band reduction must be positive")
    if vpr.above_melting_layer_correction_db_per_km <= 0:
        raise QPEConfigError("VPR above-melting-layer correction must be positive")
    if vpr.maximum_positive_correction_db <= 0:
        raise QPEConfigError("VPR maximum positive correction must be positive")
    if vpr.extrapolation_limit_m <= 0:
        raise QPEConfigError("VPR extrapolation limit must be positive")
    if vpr.base_uncertainty_db < 0:
        raise QPEConfigError("VPR base uncertainty must be non-negative")
    if vpr.uncertainty_growth_db_per_km < 0:
        raise QPEConfigError("VPR uncertainty growth must be non-negative")
    if vpr.maximum_uncertainty_db <= 0:
        raise QPEConfigError("VPR maximum uncertainty must be positive")
    if vpr.overshoot_policy != "mark_missing":
        raise QPEConfigError("unsupported VPR overshoot policy")
