from __future__ import annotations

import numpy as np

from rainpulse_algo.nowcast.nowcastnet_tile_atlas import (
    AtlasTile,
    TileAtlas,
    TrustedWindow,
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


def test_atlas_rejects_missing_tile_and_preserves_transparent_support() -> None:
    atlas = _atlas()
    validate_tile_atlas(atlas)
    rate = np.ones((9, 64, 64), dtype="float32")
    valid = np.ones_like(rate, dtype="uint8")
    valid[:, :, 33] = 0
    rate[:, :, 33] = np.nan

    prepared = prepare_atlas_tiles(rate, valid, atlas)

    assert [item.tile.tile_id for item in prepared.eligible] == ["west"]
    assert prepared.rejected == (("east", "input_window_has_missing_cells"),)
    assert prepared.trusted_coverage_ratio == 0.5


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
