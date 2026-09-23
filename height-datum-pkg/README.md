# 高程基准转换包：1985 国家高程基准 → EGM2008

面向 RainPulse 福建四站 S 波段雷达的站高/天线高基准转换，解决
"站点高程（EPSG:5737）与 Copernicus GLO-30 DEM（EGM2008 / EPSG:3855）混用"
导致的 QC 高程链 `altitude_datum_status=unverified` 问题。

## 转换关系

```
h(椭球高) = H_1985 + ζ_1985(高程异常)
h(椭球高) = H_EGM2008 + N_EGM2008(大地水准面高)
⇒ H_EGM2008 = H_1985 + δ，其中 δ = ζ_1985 − N_EGM2008
```

福建区域（邻广东 0.323 m，全国 0.32 m）取 **δ = +0.32 m（1σ ≈ ±0.10 m）**：
**H_EGM2008 ≈ H_1985 + 0.32 m**。

## 包内容

| 路径 | 说明 |
|---|---|
| `heightdatum/` | Python 包：格网内插（零 GDAL 依赖）、转换、不确定度、GNSS 锚定、rainpulse 片段生成、CLI |
| `data/egm2008_fujian_2p5.{npy,tif,json}` | 福建区域（115.5–121.5°E, 22.5–29°N）EGM2008 N 格网，2.5′ 原生节点，取自 PROJ 官方 CDN `us_nga_egm08_25.tif` |
| `data/stations_input.csv` / `stations_egm2008.csv` | 四站输入/转换结果 |
| `docs/` | 方法与精度评估、rainpulse 接入指南、公开资源清单 |
| `report/福建四站转换报告.md` | 四站数值、不确定度预算、对波束阻挡的影响量化、定版验证清单 |
| `tests/` | pytest 回归（含 GeographicLib 官方在线计算器比对点） |

## 快速开始

```bash
cd height-datum-pkg
pip install pytest numpy   # 仅需 numpy；tifffile 可选（读 .tif 时）
python -m pytest tests -q

# 单点转换
PYTHONPATH=. python -m heightdatum point 117.08056 27.00861 1740.0

# 批量 CSV
PYTHONPATH=. python -m heightdatum csv data/stations_input.csv -o out.csv

# GNSS 锚定验证（有实测椭球高后）
PYTHONPATH=. python -m heightdatum gnss 117.08056 27.00861 <h_gnss> 1740.0
```

## 精度结论（详见 report/）

- 文献常数法：σ ≈ ±0.10 m（1σ）。真实 DEM 成对回放中，±0.10 m 引起的
  PBB/CBB 最大变化为 0.058%，阈值分类 flag-rate 最大变化为 0.156 个百分点；
  数值很小，但离散阈值会放大分类变化，因此仍只允许 audit，不允许动作；
- GNSS 锚定后：σ ≈ ±0.03–0.05 m；
- 但 rainpulse 的 `verified_egm2008` 状态仍按证据链纪律：文献转换标记为
  `converted_literature_offset`；GNSS 锚定或权威部门 ζ 格网验证回执齐全后再翻 verified。
  `site_yaml_snippet(..., method="gnss_anchor")` 必须传入 anchor report。

## 数据来源

- 格网：PROJ CDN `us_nga_egm08_25.tif`（US NGA EGM2008，2.5′，EPSG:3855 配套），
  与 GeographicLib 官方在线计算器交叉验证一致（福州点差 2 mm）；
- 系统差文献：翟振和等 2011（0.32 m，936 GPS/水准点）、许耿然等（广东 0.323 m）等，
  见 `heightdatum/offsets.py`。
