from __future__ import annotations

from dataclasses import dataclass

import numpy as np


class NowcastNetTilingError(ValueError):
    """Raised when strict all-valid tiling or blending is inconsistent."""


@dataclass(frozen=True, order=True)
class NowcastNetTile:
    y_start: int
    x_start: int
    height: int
    width: int

    @property
    def area(self) -> int:
        return self.height * self.width


@dataclass(frozen=True)
class NowcastNetTileSelection:
    tiles: tuple[NowcastNetTile, ...]
    common_valid_cell_count: int
    covered_cell_count: int
    common_valid_coverage_ratio: float
    domain_coverage_ratio: float


@dataclass(frozen=True)
class NowcastNetStitchedResult:
    rain_rate_mm_h: np.ndarray
    valid_mask: np.ndarray
    tile_coverage_count: np.ndarray
    overlap_cell_count: int
    overlap_difference_p95_mm_h: float
    primary_consistency_mae_mm_h: float
    primary_consistency_p95_mm_h: float
    seam_gradient_ratio: float


def select_all_valid_tiles(
    common_valid: np.ndarray,
    *,
    spatial_multiple: int = 32,
    minimum_tile_size: int = 64,
    candidate_stride: int = 8,
    maximum_tiles: int = 64,
) -> NowcastNetTileSelection:
    support = np.asarray(common_valid, dtype=bool)
    if support.ndim != 2:
        raise NowcastNetTilingError("NowcastNet common-valid support must be two-dimensional")
    if (
        spatial_multiple < 1
        or minimum_tile_size < spatial_multiple
        or minimum_tile_size % spatial_multiple
        or candidate_stride < 1
        or maximum_tiles < 1
    ):
        raise NowcastNetTilingError("NowcastNet tile geometry is invalid")

    primary = largest_aligned_valid_rectangle(
        support,
        multiple=spatial_multiple,
        minimum_height=minimum_tile_size,
        minimum_width=minimum_tile_size,
    )
    if primary is None:
        raise NowcastNetTilingError("common-valid support has no eligible NowcastNet tile")

    candidates = _fixed_tile_candidates(
        support,
        size=minimum_tile_size,
        stride=candidate_stride,
    )
    if primary not in candidates:
        candidates.insert(0, primary)

    target = np.zeros_like(support)
    for tile in candidates:
        target[_tile_slices(tile)] = True
    selected = [primary]
    covered = np.zeros_like(support)
    covered[_tile_slices(primary)] = True
    remaining = [tile for tile in candidates if tile != primary]

    while remaining and len(selected) < maximum_tiles:
        best_index = -1
        best_gain = 0
        for index, tile in enumerate(remaining):
            ys, xs = _tile_slices(tile)
            gain = int(np.count_nonzero(target[ys, xs] & ~covered[ys, xs]))
            if gain > best_gain:
                best_index = index
                best_gain = gain
        if best_index < 0 or best_gain == 0:
            break
        chosen = remaining.pop(best_index)
        selected.append(chosen)
        covered[_tile_slices(chosen)] = True

    covered &= target
    common_count = int(np.count_nonzero(support))
    covered_count = int(np.count_nonzero(covered))
    return NowcastNetTileSelection(
        tiles=tuple(selected),
        common_valid_cell_count=common_count,
        covered_cell_count=covered_count,
        common_valid_coverage_ratio=covered_count / common_count if common_count else 0.0,
        domain_coverage_ratio=covered_count / support.size,
    )


def blend_tile_forecasts(
    tile_forecasts: list[tuple[NowcastNetTile, np.ndarray]],
    *,
    output_shape: tuple[int, int],
) -> NowcastNetStitchedResult:
    if not tile_forecasts:
        raise NowcastNetTilingError("at least one NowcastNet tile forecast is required")
    height, width = output_shape
    lead_count = int(np.asarray(tile_forecasts[0][1]).shape[0])
    if min(height, width, lead_count) < 1:
        raise NowcastNetTilingError("NowcastNet stitched output shape is invalid")

    weighted_sum = np.zeros((lead_count, height, width), dtype="float64")
    total_weight = np.zeros((height, width), dtype="float64")
    coverage_count = np.zeros((height, width), dtype="uint16")
    minimum_area = min(tile.area for tile, _ in tile_forecasts)
    normalized: list[tuple[NowcastNetTile, np.ndarray, np.ndarray]] = []

    for tile, raw_values in tile_forecasts:
        values = np.asarray(raw_values, dtype="float32")
        expected = (lead_count, tile.height, tile.width)
        if values.shape != expected or np.any(~np.isfinite(values)) or np.any(values < 0):
            raise NowcastNetTilingError(
                f"NowcastNet tile forecast must be finite non-negative {expected}"
            )
        if (
            tile.y_start < 0
            or tile.x_start < 0
            or tile.y_start + tile.height > height
            or tile.x_start + tile.width > width
        ):
            raise NowcastNetTilingError("NowcastNet tile lies outside the output grid")
        # Larger tiles see substantially more spatial context and provide the
        # stable anchor forecast. A superlinear context vote lets small edge
        # tiles extend support without overriding that anchor in overlaps.
        context_weight = (tile.area / minimum_area) ** 1.5
        weights = _cosine_taper(tile.height, tile.width) * context_weight
        ys, xs = _tile_slices(tile)
        weighted_sum[:, ys, xs] += values * weights[np.newaxis, ...]
        total_weight[ys, xs] += weights
        coverage_count[ys, xs] += 1
        normalized.append((tile, values, weights))

    support = total_weight > 0
    output = np.zeros_like(weighted_sum, dtype="float32")
    output[:, support] = (weighted_sum[:, support] / total_weight[support]).astype("float32")

    overlap_differences: list[np.ndarray] = []
    for tile, values, _ in normalized:
        ys, xs = _tile_slices(tile)
        overlap = coverage_count[ys, xs] > 1
        if np.any(overlap):
            overlap_differences.append(
                np.abs(values[:, overlap] - output[:, ys, xs][:, overlap]).ravel()
            )
    overlap_values = (
        np.concatenate(overlap_differences)
        if overlap_differences
        else np.asarray([], dtype="float32")
    )

    primary, primary_values, _ = normalized[0]
    primary_y, primary_x = _tile_slices(primary)
    primary_difference = np.abs(
        primary_values - output[:, primary_y, primary_x]
    ).ravel()
    return NowcastNetStitchedResult(
        rain_rate_mm_h=output,
        valid_mask=np.broadcast_to(support, output.shape).astype("uint8", copy=True),
        tile_coverage_count=coverage_count,
        overlap_cell_count=int(np.count_nonzero(coverage_count > 1)),
        overlap_difference_p95_mm_h=_percentile(overlap_values, 95),
        primary_consistency_mae_mm_h=float(np.mean(primary_difference)),
        primary_consistency_p95_mm_h=_percentile(primary_difference, 95),
        seam_gradient_ratio=_seam_gradient_ratio(output, support, [item[0] for item in normalized]),
    )


def largest_aligned_valid_rectangle(
    valid: np.ndarray,
    *,
    multiple: int,
    minimum_height: int,
    minimum_width: int,
) -> NowcastNetTile | None:
    support = np.asarray(valid, dtype=bool)
    if support.ndim != 2 or multiple < 1:
        raise NowcastNetTilingError("valid support must be 2-D with positive alignment")
    heights = range(_round_up(minimum_height, multiple), support.shape[0] + 1, multiple)
    widths = range(_round_up(minimum_width, multiple), support.shape[1] + 1, multiple)
    shapes = sorted(
        ((height, width) for height in heights for width in widths),
        key=lambda value: (value[0] * value[1], min(value), value[1]),
        reverse=True,
    )
    missing = (~support).astype("int32")
    integral = np.pad(missing, ((1, 0), (1, 0))).cumsum(0).cumsum(1)
    for height, width in shapes:
        window_missing = _window_sums(integral, height, width)
        positions = np.argwhere(window_missing == 0)
        if positions.size:
            y_start, x_start = (int(value) for value in positions[0])
            return NowcastNetTile(y_start, x_start, height, width)
    return None


def _fixed_tile_candidates(
    support: np.ndarray,
    *,
    size: int,
    stride: int,
) -> list[NowcastNetTile]:
    missing = (~support).astype("int32")
    integral = np.pad(missing, ((1, 0), (1, 0))).cumsum(0).cumsum(1)
    window_missing = _window_sums(integral, size, size)
    candidates: list[NowcastNetTile] = []
    for y_start in _axis_origins(window_missing.shape[0] - 1, stride):
        for x_start in _axis_origins(window_missing.shape[1] - 1, stride):
            if window_missing[y_start, x_start] == 0:
                candidates.append(NowcastNetTile(y_start, x_start, size, size))
    return candidates


def _window_sums(integral: np.ndarray, height: int, width: int) -> np.ndarray:
    return (
        integral[height:, width:]
        - integral[:-height, width:]
        - integral[height:, :-width]
        + integral[:-height, :-width]
    )


def _axis_origins(maximum: int, stride: int) -> tuple[int, ...]:
    if maximum < 0:
        return ()
    values = list(range(0, maximum + 1, stride))
    if not values or values[-1] != maximum:
        values.append(maximum)
    return tuple(values)


def _tile_slices(tile: NowcastNetTile) -> tuple[slice, slice]:
    return (
        slice(tile.y_start, tile.y_start + tile.height),
        slice(tile.x_start, tile.x_start + tile.width),
    )


def _cosine_taper(height: int, width: int) -> np.ndarray:
    y = np.sin(np.pi * (np.arange(height, dtype="float64") + 0.5) / height) ** 2
    x = np.sin(np.pi * (np.arange(width, dtype="float64") + 0.5) / width) ** 2
    return np.maximum(np.outer(y, x), np.finfo("float32").eps)


def _seam_gradient_ratio(
    values: np.ndarray,
    support: np.ndarray,
    tiles: list[NowcastNetTile],
) -> float:
    horizontal = np.abs(np.diff(values, axis=2))
    vertical = np.abs(np.diff(values, axis=1))
    horizontal_valid = support[:, :-1] & support[:, 1:]
    vertical_valid = support[:-1, :] & support[1:, :]
    all_gradients = np.concatenate(
        (horizontal[:, horizontal_valid].ravel(), vertical[:, vertical_valid].ravel())
    )
    seam_horizontal = np.zeros_like(horizontal_valid)
    seam_vertical = np.zeros_like(vertical_valid)
    for tile in tiles:
        for x_edge in (tile.x_start, tile.x_start + tile.width):
            if 0 < x_edge < support.shape[1]:
                seam_horizontal[
                    tile.y_start : tile.y_start + tile.height, x_edge - 1
                ] = True
        for y_edge in (tile.y_start, tile.y_start + tile.height):
            if 0 < y_edge < support.shape[0]:
                seam_vertical[
                    y_edge - 1, tile.x_start : tile.x_start + tile.width
                ] = True
    seam_horizontal &= horizontal_valid
    seam_vertical &= vertical_valid
    seam_gradients = np.concatenate(
        (
            horizontal[:, seam_horizontal].ravel(),
            vertical[:, seam_vertical].ravel(),
        )
    )
    baseline = _percentile(all_gradients, 95)
    if baseline <= 0:
        return 0.0 if _percentile(seam_gradients, 95) <= 0 else float("inf")
    return _percentile(seam_gradients, 95) / baseline


def _percentile(values: np.ndarray, percentile: float) -> float:
    return float(np.percentile(values, percentile)) if values.size else 0.0


def _round_up(value: int, multiple: int) -> int:
    return ((value + multiple - 1) // multiple) * multiple
