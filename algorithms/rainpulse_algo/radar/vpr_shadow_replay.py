from __future__ import annotations

import json
from pathlib import Path
from typing import Any
from uuid import UUID

import numpy as np
import yaml
import zarr
from zarr.storage import MemoryStore

from .analysis_zarr import (
    build_radar_analysis_zarr_store,
    validate_radar_analysis_zarr_store,
)
from .qpe import QPEInputError
from .qpe_profile import load_qpe_profile

REPOSITORY_ROOT = Path(__file__).resolve().parents[3]
DEFAULT_MANIFEST_PATH = (
    REPOSITORY_ROOT / "configs" / "verification" / "rp017-vpr-shadow-replay-manifest-v1.json"
)
DEFAULT_PROFILE_PATH = REPOSITORY_ROOT / "configs" / "qpe" / "rp017-stratiform-vpr-v1.yaml"
DEFAULT_FLAG_DEFINITIONS_PATH = REPOSITORY_ROOT / "configs" / "qc" / "flag-definitions.yaml"
DEFAULT_MOSAIC_ANALYSIS_ID = UUID("75000000-0000-4000-8000-000000000001")


def run_vpr_shadow_replay_validation(
    manifest_path: str | Path = DEFAULT_MANIFEST_PATH,
    *,
    profile_path: str | Path = DEFAULT_PROFILE_PATH,
    flag_definitions_path: str | Path = DEFAULT_FLAG_DEFINITIONS_PATH,
) -> dict[str, Any]:
    manifest_file = Path(manifest_path).resolve(strict=True)
    profile_file = Path(profile_path).resolve(strict=True)
    flag_file = Path(flag_definitions_path).resolve(strict=True)
    manifest = _load_manifest(manifest_file)
    profile = load_qpe_profile(profile_file)
    _validate_manifest_profile_boundary(manifest, profile)
    flag_masks = _load_flag_masks(flag_file)

    case_reports = []
    passed_case_count = 0
    for case in manifest["cases"]:
        report = _run_case(case, profile=profile, flag_masks=flag_masks)
        case_reports.append(report)
        if report["status"] == "passed":
            passed_case_count += 1

    failed_case_count = len(case_reports) - passed_case_count
    return {
        "schema_version": manifest["schema_version"],
        "manifest_version": manifest["manifest_version"],
        "qpe_profile_version": profile.profile_version,
        "qpe_algorithm_version": profile.algorithm_version,
        "case_count": len(case_reports),
        "passed_case_count": passed_case_count,
        "failed_case_count": failed_case_count,
        "cases": case_reports,
    }


def _load_manifest(path: Path) -> dict[str, Any]:
    value = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(value, dict):
        raise ValueError("VPR shadow replay manifest must be a JSON object")
    if value.get("schema_version") != "1.0":
        raise ValueError("unsupported VPR shadow replay manifest schema")
    if value.get("manifest_version") != "rp017-vpr-shadow-replay-v1":
        raise ValueError("unsupported VPR shadow replay manifest version")
    cases = value.get("cases")
    if not isinstance(cases, list) or not cases:
        raise ValueError("VPR shadow replay manifest must contain non-empty cases")
    return value


def _validate_manifest_profile_boundary(manifest: dict[str, Any], profile: Any) -> None:
    if manifest.get("qpe_profile_version") != profile.profile_version:
        raise ValueError("manifest qpe_profile_version differs from the mounted QPE profile")
    if manifest.get("qpe_algorithm_version") != profile.algorithm_version:
        raise ValueError(
            "manifest qpe_algorithm_version differs from the mounted QPE profile"
        )
    if manifest.get("flag_definition_version") != profile.flag_definition_version:
        raise ValueError(
            "manifest flag_definition_version differs from the mounted QPE profile"
        )
    if profile.vpr_correction is None:
        raise ValueError("mounted QPE profile does not enable VPR correction")


def _load_flag_masks(path: Path) -> dict[str, np.uint32]:
    value = yaml.safe_load(path.read_text(encoding="utf-8"))
    if value.get("storage_dtype") != "uint32":
        raise ValueError("QC flag storage dtype must be uint32")
    return {
        str(item["name"]): np.uint32(item["mask"])
        for item in value.get("flags", [])
    }


def _run_case(
    case: dict[str, Any],
    *,
    profile: Any,
    flag_masks: dict[str, np.uint32],
) -> dict[str, Any]:
    case_id = _require_text(case, "case_id")
    description = _require_text(case, "description")
    analysis_id = UUID(str(case.get("analysis_id", str(DEFAULT_MOSAIC_ANALYSIS_ID))))
    asset_id = str(case.get("asset_id") or f"rp017-{case_id}")
    mosaic_objects = _build_mosaic_objects(
        case_id=case_id,
        analysis_id=analysis_id,
        input_data=_require_mapping(case, "input"),
    )

    expected_error = case.get("expected_error_substring")
    if isinstance(expected_error, str) and expected_error:
        try:
            build_radar_analysis_zarr_store(
                mosaic_objects,
                mosaic_uri=f"s3://rainpulse/rp017-shadow-replay/{case_id}/mosaic.zarr",
                analysis_id=analysis_id,
                profile=profile,
                asset_id=asset_id,
                flag_masks=flag_masks,
            )
        except QPEInputError as error:
            if expected_error in str(error):
                return {
                    "case_id": case_id,
                    "description": description,
                    "status": "passed",
                    "mode": "expected_error",
                    "expected_error_substring": expected_error,
                    "actual_error": str(error),
                }
            return {
                "case_id": case_id,
                "description": description,
                "status": "failed",
                "mode": "expected_error",
                "expected_error_substring": expected_error,
                "actual_error": str(error),
                "failure": "raised a different QPEInputError than expected",
            }
        return {
            "case_id": case_id,
            "description": description,
            "status": "failed",
            "mode": "expected_error",
            "expected_error_substring": expected_error,
            "failure": "case succeeded but an explicit fail-closed error was required",
        }

    expected = _require_mapping(case, "expected")
    try:
        objects = build_radar_analysis_zarr_store(
            mosaic_objects,
            mosaic_uri=f"s3://rainpulse/rp017-shadow-replay/{case_id}/mosaic.zarr",
            analysis_id=analysis_id,
            profile=profile,
            asset_id=asset_id,
            flag_masks=flag_masks,
        )
        validation = validate_radar_analysis_zarr_store(objects)
        store = MemoryStore()
        store.update(objects)
        root = zarr.open_group(store=store, mode="r")
        _validate_positive_case(root, objects, validation, expected)
    except Exception as error:  # noqa: BLE001 - report exact replay mismatch
        return {
            "case_id": case_id,
            "description": description,
            "status": "failed",
            "mode": "success_case",
            "failure": str(error),
        }

    summary = json.loads(objects["qpe/summary.json"])
    return {
        "case_id": case_id,
        "description": description,
        "status": "passed",
        "mode": "success_case",
        "summary": {
            "input_field": summary["input_field"],
            "vpr_corrected_cell_count": summary["vpr_corrected_cell_count"],
            "vpr_bright_band_cell_count": summary["vpr_bright_band_cell_count"],
            "vpr_overshoot_missing_cell_count": summary["vpr_overshoot_missing_cell_count"],
        },
        "validation": {
            "valid_cell_count": validation["valid_cell_count"],
            "missing_cell_count": validation["missing_cell_count"],
        },
    }


def _validate_positive_case(
    root: zarr.Group,
    objects: dict[str, bytes],
    validation: dict[str, Any],
    expected: dict[str, Any],
) -> None:
    reflectivity_field = expected.get("qpe_reflectivity_field")
    if (
        reflectivity_field is not None
        and root.attrs.get("qpe_reflectivity_field") != reflectivity_field
    ):
        raise AssertionError("qpe_reflectivity_field differs from the fixed replay manifest")

    for field_name in (
        "DBZH_VPR_INPUT",
        "DBZH_QC",
        "DBZH_VPR_CORRECTED",
        "VPR_APPLIED_MASK",
        "VPR_STRATIFORM_MASK",
        "VPR_OVERSHOOT_MASK",
        "VALID_MASK",
        "LOW_QUALITY_MASK",
        "QC_FLAGS",
    ):
        if field_name in expected:
            _assert_array_equal(root[field_name][:], expected[field_name], field_name)

    summary = json.loads(objects["qpe/summary.json"])
    for key, value in _require_mapping(expected, "summary").items():
        if summary.get(key) != value:
            raise AssertionError(f"summary field {key} differs from the fixed replay manifest")

    for key, value in _require_mapping(expected, "validation").items():
        if validation.get(key) != value:
            raise AssertionError(
                f"validation field {key} differs from the fixed replay manifest"
            )

    rate_expectations = _require_mapping(expected, "rate_expectations")
    rate = root["RATE_QPE"][:]
    for row, column in rate_expectations.get("nan_indices", []):
        if not np.isnan(rate[row, column]):
            raise AssertionError("RATE_QPE expected a missing cell but found a finite value")
    for left, right in rate_expectations.get("less_than", []):
        if not float(rate[left[0], left[1]]) < float(rate[right[0], right[1]]):
            raise AssertionError("RATE_QPE ordering check failed for less_than")
    for left, right in rate_expectations.get("greater_than", []):
        if not float(rate[left[0], left[1]]) > float(rate[right[0], right[1]]):
            raise AssertionError("RATE_QPE ordering check failed for greater_than")
    for left, right in rate_expectations.get("equal_to", []):
        if not np.isclose(rate[left[0], left[1]], rate[right[0], right[1]], atol=1e-6):
            raise AssertionError("RATE_QPE ordering check failed for equal_to")


def _build_mosaic_objects(
    *,
    case_id: str,
    analysis_id: UUID,
    input_data: dict[str, Any],
) -> dict[str, bytes]:
    dbzh = np.asarray(input_data["DBZH_QC"], dtype="float32")
    if dbzh.ndim != 2:
        raise ValueError("DBZH_QC must be a two-dimensional array")
    lat = np.asarray(input_data["lat"], dtype="float32")
    lon = np.asarray(input_data["lon"], dtype="float32")
    if lat.shape != (dbzh.shape[0],):
        raise ValueError("lat length must match the row count of DBZH_QC")
    if lon.shape != (dbzh.shape[1],):
        raise ValueError("lon length must match the column count of DBZH_QC")

    valid = np.asarray(
        input_data.get("VALID_MASK", np.ones(dbzh.shape, dtype="uint8")),
        dtype="uint8",
    )
    if valid.shape != dbzh.shape:
        raise ValueError("VALID_MASK shape must match DBZH_QC")
    missing = valid == 0

    store = MemoryStore()
    root = zarr.group(store=store, overwrite=True)
    root.attrs.update(
        {
            "contract_name": "rainpulse.radar-mosaic",
            "contract_version": "1.0",
            "asset_id": f"76000000-0000-4000-8000-{case_id[-12:].rjust(12, '0')}",
            "analysis_id": str(analysis_id),
            "analysis_time": "2026-08-25T12:05:00+00:00",
            "grid_id": "fuzhou_118_123_25_27_0p01deg_v1",
            "grid_config_version": "fuzhou-grid-0p01deg-v1",
            "coordinate_sha256": f"rp017-shadow-replay-{case_id}",
            "crs": "EPSG:4326",
            "registration": "point",
            "profile_version": "rp016-qi-mosaic-v1",
            "mosaic_algorithm_version": "qi-mosaic-1.1.0",
            "analysis_cycle_version": "analysis-cycle-rp010-v1",
            "flag_definition_version": "qc-flags-v1",
            "contributors": [{"radar_id": "z9598", "scan_id": case_id}],
            "input_asset_ids": [f"74000000-0000-4000-8000-{case_id[-12:].rjust(12, '0')}"],
            "qc_pipeline_versions": ["rp008-basic-qc-1.0.0"],
            "radar_source_codes": {"z9598": 1},
            "blended_source_code": 65535,
            "operational_eligible": True,
            "operational_reasons": [],
        }
    )
    root.create_dataset("lat", data=lat)
    root.create_dataset("lon", data=lon)

    float_fields = {
        "DBZH_QC": dbzh,
        "REF_NOWCAST": np.asarray(input_data.get("REF_NOWCAST", dbzh), dtype="float32"),
        "QUALITY_INDEX": np.full(dbzh.shape, 0.8, dtype="float32"),
        "QI_METEO": np.full(dbzh.shape, np.nan, dtype="float32"),
        "QI_BLOCKAGE": np.full(dbzh.shape, 0.8, dtype="float32"),
        "QI_BEAM_HEIGHT": np.full(dbzh.shape, 0.7, dtype="float32"),
        "QI_ATTENUATION": np.full(dbzh.shape, np.nan, dtype="float32"),
        "QI_INTERFERENCE": np.full(dbzh.shape, np.nan, dtype="float32"),
        "QI_TIME": np.full(dbzh.shape, 0.9, dtype="float32"),
        "QI_CALIBRATION": np.full(dbzh.shape, np.nan, dtype="float32"),
        "QI_RANGE": np.full(dbzh.shape, np.nan, dtype="float32"),
        "SOURCE_ELEVATION": np.full(dbzh.shape, 0.5, dtype="float32"),
        "BEAM_HEIGHT": np.asarray(input_data["BEAM_HEIGHT"], dtype="float32"),
        "TERRAIN_HEIGHT": np.full(dbzh.shape, 100.0, dtype="float32"),
        "BLOCKAGE_RATE": np.full(dbzh.shape, 0.2, dtype="float32"),
        "DATA_AGE": np.full(dbzh.shape, 0.3, dtype="float32"),
    }
    for optional_name in (
        "MELTING_LAYER_BOTTOM_HEIGHT",
        "MELTING_LAYER_TOP_HEIGHT",
    ):
        if optional_name in input_data:
            float_fields[optional_name] = np.asarray(input_data[optional_name], dtype="float32")
    for name, values in float_fields.items():
        if values.shape != dbzh.shape:
            raise ValueError(f"{name} shape must match DBZH_QC")
        payload = values.copy()
        payload[missing] = np.nan
        root.create_dataset(name, data=payload)

    if "PRECIP_TYPE" in input_data:
        precip_type = np.asarray(input_data["PRECIP_TYPE"], dtype="uint8")
        if precip_type.shape != dbzh.shape:
            raise ValueError("PRECIP_TYPE shape must match DBZH_QC")
        precip_payload = precip_type.copy()
        precip_payload[missing] = 0
        root.create_dataset("PRECIP_TYPE", data=precip_payload)

    qc_flags = np.asarray(
        input_data.get("QC_FLAGS", np.zeros(dbzh.shape, dtype="uint32")), dtype="uint32"
    )
    if qc_flags.shape != dbzh.shape:
        raise ValueError("QC_FLAGS shape must match DBZH_QC")
    root.create_dataset("QC_FLAGS", data=qc_flags)
    source_radar = np.ones(dbzh.shape, dtype="uint16")
    source_radar[missing] = 0
    contributor_count = np.ones(dbzh.shape, dtype="uint8")
    contributor_count[missing] = 0
    root.create_dataset("SOURCE_RADAR", data=source_radar)
    root.create_dataset("CONTRIBUTOR_COUNT", data=contributor_count)
    root.create_dataset("VALID_MASK", data=valid)
    root.create_dataset(
        "LOW_QUALITY_MASK",
        data=np.asarray(
            input_data.get("LOW_QUALITY_MASK", np.zeros(dbzh.shape, dtype="uint8")), dtype="uint8"
        ),
    )
    store["mosaic/summary.json"] = json.dumps(
        {"valid_cell_count": int(np.count_nonzero(valid))},
        separators=(",", ":"),
        sort_keys=True,
    ).encode()
    zarr.consolidate_metadata(store)
    return {str(key): bytes(value) for key, value in store.items()}


def _assert_array_equal(actual: np.ndarray, expected: Any, field_name: str) -> None:
    expected_array = np.asarray(
        [[np.nan if value is None else value for value in row] for row in expected]
        if isinstance(expected, list) and expected and isinstance(expected[0], list)
        else expected,
        dtype="float32" if np.issubdtype(actual.dtype, np.floating) else actual.dtype,
    )
    if actual.shape != expected_array.shape:
        raise AssertionError(f"{field_name} shape differs from the fixed replay manifest")
    if np.issubdtype(actual.dtype, np.floating):
        if not np.allclose(actual, expected_array, equal_nan=True, atol=1e-6, rtol=1e-6):
            raise AssertionError(f"{field_name} values differ from the fixed replay manifest")
        return
    if not np.array_equal(actual, expected_array.astype(actual.dtype, copy=False)):
        raise AssertionError(f"{field_name} values differ from the fixed replay manifest")


def _require_mapping(parent: dict[str, Any], key: str) -> dict[str, Any]:
    value = parent.get(key)
    if not isinstance(value, dict):
        raise ValueError(f"{key} must be a JSON object")
    return value


def _require_text(parent: dict[str, Any], key: str) -> str:
    value = parent.get(key)
    if not isinstance(value, str) or not value:
        raise ValueError(f"{key} must be a non-empty string")
    return value
