# ruff: noqa: E501, I001
"""A numerical candidate and small quicklooks; rendering never changes QC values."""
from __future__ import annotations

import hashlib
import json
import struct
import zlib
import numpy as np

from .codec import encode_arrays
from .fusion import Composite
from .model import json_bytes

# Stable meteorological quicklook colors; this is not a plotting-library style.
LEVELS = np.array([-10, 0, 10, 20, 30, 40, 50, 60, 70], dtype=float)
COLORS = np.array([[185,212,242],[142,194,241],[15,163,234],[6,210,21],[8,158,10],[240,172,20],[228,108,96],[203,21,170],[173,150,242]], dtype=np.uint8)
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
                    size: int = 720) -> bytes:
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
            uncertain = action == 3
            objects[qc_key] = polar_quicklook(azimuth, ranges, qc, uncertain=uncertain)
            objects[flags_key] = polar_quicklook(azimuth, ranges, raw, actions=action)
            if sum(map(len, objects.values())) > MAX_X_QC_PREVIEW_BYTES:
                raise ValueError("X QC PPI preview exceeds 512 MiB output budget")
            layers.extend([
                {"object_path": raw_key, "title": f"原始反射率 · 第{sequence}层（扫层编号 {number}）", "field": "DBZH_RAW", "sweep_number": number},
                {"object_path": qc_key, "title": f"基础质控后 · 第{sequence}层（扫层编号 {number}）", "field": "DBZH_QC_DISPLAY", "sweep_number": number},
                {"object_path": flags_key, "title": f"疑似/确认标记 · 第{sequence}层（扫层编号 {number}）", "field": "QC_ACTION", "sweep_number": number},
            ])
            comparisons.append({"sweep_number": number, "sequence": sequence, "raw": raw_key, "qc": qc_key, "flags": flags_key,
                                "elevation_deg": float(np.nanmedian(sweep.elevation_deg))})
        del volume
    if metadata is None or not comparisons:
        raise ValueError("X QC preview requires at least one reflectivity sweep")
    objects["manifest.json"] = json_bytes({
        "contract": "rainpulse.multiband.x-qc-preview-v1",
        "radar_id": metadata["radar_id"], "scan_id": metadata["scan_id"],
        "volume_start": metadata["volume_start"], "volume_end": metadata["volume_end"],
        "geometry": "station-centred polar; no geographic site geometry applied",
        "candidate_only": True, "operational_eligible": False, "qpe_enabled": False,
        "comparison": {"sweeps": comparisons},
        "layers": layers,
        "legend": [{"minimum_dbzh": float(n), "rgb": list(map(int, c))} for n, c in zip(LEVELS, COLORS, strict=True)],
        "flag_legend": [{"action": 2, "label": "确认无效/污染", "color": "#bf3930"},
                        {"action": 3, "label": "未决，待复核", "color": "#eea028"}],
        "note": "确认无效/污染门从显示场剔除；琥珀色为未决门，保留原强度；缺测和径向空隙保持透明。",
    })
    return objects


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
        uncertain = int(component.metadata["uncertain_only_cells"])
        products.append({
            "product_id": f"{band.lower()}_only",
            "band": band,
            "label": label,
            "status": "available" if valid else "no_qualified_echo",
            "object_path": path,
            "valid_echo_cells": valid,
            "uncertain_only_cells": uncertain,
            "source_count": len(component.metadata.get("sources", [])),
            "sources": component.metadata.get("sources", []),
            "skipped": component.metadata.get("skipped", []),
            "reason": None if valid else f"{band} 输入没有获得融合资格的回波；不确定回波单独计数",
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
    products.append({
        "product_id": "sx_composite",
        "band": "S+X",
        "label": "S/X 融合",
        "status": "available" if result.metadata["valid_echo_cells"] else "no_qualified_echo",
        "object_path": joint_path,
        "valid_echo_cells": int(result.metadata["valid_echo_cells"]),
        "uncertain_only_cells": int(result.metadata["uncertain_only_cells"]),
        "source_count": len(result.metadata.get("sources", [])),
        "sources": result.metadata.get("sources", []),
        "skipped": result.metadata.get("skipped", []),
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
    manifest["layers"] = [*manifest["layers"], *component_layers,
        {"object_path": joint_path, "title": "S/X 融合组合反射率候选", "field": "CR_DBZH"},
        {"object_path": difference_path, "title": "X−S 差值；仅显示双方有效像元", "field": "DBZH_X_MINUS_S"}]
    manifest["comparison"] = {
        "cadence_seconds": int(manifest["cadence_seconds"]),
        "same_grid": True,
        "difference_semantics": "x_minus_s_where_both_are_finite",
        "difference_valid_cells": int(np.count_nonzero(both)),
        "products": products,
        "display_note": "同一冻结输入、投影网格与六分钟时次；快视图遵循网格 south_to_north 行序，非经纬度瓦片。",
    }
    objects["manifest.json"] = json_bytes(manifest)
    return objects
