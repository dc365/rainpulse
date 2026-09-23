"""GNSS 锚定工作流：把「文献常数转换」升级为「本地实测验证」。

业务定版推荐路线（与 rainpulse 的 verified_egm2008 证据链对齐）：

1. 在雷达站天线基座/站址标志点上做 GNSS 静态观测（≥2 h，PPP 或 CORS 网解），
   得 CGCS2000 椭球高 h（σ≈0.02 m）；
2. H_EGM2008 = h − N(lon,lat)（本包格网或全球 1′ 格网）；
3. 反算本地 δ_meas = h − N − H_1985，与文献 +0.32 m 比对：
   |δ_meas − 0.32| ≤ 0.15 m 视为文献值在该站成立；差异更大时以实测为准并复核
   站高抄录值（抄录误差是更常见的污染源）；
4. 将「实测回执 ID + 计算链 + 格网版本/SHA256」写入 rainpulse 站点配置，
   高程基准状态方可翻为 verified。

没有 GNSS 条件时的降级路线：向省级测绘部门申请 CQG2000 或省级精化
似大地水准面格网（ζ_1985），以 δ = ζ − N 逐点替换常数，精度 ±0.03–0.05 m。
"""

from __future__ import annotations

from .convert import egm2008_from_gnss, local_offset_from_anchor


def anchor_report(station: str, lon: float, lat: float, h_gnss_m: float, h_1985_m: float,
                  *, geoid_grid, literature_offset_m: float = 0.32) -> dict:
    """生成单站锚定报告（字典，可直接 json.dumps）。"""
    h_egm = egm2008_from_gnss(h_gnss_m, lon, lat, geoid_grid=geoid_grid)
    delta_meas = local_offset_from_anchor(h_gnss_m, h_1985_m, lon, lat, geoid_grid=geoid_grid)
    diff = delta_meas - literature_offset_m
    return {
        "station": station,
        "lon_deg": lon,
        "lat_deg": lat,
        "gnss_ellipsoidal_h_m": h_gnss_m,
        "N_egm2008_m": round(geoid_grid.sample(lon, lat), 4),
        "h_egm2008_m": round(h_egm, 3),
        "h_1985_recorded_m": h_1985_m,
        "delta_measured_m": round(delta_meas, 3),
        "delta_literature_m": literature_offset_m,
        "delta_residual_m": round(diff, 3),
        "literature_consistent": bool(abs(diff) <= 0.15),
        "action": (
            "文献值成立，可依据实测值定版"
            if abs(diff) <= 0.15
            else "残差超阈：复核站高抄录值与 GNSS 观测，以实测 δ 为准"
        ),
    }
