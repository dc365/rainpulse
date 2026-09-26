# ruff: noqa: E501, I001
"""A numerical candidate and small quicklooks; rendering never changes QC values."""
from __future__ import annotations

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
    """Render one polar sweep into a station-centred PPI without site geometry."""
    az = np.asarray(azimuth_deg, dtype=float) % 360
    ranges = np.asarray(range_m, dtype=float)
    data = np.asarray(values)
    if data.shape != (len(az), len(ranges)) or len(az) == 0 or len(ranges) == 0:
        raise ValueError("polar quicklook coordinates and field differ")
    radius = max(float(ranges[-1]), 1.0)
    axis = (np.arange(size, dtype=float) - (size - 1) / 2) * (2 * radius / size)
    xx, yy = np.meshgrid(axis, axis)
    distance = np.hypot(xx, yy)
    bearing = np.rad2deg(np.arctan2(xx, yy)) % 360
    if map_sampling is not None:
        distance, bearing = map_sampling
    # Binary-search sorted azimuths instead of allocating a pixels-by-rays cube.
    order = np.argsort(az)
    ordered = az[order]
    extended = np.concatenate(([ordered[-1] - 360], ordered, [ordered[0] + 360]))
    ray_order = np.concatenate(([order[-1]], order, [order[0]]))
    right = np.searchsorted(extended, bearing, side="left")
    left = np.clip(right - 1, 0, len(extended) - 1)
    right = np.clip(right, 0, len(extended) - 1)
    choose_right = np.abs(extended[right] - bearing) < np.abs(extended[left] - bearing)
    rays = ray_order[np.where(choose_right, right, left)]
    angular_distance = np.minimum(np.abs(az[rays] - bearing), 360 - np.abs(az[rays] - bearing))
    circular_steps = np.diff(np.concatenate((ordered, [ordered[0] + 360])))
    angular_limit = min(
        max(float(np.median(circular_steps[circular_steps > 0])) * 1.5, 0.1)
        if np.any(circular_steps > 0) else 0.1,
        MAX_PPI_INTERPOLATION_GAP_DEG,
    )
    if map_sampling is not None:
        # Invert the 4/3-Earth ground-arc equation using each selected ray's
        # actual elevation. No MSL height is assumed for this display mapping.
        effective_radius = 6371008.8 * 4 / 3
        angle = distance / effective_radius
        elevation = np.deg2rad(np.broadcast_to(elevation_deg, az.shape)[rays])
        denominator = np.cos(elevation + angle)
        distance = np.where(denominator > 0, effective_radius * np.sin(angle) / denominator, np.inf)
    gates = np.searchsorted(ranges, distance, side="left")
    valid = (distance <= radius) & (gates < len(ranges)) & (angular_distance <= angular_limit)
    gates = np.clip(gates, 0, len(ranges) - 1)
    sample = data[rays, gates]
    valid &= np.isfinite(sample)
    rgba = np.zeros((size, size, 4), np.uint8)
    index = np.clip(np.searchsorted(LEVELS, np.nan_to_num(sample, nan=-100), side="right") - 1, 0, len(LEVELS) - 1)
    rgba[:, :, :3] = COLORS[index]
    rgba[:, :, 3] = np.where(valid, 255, 0)
    if uncertain is not None:
        mask = np.asarray(uncertain, dtype=bool)[rays, gates] & valid
        # Amber pixels identify provisional/uncertain gates without hiding echo strength.
        rgba[mask, :3] = np.array([238, 160, 40], dtype=np.uint8)
    if actions is not None:
        action = np.asarray(actions, dtype=np.uint8)[rays, gates]
        rejected = (action == 2) & valid
        uncertain_gate = (action == 3) & valid
        rgba[:, :, 3] = np.where(rejected | uncertain_gate, 255, 0)
        rgba[rejected, :3] = np.array([191, 57, 48], dtype=np.uint8)
        rgba[uncertain_gate, :3] = np.array([238, 160, 40], dtype=np.uint8)
    return png(rgba[::-1])


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
            objects[raw_key] = polar_quicklook(azimuth, ranges, raw)
            # Confirmed rejects are removed from the display; uncertain gates retain
            # their reflectivity and are highlighted in amber in a separate layer.
            qc = np.where(action == 2, np.nan, fields["DBZH_QC"])
            objects[qc_key] = polar_quicklook(azimuth, ranges, qc)
            objects[flags_key] = polar_quicklook(azimuth, ranges, raw, actions=action)
            if sum(map(len, objects.values())) > MAX_X_QC_PREVIEW_BYTES:
                raise ValueError("X QC PPI preview exceeds 512 MiB output budget")
            layers.extend([
                {"object_path": raw_key, "title": f"原始反射率 · 第{sequence}层（扫层编号 {number}）", "field": "DBZH_RAW", "sweep_number": number},
                {"object_path": qc_key, "title": f"基础质控后 · 第{sequence}层（扫层编号 {number}）", "field": "DBZH_QC_DISPLAY", "sweep_number": number},
                {"object_path": flags_key, "title": f"疑似/确认标记 · 第{sequence}层（扫层编号 {number}）", "field": "QC_ACTION", "sweep_number": number},
            ])
            map_layer = geographic_sweep_preview(sweep, volume.metadata, raw, qc, action, objects)
            comparisons.append({"sweep_number": number, "sequence": sequence, "raw": raw_key, "qc": qc_key, "flags": flags_key,
                                "elevation_deg": float(np.nanmedian(sweep.elevation_deg)), **({"map": map_layer} if map_layer else {})})
        del volume
    if metadata is None or not comparisons:
        raise ValueError("X QC preview requires at least one reflectivity sweep")
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
    for name, values, actions in (("raw", raw, None), ("qc", qc, None), ("flags", raw, action)):
        objects[paths[name]] = polar_quicklook(sweep.azimuth_deg, ranges, values, actions=actions,
                                              size=size, map_sampling=sampling, elevation_deg=elevations)
    if sum(map(len, objects.values())) > MAX_X_QC_PREVIEW_BYTES:
        raise ValueError("X QC map preview exceeds 512 MiB output budget")
    return {"crs": "EPSG:4326", "bounds": [west, south, east, north],
            "longitude_deg": float(lon), "latitude_deg": float(lat), "maximum_range_km": radius / 1000,
            "coordinate_source": "normalized_volume_site", "projection_version": "wgs84-geodesic-4over3-v1", **paths}


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


def geographic_composite_preview(values: np.ndarray, metadata: dict) -> tuple[dict, bytes]:
    """Nearest-neighbour map preview; scientific arrays remain on the frozen grid."""
    from pyproj import Transformer

    width, height = int(metadata["width"]), int(metadata["height"])
    if values.shape != (height, width) or width < 1 or height < 1:
        raise ValueError("composite shape does not match grid")
    spacing = float(metadata["spacing_m"])
    west, south = float(metadata["west_m"]), float(metadata["south_m"])
    if spacing <= 0 or metadata["row_order"] != "south_to_north":
        raise ValueError("unsupported composite grid")
    forward = Transformer.from_crs(metadata["crs"], "EPSG:4326", always_xy=True)
    bounds = forward.transform_bounds(west, south, west + width * spacing,
                                      south + height * spacing, densify_pts=41)
    if not np.all(np.isfinite(bounds)) or bounds[0] >= bounds[2] or bounds[1] >= bounds[3]:
        raise ValueError("invalid map bounds")
    size = min(1024, max(width, height, 256))
    lon = bounds[0] + (np.arange(size) + .5) * (bounds[2] - bounds[0]) / size
    lat = bounds[1] + (np.arange(size) + .5) * (bounds[3] - bounds[1]) / size
    xx, yy = Transformer.from_crs("EPSG:4326", metadata["crs"], always_xy=True).transform(*np.meshgrid(lon, lat))
    cols = np.floor((xx - west) / spacing).astype(np.int64)
    rows = np.floor((yy - south) / spacing).astype(np.int64)
    valid = (cols >= 0) & (cols < width) & (rows >= 0) & (rows < height)
    sampled = np.full((size, size), np.nan, np.float32)
    sampled[valid] = values[rows[valid], cols[valid]]
    return {"crs": "EPSG:4326", "bounds": list(bounds), "resampling": "nearest", "source_grid_id": metadata["grid_id"]}, quicklook(sampled)


def sx_comparison_objects(result: Composite, band_results: dict[str, Composite | None]) -> dict[str, bytes]:
    """Attach S-only, X-only and joint-grid comparisons to one frozen task result."""
    objects = composite_objects(result)
    arrays = dict(result.arrays)
    products = []
    component_layers = []
    component_fields = {}
    for band, label in (("S", "S 单独"), ("X", "X 单独")):
        component = band_results.get(band)
        path = f"comparison/{band.lower()}_only.png"
        if component is None:
            products.append({
                "product_id": f"{band.lower()}_only",
                "band": band,
                "label": label,
                "status": "no_inputs",
                "object_path": None,
                "valid_echo_cells": 0,
                "source_count": 0,
                "reason": f"本时次没有可用的 {band} 波段输入",
            })
            component_fields[f"CR_DBZH_{band}_ONLY"] = np.full(
                result.arrays["CR_DBZH"].shape, np.nan, np.float32
            )
            continue
        objects[path] = quicklook(component.arrays["CR_DBZH"])
        arrays[f"CR_DBZH_{band}_ONLY"] = component.arrays["CR_DBZH"]
        component_fields[f"CR_DBZH_{band}_ONLY"] = component.arrays["CR_DBZH"]
        valid = int(component.metadata["valid_echo_cells"])
        valid_no_echo = int(component.metadata.get("valid_no_echo_cells", 0))
        uncertain = int(component.metadata["uncertain_only_cells"])
        products.append({
            "product_id": f"{band.lower()}_only",
            "band": band,
            "label": label,
            "status": "available" if valid else "no_echo" if valid_no_echo else "no_qualified_echo",
            "object_path": path,
            "valid_echo_cells": valid,
            "valid_no_echo_cells": valid_no_echo,
            "contributing_bands": [band] if valid or valid_no_echo else [],
            "echo_contributing_bands": [band] if valid else [],
            "uncertain_only_cells": uncertain,
            "source_count": len(component.metadata.get("sources", [])),
            "sources": component.metadata.get("sources", []),
            "skipped": component.metadata.get("skipped", []),
            "reason": (
                None
                if valid
                else f"{band} 有有效无回波覆盖，没有合格回波；不确定回波单独计数"
                if valid_no_echo
                else f"{band} 输入没有获得融合资格的回波；不确定回波单独计数"
            ),
        })
        component_layers.append({
            "object_path": path,
            "title": f"{label}组合反射率候选",
            "field": "CR_DBZH",
            "band": band,
        })

    s_values = component_fields["CR_DBZH_S_ONLY"]
    x_values = component_fields["CR_DBZH_X_ONLY"]
    both = np.isfinite(s_values) & np.isfinite(x_values)
    difference = np.full(s_values.shape, np.nan, np.float32)
    difference[both] = x_values[both] - s_values[both]
    arrays["DBZH_X_MINUS_S"] = difference
    difference_path = "comparison/x_minus_s.png"
    objects[difference_path] = difference_quicklook(difference)
    joint_path = "comparison/sx_composite.png"
    objects[joint_path] = quicklook(result.arrays["CR_DBZH"])
    contributing_bands = [
        band
        for band in ("S", "X")
        if band_results.get(band) is not None
        and (
            int(band_results[band].metadata["valid_echo_cells"])
            + int(band_results[band].metadata.get("valid_no_echo_cells", 0))
        ) > 0
    ]
    fused_echo_cells = int(result.metadata["valid_echo_cells"])
    fused_no_echo_cells = int(result.metadata.get("valid_no_echo_cells", 0))
    echo_contributing_bands = []
    winner_sources = result.arrays.get("WINNER_SOURCE")
    reflectivity = result.arrays.get("CR_DBZH")
    if winner_sources is not None and reflectivity is not None:
        used_indices = np.unique(winner_sources[np.isfinite(reflectivity)])
        source_records = result.metadata.get("sources", [])
        echo_contributing_bands = sorted({
            source_records[int(index)]["band"]
            for index in used_indices
            if 0 <= int(index) < len(source_records)
            and source_records[int(index)].get("band") in {"S", "X"}
        })
    if len(contributing_bands) == 1:
        fusion_label = (
            f"仅{contributing_bands[0]}覆盖（仅有效无回波像元）"
            if fused_no_echo_cells and not fused_echo_cells
            else f"仅{contributing_bands[0]}贡献（未形成 S/X 融合）"
        )
    elif not contributing_bands:
        fusion_label = "S/X 联合候选（无有效贡献）"
    elif fused_no_echo_cells and not fused_echo_cells:
        fusion_label = "S/X 融合（仅有效无回波像元）"
    elif fused_echo_cells and len(echo_contributing_bands) == 2:
        fusion_label = "S/X 融合"
    elif fused_echo_cells and len(echo_contributing_bands) == 1:
        fusion_label = f"S/X 融合（最终回波来自 {echo_contributing_bands[0]}）"
    else:
        fusion_label = "S/X 融合"
    missing_reason = None
    if len(contributing_bands) == 1:
        absent_band = "X" if contributing_bands[0] == "S" else "S"
        absent_product = next(item for item in products if item["band"] == absent_band)
        missing_reason = (
            f"{absent_band} 波段没有合格贡献（{absent_product['reason']}）；"
            f"结果仅由 {contributing_bands[0]} 波段贡献，未形成双波段融合"
        )
    elif fused_no_echo_cells and not fused_echo_cells and len(contributing_bands) == 2:
        missing_reason = (
            f"最终组合含 {fused_no_echo_cells:,} 个有效无回波像元，但没有有效回波"
        )
    elif fused_echo_cells and len(contributing_bands) == 2 and len(echo_contributing_bands) == 1:
        other_band = "X" if echo_contributing_bands[0] == "S" else "S"
        missing_reason = (
            f"最终组合的有效回波来自 {echo_contributing_bands[0]}；"
            f"{other_band} 波段未被选为最终回波来源"
        )
    elif not contributing_bands:
        missing_reason = "S 与 X 均没有获得融合资格的有效像元"
    products.append({
        "product_id": "sx_composite",
        "band": "S+X",
        "label": fusion_label,
        "status": "available" if fused_echo_cells else "no_echo" if fused_no_echo_cells else "no_qualified_echo",
        "object_path": joint_path,
        "valid_echo_cells": fused_echo_cells,
        "valid_no_echo_cells": fused_no_echo_cells,
        "uncertain_only_cells": int(result.metadata["uncertain_only_cells"]),
        "source_count": len(result.metadata.get("sources", [])),
        "contributing_bands": contributing_bands,
        "echo_contributing_bands": echo_contributing_bands,
        "sources": result.metadata.get("sources", []),
        "skipped": result.metadata.get("skipped", []),
        "reason": missing_reason,
    })
    products.append({
        "product_id": "x_minus_s",
        "band": "X-S",
        "label": "X−S 差值",
        "status": "available" if np.any(both) else "no_overlap",
        "object_path": difference_path,
        "valid_cells": int(np.count_nonzero(both)),
        "unit": "dBZ",
        "reason": None if np.any(both) else "S 与 X 单独产品没有共同的有效像元；缺测未按 0 处理",
    })
    arrays_object = encode_arrays(arrays)
    objects["arrays.npz"] = arrays_object
    manifest = json.loads(objects["manifest.json"])
    manifest["arrays_sha256"] = hashlib.sha256(arrays_object).hexdigest()
    manifest["layers"] = [
        *[
            {**layer, "title": f"{fusion_label}组合反射率候选"}
            if layer.get("object_path") == "cr.png" and layer.get("field") == "CR_DBZH"
            else layer
            for layer in manifest["layers"]
        ],
        *component_layers,
        {"object_path": joint_path, "title": f"{fusion_label}组合反射率候选", "field": "CR_DBZH"},
        {"object_path": difference_path, "title": "X−S 差值；仅显示双方有效像元", "field": "DBZH_X_MINUS_S"},
    ]
    manifest["comparison"] = {
        "cadence_seconds": int(manifest["cadence_seconds"]),
        "same_grid": True,
        "difference_semantics": "x_minus_s_where_both_are_finite",
        "difference_valid_cells": int(np.count_nonzero(both)),
        "products": products,
        "display_note": "同一冻结输入、投影网格与六分钟时次；快视图遵循网格 south_to_north 行序，非经纬度瓦片。",
    }
    for product_entry in products:
        field = {"s_only": "CR_DBZH_S_ONLY", "x_only": "CR_DBZH_X_ONLY", "sx_composite": "CR_DBZH"}.get(product_entry["product_id"])
        if field is None or product_entry.get("object_path") is None or "crs" not in result.metadata:
            continue
        geometry, preview = geographic_composite_preview(arrays[field], result.metadata)
        path = f"map/{product_entry['product_id']}.png"
        objects[path] = preview
        product_entry["map"] = {**geometry, "object_path": path}
        manifest["layers"].append({"object_path": path, "title": product_entry["label"], "field": field})
    manifest["comparison"]["comparison_group"] = hashlib.sha256(arrays_object).hexdigest()
    objects["manifest.json"] = json_bytes(manifest)
    return objects
