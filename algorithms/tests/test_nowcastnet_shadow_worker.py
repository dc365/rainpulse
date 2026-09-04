from __future__ import annotations

from dataclasses import replace
from pathlib import Path

import numpy as np

from rainpulse_algo.grid import RegularLatLonGrid
from rainpulse_algo.nowcast.nowcastnet_profile import load_nowcastnet_profile
from rainpulse_algo.nowcast.nowcastnet_shadow_worker import (
    LoadedShadowRuntime,
    ShadowTaskConfiguration,
    run_fixed_tile_atlas,
)
from rainpulse_algo.nowcast.nowcastnet_tile_atlas import (
    AtlasTile,
    TileAtlas,
    TrustedWindow,
)
from rainpulse_algo.products.profile import load_product_builder_profile

ROOT = Path(__file__).resolve().parents[2]


def _runtime() -> LoadedShadowRuntime:
    parent = load_nowcastnet_profile(ROOT / "configs/nowcast/rp026-nowcastnet-offline-v1.yaml")
    parent = replace(parent, protocol=replace(parent.protocol, input_height=64, input_width=32))
    grid = RegularLatLonGrid(
        grid_id="test-grid",
        config_version="test-grid-v1",
        west=118.0,
        east=118.63,
        south=25.0,
        north=25.63,
        longitude_interval_deg=0.01,
        latitude_interval_deg=0.01,
        longitude_count=64,
        latitude_count=64,
        reference_latitude_deg=25.3,
        ancillary_domain_id="test",
    )
    atlas = TileAtlas(
        atlas_version="fujian-nowcastnet-tile-atlas-v1",
        grid_id=grid.grid_id,
        grid_config_version=grid.config_version,
        grid_shape=grid.shape,
        spatial_multiple=32,
        missing_policy="reject_any_missing",
        tiles=(
            AtlasTile("west", 0, 0, 64, 32, TrustedWindow(0, 64, 0, 32)),
            AtlasTile("east", 0, 32, 64, 32, TrustedWindow(0, 64, 0, 32)),
        ),
    )
    task = ShadowTaskConfiguration(
        profile_version="fujian-nowcastnet-shadow-v2",
        source_model_profile="rp026-nowcastnet-offline-v1",
        grid_id=grid.grid_id,
        grid_config_version=grid.config_version,
        tile_atlas_version=atlas.atlas_version,
        product_profile="rp015-application-products-v1",
        input_frames=9,
        issue_cadence_minutes=5,
        input_timestep_minutes=10,
        native_output_timestep_minutes=10,
        product_timestep_minutes=5,
        native_output_lead_minutes=tuple(range(10, 121, 10)),
        gpu_batch_size=4,
        batch_fallback="serial",
        temporal_adapter="bidirectional-dense-optical-flow-advection-v1",
    )
    return LoadedShadowRuntime(
        task=task,
        parent_profile=parent,
        atlas=atlas,
        grid=grid,
        product_profile=load_product_builder_profile(
            ROOT / "configs/products/rp015-application-products-v1.yaml"
        ),
        capsule_root="/unused",
        device="cpu",
    )


def test_fixed_atlas_uses_one_same_shape_batch() -> None:
    runtime = _runtime()
    rates = np.ones((9, 64, 64), dtype="float32")
    valid = np.ones_like(rates, dtype="uint8")

    class Backend:
        def infer_batch(self, fields: np.ndarray, members: int, seed: int) -> np.ndarray:
            assert fields.shape == (2, 9, 64, 32, 2)
            assert members == 4
            assert seed == 17
            return np.broadcast_to(
                fields[np.newaxis, :, np.newaxis, -1, ..., 0],
                (4, 2, 20, 64, 32),
            ).copy()

    result, output_valid, summary = run_fixed_tile_atlas(
        rates,
        valid,
        runtime=runtime,
        backend_factory=lambda _profile: Backend(),
        random_seed=17,
    )

    assert result.shape == (4, 12, 64, 64)
    assert np.all(output_valid == 1)
    assert summary["batch_sizes"] == [2]
    assert summary["batch_fallback_count"] == 0


def test_fixed_atlas_falls_back_to_serial_when_batch_is_unavailable() -> None:
    runtime = _runtime()
    rates = np.ones((9, 64, 64), dtype="float32")
    valid = np.ones_like(rates, dtype="uint8")

    class Backend:
        def infer_batch(self, *_args: object) -> np.ndarray:
            raise RuntimeError("batch unsupported")

        def __call__(self, fields: np.ndarray, members: int, _seed: int) -> np.ndarray:
            return np.broadcast_to(fields[-1, ..., 0], (members, 20, 64, 32)).copy()

    result, output_valid, summary = run_fixed_tile_atlas(
        rates,
        valid,
        runtime=runtime,
        backend_factory=lambda _profile: Backend(),
        random_seed=17,
    )

    assert np.all(result == 1.0)
    assert np.all(output_valid == 1)
    assert summary["batch_sizes"] == []
    assert summary["batch_fallback_count"] == 2
