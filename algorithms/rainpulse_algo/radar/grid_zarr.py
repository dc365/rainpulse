from __future__ import annotations

import json
from collections.abc import Mapping
from typing import Any
from uuid import UUID

import numpy as np
import zarr
from numcodecs import Blosc
from zarr.storage import MemoryStore

from .hybrid import RadarGridInputError, RadarGridResult

CONTRACT_NAME = "rainpulse.radar-grid"
CONTRACT_VERSION = "1.2"
REQUIRED_FIELDS = {
    "DBZH_QC": np.dtype("float32"),
    "QUALITY_INDEX": np.dtype("float32"),
    "QI_BLOCKAGE": np.dtype("float32"),
    "QI_BEAM_HEIGHT": np.dtype("float32"),
    "QC_FLAGS": np.dtype("uint32"),
    "SOURCE_SWEEP": np.dtype("int16"),
    "SOURCE_ELEVATION": np.dtype("float32"),
    "BEAM_HEIGHT": np.dtype("float32"),
    "TERRAIN_HEIGHT": np.dtype("float32"),
    "BLOCKAGE_RATE": np.dtype("float32"),
    "DATA_AGE": np.dtype("float32"),
    "VALID_MASK": np.dtype("uint8"),
    "LOW_QUALITY_MASK": np.dtype("uint8"),
}


def build_radar_grid_zarr_store(
    result: RadarGridResult,
    *,
    asset_id: UUID | str,
    qc_volume_uri: str,
    provenance: Mapping[str, str] | None = None,
) -> dict[str, bytes]:
    store = MemoryStore()
    root = zarr.group(store=store, overwrite=True)
    profile = result.profile
    source = result.source_attributes
    root.attrs.update(
        {
            "contract_name": CONTRACT_NAME,
            "contract_version": CONTRACT_VERSION,
            "asset_id": str(asset_id),
            "scan_id": source["scan_id"],
            "radar_id": source["radar_id"],
            "qc_asset_id": source["asset_id"],
            "qc_volume_uri": qc_volume_uri,
            "normalized_volume_uri": source["normalized_volume_uri"],
            "radar_config_version": source["radar_config_version"],
            "qc_profile": source["qc_profile"],
            "qc_pipeline_version": source["qc_pipeline_version"],
            "flag_definition_version": source["flag_definition_version"],
            "grid_id": result.grid.grid_id,
            "grid_config_version": result.grid.config_version,
            "coordinate_sha256": result.grid.coordinate_sha256,
            "crs": "EPSG:4326",
            "registration": "point",
            "coordinate_centre_bounds": result.grid.coordinate_centre_bounds,
            "pixel_edge_bounds": result.grid.pixel_edge_bounds,
            "longitude_interval_deg": result.grid.longitude_interval_deg,
            "latitude_interval_deg": result.grid.latitude_interval_deg,
            "grid_metric_version": result.grid.metric().version,
            "profile_version": profile.profile_version,
            "hybrid_scan_version": profile.algorithm_version,
            "ancillary_config_version": profile.ancillary_config_version,
            "dem_asset_version": profile.dem.asset_version,
            "dem_horizontal_crs": profile.dem.horizontal_crs,
            "dem_vertical_crs": profile.dem.vertical_crs,
            "dem_sampling": profile.dem.sampling,
            "beam_effective_earth_radius_factor": (
                profile.beam_geometry.effective_earth_radius_factor
            ),
            "beam_earth_radius_m": profile.beam_geometry.earth_radius_m,
            "vertical_datum_status": result.vertical_datum_status,
            "operational_eligible": result.operational_eligible,
            "operational_reasons": list(result.operational_reasons),
            "volume_start_time_utc": source["volume_start_time_utc"],
            "volume_end_time_utc": source["volume_end_time_utc"],
            "created_at_utc": result.created_at.isoformat(),
            "polar_blockage_diagnostics": "polar/{sweep}/",
        }
    )
    if provenance:
        root.attrs.update(dict(provenance))

    compressor = Blosc(cname="zstd", clevel=3, shuffle=Blosc.BITSHUFFLE)
    latitude = _array(root, "lat", result.grid.latitude, None)
    latitude.attrs.update(
        {"standard_name": "latitude", "long_name": "latitude", "units": "degrees_north"}
    )
    longitude = _array(root, "lon", result.grid.longitude, None)
    longitude.attrs.update(
        {"standard_name": "longitude", "long_name": "longitude", "units": "degrees_east"}
    )
    for name, values in result.fields.items():
        array = root.create_dataset(
            name,
            data=values,
            chunks=(min(128, values.shape[0]), min(256, values.shape[1])),
            compressor=compressor,
            overwrite=True,
            fill_value=_fill_value(name, values.dtype),
        )
        array.attrs.update(_field_attributes(name))

    polar_root = root.create_group("polar")
    for diagnostic in result.polar_diagnostics:
        group = polar_root.create_group(diagnostic.name)
        group.attrs.update(
            {
                "source_sweep": int(diagnostic.name.removeprefix("sweep_")),
                "nominal_elevation_deg": diagnostic.nominal_elevation_deg,
                "support_semantics": "ray_gate_paths_required_by_registered_grid",
            }
        )
        for name, values, units in (
            ("azimuth", diagnostic.azimuth_deg, "degree"),
            ("elevation", diagnostic.elevation_deg, "degree"),
            ("range", diagnostic.range_m, "m"),
        ):
            array = _array(group, name, values, compressor)
            array.attrs["units"] = units
        for name, values in (
            ("PARTIAL_BLOCKAGE", diagnostic.blockage.partial),
            ("BLOCKAGE_RATE", diagnostic.blockage.cumulative),
            ("BEAM_HEIGHT", diagnostic.blockage.beam_height_m),
            ("TERRAIN_HEIGHT", diagnostic.blockage.terrain_height_m),
            ("SUPPORT_MASK", diagnostic.blockage.support_mask),
        ):
            array = group.create_dataset(
                name,
                data=values,
                chunks=(min(64, values.shape[0]), min(512, values.shape[1])),
                compressor=compressor,
                overwrite=True,
                fill_value=_fill_value(name, values.dtype),
            )
            array.attrs.update(_field_attributes(name))

    store["grid/summary.json"] = json.dumps(
        result.summary,
        separators=(",", ":"),
        sort_keys=True,
    ).encode()
    zarr.consolidate_metadata(store)
    objects = {str(key): bytes(value) for key, value in store.items()}
    validate_radar_grid_zarr_store(objects)
    return objects


def validate_radar_grid_zarr_store(objects: Mapping[str, bytes]) -> dict[str, Any]:
    if ".zgroup" not in objects or ".zattrs" not in objects or "grid/summary.json" not in objects:
        raise RadarGridInputError("RadarGrid Zarr is missing metadata or summary")
    store = MemoryStore()
    store.update({key: bytes(value) for key, value in objects.items()})
    root = zarr.open_group(store=store, mode="r")
    if root.attrs.get("contract_name") != CONTRACT_NAME:
        raise RadarGridInputError("RadarGrid contract name is invalid")
    if root.attrs.get("contract_version") != CONTRACT_VERSION:
        raise RadarGridInputError("RadarGrid contract version is invalid")
    if root.attrs.get("crs") != "EPSG:4326" or root.attrs.get("registration") != "point":
        raise RadarGridInputError("RadarGrid spatial reference is invalid")
    latitude = root["lat"][:]
    longitude = root["lon"][:]
    if latitude.dtype != np.dtype("float32") or longitude.dtype != np.dtype("float32"):
        raise RadarGridInputError("RadarGrid coordinates must be float32")
    if np.any(np.diff(latitude) <= 0) or np.any(np.diff(longitude) <= 0):
        raise RadarGridInputError("RadarGrid coordinates must be strictly increasing")
    shape = (len(latitude), len(longitude))
    for name, dtype in REQUIRED_FIELDS.items():
        if name not in root or root[name].shape != shape or root[name].dtype != dtype:
            raise RadarGridInputError(f"RadarGrid field {name} has invalid shape or dtype")
    valid = root["VALID_MASK"][:]
    low_quality = root["LOW_QUALITY_MASK"][:]
    flags = root["QC_FLAGS"][:]
    source_sweep = root["SOURCE_SWEEP"][:]
    if np.any((valid != 0) & (valid != 1)) or np.any((low_quality != 0) & (low_quality != 1)):
        raise RadarGridInputError("RadarGrid masks are not binary")
    if np.any(low_quality > valid):
        raise RadarGridInputError("RadarGrid low-quality cells include missing cells")
    missing = valid == 0
    if np.any(source_sweep[missing] != -1) or np.any((flags[missing] & np.uint32(4096)) == 0):
        raise RadarGridInputError("RadarGrid missing-state identity is inconsistent")
    floating_fields = [
        name for name, dtype in REQUIRED_FIELDS.items() if dtype == np.dtype("float32")
    ]
    for name in floating_fields:
        values = root[name][:]
        if np.any(~np.isnan(values[missing])):
            raise RadarGridInputError(f"RadarGrid missing cells contain finite {name}")
        if np.any(~np.isfinite(values[~missing])):
            raise RadarGridInputError(f"RadarGrid valid cells contain missing {name}")
    for name in ("QUALITY_INDEX", "QI_BLOCKAGE", "QI_BEAM_HEIGHT", "BLOCKAGE_RATE"):
        finite = root[name][:][~missing]
        if finite.size and (finite.min() < 0 or finite.max() > 1):
            raise RadarGridInputError(f"RadarGrid field {name} is outside [0, 1]")
    if np.any(root["DATA_AGE"][:][~missing] < 0):
        raise RadarGridInputError("RadarGrid data age is negative")
    if "polar" not in root or not list(root["polar"].group_keys()):
        raise RadarGridInputError("RadarGrid has no polar blockage diagnostics")
    for name in root["polar"].group_keys():
        group = root[f"polar/{name}"]
        polar_shape = (len(group["azimuth"]), len(group["range"]))
        for field in (
            "PARTIAL_BLOCKAGE",
            "BLOCKAGE_RATE",
            "BEAM_HEIGHT",
            "TERRAIN_HEIGHT",
            "SUPPORT_MASK",
        ):
            if group[field].shape != polar_shape:
                raise RadarGridInputError(f"polar diagnostic {name}/{field} has invalid shape")
        cumulative = group["BLOCKAGE_RATE"][:]
        adjacent = np.isfinite(cumulative[:, 1:]) & np.isfinite(cumulative[:, :-1])
        if np.any(np.diff(cumulative, axis=1)[adjacent] < -1e-6):
            raise RadarGridInputError(f"polar blockage is not cumulative for {name}")
    summary = json.loads(objects["grid/summary.json"])
    valid_count = int(np.count_nonzero(valid))
    if summary.get("valid_cell_count") != valid_count:
        raise RadarGridInputError("RadarGrid summary valid count differs from arrays")
    return {
        "shape": shape,
        "valid_cell_count": valid_count,
        "missing_cell_count": int(np.count_nonzero(missing)),
        "mean_quality_index": (
            float(np.mean(root["QUALITY_INDEX"][:][~missing])) if valid_count else 0.0
        ),
        "operational_eligible": bool(root.attrs.get("operational_eligible")),
        "object_count": len(objects),
        "size_bytes": sum(len(value) for value in objects.values()),
    }


def _array(
    group: zarr.Group,
    name: str,
    values: np.ndarray,
    compressor: Blosc | None,
) -> zarr.Array:
    return group.create_dataset(
        name,
        data=values,
        chunks=(min(512, len(values)),),
        compressor=compressor,
        overwrite=True,
    )


def _fill_value(name: str, dtype: np.dtype[Any]) -> float | int:
    if np.issubdtype(dtype, np.floating):
        return np.nan
    if name == "SOURCE_SWEEP":
        return -1
    return 0


def _field_attributes(name: str) -> dict[str, Any]:
    if name in {
        "QUALITY_INDEX",
        "QI_BLOCKAGE",
        "QI_BEAM_HEIGHT",
        "BLOCKAGE_RATE",
        "PARTIAL_BLOCKAGE",
    }:
        return {"units": "1", "valid_range": [0.0, 1.0], "missing_value": "NaN"}
    if name in {"VALID_MASK", "LOW_QUALITY_MASK", "SUPPORT_MASK"}:
        return {"units": "1", "valid_values": [0, 1]}
    if name == "QC_FLAGS":
        return {"units": "1", "storage_dtype": "uint32"}
    if name == "DBZH_QC":
        return {"units": "dBZ", "missing_value": "NaN"}
    if name == "SOURCE_ELEVATION":
        return {"units": "degree", "missing_value": "NaN"}
    if name in {"BEAM_HEIGHT", "TERRAIN_HEIGHT"}:
        return {"units": "m", "missing_value": "NaN"}
    if name == "DATA_AGE":
        return {"units": "minute", "missing_value": "NaN"}
    return {"units": "1"}
