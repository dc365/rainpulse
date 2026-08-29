from __future__ import annotations

import re
from dataclasses import dataclass
from pathlib import Path

import yaml

from .profile import PaletteStop


class EnsembleProductConfigError(ValueError):
    """Raised when the RP-023 offline product profile is inconsistent."""


@dataclass(frozen=True)
class EnsembleApplicationProductProfile:
    profile_version: str
    builder_version: str
    bundle_contract_version: str
    forecast_output_contract_version: str
    source_product_profile_version: str
    event_operator: str
    thresholds_mm_h: tuple[float, ...]
    calibration_status: str
    quantiles: tuple[float, ...]
    palette_version: str
    opacity: int
    probability_transparent_below: float
    probability_palette: tuple[PaletteStop, ...]
    quantile_transparent_below_mm_h: float
    quantile_palette: tuple[PaletteStop, ...]
    netcdf_fill_value: float
    operational_enabled: bool
    operational_gate: str


def load_ensemble_application_product_profile(
    path: str | Path,
) -> EnsembleApplicationProductProfile:
    profile_path = Path(path)
    raw = yaml.safe_load(profile_path.read_text())
    if not isinstance(raw, dict) or raw.get("schema_version") != "1.0":
        raise EnsembleProductConfigError("unsupported ensemble-product profile schema")
    try:
        exceedance = raw["rain_rate_exceedance"]
        quantiles = raw["rain_rate_quantiles"]
        rendering = raw["rendering"]
        outputs = raw["outputs"]
        publication = raw["publication"]
        profile = EnsembleApplicationProductProfile(
            profile_version=str(raw["profile_version"]),
            builder_version=str(raw["builder_version"]),
            bundle_contract_version=str(raw["bundle_contract_version"]),
            forecast_output_contract_version=str(raw["forecast_output_contract_version"]),
            source_product_profile_version=str(raw["source_product_profile_version"]),
            event_operator=str(exceedance["event_operator"]),
            thresholds_mm_h=tuple(float(value) for value in exceedance["thresholds_mm_h"]),
            calibration_status=str(exceedance["calibration_status"]),
            quantiles=tuple(float(value) for value in quantiles["quantiles"]),
            palette_version=str(rendering["palette_version"]),
            opacity=int(rendering["opacity"]),
            probability_transparent_below=float(
                rendering["probability_transparent_below"]
            ),
            probability_palette=_stops(rendering["probability"]),
            quantile_transparent_below_mm_h=float(
                rendering["quantile_transparent_below_mm_h"]
            ),
            quantile_palette=_stops(rendering["quantile"]),
            netcdf_fill_value=float(outputs["netcdf"]["fill_value"]),
            operational_enabled=bool(publication["operational_enabled"]),
            operational_gate=str(publication["operational_gate"]),
        )
    except (KeyError, TypeError, ValueError) as exc:
        raise EnsembleProductConfigError(
            f"invalid ensemble-product profile {profile_path}: {exc}"
        ) from exc
    _validate(profile)
    return profile


def _stops(values: object) -> tuple[PaletteStop, ...]:
    if not isinstance(values, list):
        raise EnsembleProductConfigError("ensemble palette stops must be a list")
    return tuple(
        PaletteStop(minimum=float(item["minimum"]), color=str(item["color"]))
        for item in values
    )


def _validate(profile: EnsembleApplicationProductProfile) -> None:
    if (
        profile.profile_version != "rp023-ensemble-application-products-v1"
        or profile.builder_version != "ensemble-application-product-builder-1.0.0"
        or profile.bundle_contract_version != "1.0"
        or profile.forecast_output_contract_version != "1.2"
        or profile.source_product_profile_version != "rp022-ensemble-products-v1"
    ):
        raise EnsembleProductConfigError("RP-023 product identity differs from frozen profile")
    if (
        profile.event_operator != "greater_than"
        or profile.thresholds_mm_h != (1.0, 5.0, 10.0, 20.0, 50.0)
        or profile.calibration_status
        != "raw_ensemble_relative_frequency_uncalibrated"
        or profile.quantiles != (0.1, 0.5, 0.9)
    ):
        raise EnsembleProductConfigError("RP-023 probability semantics differ")
    if profile.operational_enabled or (
        profile.operational_gate
        != "independent_fujian_probabilistic_acceptance_required"
    ):
        raise EnsembleProductConfigError("RP-023 products must remain offline")
    if profile.netcdf_fill_value != -9999.0:
        raise EnsembleProductConfigError("RP-023 NetCDF fill value differs")
    if not 1 <= profile.opacity <= 255:
        raise EnsembleProductConfigError("RP-023 palette opacity is invalid")
    for stops, transparent in (
        (profile.probability_palette, profile.probability_transparent_below),
        (profile.quantile_palette, profile.quantile_transparent_below_mm_h),
    ):
        if len(stops) < 2 or stops[0].minimum != transparent:
            raise EnsembleProductConfigError("first palette stop must equal transparency")
        if any(
            current.minimum >= following.minimum
            for current, following in zip(stops, stops[1:], strict=False)
        ):
            raise EnsembleProductConfigError("ensemble palette stops must increase")
        if any(not re.fullmatch(r"#[0-9a-fA-F]{6}", stop.color) for stop in stops):
            raise EnsembleProductConfigError("ensemble palette colors are invalid")
