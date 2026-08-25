from __future__ import annotations

import re
from dataclasses import dataclass
from pathlib import Path

import yaml


class ProductBuilderConfigError(ValueError):
    """Raised when the RP-015 distribution profile is inconsistent."""


@dataclass(frozen=True)
class PaletteStop:
    minimum: float
    color: str


@dataclass(frozen=True)
class ProductPalette:
    version: str
    transparent_below_mm: float
    opacity: int
    rain_rate: tuple[PaletteStop, ...]
    rainfall_amount: tuple[PaletteStop, ...]


@dataclass(frozen=True)
class COGOutput:
    compression: str
    block_size: int


@dataclass(frozen=True)
class NetCDFOutput:
    format: str
    fill_value: float
    legacy_profile: str


@dataclass(frozen=True)
class ProductBuilderProfile:
    profile_version: str
    builder_version: str
    bundle_contract_version: str
    forecast_output_contract_version: str
    grid_id: str
    grid_config_version: str
    palette: ProductPalette
    cog: COGOutput
    netcdf: NetCDFOutput
    point_query_contract_version: str


def load_product_builder_profile(path: str | Path) -> ProductBuilderProfile:
    profile_path = Path(path)
    raw = yaml.safe_load(profile_path.read_text())
    if not isinstance(raw, dict) or raw.get("schema_version") != "1.0":
        raise ProductBuilderConfigError("unsupported product-builder profile schema")
    try:
        palette = raw["palette"]
        outputs = raw["outputs"]
        profile = ProductBuilderProfile(
            profile_version=str(raw["profile_version"]),
            builder_version=str(raw["builder_version"]),
            bundle_contract_version=str(raw["bundle_contract_version"]),
            forecast_output_contract_version=str(raw["forecast_output_contract_version"]),
            grid_id=str(raw["grid_id"]),
            grid_config_version=str(raw["grid_config_version"]),
            palette=ProductPalette(
                version=str(palette["version"]),
                transparent_below_mm=float(palette["transparent_below_mm"]),
                opacity=int(palette["opacity"]),
                rain_rate=_stops(palette["rain_rate"]),
                rainfall_amount=_stops(palette["rainfall_amount"]),
            ),
            cog=COGOutput(
                compression=str(outputs["cog"]["compression"]),
                block_size=int(outputs["cog"]["block_size"]),
            ),
            netcdf=NetCDFOutput(
                format=str(outputs["netcdf"]["format"]),
                fill_value=float(outputs["netcdf"]["fill_value"]),
                legacy_profile=str(outputs["netcdf"]["legacy_profile"]),
            ),
            point_query_contract_version=str(
                outputs["point_query_index"]["contract_version"]
            ),
        )
    except (KeyError, TypeError, ValueError) as exc:
        raise ProductBuilderConfigError(
            f"invalid product-builder profile {profile_path}: {exc}"
        ) from exc
    _validate(profile)
    return profile


def _stops(values: object) -> tuple[PaletteStop, ...]:
    if not isinstance(values, list):
        raise ProductBuilderConfigError("palette stops must be a list")
    return tuple(
        PaletteStop(minimum=float(value["minimum"]), color=str(value["color"]))
        for value in values
    )


def _validate(profile: ProductBuilderProfile) -> None:
    if profile.bundle_contract_version != "1.0":
        raise ProductBuilderConfigError("RP-015 requires product bundle contract 1.0")
    if profile.forecast_output_contract_version != "1.1":
        raise ProductBuilderConfigError("RP-015 requires ForecastOutput contract 1.1")
    if profile.cog.compression != "DEFLATE" or not 128 <= profile.cog.block_size <= 512:
        raise ProductBuilderConfigError("RP-015 COG settings differ from the frozen profile")
    if profile.netcdf != NetCDFOutput(
        "NETCDF3_CLASSIC", -9999.0, "numerical-model-example-v1"
    ):
        raise ProductBuilderConfigError("RP-015 NetCDF profile differs from the frozen profile")
    if profile.point_query_contract_version != "1.0":
        raise ProductBuilderConfigError("unsupported point-query index contract")
    if not 0 <= profile.palette.transparent_below_mm <= 1:
        raise ProductBuilderConfigError("transparent rainfall threshold is outside [0, 1]")
    if not 1 <= profile.palette.opacity <= 255:
        raise ProductBuilderConfigError("palette opacity is outside [1, 255]")
    for stops in (profile.palette.rain_rate, profile.palette.rainfall_amount):
        if len(stops) < 2 or any(
            current.minimum >= following.minimum
            for current, following in zip(stops, stops[1:], strict=False)
        ):
            raise ProductBuilderConfigError("palette stops must be strictly increasing")
        if stops[0].minimum != profile.palette.transparent_below_mm:
            raise ProductBuilderConfigError("first palette stop must equal transparency threshold")
        if any(not re.fullmatch(r"#[0-9a-fA-F]{6}", stop.color) for stop in stops):
            raise ProductBuilderConfigError("palette colors must use six-digit hex")

