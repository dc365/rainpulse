from __future__ import annotations

import argparse
import fcntl
import hashlib
import json
import os
import shutil
import subprocess
import sys
import zipfile
from concurrent.futures import ThreadPoolExecutor, as_completed
from contextlib import contextmanager
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path, PurePosixPath
from typing import Any

import rasterio
import shapefile
import yaml
from pyproj import Geod
from shapely.geometry import box, shape
from shapely.strtree import STRtree


class AncillaryError(RuntimeError):
    """Raised when an ancillary source or downloaded asset is invalid."""


@dataclass(frozen=True)
class Bounds:
    west: int
    east: int
    south: int
    north: int

    @property
    def tile_count(self) -> int:
        return (self.east - self.west) * (self.north - self.south)


@dataclass(frozen=True)
class DEMSource:
    asset_version: str
    base_url: str
    planned_tile_count: int
    storage_prefix: str
    native_resolution_arc_seconds: float
    max_uncovered_land_area_km2_per_tile: float


@dataclass(frozen=True)
class CoastlineSource:
    asset_version: str
    source_url: str
    source_sha256: str
    storage_prefix: str


@dataclass(frozen=True)
class AncillarySource:
    domain_id: str
    config_version: str
    bounds: Bounds
    dem: DEMSource
    coastline: CoastlineSource


@dataclass(frozen=True)
class DEMTile:
    latitude: int
    longitude: int
    tile_id: str
    url: str
    relative_path: Path


def load_source(path: Path) -> AncillarySource:
    raw = yaml.safe_load(path.read_text())
    try:
        bounds = Bounds(**raw["bounds"])
        dem = DEMSource(
            asset_version=raw["dem"]["asset_version"],
            base_url=raw["dem"]["base_url"],
            planned_tile_count=int(raw["dem"]["planned_tile_count"]),
            storage_prefix=raw["dem"]["storage_prefix"],
            native_resolution_arc_seconds=float(raw["dem"]["native_resolution_arc_seconds"]),
            max_uncovered_land_area_km2_per_tile=float(
                raw["dem"]["max_uncovered_land_area_km2_per_tile"]
            ),
        )
        coastline = CoastlineSource(
            asset_version=raw["coastline"]["asset_version"],
            source_url=raw["coastline"]["source_url"],
            source_sha256=raw["coastline"]["source_sha256"],
            storage_prefix=raw["coastline"]["storage_prefix"],
        )
        source = AncillarySource(
            domain_id=raw["domain_id"],
            config_version=raw["config_version"],
            bounds=bounds,
            dem=dem,
            coastline=coastline,
        )
    except (KeyError, TypeError, ValueError) as exc:
        raise AncillaryError(f"invalid ancillary source configuration {path}: {exc}") from exc

    if bounds.west >= bounds.east or bounds.south >= bounds.north:
        raise AncillaryError("ancillary bounds must have positive width and height")
    if bounds.tile_count != dem.planned_tile_count:
        raise AncillaryError(
            f"DEM tile count mismatch: bounds require {bounds.tile_count}, "
            f"configuration declares {dem.planned_tile_count}"
        )
    if not dem.base_url.startswith("https://") or not coastline.source_url.startswith(
        "https://"
    ):
        raise AncillaryError("ancillary source URLs must use HTTPS")
    return source


def _degree_token(value: int, positive: str, negative: str, width: int) -> str:
    hemisphere = positive if value >= 0 else negative
    return f"{hemisphere}{abs(value):0{width}d}_00"


def iter_dem_tiles(source: AncillarySource) -> tuple[DEMTile, ...]:
    tiles: list[DEMTile] = []
    base_url = source.dem.base_url.rstrip("/")
    for latitude in range(source.bounds.south, source.bounds.north):
        for longitude in range(source.bounds.west, source.bounds.east):
            lat_token = _degree_token(latitude, "N", "S", 2)
            lon_token = _degree_token(longitude, "E", "W", 3)
            tile_id = f"Copernicus_DSM_COG_10_{lat_token}_{lon_token}_DEM"
            filename = f"{tile_id}.tif"
            tiles.append(
                DEMTile(
                    latitude=latitude,
                    longitude=longitude,
                    tile_id=tile_id,
                    url=f"{base_url}/{tile_id}/{filename}",
                    relative_path=Path(source.dem.storage_prefix) / "tiles" / filename,
                )
            )
    if len(tiles) != source.dem.planned_tile_count:
        raise AncillaryError(
            f"generated {len(tiles)} DEM tiles, expected {source.dem.planned_tile_count}"
        )
    return tuple(tiles)


def sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(8 * 1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _download_with_curl(
    url: str,
    destination: Path,
    proxy: str | None,
    *,
    expected_size: int | None = None,
) -> None:
    if destination.is_file() and (
        expected_size is None or destination.stat().st_size == expected_size
    ):
        return
    curl = shutil.which("curl")
    if curl is None:
        raise AncillaryError("curl is required for resumable ancillary downloads")

    destination.parent.mkdir(parents=True, exist_ok=True)
    partial = destination.with_name(f"{destination.name}.part")
    if destination.is_file():
        destination_size = destination.stat().st_size
        partial_size = partial.stat().st_size if partial.is_file() else -1
        destination_is_best_prefix = destination_size > partial_size and destination_size <= (
            expected_size or destination_size
        )
        if destination_is_best_prefix:
            os.replace(destination, partial)
        else:
            destination.unlink()
    if partial.is_file() and expected_size is not None and partial.stat().st_size > expected_size:
        partial.unlink()
    command = [
        curl,
        "--fail",
        "--location",
        "--retry",
        "5",
        "--retry-connrefused",
        "--silent",
        "--show-error",
        "--continue-at",
        "-",
        "--output",
        str(partial),
        url,
    ]
    if proxy:
        command[1:1] = ["--proxy", proxy]
    try:
        subprocess.run(command, check=True)
    except subprocess.CalledProcessError as exc:
        raise AncillaryError(f"download failed for {url}") from exc
    if not partial.is_file() or partial.stat().st_size == 0:
        raise AncillaryError(f"download produced an empty file for {url}")
    if expected_size is not None and partial.stat().st_size != expected_size:
        raise AncillaryError(
            f"download size mismatch for {url}: expected {expected_size}, "
            f"got {partial.stat().st_size}"
        )
    os.replace(partial, destination)


def _remote_metadata(url: str, proxy: str | None) -> tuple[int, int | None]:
    curl = shutil.which("curl")
    if curl is None:
        raise AncillaryError("curl is required for ancillary source checks")
    command = [
        curl,
        "--head",
        "--location",
        "--silent",
        "--show-error",
        "--write-out",
        "\n%{http_code}",
        url,
    ]
    if proxy:
        command[1:1] = ["--proxy", proxy]
    result = subprocess.run(command, check=False, capture_output=True, text=True)
    try:
        output, status_text = result.stdout.rsplit("\n", 1)
        status = int(status_text[-3:])
    except ValueError as exc:
        raise AncillaryError(f"could not determine HTTP status for {url}: {result.stderr}") from exc
    if status not in {200, 404}:
        raise AncillaryError(f"unexpected HTTP status {status} for {url}: {result.stderr}")
    content_lengths = []
    for line in output.splitlines():
        name, separator, value = line.partition(":")
        if separator and name.strip().lower() == "content-length":
            try:
                content_lengths.append(int(value.strip()))
            except ValueError:
                continue
    return status, content_lengths[-1] if content_lengths else None


def _safe_extract_selected_coastline(archive: Path, destination: Path) -> tuple[Path, ...]:
    prefixes = (
        "GSHHS_shp/f/GSHHS_f_L1.",
        "GSHHS_shp/h/GSHHS_h_L1.",
    )
    documentation_names = {"README.TXT", "SHAPEFILES.TXT", "LICENSE.TXT"}
    extracted: list[Path] = []
    destination.mkdir(parents=True, exist_ok=True)
    root = destination.resolve()
    with zipfile.ZipFile(archive) as bundle:
        for member in bundle.infolist():
            normalized = str(PurePosixPath(member.filename))
            basename = PurePosixPath(normalized).name.upper()
            selected = normalized.startswith(prefixes) or basename in documentation_names
            if not selected or member.is_dir():
                continue
            target = (destination / normalized).resolve()
            if root not in target.parents:
                raise AncillaryError(f"unsafe path in coastline archive: {member.filename}")
            target.parent.mkdir(parents=True, exist_ok=True)
            with bundle.open(member) as source_handle, target.open("wb") as target_handle:
                shutil.copyfileobj(source_handle, target_handle)
            extracted.append(target)
    required = {
        (destination / "GSHHS_shp" / resolution / f"GSHHS_{resolution}_L1.shp").resolve()
        for resolution in ("f", "h")
    }
    missing = [str(path) for path in required if path not in extracted]
    if missing:
        raise AncillaryError(f"coastline archive is missing required shapefiles: {missing}")
    return tuple(sorted(extracted))


def _download_one(tile: DEMTile, root: Path, proxy: str | None) -> dict[str, Any]:
    path = root / tile.relative_path
    status, content_length = _remote_metadata(tile.url, proxy)
    if status == 404:
        partial = path.with_name(f"{path.name}.part")
        if partial.is_file():
            partial.unlink()
        return {
            "asset_type": "dem_tile_absent",
            "tile_id": tile.tile_id,
            "source_url": tile.url,
            "reason": "source_not_published_candidate_ocean_tile",
        }
    _download_with_curl(tile.url, path, proxy, expected_size=content_length)
    return {
        "asset_type": "dem_tile",
        "tile_id": tile.tile_id,
        "source_url": tile.url,
        "relative_path": path.relative_to(root).as_posix(),
        "size_bytes": path.stat().st_size,
        "sha256": sha256_file(path),
    }


def _write_json_atomic(path: Path, value: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_name(f"{path.name}.tmp")
    temporary.write_text(json.dumps(value, ensure_ascii=False, indent=2, sort_keys=True) + "\n")
    os.replace(temporary, path)


@contextmanager
def _download_lock(root: Path):
    path = root / ".download.lock"
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w") as handle:
        try:
            fcntl.flock(handle.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError as exc:
            raise AncillaryError(f"another ancillary download holds {path}") from exc
        handle.write(f"pid={os.getpid()}\n")
        handle.flush()
        try:
            yield
        finally:
            fcntl.flock(handle.fileno(), fcntl.LOCK_UN)


def download_assets(
    source: AncillarySource,
    root: Path,
    *,
    workers: int,
    proxy: str | None,
) -> Path:
    root.mkdir(parents=True, exist_ok=True)
    with _download_lock(root):
        return _download_assets_unlocked(source, root, workers=workers, proxy=proxy)


def _download_assets_unlocked(
    source: AncillarySource,
    root: Path,
    *,
    workers: int,
    proxy: str | None,
) -> Path:
    if workers < 1 or workers > 16:
        raise AncillaryError("download workers must be between 1 and 16")
    assets: list[dict[str, Any]] = []
    tiles = iter_dem_tiles(source)
    with ThreadPoolExecutor(max_workers=workers) as executor:
        futures = {executor.submit(_download_one, tile, root, proxy): tile for tile in tiles}
        for future in as_completed(futures):
            assets.append(future.result())

    coastline_root = root / source.coastline.storage_prefix
    archive = coastline_root / "source" / "gshhg-shp-2.3.7.zip"
    _download_with_curl(source.coastline.source_url, archive, proxy)
    archive_sha = sha256_file(archive)
    if archive_sha != source.coastline.source_sha256:
        raise AncillaryError(
            f"coastline SHA-256 mismatch: expected {source.coastline.source_sha256}, "
            f"got {archive_sha}"
        )
    _safe_extract_selected_coastline(archive, coastline_root / "extracted")
    assets.append(
        {
            "asset_type": "coastline_archive",
            "source_url": source.coastline.source_url,
            "relative_path": archive.relative_to(root).as_posix(),
            "size_bytes": archive.stat().st_size,
            "sha256": archive_sha,
        }
    )

    manifest = {
        "schema_version": "1.0",
        "domain_id": source.domain_id,
        "config_version": source.config_version,
        "created_at": datetime.now(UTC).isoformat().replace("+00:00", "Z"),
        "bounds": source.bounds.__dict__,
        "dem_asset_version": source.dem.asset_version,
        "coastline_asset_version": source.coastline.asset_version,
        "asset_count": len(assets),
        "assets": sorted(
            assets,
            key=lambda item: item.get("relative_path", item.get("tile_id", "")),
        ),
    }
    manifest_path = root / "manifests" / f"{source.config_version}.json"
    _write_json_atomic(manifest_path, manifest)
    return manifest_path


def _verify_dem_tile(path: Path, tile: DEMTile, resolution_arc_seconds: float) -> dict[str, Any]:
    with rasterio.open(path) as dataset:
        if dataset.crs is None or dataset.crs.to_epsg() != 4326:
            raise AncillaryError(f"{path} CRS is {dataset.crs}, expected EPSG:4326")
        if dataset.count != 1:
            raise AncillaryError(f"{path} has {dataset.count} bands, expected one")
        expected_size = round(3600 / resolution_arc_seconds)
        if dataset.width not in {expected_size, expected_size + 1} or dataset.height not in {
            expected_size,
            expected_size + 1,
        }:
            raise AncillaryError(
                f"{path} has shape {dataset.height}x{dataset.width}, expected about "
                f"{expected_size}x{expected_size}"
            )
        tolerance = 2 * resolution_arc_seconds / 3600
        expected = (tile.longitude, tile.latitude, tile.longitude + 1, tile.latitude + 1)
        actual = tuple(dataset.bounds)
        if any(abs(left - right) > tolerance for left, right in zip(actual, expected, strict=True)):
            raise AncillaryError(f"{path} bounds are {actual}, expected {expected}")
        return {
            "width": dataset.width,
            "height": dataset.height,
            "dtype": dataset.dtypes[0],
            "nodata": dataset.nodata,
            "bounds": actual,
        }


def _verify_coastline(source: AncillarySource, root: Path) -> dict[str, Any]:
    extracted_root = root / source.coastline.storage_prefix / "extracted" / "GSHHS_shp"
    summaries: dict[str, Any] = {}
    for resolution in ("f", "h"):
        path = extracted_root / resolution / f"GSHHS_{resolution}_L1.shp"
        if not path.is_file():
            raise AncillaryError(f"missing coastline shapefile {path}")
        with shapefile.Reader(str(path)) as reader:
            bbox = tuple(float(value) for value in reader.bbox)
            if not (
                bbox[0] <= source.bounds.west
                and bbox[1] <= source.bounds.south
                and bbox[2] >= source.bounds.east
                and bbox[3] >= source.bounds.north
            ):
                raise AncillaryError(
                    f"coastline {resolution} bounds {bbox} do not cover {source.bounds}"
                )
            summaries[resolution] = {"feature_count": len(reader), "bounds": bbox}
    return summaries


def _verify_absent_tiles_are_ocean(
    source: AncillarySource,
    root: Path,
    absent_tiles: list[DEMTile],
) -> list[dict[str, Any]]:
    if not absent_tiles:
        return []
    path = (
        root
        / source.coastline.storage_prefix
        / "extracted"
        / "GSHHS_shp"
        / "h"
        / "GSHHS_h_L1.shp"
    )
    domain_bbox = (
        source.bounds.west,
        source.bounds.south,
        source.bounds.east,
        source.bounds.north,
    )
    with shapefile.Reader(str(path)) as reader:
        land = [shape(item.__geo_interface__) for item in reader.iterShapes(bbox=domain_bbox)]
    if not land:
        raise AncillaryError("coastline contains no land polygons in the ancillary domain")
    tree = STRtree(land)
    geod = Geod(ellps="WGS84")
    subthreshold_land: list[dict[str, Any]] = []
    for tile in absent_tiles:
        footprint = box(tile.longitude, tile.latitude, tile.longitude + 1, tile.latitude + 1)
        candidate_indexes = tree.query(footprint)
        land_area_km2 = 0.0
        for index in candidate_indexes:
            intersection = land[int(index)].intersection(footprint)
            if not intersection.is_empty:
                area_m2, _ = geod.geometry_area_perimeter(intersection)
                land_area_km2 += abs(area_m2) / 1_000_000
        if land_area_km2 > source.dem.max_uncovered_land_area_km2_per_tile:
            raise AncillaryError(
                f"DEM tile {tile.tile_id} is absent from the source but intersects "
                f"{land_area_km2:.6f} km2 of GSHHG land; limit is "
                f"{source.dem.max_uncovered_land_area_km2_per_tile:.6f} km2"
            )
        if land_area_km2 > 0:
            subthreshold_land.append(
                {"tile_id": tile.tile_id, "uncovered_land_area_km2": land_area_km2}
            )
    return subthreshold_land


def verify_assets(source: AncillarySource, root: Path) -> dict[str, Any]:
    manifest_path = root / "manifests" / f"{source.config_version}.json"
    if not manifest_path.is_file():
        raise AncillaryError(f"missing runtime manifest {manifest_path}")
    manifest = json.loads(manifest_path.read_text())
    assets = manifest.get("assets", [])
    if manifest.get("asset_count") != source.dem.planned_tile_count + 1:
        raise AncillaryError("runtime manifest asset count does not match configured sources")
    if len(assets) != manifest["asset_count"]:
        raise AncillaryError("runtime manifest asset list length is inconsistent")

    for asset in assets:
        if asset["asset_type"] == "dem_tile_absent":
            continue
        path = root / asset["relative_path"]
        if not path.is_file() or path.stat().st_size != asset["size_bytes"]:
            raise AncillaryError(f"asset missing or wrong size: {path}")
        digest = sha256_file(path)
        if digest != asset["sha256"]:
            raise AncillaryError(f"asset SHA-256 mismatch: {path}")

    tiles_by_id = {tile.tile_id: tile for tile in iter_dem_tiles(source)}
    dem_summaries: list[dict[str, Any]] = []
    absent_tiles: list[DEMTile] = []
    for asset in assets:
        if asset["asset_type"] == "dem_tile_absent":
            tile = tiles_by_id.get(asset["tile_id"])
            if tile is None:
                raise AncillaryError(f"manifest contains unexpected DEM tile {asset['tile_id']}")
            absent_tiles.append(tile)
            continue
        if asset["asset_type"] != "dem_tile":
            continue
        tile = tiles_by_id.get(asset["tile_id"])
        if tile is None:
            raise AncillaryError(f"manifest contains unexpected DEM tile {asset['tile_id']}")
        summary = _verify_dem_tile(
            root / asset["relative_path"], tile, source.dem.native_resolution_arc_seconds
        )
        dem_summaries.append({"tile_id": tile.tile_id, **summary})

    coastline_summary = _verify_coastline(source, root)
    subthreshold_land = _verify_absent_tiles_are_ocean(source, root, absent_tiles)
    total_size = sum(int(asset.get("size_bytes", 0)) for asset in assets)
    verification = {
        "schema_version": "1.0",
        "domain_id": source.domain_id,
        "config_version": source.config_version,
        "verified_at": datetime.now(UTC).isoformat().replace("+00:00", "Z"),
        "status": "accepted",
        "asset_count": len(assets),
        "dem_tile_count": len(dem_summaries),
        "dem_absent_ocean_tile_count": len(absent_tiles),
        "dem_absent_tiles_with_subthreshold_land": subthreshold_land,
        "total_size_bytes": total_size,
        "dem_shapes": sorted(
            {f"{item['height']}x{item['width']}" for item in dem_summaries}
        ),
        "dem_dtypes": sorted({str(item["dtype"]) for item in dem_summaries}),
        "coastline": coastline_summary,
    }
    verification_path = root / "manifests" / f"{source.config_version}.verification.json"
    _write_json_atomic(verification_path, verification)
    return verification


def build_plan(source: AncillarySource) -> dict[str, Any]:
    tiles = iter_dem_tiles(source)
    return {
        "domain_id": source.domain_id,
        "config_version": source.config_version,
        "bounds": source.bounds.__dict__,
        "dem_planned_tile_count": len(tiles),
        "first_dem_tile": tiles[0].tile_id,
        "last_dem_tile": tiles[-1].tile_id,
        "coastline_url": source.coastline.source_url,
    }


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Prepare RainPulse DEM and coastline assets")
    parser.add_argument("--config", type=Path, required=True)
    subparsers = parser.add_subparsers(dest="command", required=True)
    subparsers.add_parser("plan")

    download = subparsers.add_parser("download")
    download.add_argument("--root", type=Path, required=True)
    download.add_argument("--workers", type=int, default=4)
    download.add_argument("--proxy")

    verify = subparsers.add_parser("verify")
    verify.add_argument("--root", type=Path, required=True)
    return parser


def main(argv: list[str] | None = None) -> int:
    args = _parser().parse_args(argv)
    try:
        source = load_source(args.config)
        if args.command == "plan":
            result = build_plan(source)
        elif args.command == "download":
            manifest = download_assets(
                source, args.root, workers=args.workers, proxy=args.proxy
            )
            result = {"status": "downloaded", "manifest": str(manifest)}
        else:
            result = verify_assets(source, args.root)
    except AncillaryError as exc:
        print(
            json.dumps({"status": "failed", "error": str(exc)}, ensure_ascii=False),
            file=sys.stderr,
        )
        return 2
    print(json.dumps(result, ensure_ascii=False, indent=2, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
