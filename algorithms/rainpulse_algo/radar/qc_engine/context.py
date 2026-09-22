"""Frozen, causal context, based on independent observations (not neighbouring final jobs)."""

from __future__ import annotations

import hashlib
import json
from datetime import UTC, datetime

import numpy as np

from rainpulse_algo.worker.object_store import ArtifactObjectReader, artifact_sha256

from ..config import load_radar_config
from ..qc_geometry import (
    CrossRadarSupportReference,
    build_trusted_cross_radar_support,
    radar_beam_context_from_config,
)
from ..qc_input import open_qc_input
from .adapters import adapt_sweep
from .algorithms import library_evidence
from .decision import decide
from .fingerprints import context_arrays_identity
from .objects import radial_objects
from .radial import local_radial_candidates
from .temporal import aggregate_temporal_rfi
from .standalone_evidence import stage_a, aggregate_stage_a_temporal


def utc_time(value: str) -> datetime:
    parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    if parsed.tzinfo is None:
        raise ValueError("context times must be timezone-aware")
    return parsed.astimezone(UTC)


def validate_context_identity(root, item, *, role, current_root, cutoff, config, profile=None):
    if getattr(item, "scan_id", None) is not None and str(root.attrs.get("scan_id")) != str(
        item.scan_id
    ):
        raise ValueError("context scan identity differs from the committed task")
    if str(root.attrs.get("radar_id", "")).lower() != item.radar_id.lower():
        raise ValueError("context radar identity differs from the committed task")
    actual = utc_time(root.attrs["volume_end_time_utc"])
    claimed = getattr(item, "volume_end_time_utc", actual).astimezone(UTC)
    if actual != claimed:
        raise ValueError("context observation time differs from the committed task")
    current_end = utc_time(current_root.attrs["volume_end_time_utc"])
    near = getattr(profile, "volume_review", None)
    near = getattr(near, "clutter_fusion", None)
    near = getattr(near, "near_revision", None)
    strong = getattr(near, "strong_near", None)
    temporal = getattr(strong, "temporal_low_rho", None)
    boundary = bool(getattr(temporal, "retrospective_boundary_enabled", False))
    if (actual > current_end and not (boundary and role == "temporal")) or actual > cutoff:
        return "future_context_disallowed"
    if role == "temporal":
        if item.radar_id.lower() != str(current_root.attrs["radar_id"]).lower():
            raise ValueError("temporal context must be the same radar")
        if actual == current_end:
            return "temporal_context_not_strictly_past"
        age = abs((current_end - actual).total_seconds())
        limit = getattr(temporal, "maximum_boundary_age_seconds", config.max_age_seconds) if boundary else config.max_age_seconds
        if age > limit:
            return "context_too_old"
    else:
        if item.radar_id.lower() == str(current_root.attrs["radar_id"]).lower():
            raise ValueError("cross-radar context must be a different radar")
        if (current_end - actual).total_seconds() > config.max_cross_offset_seconds:
            return "cross_context_too_old"
    # Where ingest time is available it too must precede the frozen decision cut-off.
    # Without it the task manifest proves selection, but not retrospective ingest availability.
    available_at = root.attrs.get("ingest_available_at_utc")
    if available_at and utc_time(available_at) > cutoff:
        return "context_not_available_at_cutoff"
    return None


def prepare_open_source_inputs(
    request, normalized, profile, client, *, reader=None, ancillary_maps=None
):
    from ..qc_worker import _load_qc_geometry_resources

    view = open_qc_input(normalized)
    if profile.rfi_objects is not None:
        for field in ("scan_id", "radar_id", "radar_config_version"):
            if str(view.root.attrs.get(field)) != str(getattr(request.payload, field)):
                raise ValueError(f"current {field} differs from frozen QC task")
        if utc_time(view.root.attrs["volume_end_time_utc"]) > request.occurred_at.astimezone(UTC):
            raise ValueError("current observation is after the frozen decision cutoff")
    geometry_audit = {}
    audited = getattr(profile, "residual_repair", None) is not None
    beam, terrain, config_dir, dem_version = _load_qc_geometry_resources(
        request, profile, **({"audit": geometry_audit} if audited else {})
    )
    load = reader or ArtifactObjectReader(client)
    artifacts = [
        {"role": "current", "uri": request.payload.input_uri, "sha256": artifact_sha256(normalized)}
    ]
    unified = profile.evidence_graph is not None and profile.evidence_graph.unified_stage_a
    independent_records = []
    current_standalone = {}
    # Prepare local current evidence once for both temporal matching and the runner.
    current_priors = ancillary_maps or {}
    if unified and profile.static_ground_clutter.asset_uri and ancillary_maps is None:
        current_priors = _load_clutter(profile, client, view.root)
    if unified:
        for number in view.root["sweep_number"][:]:
            cut = f"sweep_{int(number):03d}"
            current_native = adapt_sweep(view.root, cut, profile)
            prior = current_priors.get(cut, {}).get("ground_clutter")
            if prior is not None:
                prior = prior[current_native.original_indices]
            current_standalone[cut] = stage_a(current_native, profile, prior)
    contexts: dict = {}
    counts = {"temporal_available_count": 0, "cross_radar_available_count": 0}
    references = []
    reference_entries = []
    cross_availability = {}
    temporal = []
    cutoff = request.occurred_at.astimezone(UTC)
    seen = {str(request.payload.scan_id)}
    seen_sources = {artifact_sha256(normalized)}
    seen_scans = {str(request.payload.scan_id)}
    if profile.context.enabled:
        for role, items in (
            ("temporal", request.payload.temporal_context),
            ("cross_radar", request.payload.cross_radar_context),
        ):
            maximum = profile.context.max_temporal_scans if role == "temporal" else 3
            if len(items) > maximum:
                raise ValueError("frozen context exceeds configured resource limit")
            for item in items:
                requested_identity = str(getattr(item, "scan_id", item.input_uri))
                if requested_identity in seen:
                    raise ValueError("duplicate context scan identity")
                seen.add(requested_identity)
                entry = {"role": role, "uri": item.input_uri}
                artifacts.append(entry)
                # Transport absence is a declared capability loss; a malformed or
                # mismatched committed object below is never silently accepted.
                try:
                    obj = load.load(item.input_uri)
                except (OSError, ConnectionError) as error:
                    entry.update(status="unavailable", reason=type(error).__name__)
                    continue
                context_root = open_qc_input(obj).root
                entry["sha256"] = artifact_sha256(obj)
                entry["scan_id"] = str(context_root.attrs.get("scan_id"))
                if entry["scan_id"] in seen_scans or entry["sha256"] in seen_sources:
                    raise ValueError("duplicate physical context observation")
                seen_scans.add(entry["scan_id"])
                seen_sources.add(entry["sha256"])
                reason = validate_context_identity(
                    context_root,
                    item,
                    role=role,
                    current_root=view.root,
                    cutoff=cutoff,
                    config=profile.context, profile=profile,
                )
                if reason:
                    entry.update(status="excluded", reason=reason)
                    continue
                health = json.loads(obj.get("health/summary.json", b"{}"))
                if health.get("health") not in {"HEALTHY", "DEGRADED"}:
                    entry.update(status="unavailable", reason="context_health_unavailable")
                    continue
                counts[f"{role}_available_count"] += 1
                entry.update(
                    status="available",
                    ingest_time_verified=bool(context_root.attrs.get("ingest_available_at_utc")),
                )
                first_pass = {}
                object_pass = {}
                for number in context_root["sweep_number"][:]:
                    name = f"sweep_{int(number):03d}"
                    sweep = adapt_sweep(context_root, name, profile)
                    if unified:
                        independent = stage_a(sweep, profile)
                        # Only uncertain GATES abstain; preserve unrelated healthy echoes.
                        first_pass[name] = sweep.restore(~independent.donor_usable)
                        object_pass[name] = (sweep, independent)
                        independent_records.append(
                            {"scan_id": entry["scan_id"], "sweep": name, **independent.summary}
                        )
                        continue
                    evidence = library_evidence(sweep, profile)
                    objects_evidence = (
                        radial_objects(sweep, profile.rfi_objects)
                        if profile.rfi_objects is not None
                        else None
                    )
                    if objects_evidence is None:
                        radial, _ = local_radial_candidates(sweep, profile.rfi)
                    else:
                        radial = objects_evidence.candidate
                        object_pass[name] = (sweep, objects_evidence)
                    result = decide(
                        sweep,
                        evidence,
                        profile,
                        rfi_candidate=radial,
                        object_evidence=objects_evidence,
                    )
                    # Withhold only uncertain gates; a noisy edge must not discard a clean ray.
                    untrusted = result.arrays["QC_ACTION"] != 0
                    first_pass[name] = sweep.restore(untrusted)
                if role == "temporal":
                    temporal.append((context_root, first_pass, object_pass))
                elif config_dir is not None:
                    try:
                        cfg_path = config_dir / f"{item.radar_id.lower()}.yaml"
                        config_bytes = cfg_path.read_bytes()
                        cfg = load_radar_config(cfg_path)
                        if cfg_path.read_bytes() != config_bytes:
                            raise ValueError("cross-radar config changed during preparation")
                        if cfg.radar_id.lower() != item.radar_id.lower():
                            raise ValueError("cross-radar config identity differs")
                    except (OSError, ValueError) as error:
                        if not audited:
                            raise
                        entry.update(
                            support_status="config_unavailable", error_type=type(error).__name__
                        )
                        continue
                    if audited:
                        entry.update(
                            geometry_config_sha256=hashlib.sha256(config_bytes).hexdigest(),
                            support_status="reference_prepared_not_yet_comparable",
                        )
                    reference_entries.append(entry)
                    references.append(
                        CrossRadarSupportReference(
                            item.radar_id,
                            context_root,
                            radar_beam_context_from_config(cfg),
                            True,
                            bool(
                                dem_version
                                and cfg.ancillary.get("dem_asset_version") == dem_version
                            ),
                            first_pass,
                        )
                    )
    for number in view.root["sweep_number"][:]:
        name = f"sweep_{int(number):03d}"
        group = view.root[name]
        if "DBZH" not in group:
            continue
        dbzh = group["DBZH"][:]
        cross = build_trusted_cross_radar_support(
            current_sweep={
                "dbzh": dbzh,
                "azimuth": group["azimuth"][:],
                "elevation": group["elevation"][:],
                "range": group["range"][:],
            },
            current_beam_context=beam,
            references=tuple(references),
            valid_range_dbz=profile.echo.dbzh_valid_range_dbz,
            echo_threshold_dbzh=profile.context.echo_threshold_dbz,
            minimum_overlap_gates=1,
            terrain=terrain,
        )
        if audited:
            cross_availability[name] = cross.availability_audit
            for entry, used in zip(reference_entries, cross.reference_used_mask, strict=True):
                entry.setdefault("comparable_sweeps", [])
                if used:
                    entry["comparable_sweeps"].append(name)
                entry["support_status"] = (
                    "comparable" if entry["comparable_sweeps"] else "no_comparable_gates"
                )
        contexts[name] = {
            "WEATHER_SUPPORT_SCORE": np.where(
                cross.available_mask == 1, cross.support_fraction, np.nan
            ).astype("float32")
        }
        if profile.rfi_objects is not None:
            current = adapt_sweep(view.root, name, profile)
            samples = [objects[name] for _, _, objects in temporal if name in objects]
            values = (
                aggregate_stage_a_temporal(current, samples)
                if unified
                else aggregate_temporal_rfi(current, samples)
            )
            contexts[name].update({key: current.restore(value) for key, value in values.items()})
            if profile.nonprecip_review is not None:
                from .review_extension.temporal import recurrence_from_roots

                raw_values, raw_record = recurrence_from_roots(
                    current, [other for other, _, _ in temporal], profile,
                )
                contexts[name].update({key: current.restore(value) for key, value in raw_values.items()})
                independent_records.append({"sweep": name, "nonprecip_raw_recurrence": raw_record})
            continue
        # V1 temporal evidence is diagnostic only and requires exactly matching
        # native-cut geometry. A same-angle split cut is not merged by similarity.
        samples = []
        for other, masks, _ in temporal:
            if name not in other or name not in masks:
                continue
            compare = other[name]
            if all(
                np.array_equal(group[k][:], compare[k][:])
                for k in ("range", "azimuth", "elevation")
            ):
                samples.append(masks[name].astype("float32"))
        if samples:
            contexts[name]["TEMPORAL_CANDIDATE_PERSISTENCE"] = np.mean(samples, axis=0).astype(
                "float32"
            )
    ancillary = current_priors if unified else (ancillary_maps or {})
    if profile.static_ground_clutter.asset_uri and ancillary_maps is None and not unified:
        ancillary = _load_clutter(profile, client, view.root)
    identity = {
        "parameters_hash": profile.parameters_hash,
        "libraries": [profile.arm_pyart_version, profile.wradlib_version],
        "decision_cutoff_utc": cutoff.isoformat(),
        "artifacts": artifacts,
        "dem_version": dem_version,
        "clutter_sha256": profile.static_ground_clutter.asset_sha256,
    }
    if audited:
        identity["geometry_resources"] = geometry_audit
        identity["prepared_context"] = context_arrays_identity(contexts)
        identity["support_statistics"] = {
            "cross_radar_raw_available_count": counts["cross_radar_available_count"],
            "cross_radar_reference_count": len(references),
            "availability_by_sweep": cross_availability,
            "comparable_gate_count_by_sweep": {
                name: int(np.isfinite(fields["WEATHER_SUPPORT_SCORE"]).sum())
                for name, fields in contexts.items()
            },
            "interpretation": "available_is_not_weather_or_clear_air_truth",
        }
    if unified:
        identity["stage_a"] = {
            "current": {
                name: {k: v for k, v in a.summary.items() if k != "elapsed_ms"}
                for name, a in current_standalone.items()
            },
            "references": [
                {k: v for k, v in a.items() if k != "elapsed_ms"} for a in independent_records
            ],
            "recurrence_semantics": "structural_repetition_not_truth",
        }
    # The loader above has already selected and validated strictly-past raw
    # observations. Pass them and the SAME terrain resources to the final CF
    # stage, without placing any radar arrays into task/event payloads.
    from .volume_review.clutter_fusion.near_runtime import prepare as prepare_near
    near_context = prepare_near(profile, view.root, temporal, beam, terrain,
                                dem_version, cutoff, artifacts)
    if near_context is not None:
        identity["near_joint_context"] = {
            "source_sha256": sorted({p.source_sha256 for p in near_context.past}),
            "dem_version": dem_version,
            "terrain_identity": getattr(terrain, "cache_identity", None),
            "beam_geometry_sha256": hashlib.sha256(json.dumps({
                k: getattr(beam, k, None) for k in ("radar_id", "radar_config_version",
                    "longitude_deg", "latitude_deg", "antenna_altitude_m",
                    "beam_width_vertical_deg", "altitude_datum_status")
            }, sort_keys=True).encode()).hexdigest(),
            "datum_status": getattr(beam, "altitude_datum_status", None),
            "temporal_policy": "frozen_strictly_past_raw_not_previous_QC",
        }
    fingerprint = hashlib.sha256(json.dumps(identity, sort_keys=True).encode()).hexdigest()
    return {
        "input_view": view,
        "ancillary_maps": ancillary,
        "radial_context": contexts,
        "radar_beam_context": beam,
        **({"near_clutter_context": near_context} if near_context is not None else {}),
        **({"standalone_by_sweep": current_standalone} if unified else {}),
    }, {
        **identity,
        **counts,
        "context_fingerprint": fingerprint,
        "context_age_seconds": 0.0,
        "cold_start": not references and not temporal,
        "context_availability_claim": "frozen_task_selection; ingest_time_verified per artifact",
    }


def _load_clutter(profile, client, root):
    from ..qc_worker import _load_npz

    uri = profile.static_ground_clutter.asset_uri
    # The asset format includes the hash of every native geometry. _load_npz
    # loads only data, never pickle/code. Artifact-content digest is verified here.
    archive = _load_npz(uri, client)
    actual = hashlib.sha256()
    for key in sorted(archive):
        values = np.asarray(archive[key])
        actual.update(key.encode() + str(values.dtype).encode() + str(values.shape).encode())
        actual.update(values.tobytes())
    if actual.hexdigest() != profile.static_ground_clutter.asset_sha256:
        raise ValueError("clutter asset content hash mismatch")
    if "__meta_json" in archive:
        from .review_extension.background_registry import load_verified_background

        return load_verified_background(
            archive, root, profile.static_ground_clutter.asset_sha256,
            profile.static_ground_clutter.asset_version, load_asset=lambda uri: _load_npz(uri, client),
        )
    result = {}
    for number in root["sweep_number"][:]:
        name = f"sweep_{int(number):03d}"
        for coord in ("azimuth", "elevation", "range"):
            if not np.array_equal(archive[f"{name}__{coord}"], root[name][coord][:]):
                raise ValueError("clutter asset geometry differs from the native cut")
        values = archive[f"{name}__ground_clutter"]
        finite = values[np.isfinite(values)]
        if finite.size and (finite.min() < 0 or finite.max() > 1):
            raise ValueError("invalid clutter prior")
        result[name] = {"ground_clutter": values}
    return result
