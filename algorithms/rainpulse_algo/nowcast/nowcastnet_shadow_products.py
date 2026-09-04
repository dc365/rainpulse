"""Small, immutable public-weight NowcastNet shadow product bundles."""

from __future__ import annotations

import hashlib
import json
from datetime import UTC, datetime, timedelta
from typing import Any
from uuid import UUID

import numpy as np

from rainpulse_algo.diagnostics.png import encode_rgba_png
from rainpulse_algo.grid import RegularLatLonGrid
from rainpulse_algo.products.builder import rainfall_rgba
from rainpulse_algo.products.point_index import encode_point_query_index
from rainpulse_algo.products.profile import ProductBuilderProfile

from .nowcastnet_profile import NowcastNetProfile
from .temporal_adapter import AdaptedForecast

CONTRACT_NAME = "rainpulse.nowcastnet-shadow-product-bundle"
CONTRACT_VERSION = "1.2"
POINT_QUERY_PATH = "rain_rate/query/point-index.bin"


class NowcastNetShadowProductError(ValueError):
    """Raised when a shadow product would hide an invalid or derived frame."""


def build_nowcastnet_shadow_product_bundle(
    forecast: AdaptedForecast,
    *,
    run_id: UUID,
    job_id: UUID,
    algorithm_run_id: UUID,
    issue_time: datetime,
    grid: RegularLatLonGrid,
    model_profile: NowcastNetProfile,
    shadow_profile_version: str,
    atlas_version: str,
    product_profile: ProductBuilderProfile,
    input_analysis: list[dict[str, str]],
    runtime: dict[str, Any],
) -> dict[str, bytes]:
    """Build 24 five-minute display frames and an exact-value point sidecar.

    Native ten-minute member fields are already represented by ``forecast``;
    this routine only renders and records them.  It never resamples or rounds
    values before serialising the point-query index.
    """

    issue_time = issue_time.astimezone(UTC)
    members = np.asarray(forecast.rain_rate_mm_h, dtype="float32")
    valid = np.asarray(forecast.valid_mask, dtype="uint8")
    confidence = np.asarray(forecast.confidence, dtype="float32")
    if (
        members.ndim != 4
        or valid.shape != members.shape
        or confidence.shape != members.shape
        or members.shape[0] != model_profile.protocol.ensemble_members
        or members.shape[2:] != grid.shape
        or members.shape[1] != len(forecast.frames)
    ):
        raise NowcastNetShadowProductError("NowcastNet shadow product dimensions differ")
    if len(forecast.frames) != 24:
        raise NowcastNetShadowProductError(
            "NowcastNet shadow product requires 24 five-minute frames"
        )
    leads = [frame.lead_minutes for frame in forecast.frames]
    if leads != list(range(5, 121, 5)):
        raise NowcastNetShadowProductError("NowcastNet shadow lead times differ")
    if any(frame.frame_kind not in {"native", "derived"} for frame in forecast.frames):
        raise NowcastNetShadowProductError("NowcastNet frame kind is invalid")
    if np.any((valid != 0) & (valid != 1)) or np.any(
        ~np.isfinite(members[valid == 1])
    ):
        raise NowcastNetShadowProductError("NowcastNet shadow values or masks are invalid")
    if np.any(confidence[valid == 1] < 0) or np.any(confidence[valid == 1] > 1):
        raise NowcastNetShadowProductError("NowcastNet shadow confidence is invalid")

    support = np.all(valid == 1, axis=0)
    count = np.sum(valid == 1, axis=0)
    average = np.full(members.shape[1:], np.nan, dtype="float32")
    sum_values = np.sum(np.where(valid == 1, members, 0.0), axis=0, dtype="float64")
    average[support] = (sum_values[support] / count[support]).astype("float32")
    mean_confidence = np.zeros(members.shape[1:], dtype="float32")
    mean_confidence[support] = (
        np.sum(np.where(valid == 1, confidence, 0.0), axis=0, dtype="float64")[support]
        / count[support]
    ).astype("float32")

    objects: dict[str, bytes] = {}
    frames: list[dict[str, Any]] = []
    cell_count = int(np.prod(grid.shape))
    for index, metadata in enumerate(forecast.frames):
        frame_valid = support[index]
        values = average[index]
        png = encode_rgba_png(
            rainfall_rgba(
                values,
                frame_valid,
                product_profile.palette.rain_rate,
                transparent_below=product_profile.palette.transparent_below_mm,
                opacity=product_profile.palette.opacity,
            )
        )
        lead = metadata.lead_minutes
        object_path = f"rain_rate/lead-{lead:03d}/layer.png"
        objects[object_path] = png
        valid_count = int(np.count_nonzero(frame_valid))
        frame: dict[str, Any] = {
            "asset_id": f"ensemble-mean-lead-{lead:03d}-png",
            "object_path": object_path,
            "media_type": "image/png",
            "sha256": hashlib.sha256(png).hexdigest(),
            "size_bytes": len(png),
            "lead_time_minutes": lead,
            "valid_time": (issue_time + timedelta(minutes=lead)).isoformat(),
            "unit": "mm/h",
            "coverage_ratio": valid_count / cell_count,
            "valid_cell_count": valid_count,
            "missing_cell_count": cell_count - valid_count,
            "pixel_edge_bounds": list(grid.pixel_edge_bounds),
            "frame_kind": metadata.frame_kind,
            "source_leads": list(metadata.source_leads),
        }
        if metadata.derivation:
            frame["derivation"] = metadata.derivation
        frames.append(frame)

    point_index = encode_point_query_index(
        average,
        mean_confidence,
        support.astype("uint8"),
        west=grid.west,
        south=grid.south,
        longitude_interval=grid.longitude_interval_deg,
        latitude_interval=grid.latitude_interval_deg,
    )
    objects[POINT_QUERY_PATH] = point_index
    created_at = datetime.now(UTC)
    manifest = {
        "contract_name": CONTRACT_NAME,
        "contract_version": CONTRACT_VERSION,
        "bundle_id": str(job_id),
        "run_id": str(run_id),
        "job_id": str(job_id),
        "algorithm_run_id": str(algorithm_run_id),
        "issue_time": issue_time.isoformat(),
        "grid_id": grid.grid_id,
        "grid_config_version": grid.config_version,
        "coordinate_sha256": grid.coordinate_sha256,
        "width": grid.longitude_count,
        "height": grid.latitude_count,
        "pixel_edge_bounds": list(grid.pixel_edge_bounds),
        "model_id": model_profile.model_id,
        "model_version": model_profile.model_version,
        "profile_version": shadow_profile_version,
        "member_count": model_profile.protocol.ensemble_members,
        "issue_cadence_minutes": 5,
        "native_timestep_minutes": model_profile.protocol.timestep_minutes,
        "cadence_minutes": 5,
        "lifecycle": "shadow",
        "operational_eligible": False,
        "tile_atlas_version": atlas_version,
        "missing_policy": "reject_any_missing",
        "legend_unit": "mm/h",
        "legend": [
            {"minimum": stop.minimum, "color": stop.color}
            for stop in product_profile.palette.rain_rate
        ],
        "frames": frames,
        "point_queries": {
            "nowcastnet": {
                "object_path": POINT_QUERY_PATH,
                "sha256": hashlib.sha256(point_index).hexdigest(),
                "size_bytes": len(point_index),
                "unit": "mm/h",
                "lead_minutes": leads,
                "valid_times": [
                    (issue_time + timedelta(minutes=lead)).isoformat() for lead in leads
                ],
                "frame_kinds": [frame.frame_kind for frame in forecast.frames],
                "derivations": [frame.derivation or "" for frame in forecast.frames],
                "quality_kind": "shadow_member_mean_support_fraction",
            }
        },
        "input_analysis": input_analysis,
        "runtime": runtime,
        "created_at": created_at.isoformat(),
    }
    objects["manifest.json"] = json.dumps(
        manifest, ensure_ascii=False, separators=(",", ":"), sort_keys=True
    ).encode()
    return dict(sorted(objects.items()))
