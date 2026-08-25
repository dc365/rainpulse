# RP-013 NowcastInput 验收记录

日期：2026-08-25
基线：`RainPulse_技术架构与实施方案_含雷达质控_v1.1.md`

## 1. 验收结论

RP-013 核心纵向链路已经完成。Go 控制面只从已提交的 `RadarAnalysis` 中选择
以预报起报时刻结束的连续 5 分钟序列，Python Worker 再次校验每一帧的契约、
时次、网格、QPE 版本、质量门槛和上游业务资格，然后构造 canonical
`NowcastInput` Zarr 并原子发布。完成事件通过事务更新输入记录，将预报运行从
`PREPROCESSING` 推进至 `INPUT_READY`，并发布 `nowcast.input.ready.v1`。

本次在 测试服务器完成了两类验收：

- 现有真实 Z9598 工程分析被业务门禁正确拒绝，没有伪造连续时次，也没有进入
  模型输入阶段；
- 三帧、完整福州网格的显式合成验收序列打通编排、NATS、Worker、MinIO、
  PostgreSQL 和 ready 事件全链路。

因此 RP-013 可供 RP-014 pySTEPS 集成使用，但真实气象序列的业务验收仍未完成。
它需要至少三个连续、经过真实极坐标质控并满足业务资格的 `RadarAnalysis` 时次。

## 2. 冻结的输入契约与门槛

配置为 `rp013-fixed-5min-v1`，构建器为
`nowcast-input-builder-1.0.0`，`NowcastInput` 契约版本为 1.2。

固定规则如下：

- 时间维为 3–6 帧，间隔严格为 5 分钟，最后一帧必须等于起报时刻；
- 网格为 `fuzhou_118_123_25_27_0p01deg_v1`，EPSG:4326、`0.01°`、
  `201 × 501`，坐标哈希必须与冻结网格一致；
- 必需变量为 `DBZH_QC`、`RATE_QPE`、`QUALITY_INDEX`、`QC_FLAGS`、
  `VALID_MASK`、`LOW_QUALITY_MASK` 和 `DATA_AGE`，可选保留 `BEAM_HEIGHT`；
- 缺测必须保持 NaN + 无效掩膜；有效无雨保持 `RATE_QPE=0`；低质量仍为有效值，
  由独立掩膜标识；
- 任何缺帧、时间抖动、混合网格、混合 QPE 版本、上游非业务可用或质量门槛失败
  都直接拒绝，不进行时间插值或补零。

当前版本化候选门槛为：最小逐帧有效覆盖率 0.70、序列有效格点平均 QI 0.45、
最大数据年龄 10 分钟，并要求全部上游分析 `operational_eligible=true`。这些是工程
初值，必须使用代表性多雷达、雨量站和天气过程重新标定后才能业务签署。

## 3. 产物、控制面与事件

迁移 `0012_nowcast_input.sql` 新增：

- `INPUT_READY` 预报状态；
- `nowcast_input_runs`，保存起报时刻、构建器/门槛版本、输出 URI 和完整诊断；
- `nowcast_input_frames`，按序保存每一帧 analysis ID、时次和不可变输入 URI。

输入请求、完成和 ready 边界都会核对 run/job/trace、时次、网格、版本、帧数和
逐帧溯源。正式事件为：

```text
rainpulse.jobs.requested.nowcast_input
→ rainpulse.jobs.completed
→ rainpulse.jobs.lifecycle.nowcast_input_ready
```

输出路径按网格、起报时刻和构建器版本隔离：

```text
nowcast-input/{grid_id}/{YYYY}/{MM}/{DD}/{HHMMSS}Z/{builder_version}/input.zarr
```

Worker 在最终路径写入全部 Zarr 对象并校验后，最后写 `_SUCCESS.json`；复读时逐对象
核对大小、SHA-256 和 bundle SHA-256。

## 4. 本地验证

以下检查全部通过：

```text
uv run --project algorithms pytest contracts/tests algorithms/tests configs/tests
go test ./services/control/...
make lint
make build
git diff --check
```

专项测试覆盖：三态语义、连续帧成功、缺帧拒绝、上游降级拒绝、覆盖率/QI/数据年龄
门禁、混合 QPE 版本拒绝、确定性身份、事务持久化、完成事件身份核对、失败状态和
ready 事件发布。

## 5. 测试服务器验收

部署目录：`<remote-project-dir>`
运行版本：`rp013-v1.1-68574c0-20260825`
运行状态：14 个常驻 Compose 服务全部 healthy；数据库最新迁移为
`0012_nowcast_input.sql`。既有 PostgreSQL、NATS 和 MinIO 卷原地保留。

### 5.1 真实工程分析负向门禁

对真实 analysis `6c05c243-0a73-59a0-93f3-50da55248d1e`、起报时刻
`2026-06-15T12:05:00Z` 触发 RP-013，控制面返回：

```text
RadarAnalysis 6c05c243-0a73-59a0-93f3-50da55248d1e
is not operationally eligible
```

该分析仅有 3.15% 覆盖率、平均 QI 约 0.289，并继承 Z9598 未 ready 和单雷达工程
回放原因。请求退出码为 1，没有创建可供模型消费的真实 `NowcastInput`。

### 5.2 显式合成全链路验收

验收夹具 `scripts/rp013_synthetic_acceptance_fixture.py` 创建三个完整
`201 × 501` 福州网格帧。三帧源对象、对象属性和源分析记录分别使用
`rp013-acceptance/synthetic`、`synthetic_acceptance_fixture=true` 和
`rp013-synthetic-acceptance-v1` 标识；最终输入的逐帧 URI 继续保留该溯源，不能
作为真实气象样本或准确率证据。

确定性身份：

- analysis：`81300000-0000-4000-8000-000000000001`、`...0002`、`...0003`
- analysis time：`09:50`、`09:55`、`10:00 UTC`
- forecast run：`a78aa324-0832-59e1-b9ea-d97933b2821e`
- input job：`498308d8-994e-522b-b976-e1e9ce242e6f`
- trace：`43ec6162-bd64-567e-8644-b5985bdce319`

输出：

```text
s3://rainpulse/nowcast-input/fuzhou_118_123_25_27_0p01deg_v1/
2026/08/25/100000Z/nowcast-input-builder-1.0.0/input.zarr
```

| 指标 | 值 |
|---|---:|
| 输出 shape | 3 × 201 × 501 |
| 固定时间步长 | 5 min |
| 有效覆盖率（最差帧） | 0.9500998 |
| 平均 QI | 0.7706303 |
| 最大数据年龄 | 0.75 min |
| 有效格点数 | 287,028 |
| 缺测格点数 | 15,075 |
| 低质量格点数 | 30,150 |
| Zarr 对象数 | 125 |
| bundle 大小 | 26,202 bytes |
| Worker 运行时间 | 214 ms |

最终状态为 `forecast_runs=INPUT_READY`、`nowcast_input_runs=SUCCEEDED`。请求事件和
ready 事件均为 `published`。Worker 从 MinIO `_SUCCESS.json` 重新读取 125 个对象
并再次通过 `NowcastInput` 契约校验。相同命令重复执行返回相同 run/job，输入运行表
仍只有一条成功记录。

## 6. 未解除门槛与下一阶段

真实 RP-013 验收仍需要：连续至少三个业务可用雷达分析时次、ready 雷达元数据、
代表性天气过程、静态杂波/海杂波/AP 资产、质量门槛标定和后续雨量站检验。当前
合成序列只证明软件链路和三态语义，不证明真实回波运动或降水预报能力。

下一阶段进入 RP-014：以本次 `NowcastInput` 先打通 pySTEPS-LK 基线，冻结输入变换、
运动场 U/V、24 个 5 分钟时效、持续性与整场平移基线、失败/幂等行为及
`ForecastOutput` v1.1。真实预报验收继续等待连续业务可用分析序列。
