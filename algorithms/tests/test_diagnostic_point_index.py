from __future__ import annotations

import json
from uuid import uuid4

import numpy as np
import zarr
from zarr.storage import MemoryStore

from rainpulse_algo.diagnostics.point_index import (
    QPE_POINT_INDEX_PATH,
    attach_analysis_point_index,
)
from rainpulse_algo.products.point_index import validate_point_query_index


def _analysis() -> dict[str, bytes]:
    store = MemoryStore()
    root = zarr.group(store=store, overwrite=True)
    root.attrs.update(
        {
            "analysis_time": "2026-08-28T02:30:00+00:00",
            "analysis_id": str(uuid4()),
            "grid_id": "fujian-grid",
        }
    )
    root.create_dataset("lat", data=np.asarray([25.0, 25.01], dtype="float64"))
    root.create_dataset("lon", data=np.asarray([118.0, 118.01, 118.02], dtype="float64"))
    root.create_dataset(
        "RATE_QPE",
        data=np.asarray([[0.0, 2.5, np.nan], [4.0, 8.0, 16.0]], dtype="float32"),
    )
    root.create_dataset(
        "QUALITY_INDEX",
        data=np.asarray([[0.9, 0.8, np.nan], [0.7, 0.6, 0.5]], dtype="float32"),
    )
    root.create_dataset(
        "VALID_MASK",
        data=np.asarray([[1, 1, 0], [1, 1, 1]], dtype="uint8"),
    )
    return {str(key): bytes(value) for key, value in store.items()}


def test_attaches_exact_qpe_point_index_without_changing_png_contract() -> None:
    bundle = {
        "manifest.json": json.dumps(
            {
                "contract_version": "1.0",
                "analysis_id": "a",
                "layers": [{"layer_id": "grid-rate-qpe"}],
            }
        ).encode(),
        "layers/grid-rate-qpe.png": b"png-placeholder",
    }
    result = attach_analysis_point_index(bundle, _analysis())
    assert result["layers/grid-rate-qpe.png"] == b"png-placeholder"
    report = validate_point_query_index(result[QPE_POINT_INDEX_PATH])
    assert report["width"] == 3
    assert report["height"] == 2
    assert report["lead_count"] == 1
    manifest = json.loads(result["manifest.json"])
    query = manifest["point_queries"]["grid-rate-qpe"]
    assert query["lead_minutes"] == [0]
    assert query["frame_kinds"] == ["analysis"]
    assert query["size_bytes"] == len(result[QPE_POINT_INDEX_PATH])
