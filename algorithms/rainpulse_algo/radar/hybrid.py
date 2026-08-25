from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Any

import numpy as np
import zarr
from zarr.storage import MemoryStore

from rainpulse_algo.grid import RegularLatLonGrid

from .blockage import (
    GridPolarMapping,
    PolarBlockage,
    TerrainSampler,
    calculate_polar_blockage,
    map_grid_to_polar,
    required_gate_by_ray,
)
from .config import RadarDecoderConfig
from .grid_profile import RadarGridProfile


class RadarGridInputError(ValueError):
    """Raised when a QC volume cannot safely enter RP-009."""


@dataclass(frozen=True)
class PolarSweepDiagnostic:
    name: str
    nominal_elevation_deg: float
    azimuth_deg: np.ndarray
    elevation_deg: np.ndarray
    range_m: np.ndarray
    mapping: GridPolarMapping
    blockage: PolarBlockage


@dataclass(frozen=True)
class RadarGridResult:
    grid: RegularLatLonGrid
    profile: RadarGridProfile
    fields: dict[str, np.ndarray]
    polar_diagnostics: tuple[PolarSweepDiagnostic, ...]
    source_attributes: dict[str, Any]
    summary: dict[str, Any]
    operational_eligible: bool
    operational_reasons: tuple[str, ...]
    vertical_datum_status: str
    created_at: datetime


def build_hybrid_scan(
    qc_objects: Mapping[str, bytes],
    *,
    radar_config: RadarDecoderConfig,
    grid: RegularLatLonGrid,
    profile: RadarGridProfile,
    terrain: TerrainSampler,
    flag_masks: Mapping[str, np.uint32],
    expected_scan_id: str | None = None,
    created_at: datetime | None = None,
) -> RadarGridResult:
    store = MemoryStore()
    store.update({key: bytes(value) for key, value in qc_objects.items()})
    root = zarr.open_group(store=store, mode="r")
    _validate_inputs(root, radar_config, grid, profile, expected_scan_id, flag_masks)

    antenna_altitude = radar_config.site.get("antenna_altitude_m")
    beam_width = radar_config.hardware.get("beam_width_vertical_deg")
    if antenna_altitude is None or beam_width is None:
        raise RadarGridInputError("radar antenna altitude and vertical beam width are required")
    vertical_status = _vertical_datum_status(radar_config, profile)
    operational_reasons = _operational_reasons(root, radar_config, vertical_status)

    longitude, latitude = np.meshgrid(grid.longitude, grid.latitude)
    shape = grid.shape
    output = _empty_grid_fields(shape, flag_masks)
    selected = np.zeros(shape, dtype=bool)
    severe_blockage_seen = np.zeros(shape, dtype=bool)
    polar_diagnostics: list[PolarSweepDiagnostic] = []
    selection_counts: dict[str, int] = {}
    skipped_sweeps: dict[str, str] = {}

    sweep_names = [f"sweep_{int(item):03d}" for item in root["sweep_number"][:]]
    sweep_names.sort(key=lambda name: (float(root[name].attrs["nominal_elevation_deg"]), name))
    for name in sweep_names:
        group = root[name]
        dbzh = group["DBZH_QC"][:].astype("float32", copy=False)
        valid_source = group["VALID_MASK"][:] == 1
        if not np.any(valid_source & np.isfinite(dbzh)):
            skipped_sweeps[name] = "no_finite_valid_dbzh"
            continue
        mapping = map_grid_to_polar(
            longitude,
            latitude,
            radar_longitude_deg=float(radar_config.site["longitude_deg"]),
            radar_latitude_deg=float(radar_config.site["latitude_deg"]),
            sweep_azimuth_deg=group["azimuth"][:],
            sweep_range_m=group["range"][:],
            config=profile.polar_mapping,
        )
        required = required_gate_by_ray(mapping, dbzh.shape[0])
        polar = calculate_polar_blockage(
            azimuth_deg=group["azimuth"][:],
            elevation_deg=group["elevation"][:],
            range_m=group["range"][:],
            required_max_gate=required,
            radar_longitude_deg=float(radar_config.site["longitude_deg"]),
            radar_latitude_deg=float(radar_config.site["latitude_deg"]),
            antenna_altitude_m=float(antenna_altitude),
            vertical_beam_width_deg=float(beam_width),
            beam_config=profile.beam_geometry,
            blockage_config=profile.blockage,
            terrain=terrain,
        )
        polar_diagnostics.append(
            PolarSweepDiagnostic(
                name=name,
                nominal_elevation_deg=float(group.attrs["nominal_elevation_deg"]),
                azimuth_deg=group["azimuth"][:].astype("float32"),
                elevation_deg=group["elevation"][:].astype("float32"),
                range_m=group["range"][:].astype("float32"),
                mapping=mapping,
                blockage=polar,
            )
        )
        candidate = _candidate_fields(group, mapping, polar, profile, flag_masks)
        severe_blockage_seen |= candidate["severe_blockage"]
        choose = ~selected & candidate["usable"]
        if not np.any(choose):
            selection_counts[name] = 0
            continue
        sweep_index = int(name.removeprefix("sweep_"))
        _select_into(output, candidate, choose, sweep_index, profile, flag_masks)
        selected |= choose
        selection_counts[name] = int(np.count_nonzero(choose))

    output["QC_FLAGS"][~selected] = np.uint32(flag_masks["MISSING"])
    output["QC_FLAGS"][~selected & severe_blockage_seen] |= np.uint32(
        flag_masks["BEAM_BLOCKED"]
    )
    if not polar_diagnostics:
        raise RadarGridInputError("QC volume has no selectable reflectivity sweep")
    valid_count = int(np.count_nonzero(selected))
    low_quality_count = int(np.count_nonzero(output["LOW_QUALITY_MASK"]))
    finite_quality = output["QUALITY_INDEX"][selected]
    summary = {
        "schema_version": "1.0",
        "scan_id": str(root.attrs.get("scan_id")),
        "radar_id": str(root.attrs.get("radar_id")),
        "grid_id": grid.grid_id,
        "grid_config_version": grid.config_version,
        "profile_version": profile.profile_version,
        "algorithm_version": profile.algorithm_version,
        "dem_asset_version": profile.dem.asset_version,
        "vertical_datum_status": vertical_status,
        "operational_eligible": not operational_reasons,
        "operational_reasons": list(operational_reasons),
        "grid_cell_count": int(np.prod(shape)),
        "valid_cell_count": valid_count,
        "missing_cell_count": int(np.prod(shape)) - valid_count,
        "low_quality_cell_count": low_quality_count,
        "valid_coverage_ratio": valid_count / int(np.prod(shape)),
        "mean_quality_index": (
            float(np.mean(finite_quality)) if finite_quality.size else 0.0
        ),
        "beam_blocked_missing_cell_count": int(
            np.count_nonzero(~selected & severe_blockage_seen)
        ),
        "selection_counts": selection_counts,
        "skipped_sweeps": skipped_sweeps,
    }
    source_attributes = {
        key: root.attrs.get(key)
        for key in (
            "asset_id",
            "scan_id",
            "radar_id",
            "normalized_volume_uri",
            "radar_config_version",
            "qc_profile",
            "qc_pipeline_version",
            "flag_definition_version",
        )
    }
    ray_times = np.concatenate(
        [root[name]["ray_time"][:].astype("datetime64[ns]") for name in sweep_names]
    )
    source_attributes["volume_start_time_utc"] = np.datetime_as_string(
        ray_times.min(), unit="ns", timezone="UTC"
    )
    source_attributes["volume_end_time_utc"] = np.datetime_as_string(
        ray_times.max(), unit="ns", timezone="UTC"
    )
    return RadarGridResult(
        grid=grid,
        profile=profile,
        fields=output,
        polar_diagnostics=tuple(polar_diagnostics),
        source_attributes=source_attributes,
        summary=summary,
        operational_eligible=not operational_reasons,
        operational_reasons=operational_reasons,
        vertical_datum_status=vertical_status,
        created_at=created_at or datetime.now(UTC),
    )


def _validate_inputs(
    root: zarr.Group,
    radar_config: RadarDecoderConfig,
    grid: RegularLatLonGrid,
    profile: RadarGridProfile,
    expected_scan_id: str | None,
    flag_masks: Mapping[str, np.uint32],
) -> None:
    if root.attrs.get("contract_name") != "rainpulse.qc-radar-volume":
        raise RadarGridInputError("grid input is not a QCRadarVolume")
    if root.attrs.get("radar_id") != radar_config.radar_id:
        raise RadarGridInputError("QC radar identity differs from radar configuration")
    if root.attrs.get("radar_config_version") != radar_config.config_version:
        raise RadarGridInputError("QC radar configuration version differs")
    if expected_scan_id is not None and str(root.attrs.get("scan_id")) != expected_scan_id:
        raise RadarGridInputError("QC scan identity differs from the grid request")
    if grid.grid_id != profile.grid_id or grid.config_version != profile.grid_config_version:
        raise RadarGridInputError("target grid differs from the Hybrid Scan profile")
    if root.attrs.get("flag_definition_version") != profile.flag_definition_version:
        raise RadarGridInputError("QC flag definition differs from the grid profile")
    required_flags = {"MISSING", "HARDWARE_ANOMALY", "BEAM_BLOCKED", "LOW_QUALITY"}
    if not required_flags <= flag_masks.keys():
        raise RadarGridInputError("flag definition is missing RP-009 flags")


def _vertical_datum_status(
    radar_config: RadarDecoderConfig,
    profile: RadarGridProfile,
) -> str:
    datum = radar_config.site.get("altitude_datum")
    if datum is None:
        if profile.beam_geometry.unverified_vertical_datum_policy == "reject":
            raise RadarGridInputError("radar antenna altitude datum is unverified")
        return "unverified_engineering"
    normalized = str(datum).strip().upper().replace(" ", "")
    if normalized not in {"EPSG:3855", "EGM2008", "EGM2008HEIGHT"}:
        raise RadarGridInputError(
            f"radar altitude datum {datum!r} is incompatible with {profile.dem.vertical_crs}"
        )
    return "verified_egm2008"


def _operational_reasons(
    root: zarr.Group,
    radar_config: RadarDecoderConfig,
    vertical_status: str,
) -> tuple[str, ...]:
    reasons: list[str] = []
    if radar_config.lifecycle != "ready":
        reasons.append("radar_config_not_ready")
    if vertical_status != "verified_egm2008":
        reasons.append("vertical_datum_unverified")
    modules = {
        item.get("name"): item.get("status")
        for item in root.attrs.get("module_provenance", [])
        if isinstance(item, dict)
    }
    for module in ("static_ground_clutter", "sea_ap"):
        if modules.get(module) != "applied":
            reasons.append(f"qc_{module}_not_applied")
    return tuple(reasons)


def _empty_grid_fields(
    shape: tuple[int, int],
    flag_masks: Mapping[str, np.uint32],
) -> dict[str, np.ndarray]:
    floating = (
        "DBZH_QC",
        "QUALITY_INDEX",
        "QI_BLOCKAGE",
        "QI_BEAM_HEIGHT",
        "SOURCE_ELEVATION",
        "BEAM_HEIGHT",
        "TERRAIN_HEIGHT",
        "BLOCKAGE_RATE",
        "DATA_AGE",
    )
    result = {name: np.full(shape, np.nan, dtype="float32") for name in floating}
    result["QC_FLAGS"] = np.full(shape, flag_masks["MISSING"], dtype="uint32")
    result["SOURCE_SWEEP"] = np.full(shape, -1, dtype="int16")
    result["VALID_MASK"] = np.zeros(shape, dtype="uint8")
    result["LOW_QUALITY_MASK"] = np.zeros(shape, dtype="uint8")
    return result


def _map(values: np.ndarray, mapping: GridPolarMapping) -> np.ndarray:
    fill_value = np.nan if np.issubdtype(values.dtype, np.floating) else 0
    output = np.full(mapping.supported.shape, fill_value, dtype=values.dtype)
    supported = mapping.supported
    output[supported] = values[
        mapping.ray_index[supported], mapping.gate_index[supported]
    ]
    return output


def _candidate_fields(
    group: zarr.Group,
    mapping: GridPolarMapping,
    polar: PolarBlockage,
    profile: RadarGridProfile,
    flag_masks: Mapping[str, np.uint32],
) -> dict[str, np.ndarray]:
    dbzh = _map(group["DBZH_QC"][:], mapping).astype("float32")
    source_quality = _map(group["QUALITY_INDEX"][:], mapping).astype("float32")
    source_flags = _map(group["QC_FLAGS"][:], mapping).astype("uint32")
    source_valid = _map(group["VALID_MASK"][:], mapping).astype("uint8")
    elevation = _map(
        np.broadcast_to(group["elevation"][:][:, None], group["DBZH_QC"].shape),
        mapping,
    ).astype("float32")
    blockage = _map(polar.cumulative, mapping).astype("float32")
    beam_height = _map(polar.beam_height_m, mapping).astype("float32")
    terrain_height = _map(polar.terrain_height_m, mapping).astype("float32")
    beam_agl = beam_height - terrain_height
    qi_blockage = (1.0 - blockage).astype("float32")
    qi_height = np.exp(
        -np.maximum(beam_agl, 0) / profile.hybrid_scan.beam_height_quality_scale_m
    ).astype("float32")
    quality = (source_quality * qi_blockage * qi_height).astype("float32")
    reject_mask = np.uint32(0)
    for name in profile.hybrid_scan.reject_flags:
        reject_mask |= np.uint32(flag_masks[name])
    finite = (
        mapping.supported
        & np.isfinite(dbzh)
        & np.isfinite(source_quality)
        & np.isfinite(blockage)
        & np.isfinite(beam_height)
        & np.isfinite(terrain_height)
    )
    usable = (
        finite
        & (source_valid == 1)
        & ((source_flags & reject_mask) == 0)
        & (source_quality >= profile.hybrid_scan.minimum_source_quality_index)
        & (blockage <= profile.blockage.maximum_usable_fraction)
        & (beam_agl >= 0)
        & (beam_agl <= profile.hybrid_scan.maximum_beam_height_agl_m)
    )
    severe = finite & (source_valid == 1) & (
        blockage > profile.blockage.maximum_usable_fraction
    )
    return {
        "dbzh": dbzh,
        "quality": quality,
        "qi_blockage": qi_blockage,
        "qi_height": qi_height,
        "source_flags": source_flags,
        "elevation": elevation,
        "beam_height": beam_height,
        "terrain_height": terrain_height,
        "blockage": blockage,
        "usable": usable,
        "severe_blockage": severe,
    }


def _select_into(
    output: dict[str, np.ndarray],
    candidate: dict[str, np.ndarray],
    choose: np.ndarray,
    sweep_index: int,
    profile: RadarGridProfile,
    flag_masks: Mapping[str, np.uint32],
) -> None:
    mapping = {
        "DBZH_QC": "dbzh",
        "QUALITY_INDEX": "quality",
        "QI_BLOCKAGE": "qi_blockage",
        "QI_BEAM_HEIGHT": "qi_height",
        "SOURCE_ELEVATION": "elevation",
        "BEAM_HEIGHT": "beam_height",
        "TERRAIN_HEIGHT": "terrain_height",
        "BLOCKAGE_RATE": "blockage",
    }
    for output_name, candidate_name in mapping.items():
        output[output_name][choose] = candidate[candidate_name][choose]
    flags = candidate["source_flags"].copy()
    flags[candidate["blockage"] >= profile.blockage.flag_fraction] |= np.uint32(
        flag_masks["BEAM_BLOCKED"]
    )
    low_quality = candidate["quality"] < profile.hybrid_scan.low_quality_threshold
    flags[low_quality] |= np.uint32(flag_masks["LOW_QUALITY"])
    output["QC_FLAGS"][choose] = flags[choose]
    output["SOURCE_SWEEP"][choose] = np.int16(sweep_index)
    output["DATA_AGE"][choose] = 0.0
    output["VALID_MASK"][choose] = 1
    output["LOW_QUALITY_MASK"][choose] = low_quality[choose].astype("uint8")
