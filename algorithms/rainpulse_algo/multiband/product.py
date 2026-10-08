# ruff: noqa: E501, I001
"""A numerical candidate and small quicklooks; rendering never changes QC values."""
from __future__ import annotations

from rainpulse_algo.performance import (timed as _perf_timed)

import hashlib
import json
import struct
import zlib
from importlib.resources import files
import numpy as np

from .codec import encode_arrays
from .fusion import Composite
from .model import json_bytes

# Same discrete 5 dBZ palette as the S-band operational renderer and Web legend.
_PALETTE = json.loads(files("rainpulse_algo").joinpath("reflectivity_palette.json").read_text())
LEVELS = np.array([entry[0] for entry in _PALETTE], dtype=float)
COLORS = np.array([[int(color[i:i+2], 16) for i in (1, 3, 5)] for _, color in _PALETTE], dtype=np.uint8)
MAX_X_QC_PREVIEW_BYTES = 512 * 1024**2
MAX_PPI_INTERPOLATION_GAP_DEG = 3.0


@_perf_timed("preview.png_encoding")
def png(rgba: np.ndarray) -> bytes:
    if rgba.ndim != 3 or rgba.shape[2] != 4 or rgba.dtype != np.uint8:
        raise ValueError("RGBA uint8 required")
    height, width = rgba.shape[:2]
    def chunk(name, data):
        return struct.pack(">I",len(data))+name+data+struct.pack(">I",zlib.crc32(name+data)&0xffffffff)
    scan = b"".join(b"\x00"+row.tobytes() for row in rgba)
    return b"\x89PNG\r\n\x1a\n"+chunk(b"IHDR",struct.pack(">IIBBBBB",width,height,8,6,0,0,0))+chunk(b"IDAT",zlib.compress(scan, 3))+chunk(b"IEND",b"")


def quicklook(values: np.ndarray) -> bytes:
    supported = np.isfinite(values)
    index = np.clip(np.searchsorted(LEVELS, np.nan_to_num(values, nan=-100), side="right")-1, 0, len(LEVELS)-1)
    rgba = np.zeros((*values.shape, 4), np.uint8)
    rgba[:,:,:3] = COLORS[index]
    rgba[:,:,3] = np.where(supported, 255, 0)
    # Numeric storage is south-to-north; image row zero is the north edge.
    return png(rgba[::-1])


def polar_quicklook(azimuth_deg: np.ndarray, range_m: np.ndarray, values: np.ndarray,
                    *, uncertain: np.ndarray | None = None, actions: np.ndarray | None = None,
                    size: int = 720, map_sampling=None, elevation_deg=None) -> bytes:
    """Backward-compatible single-view API; multi-field callers share one plan."""
    from .preview_sampling import prepare_polar_sampling, render_polar_sampling
    if np.asarray(values).shape != (len(azimuth_deg), len(range_m)) or not len(azimuth_deg) or not len(range_m):
        raise ValueError("polar quicklook coordinates and field differ")
    plan = prepare_polar_sampling(azimuth_deg, range_m, size=size,
        map_sampling=map_sampling, elevation_deg=elevation_deg)
    return render_polar_sampling(plan, values, LEVELS, COLORS, png,
                                 uncertain=uncertain, actions=actions)


@_perf_timed("x.preview")
def x_qc_objects(volumes) -> dict[str, bytes]:
    """Small, geometry-independent raw/QC PPI comparisons from sweep volumes."""
    if hasattr(volumes, "sweeps"):
        volumes = (volumes,)
    objects: dict[str, bytes] = {}
    layers = []
    comparisons = []
    metadata = None
    for volume in volumes:
        metadata = metadata or volume.metadata
        for sweep in volume.sweeps:
            number = sweep.number
            sequence = len(comparisons) + 1
            fields = sweep.fields
            raw_key, qc_key, flags_key = (f"sweeps/{number}/{name}.png" for name in ("raw", "qc", "flags"))
            azimuth = sweep.azimuth_deg
            ranges = sweep.range_m
            raw = fields["DBZH_RAW"]
            action = fields["QC_ACTION"]
            from .xqc_v2.export import export_sweep
            xqc_detail = export_sweep(sweep, volume.metadata, objects)
            from .preview_sampling import prepare_polar_sampling, render_polar_sampling
            plan = prepare_polar_sampling(azimuth, ranges, ray_time_epoch=sweep.ray_time_epoch)
            objects[raw_key] = render_polar_sampling(plan, raw, LEVELS, COLORS, png)
            # Confirmed rejects are removed from the display; uncertain gates retain
            # their reflectivity and are highlighted in amber in a separate layer.
            qc = np.where(action == 2, np.nan, fields["DBZH_QC"])
            objects[qc_key] = render_polar_sampling(plan, qc, LEVELS, COLORS, png)
            objects[flags_key] = render_polar_sampling(plan, raw, LEVELS, COLORS, png, actions=action)
            if sum(map(len, objects.values())) > MAX_X_QC_PREVIEW_BYTES:
                raise ValueError("X QC PPI preview exceeds 512 MiB output budget")
            layers.extend([
                {"object_path": raw_key, "title": f"原始反射率 · 第{sequence}层（扫层编号 {number}）", "field": "DBZH_RAW", "sweep_number": number},
                {"object_path": qc_key, "title": f"基础质控后 · 第{sequence}层（扫层编号 {number}）", "field": "DBZH_QC_DISPLAY", "sweep_number": number},
                {"object_path": flags_key, "title": f"疑似/确认标记 · 第{sequence}层（扫层编号 {number}）", "field": "QC_ACTION", "sweep_number": number},
            ])
            map_layer = geographic_sweep_preview(sweep, volume.metadata, raw, qc, action, objects)
            comparisons.append({"sweep_number": number, "sequence": sequence, "raw": raw_key, "qc": qc_key, "flags": flags_key,
                                "elevation_deg": float(np.nanmedian(sweep.elevation_deg)), **({"xqc_v2": xqc_detail} if xqc_detail else {}), **({"map": map_layer} if map_layer else {})})
        del volume
    if metadata is None or not comparisons:
        raise ValueError("X QC preview requires at least one reflectivity sweep")
    if sum(map(len, objects.values())) > MAX_X_QC_PREVIEW_BYTES:
        raise ValueError("X QC preview and numeric indices exceed output budget")
    objects["manifest.json"] = json_bytes({
        "contract": "rainpulse.multiband.x-qc-preview-v1",
        "radar_id": metadata["radar_id"], "scan_id": metadata["scan_id"],
        "volume_start": metadata["volume_start"], "volume_end": metadata["volume_end"],
        "geometry": "station-centred polar; optional candidate EPSG:4326 map sampled from native site/ray geometry",
        "candidate_only": True, "operational_eligible": False, "qpe_enabled": False,
        "comparison": {"sweeps": comparisons},
        "layers": layers,
        "legend": [{"minimum_dbzh": float(n), "rgb": list(map(int, c))} for n, c in zip(LEVELS, COLORS, strict=True)],
        "flag_legend": [{"action": 2, "label": "确认无效/污染", "color": "#bf3930"},
                        {"action": 3, "label": "未决，待复核", "color": "#eea028"}],
        "palette_version": "operational-reflectivity-5dbz-v3",
        "note": "S/X 共用反射率色谱；确认无效门从显示场剔除，待复核门保留反射率，另在标记图显示；缺测透明。",
    })
    return objects


@_perf_timed("x.map_preview")
def geographic_sweep_preview(sweep, metadata, raw, qc, action, objects, size=720):
    """Sample native polar gates onto a north-up EPSG:4326 display raster.

    Site coordinates come from the normalized volume frozen in this task.
    This candidate display does not change numerical QC or fusion eligibility.
    """
    from pyproj import Geod

    lon, lat = metadata.get("longitude_deg"), metadata.get("latitude_deg")
    if lon is None or lat is None:
        return None
    if not np.isfinite([lon, lat]).all() or not -180 < lon < 180 or not -89 < lat < 89:
        return None
    ranges = np.asarray(sweep.range_m, dtype=float)
    elevations = np.asarray(sweep.elevation_deg, dtype=float)
    if not np.isfinite(ranges).all() or ranges[-1] <= 0 or not np.isfinite(elevations).all():
        raise ValueError("invalid native map range/elevation")
    re = 6371008.8 * 4 / 3
    elevation = np.deg2rad(elevations)
    ground = re * np.arctan2(ranges[-1] * np.cos(elevation), re + ranges[-1] * np.sin(elevation))
    radius = float(np.max(ground))
    if radius <= 0:
        return None
    geod = Geod(ellps="WGS84")
    bearings = np.arange(360, dtype=float)
    ring_lon, ring_lat, _ = geod.fwd(np.full(360, lon), np.full(360, lat), bearings, np.full(360, radius))
    west, south, east, north = float(min(ring_lon)), float(min(ring_lat)), float(max(ring_lon)), float(max(ring_lat))
    if east - west > 180:  # Antimeridian splitting needs a separate asset contract.
        return None
    xs = west + (np.arange(size) + .5) * (east - west) / size
    ys = south + (np.arange(size) + .5) * (north - south) / size
    xx, yy = np.meshgrid(xs, ys)
    azimuth, _, distance = geod.inv(np.full_like(xx, lon), np.full_like(yy, lat), xx, yy)
    sampling = (distance, azimuth % 360)
    paths = {name: f"sweeps/{sweep.number}/map_{name}.png" for name in ("raw", "qc", "flags")}
    from .preview_sampling import prepare_polar_sampling, render_polar_sampling
    plan = prepare_polar_sampling(sweep.azimuth_deg, ranges, size=size,
                                 map_sampling=sampling, elevation_deg=elevations,
                                 ray_time_epoch=sweep.ray_time_epoch)
    for name, values, actions in (("raw", raw, None), ("qc", qc, None), ("flags", raw, action)):
        objects[paths[name]] = render_polar_sampling(plan, values, LEVELS, COLORS, png, actions=actions)
    if sum(map(len, objects.values())) > MAX_X_QC_PREVIEW_BYTES:
        raise ValueError("X QC map preview exceeds 512 MiB output budget")
    from rainpulse_algo.diagnostics.radar_probe import attach_probe
    probe_fields = {name: np.where(plan.support, values[plan.rays, plan.gates], np.nan)[::-1]
                    for name, values in (("DBZH_RAW", raw), ("DBZH_QC", qc), ("QC_ACTION", action))}
    for name in ("QUALITY_INDEX", "QC_FLAGS", "NO_ECHO_MASK", "QUALITY_SCORE", "MB_QC_FLAGS", "XQC_REASON", "XQC_WITHHELD_MASK", "XQC_REJECTED_MASK", "XQC_QUANTITATIVE_READY_MASK", "XQC_SOURCE_KIND", "XQC_SOURCE_MIXED_MASK",
                 "XQC_CONTEXT_DONOR", "XQC_CONTEXT_RAY", "XQC_CONTEXT_GATE"):
        if name in sweep.fields and np.shape(sweep.fields[name]) == np.shape(raw):
            probe_fields[name] = np.where(plan.support, sweep.fields[name][plan.rays, plan.gates], np.nan)[::-1]
    probe_fields["DISPLAY_VALID"] = (plan.support & np.isfinite(qc[plan.rays, plan.gates]))[::-1].astype(float)
    probe_fields["SOURCE_RAY"] = np.where(plan.support, plan.rays, np.nan)[::-1]
    probe_fields["SOURCE_GATE"] = np.where(plan.support, plan.gates, np.nan)[::-1]
    probe = attach_probe(objects, f"query/sweeps/{sweep.number}", probe_fields, image_path=paths["qc"],
                         identity={"radar_id": metadata["radar_id"], "scan_id": metadata["scan_id"], "sweep_number": sweep.number})
    return {"crs": "EPSG:4326", "bounds": [west, south, east, north],
            "longitude_deg": float(lon), "latitude_deg": float(lat), "maximum_range_km": radius / 1000,
            "coordinate_source": "normalized_volume_site", "projection_version": "wgs84-geodesic-4over3-v1", "probe": probe, **paths}


@_perf_timed("fusion.product_encoding")
def composite_objects(result: Composite) -> dict[str, bytes]:
    arrays = encode_arrays(result.arrays)
    objects = {"arrays.npz": arrays, "cr.png": quicklook(result.arrays["CR_DBZH"]),
               "uncertain.png": quicklook(result.arrays["CR_UNCERTAIN_DBZH"])}
    manifest = {**result.metadata, "arrays_sha256": hashlib.sha256(arrays).hexdigest(),
                "layers": [{"object_path":"cr.png", "title":"S/X 融合组合反射率（独立候选）", "field":"CR_DBZH"},
                           {"object_path":"uncertain.png", "title":"未获得可信融合资格的回波（不替代可信产品）", "field":"CR_UNCERTAIN_DBZH"}],
                "legend": [{"minimum_dbzh": float(n), "rgb": list(map(int,c))} for n,c in zip(LEVELS,COLORS,strict=True)],
                "display_note":"投影网格快视图；不是经纬度瓦片，数值与来源以arrays.npz为准"}
    objects["manifest.json"] = json_bytes(manifest)
    return objects


def difference_quicklook(values: np.ndarray) -> bytes:
    """Render X-minus-S dBZ with a symmetric scale and transparent missing cells."""
    data = np.asarray(values, dtype=np.float32)
    supported = np.isfinite(data)
    stops = np.array([-20, -15, -10, -5, 0, 5, 10, 15, 20], dtype=np.float32)
    colors = np.array(
        [
            [31, 91, 171], [64, 132, 200], [126, 183, 221], [196, 222, 235],
            [246, 246, 239], [245, 205, 170], [224, 137, 105], [192, 76, 63],
            [142, 31, 50],
        ],
        dtype=np.uint8,
    )
    index = np.clip(np.searchsorted(stops, np.nan_to_num(data, nan=0), side="right") - 1, 0, len(stops)-1)
    rgba = np.zeros((*data.shape, 4), np.uint8)
    rgba[:, :, :3] = colors[index]
    rgba[:, :, 3] = np.where(supported, 255, 0)
    return png(rgba[::-1])


def geographic_composite_sample(values: np.ndarray, metadata: dict):
    """Reuse the exact nearest-cell plan within one comparison encoding."""
    from .composite_sampling import sample
    return sample(values, metadata)


def geographic_composite_preview(values: np.ndarray, metadata: dict) -> tuple[dict, bytes]:
    geometry, sampled = geographic_composite_sample(values, metadata)
    return geometry, quicklook(sampled)


def categorical_preview(values, legend):
    rgba = np.zeros((*values.shape, 4), np.uint8)
    for entry in legend:
        color = entry['color'].lstrip('#')
        rgba[values == entry['minimum']] = [*[int(color[i:i+2], 16) for i in (0, 2, 4)], 255]
    return png(rgba[::-1])


@_perf_timed("fusion.comparison_encoding")
def sx_comparison_objects(result: Composite, band_results: dict[str, Composite | None]) -> dict[str, bytes]:
    """Encode a versioned comparison without changing numerical selection."""
    from .comparison_finish import build
    return build(result, band_results)
