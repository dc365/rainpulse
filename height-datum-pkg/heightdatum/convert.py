"""高程基准转换核心。

记号：
  h        椭球高（CGCS2000/WGS84，两者椭球差异在厘米级，工程上可互换）
  H_1985   1985 国家高程基准正常高（EPSG:5737）
  H_EGM    EGM2008 正高（EPSG:3855，Copernicus GLO-30 DEM 所用基准）
  N        EGM2008 大地水准面高（EGM2008 格网）
  ζ_1985   1985 似大地水准面高程异常（CQG2000/省级精化，通常无公开格网）
  δ        ζ_1985 − N，即两基准的转换量

恒等式：h = H_1985 + ζ_1985 = H_EGM + N  =>  H_EGM = H_1985 + δ
"""

from __future__ import annotations

from .offsets import DEFAULT_OFFSET_M, DEFAULT_OFFSET_SIGMA_M


def egm2008_from_1985(h_1985: float, lon=None, lat=None, *, offset_m=None, offset_grid=None,
                      geoid_grid=None) -> float:
    """1985 正常高 → EGM2008 正高。

    offset 的优先级：offset_grid（δ=ζ−N 逐点格网，如 CQG2000−EGM2008）
    > offset_m（显式常数）> 文献默认 +0.32 m。
    提供 offset_grid 时必须同时提供 geoid_grid 以便 δ = ζ_grid − N_grid。
    """
    delta = _resolve_delta(lon, lat, offset_m, offset_grid, geoid_grid)
    return float(h_1985) + delta


def h1985_from_egm2008(h_egm: float, lon=None, lat=None, *, offset_m=None, offset_grid=None,
                       geoid_grid=None) -> float:
    """EGM2008 正高 → 1985 正常高（上式的反解）。"""
    delta = _resolve_delta(lon, lat, offset_m, offset_grid, geoid_grid)
    return float(h_egm) - delta


def egm2008_from_gnss(h_ellipsoidal: float, lon, lat, *, geoid_grid) -> float:
    """GNSS 锚定路线：H_EGM2008 = h − N(lon,lat)。最可靠，不经过 1985 基准。"""
    return float(h_ellipsoidal) - geoid_grid.sample(lon, lat)


def h1985_from_gnss(h_ellipsoidal: float, lon, lat, *, geoid_grid, offset_m=None,
                    offset_grid=None) -> float:
    """H_1985 = h − ζ_1985 = h − N − δ。"""
    delta = _resolve_delta(lon, lat, offset_m, offset_grid, geoid_grid)
    return float(h_ellipsoidal) - geoid_grid.sample(lon, lat) - delta


def local_offset_from_anchor(h_ellipsoidal: float, h_1985_known: float, lon, lat, *,
                             geoid_grid) -> float:
    """在已知 1985 高程的点上实测椭球高，反算本地 δ = h − N − H_1985。

    用于验证文献常数（+0.32 m），或标定本地常数/拟合区域改正面。
    """
    return float(h_ellipsoidal) - geoid_grid.sample(lon, lat) - float(h_1985_known)


def _resolve_delta(lon, lat, offset_m, offset_grid, geoid_grid) -> float:
    if offset_grid is not None:
        if geoid_grid is None:
            raise ValueError("offset_grid 需要配套 geoid_grid（δ=ζ−N）")
        if lon is None or lat is None:
            raise ValueError("使用格网 δ 必须提供经纬度")
        return offset_grid.sample(lon, lat) - geoid_grid.sample(lon, lat)
    if offset_m is not None:
        return float(offset_m)
    return DEFAULT_OFFSET_M


def uncertainty_budget(*, method: str = "literature", mountainous: bool = False,
                       gnss_sigma_m: float = 0.02) -> dict:
    """1σ 不确定度预算（米），返回各项与合成值。

    method:
      "literature"  文献常数 δ=+0.32（福建区域）：σ≈0.10 m
      "grid"        CQG2000/省级精化格网 δ：σ≈0.03–0.05 m
      "gnss"        GNSS 锚定：σ≈0.03–0.05 m（含格网误差）
    """
    items = {}
    if method == "literature":
        items["offset_spatial_variation"] = 0.07          # 区域残差空间变化（广东 0.323 vs 全国 0.32 同级）
        items["egm2008_commission_china"] = 0.05          # EGM2008 区域符合精度（去均值后）
        items["quasigeoid_minus_geoid"] = 0.05 if mountainous else 0.02  # N−ζ，山区取大
        items["grid_interpolation"] = 0.01
    elif method == "grid":
        items["offset_grid_accuracy"] = 0.03
        items["egm2008_commission_china"] = 0.03
        items["grid_interpolation"] = 0.01
    elif method == "gnss":
        items["gnss_ellipsoidal_height"] = float(gnss_sigma_m)
        items["egm2008_grid_value"] = 0.03
        items["antenna_reference_uncertainty"] = 0.01
    else:
        raise ValueError(method)
    items["total_rss"] = float(sum(v * v for v in items.values())) ** 0.5
    return items
