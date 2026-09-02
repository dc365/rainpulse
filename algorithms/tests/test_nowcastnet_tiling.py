from __future__ import annotations

import numpy as np

from rainpulse_algo.nowcast.nowcastnet_tiling import (
    NowcastNetTile,
    blend_tile_forecasts,
    select_all_valid_tiles,
)


def test_tile_selection_expands_beyond_one_rectangle_without_covering_missing() -> None:
    valid = np.zeros((160, 224), dtype=bool)
    valid[32:128, 0:160] = True
    valid[64:128, 160:224] = True

    selection = select_all_valid_tiles(
        valid,
        minimum_tile_size=64,
        candidate_stride=8,
    )

    covered = np.zeros_like(valid)
    for tile in selection.tiles:
        covered[
            tile.y_start : tile.y_start + tile.height,
            tile.x_start : tile.x_start + tile.width,
        ] = True
    assert len(selection.tiles) > 1
    assert selection.covered_cell_count > selection.tiles[0].area
    assert np.all(valid[covered])
    assert not np.any(covered & ~valid)
    assert selection.common_valid_coverage_ratio == 1.0


def test_weighted_blend_returns_full_grid_support_and_no_constant_field_seam() -> None:
    tiles = [
        NowcastNetTile(32, 0, 64, 96),
        NowcastNetTile(32, 64, 64, 96),
    ]
    forecasts = [
        (tile, np.full((12, tile.height, tile.width), 5, dtype="float32"))
        for tile in tiles
    ]

    result = blend_tile_forecasts(forecasts, output_shape=(128, 192))

    support = result.valid_mask[0] == 1
    assert np.all(result.valid_mask[:, support] == 1)
    assert np.all(result.rain_rate_mm_h[:, support] == 5)
    assert np.all(result.rain_rate_mm_h[:, ~support] == 0)
    assert result.overlap_cell_count == 64 * 32
    assert result.overlap_difference_p95_mm_h == 0
    assert result.primary_consistency_mae_mm_h == 0
    assert result.primary_consistency_p95_mm_h == 0
    assert result.seam_gradient_ratio == 0


def test_larger_context_tile_has_more_weight_in_overlap() -> None:
    primary = NowcastNetTile(0, 0, 96, 128)
    secondary = NowcastNetTile(32, 64, 64, 64)
    result = blend_tile_forecasts(
        [
            (primary, np.full((1, 96, 128), 10, dtype="float32")),
            (secondary, np.zeros((1, 64, 64), dtype="float32")),
        ],
        output_shape=(96, 128),
    )

    assert result.rain_rate_mm_h[0, 48, 80] > 5
    assert result.primary_consistency_mae_mm_h > 0
