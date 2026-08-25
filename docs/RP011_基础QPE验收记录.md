# RP-011 基础 QPE 验收记录

日期：2026-08-25  
基线：`RainPulse_技术架构与实施方案_含雷达质控_v1.1.md`

## 1. 验收结论

RP-011 核心纵向链路已经完成：Go 控制面只从已原子提交的 RP-010
`RadarMosaic` 创建确定性 QPE 作业，Python Worker 按版本化 Z–R 配置生成
`RATE_QPE`，并发布完整的 `RadarAnalysis` v1.2。数据库迁移、事件契约、API、
对象清单和校验和、Zarr 数组约束、真实 Z9598 工程回放以及重复请求幂等均已
验证。

当前结论是“基础工程 QPE 链路通过”，不是“业务定量降水估计通过”。当前 Z–R
关系是首阶段工程基线，尚未用福建雨量站资料标定或检验；雨量站订正明确关闭，
且输入拼图只有一部未达到业务 ready 状态的雷达，因此产物保留
`operational_eligible=false`。

## 2. 冻结的算法和语义

- 配置：`rp011-basic-qpe-v1`；算法：`basic-zr-qpe-1.0.0`。
- 输入字段：`DBZH_QC`；关系式：`Z = 200 R^1.6`。
- 小于 `10 dBZ` 的有效格点输出 `0 mm/h`，表示“有效无雨”。
- 缺测格点的 `RATE_QPE` 必须为 `NaN`，不得转成无雨。
- 最大瞬时雨强限制为 `300 mm/h`，同时记录截断前最大值和截断格点数。
- `VALID_MASK`、`LOW_QUALITY_MASK`、`QUALITY_INDEX`、`QC_FLAGS`、来源雷达、
  来源仰角、波束高度、地形、遮挡率和数据年龄均从拼图无损保留。
- 当前上游拼图不能无损恢复极坐标 `DBZH_RAW`，也没有冻结的分类
  `INTERFERENCE_TYPE`；`RadarAnalysis` 不伪造这两个字段，原始/质控对比继续从
  单雷达 Normalized/QC 产物查询。
- 雨量站订正开关为 false；在雨量站位置、时间间隔和质控规则冻结前不得打开。

## 3. 产物和控制面

QPE 作业使用 `analysis.qpe.requested.v1`，独立 subject、durable consumer 和
常驻 `analysis-qpe-worker`。输出按算法版本隔离：

```text
analysis/{grid_id}/{yyyy}/{mm}/{dd}/{HHMMSSZ}/
{qpe_algorithm_version}/analysis.zarr
```

`RadarAnalysis` v1.2 在完整 RP-010 拼图字段之外增加 float32 `RATE_QPE`，并包含
`qpe/summary.json`。控制面新增：

- `qpe_runs` 和迁移 `0010_analysis_qpe.sql`；
- QPE 配置、算法、输入拼图、输出分析产物和诊断的不可变身份；
- `QPE_RUNNING → ANALYSIS_READY` 的独立完成路径；
- `GET /api/v1/analysis-cycles/{analysis_id}/qpe-summary`；
- 精确重放返回原 job，不重新发布消息，也不回退已完成状态。

## 4. 本地验证

实现阶段通过完整测试和静态检查：

```text
make test      # Python algorithms 55 tests；Go/Web/contracts 全部通过
make lint      # go vet、ruff、eslint、生成契约漂移检查全部通过
make build-linux
git diff --check
```

RP-011 专项测试覆盖 Z–R 数值、有效无雨、缺测、低质量保留、雨强截断、非法掩膜、
配置 gate、RadarAnalysis 字段/类型/摘要一致性、Worker 请求身份、Go 原子持久化、
完成校验、失败路径和重放幂等。

## 5. 测试服务器真实回放

部署目录：`<remote-project-dir>`  
运行版本：`rp011-v1.1-f8759bb-20260825`  
运行状态：12 个常驻 Compose 服务全部 healthy；数据库最新迁移为
`0010_analysis_qpe.sql`。现有 PostgreSQL、NATS 和 MinIO 卷均原地保留。

确定性工作流身份：

- analysis：`6c05c243-0a73-59a0-93f3-50da55248d1e`
- run：`74415ed9-b8f7-5c39-9732-43936fbafbe5`
- QPE job：`72497e28-f158-5b49-bfde-7b731ceb256a`

输入是 RP-010 已验收的不可变拼图，输出为：

```text
s3://rainpulse/analysis/fuzhou_118_123_25_27_0p01deg_v1/
2026/06/15/120500Z/basic-zr-qpe-1.0.0/analysis.zarr
```

直接按 MinIO `_SUCCESS.json` 清单重新读取并校验 142 个对象，结果为：

| 指标 | 值 |
|---|---:|
| 网格形状 | 201 × 501 |
| 总格点 | 100,701 |
| 有效格点 | 3,171 |
| 缺测格点 | 97,530 |
| 低质量格点 | 3,165 |
| 有效无雨格点 | 329 |
| 有效有雨格点 | 2,842 |
| 雨强截断格点 | 0 |
| 有效覆盖率 | 0.0314892603 |
| 平均 QI | 0.2891941667 |
| 有效格点平均雨强 | 2.1022082778 mm/h |
| 95 分位雨强 | 10.7301616669 mm/h |
| 最大雨强 | 31.5759372711 mm/h |
| 产物对象总大小 | 153,804 bytes |

产物的对象清单、逐对象 SHA-256、聚合校验和、契约版本、坐标、字段 shape/dtype、
掩膜语义、雨强范围及摘要与数组计数全部通过直接校验。

同一 QPE 命令重复执行后返回完全相同的 analysis/run/job ID；`qpe_runs` 仍只有
一条 `SUCCEEDED` 记录，分析保持 `ANALYSIS_READY`，`updated_at` 未变化，证明
重放没有产生重复工作流、重复产物或状态回退。

业务可用性继承 RP-010，为 false，原因是：

```text
insufficient_operational_contributors
input_not_operational:z9598
```

## 6. 尚未解除的门槛

1. 需要带可靠时间戳和 QC 的福建雨量站资料，完成 Z–R 关系检验、分区/分型评估
   及后续雨量站订正；当前参数不能作为业务精度结论。
2. 需要第二部及后续 ready 雷达和同期代表性体扫，完成真实多雷达 QPE 验收。
3. Z9598 权威站点/垂直基准、校准信息、静态杂波、海杂波/AP 和代表性天气案例
   仍是业务运行门槛。
4. 本次低质量格点 3,165 个仍有雨强值并以掩膜明确标记；下游业务发布和
   NowcastInput 必须按各自门槛处理，不能悄悄把它们当高质量输入。

## 7. 下一阶段

进入 RP-012 React 质控与拼图诊断：提供单雷达原始/质控对比、QI 与分量、flags、
来源雷达、来源仰角、波束高度、有效/低质量/缺测掩膜、拼图和 QPE 图层，以及
分析时次状态和降级原因。原始极坐标与网格分析产品不能假装是同一几何，应通过
各自 API/图层在同一诊断流程中关联展示。
