from __future__ import annotations

import io
import json
import os
from pathlib import Path
from urllib.parse import unquote, urlparse
from uuid import NAMESPACE_URL, uuid5

import numpy as np
import zarr
from minio import Minio
from minio.error import S3Error
from pyproj import Geod
from zarr.storage import MemoryStore

from rainpulse_algo.worker.domain_contracts import RadarQCRequested
from rainpulse_algo.worker.object_store import (
    ArtifactObjectReader,
    minio_client_from_environment,
    parse_s3_uri,
)
from rainpulse_algo.worker.runtime import WorkerResult

from .qc import (
    BasicQCProfile,
    QCConfigError,
    QCInputError,
    _cross_radar_consistency_by_ray,
    _nearest_azimuth_indices,
    _nearest_coordinate_indices,
    _temporal_radial_persistence,
    apply_basic_qc,
    load_qc_profile,
)
from .qc_zarr import build_qc_zarr_store, validate_qc_zarr_store


def execute_basic_qc(request: RadarQCRequested) -> WorkerResult:
    return _execute_basic_qc(request, minio_client_from_environment())


def _execute_basic_qc(request: RadarQCRequested, client: Minio) -> WorkerResult:
    profile = load_qc_profile(
        _required_file("RAINPULSE_RADAR_QC_CONFIG"),
        _required_file("RAINPULSE_QC_FLAG_DEFINITIONS"),
    )
    _validate_request_versions(request, profile)
    normalized = ArtifactObjectReader(client).load(request.payload.input_uri)
    ancillary = _load_ancillary_maps(profile, client)
    radial_context, context_provenance = _load_radial_context(
        request,
        normalized,
        profile,
        client,
    )
    result = apply_basic_qc(
        normalized,
        profile,
        ancillary_maps=ancillary,
        radial_context=radial_context,
    )
    result.summary["radial_context"] = context_provenance
    qc_asset_id = uuid5(NAMESPACE_URL, f"rainpulse:qc-asset:{request.job_id}")
    objects = build_qc_zarr_store(
        normalized,
        result,
        asset_id=qc_asset_id,
        normalized_volume_uri=request.payload.input_uri,
        provenance={
            "scan_id": str(request.payload.scan_id),
            "run_id": str(request.run_id),
            "job_id": str(request.job_id),
            "trace_id": str(request.trace_id),
            "radial_context": json.dumps(context_provenance, sort_keys=True),
        },
    )
    validation = validate_qc_zarr_store(objects)
    summary = result.summary
    return WorkerResult(
        objects=objects,
        diagnostics={"radar_qc": summary},
        metrics={
            "output_size_bytes": float(validation["size_bytes"]),
            "zarr_object_count": float(validation["object_count"]),
            "sweep_count": float(validation["sweep_count"]),
            "ray_count": float(validation["ray_count"]),
            "valid_gate_count": float(validation["valid_gate_count"]),
            "missing_gate_count": float(validation["missing_gate_count"]),
            "low_quality_gate_count": float(summary["low_quality_gate_count"]),
            "mean_quality_index": float(summary["mean_quality_index"]),
            "radial_interference_ray_count": float(
                summary["radial_interference_ray_count"]
            ),
            "radial_interference_gate_count": float(
                summary["radial_interference_gate_count"]
            ),
            "radial_interference_area_km2": float(
                summary["radial_interference_area_km2"]
            ),
            "radial_temporal_context_volume_count": float(
                context_provenance["temporal_available_count"]
            ),
            "radial_cross_radar_context_volume_count": float(
                context_provenance["cross_radar_available_count"]
            ),
        },
    )


def _load_radial_context(
    request: RadarQCRequested,
    normalized: dict[str, bytes],
    profile: BasicQCProfile,
    client: Minio,
) -> tuple[dict[str, dict[str, np.ndarray]] | None, dict[str, int]]:
    fusion = profile.radial_interference.morphology.context_fusion
    provenance = {
        "temporal_requested_count": len(request.payload.temporal_context),
        "temporal_available_count": 0,
        "cross_radar_requested_count": len(request.payload.cross_radar_context),
        "cross_radar_available_count": 0,
        "temporal_supported_sweep_count": 0,
        "cross_radar_supported_sweep_count": 0,
    }
    if not fusion.enabled:
        return None, provenance

    reader = ArtifactObjectReader(client)
    temporal_candidates: list[dict[str, tuple[np.ndarray, np.ndarray]]] = []
    for input_ref in request.payload.temporal_context:
        try:
            objects = reader.load(input_ref.input_uri)
            candidates = _radial_candidates_by_sweep(objects, profile)
        # Context evidence is optional.  A missing/corrupt secondary artifact
        # must not prevent the current volume from completing its primary QC.
        except (OSError, RuntimeError, S3Error, QCInputError, QCConfigError, ValueError):
            continue
        temporal_candidates.append(candidates)
    provenance["temporal_available_count"] = len(temporal_candidates)

    cross_roots: list[zarr.Group] = []
    for input_ref in request.payload.cross_radar_context:
        try:
            objects = reader.load(input_ref.input_uri)
            root = _normalized_root(objects)
        # Keep cross-radar input best-effort for the same reason as temporal
        # evidence: it can influence a weak candidate but cannot own the job.
        except (OSError, RuntimeError, S3Error, QCInputError, ValueError):
            continue
        if str(root.attrs.get("radar_id", "")).lower() == request.payload.radar_id.lower():
            continue
        if str(root.attrs.get("radar_id", "")).lower() != input_ref.radar_id.lower():
            continue
        cross_roots.append(root)
    provenance["cross_radar_available_count"] = len(cross_roots)

    if not temporal_candidates and not cross_roots:
        return None, provenance
    current_root = _normalized_root(normalized)
    context: dict[str, dict[str, np.ndarray]] = {}
    for sweep_number in current_root["sweep_number"][:]:
        name = f"sweep_{int(sweep_number):03d}"
        group = current_root[name]
        sweep_context: dict[str, np.ndarray] = {}
        temporal_inputs = [
            values[name]
            for values in temporal_candidates
            if name in values
        ]
        if temporal_inputs:
            persistence = _temporal_radial_persistence(
                group["azimuth"][:],
                tuple(temporal_inputs),
                minimum_context_scans=fusion.minimum_temporal_context_scans,
                maximum_context_scans=fusion.maximum_temporal_context_scans,
            )
            sweep_context["temporal_persistence"] = persistence
            if np.any(np.isfinite(persistence)):
                provenance["temporal_supported_sweep_count"] += 1

        if "DBZH" in group and cross_roots:
            dbzh = group["DBZH"][:].astype("float32", copy=False)
            neighbours = tuple(
                _reproject_neighbour_to_current_polar(current_root, group, root)
                for root in cross_roots
            )
            consistency = _cross_radar_consistency_by_ray(
                dbzh,
                np.isfinite(dbzh),
                neighbours,
                echo_threshold_dbzh=fusion.cross_radar_echo_threshold_dbzh,
                minimum_overlap_gates=fusion.minimum_cross_radar_overlap_gates,
            )
            sweep_context["cross_radar_consistency"] = consistency
            if np.any(np.isfinite(consistency)):
                provenance["cross_radar_supported_sweep_count"] += 1
        if sweep_context:
            context[name] = sweep_context
    return context or None, provenance


def _radial_candidates_by_sweep(
    normalized: dict[str, bytes],
    profile: BasicQCProfile,
) -> dict[str, tuple[np.ndarray, np.ndarray]]:
    result = apply_basic_qc(normalized, profile)
    root = _normalized_root(normalized)
    threshold = profile.radial_interference.morphology.diagnostic_probability
    return {
        sweep.name: (
            root[sweep.name]["azimuth"][:].astype("float32", copy=False),
            np.any(
                np.nan_to_num(sweep.p_radial_interference, nan=0.0) >= threshold,
                axis=1,
            ),
        )
        for sweep in result.sweeps
    }


def _normalized_root(objects: dict[str, bytes]) -> zarr.Group:
    store = MemoryStore()
    store.update(objects)
    root = zarr.open_group(store=store, mode="r")
    if root.attrs.get("contract_name") != "rainpulse.normalized-radar-volume":
        raise QCInputError("radial context input is not a normalized radar volume")
    return root


def _reproject_neighbour_to_current_polar(
    current_root: zarr.Group,
    current_group: zarr.Group,
    neighbour_root: zarr.Group,
) -> np.ndarray:
    current_shape = (len(current_group["azimuth"]), len(current_group["range"]))
    result = np.full(current_shape, np.nan, dtype="float32")
    neighbour_group = _nearest_elevation_sweep(current_group, neighbour_root)
    if neighbour_group is None or "DBZH" not in neighbour_group:
        return result
    try:
        current_lon = float(current_root.attrs["site_longitude_deg"])
        current_lat = float(current_root.attrs["site_latitude_deg"])
        neighbour_lon = float(neighbour_root.attrs["site_longitude_deg"])
        neighbour_lat = float(neighbour_root.attrs["site_latitude_deg"])
    except (KeyError, TypeError, ValueError):
        return result
    if not all(
        np.isfinite(item)
        for item in (current_lon, current_lat, neighbour_lon, neighbour_lat)
    ):
        return result

    azimuth = current_group["azimuth"][:].astype("float64", copy=False)
    ranges = current_group["range"][:].astype("float64", copy=False)
    azimuth_grid = np.broadcast_to(azimuth[:, None], current_shape)
    range_grid = np.broadcast_to(ranges[None, :], current_shape)
    geod = Geod(ellps="WGS84")
    longitude, latitude, _ = geod.fwd(
        np.full(azimuth_grid.size, current_lon),
        np.full(azimuth_grid.size, current_lat),
        azimuth_grid.ravel(),
        range_grid.ravel(),
    )
    neighbour_azimuth = neighbour_group["azimuth"][:].astype("float64", copy=False)
    neighbour_ranges = neighbour_group["range"][:].astype("float64", copy=False)
    if neighbour_azimuth.size == 0 or neighbour_ranges.size == 0:
        return result
    forward, _, distance = geod.inv(
        np.full(longitude.size, neighbour_lon),
        np.full(latitude.size, neighbour_lat),
        longitude,
        latitude,
    )
    nearest_rays = _nearest_azimuth_indices(np.mod(forward, 360.0), neighbour_azimuth)
    nearest_gates = _nearest_coordinate_indices(distance, neighbour_ranges)
    azimuth_offset = np.abs(
        (neighbour_azimuth[nearest_rays] - np.mod(forward, 360.0) + 180.0) % 360.0 - 180.0
    )
    ray_spacing = _median_circular_ray_spacing(neighbour_azimuth)
    gate_spacing = float(np.median(np.diff(neighbour_ranges))) if neighbour_ranges.size > 1 else 0.0
    supported = (
        np.isfinite(distance)
        & (distance >= neighbour_ranges[0])
        & (distance <= neighbour_ranges[-1])
        & (azimuth_offset <= max(0.5, ray_spacing * 0.75))
        & (np.abs(neighbour_ranges[nearest_gates] - distance) <= max(1.0, gate_spacing * 0.75))
    )
    values = neighbour_group["DBZH"][:].astype("float32", copy=False)
    flattened = result.ravel()
    flattened[supported] = values[nearest_rays[supported], nearest_gates[supported]]
    return result


def _nearest_elevation_sweep(
    current_group: zarr.Group,
    neighbour_root: zarr.Group,
) -> zarr.Group | None:
    target = float(np.nanmedian(current_group["elevation"][:]))
    candidates = [
        neighbour_root[f"sweep_{int(number):03d}"]
        for number in neighbour_root["sweep_number"][:]
    ]
    candidates = [item for item in candidates if "elevation" in item and "DBZH" in item]
    if not candidates:
        return None
    return min(
        candidates,
        key=lambda item: abs(float(np.nanmedian(item["elevation"][:])) - target),
    )


def _median_circular_ray_spacing(azimuth: np.ndarray) -> float:
    values = np.sort(np.mod(np.asarray(azimuth, dtype="float64"), 360.0))
    if values.size < 2:
        return 0.5
    spacing = np.diff(np.concatenate((values, values[:1] + 360.0)))
    finite = spacing[np.isfinite(spacing) & (spacing > 0)]
    return float(np.median(finite)) if finite.size else 0.5


def _validate_request_versions(
    request: RadarQCRequested,
    profile: BasicQCProfile,
) -> None:
    payload = request.payload
    expected = (
        ("qc_profile", payload.qc_profile, profile.profile_version),
        ("qc_pipeline_version", payload.qc_pipeline_version, profile.pipeline_version),
        (
            "flag_definition_version",
            payload.flag_definition_version,
            profile.flag_definition_version,
        ),
    )
    for name, requested, configured in expected:
        if requested != configured:
            raise QCConfigError(f"requested {name} differs from the mounted QC profile")


def _load_ancillary_maps(
    profile: BasicQCProfile,
    client: Minio,
) -> dict[str, dict[str, np.ndarray]] | None:
    values: dict[str, dict[str, np.ndarray]] = {}
    if profile.static_ground_clutter.asset_uri:
        _merge_npz(values, _load_npz(profile.static_ground_clutter.asset_uri, client))
    if profile.sea_ap.coastline_asset_uri:
        _merge_npz(values, _load_npz(profile.sea_ap.coastline_asset_uri, client))
    return values or None


def _load_npz(uri: str, client: Minio) -> dict[str, np.ndarray]:
    parsed = urlparse(uri)
    if parsed.scheme == "s3":
        bucket, key = parse_s3_uri(uri)
        response = client.get_object(bucket, key)
        try:
            data = response.read()
        finally:
            response.close()
            response.release_conn()
    elif parsed.scheme == "file":
        path = Path(unquote(parsed.path)).resolve(strict=True)
        roots = _required_roots("RAINPULSE_QC_ASSET_ROOTS")
        if not any(path == root or root in path.parents for root in roots):
            raise QCConfigError("QC ancillary file is outside the configured roots")
        data = path.read_bytes()
    else:
        raise QCConfigError(f"unsupported QC ancillary URI {uri!r}")
    with np.load(io.BytesIO(data), allow_pickle=False) as archive:
        return {name: archive[name].astype("float32") for name in archive.files}


def _merge_npz(
    target: dict[str, dict[str, np.ndarray]],
    arrays: dict[str, np.ndarray],
) -> None:
    for key, values in arrays.items():
        sweep, separator, field = key.partition("__")
        if not separator or not sweep.startswith("sweep_") or field not in {
            "ground_clutter",
            "sea_clutter",
            "ap",
        }:
            raise QCConfigError(f"invalid QC ancillary array key {key!r}")
        target.setdefault(sweep, {})[field] = values


def _required_file(name: str) -> Path:
    value = os.getenv(name)
    if not value:
        raise QCConfigError(f"{name} is required")
    path = Path(value).resolve(strict=True)
    if not path.is_file():
        raise QCConfigError(f"{name} must identify a file")
    return path


def _required_roots(name: str) -> tuple[Path, ...]:
    value = os.getenv(name)
    if not value:
        raise QCConfigError(f"{name} is required for file ancillary assets")
    return tuple(Path(item).resolve(strict=True) for item in value.split(os.pathsep) if item)
