from __future__ import annotations

import os
from collections.abc import Mapping
from dataclasses import dataclass
from typing import Any, Literal
from uuid import UUID

import numpy as np
import zarr
from numcodecs import Blosc
from zarr.storage import MemoryStore

from .attenuation import (
    ATTENUATION_CORRECTION_SHADOW_FIELD,
    ATTENUATION_SHADOW_AVAILABLE_MASK_FIELDS,
    ATTENUATION_SHADOW_OUTPUT_DTYPES,
    ATTENUATION_SHADOW_SEGMENT_INDEX_FIELD,
    SPECIFIC_ATTENUATION_SHADOW_FIELD,
)
from .phase_processing import (
    KDP_SHADOW_UNCERTAINTY_FIELD,
    PHASE_PROCESSING_AVAILABLE_MASK_FIELDS,
    PHASE_PROCESSING_OUTPUT_DTYPES,
    PHIDP_SHADOW_SEGMENT_INDEX_FIELD,
)
from .qc import QCInputError, QCResult
from .qc_geometry import (
    CROSS_RADAR_TRUSTED_SUPPORT_FIELD,
    QC_GEOMETRY_AVAILABLE_MASK_FIELDS,
    QC_GEOMETRY_OUTPUT_DTYPES,
    VERTICAL_HEIGHT_DIFFERENCE_M_FIELD,
)
from .qc_texture import (
    TEXTURE_AVAILABLE_MASK_FIELDS,
    TEXTURE_OUTPUT_DTYPES,
    TEXTURE_SUPPORT_RATE_FIELDS,
)

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

QC_ZARR_LAYOUT_64X512 = "64x512"
QC_ZARR_LAYOUT_128X1024 = "128x1024"
QC_ZARR_LAYOUT_FULL_RAY_RANGE = "full-ray-range"
SUPPORTED_QC_ZARR_LAYOUTS = (
    QC_ZARR_LAYOUT_64X512,
    QC_ZARR_LAYOUT_128X1024,
    QC_ZARR_LAYOUT_FULL_RAY_RANGE,
)
DEFAULT_QC_ZARR_LAYOUT = QC_ZARR_LAYOUT_64X512


@dataclass(frozen=True)
class QCZarrWriteSettings:
    layout: Literal["64x512", "128x1024", "full-ray-range"] = DEFAULT_QC_ZARR_LAYOUT
    write_empty_chunks: bool = False


def build_qc_zarr_store(
    normalized_objects: Mapping[str, bytes],
    result: QCResult,
    *,
    asset_id: UUID | str,
    normalized_volume_uri: str,
    provenance: Mapping[str, str] | None = None,
    write_settings: QCZarrWriteSettings | None = None,
) -> dict[str, bytes]:
    objects, _ = build_validated_qc_zarr_store(
        normalized_objects,
        result,
        asset_id=asset_id,
        normalized_volume_uri=normalized_volume_uri,
        provenance=provenance,
        write_settings=write_settings,
    )
    return objects


def build_validated_qc_zarr_store(
    normalized_objects: Mapping[str, bytes],
    result: QCResult,
    *,
    asset_id: UUID | str,
    normalized_volume_uri: str,
    provenance: Mapping[str, str] | None = None,
    write_settings: QCZarrWriteSettings | None = None,
) -> tuple[dict[str, bytes], dict[str, Any]]:
    objects = _build_qc_zarr_store_objects(
        normalized_objects,
        result,
        asset_id=asset_id,
        normalized_volume_uri=normalized_volume_uri,
        provenance=provenance,
        write_settings=write_settings,
    )
    return objects, validate_qc_zarr_store(objects)


def _build_qc_zarr_store_objects(
    normalized_objects: Mapping[str, bytes],
    result: QCResult,
    *,
    asset_id: UUID | str,
    normalized_volume_uri: str,
    provenance: Mapping[str, str] | None = None,
    write_settings: QCZarrWriteSettings | None = None,
) -> dict[str, bytes]:
    settings = _coerce_write_settings(write_settings)
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
            "input_asset_ids": [str(source.attrs["asset_id"])],
            "scan_id": source.attrs.get("scan_id"),
            "radar_id": source.attrs["radar_id"],
            "normalized_volume_uri": normalized_volume_uri,
            "radar_config_version": source.attrs["radar_config_version"],
            "qc_profile": result.profile.profile_version,
            "qc_pipeline_version": result.profile.pipeline_version,
            "decision_version": result.profile.decision_version,
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
            "field_chunk_layout": settings.layout,
            "write_empty_chunks": settings.write_empty_chunks,
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
        if result.profile.dual_pol_fuzzy.enabled:
            fields["P_METEO_DUAL_POL"] = qc_sweep.p_meteo_dual_pol
        if result.profile.vertical_consistency.enabled:
            fields["P_VERTICAL_CONSISTENCY"] = qc_sweep.p_vertical_consistency
        if result.profile.radial_interference.morphology.enabled:
            fields["INTERFERENCE_TYPE"] = qc_sweep.interference_type
        for name, values in fields.items():
            array = group.create_dataset(
                name,
                data=values,
                chunks=_field_chunks(values, settings.layout),
                compressor=compressor,
                overwrite=True,
                fill_value=_fill_value(values.dtype),
                write_empty_chunks=settings.write_empty_chunks,
            )
            array.attrs.update(_field_attributes(name))

    output_store["qc/summary.json"] = result.summary_bytes()
    zarr.consolidate_metadata(output_store)
    return {str(key): bytes(value) for key, value in output_store.items()}


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
    layout = root.attrs.get("field_chunk_layout", DEFAULT_QC_ZARR_LAYOUT)
    if layout not in SUPPORTED_QC_ZARR_LAYOUTS:
        raise QCInputError("QC Zarr field chunk layout is invalid")
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
    quality_sum = 0.0
    quality_count = 0
    field_chunk_shape: tuple[int, int] | None = None
    for sweep_number in sweep_numbers:
        group = root[f"sweep_{int(sweep_number):03d}"]
        shape = (len(group["azimuth"]), len(group["range"]))
        for name, dtype in REQUIRED_FIELDS.items():
            if name not in group or group[name].shape != shape or group[name].dtype != dtype:
                raise QCInputError(f"QC field {name} has invalid shape or dtype")
        if field_chunk_shape is None:
            field_chunk_shape = tuple(int(item) for item in group["DBZH_QC"].chunks)
        _validate_texture_fields(group, shape)
        _validate_geometry_fields(group, shape)
        _validate_phase_processing_fields(group, shape)
        _validate_attenuation_fields(group, shape)
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
            "P_METEO_DUAL_POL",
            "P_VERTICAL_CONSISTENCY",
        ):
            if name not in group:
                continue
            values = group[name][:]
            finite = values[np.isfinite(values)]
            if finite.size and (finite.min() < 0 or finite.max() > 1):
                raise QCInputError(f"QC probability or quality field {name} is outside [0, 1]")
        if "INTERFERENCE_TYPE" in group:
            values = group["INTERFERENCE_TYPE"][:]
            if values.shape != shape or values.dtype != np.dtype("uint8"):
                raise QCInputError("QC interference type has invalid shape or dtype")
            if np.any(values > 5):
                raise QCInputError("QC interference type contains an unknown code")
        valid_total += int(np.count_nonzero(valid))
        missing_total += int(np.count_nonzero(missing))
        quality = group["QUALITY_INDEX"][:]
        finite_quality = quality[np.isfinite(quality)]
        quality_sum += float(np.sum(finite_quality))
        quality_count += int(finite_quality.size)
    return {
        "sweep_count": int(len(sweep_numbers)),
        "ray_count": int(ends[-1] + 1),
        "valid_gate_count": valid_total,
        "missing_gate_count": missing_total,
        "mean_quality_index": quality_sum / quality_count if quality_count else 0.0,
        "object_count": len(objects),
        "size_bytes": sum(len(value) for value in objects.values()),
        "field_chunk_layout": str(layout),
        "field_chunk_shape": list(field_chunk_shape or (0, 0)),
        "write_empty_chunks": bool(root.attrs.get("write_empty_chunks", False)),
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


def default_qc_zarr_write_settings() -> QCZarrWriteSettings:
    layout = os.getenv("RAINPULSE_RADAR_QC_ZARR_LAYOUT", DEFAULT_QC_ZARR_LAYOUT)
    if layout not in SUPPORTED_QC_ZARR_LAYOUTS:
        raise QCInputError(f"unsupported QC Zarr chunk layout {layout!r}")
    write_empty_chunks = _environment_flag(
        "RAINPULSE_RADAR_QC_ZARR_WRITE_EMPTY_CHUNKS",
        default=False,
    )
    return QCZarrWriteSettings(
        layout=layout,
        write_empty_chunks=write_empty_chunks,
    )


def _coerce_write_settings(write_settings: QCZarrWriteSettings | None) -> QCZarrWriteSettings:
    return write_settings or default_qc_zarr_write_settings()


def _field_chunks(values: np.ndarray, layout: str) -> tuple[int, int]:
    if values.ndim != 2:
        raise QCInputError("QC fields must be 2-D to determine chunk layout")
    if layout == QC_ZARR_LAYOUT_64X512:
        return min(64, values.shape[0]), min(512, values.shape[1])
    if layout == QC_ZARR_LAYOUT_128X1024:
        return min(128, values.shape[0]), min(1024, values.shape[1])
    if layout == QC_ZARR_LAYOUT_FULL_RAY_RANGE:
        return 1, values.shape[1]
    raise QCInputError(f"unsupported QC Zarr chunk layout {layout!r}")


def _environment_flag(name: str, *, default: bool) -> bool:
    value = os.getenv(name)
    if value is None:
        return default
    normalized = value.strip().lower()
    if normalized in {"1", "true", "yes", "on"}:
        return True
    if normalized in {"0", "false", "no", "off"}:
        return False
    raise QCInputError(f"{name} must be a boolean flag")


def _field_attributes(name: str) -> dict[str, Any]:
    if name == "QC_FLAGS":
        return {"units": "1", "storage_dtype": "uint32"}
    if name.endswith("_MASK"):
        return {"units": "1", "valid_values": [0, 1]}
    if name in {PHIDP_SHADOW_SEGMENT_INDEX_FIELD, ATTENUATION_SHADOW_SEGMENT_INDEX_FIELD}:
        return {"units": "1", "missing_value": -1, "minimum_value": -1}
    if name == VERTICAL_HEIGHT_DIFFERENCE_M_FIELD:
        return {"units": "m", "missing_value": "NaN", "minimum_value": 0.0}
    if name == SPECIFIC_ATTENUATION_SHADOW_FIELD:
        return {"units": "dB km-1", "missing_value": "NaN", "minimum_value": 0.0}
    if name == ATTENUATION_CORRECTION_SHADOW_FIELD:
        return {"units": "dB", "missing_value": "NaN", "minimum_value": 0.0}
    if name in TEXTURE_SUPPORT_RATE_FIELDS or name == "PHIDP_CIRCULAR_VARIANCE":
        return {"units": "1", "valid_range": [0.0, 1.0], "missing_value": "NaN"}
    if name.endswith("_TEXTURE"):
        if name.startswith("DBZH"):
            return {"units": "dBZ", "missing_value": "NaN", "minimum_value": 0.0}
        if name.startswith("ZDR"):
            return {"units": "dB", "missing_value": "NaN", "minimum_value": 0.0}
        if name.startswith("RHOHV"):
            return {"units": "1", "missing_value": "NaN", "minimum_value": 0.0}
    if name == "INTERFERENCE_TYPE":
        return {
            "units": "1",
            "codes": {
                "0": "none",
                "1": "narrow",
                "2": "broad",
                "3": "intermittent",
                "4": "short_range",
                "5": "reverse",
            },
        }
    if name.startswith(("QI_", "P_")) or name == "QUALITY_INDEX":
        return {"units": "1", "valid_range": [0.0, 1.0], "missing_value": "NaN"}
    if name.startswith("KDP"):
        return {"units": "degree km-1", "missing_value": "NaN"}
    if name.startswith("DBZH"):
        return {"units": "dBZ", "missing_value": "NaN"}
    if name.startswith("ZDR"):
        return {"units": "dB", "missing_value": "NaN"}
    if name.startswith("PHIDP"):
        return {"units": "degree", "missing_value": "NaN"}
    if name.startswith("VR"):
        return {"units": "m s-1", "missing_value": "NaN"}
    return {"missing_value": "NaN"}


def _validate_texture_fields(group: zarr.Group, shape: tuple[int, int]) -> None:
    for name, dtype in TEXTURE_OUTPUT_DTYPES.items():
        if name not in group:
            continue
        array = group[name]
        if array.shape != shape or array.dtype != dtype:
            raise QCInputError(f"QC texture field {name} has invalid shape or dtype")
        values = array[:]
        if name in TEXTURE_AVAILABLE_MASK_FIELDS:
            if np.any((values != 0) & (values != 1)):
                raise QCInputError(f"QC texture availability mask {name} is not binary")
            continue
        finite = values[np.isfinite(values)]
        if name in TEXTURE_SUPPORT_RATE_FIELDS or name == "PHIDP_CIRCULAR_VARIANCE":
            if finite.size and (finite.min() < 0 or finite.max() > 1):
                raise QCInputError(f"QC texture support field {name} is outside [0, 1]")
            continue
        if finite.size and finite.min() < 0:
            raise QCInputError(f"QC texture field {name} must be non-negative")


def _validate_geometry_fields(group: zarr.Group, shape: tuple[int, int]) -> None:
    for name, dtype in QC_GEOMETRY_OUTPUT_DTYPES.items():
        if name not in group:
            continue
        array = group[name]
        if array.shape != shape or array.dtype != dtype:
            raise QCInputError(f"QC geometry field {name} has invalid shape or dtype")
        values = array[:]
        if name in QC_GEOMETRY_AVAILABLE_MASK_FIELDS:
            if np.any((values != 0) & (values != 1)):
                raise QCInputError(f"QC geometry availability mask {name} is not binary")
            continue
        finite = values[np.isfinite(values)]
        if name == CROSS_RADAR_TRUSTED_SUPPORT_FIELD:
            if finite.size and (finite.min() < 0 or finite.max() > 1):
                raise QCInputError(f"QC geometry support field {name} is outside [0, 1]")
            continue
        if name == VERTICAL_HEIGHT_DIFFERENCE_M_FIELD and finite.size and finite.min() < 0:
            raise QCInputError(f"QC geometry field {name} must be non-negative")


def _validate_phase_processing_fields(group: zarr.Group, shape: tuple[int, int]) -> None:
    for name, dtype in PHASE_PROCESSING_OUTPUT_DTYPES.items():
        if name not in group:
            continue
        array = group[name]
        if array.shape != shape or array.dtype != dtype:
            raise QCInputError(
                f"QC phase-processing field {name} has invalid shape or dtype"
            )
        values = array[:]
        if name in PHASE_PROCESSING_AVAILABLE_MASK_FIELDS:
            if np.any((values != 0) & (values != 1)):
                raise QCInputError(
                    f"QC phase-processing mask {name} is not binary"
                )
            continue
        if name == PHIDP_SHADOW_SEGMENT_INDEX_FIELD:
            if np.any(values < -1):
                raise QCInputError(
                    f"QC phase-processing segment field {name} must be >= -1"
                )
            continue
        finite = values[np.isfinite(values)]
        if name == KDP_SHADOW_UNCERTAINTY_FIELD and finite.size and finite.min() < 0:
            raise QCInputError(
                f"QC phase-processing field {name} must be non-negative"
            )


def _validate_attenuation_fields(group: zarr.Group, shape: tuple[int, int]) -> None:
    for name, dtype in ATTENUATION_SHADOW_OUTPUT_DTYPES.items():
        if name not in group:
            continue
        array = group[name]
        if array.shape != shape or array.dtype != dtype:
            raise QCInputError(
                f"QC attenuation-shadow field {name} has invalid shape or dtype"
            )
        values = array[:]
        if name in ATTENUATION_SHADOW_AVAILABLE_MASK_FIELDS:
            if np.any((values != 0) & (values != 1)):
                raise QCInputError(
                    f"QC attenuation-shadow mask {name} is not binary"
                )
            continue
        if name == ATTENUATION_SHADOW_SEGMENT_INDEX_FIELD:
            if np.any(values < -1):
                raise QCInputError(
                    f"QC attenuation-shadow segment field {name} must be >= -1"
                )
            continue
        finite = values[np.isfinite(values)]
        if name in {SPECIFIC_ATTENUATION_SHADOW_FIELD, ATTENUATION_CORRECTION_SHADOW_FIELD}:
            if finite.size and finite.min() < 0:
                raise QCInputError(
                    f"QC attenuation-shadow field {name} must be non-negative"
                )
