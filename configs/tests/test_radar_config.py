import copy
import hashlib
import json
import math
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
    assert config["ancillary"]["dem_asset_version"] == "copernicus-dem-glo30-2022-v1"
    assert config["ancillary"]["coastline_asset_version"] == (
        "gshhg-2.3.7-fujian-taiwan-v1"
    )

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


def test_fuzhou_grid_is_valid_and_matches_inclusive_point_registration() -> None:
    schema = json.loads((CONFIG_ROOT / "schemas" / "grid-config.schema.json").read_text())
    grid = yaml.safe_load((CONFIG_ROOT / "grids" / "fuzhou-0p01deg-v1.yaml").read_text())

    Draft202012Validator.check_schema(schema)
    Draft202012Validator(schema).validate(grid)
    assert grid["grid_id"] == "fuzhou_118_123_25_27_0p01deg_v1"
    assert grid["crs"]["code"] == 4326
    assert grid["coordinates"]["dimension_order"] == ["lat", "lon"]
    assert grid["coordinate_sha256"] == (
        "111a8653e5f227153216d100b81e4214bd2dbf3d134ee07641969be884a3d658"
    )

    bounds = grid["bounds"]
    spacing = grid["spacing"]
    expected_lon = round((bounds["east"] - bounds["west"]) / spacing["longitude_deg"]) + 1
    expected_lat = round((bounds["north"] - bounds["south"]) / spacing["latitude_deg"]) + 1
    assert grid["shape"] == {"longitude": expected_lon, "latitude": expected_lat}
    assert grid["shape"] == {"longitude": 501, "latitude": 201}

    image_edges = (
        bounds["west"] - spacing["longitude_deg"] / 2,
        bounds["south"] - spacing["latitude_deg"] / 2,
        bounds["east"] + spacing["longitude_deg"] / 2,
        bounds["north"] + spacing["latitude_deg"] / 2,
    )
    assert image_edges == pytest.approx((117.995, 24.995, 123.005, 27.005))
    assert not math.isclose(
        spacing["longitude_deg"] * math.cos(math.radians(grid["reference_latitude_deg"])),
        spacing["latitude_deg"],
    )


def test_fujian_taiwan_ancillary_sources_are_valid_and_cover_104_dem_tiles() -> None:
    schema = json.loads(
        (CONFIG_ROOT / "schemas" / "ancillary-source.schema.json").read_text()
    )
    source = yaml.safe_load((CONFIG_ROOT / "ancillary" / "fujian-taiwan-v1.yaml").read_text())

    Draft202012Validator.check_schema(schema)
    Draft202012Validator(schema, format_checker=FormatChecker()).validate(source)
    bounds = source["bounds"]
    expected_tiles = (bounds["east"] - bounds["west"]) * (
        bounds["north"] - bounds["south"]
    )
    assert expected_tiles == 104
    assert source["dem"]["planned_tile_count"] == expected_tiles
    assert source["dem"]["missing_tile_policy"] == (
        "allow_source_404_below_frozen_land_area_threshold"
    )
    assert source["dem"]["max_uncovered_land_area_km2_per_tile"] == pytest.approx(0.1)
    assert source["dem"]["horizontal_crs"] == "EPSG:4326"
    assert source["dem"]["vertical_crs"] == "EPSG:3855"
    assert source["coastline"]["source_sha256"] == (
        "8dbbe7e071e77e9e75f2d639239099ebca8d5c16d6a07df8169729d49f15cf41"
    )
    assert source["static_clutter"]["status"] == "awaiting_clear_air_samples"


def test_rp031_automatic_verification_profile_is_frozen_and_non_promoting() -> None:
    schema = json.loads(
        (CONFIG_ROOT / "schemas" / "operational-verification-profile.schema.json").read_text()
    )
    profile = yaml.safe_load(
        (CONFIG_ROOT / "verification" / "rp031-operational-deterministic-v1.yaml").read_text()
    )

    Draft202012Validator.check_schema(schema)
    Draft202012Validator(schema).validate(profile)
    assert profile["lead_minutes"] == list(range(5, 125, 5))
    assert profile["fss_windows_km"] == [1, 5, 10, 20, 40]
    assert profile["validity_domain"] == "common"
    assert profile["promotion_eligible"] is False


def test_rp009_hybrid_profile_is_valid_and_explicitly_engineering_only() -> None:
    schema = json.loads(
        (CONFIG_ROOT / "schemas" / "radar-grid-profile.schema.json").read_text()
    )
    profile_path = CONFIG_ROOT / "gridding" / "rp009-hybrid-v1.1.yaml"
    profile = yaml.safe_load(profile_path.read_text())

    Draft202012Validator.check_schema(schema)
    Draft202012Validator(schema).validate(profile)
    assert profile["profile_version"] == "rp009-hybrid-v1.1"
    assert profile["algorithm_version"] == "hybrid-scan-1.0.1"
    assert profile["grid_id"] == "fuzhou_118_123_25_27_0p01deg_v1"
    assert profile["dem"]["asset_version"] == "copernicus-dem-glo30-2022-v1"
    assert profile["beam_geometry"]["unverified_vertical_datum_policy"] == (
        "allow_engineering_only"
    )
    assert profile["blockage"]["flag_fraction"] < profile["blockage"][
        "maximum_usable_fraction"
    ]
    assert profile["hybrid_scan"]["selection"] == "lowest_usable_elevation"
    assert profile["hybrid_scan"]["reject_flags"] == ["MISSING", "HARDWARE_ANOMALY"]
    assert hashlib.sha256(profile_path.read_bytes()).hexdigest() == (
        "0c4242370b9c8b7fa0ccb6e33d6e2c1e221400222c73fbe50b433f1ca1798c70"
    )


def test_rp016_hybrid_profile_versions_hard_qc_gate_immutably() -> None:
    schema = json.loads(
        (CONFIG_ROOT / "schemas" / "radar-grid-profile.schema.json").read_text()
    )
    profile = yaml.safe_load(
        (CONFIG_ROOT / "gridding" / "rp016-hybrid-v1.yaml").read_text()
    )

    Draft202012Validator(schema).validate(profile)
    assert profile["profile_version"] == "rp016-hybrid-v1"
    assert profile["algorithm_version"] == "hybrid-scan-1.1.0"
    assert {
        "MISSING",
        "HARDWARE_ANOMALY",
        "RADIAL_INTERFERENCE",
        "GROUND_CLUTTER",
        "SEA_CLUTTER",
        "ANOMALOUS_PROPAGATION",
        "BIOLOGICAL_ECHO",
    } <= set(profile["hybrid_scan"]["reject_flags"])


def test_rp010_mosaic_profile_freezes_time_alignment_and_linear_z_fusion() -> None:
    schema = json.loads(
        (CONFIG_ROOT / "schemas" / "radar-mosaic-profile.schema.json").read_text()
    )
    profile_path = CONFIG_ROOT / "mosaic" / "rp010-qi-mosaic-v1.yaml"
    profile = yaml.safe_load(profile_path.read_text())

    Draft202012Validator.check_schema(schema)
    Draft202012Validator(schema).validate(profile)
    assert profile["alignment"]["step_seconds"] == 300
    assert profile["alignment"]["minimum_contributors"] == 1
    assert profile["alignment"]["minimum_operational_contributors"] >= 2
    assert profile["alignment"]["expected_radar_ids"] == []
    assert profile["fusion"]["method"] == "highest_qi_then_linear_z_blend"
    assert profile["fusion"]["blended_source_code"] == 65535
    assert profile["fusion"]["reject_flags"] == ["MISSING", "HARDWARE_ANOMALY"]
    assert hashlib.sha256(profile_path.read_bytes()).hexdigest() == (
        "0d675a4aa9d667222e32689b4881a7def04dc063ef5f50c18a910b4d597d7a05"
    )


def test_rp016_mosaic_profile_versions_hard_qc_gate_immutably() -> None:
    schema = json.loads(
        (CONFIG_ROOT / "schemas" / "radar-mosaic-profile.schema.json").read_text()
    )
    profile = yaml.safe_load(
        (CONFIG_ROOT / "mosaic" / "rp016-qi-mosaic-v1.yaml").read_text()
    )

    Draft202012Validator(schema).validate(profile)
    assert profile["profile_version"] == "rp016-qi-mosaic-v1"
    assert profile["algorithm_version"] == "qi-mosaic-1.1.0"
    assert {
        "MISSING",
        "HARDWARE_ANOMALY",
        "RADIAL_INTERFERENCE",
        "GROUND_CLUTTER",
        "SEA_CLUTTER",
        "ANOMALOUS_PROPAGATION",
        "BIOLOGICAL_ECHO",
    } <= set(profile["fusion"]["reject_flags"])


def test_rp011_qpe_profile_freezes_basic_zr_and_disables_gauge_adjustment() -> None:
    schema = json.loads(
        (CONFIG_ROOT / "schemas" / "qpe-profile.schema.json").read_text()
    )
    profile = yaml.safe_load(
        (CONFIG_ROOT / "qpe" / "rp011-basic-zr-v1.yaml").read_text()
    )

    Draft202012Validator.check_schema(schema)
    Draft202012Validator(schema).validate(profile)
    assert profile["qpe"]["relation"] == "power_law_z_r"
    assert profile["qpe"]["coefficient_a"] == pytest.approx(200.0)
    assert profile["qpe"]["exponent_b"] == pytest.approx(1.6)
    assert profile["qpe"]["overflow_policy"] == "cap_and_report"
    assert profile["flag_definition_version"] == "qc-flags-v1"
    assert profile["gauge_adjustment"] == {
        "enabled": False,
        "method": "none",
        "observation_qc_version": None,
    }


def test_rp012_diagnostic_profile_freezes_layers_and_transparency() -> None:
    schema = json.loads(
        (CONFIG_ROOT / "schemas" / "diagnostic-profile.schema.json").read_text()
    )
    profile = yaml.safe_load(
        (
            CONFIG_ROOT
            / "diagnostics"
            / "rp012-operational-diagnostics-v1.yaml"
        ).read_text()
    )

    Draft202012Validator.check_schema(schema)
    Draft202012Validator(schema).validate(profile)
    assert profile["renderer_version"] == "radar-diagnostic-renderer-1.0.0"
    assert profile["grid_render"] == {
        "pixel_scale": 2,
        "north_up": True,
        "missing_alpha": 0,
    }
    assert profile["polar_render"]["sweep_selection"] == "lowest_dbzh_sweep"
    assert {
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
    } == set(profile["layers"])


def test_rp013_nowcast_input_profile_freezes_sequence_and_operational_gates() -> None:
    schema = json.loads(
        (CONFIG_ROOT / "schemas" / "nowcast-input-profile.schema.json").read_text()
    )
    Draft202012Validator.check_schema(schema)
    profiles = [
        yaml.safe_load((CONFIG_ROOT / "nowcast" / name).read_text())
        for name in ("rp013-fixed-5min-v1.yaml", "rp013-fixed-5min-v1.1.yaml")
    ]
    for profile in profiles:
        Draft202012Validator(schema).validate(profile)
        assert profile["nowcast_input_contract_version"] == "1.2"
        assert profile["radar_analysis_contract_version"] == "1.2"
        assert profile["sequence"] == {
            "minimum_frames": 3,
            "maximum_frames": 6,
            "timestep_minutes": 5,
            "selection": "latest_contiguous",
        }
        assert profile["gates"]["require_all_frames_operational_eligible"] is True
        assert profile["gates"]["minimum_valid_coverage_ratio"] > 0
        assert profile["gates"]["minimum_mean_quality_index"] > 0
    assert profiles[1]["builder_version"] == "nowcast-input-builder-1.0.1"


def test_rp014_pysteps_lk_profile_freezes_motion_and_baselines() -> None:
    schema = json.loads(
        (CONFIG_ROOT / "schemas" / "pysteps-lk-profile.schema.json").read_text()
    )
    profile_path = CONFIG_ROOT / "nowcast" / "rp014-pysteps-lk-v1.yaml"
    profile = yaml.safe_load(profile_path.read_text())

    Draft202012Validator.check_schema(schema)
    Draft202012Validator(schema).validate(profile)
    assert profile["pysteps_version"] == "1.21.5"
    assert profile["opencv_version"] == "5.0.0.93"
    assert profile["forecast_output_contract_version"] == "1.1"
    assert profile["sequence"] == {
        "minimum_frames": 3,
        "maximum_frames": 6,
        "timestep_minutes": 5,
    }
    assert profile["motion"]["missing_policy"].endswith("preserve_advected_mask")
    assert profile["motion"]["fallback"] == "zero_motion_when_insufficient_features"
    assert profile["extrapolation"]["lead_count"] == 24
    assert profile["extrapolation"]["baselines"] == ["persistence", "translation"]
    assert profile["motion"]["minimum_trackable_rain_pixels"] == 16
    assert profile["motion"]["missing_policy"] == (
        "dry_floor_working_copy_preserve_advected_mask"
    )
    assert hashlib.sha256(profile_path.read_bytes()).hexdigest() == (
        "18bc0d11b01b6437f63a79c57997a818d1aaed291d8f16a24efc54709b86a48d"
    )


def test_rp016_pysteps_lk_profile_versions_motion_safeguards_immutably() -> None:
    schema = json.loads(
        (CONFIG_ROOT / "schemas" / "pysteps-lk-profile.schema.json").read_text()
    )
    profile = yaml.safe_load(
        (CONFIG_ROOT / "nowcast" / "rp016-pysteps-lk-v1.yaml").read_text()
    )

    Draft202012Validator(schema).validate(profile)
    assert profile["profile_version"] == "rp016-pysteps-lk-v1"
    assert profile["model_version"] == "pysteps-lk-1.1.0"
    assert profile["motion"]["minimum_trackable_rain_pixels"] == 64
    assert profile["motion"]["minimum_motion_features"] == 4
    assert profile["motion"]["missing_buffer_pixels"] == 5
    assert profile["motion"]["missing_policy"] == (
        "nearest_valid_buffer_preserve_advected_mask"
    )


def test_rp015_product_profile_freezes_all_distribution_formats() -> None:
    schema = json.loads(
        (CONFIG_ROOT / "schemas" / "product-builder-profile.schema.json").read_text()
    )
    profile = yaml.safe_load(
        (CONFIG_ROOT / "products" / "rp015-application-products-v1.yaml").read_text()
    )

    Draft202012Validator.check_schema(schema)
    Draft202012Validator(schema).validate(profile)
    assert profile["bundle_contract_version"] == "1.0"
    assert profile["forecast_output_contract_version"] == "1.1"
    assert profile["outputs"]["png"] == {"enabled": True, "north_up": True}
    assert profile["outputs"]["cog"]["compression"] == "DEFLATE"
    assert profile["outputs"]["netcdf"]["format"] == "NETCDF3_CLASSIC"
    assert profile["outputs"]["netcdf"]["fill_value"] == -9999.0
