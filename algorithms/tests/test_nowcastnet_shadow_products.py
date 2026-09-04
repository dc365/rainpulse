from __future__ import annotations

import json
from datetime import UTC, datetime
from pathlib import Path
from uuid import UUID

import numpy as np

from rainpulse_algo.grid import RegularLatLonGrid
from rainpulse_algo.nowcast.nowcastnet_profile import load_nowcastnet_profile
from rainpulse_algo.nowcast.nowcastnet_shadow_products import (
    POINT_QUERY_PATH,
    build_nowcastnet_shadow_product_bundle,
)
from rainpulse_algo.nowcast.temporal_adapter import AdaptedForecast, AdaptedFrame
from rainpulse_algo.products.point_index import validate_point_query_index
from rainpulse_algo.products.profile import load_product_builder_profile

ROOT = Path(__file__).resolve().parents[2]


def test_shadow_product_keeps_native_and_derived_frame_lineage() -> None:
    grid = RegularLatLonGrid(
        grid_id="tiny-grid",
        config_version="tiny-v1",
        west=118.0,
        east=118.31,
        south=25.0,
        north=25.31,
        longitude_interval_deg=0.01,
        latitude_interval_deg=0.01,
        longitude_count=32,
        latitude_count=32,
        reference_latitude_deg=25.15,
        ancillary_domain_id="tiny",
    )
    frames = tuple(
        AdaptedFrame(
            lead_minutes=lead,
            frame_kind="native" if lead % 10 == 0 else "derived",
            derivation=None
            if lead % 10 == 0
            else "bidirectional-dense-optical-flow-advection-v1",
            source_leads=(lead,)
            if lead % 10 == 0
            else (lead - 5, lead + 5),
        )
        for lead in range(5, 121, 5)
    )
    values = np.full((4, 24, 32, 32), 2.0, dtype="float32")
    forecast = AdaptedForecast(
        rain_rate_mm_h=values,
        valid_mask=np.ones_like(values, dtype="uint8"),
        confidence=np.ones_like(values, dtype="float32"),
        frames=frames,
    )
    objects = build_nowcastnet_shadow_product_bundle(
        forecast,
        run_id=UUID("11111111-1111-1111-1111-111111111111"),
        job_id=UUID("22222222-2222-2222-2222-222222222222"),
        algorithm_run_id=UUID("33333333-3333-3333-3333-333333333333"),
        issue_time=datetime(2026, 8, 28, 2, 0, tzinfo=UTC),
        grid=grid,
        model_profile=load_nowcastnet_profile(
            ROOT / "configs/nowcast/rp026-nowcastnet-offline-v1.yaml"
        ),
        shadow_profile_version="fujian-nowcastnet-shadow-v2",
        atlas_version="fujian-nowcastnet-tile-atlas-v1",
        product_profile=load_product_builder_profile(
            ROOT / "configs/products/rp015-application-products-v1.yaml"
        ),
        input_analysis=[],
        runtime={},
    )

    manifest = json.loads(objects["manifest.json"])
    assert manifest["cadence_minutes"] == 5
    assert len(manifest["frames"]) == 24
    assert manifest["frames"][0]["frame_kind"] == "derived"
    assert manifest["frames"][1]["frame_kind"] == "native"
    assert manifest["point_queries"]["nowcastnet"]["frame_kinds"][:2] == [
        "derived",
        "native",
    ]
    assert validate_point_query_index(objects[POINT_QUERY_PATH])["lead_count"] == 24
