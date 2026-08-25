from __future__ import annotations

import json
from collections.abc import Mapping
from datetime import UTC, datetime
from typing import Any
from uuid import UUID

import numpy as np
import zarr
from numcodecs import Blosc
from zarr.storage import MemoryStore

from .mosaic_zarr import REQUIRED_FIELDS as MOSAIC_FIELDS
from .mosaic_zarr import validate_radar_mosaic_zarr_store
from .qpe import QPEInputError, convert_dbzh_to_rate
from .qpe_profile import QPEProfile

CONTRACT_NAME = "rainpulse.radar-analysis"
CONTRACT_VERSION = "1.2"
REQUIRED_FIELDS = {**MOSAIC_FIELDS, "RATE_QPE": np.dtype("float32")}


def build_radar_analysis_zarr_store(
    mosaic_objects: Mapping[str, bytes],
    *,
    mosaic_uri: str,
    analysis_id: UUID,
    profile: QPEProfile,
    asset_id: UUID | str,
    provenance: Mapping[str, str] | None = None,
) -> dict[str, bytes]:
    validate_radar_mosaic_zarr_store(mosaic_objects)
    input_store = MemoryStore()
    input_store.update({key: bytes(value) for key, value in mosaic_objects.items()})
    source = zarr.open_group(store=input_store, mode="r")
    _validate_identity(source, analysis_id, profile)

    rate, rate_summary = convert_dbzh_to_rate(
        source[profile.qpe.input_field][:], source["VALID_MASK"][:], profile
    )
    valid = source["VALID_MASK"][:] == 1
    quality = source["QUALITY_INDEX"][:]
    operational_eligible = bool(source.attrs.get("operational_eligible"))
    operational_reasons = list(source.attrs.get("operational_reasons", []))
    summary = {
        "analysis_id": str(analysis_id),
        "analysis_time": str(source.attrs["analysis_time"]),
        "grid_id": profile.grid_id,
        "grid_config_version": profile.grid_config_version,
        "qpe_config_version": profile.profile_version,
        "qpe_algorithm_version": profile.algorithm_version,
        "mosaic_config_version": str(source.attrs["profile_version"]),
        "mosaic_algorithm_version": str(source.attrs["mosaic_algorithm_version"]),
        "flag_definition_version": str(source.attrs["flag_definition_version"]),
        "input_mosaic_uri": mosaic_uri,
        "input_field": profile.qpe.input_field,
        "coefficient_a": profile.qpe.coefficient_a,
        "exponent_b": profile.qpe.exponent_b,
        "no_rain_below_dbz": profile.qpe.no_rain_below_dbz,
        "maximum_rate_mm_h": profile.qpe.maximum_rate_mm_h,
        "gauge_adjustment_enabled": profile.gauge_adjustment.enabled,
        "operational_eligible": operational_eligible,
        "operational_reasons": operational_reasons,
        "grid_cell_count": int(valid.size),
        "low_quality_cell_count": int(
            np.count_nonzero(source["LOW_QUALITY_MASK"][:])
        ),
        "valid_coverage_ratio": float(np.count_nonzero(valid) / valid.size),
        "mean_quality_index": (
            float(np.mean(quality[valid])) if np.any(valid) else 0.0
        ),
        **rate_summary,
    }

    output_store = MemoryStore()
    root = zarr.group(store=output_store, overwrite=True)
    attributes = dict(source.attrs)
    attributes.update(
        {
            "contract_name": CONTRACT_NAME,
            "contract_version": CONTRACT_VERSION,
            "asset_id": str(asset_id),
            "analysis_id": str(analysis_id),
            "input_mosaic_uri": mosaic_uri,
            "mosaic_config_version": str(source.attrs["profile_version"]),
            "qpe_config_version": profile.profile_version,
            "qpe_algorithm_version": profile.algorithm_version,
            "qpe_relation": "Z = a R^b",
            "qpe_coefficient_a": profile.qpe.coefficient_a,
            "qpe_exponent_b": profile.qpe.exponent_b,
            "qpe_no_rain_below_dbz": profile.qpe.no_rain_below_dbz,
            "qpe_maximum_rate_mm_h": profile.qpe.maximum_rate_mm_h,
            "gauge_adjustment_enabled": profile.gauge_adjustment.enabled,
            "operational_eligible": operational_eligible,
            "operational_reasons": operational_reasons,
            "created_at_utc": datetime.now(UTC).isoformat(),
        }
    )
    if provenance:
        attributes.update(dict(provenance))
    root.attrs.update(attributes)

    compressor = Blosc(cname="zstd", clevel=3, shuffle=Blosc.BITSHUFFLE)
    for coordinate in ("lat", "lon"):
        source_array = source[coordinate]
        target = root.create_dataset(
            coordinate,
            data=source_array[:],
            chunks=source_array.chunks,
        )
        target.attrs.update(dict(source_array.attrs))
    for name in MOSAIC_FIELDS:
        source_array = source[name]
        target = root.create_dataset(
            name,
            data=source_array[:],
            chunks=source_array.chunks,
            compressor=compressor,
            fill_value=source_array.fill_value,
        )
        target.attrs.update(dict(source_array.attrs))
    rate_array = root.create_dataset(
        "RATE_QPE",
        data=rate,
        chunks=(min(128, rate.shape[0]), min(256, rate.shape[1])),
        compressor=compressor,
        fill_value=np.nan,
    )
    rate_array.attrs.update(
        {
            "long_name": "instantaneous radar quantitative precipitation estimate",
            "units": "mm h-1",
            "valid_min": 0.0,
            "missing_value": "NaN",
        }
    )
    output_store["qpe/summary.json"] = json.dumps(
        summary, separators=(",", ":"), sort_keys=True
    ).encode()
    zarr.consolidate_metadata(output_store)
    objects = {str(key): bytes(value) for key, value in output_store.items()}
    validate_radar_analysis_zarr_store(objects)
    return objects


def validate_radar_analysis_zarr_store(
    objects: Mapping[str, bytes],
) -> dict[str, Any]:
    required_objects = {".zgroup", ".zattrs", "qpe/summary.json"}
    if not required_objects <= objects.keys():
        raise QPEInputError("RadarAnalysis Zarr is missing metadata or QPE summary")
    store = MemoryStore()
    store.update({key: bytes(value) for key, value in objects.items()})
    root = zarr.open_group(store=store, mode="r")
    if root.attrs.get("contract_name") != CONTRACT_NAME:
        raise QPEInputError("RadarAnalysis contract name is invalid")
    if root.attrs.get("contract_version") != CONTRACT_VERSION:
        raise QPEInputError("RadarAnalysis contract version is invalid")
    for name in ("input_asset_ids", "qc_pipeline_versions"):
        values = root.attrs.get(name)
        if not isinstance(values, list) or not values or not all(
            isinstance(value, str) and value for value in values
        ):
            raise QPEInputError(f"RadarAnalysis attribute {name} is invalid")
    if root.attrs.get("crs") != "EPSG:4326" or root.attrs.get("registration") != "point":
        raise QPEInputError("RadarAnalysis spatial reference is invalid")
    shape = (len(root["lat"]), len(root["lon"]))
    for name, dtype in REQUIRED_FIELDS.items():
        if name not in root or root[name].shape != shape or root[name].dtype != dtype:
            raise QPEInputError(f"RadarAnalysis field {name} has invalid shape or dtype")
    valid = root["VALID_MASK"][:] == 1
    rate = root["RATE_QPE"][:]
    if np.any(~np.isnan(rate[~valid])):
        raise QPEInputError("RadarAnalysis missing cells contain finite RATE_QPE")
    if np.any(~np.isfinite(rate[valid])) or np.any(rate[valid] < 0):
        raise QPEInputError("RadarAnalysis valid cells contain invalid RATE_QPE")
    maximum_rate = float(root.attrs.get("qpe_maximum_rate_mm_h", -1))
    if maximum_rate <= 0 or np.any(rate[valid] > maximum_rate):
        raise QPEInputError("RadarAnalysis RATE_QPE exceeds its configured maximum")
    if "DBZH_RAW" in root or "INTERFERENCE_TYPE" in root:
        raise QPEInputError("RadarAnalysis contains an unfrozen fabricated field")
    summary = json.loads(objects["qpe/summary.json"])
    valid_count = int(np.count_nonzero(valid))
    missing_count = int(np.count_nonzero(~valid))
    if (
        summary.get("analysis_id") != root.attrs.get("analysis_id")
        or summary.get("qpe_config_version") != root.attrs.get("qpe_config_version")
        or summary.get("qpe_algorithm_version")
        != root.attrs.get("qpe_algorithm_version")
        or summary.get("input_mosaic_uri") != root.attrs.get("input_mosaic_uri")
        or summary.get("valid_cell_count") != valid_count
        or summary.get("missing_cell_count") != missing_count
        or summary.get("rain_cell_count", 0) + summary.get("no_rain_cell_count", 0)
        != valid_count
    ):
        raise QPEInputError("RadarAnalysis QPE summary differs from arrays")
    return {
        "shape": shape,
        "valid_cell_count": valid_count,
        "missing_cell_count": missing_count,
        "rain_cell_count": int(summary["rain_cell_count"]),
        "no_rain_cell_count": int(summary["no_rain_cell_count"]),
        "capped_cell_count": int(summary["capped_cell_count"]),
        "mean_rate_mm_h": float(summary["mean_rate_mm_h"]),
        "maximum_observed_rate_mm_h": float(summary["maximum_observed_rate_mm_h"]),
        "operational_eligible": bool(root.attrs.get("operational_eligible")),
        "object_count": len(objects),
        "size_bytes": sum(len(value) for value in objects.values()),
    }


def _validate_identity(root: zarr.Group, analysis_id: UUID, profile: QPEProfile) -> None:
    if root.attrs.get("analysis_id") != str(analysis_id):
        raise QPEInputError("RadarMosaic analysis ID differs from the QPE request")
    expected = (
        ("contract_version", root.attrs.get("contract_version"), profile.mosaic_contract_version),
        ("grid_id", root.attrs.get("grid_id"), profile.grid_id),
        (
            "grid_config_version",
            root.attrs.get("grid_config_version"),
            profile.grid_config_version,
        ),
        (
            "flag_definition_version",
            root.attrs.get("flag_definition_version"),
            profile.flag_definition_version,
        ),
    )
    for name, actual, configured in expected:
        if actual != configured:
            raise QPEInputError(f"RadarMosaic {name} differs from the QPE profile")
