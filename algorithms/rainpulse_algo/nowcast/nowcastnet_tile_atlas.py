"""Fixed, halo-aware NowcastNet Tile Atlas for the Fujian shadow workflow."""

from __future__ import annotations

from collections import defaultdict
from collections.abc import Iterable, Sequence
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import numpy as np
import yaml


class TileAtlasError(ValueError):
    """Raised when a Tile Atlas or its inputs are inconsistent."""


@dataclass(frozen=True)
class TrustedWindow:
    y_start: int
    y_end: int
    x_start: int
    x_end: int


@dataclass(frozen=True)
class AtlasTile:
    tile_id: str
    y_start: int
    x_start: int
    height: int
    width: int
    trusted: TrustedWindow

    @property
    def y_end(self) -> int:
        return self.y_start + self.height

    @property
    def x_end(self) -> int:
        return self.x_start + self.width

    @property
    def global_trusted(self) -> TrustedWindow:
        return TrustedWindow(
            self.y_start + self.trusted.y_start,
            self.y_start + self.trusted.y_end,
            self.x_start + self.trusted.x_start,
            self.x_start + self.trusted.x_end,
        )


@dataclass(frozen=True)
class TileAtlas:
    atlas_version: str
    grid_id: str
    grid_config_version: str
    grid_shape: tuple[int, int]
    spatial_multiple: int
    missing_policy: str
    tiles: tuple[AtlasTile, ...]


@dataclass(frozen=True)
class PreparedTile:
    tile: AtlasTile
    rain_rate_mm_h: np.ndarray
    valid_mask: np.ndarray


@dataclass(frozen=True)
class AtlasPreparation:
    eligible: tuple[PreparedTile, ...]
    rejected: tuple[tuple[str, str], ...]
    trusted_coverage_ratio: float
    publication_mask: np.ndarray
    filled_missing_cell_count: int


def load_tile_atlas(path: str | Path) -> TileAtlas:
    source = Path(path)
    try:
        raw: dict[str, Any] = yaml.safe_load(source.read_text(encoding="utf-8"))
        if raw["schema_version"] != "1.0":
            raise TileAtlasError("unsupported Tile Atlas schema")
        grid = raw["grid"]
        policy = raw["policy"]
        tiles = tuple(
            AtlasTile(
                tile_id=str(item["tile_id"]),
                y_start=int(item["input"]["y_start"]),
                x_start=int(item["input"]["x_start"]),
                height=int(item["input"]["height"]),
                width=int(item["input"]["width"]),
                trusted=TrustedWindow(
                    int(item["trusted"]["y_start"]),
                    int(item["trusted"]["y_end"]),
                    int(item["trusted"]["x_start"]),
                    int(item["trusted"]["x_end"]),
                ),
            )
            for item in raw["tiles"]
        )
        atlas = TileAtlas(
            atlas_version=str(raw["atlas_version"]),
            grid_id=str(grid["grid_id"]),
            grid_config_version=str(grid["grid_config_version"]),
            grid_shape=(int(grid["height"]), int(grid["width"])),
            spatial_multiple=int(policy["spatial_multiple"]),
            missing_policy=str(policy["missing_policy"]),
            tiles=tiles,
        )
    except TileAtlasError:
        raise
    except (OSError, KeyError, TypeError, ValueError, yaml.YAMLError) as error:
        raise TileAtlasError(f"invalid Tile Atlas {source}: {error}") from error
    validate_tile_atlas(atlas)
    return atlas


def validate_tile_atlas(atlas: TileAtlas) -> None:
    height, width = atlas.grid_shape
    if not atlas.atlas_version or not atlas.grid_id or not atlas.grid_config_version:
        raise TileAtlasError("Tile Atlas identity is incomplete")
    if height < 1 or width < 1 or atlas.spatial_multiple < 1 or not atlas.tiles:
        raise TileAtlasError("Tile Atlas geometry is invalid")
    if atlas.missing_policy != "reject_any_missing":
        raise TileAtlasError("NowcastNet Tile Atlas must reject missing input")
    coverage = np.zeros(atlas.grid_shape, dtype="uint8")
    tile_ids: set[str] = set()
    for tile in atlas.tiles:
        if not tile.tile_id or tile.tile_id in tile_ids:
            raise TileAtlasError("Tile IDs must be unique")
        tile_ids.add(tile.tile_id)
        if min(tile.y_start, tile.x_start) < 0 or min(tile.height, tile.width) < 1:
            raise TileAtlasError(f"invalid input window for {tile.tile_id}")
        if tile.y_end > height or tile.x_end > width:
            raise TileAtlasError(f"input window leaves grid for {tile.tile_id}")
        if tile.height % atlas.spatial_multiple or tile.width % atlas.spatial_multiple:
            raise TileAtlasError(f"input size violates spatial multiple for {tile.tile_id}")
        trusted = tile.trusted
        if (
            min(trusted.y_start, trusted.x_start) < 0
            or trusted.y_end <= trusted.y_start
            or trusted.x_end <= trusted.x_start
            or trusted.y_end > tile.height
            or trusted.x_end > tile.width
        ):
            raise TileAtlasError(f"trusted window is invalid for {tile.tile_id}")
        global_trusted = tile.global_trusted
        coverage[
            global_trusted.y_start : global_trusted.y_end,
            global_trusted.x_start : global_trusted.x_end,
        ] += 1
    if np.any(coverage == 0):
        raise TileAtlasError("trusted windows do not cover the target grid")


def prepare_atlas_tiles(
    rain_rate_mm_h: np.ndarray,
    valid_mask: np.ndarray,
    atlas: TileAtlas,
) -> AtlasPreparation:
    rate = np.asarray(rain_rate_mm_h, dtype="float32")
    valid = np.asarray(valid_mask, dtype="uint8")
    if rate.ndim != 3 or rate.shape != valid.shape or rate.shape[1:] != atlas.grid_shape:
        raise TileAtlasError("Tile Atlas input shape differs")
    if np.any((valid != 0) & (valid != 1)):
        raise TileAtlasError("Tile Atlas valid mask is not binary")
    if np.any(~np.isfinite(rate[valid == 1])) or np.any(rate[valid == 1] < 0):
        raise TileAtlasError("Tile Atlas valid rate is invalid")

    eligible: list[PreparedTile] = []
    trusted_support = np.zeros(atlas.grid_shape, dtype=bool)
    filled_missing_cell_count = 0
    for tile in atlas.tiles:
        ys = slice(tile.y_start, tile.y_end)
        xs = slice(tile.x_start, tile.x_end)
        tile_valid = valid[:, ys, xs]
        # The frozen parent adapter still rejects missing model inputs.  Give
        # every fixed tile a finite, conservative dry context while retaining
        # the real latest-QPE support separately for publication.  This avoids
        # dropping a whole 128x128 tile because one historical cell is absent.
        tile_rate = np.where(tile_valid == 1, rate[:, ys, xs], 0.0)
        filled_missing_cell_count += int(np.count_nonzero(tile_valid == 0))
        eligible.append(
            PreparedTile(
                tile=tile,
                rain_rate_mm_h=np.ascontiguousarray(tile_rate, dtype="float32"),
                valid_mask=np.ones(tile_valid.shape, dtype="uint8"),
            )
        )
        trusted = tile.global_trusted
        trusted_support[
            trusted.y_start : trusted.y_end,
            trusted.x_start : trusted.x_end,
        ] = True
    return AtlasPreparation(
        eligible=tuple(eligible),
        rejected=(),
        trusted_coverage_ratio=float(np.mean(trusted_support)),
        publication_mask=np.ascontiguousarray(valid[-1], dtype="uint8"),
        filled_missing_cell_count=filled_missing_cell_count,
    )


def group_prepared_tiles(
    values: Sequence[PreparedTile],
) -> dict[tuple[int, int], tuple[PreparedTile, ...]]:
    groups: dict[tuple[int, int], list[PreparedTile]] = defaultdict(list)
    for item in values:
        groups[(item.tile.height, item.tile.width)].append(item)
    return {key: tuple(group) for key, group in sorted(groups.items())}


def chunked(values: Sequence[PreparedTile], size: int) -> Iterable[tuple[PreparedTile, ...]]:
    if size < 1:
        raise TileAtlasError("GPU batch size must be positive")
    for offset in range(0, len(values), size):
        yield tuple(values[offset : offset + size])


def stitch_member_tiles(
    values: Sequence[tuple[AtlasTile, np.ndarray]], *, output_shape: tuple[int, int]
) -> tuple[np.ndarray, np.ndarray]:
    """Stitch only trusted windows; unserved cells remain NaN/missing."""

    if not values:
        raise TileAtlasError("no Tile Atlas forecasts are available")
    first = np.asarray(values[0][1], dtype="float32")
    if first.ndim != 4:
        raise TileAtlasError("tile forecasts must be member x lead x y x x")
    members, leads = first.shape[:2]
    weighted = np.zeros((members, leads, *output_shape), dtype="float64")
    weights = np.zeros(output_shape, dtype="float64")
    for tile, raw_forecast in values:
        forecast = np.asarray(raw_forecast, dtype="float32")
        if forecast.shape != (members, leads, tile.height, tile.width):
            raise TileAtlasError(f"tile forecast shape differs for {tile.tile_id}")
        trusted = tile.trusted
        global_trusted = tile.global_trusted
        local = forecast[:, :, trusted.y_start : trusted.y_end, trusted.x_start : trusted.x_end]
        edge_weight = _raised_edge_weight(local.shape[-2:])
        weighted[
            :,
            :,
            global_trusted.y_start : global_trusted.y_end,
            global_trusted.x_start : global_trusted.x_end,
        ] += local * edge_weight
        weights[
            global_trusted.y_start : global_trusted.y_end,
            global_trusted.x_start : global_trusted.x_end,
        ] += edge_weight
    valid = weights > 0
    result = np.full((members, leads, *output_shape), np.nan, dtype="float32")
    result[:, :, valid] = (weighted[:, :, valid] / weights[valid]).astype("float32")
    return result, np.broadcast_to(valid, result.shape).astype("uint8", copy=True)


def _raised_edge_weight(shape: tuple[int, int]) -> np.ndarray:
    height, width = shape
    if min(height, width) < 1:
        raise TileAtlasError("trusted output shape is invalid")
    ramp_y = np.minimum(np.arange(height) + 1, np.arange(height, 0, -1)).astype("float64")
    ramp_x = np.minimum(np.arange(width) + 1, np.arange(width, 0, -1)).astype("float64")
    return np.outer(ramp_y, ramp_x) / max(float(np.max(ramp_y) * np.max(ramp_x)), 1.0)
