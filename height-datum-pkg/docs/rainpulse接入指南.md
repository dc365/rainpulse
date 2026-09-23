# RainPulse 接入指南

目标：把四站 `site.altitude_m / antenna_altitude_m` 从 EPSG:5737 换到 EPSG:3855，
并按证据强度决定 `altitude_datum_status`，解锁两条被卡住的链路：

1. `qc_geometry.build_vertical_consistency_diagnostics` 的 `strict_observability`
   要求 `verified_egm2008`（跨雷达门级支撑）；
2. clutter_fusion / near-joint 的 DEM 地形阻挡准入（`terrain_admission`）
   要求高程基准已核验，否则弃权。

## 步骤

### 第 0 步（先做，不改状态）：只更新数值、不改 status

按 `data/stations_egm2008.csv` 更新四站 YAML 的数值字段，datum 字段改为
`EPSG:3855`，status 写 `converted_literature_offset`。用
`heightdatum.rainpulse.site_yaml_snippet()` 生成片段，例如 Z9598：

```yaml
site:
  altitude_m: 1692.32
  antenna_altitude_m: 1740.32
  altitude_datum: "EPSG:3855"
  altitude_datum_status: "converted_literature_offset"
  altitude_sigma_m: 0.10
  altitude_evidence: "height-datum-1985-egm2008 v1.0.0; 方法=literature_offset(+0.32m)"
```

**配置版本号必须换新**（如 `z9598-fmt-20260915-height1985-v3` →
`z9598-egm2008-20260923-v1`），保持"冻结旧配置不动"的纪律；DEM 资产
`copernicus-dem-glo30-2022-v1` 本身已是 EGM2008，无需更换。

### 第 1 步：量化影响再决定是否翻 verified

运行一次成对回放（旧高程 vs 新高程，其余冻结），比较：
- 各站各仰角 PBB/CBB 阻挡率变化（预期 |Δ| < 0.02%）；
- 跨雷达支撑门数从全 NaN 变为有限值（这是真正的收益）；
- CF DEM 准入分支从弃权变为可评估。

### 第 2 步：翻 verified 的证据门槛

`verified_egm2008` 建议只在以下任一完成后翻转：

- **GNSS 锚定**：四站（至少 Z9598/Z9591 两个代表站）站址 GNSS 静态观测椭球高，
  走 `python -m heightdatum gnss ...`，本地 δ 与文献值残差 ≤0.15 m；
- **权威格网**：取得 CQG2000/福建省精化似大地水准面格网，以路线 C 重算，
  与路线 A 结果互差 ≤0.10 m。

证据 JSON 用 `heightdatum.rainpulse.evidence_json()` 生成（含格网 SHA256），
随 QC 资产注册留存。

### 第 3 步：下游联动检查

- 跨雷达支撑恢复后，V5/OC1 的"天气反证"保护会增强，RFI 隔离可能变化——
  按成对回放惯例验收，不要默认无影响；
- CF 的 verified_beam 垂直衰减 + DEM 准入同时激活后，注意审计字段中
  `support_status` 从 `reference_prepared_not_yet_comparable` 的迁移；
- 若 near-joint DEM 分支开始动作，按既有审计工具核查赢家资格与 CR 泄漏。

## 四站转换结果速查

| 站 | 天线高 1985 (m) | N_EGM2008 (m) | δ (m) | 天线高 EGM2008 (m) | 1σ |
|---|---:|---:|---:|---:|---:|
| Z9591 福州长乐 | 641.00 | +12.190 | +0.32 | 641.32 | ±0.10 |
| Z9593 宁德 | 546.00 | +12.441 | +0.32 | 546.32 | ±0.10 |
| Z9598 三明 | 1740.00 | +1.456 | +0.32 | 1740.32 | ±0.10 |
| Z9599 南平建阳 | 1047.00 | +4.798 | +0.32 | 1047.32 | ±0.10 |

（δ 为区域常数；GNSS 锚定后按站替换实测值并重发配置版本。）
