from __future__ import annotations

from collections.abc import Mapping
from typing import Any
from uuid import UUID

import numpy as np
import zarr
from numcodecs import Blosc
from zarr.storage import MemoryStore

from .qc import QCInputError, QCResult

CONTRACT_NAME = "rainpulse.qc-radar-volume"
CONTRACT_VERSION = "1.0"
GEOMETRY_ENCODING = "sweep_groups_v1"
REQUIRED_FIELDS = {
    "DBZH_RAW": np.dtype("float32"),
    "DBZH_QC": np.dtype("float32"),
    "QUALITY_INDEX": np.dtype("float32"),
    "QI_METEO": np.dtype("float32"),
    "QI_BLOCKAGE": np.dtype("float32"),
    "QI_BEAM_HEIGHT": np.dtype("float32"),
    "QI_ATTENUATION": np.dtype("float32"),
    "QI_INTERFERENCE": np.dtype("float32"),
    "QI_TIME": np.dtype("float32"),
    "QI_CALIBRATION": np.dtype("float32"),
    "QI_RANGE": np.dtype("float32"),
    "QC_FLAGS": np.dtype("uint32"),
    "VALID_MASK": np.dtype("uint8"),
    "LOW_QUALITY_MASK": np.dtype("uint8"),
    "P_METEO": np.dtype("float32"),
    "P_AP": np.dtype("float32"),
    "P_SEA_CLUTTER": np.dtype("float32"),
    "P_RADIAL_INTERFERENCE": np.dtype("float32"),
}


def build_qc_zarr_store(
    normalized_objects: Mapping[str, bytes],
    result: QCResult,
    *,
    asset_id: UUID | str,
    normalized_volume_uri: str,
    provenance: Mapping[str, str] | None = None,
) -> dict[str, bytes]:
    source_store = MemoryStore()
    source_store.update({key: bytes(value) for key, value in normalized_objects.items()})
    source = zarr.open_group(store=source_store, mode="r")
    output_store = MemoryStore()
    root = zarr.group(store=output_store, overwrite=True)
    root.attrs.update(
        {
            "contract_name": CONTRACT_NAME,
            "contract_version": CONTRACT_VERSION,
            "geometry_encoding": GEOMETRY_ENCODING,
            "asset_id": str(asset_id),
            "scan_id": source.attrs.get("scan_id"),
            "radar_id": source.attrs["radar_id"],
            "normalized_volume_uri": normalized_volume_uri,
            "radar_config_version": source.attrs["radar_config_version"],
            "qc_profile": result.profile.profile_version,
            "qc_pipeline_version": result.profile.pipeline_version,
            "flag_definition_version": result.profile.flag_definition_version,
            "dem_asset_version": None,
            "clutter_map_version": result.profile.static_ground_clutter.asset_version,
            "coastline_asset_version": result.profile.sea_ap.asset_version,
            "created_at_utc": result.created_at.isoformat(),
            "source_health_state": result.health["health"],
            "module_provenance": [record.value() for record in result.modules],
            "quality_index_components": list(result.profile.quality_index.components),
            "unavailable_component_policy": (
                result.profile.quality_index.unavailable_component_policy
            ),
        }
    )
    if provenance:
        root.attrs.update(dict(provenance))

    compressor = Blosc(cname="zstd", clevel=3, shuffle=Blosc.BITSHUFFLE)
    for name in ("sweep_number", "sweep_start_ray_index", "sweep_end_ray_index"):
        _array(root, name, source[name][:], None)
    for qc_sweep in result.sweeps:
        source_group = source[qc_sweep.name]
        group = root.create_group(qc_sweep.name)
        group.attrs.update(dict(source_group.attrs))
        for coordinate in (
            "azimuth",
            "elevation",
            "ray_time",
            "horizontal_noise",
            "vertical_noise",
            "range",
        ):
            array = _array(group, coordinate, source_group[coordinate][:], compressor)
            array.attrs.update(dict(source_group[coordinate].attrs))
        fields = {
            "DBZH_RAW": qc_sweep.dbzh_raw,
            "DBZH_QC": qc_sweep.dbzh_qc,
            **qc_sweep.optional_qc_fields,
            "QUALITY_INDEX": qc_sweep.quality_index,
            **qc_sweep.qi_components,
            "QC_FLAGS": qc_sweep.qc_flags,
            "VALID_MASK": qc_sweep.valid_mask,
            "LOW_QUALITY_MASK": qc_sweep.low_quality_mask,
            "P_METEO": qc_sweep.p_meteo,
            "P_AP": qc_sweep.p_ap,
            "P_SEA_CLUTTER": qc_sweep.p_sea_clutter,
            "P_RADIAL_INTERFERENCE": qc_sweep.p_radial_interference,
        }
        for name, values in fields.items():
            array = group.create_dataset(
                name,
                data=values,
                chunks=(min(64, values.shape[0]), min(512, values.shape[1])),
                compressor=compressor,
                overwrite=True,
                fill_value=_fill_value(values.dtype),
            )
            array.attrs.update(_field_attributes(name))

    output_store["qc/summary.json"] = result.summary_bytes()
    zarr.consolidate_metadata(output_store)
    objects = {str(key): bytes(value) for key, value in output_store.items()}
    validate_qc_zarr_store(objects)
    return objects


def validate_qc_zarr_store(objects: Mapping[str, bytes]) -> dict[str, Any]:
    if ".zgroup" not in objects or ".zattrs" not in objects or "qc/summary.json" not in objects:
        raise QCInputError("QC Zarr store is missing root metadata or summary")
    store = MemoryStore()
    store.update({key: bytes(value) for key, value in objects.items()})
    root = zarr.open_group(store=store, mode="r")
    if root.attrs.get("contract_name") != CONTRACT_NAME:
        raise QCInputError("QC Zarr contract name is invalid")
    if root.attrs.get("geometry_encoding") != GEOMETRY_ENCODING:
        raise QCInputError("QC Zarr geometry encoding is invalid")
    modules = root.attrs.get("module_provenance")
    if not isinstance(modules, list) or any(
        item.get("status") not in {"applied", "skipped", "failed"} for item in modules
    ):
        raise QCInputError("QC module provenance is invalid")

    sweep_numbers = root["sweep_number"][:]
    starts = root["sweep_start_ray_index"][:]
    ends = root["sweep_end_ray_index"][:]
    if len(sweep_numbers) == 0 or not (len(sweep_numbers) == len(starts) == len(ends)):
        raise QCInputError("QC sweep index arrays are inconsistent")
    if np.any(ends < starts):
        raise QCInputError("QC sweep boundaries are invalid")

    valid_total = 0
    missing_total = 0
    quality_values: list[np.ndarray] = []
    for sweep_number in sweep_numbers:
        group = root[f"sweep_{int(sweep_number):03d}"]
        shape = (len(group["azimuth"]), len(group["range"]))
        for name, dtype in REQUIRED_FIELDS.items():
            if name not in group or group[name].shape != shape or group[name].dtype != dtype:
                raise QCInputError(f"QC field {name} has invalid shape or dtype")
        valid = group["VALID_MASK"][:]
        low_quality = group["LOW_QUALITY_MASK"][:]
        flags = group["QC_FLAGS"][:]
        dbzh_qc = group["DBZH_QC"][:]
        if np.any((valid != 0) & (valid != 1)) or np.any((low_quality != 0) & (low_quality != 1)):
            raise QCInputError("QC masks are not binary")
        if np.any(low_quality > valid):
            raise QCInputError("low-quality mask includes invalid gates")
        missing = valid == 0
        if np.any(~np.isnan(dbzh_qc[missing])) or np.any((flags[missing] & np.uint32(4096)) == 0):
            raise QCInputError("QC missing-state semantics are inconsistent")
        for name in (
            "QUALITY_INDEX",
            "QI_METEO",
            "QI_BLOCKAGE",
            "QI_BEAM_HEIGHT",
            "QI_ATTENUATION",
            "QI_INTERFERENCE",
            "QI_TIME",
            "QI_CALIBRATION",
            "QI_RANGE",
            "P_METEO",
            "P_AP",
            "P_SEA_CLUTTER",
            "P_RADIAL_INTERFERENCE",
        ):
            values = group[name][:]
            finite = values[np.isfinite(values)]
            if finite.size and (finite.min() < 0 or finite.max() > 1):
                raise QCInputError(f"QC probability or quality field {name} is outside [0, 1]")
        valid_total += int(np.count_nonzero(valid))
        missing_total += int(np.count_nonzero(missing))
        quality = group["QUALITY_INDEX"][:]
        quality_values.append(quality[np.isfinite(quality)])
    finite_quality = np.concatenate(quality_values)
    return {
        "sweep_count": int(len(sweep_numbers)),
        "ray_count": int(ends[-1] + 1),
        "valid_gate_count": valid_total,
        "missing_gate_count": missing_total,
        "mean_quality_index": float(finite_quality.mean()) if finite_quality.size else 0.0,
        "object_count": len(objects),
        "size_bytes": sum(len(value) for value in objects.values()),
    }


def _array(
    group: zarr.Group,
    name: str,
    values: np.ndarray,
    compressor: Blosc | None,
) -> zarr.Array:
    chunks = (min(512, len(values)),)
    return group.create_dataset(
        name,
        data=values,
        chunks=chunks,
        compressor=compressor,
        overwrite=True,
    )


def _fill_value(dtype: np.dtype[Any]) -> float | int:
    return np.nan if np.issubdtype(dtype, np.floating) else 0


def _field_attributes(name: str) -> dict[str, Any]:
    if name == "QC_FLAGS":
        return {"units": "1", "storage_dtype": "uint32"}
    if name.endswith("_MASK"):
        return {"units": "1", "valid_values": [0, 1]}
    if name.startswith(("QI_", "P_")) or name == "QUALITY_INDEX":
        return {"units": "1", "valid_range": [0.0, 1.0], "missing_value": "NaN"}
    if name.startswith("DBZH"):
        return {"units": "dBZ", "missing_value": "NaN"}
    if name.startswith("ZDR"):
        return {"units": "dB", "missing_value": "NaN"}
    if name.startswith("PHIDP"):
        return {"units": "degree", "missing_value": "NaN"}
    if name.startswith("VR"):
        return {"units": "m s-1", "missing_value": "NaN"}
    return {"missing_value": "NaN"}
