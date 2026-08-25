# RP-014 pySTEPS-LK 验收记录

日期：2026-08-25
代码基线：`e40c09b`
测试服务器：`deployment-host.internal`
部署目录：`<remote-project-dir>`

## 1. 验收结论

RP-014 核心纵向切片已完成并通过服务器端到端验收：真实 pySTEPS dense
Lucas–Kanade 估计、半拉格朗日外推、物理 U/V、24 个五分钟时效、持续性与整场
平移基线、ForecastOutput v1.1、Go 调度/持久化/事件和消息重放幂等均已打通。

本次输入是明确标记的静态合成验收序列，只证明软件链路、契约、缺测语义和失败
回退，不证明真实降水预报能力。真实业务验收仍需连续业务可用的雷达分析序列和
可跟踪回波。

## 2. 冻结的实现边界

- 输入：`NowcastInput` v1.2，3–6 帧、固定 5 分钟、EPSG:4326 福州
  `201 × 501` 网格。
- 主模型：`pysteps-lk` / `pysteps-lk-1.0.0`，配置
  `rp014-pysteps-lk-v1`。
- 运动：pySTEPS dense Lucas–Kanade；无有效特征时显式零运动回退，不能静默伪造
  位移。
- 外推：pySTEPS semi-Lagrangian，5–120 分钟，共 24 个时效。
- 基线：持续性和整场平移；二者与主预报分别保留有效掩膜。
- 缺测：运动估计工作副本可填充值，业务输出继续使用平流后的有效掩膜；缺测格点
  必须为 NaN，不能变成无雨。
- 运动单位：内部像素/5 分钟转换为 `m/s`，东西向距离按 WGS84 纬度逐行计算。
- 输出：原子发布的 `ForecastOutput` v1.1 Zarr；主降水率为
  `member × lead × lat × lon`，另含 60/120 分钟累计量、置信度、U/V 和两类基线。

## 3. 实现内容

### 3.1 Python 计算面

- 新增长期运行的 `pysteps-lk-worker`，独立 subject、consumer、健康检查和 900 秒
  ack 窗口。
- 固定 pySTEPS `1.21.5`、OpenCV Headless `5.0.0.93`、SciPy `1.18.1`。
- pySTEPS 当前仅提供源码包；Linux 制品构建复制已锁定的纯 Python 模块，并绕过
  未使用的 Proesmans/VET 可选原生扩展。OpenCV/SciPy 使用固定 manylinux wheel、
  断点续传和 SHA-256 校验。
- ForecastOutput 构建后立即从内存对象重新打开并做强校验，再交给原子发布器。

### 3.2 Go 控制面

- 新增 `forecast.pysteps_lk.requested.v1` 和
  `forecast.baseline.ready.v1` 契约、outbox 发布与 replay 路由。
- 只有已提交的 `INPUT_READY`、S3 NowcastInput 和完整原始资产溯源可进入
  `BASELINE_RUNNING`。
- `model_runs` 持久化输入/输出 URI、诊断、测量时刻和运行时间；完成事件再次比对
  run/job/model/config/grid/input assets/24 时效/缺测策略后才进入 `BASELINE_READY`。
- 数据库迁移：`0013_pysteps_lk.sql`。

### 3.3 RP-013 溯源补丁

旧验收输入的 Zarr 根属性已有 3 个原始资产 UUID，但旧 summary/数据库诊断没有该
字段。没有改写旧的不可变对象，而是新增
`rp013-fixed-5min-v1.1` / `nowcast-input-builder-1.0.1`，从同一组分析帧生成新的
溯源完整输入。

## 4. 本地验证

- `make test`：配置 17 项、契约 32 项、算法 68 项、Go 全包和 Web 2 项通过。
- `make lint`、`git diff --check` 通过。
- `make build-linux`、`make build-worker-linux` 通过。
- Linux worker 制品内 OpenCV/SciPy 原生库均验证为 x86-64 ELF；pySTEPS 目录不含
  macOS 或未使用的可选原生扩展。

## 5. 测试服务器验收

### 5.1 部署与运行环境

- `0013_pysteps_lk.sql` 已应用。
- 15 个长期 Compose 服务全部 healthy，新增 `pysteps-lk-worker`。
- 容器内实际加载：OpenCV `5.0.0`、SciPy `1.18.1`、
  `dense_lucaskanade`、`semilagrangian.extrapolate`。

### 5.2 溯源完整的输入

- forecast run：`0ce8e90c-3160-5e5d-874d-1eda09bf1084`
- RP-013 job：`e03068c8-5ff6-5f7f-8f39-4c0ee90fab32`
- 状态：`INPUT_READY / SUCCEEDED`
- 原始资产数：3
- 输入：

```text
s3://rainpulse/nowcast-input/fuzhou_118_123_25_27_0p01deg_v1/
2026/08/25/100000Z/nowcast-input-builder-1.0.1/input.zarr
```

### 5.3 RP-014 输出

- RP-014 job：`d92a752d-a42e-5f09-a18b-9a9b904631a2`
- model run：`79eff584-e840-5f38-a5fd-d69a0caaf6fc`
- 最终状态：`BASELINE_READY / SUCCEEDED / completed`
- Worker 总运行时间：2313 ms；模型算法计算时间：2042 ms。
- requested 与 baseline-ready 事件各 1 条，均为 `published`。
- 输出：

```text
s3://rainpulse/products/0ce8e90c-3160-5e5d-874d-1eda09bf1084/
pysteps-lk/pysteps-lk-1.0.0/forecast.zarr
```

| 指标 | 值 |
|---|---:|
| 主降水率 shape | 1 × 24 × 201 × 501 |
| 时效 | 5–120 min，步长 5 min |
| 首时效有效覆盖率 | 0.9500998 |
| 末时效有效覆盖率 | 0.9500998 |
| 最大降水率 | 2.0 mm/h |
| 最大 60 分钟累计量 | 2.0 mm |
| 最大 120 分钟累计量 | 4.0 mm |
| Zarr 对象数 | 726 |
| bundle 大小 | 77,557 bytes |
| 原始输入资产数 | 3 |
| 缺测降水率全部 NaN | true |
| 运动回退 | true（静态合成场，预期） |

### 5.4 幂等重放

对已完成 job 执行 request replay 后，forecast run 仍为 `BASELINE_READY`，数据库仍
只有 1 个 pySTEPS job、1 个 model run 和 1 个 completion inbox 事件。已发布对象
由原子 marker 校验并复用，没有生成第二份 ForecastOutput。

## 6. 未解除门槛与下一步

- 真实 RP-013/RP-014 仍需至少 3 个连续、业务可用、具有可跟踪回波的
  QC→grid→mosaic→QPE 时次。
- 当前零运动回退只说明静态夹具行为符合契约；不能据此评价 LK 速度场或预报技巧。
- 真实多雷达拼图、雨量站订正、静态杂波/海杂波/AP、垂直基准和代表性山区遮挡
  仍是业务门槛。
- 下一阶段 RP-015：基于 ForecastOutput 构建 0–1 h/0–2 h 应用产品、透明地图图层/
  COG、REST/SSE 和 React 短临地图时间轴；内部大 Zarr 不直接交给前端，只有约定的
  应用降雨产品生成 NetCDF。
