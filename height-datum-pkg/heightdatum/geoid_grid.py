"""大地水准面/似大地水准面格网的读取与双线性内插。

支持三种来源，全部零 GDAL 依赖：
  - .npy + .json（本包随附的福建区域 EGM2008 格网，推荐）
  - 未压缩 GeoTIFF（本包随附的 .tif；需要 tifffile，可选）
  - GeographicLib PGM（egm2008-1/2_5/5，可自行下载全球 1′ 格网）

约定：格网值为 N（大地水准面高，米，WGS84 椭球之上），节点配准，
第 0 行在最北（lat_max），第 0 列在最西（lon_min）。
"""

from __future__ import annotations

import json
import os
import struct

import numpy as np


class GeoidGrid:
    def __init__(self, values: np.ndarray, lon_min: float, lat_max: float, res_deg: float, name: str = ""):
        self.values = np.asarray(values, dtype=np.float64)
        self.lon_min = float(lon_min)
        self.lat_max = float(lat_max)
        self.res = float(res_deg)
        self.name = name
        self.nlat, self.nlon = self.values.shape

    # ------------------------------------------------------------------ 加载
    @classmethod
    def load(cls, path: str) -> "GeoidGrid":
        ext = os.path.splitext(path)[1].lower()
        if ext == ".npy":
            meta_path = os.path.splitext(path)[0] + ".json"
            with open(meta_path, encoding="utf-8") as f:
                meta = json.load(f)
            return cls(np.load(path), meta["lon_min"], meta["lat_max"], meta["res_deg"],
                       name=os.path.basename(path))
        if ext in (".tif", ".tiff"):
            return cls._load_tiff(path)
        if ext == ".pgm":
            return cls._load_geographiclib_pgm(path)
        raise ValueError(f"不支持的格网格式: {path}")

    @classmethod
    def _load_tiff(cls, path: str) -> "GeoidGrid":
        try:
            import tifffile
        except ImportError as e:  # pragma: no cover
            raise ImportError("读取 GeoTIFF 需要 tifffile（pip install tifffile）；或改用 .npy/.pgm") from e
        with tifffile.TiffFile(path) as tf:
            page = tf.pages[0]
            vals = page.asarray()
            tags = {t.name: t.value for t in page.tags.values()}
            scale = tags["ModelPixelScaleTag"]
            tie = tags["ModelTiepointTag"]
            res = float(scale[0])
            # PixelIsArea：tiepoint 为西北角像素的外角；节点（像素中心）在其东/南各半像素
            lon_min = float(tie[3]) + res / 2
            lat_max = float(tie[4]) - res / 2
            return cls(vals, lon_min, lat_max, res, name=os.path.basename(path))

    @classmethod
    def _load_geographiclib_pgm(cls, path: str) -> "GeoidGrid":
        """GeographicLib 16 位 PGM：N = raw*0.003 − 10800（米），全球节点配准。"""
        with open(path, "rb") as f:
            data = f.read()
        # 头：P5\n# Offset -10800\n# Scale 0.003\n# ...\n<width> <height>\n65535\n<binary>
        m = re_match = None
        import re
        m = re.match(rb"P5\s+((?:#[^\n]*\n|\s+)*?)(\d+)\s+(\d+)\s+(\d+)\s", data, re.S)
        if not m:
            raise ValueError("不是合法的 GeographicLib PGM")
        header, w, h, maxval = m.group(1), int(m.group(2)), int(m.group(3)), int(m.group(4))
        mo = re.search(rb"#\s*Offset\s+(-?\d+)", header)
        ms = re.search(rb"#\s*Scale\s+([\d.eE+-]+)", header)
        offset = float(mo.group(1)) if mo else -10800.0
        scale = float(ms.group(1)) if ms else 0.003
        body = data[m.end():m.end() + w * h * 2]
        raw = np.frombuffer(body, dtype=">u2").reshape(h, w)
        vals = raw.astype(np.float64) * scale + offset
        res = 360.0 / w
        return cls(vals, -180.0, 90.0, res, name=os.path.basename(path))

    # ------------------------------------------------------------------ 内插
    def sample(self, lon: float, lat: float) -> float:
        """双线性内插；出界抛 ValueError（不外推）。"""
        x = (float(lon) - self.lon_min) / self.res
        y = (self.lat_max - float(lat)) / self.res
        if not (0 <= x <= self.nlon - 1 and 0 <= y <= self.nlat - 1):
            raise ValueError(f"({lon},{lat}) 出格网范围")
        x = min(x, self.nlon - 1.0000001)
        y = min(y, self.nlat - 1.0000001)
        x0, y0 = int(x), int(y)
        dx, dy = x - x0, y - y0
        v = self.values
        return float(v[y0, x0] * (1 - dx) * (1 - dy) + v[y0, x0 + 1] * dx * (1 - dy)
                     + v[y0 + 1, x0] * (1 - dx) * dy + v[y0 + 1, x0 + 1] * dx * dy)

    def sample_many(self, lons, lats):
        return np.array([self.sample(lo, la) for lo, la in zip(np.atleast_1d(lons), np.atleast_1d(lats))])
