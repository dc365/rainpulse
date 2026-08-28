from __future__ import annotations

from collections.abc import Mapping
from pathlib import Path
from typing import Any
from uuid import UUID

import numpy as np
import zarr
from numcodecs import Blosc
from zarr.storage import MemoryStore

from .config import RadarDecoderConfig
from .fmt import (
    ABSENT_RAW_GATE_CODE,
    DECODER_ID,
    DECODER_VERSION,
    DecodedRadarVolume,
    DecodeError,
)
from .health import RadarHealthSummary

CONTRACT_NAME = "rainpulse.normalized-radar-volume"
CONTRACT_VERSION = "1.1"
GEOMETRY_ENCODING = "sweep_groups_v1"


def build_zarr_store(
    volume: DecodedRadarVolume,
    config: RadarDecoderConfig,
    *,
    asset_id: UUID | str,
    source_uri: str,
    health: RadarHealthSummary | None = None,
    provenance: Mapping[str, str] | None = None,
) -> dict[str, bytes]:
    store = MemoryStore()
    root = zarr.group(store=store, overwrite=True)
    starts: list[int] = []
    ends: list[int] = []
    cursor = 0
    for sweep in volume.sweeps:
        starts.append(cursor)
        cursor += sweep.ray_count
        ends.append(cursor - 1)

    root.attrs.update(
        {
            "contract_name": CONTRACT_NAME,
            "contract_version": CONTRACT_VERSION,
            "asset_id": str(asset_id),
            "radar_id": config.radar_id,
            "radar_config_version": config.config_version,
            "radar_config_lifecycle": config.lifecycle,
            "operational_eligible": config.lifecycle == "ready",
            "decoder_id": DECODER_ID,
            "decoder_version": DECODER_VERSION,
            "source_format": config.source["format"],
            "source_format_version": config.source["format_version"],
            "source_uri": source_uri,
            "source_filename": volume.source_filename,
            "input_sha256": volume.input_sha256,
            "input_size_bytes": volume.input_size_bytes,
            "field_mapping_version": config.config_version,
            "geometry_encoding": GEOMETRY_ENCODING,
            "raw_gate_code_encoding": "uint32_source_code_with_absent_moment_sentinel",
            "absent_raw_gate_code": int(ABSENT_RAW_GATE_CODE),
            "site_name": volume.site.name,
            "site_longitude_deg": volume.site.longitude_deg,
            "site_latitude_deg": volume.site.latitude_deg,
            "site_altitude_m": volume.site.ground_altitude_m,
            "antenna_altitude_m": volume.site.antenna_altitude_m,
            "altitude_datum": config.site.get("altitude_datum"),
            "radar_band": config.hardware.get("radar_band"),
            "frequency_mhz": volume.site.frequency_mhz,
            "scan_strategy": volume.task.name,
            "volume_start_time_utc": volume.volume_start_time.isoformat(),
            "volume_end_time_utc": volume.volume_end_time.isoformat(),
            "filename_time_utc": (
                volume.filename_time.isoformat() if volume.filename_time is not None else None
            ),
            "canonical_fields": list(volume.canonical_fields),
            "source_cut_count": len(volume.cuts),
            "source_ray_count": volume.ray_count,
            "decode_warnings": list(volume.warnings),
            "known_source_issues": list(config.known_issues),
        }
    )
    if health is not None:
        root.attrs.update(
            {
                "health_summary_key": "health/summary.json",
                "health_profile_version": health.value["health_profile_version"],
                "radar_health": health.value["health"],
                "scan_completeness": health.value["scan_completeness"],
                "channel_status": health.value["channel_status"],
            }
        )
    if provenance:
        root.attrs.update(dict(provenance))

    _array(root, "sweep_number", np.arange(len(volume.sweeps), dtype="int16"), None)
    _array(root, "sweep_start_ray_index", np.asarray(starts, dtype="int32"), None)
    _array(root, "sweep_end_ray_index", np.asarray(ends, dtype="int32"), None)

    compressor = Blosc(cname="zstd", clevel=3, shuffle=Blosc.BITSHUFFLE)
    for sweep_index, sweep in enumerate(volume.sweeps):
        group = root.create_group(f"sweep_{sweep_index:03d}")
        group.attrs.update(
            {
                "sweep_number": sweep_index,
                "source_sweep_number": sweep.source_sweep_number,
                "nominal_elevation_deg": sweep.nominal_elevation_deg,
                "source_moments": list(sweep.source_moments),
                "radial_state_counts": {
                    str(key): value for key, value in sweep.radial_state_counts.items()
                },
                "nyquist_velocity_m_s": sweep.nyquist_velocity_m_s,
            }
        )
        azimuth = _array(group, "azimuth", sweep.azimuth_deg, compressor)
        azimuth.attrs.update({"units": "degree", "standard_name": "sensor_azimuth_angle"})
        elevation = _array(group, "elevation", sweep.elevation_deg, compressor)
        elevation.attrs.update(
            {"units": "degree", "standard_name": "sensor_elevation_angle"}
        )
        ray_time = _array(group, "ray_time", sweep.ray_time, compressor)
        ray_time.attrs.update({"timezone": "UTC", "standard_name": "time"})
        horizontal_noise = _array(
            group, "horizontal_noise", sweep.horizontal_noise_dbm, compressor
        )
        horizontal_noise.attrs.update(
            {"units": "dBm", "source": "RSTM radial header", "missing_value": "NaN"}
        )
        vertical_noise = _array(group, "vertical_noise", sweep.vertical_noise_dbm, compressor)
        vertical_noise.attrs.update(
            {"units": "dBm", "source": "RSTM radial header", "missing_value": "NaN"}
        )
        range_array = _array(group, "range", sweep.range_m, compressor)
        range_array.attrs.update(
            {"units": "m", "standard_name": "projection_range_coordinate"}
        )
        for name, values in sorted(sweep.fields.items()):
            chunks = (min(64, values.shape[0]), min(512, values.shape[1]))
            array = group.create_dataset(
                name,
                data=values,
                chunks=chunks,
                compressor=compressor,
                overwrite=True,
                fill_value=np.nan,
            )
            field = sweep.field_metadata[name]
            array.attrs.update(
                {
                    "units": field.mapping.canonical_unit,
                    "source_name": field.mapping.source_name,
                    "source_unit": field.mapping.source_unit,
                    "source_data_type_code": field.source_code,
                    "raw_scale": field.raw_scale,
                    "raw_offset": field.raw_offset,
                    "raw_bin_length": field.raw_bin_length,
                    "raw_reserved_codes": [0, 1, 2, 3, 4],
                    "source_flags": field.source_flags,
                    "missing_value": "NaN",
                }
            )
            raw_codes = sweep.raw_gate_codes[name]
            raw_array = group.create_dataset(
                f"{name}_RAW_CODE",
                data=raw_codes,
                chunks=chunks,
                compressor=compressor,
                overwrite=True,
                fill_value=ABSENT_RAW_GATE_CODE,
            )
            raw_array.attrs.update(
                {
                    "source_field": name,
                    "source_name": field.mapping.source_name,
                    "source_data_type_code": field.source_code,
                    "source_bin_length": field.raw_bin_length,
                    "reserved_codes": [0, 1, 2, 3, 4],
                    "reserved_code_semantics": "vendor_mapping_pending",
                    "absent_moment_code": int(ABSENT_RAW_GATE_CODE),
                }
            )

    zarr.consolidate_metadata(store)
    objects = {str(key): bytes(value) for key, value in store.items()}
    if health is not None:
        objects["health/summary.json"] = health.json_bytes()
    validate_zarr_store(objects)
    return objects


def validate_zarr_store(objects: Mapping[str, bytes]) -> dict[str, Any]:
    if not objects or ".zgroup" not in objects or ".zattrs" not in objects:
        raise DecodeError("normalized Zarr store is missing root metadata")
    store = MemoryStore()
    store.update({key: bytes(value) for key, value in objects.items()})
    root = zarr.open_group(store=store, mode="r")
    if root.attrs.get("contract_name") != CONTRACT_NAME:
        raise DecodeError("normalized Zarr contract name is invalid")
    if root.attrs.get("geometry_encoding") != GEOMETRY_ENCODING:
        raise DecodeError("normalized Zarr geometry encoding is invalid")
    sweep_numbers = root["sweep_number"][:]
    starts = root["sweep_start_ray_index"][:]
    ends = root["sweep_end_ray_index"][:]
    if not (len(sweep_numbers) == len(starts) == len(ends) and len(sweep_numbers) > 0):
        raise DecodeError("normalized Zarr sweep index arrays are inconsistent")
    if np.any(ends < starts) or np.any(np.diff(starts) <= 0):
        raise DecodeError("normalized Zarr sweep ray boundaries are invalid")

    field_names: set[str] = set()
    raw_code_names: set[str] = set()
    for sweep_number in sweep_numbers:
        group = root[f"sweep_{int(sweep_number):03d}"]
        azimuth = group["azimuth"][:]
        elevation = group["elevation"][:]
        ray_time = group["ray_time"][:].astype("datetime64[ns]").astype("int64")
        horizontal_noise = group["horizontal_noise"][:]
        vertical_noise = group["vertical_noise"][:]
        ranges = group["range"][:]
        if not (
            azimuth.ndim
            == elevation.ndim
            == ray_time.ndim
            == horizontal_noise.ndim
            == vertical_noise.ndim
            == ranges.ndim
            == 1
        ):
            raise DecodeError("normalized Zarr coordinate rank is invalid")
        if not (
            len(azimuth)
            == len(elevation)
            == len(ray_time)
            == len(horizontal_noise)
            == len(vertical_noise)
        ):
            raise DecodeError("normalized Zarr ray coordinates have different lengths")
        if np.any((azimuth < 0) | (azimuth >= 360)):
            raise DecodeError("normalized Zarr azimuth is outside [0, 360)")
        if np.any(np.diff(ray_time) < 0) or np.any(np.diff(ranges) <= 0):
            raise DecodeError("normalized Zarr time/range coordinates are not increasing")
        for name, array in group.arrays():
            if name in {
                "azimuth",
                "elevation",
                "ray_time",
                "horizontal_noise",
                "vertical_noise",
                "range",
            }:
                continue
            if name.endswith("_RAW_CODE"):
                source_field = name.removesuffix("_RAW_CODE")
                if array.shape != (len(azimuth), len(ranges)) or array.dtype != np.dtype(
                    "uint32"
                ):
                    raise DecodeError(f"normalized raw gate codes {name} are invalid")
                absent_code = int(array.attrs.get("absent_moment_code", -1))
                if absent_code != int(ABSENT_RAW_GATE_CODE):
                    raise DecodeError(f"normalized raw gate codes {name} lack sentinel metadata")
                raw_code_names.add(source_field)
                continue
            if array.shape != (len(azimuth), len(ranges)) or array.dtype != np.dtype("float32"):
                raise DecodeError(f"normalized field {name} has invalid shape or dtype")
            field_names.add(name)
    if "DBZH" not in field_names:
        raise DecodeError("normalized Zarr store has no DBZH field")
    if raw_code_names != field_names:
        raise DecodeError("normalized Zarr raw gate codes do not match canonical fields")
    return {
        "sweep_count": int(len(sweep_numbers)),
        "ray_count": int(ends[-1] + 1),
        "fields": sorted(field_names),
        "raw_gate_code_fields": sorted(raw_code_names),
        "object_count": len(objects),
        "size_bytes": sum(len(value) for value in objects.values()),
    }


def write_zarr_store(objects: Mapping[str, bytes], output: str | Path) -> Path:
    output_path = Path(output)
    if output_path.exists():
        raise FileExistsError(f"output already exists: {output_path}")
    output_path.mkdir(parents=True)
    for key, value in objects.items():
        relative = Path(key)
        if relative.is_absolute() or ".." in relative.parts:
            raise ValueError(f"unsafe Zarr object key {key!r}")
        target = output_path / relative
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_bytes(value)
    return output_path


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
