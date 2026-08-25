from __future__ import annotations

import json
from collections import OrderedDict
from pathlib import Path

import numpy as np
import rasterio

from .ancillary import AncillarySource, iter_dem_tiles, sha256_file


class DEMAssetError(RuntimeError):
    """Raised when a versioned DEM runtime asset cannot be trusted or sampled."""


class VerifiedDEMTileStore:
    """Samples accepted native Copernicus tiles without creating a coarse DEM."""

    def __init__(
        self,
        source: AncillarySource,
        root: str | Path,
        *,
        expected_asset_version: str,
        expected_config_version: str,
        cache_tiles: int = 8,
    ) -> None:
        if source.dem.asset_version != expected_asset_version:
            raise DEMAssetError("DEM asset version differs from the Hybrid Scan profile")
        if source.config_version != expected_config_version:
            raise DEMAssetError("ancillary config version differs from the Hybrid Scan profile")
        if cache_tiles < 1:
            raise DEMAssetError("DEM cache must retain at least one tile")
        self.source = source
        self.root = Path(root).resolve()
        self.cache_tiles = cache_tiles
        self._cache: OrderedDict[str, tuple[np.ndarray, tuple[float, float, float, float]]] = (
            OrderedDict()
        )
        self._validated: set[str] = set()
        self._tiles = {
            (tile.latitude, tile.longitude): tile for tile in iter_dem_tiles(source)
        }
        manifest_path = (
            self.root / "manifests" / f"{source.config_version}.json"
        )
        verification_path = (
            self.root
            / "manifests"
            / f"{source.config_version}.verification.json"
        )
        if not manifest_path.is_file() or not verification_path.is_file():
            raise DEMAssetError("DEM runtime manifest and verification are required")
        self.manifest_path = manifest_path
        manifest = json.loads(manifest_path.read_text())
        verification = json.loads(verification_path.read_text())
        if (
            manifest.get("domain_id") != source.domain_id
            or manifest.get("config_version") != source.config_version
            or manifest.get("dem_asset_version") != source.dem.asset_version
        ):
            raise DEMAssetError("DEM runtime manifest identity is invalid")
        if (
            verification.get("status") != "accepted"
            or verification.get("domain_id") != source.domain_id
            or verification.get("config_version") != source.config_version
        ):
            raise DEMAssetError("DEM runtime verification is not accepted")
        assets = manifest.get("assets")
        if not isinstance(assets, list):
            raise DEMAssetError("DEM runtime manifest has no asset list")
        self._assets = {
            item["tile_id"]: item
            for item in assets
            if item.get("asset_type") in {"dem_tile", "dem_tile_absent"}
            and isinstance(item.get("tile_id"), str)
        }
        if len(self._assets) != source.dem.planned_tile_count:
            raise DEMAssetError("DEM runtime manifest does not cover every planned tile")

    def sample(self, longitude: np.ndarray, latitude: np.ndarray) -> np.ndarray:
        lon, lat = np.broadcast_arrays(
            np.asarray(longitude, dtype="float64"),
            np.asarray(latitude, dtype="float64"),
        )
        result = np.full(lon.shape, np.nan, dtype="float32")
        finite = np.isfinite(lon) & np.isfinite(lat)
        inside = finite & (
            (lon >= self.source.bounds.west)
            & (lon <= self.source.bounds.east)
            & (lat >= self.source.bounds.south)
            & (lat <= self.source.bounds.north)
        )
        if not np.any(inside):
            return result

        tile_lon = np.floor(lon[inside]).astype("int16")
        tile_lat = np.floor(lat[inside]).astype("int16")
        tile_lon = np.minimum(tile_lon, self.source.bounds.east - 1)
        tile_lat = np.minimum(tile_lat, self.source.bounds.north - 1)
        flat_indexes = np.flatnonzero(inside)
        pairs = np.stack((tile_lat, tile_lon), axis=1)
        for latitude_index, longitude_index in np.unique(pairs, axis=0):
            pair_mask = (tile_lat == latitude_index) & (tile_lon == longitude_index)
            indexes = flat_indexes[pair_mask]
            tile = self._tiles.get((int(latitude_index), int(longitude_index)))
            if tile is None:
                raise DEMAssetError(
                    f"DEM point references an unplanned tile {latitude_index}/{longitude_index}"
                )
            asset = self._assets[tile.tile_id]
            if asset["asset_type"] == "dem_tile_absent":
                result.flat[indexes] = 0.0
                continue
            values, bounds = self._load_tile(tile.tile_id, asset)
            left, bottom, right, top = bounds
            width = values.shape[1]
            height = values.shape[0]
            columns = np.floor((lon.flat[indexes] - left) / (right - left) * width).astype(
                "int64"
            )
            rows = np.floor((top - lat.flat[indexes]) / (top - bottom) * height).astype(
                "int64"
            )
            columns = np.clip(columns, 0, width - 1)
            rows = np.clip(rows, 0, height - 1)
            sampled = values[rows, columns].astype("float32", copy=True)
            sampled[~np.isfinite(sampled)] = np.nan
            result.flat[indexes] = sampled
        return result

    def _load_tile(
        self,
        tile_id: str,
        asset: dict[str, object],
    ) -> tuple[np.ndarray, tuple[float, float, float, float]]:
        cached = self._cache.pop(tile_id, None)
        if cached is not None:
            self._cache[tile_id] = cached
            return cached
        relative_path = asset.get("relative_path")
        if not isinstance(relative_path, str):
            raise DEMAssetError(f"DEM tile {tile_id} has no runtime path")
        path = (self.root / relative_path).resolve()
        if self.root not in path.parents or not path.is_file():
            raise DEMAssetError(f"DEM tile is unavailable: {path}")
        if path.stat().st_size != asset.get("size_bytes"):
            raise DEMAssetError(f"DEM tile size differs from the manifest: {path}")
        if tile_id not in self._validated:
            if sha256_file(path) != asset.get("sha256"):
                raise DEMAssetError(f"DEM tile SHA-256 differs from the manifest: {path}")
            self._validated.add(tile_id)
        with rasterio.open(path) as dataset:
            if dataset.crs is None or dataset.crs.to_epsg() != 4326:
                raise DEMAssetError(f"DEM tile CRS is not EPSG:4326: {path}")
            values = dataset.read(1, masked=True).filled(np.nan).astype("float32")
            bounds = tuple(float(item) for item in dataset.bounds)
        cached = (values, bounds)
        self._cache[tile_id] = cached
        while len(self._cache) > self.cache_tiles:
            self._cache.popitem(last=False)
        return cached
