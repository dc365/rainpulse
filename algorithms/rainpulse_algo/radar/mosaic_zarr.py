from __future__ import annotations

import json
from collections.abc import Mapping
from typing import Any
from uuid import UUID

import numpy as np
import zarr
from numcodecs import Blosc
from zarr.storage import MemoryStore

from .mosaic import QI_COMPONENTS, RadarMosaicInputError, RadarMosaicResult

CONTRACT_NAME = "rainpulse.radar-mosaic"
CONTRACT_VERSION = "1.0"
REQUIRED_FIELDS = {
    "DBZH_QC": np.dtype("float32"),
    "REF_NOWCAST": np.dtype("float32"),
    "QUALITY_INDEX": np.dtype("float32"),
    **{name: np.dtype("float32") for name in QI_COMPONENTS},
    "QC_FLAGS": np.dtype("uint32"),
    "SOURCE_RADAR": np.dtype("uint16"),
    "CONTRIBUTOR_COUNT": np.dtype("uint8"),
    "SOURCE_ELEVATION": np.dtype("float32"),
    "BEAM_HEIGHT": np.dtype("float32"),
    "TERRAIN_HEIGHT": np.dtype("float32"),
    "BLOCKAGE_RATE": np.dtype("float32"),
    "DATA_AGE": np.dtype("float32"),
    "VALID_MASK": np.dtype("uint8"),
    "LOW_QUALITY_MASK": np.dtype("uint8"),
}


def build_radar_mosaic_zarr_store(
    result: RadarMosaicResult,
    *,
    asset_id: UUID | str,
    provenance: Mapping[str, str] | None = None,
) -> dict[str, bytes]:
    store = MemoryStore()
    root = zarr.group(store=store, overwrite=True)
    profile = result.profile
    root.attrs.update(
        {
            "contract_name": CONTRACT_NAME,
            "contract_version": CONTRACT_VERSION,
            "asset_id": str(asset_id),
            "analysis_time": result.analysis_time.isoformat(),
            "grid_id": result.grid.grid_id,
            "grid_config_version": result.grid.config_version,
            "coordinate_sha256": result.grid.coordinate_sha256,
            "crs": "EPSG:4326",
            "registration": "point",
            "coordinate_centre_bounds": result.grid.coordinate_centre_bounds,
            "pixel_edge_bounds": result.grid.pixel_edge_bounds,
            "longitude_interval_deg": result.grid.longitude_interval_deg,
            "latitude_interval_deg": result.grid.latitude_interval_deg,
            "profile_version": profile.profile_version,
            "mosaic_algorithm_version": profile.algorithm_version,
            "analysis_cycle_version": profile.analysis_cycle_version,
            "flag_definition_version": profile.flag_definition_version,
            "contributors": list(result.contributors),
            "radar_source_codes": result.radar_source_codes,
            "blended_source_code": profile.fusion.blended_source_code,
            "operational_eligible": result.operational_eligible,
            "operational_reasons": list(result.operational_reasons),
            "created_at_utc": result.created_at.isoformat(),
        }
    )
    if provenance:
        root.attrs.update(dict(provenance))

    compressor = Blosc(cname="zstd", clevel=3, shuffle=Blosc.BITSHUFFLE)
    latitude = root.create_dataset(
        "lat", data=result.grid.latitude, chunks=(min(512, len(result.grid.latitude)),)
    )
    latitude.attrs.update(
        {"standard_name": "latitude", "long_name": "latitude", "units": "degrees_north"}
    )
    longitude = root.create_dataset(
        "lon", data=result.grid.longitude, chunks=(min(512, len(result.grid.longitude)),)
    )
    longitude.attrs.update(
        {
            "standard_name": "longitude",
            "long_name": "longitude",
            "units": "degrees_east",
        }
    )
    for name, values in result.fields.items():
        array = root.create_dataset(
            name,
            data=values,
            chunks=(min(128, values.shape[0]), min(256, values.shape[1])),
            compressor=compressor,
            overwrite=True,
            fill_value=_fill_value(values.dtype),
        )
        array.attrs.update(_field_attributes(name))

    store["mosaic/summary.json"] = json.dumps(
        result.summary, separators=(",", ":"), sort_keys=True
    ).encode()
    zarr.consolidate_metadata(store)
    objects = {str(key): bytes(value) for key, value in store.items()}
    validate_radar_mosaic_zarr_store(objects)
    return objects


def validate_radar_mosaic_zarr_store(objects: Mapping[str, bytes]) -> dict[str, Any]:
    required_objects = {".zgroup", ".zattrs", "mosaic/summary.json"}
    if not required_objects <= objects.keys():
        raise RadarMosaicInputError("RadarMosaic Zarr is missing metadata or summary")
    store = MemoryStore()
    store.update({key: bytes(value) for key, value in objects.items()})
    root = zarr.open_group(store=store, mode="r")
    if root.attrs.get("contract_name") != CONTRACT_NAME:
        raise RadarMosaicInputError("RadarMosaic contract name is invalid")
    if root.attrs.get("contract_version") != CONTRACT_VERSION:
        raise RadarMosaicInputError("RadarMosaic contract version is invalid")
    if root.attrs.get("crs") != "EPSG:4326" or root.attrs.get("registration") != "point":
        raise RadarMosaicInputError("RadarMosaic spatial reference is invalid")
    latitude = root["lat"][:]
    longitude = root["lon"][:]
    if latitude.dtype != np.dtype("float32") or longitude.dtype != np.dtype("float32"):
        raise RadarMosaicInputError("RadarMosaic coordinates must be float32")
    if np.any(np.diff(latitude) <= 0) or np.any(np.diff(longitude) <= 0):
        raise RadarMosaicInputError("RadarMosaic coordinates must be strictly increasing")
    shape = (len(latitude), len(longitude))
    for name, dtype in REQUIRED_FIELDS.items():
        if name not in root or root[name].shape != shape or root[name].dtype != dtype:
            raise RadarMosaicInputError(
                f"RadarMosaic field {name} has invalid shape or dtype"
            )

    valid = root["VALID_MASK"][:]
    low = root["LOW_QUALITY_MASK"][:]
    source = root["SOURCE_RADAR"][:]
    contributor_count = root["CONTRIBUTOR_COUNT"][:]
    flags = root["QC_FLAGS"][:]
    if np.any((valid != 0) & (valid != 1)) or np.any((low != 0) & (low != 1)):
        raise RadarMosaicInputError("RadarMosaic masks are not binary")
    if np.any(low > valid):
        raise RadarMosaicInputError("RadarMosaic low-quality cells include missing cells")
    missing = valid == 0
    if np.any(source[missing] != 0) or np.any(contributor_count[missing] != 0):
        raise RadarMosaicInputError("RadarMosaic missing source identity is inconsistent")
    if np.any((flags[missing] & np.uint32(4096)) == 0):
        raise RadarMosaicInputError("RadarMosaic missing cells lack the MISSING flag")
    if np.any(source[~missing] == 0) or np.any(contributor_count[~missing] == 0):
        raise RadarMosaicInputError("RadarMosaic valid source identity is inconsistent")
    blended_code = np.uint16(root.attrs.get("blended_source_code"))
    if np.any(source[contributor_count > 1] != blended_code):
        raise RadarMosaicInputError("RadarMosaic blended source identity is inconsistent")

    unavailable_qi = {
        "QI_METEO",
        "QI_ATTENUATION",
        "QI_INTERFERENCE",
        "QI_CALIBRATION",
        "QI_RANGE",
    }
    floating_fields = [
        name for name, dtype in REQUIRED_FIELDS.items() if dtype == np.dtype("float32")
    ]
    for name in floating_fields:
        values = root[name][:]
        if np.any(~np.isnan(values[missing])):
            raise RadarMosaicInputError(f"RadarMosaic missing cells contain finite {name}")
        if name not in unavailable_qi and np.any(~np.isfinite(values[~missing])):
            raise RadarMosaicInputError(f"RadarMosaic valid cells contain missing {name}")
    for name in ("QUALITY_INDEX", *QI_COMPONENTS, "BLOCKAGE_RATE"):
        finite = root[name][:][~missing]
        finite = finite[np.isfinite(finite)]
        if finite.size and (finite.min() < 0 or finite.max() > 1):
            raise RadarMosaicInputError(f"RadarMosaic field {name} is outside [0, 1]")
    if np.any(root["DATA_AGE"][:][~missing] < 0):
        raise RadarMosaicInputError("RadarMosaic data age is negative")

    summary = json.loads(objects["mosaic/summary.json"])
    valid_count = int(np.count_nonzero(valid))
    if summary.get("valid_cell_count") != valid_count:
        raise RadarMosaicInputError("RadarMosaic summary valid count differs from arrays")
    return {
        "shape": shape,
        "valid_cell_count": valid_count,
        "missing_cell_count": int(np.count_nonzero(missing)),
        "blended_cell_count": int(np.count_nonzero(contributor_count > 1)),
        "mean_quality_index": (
            float(np.mean(root["QUALITY_INDEX"][:][~missing])) if valid_count else 0.0
        ),
        "operational_eligible": bool(root.attrs.get("operational_eligible")),
        "object_count": len(objects),
        "size_bytes": sum(len(value) for value in objects.values()),
    }


def _fill_value(dtype: np.dtype[Any]) -> float | int:
    return np.nan if np.issubdtype(dtype, np.floating) else 0


def _field_attributes(name: str) -> dict[str, Any]:
    if name == "SOURCE_ELEVATION":
        return {"units": "degree", "missing_value": "NaN"}
    if name in {"BEAM_HEIGHT", "TERRAIN_HEIGHT"}:
        return {"units": "m", "missing_value": "NaN"}
    if name == "DATA_AGE":
        return {"units": "minute", "missing_value": "NaN"}
    if name in {"DBZH_QC", "REF_NOWCAST"}:
        return {"units": "dBZ", "missing_value": "NaN"}
    if name in {"QUALITY_INDEX", *QI_COMPONENTS, "BLOCKAGE_RATE"}:
        return {"units": "1", "valid_range": [0.0, 1.0], "missing_value": "NaN"}
    return {"units": "1"}

