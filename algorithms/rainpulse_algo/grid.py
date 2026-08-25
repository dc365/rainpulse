from __future__ import annotations

import hashlib
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import numpy as np
import yaml
from pyproj import Geod


class GridConfigError(ValueError):
    """Raised when an immutable RainPulse grid definition is inconsistent."""


@dataclass(frozen=True)
class GridMetric:
    x_spacing_m_by_latitude: np.ndarray
    y_spacing_m_by_latitude: np.ndarray
    version: str


@dataclass(frozen=True)
class RegularLatLonGrid:
    grid_id: str
    config_version: str
    west: float
    east: float
    south: float
    north: float
    longitude_interval_deg: float
    latitude_interval_deg: float
    longitude_count: int
    latitude_count: int
    reference_latitude_deg: float
    ancillary_domain_id: str

    @property
    def longitude(self) -> np.ndarray:
        values = self.west + np.arange(self.longitude_count, dtype="float64") * (
            self.longitude_interval_deg
        )
        return values.astype("float32")

    @property
    def latitude(self) -> np.ndarray:
        values = self.south + np.arange(self.latitude_count, dtype="float64") * (
            self.latitude_interval_deg
        )
        return values.astype("float32")

    @property
    def shape(self) -> tuple[int, int]:
        return self.latitude_count, self.longitude_count

    @property
    def coordinate_centre_bounds(self) -> tuple[float, float, float, float]:
        return self.west, self.south, self.east, self.north

    @property
    def pixel_edge_bounds(self) -> tuple[float, float, float, float]:
        return (
            self.west - self.longitude_interval_deg / 2,
            self.south - self.latitude_interval_deg / 2,
            self.east + self.longitude_interval_deg / 2,
            self.north + self.latitude_interval_deg / 2,
        )

    @property
    def coordinate_sha256(self) -> str:
        digest = hashlib.sha256()
        digest.update(self.grid_id.encode())
        digest.update(b"\0lat\0")
        digest.update(self.latitude.astype("<f4", copy=False).tobytes())
        digest.update(b"\0lon\0")
        digest.update(self.longitude.astype("<f4", copy=False).tobytes())
        return digest.hexdigest()

    def metric(self) -> GridMetric:
        geod = Geod(ellps="WGS84")
        latitude = self.latitude.astype("float64")
        longitude = self.longitude.astype("float64")
        _, _, x_spacing = geod.inv(
            np.full(latitude.shape, longitude[0]),
            latitude,
            np.full(latitude.shape, longitude[1]),
            latitude,
        )
        half_dlat = self.latitude_interval_deg / 2
        _, _, y_spacing = geod.inv(
            np.full(latitude.shape, self.reference_latitude_deg),
            latitude - half_dlat,
            np.full(latitude.shape, self.reference_latitude_deg),
            latitude + half_dlat,
        )
        return GridMetric(
            x_spacing_m_by_latitude=np.asarray(x_spacing, dtype="float64"),
            y_spacing_m_by_latitude=np.asarray(y_spacing, dtype="float64"),
            version="wgs84-geod-grid-metric-v1",
        )


def _require(mapping: dict[str, Any], name: str) -> Any:
    if name not in mapping:
        raise GridConfigError(f"missing grid configuration field {name}")
    return mapping[name]


def load_grid_config(path: Path) -> RegularLatLonGrid:
    raw = yaml.safe_load(path.read_text())
    try:
        crs = _require(raw, "crs")
        bounds = _require(raw, "bounds")
        spacing = _require(raw, "spacing")
        shape = _require(raw, "shape")
        coordinates = _require(raw, "coordinates")
        if crs != {"authority": "EPSG", "code": 4326, "name": "WGS 84"}:
            raise GridConfigError("Phase 1 grid CRS must be EPSG:4326 WGS 84")
        if raw.get("lifecycle") != "ready":
            raise GridConfigError("only a ready grid may be loaded by compute workers")
        if raw.get("registration") != "point":
            raise GridConfigError("Phase 1 uses point-registered grid coordinates")
        if bounds.get("semantics") != "inclusive_coordinate_centres":
            raise GridConfigError("grid bounds must identify inclusive coordinate centres")
        if coordinates != {
            "dimension_order": ["lat", "lon"],
            "longitude_order": "west_to_east",
            "latitude_order": "south_to_north",
            "dtype": "float32",
        }:
            raise GridConfigError("grid coordinate convention differs from the Phase 1 contract")
        grid = RegularLatLonGrid(
            grid_id=str(_require(raw, "grid_id")),
            config_version=str(_require(raw, "config_version")),
            west=float(_require(bounds, "west")),
            east=float(_require(bounds, "east")),
            south=float(_require(bounds, "south")),
            north=float(_require(bounds, "north")),
            longitude_interval_deg=float(_require(spacing, "longitude_deg")),
            latitude_interval_deg=float(_require(spacing, "latitude_deg")),
            longitude_count=int(_require(shape, "longitude")),
            latitude_count=int(_require(shape, "latitude")),
            reference_latitude_deg=float(_require(raw, "reference_latitude_deg")),
            ancillary_domain_id=str(_require(raw, "ancillary_domain_id")),
        )
    except (TypeError, ValueError) as exc:
        if isinstance(exc, GridConfigError):
            raise
        raise GridConfigError(f"invalid grid configuration {path}: {exc}") from exc

    if grid.longitude_interval_deg <= 0 or grid.latitude_interval_deg <= 0:
        raise GridConfigError("grid intervals must be positive")
    expected_lon = round((grid.east - grid.west) / grid.longitude_interval_deg) + 1
    expected_lat = round((grid.north - grid.south) / grid.latitude_interval_deg) + 1
    if grid.longitude_count != expected_lon or grid.latitude_count != expected_lat:
        raise GridConfigError(
            f"configured grid shape {grid.shape} differs from bounds/interval shape "
            f"{(expected_lat, expected_lon)}"
        )
    if not np.isclose(grid.longitude[-1], grid.east, atol=1e-6):
        raise GridConfigError("longitude endpoint differs from the configured east bound")
    if not np.isclose(grid.latitude[-1], grid.north, atol=1e-6):
        raise GridConfigError("latitude endpoint differs from the configured north bound")
    expected_digest = str(_require(raw, "coordinate_sha256"))
    if grid.coordinate_sha256 != expected_digest:
        raise GridConfigError(
            f"coordinate SHA-256 {grid.coordinate_sha256} differs from configured "
            f"{expected_digest}"
        )
    return grid
