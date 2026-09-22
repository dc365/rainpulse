"""Full-footprint, QC-masked vertical maximum for diagnostic display."""
from __future__ import annotations

import numpy as np
from pyproj import Geod

from .polar_sampling import polar_targets


def composite_reflectivity(roots, reject_mask: int, *, maximum_size: int = 1200, sites=None, return_sources=False):
    if not roots:
        raise ValueError("full-range composite requires participating radar volumes")
    geod = Geod(ellps="WGS84")
    footprints = []
    for root in roots:
        site = (sites or {}).get(root.attrs.get("radar_id"))
        lon = float(site["longitude_deg"] if site else root.attrs["site_longitude_deg"])
        lat = float(site["latitude_deg"] if site else root.attrs["site_latitude_deg"])
        if not np.isfinite([lon, lat]).all() or not -90 < lat < 90:
            raise ValueError("invalid radar site coordinates")
        radius = max(float(root[f"sweep_{int(n):03d}"]["range"][-1]) for n in root["sweep_number"][:])
        angles = np.arange(360, dtype=float)
        x, y, _ = geod.fwd(np.full(360, lon), np.full(360, lat), angles, np.full(360, radius))
        footprints.append((lon, lat, min(x), min(y), max(x), max(y)))
    west = min(v[2] for v in footprints)
    south = min(v[3] for v in footprints)
    east = max(v[4] for v in footprints)
    north = max(v[5] for v in footprints)
    if east - west > 180:
        raise ValueError("dateline-spanning diagnostic composites are unsupported")
    # Approximately 1 km, bounded by the diagnostic image pixel budget.
    step = max(0.01, (east - west) / (maximum_size - 2), (north - south) / (maximum_size - 2))
    west, south = np.floor(west / step) * step, np.floor(south / step) * step
    width = int(np.ceil((east - west) / step))
    height = int(np.ceil((north - south) / step))
    east, north = west + width * step, south + height * step
    result = np.full((height, width), np.nan, dtype="float32")
    sources = {key: np.full((height, width), -1, dtype="int32")
               for key in ("radar", "sweep", "ray", "gate")} if return_sources else None
    for radar_index, (root, (lon, lat, *_)) in enumerate(zip(roots, footprints)):
        sweeps = []
        for number in root["sweep_number"][:]:
            group = root[f"sweep_{int(number):03d}"]
            if "DBZH_QC" not in group:
                continue
            fields = ["DBZH_QC", "VALID_MASK", "QC_FLAGS", "elevation", "azimuth", "range"]
            if root.attrs.get("flag_definition_version") == "qc-flags-v2":
                # CR has its own admission result. QPE eligibility is intentionally
                # not used here: quantitative rain estimates may retain weak echoes
                # that the CR fusion has already withheld.
                fields.append("REFLECTIVITY_ELIGIBLE_FOR_CR")
            sweeps.append({"number": int(number), **{name: group[name][:] for name in fields}})
        # Reuse site geometry across sweeps; chunk rows to bound temporary memory.
        for start in range(0, height, 128):
            end = min(start + 128, height)
            xx, yy = np.meshgrid(west + (np.arange(width) + .5) * step,
                                 north - (np.arange(start, end) + .5) * step)
            angle, _, distance = geod.inv(np.full(xx.shape, lon), np.full(yy.shape, lat), xx, yy)
            for sweep in sweeps:
                elevation = float(np.nanmedian(sweep["elevation"][:]))
                # Effective-Earth-radius inversion: ground arc -> slant range.
                earth = 6371000.0 * 4.0 / 3.0
                arc = distance / earth
                denominator = np.cos(np.radians(elevation) + arc)
                slant = earth * np.sin(arc) / np.maximum(denominator, 1e-6)
                pixels = polar_targets(sweep["azimuth"][:], sweep["range"][:], slant, angle)
                ray, gate = np.maximum(pixels.ray, 0), np.maximum(pixels.gate, 0)
                values = np.asarray(sweep["DBZH_QC"][:])[ray, gate]
                valid = pixels.available & (denominator > 0) & np.isfinite(values)
                valid &= np.asarray(sweep["VALID_MASK"][:])[ray, gate] == 1
                valid &= (np.asarray(sweep["QC_FLAGS"][:])[ray, gate] & np.uint32(reject_mask)) == 0
                if root.attrs.get("flag_definition_version") == "qc-flags-v2":
                    valid &= (
                        np.asarray(sweep["REFLECTIVITY_ELIGIBLE_FOR_CR"][:])[ray, gate] == 1
                    )
                if sources is not None:
                    # Ties retain the first observed contributor, consistently.
                    wins = valid & (~np.isfinite(result[start:end]) | (values > result[start:end]))
                    sources["radar"][start:end][wins] = radar_index
                    sources["sweep"][start:end][wins] = sweep["number"]
                    sources["ray"][start:end][wins] = ray[wins]
                    sources["gate"][start:end][wins] = gate[wins]
                result[start:end] = np.fmax(result[start:end], np.where(valid, values, np.nan))
    output = result, [west, south, east, north]
    return (*output, sources) if return_sources else output
