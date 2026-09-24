# ruff: noqa: E501, I001
"""Tile-wise same-height quality selection followed by column maximum.

No X-over-S paint-over, no averaging dBZ, no full-network 3D resident array.
No temporal advection in v1: held S observations retain their measured age.
"""
from __future__ import annotations

from dataclasses import dataclass

import numpy as np
from pyproj import Geod, Transformer

from .model import Network, Station, Sweep, Volume, epoch

EARTH_EFFECTIVE_M = 6371000.0 * 4.0 / 3.0
GEOD = Geod(ellps="WGS84")


def _nearest_ray(azimuth: np.ndarray, bearing: np.ndarray) -> tuple[np.ndarray, np.ndarray]:
    order = np.argsort(azimuth)
    a = azimuth[order]
    pos = np.searchsorted(a, bearing)
    lo, hi = (pos-1) % len(a), pos % len(a)
    dlo = np.abs((bearing-a[lo]+180) % 360-180)
    dhi = np.abs((bearing-a[hi]+180) % 360-180)
    index = np.where(dlo <= dhi, lo, hi)
    return order[index], np.minimum(dlo, dhi)


def beam_height(slant_m: np.ndarray, elevation_deg: np.ndarray | float, altitude_m: float) -> np.ndarray:
    angle = np.deg2rad(elevation_deg)
    return np.sqrt(slant_m**2+EARTH_EFFECTIVE_M**2+2*slant_m*EARTH_EFFECTIVE_M*np.sin(angle))-EARTH_EFFECTIVE_M+altitude_m


@dataclass
class Footprint:
    ray: np.ndarray
    gate: np.ndarray
    horizontal: np.ndarray
    height: np.ndarray
    lower: np.ndarray
    upper: np.ndarray
    resolution: np.ndarray
    age: np.ndarray


def footprint(s: Sweep, station: Station, longitude: np.ndarray, latitude: np.ndarray, analysis_epoch: float) -> Footprint:
    # pyproj's scalar fast path does not accept a length-one ndarray safely.
    if longitude.size == 1:
        b, _, d = GEOD.inv(station.longitude_deg, station.latitude_deg,
                          float(longitude.item()), float(latitude.item()))
        bearing, distance = np.full(longitude.shape, b), np.full(longitude.shape, d)
    else:
        bearing, _, distance = GEOD.inv(np.full(longitude.shape, station.longitude_deg),
                                      np.full(latitude.shape, station.latitude_deg), longitude, latitude)
    ray, offset = _nearest_ray(s.azimuth_deg, np.mod(bearing, 360))
    elevation = s.elevation_deg[ray]
    arc = distance/EARTH_EFFECTIVE_M
    denominator = np.cos(np.deg2rad(elevation)+arc)
    slant = EARTH_EFFECTIVE_M*np.sin(arc)/np.maximum(denominator, 1e-9)
    ranges = s.range_m
    if len(ranges) < 2:
        # A solitary bin has no inferable radial width.
        width = np.full_like(slant, np.nan)
        gate = np.zeros(slant.shape, np.int32)
    else:
        pos = np.clip(np.searchsorted(ranges, slant), 1, len(ranges)-1)
        gate = np.where(abs(slant-ranges[pos-1]) <= abs(slant-ranges[pos]), pos-1, pos)
        steps = np.diff(ranges)
        widths = np.empty_like(ranges, dtype=float)
        widths[0], widths[-1] = steps[0], steps[-1]
        widths[1:-1] = np.minimum(steps[:-1], steps[1:])
        width = widths[gate]
    center_range = ranges[gate]
    age = analysis_epoch-s.ray_time_epoch[ray]
    support = ((denominator > 0) & (distance >= 0) & np.isfinite(slant)
               & (offset <= station.beam_width_h_deg/2)
               & (np.abs(center_range-slant) <= width/2)
               & (age >= 0) & (age <= station.maximum_age_seconds))
    h = beam_height(center_range, elevation, station.altitude_m_msl)
    lower = beam_height(np.maximum(0, center_range-width/2), elevation-station.beam_width_v_deg/2, station.altitude_m_msl)
    upper = beam_height(center_range+width/2, elevation+station.beam_width_v_deg/2, station.altitude_m_msl)
    resolution = np.maximum(width, center_range*np.deg2rad(station.beam_width_h_deg))
    return Footprint(ray, gate, support, h, np.minimum(lower, upper), np.maximum(lower, upper), resolution, age)


@dataclass
class Composite:
    arrays: dict[str, np.ndarray]
    metadata: dict


def build_composite(volumes: list[Volume], network: Network, product: str, analysis_time: str, cutoff: str) -> Composite:
    if product not in network.products:
        raise ValueError("unregistered product")
    grid = network.products[product]
    target, deadline = epoch(analysis_time), epoch(cutoff)
    if target > deadline or target % grid.cadence_seconds != 0:
        raise ValueError("target must be a minute boundary no later than input cutoff")
    if not 1 <= len(volumes) <= 16:
        raise ValueError("one to sixteen unique station observations are required")
    volumes = sorted(volumes, key=lambda v: (v.metadata["radar_id"], v.metadata["scan_id"]))
    seen = set()
    sweeps = []
    sources = []
    skipped = []
    for v in volumes:
        sid = v.metadata["radar_id"]
        station = network.stations[sid]
        v.validate(station)
        if not station.enabled or not station.geometry_verified:
            raise ValueError("disabled or unverified station")
        if sid in seen:
            raise ValueError("choose one causal scan per station before composing")
        seen.add(sid)
        if v.metadata.get("network_sha256") != network.sha256:
            raise ValueError("volume was not translated through this network release")
        if epoch(v.metadata["volume_end"]) > target or epoch(v.metadata["available_at"]) > deadline:
            skipped.append({"radar_id": sid, "reason": "future_observation_or_arrival"})
            continue
        if target-epoch(v.metadata["volume_end"]) > station.maximum_age_seconds:
            skipped.append({"radar_id": sid, "reason": "expired"})
            continue
        for s in sorted(v.sweeps, key=lambda s: s.number):
            for key in ("DBZH_QC", "REFLECTIVITY_ELIGIBLE_FOR_CR", "QUALITY_SCORE"):
                if key not in s.fields:
                    raise ValueError("fusion requires the explicit QC output contract: " + key)
            quality = s.fields["QUALITY_SCORE"]
            if np.any(~np.isfinite(quality)) or np.any((quality < 0) | (quality > 1)):
                raise ValueError("invalid fusion quality score")
            index = len(sources)
            sources.append({"index": index, "radar_id": sid, "band": station.band,
                            "scan_id": v.metadata["scan_id"], "sweep_number": s.number,
                            "asset_sha256": v.metadata["asset_sha256"], "input_uri": v.metadata.get("input_uri"),
                            "frequency_hz": station.frequency_hz, "site_altitude_m_msl": station.altitude_m_msl,
                            "volume_end": v.metadata["volume_end"],
                            "available_at": v.metadata["available_at"], "scan_type": v.metadata["scan_type"],
                            "qc_version": v.metadata.get("qc_pipeline_version", v.metadata.get("processing")),
                            "calibration_id": station.calibration_id, "network_sha256": network.sha256})
            sweeps.append((index, s, station))
    shape = (grid.height, grid.width)
    names = ("CR_DBZH", "WINNER_HEIGHT_MSL_M", "WINNER_AGE_SECONDS", "WINNER_RESOLUTION_M", "WINNER_QUALITY_SCORE", "CR_UNCERTAIN_DBZH", "OBSERVED_MIN_HEIGHT_MSL_M", "OBSERVED_MAX_HEIGHT_MSL_M")
    out = {name: np.full(shape, np.nan, np.float32) for name in names}
    out.update({name: np.full(shape, -1, np.int32) for name in ("WINNER_SOURCE", "WINNER_RAY", "WINNER_GATE", "COVERAGE_SOURCE")})
    out["VALID_LAYER_COUNT"] = np.zeros(shape, np.uint16)
    out["OBSERVED_MASK"] = np.zeros(shape, np.uint8)
    out["NO_ECHO_MASK"] = np.zeros(shape, np.uint8)
    transform = Transformer.from_crs(grid.crs, "EPSG:4326", always_xy=True)
    # Both iteration orders are stable: equal-score selection has deterministic
    # provenance and does not flicker with dictionary or NATS arrival order.
    for row in range(0, grid.height, grid.tile_rows):
        stop = min(row+grid.tile_rows, grid.height)
        sl = np.s_[row:stop, :]
        x = grid.west_m + (np.arange(grid.width)+.5)*grid.spacing_m
        y = grid.south_m + (np.arange(row, stop)+.5)*grid.spacing_m
        xx, yy = np.meshgrid(x, y)
        if xx.size == 1:
            a, b = transform.transform(float(xx.item()), float(yy.item()))
            lon, lat = np.full(xx.shape, a), np.full(yy.shape, b)
        else:
            lon, lat = transform.transform(xx, yy)
        if not np.all(np.isfinite(lon)) or not np.all(np.isfinite(lat)):
            raise ValueError("grid cannot be projected to radar coordinates")
        tile_shape = xx.shape
        # Only one tile of height levels is resident. Geometry is evaluated
        # once per sweep/tile, not once per height level (a major CPU saving).
        layer_shape = (len(grid.levels_m_msl), *tile_shape)
        score = np.full(layer_shape, -np.inf)
        values = np.full(layer_shape, np.nan, np.float32)
        winner = np.full(layer_shape, -1, np.int32)
        wray = np.full(layer_shape, -1, np.int32)
        wgate = np.full(layer_shape, -1, np.int32)
        h = np.full(layer_shape, np.nan, np.float32)
        age = np.full(layer_shape, np.inf, np.float32)
        resolution = np.full(layer_shape, np.inf, np.float32)
        for index, sweep, station in sweeps:
            fp = footprint(sweep, station, lon, lat, target)
            r, g = fp.ray, fp.gate
            f = sweep.fields
            valid = fp.horizontal & (f["OBSERVED_MASK"][r, g] == 1)
            noecho = f["NO_ECHO_MASK"][r, g] == 1
            sample = f["DBZH_QC"][r, g]
            echo = valid & ~noecho & np.isfinite(sample)
            eligible = valid & (f["REFLECTIVITY_ELIGIBLE_FOR_CR"][r, g] == 1) & (noecho | echo)
            quality = (f["QUALITY_SCORE"][r, g] * np.exp(-fp.age/station.maximum_age_seconds)
                       * np.minimum(1., grid.spacing_m/np.maximum(fp.resolution, 1)))
            half = np.maximum((fp.upper-fp.lower)/2, 1)
            for li, level in enumerate(grid.levels_m_msl):
                represented = (level >= fp.lower) & (level <= fp.upper)
                uncertainty = f.get("CR_UNCERTAIN_MASK")
                uncertain = represented & echo & ~eligible
                if uncertainty is not None:
                    uncertain &= uncertainty[r, g] == 1
                out["CR_UNCERTAIN_DBZH"][sl] = np.fmax(out["CR_UNCERTAIN_DBZH"][sl], np.where(uncertain, sample, np.nan))
                candidate = quality/(1+np.abs(fp.height-level)/half)
                admitted = represented & eligible & (candidate > 0)
                tied = np.isclose(candidate, score[li], rtol=0, atol=1e-12)
                better = admitted & ((candidate > score[li]+1e-12) | (tied & ((fp.age < age[li]) | ((fp.age == age[li]) & (fp.resolution < resolution[li])))))
                score[li][better] = candidate[better]
                values[li][better] = np.where(noecho[better], np.nan, sample[better])
                winner[li][better], wray[li][better], wgate[li][better] = index, r[better], g[better]
                h[li][better], age[li][better], resolution[li][better] = fp.height[better], fp.age[better], fp.resolution[better]
        for li, level in enumerate(grid.levels_m_msl):
            covered = winner[li] >= 0
            out["VALID_LAYER_COUNT"][sl] += covered.astype(np.uint16)
            out["OBSERVED_MASK"][sl] |= covered.astype(np.uint8)
            minimum, maximum = out["OBSERVED_MIN_HEIGHT_MSL_M"][sl], out["OBSERVED_MAX_HEIGHT_MSL_M"][sl]
            minimum[:] = np.fmin(minimum, np.where(covered, level, np.nan))
            maximum[:] = np.fmax(maximum, np.where(covered, level, np.nan))
            no_prior_source = (out["COVERAGE_SOURCE"][sl] < 0) & covered
            out["COVERAGE_SOURCE"][sl][no_prior_source] = winner[li][no_prior_source]
            replace = covered & np.isfinite(values[li]) & (~np.isfinite(out["CR_DBZH"][sl]) | (values[li] > out["CR_DBZH"][sl]))
            for key, data in (("CR_DBZH", values[li]), ("WINNER_SOURCE", winner[li]), ("WINNER_RAY", wray[li]), ("WINNER_GATE", wgate[li]),
                              ("WINNER_HEIGHT_MSL_M", h[li]), ("WINNER_AGE_SECONDS", age[li]), ("WINNER_RESOLUTION_M", resolution[li]), ("WINNER_QUALITY_SCORE", score[li])):
                out[key][sl][replace] = data[replace]
    # A qualified clear layer cannot turn an uncertain echo at another height
    # into valid no-echo for the column. Keep uncertainty explicitly queryable.
    out["UNCERTAIN_MASK"] = np.isfinite(out["CR_UNCERTAIN_DBZH"]).astype(np.uint8)
    out["NO_ECHO_MASK"] = ((out["OBSERVED_MASK"] == 1) & ~np.isfinite(out["CR_DBZH"])
                           & (out["UNCERTAIN_MASK"] == 0)).astype(np.uint8)
    meta = {"contract": "rainpulse.multiband.composite-v1", "product_id": product, "grid_id": grid.grid_id,
            "crs": grid.crs, "west_m": grid.west_m, "south_m": grid.south_m, "spacing_m": grid.spacing_m,
            "width": grid.width, "height": grid.height, "row_order": "south_to_north", "levels_m_msl": grid.levels_m_msl,
            "analysis_time": analysis_time, "input_cutoff": cutoff, "cadence_seconds": grid.cadence_seconds,
            "network_release": network.release_id, "network_sha256": network.sha256,
            "sources": sources, "skipped": skipped, "method": "quality_select_at_height_then_vertical_max_v1",
            "temporal_method": "causal_hold_with_measured_age_no_advection", "vertical_coverage": "observed_levels_only",
            "observed_mask_semantics": "at_least_one_qualified_sampled_height_not_full_column_coverage",
            "operational_eligible": False, "qpe_eligible": False,
            "valid_echo_cells": int(np.count_nonzero(np.isfinite(out["CR_DBZH"]))),
            "valid_no_echo_cells": int(out["NO_ECHO_MASK"].sum()),
            "uncertain_only_cells": int(np.count_nonzero(~np.isfinite(out["CR_DBZH"]) & (out["UNCERTAIN_MASK"] == 1))),
            "missing_cells": int(np.count_nonzero((out["OBSERVED_MASK"] == 0) & (out["UNCERTAIN_MASK"] == 0)))}
    return Composite(out, meta)
