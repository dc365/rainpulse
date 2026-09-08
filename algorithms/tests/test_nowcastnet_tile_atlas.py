from __future__ import annotations

from pathlib import Path

import numpy as np
import pytest
from rainpulse_algo.nowcast.nowcastnet_tile_atlas import (
    AtlasTile,
    TileAtlas,
    TrustedWindow,
    load_tile_atlas,
    prepare_atlas_tiles,
    stitch_member_tiles,
    validate_tile_atlas,
)


def _atlas() -> TileAtlas:
    return TileAtlas(
        atlas_version="test-v1",
        grid_id="test-grid",
        grid_config_version="grid-v1",
        grid_shape=(64, 64),
        spatial_multiple=32,
        missing_policy="reject_any_missing",
        tiles=(
            AtlasTile("west", 0, 0, 64, 32, TrustedWindow(0, 64, 0, 32)),
            AtlasTile("east", 0, 32, 64, 32, TrustedWindow(0, 64, 0, 32)),
        ),
    )


def test_atlas_fills_missing_context_and_preserves_latest_support() -> None:
    atlas = _atlas()
    validate_tile_atlas(atlas)
    rate = np.ones((9, 64, 64), dtype="float32")
    valid = np.ones_like(rate, dtype="uint8")
    valid[:, :, 33] = 0
    rate[:, :, 33] = np.nan

    prepared = prepare_atlas_tiles(rate, valid, atlas)

    assert [item.tile.tile_id for item in prepared.eligible] == ["west", "east"]
    assert prepared.rejected == ()
    assert prepared.trusted_coverage_ratio == 1.0
    assert np.all(prepared.eligible[1].rain_rate_mm_h[:, :, 1] == 0.0)
    assert np.all(prepared.eligible[1].valid_mask == 1)
    assert np.all(prepared.publication_mask[:, 33] == 0)
    assert np.all(prepared.publication_mask[:, 34:] == 1)


def test_stitch_uses_only_trusted_centres() -> None:
    atlas = _atlas()
    west = np.ones((2, 12, 64, 32), dtype="float32")
    east = np.full((2, 12, 64, 32), 2.0, dtype="float32")
    result, valid = stitch_member_tiles(
        [(atlas.tiles[0], west), (atlas.tiles[1], east)], output_shape=atlas.grid_shape
    )

    assert np.all(valid == 1)
    assert np.all(result[:, :, :, :32] == 1.0)
    assert np.all(result[:, :, :, 32:] == 2.0)


def test_overlap_atlas_removes_hard_joins_without_changing_equal_predictions() -> None:
    root = Path(__file__).resolve().parents[2]
    atlas = load_tile_atlas(root / "configs/nowcast/fujian-nowcastnet-tile-atlas-v2.yaml")
    forecasts = [
        (
            tile,
            np.full(
                (1, 1, tile.height, tile.width),
                1 + tile.x_start / 64 + tile.y_start / 64,
                dtype="float32",
            ),
        )
        for tile in atlas.tiles
    ]
    result, valid = stitch_member_tiles(forecasts, output_shape=atlas.grid_shape)
    assert np.all(valid == 1)
    assert np.max(np.abs(np.diff(result, axis=-1))) < 0.1
    assert np.max(np.abs(np.diff(result, axis=-2))) < 0.1
    # Consensus fields, including intense isolated cells, must not be blurred.
    field = np.ones(atlas.grid_shape, dtype="float32")
    field[96, 192] = 80
    values = [
        (tile, field[tile.y_start : tile.y_end, tile.x_start : tile.x_end][None, None])
        for tile in atlas.tiles
    ]
    same, _ = stitch_member_tiles(values, output_shape=atlas.grid_shape)
    np.testing.assert_allclose(same[0, 0], field, rtol=1e-6)
    assert same[0, 0, 96, 192] == 80


def test_sharper_overlap_is_convex_and_keeps_missing_support() -> None:
    west = AtlasTile("west", 0, 0, 8, 8, TrustedWindow(0, 8, 0, 8))
    east = AtlasTile("east", 0, 4, 8, 8, TrustedWindow(0, 8, 0, 8))
    values = [(west, np.full((1, 1, 8, 8), 40.0)), (east, np.zeros((1, 1, 8, 8)))]
    baseline, _ = stitch_member_tiles(values, output_shape=(8, 13))
    sharp, valid = stitch_member_tiles(values, output_shape=(8, 13), weight_power=2)
    # West is more central at x=4; sharpening reduces mixing, not by boosting rain.
    assert baseline[0, 0, 4, 4] < sharp[0, 0, 4, 4] < 40
    assert np.nanmin(sharp) == 0 and np.nanmax(sharp) == 40
    assert np.all(valid[..., 12] == 0) and np.all(np.isnan(sharp[..., 12]))
    equal = [(tile, np.full_like(value, 40)) for tile, value in values]
    consensus, _ = stitch_member_tiles(equal, output_shape=(8, 13), weight_power=2)
    np.testing.assert_allclose(consensus[..., :12], 40)
    with pytest.raises(ValueError):
        stitch_member_tiles(values, output_shape=(8, 13), weight_power=float("nan"))


def test_large_context_candidate_covers_grid_and_preserves_consensus() -> None:
    root = Path(__file__).resolve().parents[2]
    atlas = load_tile_atlas(root / "configs/nowcast/fujian-nowcastnet-tile-atlas-v3.yaml")
    assert len(atlas.tiles) == 6
    assert all((tile.height, tile.width) == (192, 256) for tile in atlas.tiles)
    field = np.ones(atlas.grid_shape, dtype="float32")
    field[96, 192] = 80
    result, valid = stitch_member_tiles(
        [
            (tile, field[tile.y_start : tile.y_end, tile.x_start : tile.x_end][None, None])
            for tile in atlas.tiles
        ],
        output_shape=atlas.grid_shape,
    )
    assert np.all(valid == 1)
    np.testing.assert_allclose(result[0, 0], field, rtol=1e-6)
