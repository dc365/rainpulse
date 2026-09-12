from __future__ import annotations

import hashlib
import json
from collections.abc import Mapping
from dataclasses import dataclass
from datetime import UTC, datetime
from types import MappingProxyType
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
    freeze_grid_polar_mapping,
    freeze_polar_blockage,
    grid_polar_mapping_size_bytes,
    map_grid_to_polar,
    polar_blockage_size_bytes,
    required_gate_by_ray,
)
from .config import RadarDecoderConfig
from .dem import DEFAULT_DEM_CACHE_BUDGET_BYTES, SharedDEMCache, default_dem_cache
from .grid_profile import RadarGridProfile
from .runtime_cache import ByteBudgetLRUCache, CacheLookup


class RadarGridInputError(ValueError):
    """Raised when a QC volume cannot safely enter Hybrid Scan."""


UPSTREAM_QI_COMPONENTS = (
    "QI_METEO",
    "QI_ATTENUATION",
    "QI_INTERFERENCE",
    "QI_CALIBRATION",
    "QI_RANGE",
)


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
    cache_stats: dict[str, int]
    created_at: datetime


DEFAULT_CANDIDATE_CACHE_BUDGET_BYTES = 64 * 1024 * 1024
DEFAULT_GEOMETRY_CACHE_BUDGET_BYTES = 128 * 1024 * 1024


@dataclass(frozen=True)
class HybridScanCaches:
    candidate: ByteBudgetLRUCache[Mapping[str, np.ndarray]]
    geometry: ByteBudgetLRUCache[GridPolarMapping]
    dem: SharedDEMCache


_DEFAULT_HYBRID_SCAN_CACHES = HybridScanCaches(
    candidate=ByteBudgetLRUCache(DEFAULT_CANDIDATE_CACHE_BUDGET_BYTES),
    geometry=ByteBudgetLRUCache(DEFAULT_GEOMETRY_CACHE_BUDGET_BYTES),
    dem=default_dem_cache(),
)


def build_hybrid_scan(
    qc_objects: Mapping[str, bytes],
    *,
    radar_config: RadarDecoderConfig,
    grid: RegularLatLonGrid,
    profile: RadarGridProfile,
    terrain: TerrainSampler,
    flag_masks: Mapping[str, np.uint32],
    expected_scan_id: str | None = None,
    caches: HybridScanCaches | None = None,
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
    if (
        root.attrs.get("qc_engine") == "open_source"
        and root.attrs.get("operational_eligible") is not True
    ):
        operational_reasons = (*operational_reasons, "qc_candidate_not_operationally_accepted")

    longitude, latitude = np.meshgrid(grid.longitude, grid.latitude)
    shape = grid.shape
    output = _empty_grid_fields(shape, flag_masks)
    selected = np.zeros(shape, dtype=bool)
    severe_blockage_seen = np.zeros(shape, dtype=bool)
    polar_diagnostics: list[PolarSweepDiagnostic] = []
    selection_counts: dict[str, int] = {}
    skipped_sweeps: dict[str, str] = {}
    cache_stats = _empty_cache_stats()
    cache_bundle = caches or default_hybrid_scan_caches()
    artifact_digest = _qc_artifact_digest(qc_objects)
    profile_digest = _hybrid_profile_digest(profile, flag_masks)
    terrain_identity = _terrain_cache_identity(terrain)

    sweep_names = [f"sweep_{int(item):03d}" for item in root["sweep_number"][:]]
    sweep_names.sort(key=lambda name: (float(root[name].attrs["nominal_elevation_deg"]), name))
    for name in sweep_names:
        group = root[name]
        dbzh = group["DBZH_QC"][:].astype("float32", copy=False)
        valid_source = group["VALID_MASK"][:] == 1
        if not np.any(valid_source & np.isfinite(dbzh)):
            skipped_sweeps[name] = "no_finite_valid_dbzh"
            continue
        mapping_key = _mapping_cache_key(
            artifact_digest=artifact_digest,
            sweep_name=name,
            grid=grid,
            radar_longitude_deg=float(radar_config.site["longitude_deg"]),
            radar_latitude_deg=float(radar_config.site["latitude_deg"]),
            profile=profile,
        )
        mapping_lookup = cache_bundle.geometry.get_or_compute(
            mapping_key,
            factory=lambda group=group: map_grid_to_polar(
                longitude,
                latitude,
                radar_longitude_deg=float(radar_config.site["longitude_deg"]),
                radar_latitude_deg=float(radar_config.site["latitude_deg"]),
                sweep_azimuth_deg=group["azimuth"][:],
                sweep_range_m=group["range"][:],
                config=profile.polar_mapping,
            ),
            size_of=grid_polar_mapping_size_bytes,
            freeze=freeze_grid_polar_mapping,
        )
        _record_cache_lookup(cache_stats, "geometry", mapping_lookup)
        mapping = mapping_lookup.value
        required = required_gate_by_ray(mapping, dbzh.shape[0])
        polar = _cached_polar_blockage(
            group=group,
            required=required,
            radar_config=radar_config,
            profile=profile,
            terrain=terrain,
            terrain_identity=terrain_identity,
            artifact_digest=artifact_digest,
            sweep_name=name,
            cache=cache_bundle.dem,
            cache_stats=cache_stats,
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
        candidate = _cached_candidate_fields(
            artifact_digest=artifact_digest,
            sweep_name=name,
            profile_digest=profile_digest,
            geometry_key=mapping_key,
            blockage_enabled=terrain_identity is not None,
            group=group,
            mapping=mapping,
            polar=polar,
            profile=profile,
            flag_masks=flag_masks,
            cache=cache_bundle.candidate,
            cache_stats=cache_stats,
        )
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
    output["QC_FLAGS"][~selected & severe_blockage_seen] |= np.uint32(flag_masks["BEAM_BLOCKED"])
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
        "mean_quality_index": (float(np.mean(finite_quality)) if finite_quality.size else 0.0),
        "beam_blocked_missing_cell_count": int(np.count_nonzero(~selected & severe_blockage_seen)),
        "selection_counts": selection_counts,
        "skipped_sweeps": skipped_sweeps,
    }
    source_attributes = {
        key: root.attrs.get(key)
        for key in (
            "asset_id",
            "input_asset_ids",
            "scan_id",
            "radar_id",
            "normalized_volume_uri",
            "radar_config_version",
            "qc_profile",
            "qc_pipeline_version",
            "qc_engine",
            "qc_parameters_sha256",
            "qc_libraries",
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
        cache_stats=dict(cache_stats),
        created_at=created_at or datetime.now(UTC),
    )


def create_hybrid_scan_caches(
    *,
    candidate_budget_bytes: int = DEFAULT_CANDIDATE_CACHE_BUDGET_BYTES,
    geometry_budget_bytes: int = DEFAULT_GEOMETRY_CACHE_BUDGET_BYTES,
    dem_budget_bytes: int = DEFAULT_DEM_CACHE_BUDGET_BYTES,
) -> HybridScanCaches:
    return HybridScanCaches(
        candidate=ByteBudgetLRUCache(candidate_budget_bytes),
        geometry=ByteBudgetLRUCache(geometry_budget_bytes),
        dem=SharedDEMCache(dem_budget_bytes),
    )


def default_hybrid_scan_caches() -> HybridScanCaches:
    return _DEFAULT_HYBRID_SCAN_CACHES


def _cached_polar_blockage(
    *,
    group: zarr.Group,
    required: np.ndarray,
    radar_config: RadarDecoderConfig,
    profile: RadarGridProfile,
    terrain: TerrainSampler,
    terrain_identity: str | None,
    artifact_digest: str,
    sweep_name: str,
    cache: SharedDEMCache,
    cache_stats: dict[str, int],
) -> PolarBlockage:
    if terrain_identity is None:
        return calculate_polar_blockage(
            azimuth_deg=group["azimuth"][:],
            elevation_deg=group["elevation"][:],
            range_m=group["range"][:],
            required_max_gate=required,
            radar_longitude_deg=float(radar_config.site["longitude_deg"]),
            radar_latitude_deg=float(radar_config.site["latitude_deg"]),
            antenna_altitude_m=float(radar_config.site["antenna_altitude_m"]),
            vertical_beam_width_deg=float(radar_config.hardware["beam_width_vertical_deg"]),
            beam_config=profile.beam_geometry,
            blockage_config=profile.blockage,
            terrain=terrain,
        )
    key = _polar_blockage_cache_key(
        artifact_digest=artifact_digest,
        sweep_name=sweep_name,
        terrain_identity=terrain_identity,
        required=required,
        radar_config=radar_config,
        profile=profile,
    )
    lookup = cache.get_or_compute(
        key,
        factory=lambda: calculate_polar_blockage(
            azimuth_deg=group["azimuth"][:],
            elevation_deg=group["elevation"][:],
            range_m=group["range"][:],
            required_max_gate=required,
            radar_longitude_deg=float(radar_config.site["longitude_deg"]),
            radar_latitude_deg=float(radar_config.site["latitude_deg"]),
            antenna_altitude_m=float(radar_config.site["antenna_altitude_m"]),
            vertical_beam_width_deg=float(radar_config.hardware["beam_width_vertical_deg"]),
            beam_config=profile.beam_geometry,
            blockage_config=profile.blockage,
            terrain=terrain,
        ),
        size_of=polar_blockage_size_bytes,
        freeze=freeze_polar_blockage,
    )
    _record_cache_lookup(cache_stats, "dem", lookup)
    return lookup.value


def _cached_candidate_fields(
    *,
    artifact_digest: str,
    sweep_name: str,
    profile_digest: str,
    geometry_key: str,
    blockage_enabled: bool,
    group: zarr.Group,
    mapping: GridPolarMapping,
    polar: PolarBlockage,
    profile: RadarGridProfile,
    flag_masks: Mapping[str, np.uint32],
    cache: ByteBudgetLRUCache[Mapping[str, np.ndarray]],
    cache_stats: dict[str, int],
) -> Mapping[str, np.ndarray]:
    if not blockage_enabled:
        return _candidate_fields(group, mapping, polar, profile, flag_masks)
    key = (
        f"candidate|{artifact_digest}|{sweep_name}|{profile_digest}|"
        f"{hashlib.sha256(geometry_key.encode()).hexdigest()}"
    )
    lookup = cache.get_or_compute(
        key,
        factory=lambda: _candidate_fields(group, mapping, polar, profile, flag_masks),
        size_of=_candidate_fields_size_bytes,
        freeze=_freeze_candidate_fields,
    )
    _record_cache_lookup(cache_stats, "candidate", lookup)
    return lookup.value


def _mapping_cache_key(
    *,
    artifact_digest: str,
    sweep_name: str,
    grid: RegularLatLonGrid,
    radar_longitude_deg: float,
    radar_latitude_deg: float,
    profile: RadarGridProfile,
) -> str:
    payload = {
        "artifact_digest": artifact_digest,
        "grid_coordinate_sha256": grid.coordinate_sha256,
        "mapping": {
            "maximum_azimuth_offset_deg": profile.polar_mapping.maximum_azimuth_offset_deg,
            "maximum_range_offset_gate_fraction": (
                profile.polar_mapping.maximum_range_offset_gate_fraction
            ),
        },
        "radar_latitude_deg": radar_latitude_deg,
        "radar_longitude_deg": radar_longitude_deg,
        "sweep_name": sweep_name,
    }
    return (
        "geometry|"
        + hashlib.sha256(
            json.dumps(payload, sort_keys=True, separators=(",", ":")).encode("utf-8")
        ).hexdigest()
    )


def _polar_blockage_cache_key(
    *,
    artifact_digest: str,
    sweep_name: str,
    terrain_identity: str,
    required: np.ndarray,
    radar_config: RadarDecoderConfig,
    profile: RadarGridProfile,
) -> str:
    digest = hashlib.sha256()
    digest.update(artifact_digest.encode("utf-8"))
    digest.update(sweep_name.encode("utf-8"))
    digest.update(terrain_identity.encode("utf-8"))
    digest.update(np.asarray(required, dtype="<i4").tobytes())
    digest.update(str(radar_config.site["longitude_deg"]).encode("utf-8"))
    digest.update(str(radar_config.site["latitude_deg"]).encode("utf-8"))
    digest.update(str(radar_config.site["antenna_altitude_m"]).encode("utf-8"))
    digest.update(str(radar_config.hardware["beam_width_vertical_deg"]).encode("utf-8"))
    digest.update(str(profile.beam_geometry.effective_earth_radius_factor).encode("utf-8"))
    digest.update(str(profile.beam_geometry.earth_radius_m).encode("utf-8"))
    digest.update(str(profile.blockage.flag_fraction).encode("utf-8"))
    digest.update(str(profile.blockage.maximum_usable_fraction).encode("utf-8"))
    return "dem-blockage|" + digest.hexdigest()


def _terrain_cache_identity(terrain: TerrainSampler) -> str | None:
    identity = getattr(terrain, "cache_identity", None)
    if identity is None:
        return None
    return str(identity)


def _candidate_fields_size_bytes(candidate: Mapping[str, np.ndarray]) -> int:
    total = 0
    for value in candidate.values():
        total += int(np.asarray(value).nbytes)
    return total


def _freeze_candidate_fields(candidate: Mapping[str, np.ndarray]) -> Mapping[str, np.ndarray]:
    frozen: dict[str, np.ndarray] = {}
    for key, value in candidate.items():
        array = np.asarray(value)
        array.setflags(write=False)
        frozen[key] = array
    return MappingProxyType(frozen)


def _record_cache_lookup(
    cache_stats: dict[str, int],
    name: str,
    lookup: CacheLookup[Any],
) -> None:
    metric = "hit" if lookup.hit else "miss"
    cache_stats[f"{name}_{metric}"] += 1


def _empty_cache_stats() -> dict[str, int]:
    return {
        "candidate_hit": 0,
        "candidate_miss": 0,
        "geometry_hit": 0,
        "geometry_miss": 0,
        "dem_hit": 0,
        "dem_miss": 0,
    }


def _qc_artifact_digest(objects: Mapping[str, bytes]) -> str:
    digest = hashlib.sha256()
    for key, value in sorted(objects.items()):
        encoded = key.encode("utf-8")
        digest.update(len(encoded).to_bytes(4, "big"))
        digest.update(encoded)
        digest.update(len(value).to_bytes(8, "big"))
        digest.update(hashlib.sha256(value).digest())
    return digest.hexdigest()


def _hybrid_profile_digest(
    profile: RadarGridProfile,
    flag_masks: Mapping[str, np.uint32],
) -> str:
    payload = {
        "algorithm_version": profile.algorithm_version,
        "ancillary_config_version": profile.ancillary_config_version,
        "beam_geometry": {
            "earth_radius_m": profile.beam_geometry.earth_radius_m,
            "effective_earth_radius_factor": (profile.beam_geometry.effective_earth_radius_factor),
        },
        "blockage": {
            "flag_fraction": profile.blockage.flag_fraction,
            "maximum_usable_fraction": profile.blockage.maximum_usable_fraction,
        },
        "flags": {key: int(value) for key, value in sorted(flag_masks.items())},
        "hybrid_scan": {
            "beam_height_quality_scale_m": profile.hybrid_scan.beam_height_quality_scale_m,
            "low_quality_threshold": profile.hybrid_scan.low_quality_threshold,
            "maximum_beam_height_agl_m": profile.hybrid_scan.maximum_beam_height_agl_m,
            "minimum_source_quality_index": (profile.hybrid_scan.minimum_source_quality_index),
            "reject_flags": list(profile.hybrid_scan.reject_flags),
        },
    }
    return hashlib.sha256(
        json.dumps(payload, sort_keys=True, separators=(",", ":")).encode("utf-8")
    ).hexdigest()


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
    required_flags = {
        "MISSING",
        "HARDWARE_ANOMALY",
        "BEAM_BLOCKED",
        "LOW_QUALITY",
        *profile.hybrid_scan.reject_flags,
    }
    missing_flags = sorted(required_flags - flag_masks.keys())
    if missing_flags:
        raise RadarGridInputError(
            "flag definition is missing Hybrid Scan flags: " + ",".join(missing_flags)
        )


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
        *UPSTREAM_QI_COMPONENTS,
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
    output[supported] = values[mapping.ray_index[supported], mapping.gate_index[supported]]
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
    source_components = {
        name.lower(): _map(group[name][:], mapping).astype("float32")
        for name in UPSTREAM_QI_COMPONENTS
    }
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
    if profile.flag_definition_version == "qc-flags-v2":
        for mask_name in ("REFLECTIVITY_TRUST_MASK", "QPE_ELIGIBLE_MASK"):
            if mask_name not in group:
                raise ValueError("open-source QC product is missing quantitative trust masks")
            source_valid &= _map(group[mask_name][:], mapping) == 1
    usable = (
        finite
        & (source_valid == 1)
        & ((source_flags & reject_mask) == 0)
        & (source_quality >= profile.hybrid_scan.minimum_source_quality_index)
        & (blockage <= profile.blockage.maximum_usable_fraction)
        & (beam_agl >= 0)
        & (beam_agl <= profile.hybrid_scan.maximum_beam_height_agl_m)
    )
    severe = finite & (source_valid == 1) & (blockage > profile.blockage.maximum_usable_fraction)
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
        **source_components,
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
        **{name: name.lower() for name in UPSTREAM_QI_COMPONENTS},
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
