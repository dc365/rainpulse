# ruff: noqa: E501, I001
"""Native polar observations and explicitly configured network/product capabilities."""
from __future__ import annotations

from dataclasses import dataclass, field
from datetime import UTC, datetime
import hashlib
import json
import math
from pathlib import Path
import re
from typing import Any

import numpy as np
from pyproj import CRS

NAME = re.compile(r"^[A-Za-z0-9][A-Za-z0-9_.-]{0,95}$")
SHA = re.compile(r"^[0-9a-f]{64}$")
MAX_GATES = 8_000_000
MAX_SWEEPS = 32


def utc(value: str) -> datetime:
    result = datetime.fromisoformat(value.replace("Z", "+00:00"))
    if result.tzinfo is None:
        raise ValueError("an explicit UTC offset is required")
    return result.astimezone(UTC)


def epoch(value: str) -> float:
    return utc(value).timestamp()


def json_bytes(value: Any) -> bytes:
    return json.dumps(value, sort_keys=True, separators=(",", ":"), allow_nan=False).encode()


def digest(value: Any) -> str:
    return hashlib.sha256(json_bytes(value)).hexdigest()


def finite(value: float, name: str) -> float:
    if isinstance(value, bool) or not math.isfinite(value):
        raise ValueError(f"invalid {name}")
    return value


@dataclass(frozen=True)
class XProfile:
    # All thresholds are candidate settings, not validated operational constants.
    attenuation: str = "none"
    alpha_db_per_degree: float | None = None
    max_pia_db: float = 12.0
    phase_window_m: float = 1000.0
    max_negative_phase_step_deg: float = 5.0
    phase_anchor_max_range_m: float = 1500.0
    snr_min_db: float = 3.0
    require_snr: bool = True
    rho_candidate_max: float = 0.75
    texture_candidate_db: float = 8.0
    max_blockage_fraction: float = 0.7

    def __post_init__(self) -> None:
        if self.attenuation not in {"none", "phidp_linear", "upstream_verified"}:
            raise ValueError("unsupported X attenuation method")
        for name in ("max_pia_db", "phase_window_m", "max_negative_phase_step_deg", "phase_anchor_max_range_m", "snr_min_db", "rho_candidate_max", "texture_candidate_db", "max_blockage_fraction"):
            finite(getattr(self, name), name)
        if not (0 < self.max_pia_db <= 30 and 100 <= self.phase_window_m <= 10000 and 0 < self.max_negative_phase_step_deg <= 30 and 0 <= self.phase_anchor_max_range_m <= 5000):
            raise ValueError("unbounded phase correction configuration")
        if not (-20 <= self.snr_min_db <= 40 and 0 < self.rho_candidate_max < 1 and 0 < self.texture_candidate_db <= 30 and 0 <= self.max_blockage_fraction <= 1):
            raise ValueError("invalid X quality thresholds")
        if type(self.require_snr) is not bool:
            raise ValueError("require_snr must be boolean")
        if self.alpha_db_per_degree is not None and not 0 < finite(self.alpha_db_per_degree, "alpha") <= 1:
            raise ValueError("alpha must be explicitly positive and bounded")
        if self.attenuation == "phidp_linear" and self.alpha_db_per_degree is None:
            raise ValueError("phase correction requires a site-validated alpha; no S-band default")


@dataclass(frozen=True)
class Station:
    radar_id: str
    band: str
    frequency_hz: float
    longitude_deg: float
    latitude_deg: float
    altitude_m_msl: float
    beam_width_h_deg: float
    beam_width_v_deg: float
    source: str
    enabled: bool = False
    geometry_verified: bool = False
    calibration_verified: bool = False
    calibration_id: str = "unverified"
    nominal_cadence_seconds: int = 60
    maximum_age_seconds: int = 120
    quality_scale: float = 1.0
    allowed_s_qc_versions: tuple[str, ...] = ()
    x_qc: XProfile = field(default_factory=XProfile)

    def __post_init__(self) -> None:
        if not NAME.fullmatch(self.radar_id) or self.radar_id != self.radar_id.lower():
            raise ValueError("radar_id must be a lowercase stable identifier")
        limits = {"S": (2e9, 4e9), "X": (8e9, 12e9)}
        if self.band not in limits or not limits[self.band][0] <= finite(self.frequency_hz, "frequency") <= limits[self.band][1]:
            raise ValueError("band and actual operating frequency are inconsistent")
        for name in ("longitude_deg", "latitude_deg", "altitude_m_msl", "beam_width_h_deg", "beam_width_v_deg", "quality_scale"):
            finite(getattr(self, name), name)
        if not (-180 <= self.longitude_deg <= 180 and -85 < self.latitude_deg < 85 and -500 <= self.altitude_m_msl <= 9000):
            raise ValueError("invalid verified site coordinates")
        if not (0 < self.beam_width_h_deg <= 5 and 0 < self.beam_width_v_deg <= 5 and 0 < self.quality_scale <= 1):
            raise ValueError("invalid beam widths or quality scale")
        if type(self.nominal_cadence_seconds) is not int or not 1 <= self.nominal_cadence_seconds <= 3600 or type(self.maximum_age_seconds) is not int or not 1 <= self.maximum_age_seconds <= 3600:
            raise ValueError("invalid station cadence/age")
        if self.source not in {"s_qc_zarr", "normalized_zarr", "native_bundle"}:
            raise ValueError("format adapter must be explicitly selected, not inferred from band")
        if self.source == "s_qc_zarr" and self.band != "S":
            raise ValueError("legacy QC adapter cannot masquerade as X QC")
        if self.band == "S" and self.source == "normalized_zarr":
            raise ValueError("S observations must pass the existing S QC first")
        for name in ("enabled", "geometry_verified", "calibration_verified"):
            if type(getattr(self, name)) is not bool:
                raise ValueError(f"{name} must be boolean")
        if self.enabled and not self.geometry_verified:
            raise ValueError("an enabled station requires verified MSL geometry")
        if not isinstance(self.calibration_id, str) or not self.calibration_id or len(self.calibration_id) > 128:
            raise ValueError("calibration identity required")


@dataclass(frozen=True)
class Grid:
    grid_id: str
    crs: str
    west_m: float
    south_m: float
    spacing_m: float
    width: int
    height: int
    levels_m_msl: tuple[float, ...]
    tile_rows: int = 32
    cadence_seconds: int = 60

    def __post_init__(self) -> None:
        if not NAME.fullmatch(self.grid_id):
            raise ValueError("invalid grid identity")
        crs = CRS.from_user_input(self.crs)
        if not crs.is_projected or any(abs(a.unit_conversion_factor - 1) > 1e-9 for a in crs.axis_info):
            raise ValueError("fusion grid requires a projected CRS in metres")
        for n in ("west_m", "south_m", "spacing_m"):
            finite(getattr(self, n), n)
        if not 100 <= self.spacing_m <= 5000:
            raise ValueError("experimental grid spacing must be 100..5000 m")
        if any(type(n) is not int for n in (self.width, self.height, self.tile_rows, self.cadence_seconds)):
            raise ValueError("grid dimensions and cadence must be integer")
        if not (1 <= self.width <= 2048 and 1 <= self.height <= 2048 and self.width*self.height <= 1_000_000 and 1 <= self.tile_rows <= 128):
            raise ValueError("grid exceeds bounded first-version resource budget")
        if min(self.tile_rows, self.height)*self.width*len(self.levels_m_msl) > 2_000_000:
            raise ValueError("height tile exceeds transient memory budget")
        if self.cadence_seconds != 60:
            raise ValueError("this product version has a fixed one-minute output contract")
        h = np.asarray(self.levels_m_msl, dtype=float)
        if not 1 <= len(h) <= 32 or not np.all(np.isfinite(h)) or np.any(np.diff(h) <= 0) or h[0] < -500 or h[-1] > 20000:
            raise ValueError("invalid common MSL height levels")


@dataclass(frozen=True)
class Network:
    release_id: str
    stations: dict[str, Station]
    products: dict[str, Grid]
    sha256: str
    cache_max_bytes: int = 0
    cache_ttl_seconds: int = 600
    maximum_input_bytes: int = 512*1024**2

    @classmethod
    def from_bytes(cls, raw: bytes) -> "Network":
        if len(raw) > 1024**2:
            raise ValueError("network file exceeds 1 MiB")
        data = json.loads(raw)
        allowed = {"schema_version", "release_id", "stations", "products", "cache_max_bytes", "cache_ttl_seconds", "maximum_input_bytes"}
        if set(data) - allowed or data.get("schema_version") != "1.0" or not NAME.fullmatch(data.get("release_id", "")):
            raise ValueError("invalid network contract")
        stations = {}
        for name, values in data["stations"].items():
            values = dict(values)
            values["x_qc"] = XProfile(**values.get("x_qc", {}))
            values["allowed_s_qc_versions"] = tuple(values.get("allowed_s_qc_versions", ()))
            station = Station(radar_id=name, **values)
            stations[name] = station
        products = {}
        for name, values in data["products"].items():
            values = dict(values)
            values["levels_m_msl"] = tuple(values["levels_m_msl"])
            if not NAME.fullmatch(name):
                raise ValueError("invalid product name")
            products[name] = Grid(**values)
        if not 1 <= len(stations) <= 16 or not 1 <= len(products) <= 4:
            raise ValueError("network inventory exceeds bounds")
        kwargs = {n: data[n] for n in ("cache_max_bytes", "cache_ttl_seconds", "maximum_input_bytes") if n in data}
        for n, v in kwargs.items():
            if type(v) is not int:
                raise ValueError(f"{n} must be integer")
        result = cls(data["release_id"], stations, products, hashlib.sha256(raw).hexdigest(), **kwargs)
        if not (0 <= result.cache_max_bytes <= 512*1024**2 and 1 <= result.cache_ttl_seconds <= 3600 and 1024 <= result.maximum_input_bytes <= 2*1024**3):
            raise ValueError("invalid resource budgets")
        return result

    @classmethod
    def load(cls, path: str | Path) -> "Network":
        with Path(path).open("rb") as stream:
            return cls.from_bytes(stream.read(1024**2 + 1))


@dataclass
class Sweep:
    number: int
    azimuth_deg: np.ndarray
    range_m: np.ndarray
    elevation_deg: np.ndarray
    ray_time_epoch: np.ndarray
    fields: dict[str, np.ndarray]

    def validate(self) -> None:
        az, r, el, t = (np.asarray(x) for x in (self.azimuth_deg, self.range_m, self.elevation_deg, self.ray_time_epoch))
        if type(self.number) is not int or self.number < 0 or self.number > 4096:
            raise ValueError("invalid sweep identity")
        if az.ndim != 1 or r.ndim != 1 or not (1 <= len(az) <= 4096 and 1 <= len(r) <= 16384) or el.shape != az.shape or t.shape != az.shape:
            raise ValueError("invalid native ray/gate geometry")
        if not all(np.all(np.isfinite(x)) for x in (az, r, el, t)) or np.any((az < 0) | (az >= 360)) or np.any((el < -2) | (el >= 89)) or np.any(np.diff(r) <= 0) or r[0] < 0:
            raise ValueError("nonfinite/nonmonotone native geometry")
        if len(np.unique(az)) != len(az):
            raise ValueError("duplicate azimuth rays need explicit decoder resolution")
        shape = (len(az), len(r))
        for k, arr in self.fields.items():
            if not NAME.fullmatch(k) or np.asarray(arr).shape != shape or np.asarray(arr).dtype.kind not in "bufi":
                raise ValueError(f"invalid polar field {k}")
        for k in ("DBZH", "OBSERVED_MASK", "NO_ECHO_MASK"):
            if k not in self.fields:
                raise ValueError(f"native observation requires {k}")
        for k, arr in self.fields.items():
            if k.endswith("_MASK") and not np.all(np.isin(arr, (0, 1))):
                raise ValueError(f"{k} is not a binary mask")
        obs = self.fields["OBSERVED_MASK"] == 1
        noecho = self.fields["NO_ECHO_MASK"] == 1
        if np.any(noecho & ~obs) or np.any(obs & ~noecho & ~np.isfinite(self.fields["DBZH"])):
            raise ValueError("missing, observed no-echo and measured echo must be distinct")


@dataclass
class Volume:
    metadata: dict[str, Any]
    sweeps: list[Sweep]

    def validate(self, station: Station) -> None:
        m = self.metadata
        for key in ("radar_id", "scan_id", "band", "frequency_hz", "volume_start", "volume_end", "available_at", "asset_sha256", "scan_type", "longitude_deg", "latitude_deg", "altitude_m_msl", "height_datum"):
            if key not in m:
                raise ValueError(f"native metadata missing {key}")
        if m["radar_id"] != station.radar_id or m["band"] != station.band or not SHA.fullmatch(m["asset_sha256"]):
            raise ValueError("observation station/band/content identity differs")
        for key in ("frequency_hz", "longitude_deg", "latitude_deg", "altitude_m_msl"):
            finite(float(m[key]), key)
        if m["height_datum"] != "MSL" or abs(float(m["altitude_m_msl"])-station.altitude_m_msl) > 1:
            raise ValueError("observation altitude differs from the verified common MSL datum")
        if abs(float(m["frequency_hz"]) - station.frequency_hz) > station.frequency_hz * .01:
            raise ValueError("observation and configured frequency differ")
        if abs(float(m["longitude_deg"]) - station.longitude_deg) > .0001 or abs(float(m["latitude_deg"]) - station.latitude_deg) > .0001:
            raise ValueError("native coordinates differ from verified station")
        start, end, available = (epoch(m[k]) for k in ("volume_start", "volume_end", "available_at"))
        if end < start or end - start > 3600 or available < end:
            raise ValueError("invalid acquisition/availability time interval")
        if m["scan_type"] not in {"volume", "ppi", "sector"} or not 1 <= len(self.sweeps) <= MAX_SWEEPS:
            raise ValueError("unsupported or incomplete native scan description")
        numbers = set()
        total = 0
        for s in self.sweeps:
            s.validate()
            if s.number in numbers or np.any(s.ray_time_epoch < start) or np.any(s.ray_time_epoch > end):
                raise ValueError("duplicate sweep or ray outside acquisition interval")
            numbers.add(s.number)
            total += s.fields["DBZH"].size
        if total > MAX_GATES:
            raise ValueError("volume exceeds decoded gate budget")

    @property
    def nbytes(self) -> int:
        return sum(a.nbytes for s in self.sweeps for a in [s.azimuth_deg, s.range_m, s.elevation_deg, s.ray_time_epoch, *s.fields.values()])
