import copy
import json
from pathlib import Path

import pytest
import yaml
from jsonschema import Draft202012Validator, FormatChecker, ValidationError


REPOSITORY_ROOT = Path(__file__).resolve().parents[2]
CONFIG_ROOT = REPOSITORY_ROOT / "configs"


def load_schema() -> dict:
    return json.loads((CONFIG_ROOT / "schemas" / "radar-config.schema.json").read_text())


def load_inventory_template() -> dict:
    return yaml.safe_load((CONFIG_ROOT / "radars" / "radar-inventory-template.yaml").read_text())


def validate(config: dict) -> None:
    schema = load_schema()
    Draft202012Validator.check_schema(schema)
    Draft202012Validator(schema, format_checker=FormatChecker()).validate(config)


def ready_synthetic_config() -> dict:
    config = load_inventory_template()
    config.update(
        {
            "radar_id": "synthetic_radar_01",
            "config_version": "synthetic-radar-v1",
            "lifecycle": "ready",
            "display_name": "Synthetic contract fixture",
            "inventory_owner": "contract-tests",
            "updated_at": "2026-08-24T00:00:00Z",
        }
    )
    config["site"] = {
        "longitude_deg": 0.0,
        "latitude_deg": 0.0,
        "altitude_m": 0.0,
        "antenna_altitude_m": 0.0,
        "altitude_datum": "synthetic-datum-v1",
    }
    config["hardware"] = {
        "manufacturer": "synthetic",
        "model": "contract-fixture",
        "radar_band": "S",
        "frequency_mhz": 2800.0,
        "beam_width_deg": 1.0,
        "beam_width_vertical_deg": 1.0,
        "dual_pol_available": False,
        "nyquist_velocity_mps": None,
        "calibration_offsets": {"dbzh_db": 0.0, "zdr_db": None},
    }
    config["scan"] = {
        "strategy_name": "synthetic-volume-v1",
        "expected_update_seconds": 300,
        "expected_elevations_deg": [0.5, 1.5],
        "expected_cut_elevations_deg": [0.5, 1.5],
        "azimuth_resolution_deg": 1.0,
        "range_gate_m": 1000.0,
        "max_range_m": 100000.0,
    }
    config["fields"] = [
        {
            "canonical_name": "DBZH",
            "source_name": "synthetic_dbzh",
            "source_unit": "dBZ",
            "canonical_unit": "dBZ",
            "missing_value": -9999.0,
            "scale_factor": 1.0,
            "add_offset": 0.0,
        }
    ]
    config["source"] = {
        "format": "rainpulse-synthetic-volume",
        "format_version": "1",
        "delivery": "manual",
        "uri_pattern": "s3://rainpulse-fixtures/raw/{radar_id}/{scan_time}",
        "timestamp_timezone": "UTC",
    }
    config["ancillary"] = {
        "dem_asset_version": "synthetic-dem-v1",
        "clutter_map_version": None,
        "coastline_asset_version": "synthetic-coastline-v1",
    }
    config["qc"] = {
        "profile": "synthetic-v1",
        "pipeline_version": "qc-contract-v1",
        "flag_definition_version": "qc-flags-v1",
    }
    return config


def test_inventory_template_is_a_valid_draft() -> None:
    validate(load_inventory_template())


def test_ready_configuration_requires_verified_values() -> None:
    config = load_inventory_template()
    config["lifecycle"] = "ready"

    with pytest.raises(ValidationError):
        validate(config)


def test_complete_synthetic_ready_configuration_is_valid() -> None:
    validate(ready_synthetic_config())


def test_ready_configuration_requires_dbzh_mapping() -> None:
    config = ready_synthetic_config()
    config["fields"] = []

    with pytest.raises(ValidationError):
        validate(config)


def test_unknown_configuration_keys_are_rejected() -> None:
    config = copy.deepcopy(ready_synthetic_config())
    config["site"]["guessed_station_code"] = "not-allowed"

    with pytest.raises(ValidationError):
        validate(config)


def test_z9598_real_sample_configuration_is_valid_but_not_ready() -> None:
    config = yaml.safe_load((CONFIG_ROOT / "radars" / "z9598.yaml").read_text())

    validate(config)
    assert config["lifecycle"] == "draft"
    assert config["site"]["longitude_deg"] == pytest.approx(117.08055877685547)
    assert config["scan"]["expected_cut_elevations_deg"][:4] == [0.5, 0.5, 1.5, 1.5]
    assert {item["canonical_name"] for item in config["fields"]} >= {
        "DBZH",
        "ZDR",
        "RHOHV",
        "PHIDP",
        "VR",
        "SW",
        "SNR",
    }

    config["lifecycle"] = "ready"
    with pytest.raises(ValidationError):
        validate(config)


def test_qc_flag_definition_is_a_unique_uint32_bitset() -> None:
    definition = yaml.safe_load((CONFIG_ROOT / "qc" / "flag-definitions.yaml").read_text())
    flags = definition["flags"]
    bits = [entry["bit"] for entry in flags]
    names = [entry["name"] for entry in flags]
    masks = [entry["mask"] for entry in flags]

    assert definition["storage_dtype"] == "uint32"
    assert len(bits) == len(set(bits))
    assert len(names) == len(set(names))
    assert all(0 <= bit <= 31 for bit in bits)
    assert masks == [1 << bit for bit in bits]
    assert {"MISSING", "LOW_QUALITY", "RADIAL_INTERFERENCE", "BEAM_BLOCKED"} <= set(names)


def test_rp007_health_profile_is_valid_and_versioned() -> None:
    schema = json.loads((CONFIG_ROOT / "schemas" / "radar-health.schema.json").read_text())
    profile = yaml.safe_load(
        (CONFIG_ROOT / "health" / "rp007-integrity-v1.yaml").read_text()
    )

    Draft202012Validator.check_schema(schema)
    Draft202012Validator(schema).validate(profile)
    assert profile["profile_version"] == "rp007-integrity-v1"
    assert set(profile["field_hard_limits"]) >= {"DBZH", "ZDR", "RHOHV", "PHIDP"}


def test_rp008_qc_profile_is_valid_and_keeps_external_assets_explicit() -> None:
    schema = json.loads((CONFIG_ROOT / "schemas" / "radar-qc.schema.json").read_text())
    profile = yaml.safe_load((CONFIG_ROOT / "qc" / "rp008-basic-v1.yaml").read_text())

    Draft202012Validator.check_schema(schema)
    Draft202012Validator(schema).validate(profile)
    assert profile["profile_version"] == "rp008-basic-v1"
    assert profile["flag_definition_version"] == "qc-flags-v1"
    assert profile["static_ground_clutter"]["asset_uri"] is None
    assert profile["sea_ap"]["coastline_asset_uri"] is None
    assert profile["quality_index"]["aggregation"] == "product"
