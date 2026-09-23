"""生成 rainpulse 站点配置的更新片段与 verified 证据模板。

注意：本模块只生成文本片段，不修改仓库文件。
`altitude_datum_status` 翻为 verified_egm2008 的前提（与 qc_geometry 的
strict_observability 对齐）应包含可追溯证据：格网版本+SHA256、
转换方法、以及 GNSS 锚定或权威格网的验证回执。仅文献常数转换建议
标记为 "converted_literature_offset"，待锚定后升级 "verified_egm2008"。
"""

from __future__ import annotations

import hashlib
import json
from datetime import datetime, timezone


def _require_anchor(anchor: dict | None) -> dict:
    required = {"gnss_ellipsoidal_h_m", "h_egm2008_m", "delta_residual_m"}
    if not isinstance(anchor, dict) or not required.issubset(anchor):
        raise ValueError("gnss_anchor requires a complete anchor report")
    return anchor


def site_yaml_snippet(radar_id: str, h_site_egm2008: float, h_antenna_egm2008: float,
                      *, sigma_m: float, method: str, anchor: dict | None = None) -> str:
    if method not in {"literature_offset", "grid_offset", "gnss_anchor"}:
        raise ValueError("unsupported height-datum conversion method")
    if method == "gnss_anchor":
        _require_anchor(anchor)
    status = "verified_egm2008" if method == "gnss_anchor" else "converted_literature_offset"
    return f"""# {radar_id} 站点配置更新片段（粘贴到 configs/radars/.../{radar_id}.yaml 的 site: 段）
site:
  altitude_m: {h_site_egm2008:.2f}            # 站址高程，EGM2008（EPSG:3855）
  antenna_altitude_m: {h_antenna_egm2008:.2f} # 天线相位中心高程，EGM2008
  altitude_datum: "EPSG:3855"
  altitude_datum_status: "{status}"
  altitude_sigma_m: {sigma_m:.2f}
  altitude_evidence: "height-datum-1985-egm2008 v1.0.0; 方法={method}"
"""


def evidence_json(radar_id: str, *, grid_path: str, method: str, h_1985: float,
                  h_egm2008: float, delta_m: float, sigma_m: float,
                  anchor: dict | None = None) -> str:
    """生成可追溯证据 JSON（随 QC 资产注册，支撑 datum status 翻转）。"""
    if method == "gnss_anchor":
        anchor = _require_anchor(anchor)
    with open(grid_path, "rb") as f:
        sha = hashlib.sha256(f.read()).hexdigest()
    payload = {
        "schema": "rainpulse.altitude-datum-evidence/v1",
        "radar_id": radar_id,
        "created_at": datetime.now(timezone.utc).isoformat(),
        "input": {"h_1985_m": h_1985, "datum": "EPSG:5737"},
        "output": {"h_egm2008_m": h_egm2008, "datum": "EPSG:3855", "sigma_m": sigma_m},
        "conversion": {"delta_m": delta_m, "method": method},
        "status": "verified_egm2008" if method == "gnss_anchor" else "converted_literature_offset",
        "grid": {"file": grid_path, "sha256": sha},
        "anchor": anchor,
    }
    return json.dumps(payload, ensure_ascii=False, indent=1)
