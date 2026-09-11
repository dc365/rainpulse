"""Render fixed accumulation windows without modifying rain-rate products."""

import hashlib
from datetime import datetime, timedelta

import numpy as np

from rainpulse_algo.diagnostics.png import encode_rgba_png
from rainpulse_algo.grid import RegularLatLonGrid

from .accumulation import WINDOWS, AccumulationWindows
from .builder import rainfall_rgba
from .point_index import encode_point_query_index
from .profile import ProductPalette


def render_accumulation_windows(
    accumulation: AccumulationWindows,
    *,
    issue_time: datetime,
    grid: RegularLatLonGrid,
    palette: ProductPalette,
    quantile: float | None = None,
):
    values, valid, quality = accumulation.statistic(quantile=quantile)
    objects, frames, queries = {}, [], {}
    for index, (window_id, start, end) in enumerate(WINDOWS):
        key = f"accumulation-{window_id.replace('_', '-')}"
        path = f"{key}/lead-{end:03d}/layer.png"
        data = encode_rgba_png(
            rainfall_rgba(
                values[index],
                valid[index],
                palette.rainfall_amount,
                transparent_below=palette.transparent_below_mm,
                opacity=palette.opacity,
            )
        )
        objects[path] = data
        count = int(np.count_nonzero(valid[index]))
        valid_time = (issue_time + timedelta(minutes=end)).isoformat()
        frames.append(
            {
                "asset_id": f"{key}-lead-{end:03d}-png",
                "object_path": path,
                "media_type": "image/png",
                "sha256": hashlib.sha256(data).hexdigest(),
                "size_bytes": len(data),
                "lead_time_minutes": end,
                "valid_time": valid_time,
                "unit": "mm",
                "coverage_ratio": count / valid[index].size,
                "valid_cell_count": count,
                "missing_cell_count": valid[index].size - count,
                "pixel_edge_bounds": list(grid.pixel_edge_bounds),
                "window_id": window_id,
                "window_start_minutes": start,
                "frame_kind": "derived",
                "derivation": "five-minute-right-endpoint-accumulation-v1",
                "source_leads": list(range(start + 5, end + 1, 5)),
            }
        )
        point_path = f"{key}/query/point-index.bin"
        point = encode_point_query_index(
            values[index : index + 1],
            quality[index : index + 1],
            valid[index : index + 1],
            west=grid.west,
            south=grid.south,
            longitude_interval=grid.longitude_interval_deg,
            latitude_interval=grid.latitude_interval_deg,
        )
        objects[point_path] = point
        queries[key] = {
            "object_path": point_path,
            "sha256": hashlib.sha256(point).hexdigest(),
            "size_bytes": len(point),
            "unit": "mm",
            "lead_minutes": [end],
            "valid_times": [valid_time],
            "frame_kinds": ["derived"],
            "derivations": ["five-minute-right-endpoint-accumulation-v1"],
        }
    return objects, frames, queries
