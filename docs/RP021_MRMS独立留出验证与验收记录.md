# RP-021 MRMS 独立留出验证与验收记录

更新时间：2026-08-29

## 1. 结论

RP-021 使用未参与 RP-016/RP-018 参数选择的 `2024-06` 与 `2025-01` MRMS
月份，完成了冻结参数、冻结阈值和冻结基线下的独立留出回放：

- 先从 MRMS 观测雨强冻结案例，再运行预报；选例阶段没有读取任何预报或技巧字段；
- 50/50 个 issue 完成，0 失败，258 个唯一依赖帧通过 manifest 尺寸和
  SHA-256 全校验；
- 近时效 `10–60 min` 结果为 `lk_supported`；
- 远时效 `70–120 min` 结果为 `translation_baseline_retained`，不能宣称稳定超过
  强平移基线；
- 5 个模型各有 600/600 个覆盖归因切片，0 缺片、0 域内缺测、0 闭合误差；
- 50 个不可变地图 bundle、3,000 个 PNG 图层全部通过 manifest 尺寸、SHA-256
  和 `501 × 201` 尺寸校验；
- 核心预报加评分 P95 为 `7.088 s`，单 issue 含地图的总耗时 P95 为
  `12.685 s`，峰值 RSS P95 约 `998 MiB`。

该结论证明当前 pySTEPS-LK 实现对这批美国 MRMS 独立案例具有近时效工程证据，
不能外推为福建业务可用，也不替代极坐标雷达质控、RQI、QPE 标定、雨量站检验和
真实五分钟雷达序列验收。

## 2. 观测侧冻结规则

选例工具只导入 MRMS 数据读取和固定经纬网格模块，不导入或运行 nowcast、基线或
评分实现。候选域由
`configs/verification/mrms-holdout-regions-v1.yaml` 冻结为 10 个 CONUS
`501 × 201`、`0.01°` 区域。每个整点读取同一 GRIB 一次并裁剪 10 个区域，记录：

- 有效覆盖率；
- `≥0.1 / ≥1 / ≥10 mm/h` 面积比例；
- 最大雨强。

每月选择两个不同区域的湿过程和一个干对照：

- 湿过程按锚点前后两小时共 5 个整点的
  `mean(area_fraction ≥1)+2×mean(area_fraction ≥10)` 排序；
- 至少 4/5 个整点有 `≥0.5%` 的区域达到 `1 mm/h`；
- 两个湿过程区域不同，案例锚点至少相隔 48 小时；
- 干对照按前后三小时内 `≥0.1 mm/h` 最大面积比例、平均面积比例依次升序；
- 所有筛选窗口有效覆盖率至少 99%。

完整证据在
`configs/verification/rp021-mrms-holdout-selection-v1.json`，其 SHA-256 为：

```text
db2c217d69417d0ffec13872dab2c41633589628d4df525a7f42adc04c6eea03
```

证据包含 14,640 条区域-整点统计的规范化摘要哈希，并明确记录
`model_forecast_or_skill_fields_read=false`。验证配置加载时会校验证据 SHA、选例协议
版本、案例标签、类别、issue 时段和网格坐标哈希，不能只替换配置中的案例。

## 3. 冻结案例

| 月份 | 类别 | 区域 | issue 范围 UTC | issue 数 |
|---|---|---|---|---:|
| 2024-06 | 湿 | Midwest | 2024-06-26 06:00–10:00，每 30 分钟 | 9 |
| 2024-06 | 湿 | Central Plains | 2024-06-09 04:00–08:00，每 30 分钟 | 9 |
| 2024-06 | 干 | Northeast | 2024-06-01 00:00–06:00，每 60 分钟 | 7 |
| 2025-01 | 湿 | Lower Mississippi Valley | 2025-01-10 06:00–10:00，每 30 分钟 | 9 |
| 2025-01 | 湿 | Southeast | 2025-01-18 10:00–14:00，每 30 分钟 | 9 |
| 2025-01 | 干 | Gulf Coast | 2025-01-01 00:00–06:00，每 60 分钟 | 7 |

合计 4 个湿案例、2 个干对照、50 个 issue。冻结 profile 为
`configs/verification/rp021-mrms-holdout-v1.yaml`，SHA-256 为：

```text
1a2c9a358b43f917393ddb22c64e25b801d8035ad3c737e2e463951b753297af
```

## 4. 冻结验收口径

RP-021 沿用 RP-018 的预报参数、雨强阈值、近/远时效、累计窗口和三条基线，不根据
留出结果调参。按 RP-020 的预声明口径：

1. FSS、CSI、POD、FAR 和误差继续在固定实况域评分，边界无预报仍按漏报处理；
2. 数据完整性使用
   `boundary_adjusted_forecast_to_truth_coverage >= 0.95`；
3. 原始覆盖率和边界损失继续报告，不作为重复的数据完整性处罚；
4. 任一覆盖切片缺失、域内缺测或闭合误差均使完整性失败；
5. 持续性、LK 整场平移和独立相位相关三条基线全部保留。

近时效使用 4 个湿案例、`1/5/10 mm/h`、约 `10 km` 邻域。每个阈值要求至少
3/4 个湿案例的 LK 相对对应基线为正；案例/issue 两级自举 2,000 次区间继续作为
不确定性诊断，但不事后替换冻结的案例计数门槛。

## 5. 近时效技能结果

| 基线 | 阈值 mm/h | 正技能案例 | 平均 FSS 差 | 95% 区间 |
|---|---:|---:|---:|---:|
| persistence | 1 | 4/4 | +0.01995 | +0.01132…+0.03034 |
| persistence | 5 | 4/4 | +0.03824 | +0.02521…+0.05289 |
| persistence | 10 | 4/4 | +0.05688 | +0.01519…+0.10240 |
| translation | 1 | 4/4 | +0.01140 | +0.00706…+0.01696 |
| translation | 5 | 4/4 | +0.02359 | +0.01121…+0.03983 |
| translation | 10 | 3/4 | +0.03388 | -0.00290…+0.07223 |
| phase correlation | 1 | 3/4 | +0.03112 | +0.00245…+0.08425 |
| phase correlation | 5 | 3/4 | +0.04293 | -0.00392…+0.10345 |
| phase correlation | 10 | 3/4 | +0.06445 | -0.00175…+0.14539 |

按冻结规则，近时效状态为 `lk_supported`。同时必须保留以下谨慎解释：

- translation `10 mm/h` 和 phase-correlation `5/10 mm/h` 的区间跨零；
- Southeast 案例相对 phase-correlation 在三个阈值均略为负；
- 结论是“满足当前案例计数工程门槛”，不是所有案例、所有基线均显著为正。

## 6. 远时效与时间适配

远时效状态为 `translation_baseline_retained`：

- 相对 translation 的 `5 mm/h` 只有 1/4 个湿案例为正，平均差虽为
  `+0.00573`，但未通过案例门槛；
- 相对 phase-correlation 的 `5 mm/h` 只有 2/4 个湿案例为正；
- 因此 70–120 分钟不能沿用近时效的 `lk_supported` 标签，整场平移和独立平移
  都必须继续保留。

MRMS 因果 5 分钟适配 LK 相对原生 10 分钟 LK 的平均 FSS 差为 `-0.03849`，
12,360 个配对切片中 8,059 个为负。MRMS 适配是离线验证缝，不等同于未来福建
原生五分钟雷达序列；当前证据明确禁止宣称“线性插值到五分钟会提高技巧”。

## 7. 覆盖归因与零几何域边界条件

最终 v2 报告中，各模型都是 600 个预期切片、600 个有效归因切片、0 缺片。
LK 原始平均/最低覆盖率为 `0.95721/0.72766`，平均/最大平流边界损失为
`0.04279/0.27234`；排除边界后的最低覆盖率为 `1.0`，域内缺测和闭合误差均为 0。

首轮 `independent-holdout-v1` 暴露了一个汇总器边界条件：Central Plains 的
相位相关基线在 3 个长时效切片中把几何域整体移出目标网格，排边界覆盖率出现
`0/0`，旧汇总器会静默只计 597/600 片。修复后：

- 固定实况域仍将这 3 片记作无预报并处罚漏报；
- 覆盖归因显式记录 `zero_advection_domain_slice_count=3`；
- 几何域内没有格点且域内缺测为 0 时，数据完整性按真空满足处理；
- 任何未解释的 NaN 或少片均 fail-closed。

修复后的 `independent-holdout-v2` 使用提交
`ec4bb04960d702c3e390f8ffb8abbcfe33b7d950`。v1/v2 的 90,000 行固定实况域
FSS、CSI、POD、FAR、误差和列联表字段逐行比较为 0 差异，证明修复没有改变预报
技巧结果。

## 8. 最终产物

服务器最终报告目录由外部部署配置管理，运行标识为：

```text
profile: rp021-mrms-holdout-v1
run: independent-holdout-v2
```

关键文件 SHA-256：

```text
summary.json              aba7753b15a2c30e306e74253522954ad7bbbdaf91a890a8d930d2a7beb11f68
metrics.csv              8ba0e613c5a780d14f4dc75bce2d5e875ee9b62b1b848c511ea716c071f465d3
metrics_truth_domain.csv 5f15c72c67f7f677be5794e780e1e4ba0627a4cff11e809e5a32d003aa79c94b
adaptation_metrics.csv   e98ee9148c034d46abba09672f12110d289723feb4d9c8c337cd7171388ca40c
accumulation_metrics.csv 59a8f782e512f79dd5ebc7c38dc47900a491ecdd8047d77ce5e976ed4217cd37
runtime_metrics.csv      d5c23d515f08b5cae32febc3c4e98c8b77e57583d2e27b0ed78749a687410668
maps/index.json          bdede6d22a1f32ec6a9cefbd93f8776857b6ae198b07d94f36ab091b8b212d06
```

报告约 108 MiB。地图索引、50 个 manifest 和 3,000 个 PNG 共 3,051 个文件；
所有 manifest 指向的图层均通过存在性、文件大小、SHA-256、PNG 签名和尺寸检查。

## 9. 后续边界

1. `2024-06` 与 `2025-01` 留出集现已使用完毕。不得根据本轮结果调参后继续把
   同一批数据称为独立留出验收；若改变运动、适配或技能门槛，必须另选新月份。
2. 近时效可保持当前 LK 工程实现；70–120 分钟继续明确标注尚未稳定超过强平移
   基线，不能删除 translation 或 phase-correlation。
3. 不优先为 MRMS 人工五分钟插值调参。福建原生雷达序列到位后，直接验证真实
   五分钟 NowcastInput；若确需改 MRMS 适配，只能用开发集，并另留新月份验收。
4. 无福建真实连续雷达前，可继续完善自动验证 fail-closed 检查、报告展示和运行
   运维；不得据此跳过极坐标质控、拼图/QPE 和业务真值门槛。

## 10. 可复现命令

```bash
MRMS_ROOT=/externally/configured/mrms-root \
MRMS_HOLDOUT_OUTPUT=/externally/configured/selection-output.json \
make mrms-holdout-select

MRMS_ROOT=/externally/configured/mrms-root \
MRMS_PROFILE=configs/verification/rp021-mrms-holdout-v1.yaml \
make mrms-conformance

MRMS_ROOT=/externally/configured/mrms-root \
MRMS_REPORT_ROOT=/externally/configured/report-root \
MRMS_PROFILE=configs/verification/rp021-mrms-holdout-v1.yaml \
MRMS_RUN_ID=independent-holdout-v2 \
make mrms-hindcast
```
