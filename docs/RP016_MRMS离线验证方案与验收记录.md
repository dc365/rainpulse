# RP016 MRMS 离线验证方案与验收记录

更新时间：2026-08-26

## 1. 结论

RP016 已增加一条与生产控制面隔离、但复用生产 pySTEPS-LK 算法核心的 MRMS 离线回放链。2021 年 8 月冻结案例已完成首轮全量工程验收：

- 53 个 issue 全部完成，0 个失败；
- 255 个案例依赖的唯一 MRMS 帧全部通过 manifest SHA、GRIB 元数据和固定 ROI 解码；
- 产出 57,240 行 LK、persistence、translation 共同有效域评分；
- 按冻结的 4 个湿案例、1/5/10 mm/h、10–60 分钟、11 像素 FSS 门禁，结果为 `lk_supported`；
- 为 53 个 issue 生成 53 个不可变地图 bundle、2,544 张 PNG 图层，并在原 Web 中提供实况、LK、基线同步地图；
- 含地图渲染的连续 53 个 issue 端到端回放耗时 7 分 14 秒，峰值 RSS 约 795 MiB；
- 部署后的 pySTEPS-LK Worker 为 Healthy，API/Web 冒烟通过。

该结论只表示当前算法和实现已经获得美国 MRMS 率产品上的工程证据，不能外推为福建业务就绪，也不能替代极坐标雷达质控、RQI、QPE 标定和预报置信度校准。

## 2. 验证边界

本轮验证：

```text
MRMS gzip GRIB2（10 分钟）
→ manifest 尺寸与 SHA
→ PrecipRate 元数据和四态解码
→ 201 × 501 固定点中心 ROI
→ 因果 5 分钟输入序列
→ 生产共用 pySTEPS-LK 数组核心
→ persistence / translation
→ 实际 10 分钟未来帧评分
→ CSV / JSON / Markdown 报告
→ 实况 / LK / persistence / translation 不可变 PNG 地图证据
```

本轮不验证：

- 福建雷达极坐标解码和基础质控识别率；
- MRMS RQI、RainPulse `QUALITY_INDEX` 或 `confidence` 校准；
- 雷达拼图、Z–R 参数和雨量站订正的业务准确性；
- 5、15、25 分钟等插值未来帧上的主验收结论；
- 美国案例到福建天气气候、地形和雷达体制的可迁移性。

MRMS 没有当前 RainPulse 所需的 RQI。本适配器在有效像素上使用中性质量 `1.0`，低质量标记为 `0`，并始终设置 `operational_eligible=false`。

## 3. 数据契约

### 3.1 MRMS 源语义

冻结产品为 `PrecipRate_00.00`，单位 `mm/h`。GRIB 本地 discipline 为 `209`，网格为 7000 × 3500、0.01°、北到南扫描。适配器只读取目标窗口，不生成完整 CONUS 解压缓存。

源状态保持为：

| 源值 | 语义 | 模型有效性 |
|---:|---|---|
| `-3` | no coverage | 无效 |
| `-1` | missing | 无效 |
| `0` | valid no-rain | 有效 |
| `> 0` | rain rate | 有效 |

`missing` 与 `no coverage` 对模型都无效，但在 `source_state` 中保持不同代码，不能改写为无雨。

### 3.2 10 分钟到 5 分钟的因果适配

每个对齐到 10 分钟的 issue 使用 `t-20/t-10/t` 三个实况源帧，生成：

```text
t-20, t-15, t-10, t-5, t
```

中间帧只在前后两个已到达源帧共同有效时做雨强线性插值。`t-15` 只依赖 `t-20/t-10`，`t-5` 只依赖 `t-10/t`，不读取 issue 之后的未来数据。实况帧 `DATA_AGE=0`，插值帧 `DATA_AGE=5`。

运动输入 `DBZH_QC` 是由 MRMS 雨强按 `Z=200R^1.6` 反算的替代量；`R=0` 固定为 `0 dBZ`。其 provenance 为 `surrogate_from_mrms_rate_z200_r1p6`，不能描述成真实质控反射率。

### 3.3 主评分真值

主评分只使用 issue 后 `+10,+20,...,+120` 分钟的实际 MRMS 源帧。模型 5 分钟 lead 中仅抽取对应的偶数时效，避免把插值未来帧当作独立真值。

每个 lead 的 truth、LK、persistence、translation 先求共同有效掩膜，再计算：

- CSI、POD、FAR；
- FSS，阈值 `0.1/1/5/10/20/50 mm/h`，窗口 `1/5/11/21/41` 像素；
- MAE、RMSE、平均误差；
- truth、各模型和共同有效域覆盖率。

## 4. 冻结案例

| 案例 | 类型 | issue 数 | issue 范围 UTC | ROI 点中心范围 |
|---|---|---:|---|---|
| Southern California | 干对照 | 13 | 2021-08-05 06:00–18:00，每 60 分钟 | -121.995…-116.995, 33.005…35.005 |
| Midwest convection | 湿案例 | 9 | 2021-08-10 17:00–21:00，每 30 分钟 | -94.995…-89.995, 39.005…41.005 |
| Fred | 湿案例 | 9 | 2021-08-16 16:00–20:00，每 30 分钟 | -87.995…-82.995, 28.005…30.005 |
| Henri | 湿案例 | 9 | 2021-08-22 13:00–17:00，每 30 分钟 | -74.995…-69.995, 40.005…42.005 |
| Ida | 湿案例 | 13 | 2021-08-29 14:00–20:00，每 30 分钟 | -92.995…-87.995, 28.005…30.005 |

全部网格均为 201 × 501、0.01°、纬度南到北、经度西到东，并由配置中的坐标 SHA-256 冻结。

## 5. 数据完整性结果

2021 年 8 月归档有 4,463 个 10 分钟文件；理论值为 4,464。唯一缺帧是：

```text
2021-08-10T13:50:00Z
```

因此整月 `mrms-verify` 应严格返回 source completeness 失败，但 transport integrity 仍可为通过。冻结的 53 个 issue 不依赖该时刻，全案例依赖图包含 255 个唯一帧，全部通过：

```text
checked_issue_count = 53
checked_frame_count = 255
failed_frame_count = 0
complete = true
```

单独构造的 `2021-08-10T14:00:00Z` issue 会因必需的 `13:50` 源帧不存在而在数据源边界失败，不能被插值静默掩盖。

## 6. 预报技巧结果

冻结门禁使用湿案例、10–60 分钟、11 像素 FSS。每个阈值要求 LK 相对基线在至少 3/4 湿案例上为正。95% 区间按案例、issue 两级有放回重采样 2,000 次。

| 基线 | 阈值 mm/h | 正技能案例 | 平均 FSS 差 | 95% 区间 |
|---|---:|---:|---:|---:|
| persistence | 1 | 4/4 | +0.02718 | +0.00697…+0.05411 |
| persistence | 5 | 4/4 | +0.02590 | +0.01712…+0.03645 |
| persistence | 10 | 4/4 | +0.04199 | +0.02486…+0.06265 |
| translation | 1 | 4/4 | +0.00760 | +0.00372…+0.01129 |
| translation | 5 | 4/4 | +0.00725 | +0.00089…+0.01465 |
| translation | 10 | 3/4 | +0.01348 | +0.00090…+0.02707 |

按预先冻结的案例计数规则，状态为 `lk_supported`。LK 相对 translation 的差值明显小于相对 persistence 的差值，且 10 mm/h 仍有一个湿案例为负；后续增加月份和独立留出案例时，translation 必须继续保留为强基线，不能根据本批测试删除。

## 7. 性能与故障结果

| 项目 | 结果 |
|---|---|
| 单个干案例端到端 | 约 7.3 秒，峰值约 512 MiB |
| 单个 Ida 湿案例端到端 | 约 8.0 秒，峰值约 568 MiB |
| 53 issue 连续回放（仅评分，v2） | 6 分 18 秒，峰值约 786 MiB |
| 53 issue 连续回放（评分与地图，v3） | 7 分 13.62 秒，峰值 813,616 KiB（约 795 MiB） |
| 1.5 GiB RSS 门禁 | 通过 |
| 10 秒单 issue 端到端观察值 | 两个代表样本通过 |
| 精确算法核心 p95 | 尚未单独埋点，不能宣称通过 |
| 100 次顺序运行 RSS 增长率 | 尚未执行，不能宣称通过 |

故障测试覆盖 manifest SHA 错误、必需时刻缺帧、missing/no-coverage/no-rain/rain 四态、干场零运动回退和无有效运动域回退。

## 8. 产物与部署

当前全量运行目录为 `${MRMS_REPORT_ROOT}/rp016-mrms-v1/full-202108-v3`：

```text
metrics.csv   57,240 rows
summary.json
report.md
maps/index.json
maps/<case>/<issue>/manifest.json  53 files
maps/<case>/<issue>/*.png          2,544 files
```

运行目录约 46 MiB，其中 PNG 总计 23,914,291 字节。全部 2,544 张图均按 manifest 重新校验 SHA-256、PNG 签名和 `501 × 201` 像素尺寸。地图 renderer 为 `algorithm-verification-map-renderer-1.0.0`，不会参与评分或改变模型数组。

验收时 SHA-256：

```text
summary.json 9eb751b859b9a67cb42440e2955faaa62fc864e4c8ccf3c8e728dca4758b1c71
metrics.csv  aa5278c10d9ae22a2f7113e7f093194bfed342eb6a3549da12e1f8453d7099bb
```

生产 Zarr 入口仍执行原有 NowcastInput `operational_eligible` 和身份校验；离线 MRMS 入口不能伪造生产输入。部署只增加公开数组级核心并由两个入口共同调用。部署后的 `pysteps-lk-worker` 健康检查、数组入口导入、API 与 Web 冒烟均通过。

### 8.1 Web 算法验证视图

现有 Web 顶部增加“算法验证”入口。它通过 Go 控制面只读访问 `${MRMS_REPORT_ROOT}/{profile_version}/{run_id}`，展示运行台账、冻结技能门禁、案例与 issue、12 个实际 10 分钟时效以及筛选后的 FSS/CSI/POD/FAR/MAE/覆盖率。浏览器不会直接访问服务器目录，也不会下载完整 `metrics.csv`；当前筛选固定只返回一个 issue、一个阈值和一个 FSS 窗口的三模型记录。

地图工作台在同一时间轴上显示实况、pySTEPS-LK 和所选 persistence/translation 基线，三图共享经纬度范围、缩放、拖拽和固定雨强色标，并可叠加稀疏 LK 运动矢量。时效支持 `+10…+120` 分钟逐帧选择和播放；桌面为三列，平板为实况加双模型，手机使用实况/LK/基线标签切换。Go 接口只允许读取 manifest 白名单中的 PNG，校验文件 SHA 和尺寸，并返回不可变缓存头，原始数组仍不会进入浏览器。

控制面接口和页面采用通用 algorithm-verification 契约，MRMS 只是当前 `primary_truth_kind`。福建数据到位后，只要离线验证器继续生成相同 `summary.json` 与 `metrics.csv` 字段，即可在同一页面展示，不需要复制 LK 算法或另建区域专用页面。

Compose 通过 `RAINPULSE_ALGORITHM_VERIFICATION_HOST_ROOT` 将报告根目录只读挂载到 API。该视图始终显示 `operational_eligible=false` 和“非业务验收”边界；空间图层是离线验证证据，不写数据库或对象存储，也不改变生产预报和雷达质控流程。桌面 1440 px、平板 768 px、手机 390 px 实机浏览器检查通过，无横向溢出、控制台错误或警告；旧版无地图运行会明确降级，且不会残留其他运行的地图状态。

### 8.2 重复运行说明

保留的 v2 与 v3 `metrics.csv` 并非逐字节一致。差异只出现在 `ida_20210829` 的 `2021-08-29T15:30:00Z` 单个 issue（1,080 行），其余 52 个 issue 逐字节一致。v3 之后的两次普通聚焦回放、禁用全部地图渲染的回放和单线程回放均与 v3 的该 issue 一致，因此地图渲染副作用和线程数已被排除，源 NOAA 文件及 manifest 也未变化。

历史 v2 没有记录完整 Python/依赖/线程环境指纹，无法事后把该差异归因到一个可证明的变量。v2 原样保留，v3 作为当前环境下已重复得到的基线；在补齐运行环境指纹和源 manifest 聚合摘要前，不宣称跨历史环境的评分文件可逐字节复现。该差异没有改变预声明门禁的 `lk_supported` 结论。

## 9. 可复现命令

```bash
make test-mrms
make mrms-faults

MRMS_ROOT=/externally/configured/mrms-root make mrms-conformance
MRMS_ROOT=/externally/configured/mrms-root \
MRMS_REPORT_ROOT=/externally/configured/report-root \
MRMS_RUN_ID=full-202108-v3 \
make mrms-hindcast
```

## 10. 后续门槛

1. 为每次验证运行冻结 Python/依赖/数值线程环境指纹和源 manifest 聚合摘要，建立跨运行可复现门禁。
2. 将后续下载月份作为独立留出集，保持当前阈值、案例规则和模型参数不变，检查技能是否稳定。
3. 增加算法核心逐 issue 时延和 RSS 采样，完成 p95 ≤ 10 秒与 100 次 RSS 增长 ≤ 10% 的正式门禁。
4. 增加累计降水、分区/分天气类型汇总和更长时效诊断，但不得取代当前三基线共同掩膜评分。
5. 福建连续雷达数据到位后，回到生产链验证极坐标质控、质量指数、QPE 和固定 5 分钟实况序列；MRMS 结果只能作为算法实现证据。
6. 雨量站、第二部雷达、静态杂波和海杂波/AP 资产到位前，系统仍保持工程验证状态。
