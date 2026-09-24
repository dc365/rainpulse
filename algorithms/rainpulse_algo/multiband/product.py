# ruff: noqa: E501, I001
"""A numerical candidate and small quicklooks; rendering never changes QC values."""
from __future__ import annotations

import hashlib
import struct
import zlib
import numpy as np

from .codec import encode_arrays
from .fusion import Composite
from .model import json_bytes

# Stable meteorological quicklook colors; this is not a plotting-library style.
LEVELS = np.array([-10, 0, 10, 20, 30, 40, 50, 60, 70], dtype=float)
COLORS = np.array([[185,212,242],[142,194,241],[15,163,234],[6,210,21],[8,158,10],[240,172,20],[228,108,96],[203,21,170],[173,150,242]], dtype=np.uint8)


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
