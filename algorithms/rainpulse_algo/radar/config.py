from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Any

import yaml


class RadarConfigError(ValueError):
    """Raised when a decoder configuration is incomplete or inconsistent."""


SOURCE_MOMENT_CODES = {
    "TREF": 1,
    "REF": 2,
    "VEL": 3,
    "SW": 4,
    "SQI": 5,
    "CPA": 6,
    "ZDR": 7,
    "LDR": 8,
    "RHO": 9,
    "PHI": 10,
    "KDP": 11,
    "CP": 12,
    "HCL": 14,
    "CF": 15,
    "SNRH": 16,
    "SNRV": 17,
}


@dataclass(frozen=True)
class FieldMapping:
    canonical_name: str
    source_name: str
    source_unit: str
    canonical_unit: str
    missing_value: int | float | str | None
    scale_factor: float
    add_offset: float

    @property
    def source_code(self) -> int:
        try:
            return SOURCE_MOMENT_CODES[self.source_name]
        except KeyError as error:
            raise RadarConfigError(f"unsupported FMT source moment {self.source_name!r}") from error


@dataclass(frozen=True)
class RadarDecoderConfig:
    path: Path
    schema_version: str
    config_version: str
    radar_id: str
    lifecycle: str
    display_name: str | None
    site: dict[str, Any]
    hardware: dict[str, Any]
    scan: dict[str, Any]
    fields: tuple[FieldMapping, ...]
    source: dict[str, Any]
    ancillary: dict[str, Any]
    known_issues: tuple[str, ...]

    @property
    def fields_by_source(self) -> dict[str, FieldMapping]:
        return {field.source_name: field for field in self.fields}

    @property
    def fields_by_code(self) -> dict[int, FieldMapping]:
        return {field.source_code: field for field in self.fields}


def load_radar_config(path: str | Path) -> RadarDecoderConfig:
    config_path = Path(path)
    value = yaml.safe_load(config_path.read_text())
    if not isinstance(value, dict):
        raise RadarConfigError("radar configuration must be a YAML object")

    required = {
        "schema_version",
        "config_version",
        "radar_id",
        "lifecycle",
        "site",
        "hardware",
        "scan",
        "fields",
        "source",
    }
    missing = sorted(required - value.keys())
    if missing:
        raise RadarConfigError(f"radar configuration is missing {', '.join(missing)}")
    if value["schema_version"] != "1.0":
        raise RadarConfigError("unsupported radar configuration schema")
    if value["lifecycle"] not in {"draft", "ready", "disabled"}:
        raise RadarConfigError("invalid radar configuration lifecycle")

    field_values = value["fields"]
    if not isinstance(field_values, list) or not field_values:
        raise RadarConfigError("at least one verified field mapping is required")
    fields = tuple(_field_mapping(item) for item in field_values)
    canonical_names = [item.canonical_name for item in fields]
    source_names = [item.source_name for item in fields]
    if len(canonical_names) != len(set(canonical_names)):
        raise RadarConfigError("canonical field mappings must be unique")
    if len(source_names) != len(set(source_names)):
        raise RadarConfigError("source field mappings must be unique")
    if "DBZH" not in canonical_names:
        raise RadarConfigError("FMT decoder configuration requires DBZH")

    source = _mapping(value["source"], "source")
    if source.get("format") != "cma-rstm-level2":
        raise RadarConfigError("FMT decoder requires source.format=cma-rstm-level2")
    if source.get("format_version") != "2.0":
        raise RadarConfigError("FMT decoder requires source.format_version=2.0")

    return RadarDecoderConfig(
        path=config_path,
        schema_version=value["schema_version"],
        config_version=str(value["config_version"]),
        radar_id=str(value["radar_id"]),
        lifecycle=str(value["lifecycle"]),
        display_name=value.get("display_name"),
        site=_mapping(value["site"], "site"),
        hardware=_mapping(value["hardware"], "hardware"),
        scan=_mapping(value["scan"], "scan"),
        fields=fields,
        source=source,
        ancillary=_mapping(value.get("ancillary", {}), "ancillary"),
        known_issues=tuple(str(item) for item in value.get("known_issues", [])),
    )


def _field_mapping(value: Any) -> FieldMapping:
    item = _mapping(value, "field mapping")
    required = {
        "canonical_name",
        "source_name",
        "source_unit",
        "canonical_unit",
        "missing_value",
        "scale_factor",
        "add_offset",
    }
    missing = sorted(required - item.keys())
    if missing:
        raise RadarConfigError(f"field mapping is missing {', '.join(missing)}")
    mapping = FieldMapping(
        canonical_name=str(item["canonical_name"]),
        source_name=str(item["source_name"]),
        source_unit=str(item["source_unit"]),
        canonical_unit=str(item["canonical_unit"]),
        missing_value=item["missing_value"],
        scale_factor=float(item["scale_factor"]),
        add_offset=float(item["add_offset"]),
    )
    mapping.source_code
    return mapping


def _mapping(value: Any, label: str) -> dict[str, Any]:
    if not isinstance(value, dict):
        raise RadarConfigError(f"{label} must be an object")
    return value
