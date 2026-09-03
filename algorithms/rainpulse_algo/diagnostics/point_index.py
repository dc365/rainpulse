from __future__ import annotations

import hashlib
import json
from collections.abc import Mapping
from typing import Any

import numpy as np
import zarr
from zarr.storage import MemoryStore

from rainpulse_algo.products.point_index import (
    encode_point_query_index,
    validate_point_query_index,
)

QPE_POINT_INDEX_PATH = "query/grid-rate-qpe-point-index.bin"
QPE_POINT_QUERY_ID = "grid-rate-qpe"


def attach_analysis_point_index(
    bundle_objects: Mapping[str, bytes],
    analysis_objects: Mapping[str, bytes],
) -> dict[str, bytes]:
    """Attach an exact, one-frame QPE point index to a diagnostic bundle.

    The existing PNG diagnostic contract remains unchanged. The new sidecar is
    addressed by a stable path and described in a top-level ``point_queries``
    manifest section that old readers safely ignore.
    """
    result = {str(key): bytes(value) for key, value in bundle_objects.items()}
    if "manifest.json" not in result:
        raise ValueError("diagnostic bundle has no manifest")

    input_store = MemoryStore()
    input_store.update({str(key): bytes(value) for key, value in analysis_objects.items()})
    root = zarr.open_group(store=input_store, mode="r")
    rate = np.asarray(root["RATE_QPE"][:], dtype="float32")
    quality = np.asarray(root["QUALITY_INDEX"][:], dtype="float32")
    valid = np.asarray(root["VALID_MASK"][:] == 1, dtype="uint8")
    latitude = np.asarray(root["lat"][:], dtype="float64")
    longitude = np.asarray(root["lon"][:], dtype="float64")
    if rate.ndim != 2 or quality.shape != rate.shape or valid.shape != rate.shape:
        raise ValueError("RadarAnalysis point-query fields differ in shape")
    if latitude.size != rate.shape[0] or longitude.size != rate.shape[1]:
        raise ValueError("RadarAnalysis coordinates differ from point-query fields")
    if latitude.size < 2 or longitude.size < 2:
        raise ValueError("RadarAnalysis point-query grid is too small")
    latitude_step = float(latitude[1] - latitude[0])
    longitude_step = float(longitude[1] - longitude[0])
    if latitude_step <= 0 or longitude_step <= 0:
        raise ValueError("RadarAnalysis point-query coordinates must increase")

    payload = encode_point_query_index(
        rate[np.newaxis, ...],
        quality[np.newaxis, ...],
        valid[np.newaxis, ...],
        west=float(longitude[0]),
        south=float(latitude[0]),
        longitude_interval=longitude_step,
        latitude_interval=latitude_step,
    )
    validation = validate_point_query_index(payload)
    if validation["lead_count"] != 1:
        raise ValueError("QPE point-query sidecar must contain exactly one frame")
    result[QPE_POINT_INDEX_PATH] = payload

    try:
        manifest: dict[str, Any] = json.loads(result["manifest.json"])
    except (TypeError, json.JSONDecodeError) as exc:
        raise ValueError("diagnostic bundle manifest is invalid JSON") from exc
    point_queries = manifest.setdefault("point_queries", {})
    if not isinstance(point_queries, dict):
        raise ValueError("diagnostic point_queries metadata must be an object")
    point_queries[QPE_POINT_QUERY_ID] = {
        "object_path": QPE_POINT_INDEX_PATH,
        "sha256": hashlib.sha256(payload).hexdigest(),
        "size_bytes": len(payload),
        "data_kind": "rain_rate",
        "unit": "mm/h",
        "lead_minutes": [0],
        "valid_times": [str(root.attrs["analysis_time"])],
        "quality_kind": "radar_analysis_quality_index",
        "frame_kinds": ["analysis"],
    }
    result["manifest.json"] = json.dumps(
        manifest,
        separators=(",", ":"),
        sort_keys=True,
    ).encode()
    return dict(sorted(result.items()))
