# RP-018 MRMS 验证严谨化实施记录

## 审核顺序

本分支建立在 RP-017 界面收敛分支之上。审核时先处理 PR-A（RP-017），再处理 PR-B（RP-018）；PR-A 合并后，将 PR-B 的目标分支改回 `main`，避免把界面改动与算法严谨化混在同一次审核中。

## 目标

RP-018 在 RP-016 MRMS 工程验证基础上增加更严格的对照和评分口径，同时保留现有 `metrics.csv`、地图包和 Go API 兼容性。

本阶段仍然只验证通用降水外推算法，不替代福建原始雷达基数据的极坐标质控、Hybrid Scan、QI 拼图、QPE 标定和本地实况检验。

## 新增验证链

### 1. 独立相位相关平移基线

原 `translation` 基线来自 LK 稠密运动场的全局中位位移，因此不是完全独立的算法对照。

RP-018 新增 `phase_correlation`：

```text
前一张观测雨强
+ 当前观测雨强
→ 公共有效域去均值与 Hann 加窗
→ FFT 相位相关估计整场位移
→ 独立平移未来雨强和有效掩膜
```

该基线不读取 LK 运动矢量。低方差或公共覆盖不足时显式记录零运动回退，不伪造位移。

### 2. 原生 10 分钟时间步长敏感性

RP-016 为适配生产 5 分钟接口，会利用 `t-20 / t-10 / t` 三张 MRMS 观测因果插值得到五张 5 分钟输入。

RP-018 同时运行：

- `lk_adapted_5min`：当前 5 分钟因果适配链；
- `lk_native_10min`：只使用三张真实 10 分钟观测，模型步长和输出步长均为 10 分钟。

两者共用同一 `run_pysteps_lk_fields` 核心，差异写入 `adaptation_metrics.csv` 和 `adaptation_summary`，用于判断插值是否改变算法技巧。

### 3. 双评分域

保留原有 `metrics.csv`：

- 所有模型共同有效域；
- 便于与 RP-016 历史结果和现有 API 比较。

新增 `metrics_truth_domain.csv`：

- 固定使用 MRMS 实况有效域；
- 模型缺失格点按“没有预报”处理；
- 额外记录 `forecast_to_truth_coverage`；
- 防止模型通过丢失困难区域抬高评分。

### 4. 覆盖率门槛

冻结门槛：

```text
forecast_to_truth_coverage >= 0.95
```

候选模型即使 FSS 为正，只要近时效评分切片存在覆盖不足，也不能判定为 `lk_supported`。

### 5. 物理尺度 FSS

新增目标邻域：

```text
1 / 5 / 10 / 20 / 40 km
```

每个案例根据实际网格平均间距转换为最近的奇数像素窗口，同时保存：

- `window_target_km`：目标物理尺度；
- `window_pixels`：实际滤波窗口；
- `window_km`：实际物理范围。

控制面继续用 `window_pixels` 作为内部筛选键，同时向 Web 提供 `fss_scales`：
目标公里尺度、实际覆盖范围和奇数网格窗口的稳定映射。界面只把
`1 / 5 / 10 / 20 / 40 km` 作为主标签，当前评分切片仍展示实际公里覆盖和
网格窗口，避免把目标尺度误解为精确的格点宽度。

旧 `metrics.csv` 没有 `window_target_km` 时仍可直接读取；冻结的
`1 / 5 / 11 / 21 / 41` 奇数窗口按 RP-018 物理尺度映射，未知窗口则回退到
报告中的实际公里值。API 查询仍保留 `window_pixels`，不破坏既有筛选链接。

### 6. 近、远时效分开报告

- 近时效：10–60 分钟，作为当前通过门槛；
- 远时效：70–120 分钟，独立生成 `far_skill_summary`，不再被近时效平均值掩盖。

### 7. 累积降水检验

新增：

- 0–1 小时累计；
- 0–2 小时累计；
- 1 / 5 / 10 / 25 / 50 mm 阈值；
- CSI、POD、FAR、FSS、MAE、RMSE和偏差；
- 固定实况有效域与物理尺度邻域。

结果保存到 `accumulation_metrics.csv`。

### 8. 运行环境指纹

`summary.json` 增加：

- Git commit；
- Python、NumPy、SciPy、OpenCV、pySTEPS、Rasterio版本；
- 操作系统、机器架构和处理器信息；
- OMP/OpenBLAS/MKL线程环境；
- 验证配置 SHA-256。

用于解释后续不同机器或依赖版本产生的微小数值差异。

正式回算必须记录完整 Git revision。在没有 `.git` 的部署目录中，通过
`RAINPULSE_BUILD_REVISION=<40或64位完整十六进制提交号>` 显式注入；短提交号
或非十六进制值会在读取数据前被拒绝，避免生成身份不完整的报告。

### 9. 逐起报性能证据

每个冻结起报独立记录：

- 输入读取与序列构建耗时；
- 适配 5 分钟 LK、原生 10 分钟 LK 和独立相位相关预报耗时；
- 实况读取、评分与地图生成耗时；
- 预报加评分的核心耗时、起报总耗时；
- 50 ms 采样的整进程峰值常驻内存。

结果保存到 `runtime_metrics.csv`。`summary.json` 的 `performance_summary`
给出已完成/失败起报数，以及总耗时、核心耗时和峰值 RSS 的 P50、P95、最大值。
RSS 是同一回算进程的常驻内存，不等同于单个函数的独占内存；该口径用于 105
服务器容量与连续运行基线，不作为跨操作系统的字节级性能承诺。

RP-018 地图包在 RP-016 的实况、LK、持续性和 LK 整场平移基础上增加独立
相位相关平移图层，使 Web 的第三种比较基线同时具备数值和空间证据。旧 RP-016
地图包不变，Reader 继续兼容。

## 输出文件

```text
metrics.csv                  共同有效域，兼容现有 API
metrics_truth_domain.csv     固定实况域 + 物理 FSS + 独立基线
adaptation_metrics.csv       5 分钟适配 vs 原生 10 分钟
accumulation_metrics.csv     0–1h / 0–2h 累积降水
runtime_metrics.csv          逐起报阶段耗时、核心耗时和峰值 RSS
summary.json                 门槛、覆盖、远时效、敏感性、环境与性能汇总
report.md                    人可读边界与结论
maps/                        兼容 RP-016 契约并增加独立平移基线图层
```

## 新配置

```text
configs/verification/rp018-mrms-v1.yaml
```

该配置完整继承 RP-016 的 5 个案例、53 个起报时次和坐标哈希，只增加严谨化参数；旧配置不修改。

## 运行

```bash
make mrms-conformance \
  MRMS_PROFILE=configs/verification/rp018-mrms-v1.yaml

make mrms-hindcast \
  MRMS_PROFILE=configs/verification/rp018-mrms-v1.yaml \
  MRMS_RUN_ID=rp018-full-v1
```

快速验证：

```bash
make mrms-hindcast \
  MRMS_PROFILE=configs/verification/rp018-mrms-v1.yaml \
  MRMS_CASE=socal_dry_20210805 \
  MRMS_MAX_ISSUES=1 \
  MRMS_RUN_ID=rp018-smoke
```

## 验收建议

```bash
make test-mrms
make test-python
make lint
make build
make test
```

全量 MRMS 回算完成后，Codex 还应核对：

1. `phase_correlation` 是否确实独立于 LK 运动场；
2. 固定实况域和共同域的评分差异；
3. 所有候选模型的覆盖率门槛；
4. 5 分钟适配与原生 10 分钟的敏感性；
5. 70–120 分钟是否出现灾难性退化；
6. 0–1h 和 0–2h 累积降水偏差；
7. `runtime_fingerprint` 是否完整；
8. `runtime_metrics.csv` 行数是否等于已完成加失败起报数，P50/P95/最大值是否可解释；
9. 独立相位相关基线是否同时具备指标和空间图层；
10. 福建本地链路仍保持 `operational_eligible=false`，不得因 MRMS 通过而自动转为业务产品。
