from __future__ import annotations

import io
import json
import os
import resource
import sys
import time
from datetime import UTC, datetime
from pathlib import Path
from typing import Any
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
    artifact_sha256,
    minio_client_from_environment,
    parse_s3_uri,
)
from rainpulse_algo.worker.runtime import WorkerResult

from .ancillary import load_source
from .attenuation import AttenuationProfile, load_attenuation_profile
from .config import load_radar_config
from .dem import VerifiedDEMTileStore
from .phase_processing import PhaseProcessingProfile, load_phase_processing_profile
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
from .qc_context import (
    RadialCandidateSweep,
    comparison_digest_for_sweep,
    extract_radial_candidate_sweeps,
    volume_geometry_digest,
)
from .qc_geometry import (
    CROSS_RADAR_TRUSTED_AVAILABLE_MASK_FIELD,
    CROSS_RADAR_TRUSTED_SUPPORT_FIELD,
    CrossRadarSupportReference,
    RadarBeamContext,
    build_trusted_cross_radar_support,
    radar_beam_context_from_config,
)
from .qc_zarr import build_validated_qc_zarr_store


class RadarQCStageError(RuntimeError):
    def __init__(self, stage: str, observability: dict[str, Any], cause: Exception) -> None:
        super().__init__(str(cause))
        self.stage = stage
        self.observability = dict(observability)


def execute_basic_qc(request: RadarQCRequested) -> WorkerResult:
    return _execute_basic_qc(request, minio_client_from_environment())


def _execute_basic_qc(request: RadarQCRequested, client: Minio) -> WorkerResult:
    profile = load_qc_profile(
        _required_file("RAINPULSE_RADAR_QC_CONFIG"),
        _required_file("RAINPULSE_QC_FLAG_DEFINITIONS"),
    )
    if getattr(profile, "engine", None) != "open_source":
        _load_shadow_runtime_profiles()  # Validate only the selected engine's runtime.
    _validate_request_versions(request, profile)
    observability: dict[str, Any] = {
        "input_read_ms": 0.0,
        "context_ms": 0.0,
        "qc_core_ms": 0.0,
        "serialize_validate_ms": 0.0,
        "input_bytes": 0,
        "output_bytes": 0,
        "object_count": 0,
        "context_age_seconds": 0.0,
        "cache_hit": 0,
        "cache_miss": 0,
        "rss_bytes": _process_rss_bytes(),
    }

    input_started = time.perf_counter()
    try:
        normalized = ArtifactObjectReader(client).load(request.payload.input_uri)
    except Exception as error:  # noqa: BLE001 - attach stage observability
        observability["input_read_ms"] = _elapsed_ms(input_started)
        observability["rss_bytes"] = _process_rss_bytes()
        raise RadarQCStageError("input_read", observability, error) from error
    observability["input_read_ms"] = _elapsed_ms(input_started)
    observability["input_bytes"] = sum(len(value) for value in normalized.values())

    context_started = time.perf_counter()
    try:
        prepared, context_provenance = prepare_qc_inputs(request, normalized, profile, client)
    except Exception as error:  # noqa: BLE001 - attach stage observability
        observability["context_ms"] = _elapsed_ms(context_started)
        observability["rss_bytes"] = _process_rss_bytes()
        raise RadarQCStageError("context", observability, error) from error
    observability["context_ms"] = _elapsed_ms(context_started)
    observability["context_age_seconds"] = float(context_provenance.get("context_age_seconds", 0.0))

    qc_started = time.perf_counter()
    try:
        result = apply_basic_qc(
            normalized,
            profile,
            **prepared,
            **(
                {"created_at": request.occurred_at}
                if getattr(profile, "engine", None) == "open_source"
                else {}
            ),
        )
    except Exception as error:  # noqa: BLE001 - attach stage observability
        observability["qc_core_ms"] = _elapsed_ms(qc_started)
        observability["rss_bytes"] = _process_rss_bytes()
        raise RadarQCStageError("qc_core", observability, error) from error
    observability["qc_core_ms"] = _elapsed_ms(qc_started)

    result.summary["radial_context"] = context_provenance
    qc_asset_id = uuid5(NAMESPACE_URL, f"rainpulse:qc-asset:{request.job_id}")
    serialize_started = time.perf_counter()
    try:
        objects, validation = build_validated_qc_zarr_store(
            normalized,
            result,
            asset_id=qc_asset_id,
            normalized_volume_uri=request.payload.input_uri,
            provenance={
                "scan_id": str(request.payload.scan_id),
                "run_id": str(request.run_id),
                "job_id": str(request.job_id),
                "trace_id": str(request.trace_id),
                "context_fingerprint": str(context_provenance["context_fingerprint"]),
                "radial_context": json.dumps(context_provenance, sort_keys=True),
            },
        )
    except Exception as error:  # noqa: BLE001 - attach stage observability
        observability["serialize_validate_ms"] = _elapsed_ms(serialize_started)
        observability["rss_bytes"] = _process_rss_bytes()
        raise RadarQCStageError("serialize_validate", observability, error) from error
    observability["serialize_validate_ms"] = _elapsed_ms(serialize_started)
    observability["output_bytes"] = int(validation["size_bytes"])
    observability["object_count"] = int(validation["object_count"])
    observability["rss_bytes"] = _process_rss_bytes()

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
            "radial_interference_ray_count": float(summary["radial_interference_ray_count"]),
            "radial_interference_gate_count": float(summary["radial_interference_gate_count"]),
            **(
                {"radial_interference_area_km2": float(summary["radial_interference_area_km2"])}
                if summary["radial_interference_area_km2"] is not None
                else {}
            ),
            "radial_temporal_context_volume_count": float(
                context_provenance["temporal_available_count"]
            ),
            "radial_cross_radar_context_volume_count": float(
                context_provenance["cross_radar_available_count"]
            ),
        },
        observability=observability,
    )


def prepare_qc_inputs(request, normalized, profile, client, *, reader=None, ancillary_maps=None):
    """One preparation path for online QC and frozen scientific replay."""
    if getattr(profile, "engine", None) == "open_source":
        from .qc_engine.context import prepare_open_source_inputs

        return prepare_open_source_inputs(
            request,
            normalized,
            profile,
            client,
            reader=reader,
            ancillary_maps=ancillary_maps,
        )
    from .qc_input import open_qc_input

    view = open_qc_input(normalized)
    ancillary = (
        ancillary_maps if ancillary_maps is not None else _load_ancillary_maps(profile, client)
    )
    beam, terrain, config_dir, dem_version = _load_qc_geometry_resources(request, profile)
    context, provenance = _load_radial_context(
        request,
        normalized,
        profile,
        client,
        current_beam_context=beam,
        terrain=terrain,
        radar_config_dir=config_dir,
        expected_dem_asset_version=dem_version,
        input_view=view,
        reader=reader,
    )
    phase, attenuation = _load_shadow_runtime_profiles()
    blockage = None
    if (
        attenuation is not None
        and beam is not None
        and terrain is not None
        and beam.altitude_datum_status == "verified_egm2008"
    ):
        from .blockage import calculate_polar_blockage
        from .qc_geometry import QC_GEOMETRY_BEAM_CONFIG, QC_GEOMETRY_BLOCKAGE_CONFIG

        blockage = {}
        for number in view.root["sweep_number"][:]:
            name = f"sweep_{int(number):03d}"
            group = view.root[name]
            result = calculate_polar_blockage(
                azimuth_deg=group["azimuth"][:],
                elevation_deg=group["elevation"][:],
                range_m=group["range"][:],
                required_max_gate=np.full(
                    len(group["azimuth"]), len(group["range"]) - 1, dtype="int32"
                ),
                radar_longitude_deg=beam.longitude_deg,
                radar_latitude_deg=beam.latitude_deg,
                antenna_altitude_m=beam.antenna_altitude_m,
                vertical_beam_width_deg=beam.beam_width_vertical_deg,
                beam_config=QC_GEOMETRY_BEAM_CONFIG,
                blockage_config=QC_GEOMETRY_BLOCKAGE_CONFIG,
                terrain=terrain,
            )
            blockage[name] = np.where(result.support_mask == 1, result.cumulative, np.nan).astype(
                "float32"
            )
    return {
        "ancillary_maps": ancillary,
        "radial_context": context,
        "radar_beam_context": beam,
        "input_view": view,
        "phase_processing_profile": phase,
        "attenuation_profile": attenuation,
        "blockage_by_sweep": blockage,
    }, provenance


def _load_radial_context(
    request: RadarQCRequested,
    normalized: dict[str, bytes],
    profile: BasicQCProfile,
    client: Minio,
    *,
    current_beam_context: RadarBeamContext | None = None,
    terrain: VerifiedDEMTileStore | None = None,
    radar_config_dir: Path | None = None,
    expected_dem_asset_version: str | None = None,
    input_view=None,
    reader=None,
) -> tuple[dict[str, dict[str, np.ndarray]] | None, dict[str, Any]]:
    fusion = profile.radial_interference.morphology.context_fusion
    current_root = input_view.root if input_view is not None else _normalized_root(normalized)
    current_end_time = _required_utc_time(
        current_root.attrs.get("volume_end_time_utc"),
        field_name="current radar volume end time",
    )
    provenance: dict[str, Any] = {
        "temporal_requested_count": len(request.payload.temporal_context),
        "temporal_available_count": 0,
        "temporal_used_count": 0,
        "cross_radar_requested_count": len(request.payload.cross_radar_context),
        "cross_radar_available_count": 0,
        "cross_radar_used_count": 0,
        "temporal_supported_sweep_count": 0,
        "cross_radar_supported_sweep_count": 0,
        "vertical_geometry_verified": bool(
            current_beam_context is not None
            and current_beam_context.altitude_datum_status == "verified_egm2008"
        ),
        "cross_radar_trusted_enabled": bool(profile.decision_version == "evidence-v2"),
        "cross_radar_trusted_reference_count": 0,
        "artifacts": [
            _context_provenance_entry(
                role="current",
                requested_radar_id=request.payload.radar_id,
                input_uri=request.payload.input_uri,
                objects=normalized,
                root=current_root,
                used=True,
            )
        ],
    }
    if not fusion.enabled:
        provenance["context_fingerprint"] = _radial_context_fingerprint(
            provenance["artifacts"],
            profile,
        )
        provenance["context_age_seconds"] = 0.0
        return None, provenance

    reader = reader or ArtifactObjectReader(client)
    temporal_candidates: list[dict[str, RadialCandidateSweep]] = []
    temporal_entries: list[dict[str, Any]] = []
    seen_temporal = {request.payload.input_uri}
    seen_temporal_scans = {str(request.payload.scan_id)}
    for input_ref in request.payload.temporal_context:
        entry = _context_provenance_entry(
            role="temporal",
            requested_radar_id=input_ref.radar_id,
            input_uri=input_ref.input_uri,
        )
        if input_ref.input_uri in seen_temporal:
            entry["skip_reason"] = "duplicate_temporal_input"
            provenance["artifacts"].append(entry)
            continue
        seen_temporal.add(input_ref.input_uri)
        try:
            objects = reader.load(input_ref.input_uri)
            root = _normalized_root(objects)
            entry.update(_verified_context_artifact_fields(objects, root))
            scan_identity = str(root.attrs.get("scan_id", ""))
            if not scan_identity or scan_identity in seen_temporal_scans:
                entry["skip_reason"] = "duplicate_or_missing_temporal_scan"
                provenance["artifacts"].append(entry)
                continue
            seen_temporal_scans.add(scan_identity)
            skip_reason = _validate_temporal_context_artifact(
                root,
                request=request,
                current_end_time=current_end_time,
                profile=profile,
            )
            if skip_reason is not None:
                entry["skip_reason"] = skip_reason
                provenance["artifacts"].append(entry)
                continue
            candidates = {
                sweep.comparison_digest: sweep
                for sweep in _radial_candidates_by_sweep(objects, profile).values()
            }
        # Context evidence is optional.  A missing/corrupt secondary artifact
        # must not prevent the current volume from completing its primary QC.
        except (OSError, RuntimeError, S3Error, QCInputError, QCConfigError, ValueError):
            entry["skip_reason"] = "artifact_unavailable"
            provenance["artifacts"].append(entry)
            continue
        entry["skip_reason"] = "no_comparable_sweep"
        temporal_entries.append(entry)
        temporal_candidates.append(candidates)
        provenance["artifacts"].append(entry)
    provenance["temporal_available_count"] = len(temporal_candidates)

    cross_roots: list[zarr.Group] = []
    cross_support_references: list[CrossRadarSupportReference | None] = []
    cross_entries: list[dict[str, Any]] = []
    seen_cross_uris = {request.payload.input_uri}
    seen_cross_radars = {request.payload.radar_id.lower()}
    for input_ref in request.payload.cross_radar_context:
        entry = _context_provenance_entry(
            role="cross_radar",
            requested_radar_id=input_ref.radar_id,
            input_uri=input_ref.input_uri,
        )
        if (
            input_ref.input_uri in seen_cross_uris
            or input_ref.radar_id.lower() in seen_cross_radars
        ):
            entry["skip_reason"] = "duplicate_cross_radar_input"
            provenance["artifacts"].append(entry)
            continue
        seen_cross_uris.add(input_ref.input_uri)
        seen_cross_radars.add(input_ref.radar_id.lower())
        try:
            objects = reader.load(input_ref.input_uri)
            root = _normalized_root(objects)
            entry.update(_verified_context_artifact_fields(objects, root))
        # Keep cross-radar input best-effort for the same reason as temporal
        # evidence: it can influence a weak candidate but cannot own the job.
        except (OSError, RuntimeError, S3Error, QCInputError, ValueError):
            entry["skip_reason"] = "artifact_unavailable"
            provenance["artifacts"].append(entry)
            continue
        skip_reason = _validate_cross_radar_context_artifact(
            root,
            request=request,
            requested_radar_id=input_ref.radar_id,
            current_end_time=current_end_time,
            profile=profile,
        )
        if skip_reason is not None:
            entry["skip_reason"] = skip_reason
            provenance["artifacts"].append(entry)
            continue
        entry["skip_reason"] = "no_comparable_sweep"
        cross_roots.append(root)
        cross_support_references.append(
            _load_cross_radar_support_reference(
                objects,
                root,
                profile,
                radar_config_dir=radar_config_dir,
                expected_dem_asset_version=expected_dem_asset_version,
            )
        )
        cross_entries.append(entry)
        provenance["artifacts"].append(entry)
    provenance["cross_radar_available_count"] = len(cross_roots)
    provenance["cross_radar_trusted_reference_count"] = sum(
        1
        for reference in cross_support_references
        if reference is not None
        and reference.beam_context is not None
        and reference.beam_context.altitude_datum_status == "verified_egm2008"
        and reference.health_available
        and reference.dem_compatible
    )

    if not temporal_candidates and not cross_roots:
        provenance["context_fingerprint"] = _radial_context_fingerprint(
            provenance["artifacts"],
            profile,
        )
        provenance["context_age_seconds"] = 0.0
        return None, provenance
    context: dict[str, dict[str, np.ndarray]] = {}
    for sweep_number in current_root["sweep_number"][:]:
        name = f"sweep_{int(sweep_number):03d}"
        group = current_root[name]
        comparison_digest = comparison_digest_for_sweep(group)
        sweep_context: dict[str, np.ndarray] = {}
        temporal_inputs = []
        for index, values in enumerate(temporal_candidates):
            sweep = values.get(comparison_digest)
            if sweep is None:
                continue
            temporal_entries[index]["used"] = True
            temporal_entries[index]["skip_reason"] = None
            temporal_inputs.append(
                (
                    sweep.azimuth_deg,
                    sweep.candidate_mask_by_ray,
                    sweep.observed_mask_by_ray,
                )
            )
        if temporal_inputs:
            persistence = _temporal_radial_persistence(
                group["azimuth"][:],
                tuple(temporal_inputs),
                minimum_context_scans=fusion.minimum_temporal_context_scans,
                maximum_context_scans=fusion.maximum_temporal_context_scans,
                maximum_azimuth_offset_deg=(
                    0.75 if profile.decision_version == "evidence-v2" else None
                ),
            )
            sweep_context["temporal_persistence"] = persistence
            if np.any(np.isfinite(persistence)):
                provenance["temporal_supported_sweep_count"] += 1

        if "DBZH" in group and cross_roots:
            dbzh = group["DBZH"][:].astype("float32", copy=False)
            if profile.decision_version == "evidence-v2":
                trusted_references = [
                    (index, reference)
                    for index, reference in enumerate(cross_support_references)
                    if reference is not None
                ]
                if current_beam_context is not None and terrain is not None and trusted_references:
                    diagnostics = build_trusted_cross_radar_support(
                        {
                            "dbzh": dbzh,
                            "azimuth": group["azimuth"][:],
                            "range": group["range"][:],
                            "elevation": group["elevation"][:],
                        },
                        current_beam_context,
                        tuple(reference for _, reference in trusted_references),
                        terrain=terrain,
                        echo_threshold_dbzh=fusion.cross_radar_echo_threshold_dbzh,
                        minimum_overlap_gates=fusion.minimum_cross_radar_overlap_gates,
                        valid_range_dbz=profile.echo.dbzh_valid_range_dbz,
                    )
                    for used, (index, _reference) in zip(
                        diagnostics.reference_used_mask,
                        trusted_references,
                        strict=True,
                    ):
                        if used:
                            cross_entries[index]["used"] = True
                            cross_entries[index]["skip_reason"] = None
                    consistency = diagnostics.consistency_by_ray
                    sweep_context[CROSS_RADAR_TRUSTED_SUPPORT_FIELD] = diagnostics.support_fraction
                    sweep_context[CROSS_RADAR_TRUSTED_AVAILABLE_MASK_FIELD] = (
                        diagnostics.available_mask
                    )
                else:
                    consistency = np.full(dbzh.shape[0], np.nan, dtype="float32")
                    sweep_context[CROSS_RADAR_TRUSTED_SUPPORT_FIELD] = np.full(
                        dbzh.shape,
                        np.nan,
                        dtype="float32",
                    )
                    sweep_context[CROSS_RADAR_TRUSTED_AVAILABLE_MASK_FIELD] = np.zeros(
                        dbzh.shape,
                        dtype="uint8",
                    )
            else:
                reprojected_neighbours = []
                for index, root in enumerate(cross_roots):
                    neighbour = _reproject_neighbour_to_current_polar(current_root, group, root)
                    if np.any(np.isfinite(neighbour)):
                        cross_entries[index]["used"] = True
                        cross_entries[index]["skip_reason"] = None
                    reprojected_neighbours.append(neighbour)
                consistency = _cross_radar_consistency_by_ray(
                    dbzh,
                    np.isfinite(dbzh),
                    tuple(reprojected_neighbours),
                    echo_threshold_dbzh=fusion.cross_radar_echo_threshold_dbzh,
                    minimum_overlap_gates=fusion.minimum_cross_radar_overlap_gates,
                )
            sweep_context["cross_radar_consistency"] = consistency
            if np.any(np.isfinite(consistency)):
                provenance["cross_radar_supported_sweep_count"] += 1
        if sweep_context:
            context[name] = sweep_context
    provenance["temporal_used_count"] = sum(1 for entry in temporal_entries if bool(entry["used"]))
    provenance["cross_radar_used_count"] = sum(1 for entry in cross_entries if bool(entry["used"]))
    provenance["context_fingerprint"] = _radial_context_fingerprint(
        provenance["artifacts"],
        profile,
    )
    provenance["context_age_seconds"] = _context_age_seconds(
        current_end_time,
        provenance["artifacts"],
    )
    return context or None, provenance


def _radial_candidates_by_sweep(
    normalized: dict[str, bytes],
    profile: BasicQCProfile,
) -> dict[str, RadialCandidateSweep]:
    return extract_radial_candidate_sweeps(normalized, profile)


def _load_qc_geometry_resources(
    request: RadarQCRequested,
    profile: BasicQCProfile,
) -> tuple[
    RadarBeamContext | None,
    VerifiedDEMTileStore | None,
    Path | None,
    str | None,
]:
    if profile.decision_version not in {"evidence-v2", "type-specific-v1"}:
        return None, None, None, None
    radar_config_dir = _optional_directory("RAINPULSE_RADAR_CONFIG_DIR")
    if radar_config_dir is None:
        return None, None, None, None
    try:
        current_radar_config = load_radar_config(
            radar_config_dir / f"{request.payload.radar_id}.yaml"
        )
        if current_radar_config.radar_id.lower() != request.payload.radar_id.lower():
            return None, None, radar_config_dir, None
        current_beam_context = radar_beam_context_from_config(current_radar_config)
    except (OSError, ValueError):
        return None, None, radar_config_dir, None

    ancillary_config_path = _optional_file("RAINPULSE_ANCILLARY_CONFIG")
    ancillary_root = _optional_directory("RAINPULSE_ANCILLARY_ROOT")
    expected_dem_asset_version = current_radar_config.ancillary.get("dem_asset_version")
    if (
        ancillary_config_path is None
        or ancillary_root is None
        or not isinstance(expected_dem_asset_version, str)
        or not expected_dem_asset_version
    ):
        return current_beam_context, None, radar_config_dir, None
    try:
        ancillary_source = load_source(ancillary_config_path)
        terrain = VerifiedDEMTileStore(
            ancillary_source,
            ancillary_root,
            expected_asset_version=expected_dem_asset_version,
            expected_config_version=ancillary_source.config_version,
        )
    except (OSError, RuntimeError, ValueError):
        terrain = None
    return current_beam_context, terrain, radar_config_dir, expected_dem_asset_version


def _load_cross_radar_support_reference(
    objects: dict[str, bytes],
    root: zarr.Group,
    profile: BasicQCProfile,
    *,
    radar_config_dir: Path | None,
    expected_dem_asset_version: str | None,
) -> CrossRadarSupportReference | None:
    if radar_config_dir is None:
        return None
    radar_id = str(root.attrs.get("radar_id", "")).strip()
    if not radar_id:
        return None
    try:
        radar_config = load_radar_config(radar_config_dir / f"{radar_id}.yaml")
        beam_context = radar_beam_context_from_config(radar_config)
    except (OSError, ValueError):
        return None
    try:
        candidate_sweeps = _radial_candidates_by_sweep(objects, profile)
    except (RuntimeError, QCInputError, QCConfigError, ValueError):
        candidate_sweeps = {}
    return CrossRadarSupportReference(
        radar_id=radar_id,
        root=root,
        beam_context=beam_context,
        health_available=str(root.attrs.get("radar_health", "")).upper() in {"HEALTHY", "DEGRADED"},
        dem_compatible=(
            isinstance(expected_dem_asset_version, str)
            and expected_dem_asset_version
            and radar_config.ancillary.get("dem_asset_version") == expected_dem_asset_version
        ),
        hard_interference_by_sweep={
            name: np.asarray(sweep.hard_flag_by_ray, dtype=bool)
            for name, sweep in candidate_sweeps.items()
        },
    )


def _context_provenance_entry(
    *,
    role: str,
    requested_radar_id: str,
    input_uri: str,
    objects: dict[str, bytes] | None = None,
    root: zarr.Group | None = None,
    used: bool = False,
) -> dict[str, Any]:
    entry: dict[str, Any] = {
        "role": role,
        "requested_radar_id": requested_radar_id,
        "input_uri": input_uri,
        "used": used,
        "skip_reason": None,
    }
    if objects is not None and root is not None:
        entry.update(_verified_context_artifact_fields(objects, root))
    return entry


def _verified_context_artifact_fields(
    objects: dict[str, bytes],
    root: zarr.Group,
) -> dict[str, Any]:
    return {
        "artifact_sha256": artifact_sha256(objects),
        "scan_id": str(root.attrs.get("scan_id", "")),
        "radar_id": str(root.attrs.get("radar_id", "")),
        "volume_end_time_utc": _attribute_text(root.attrs.get("volume_end_time_utc")),
        "geometry_digest": volume_geometry_digest(root),
        "radar_health": str(root.attrs.get("radar_health", "")),
        "scan_completeness": _attribute_float(root.attrs.get("scan_completeness")),
    }


def _validate_temporal_context_artifact(
    root: zarr.Group,
    *,
    request: RadarQCRequested,
    current_end_time: datetime,
    profile: BasicQCProfile,
) -> str | None:
    actual_radar_id = str(root.attrs.get("radar_id", "")).strip().lower()
    if actual_radar_id != request.payload.radar_id.lower():
        return "temporal_radar_id_mismatch"
    if str(root.attrs.get("radar_health", "")).upper() == "UNAVAILABLE":
        return "radar_health_unavailable"
    try:
        end_time = _required_utc_time(
            root.attrs.get("volume_end_time_utc"),
            field_name="temporal context volume end time",
        )
    except QCInputError:
        return "invalid_volume_end_time"
    delta_seconds = (end_time - current_end_time).total_seconds()
    fusion = profile.radial_interference.morphology.context_fusion
    if fusion.temporal_selection_mode != "symmetric_offline" and delta_seconds >= 0:
        return "future_time_disallowed" if delta_seconds > 0 else "non_past_temporal_context"
    if abs(delta_seconds) > fusion.temporal_max_time_offset_seconds:
        return "time_out_of_window"
    return None


def _validate_cross_radar_context_artifact(
    root: zarr.Group,
    *,
    request: RadarQCRequested,
    requested_radar_id: str,
    current_end_time: datetime,
    profile: BasicQCProfile,
) -> str | None:
    actual_radar_id = str(root.attrs.get("radar_id", "")).strip().lower()
    if actual_radar_id == request.payload.radar_id.lower():
        return "cross_same_radar"
    if actual_radar_id != requested_radar_id.lower():
        return "cross_radar_id_mismatch"
    if str(root.attrs.get("radar_health", "")).upper() == "UNAVAILABLE":
        return "radar_health_unavailable"
    try:
        end_time = _required_utc_time(
            root.attrs.get("volume_end_time_utc"),
            field_name="cross-radar context volume end time",
        )
    except QCInputError:
        return "invalid_volume_end_time"
    fusion = profile.radial_interference.morphology.context_fusion
    delta_seconds = (end_time - current_end_time).total_seconds()
    if fusion.temporal_selection_mode != "symmetric_offline" and delta_seconds > 0:
        return "future_time_disallowed"
    if abs(delta_seconds) > fusion.cross_radar_max_time_offset_seconds:
        return "time_out_of_window"
    return None


def _required_utc_time(value: object, *, field_name: str) -> datetime:
    if not isinstance(value, str) or not value:
        raise QCInputError(f"{field_name} is missing")
    try:
        parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError as exc:
        raise QCInputError(f"{field_name} is invalid") from exc
    if parsed.tzinfo is None or parsed.utcoffset() is None:
        raise QCInputError(f"{field_name} must include UTC offset")
    return parsed.astimezone(UTC)


def _attribute_text(value: object) -> str | None:
    if value is None:
        return None
    return str(value)


def _attribute_float(value: object) -> float | None:
    try:
        numeric = float(value)
    except (TypeError, ValueError):
        return None
    if not np.isfinite(numeric):
        return None
    return numeric


def _elapsed_ms(started_tick: float) -> float:
    return round((time.perf_counter() - started_tick) * 1000, 3)


def _process_rss_bytes() -> int:
    rss = int(resource.getrusage(resource.RUSAGE_SELF).ru_maxrss)
    if sys.platform == "darwin":
        return rss
    return rss * 1024


def _radial_context_fingerprint(
    artifacts: list[dict[str, Any]],
    profile: BasicQCProfile,
) -> str:
    payload = {
        "qc_profile": profile.profile_version,
        "qc_pipeline_version": profile.pipeline_version,
        "decision_version": profile.decision_version,
        "flag_definition_version": profile.flag_definition_version,
        "temporal_selection_mode": (
            profile.radial_interference.morphology.context_fusion.temporal_selection_mode
        ),
        "temporal_max_time_offset_seconds": (
            profile.radial_interference.morphology.context_fusion.temporal_max_time_offset_seconds
        ),
        "cross_radar_max_time_offset_seconds": (
            profile.radial_interference.morphology.context_fusion.cross_radar_max_time_offset_seconds
        ),
        "artifacts": artifacts,
    }
    return artifact_sha256(
        {
            "radial_context.json": json.dumps(
                payload,
                sort_keys=True,
                separators=(",", ":"),
            ).encode()
        }
    )


def _context_age_seconds(current_end_time: datetime, artifacts: list[dict[str, Any]]) -> float:
    maximum_age = 0.0
    for artifact in artifacts:
        if artifact.get("role") == "current" or not bool(artifact.get("used")):
            continue
        raw_end_time = artifact.get("volume_end_time_utc")
        if not isinstance(raw_end_time, str) or not raw_end_time:
            continue
        try:
            context_end_time = _required_utc_time(
                raw_end_time,
                field_name="context volume end time",
            )
        except QCInputError:
            continue
        maximum_age = max(
            maximum_age,
            abs((current_end_time - context_end_time).total_seconds()),
        )
    return float(maximum_age)


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
        np.isfinite(item) for item in (current_lon, current_lat, neighbour_lon, neighbour_lat)
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
        neighbour_root[f"sweep_{int(number):03d}"] for number in neighbour_root["sweep_number"][:]
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
    if getattr(profile, "engine", None) == "open_source":
        import hashlib

        actual = hashlib.sha256(
            _required_file("RAINPULSE_RADAR_QC_CONFIG").read_bytes()
        ).hexdigest()
        if payload.qc_profile_sha256 != actual:
            raise QCConfigError("mounted open-source QC profile SHA256 differs from frozen task")
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
        if (
            not separator
            or not sweep.startswith("sweep_")
            or field
            not in {
                "ground_clutter",
                "sea_clutter",
                "ap",
            }
        ):
            raise QCConfigError(f"invalid QC ancillary array key {key!r}")
        target.setdefault(sweep, {})[field] = values


def _load_shadow_runtime_profiles() -> tuple[
    PhaseProcessingProfile | None, AttenuationProfile | None
]:
    phase_processing_path = _optional_file("RAINPULSE_RADAR_PHASE_PROCESSING_PROFILE")
    attenuation_path = _optional_file("RAINPULSE_RADAR_ATTENUATION_PROFILE")
    try:
        phase_processing_profile = (
            None
            if phase_processing_path is None
            else load_phase_processing_profile(phase_processing_path)
        )
        attenuation_profile = (
            None if attenuation_path is None else load_attenuation_profile(attenuation_path)
        )
    except ValueError as error:
        raise QCConfigError(str(error)) from error
    if (
        phase_processing_profile is not None
        and attenuation_profile is not None
        and attenuation_profile.source_phase_processing_profile_version
        != phase_processing_profile.profile_version
    ):
        raise QCConfigError(
            "attenuation profile source_phase_processing_profile_version differs "
            "from the selected phase-processing profile"
        )
    return phase_processing_profile, attenuation_profile


def _required_file(name: str) -> Path:
    value = os.getenv(name)
    if not value:
        raise QCConfigError(f"{name} is required")
    path = Path(value).resolve(strict=True)
    if not path.is_file():
        raise QCConfigError(f"{name} must identify a file")
    return path


def _optional_file(name: str) -> Path | None:
    value = os.getenv(name)
    if not value:
        return None
    path = Path(value).resolve(strict=True)
    if not path.is_file():
        raise QCConfigError(f"{name} must identify a file")
    return path


def _optional_directory(name: str) -> Path | None:
    value = os.getenv(name)
    if not value:
        return None
    path = Path(value).resolve(strict=True)
    if not path.is_dir():
        raise QCConfigError(f"{name} must identify a directory")
    return path


def _required_roots(name: str) -> tuple[Path, ...]:
    value = os.getenv(name)
    if not value:
        raise QCConfigError(f"{name} is required for file ancillary assets")
    return tuple(Path(item).resolve(strict=True) for item in value.split(os.pathsep) if item)
