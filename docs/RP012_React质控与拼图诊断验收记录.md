# RP-012 React 质控与拼图诊断验收记录

日期：2026-08-25
基线：`RainPulse_技术架构与实施方案_含雷达质控_v1.1.md`

## 1. 验收结论

RP-012 核心纵向链路已经完成。Go 控制面从已提交的 RP-011
`RadarAnalysis` 和该分析实际参与雷达的精确 `QCRadarVolume` 创建确定性诊断
作业；Python Worker 预渲染带 Alpha 的网格/PPI PNG 并以不可变 bundle 原子
发布；React 只通过 Go API 读取 manifest、指标和图片，不解析 Zarr 数组。

真实 Z9598 工程回放、对象清单复读、PNG 几何与 Alpha、API 白名单读取、重复请求
幂等、桌面/平板/手机布局均已验证。页面明确区分原始极坐标、质控极坐标和
EPSG:4326 分析网格，也明确区分有效无雨、低质量和缺测。

本次验收证明诊断证据链可用，不改变 RP-011 的业务可用性结论。当前输入只有一部
未 ready 雷达，诊断页面和 manifest 均保留 `operational_eligible=false`。

## 2. 冻结的图层和语义

配置为 `rp012-operational-diagnostics-v1`，渲染器为
`radar-diagnostic-renderer-1.0.0`，bundle 契约为 1.0，色表为
`rainpulse-meteorological-v1`。

每个分析固定生成 7 个网格图层：

1. `DBZH_QC`
2. `RATE_QPE`
3. `QUALITY_INDEX`
4. `SOURCE_RADAR`
5. `BEAM_HEIGHT`
6. `QC_FLAGS`
7. `VALID_MASK+LOW_QUALITY_MASK`

每部实际贡献雷达固定生成 4 个最低可用 DBZH 仰角 PPI 图层：

1. `DBZH_RAW`
2. `DBZH_QC`
3. `QUALITY_INDEX`
4. `QC_FLAGS`

网格图按北向上渲染并携带像元边界；PPI 携带 radar、scan、sweep、仰角和最大
距离身份。缺测像素 Alpha 为 0；有效无雨保留有值的 `0 mm/h`，不能显示成
透明；低质量像素继续有效，但由独立掩膜和原因标志标识。

## 3. 产物与控制面

诊断作业使用 `analysis.diagnostics.requested.v1`、独立 subject 和 durable
consumer，常驻 `analysis-diagnostics-worker`。输出按分析和渲染器版本隔离：

```text
diagnostics/{analysis_id}/{renderer_version}/diagnostics/
├── manifest.json
└── layers/{layer_id}.png
```

控制面新增：

- `diagnostic_runs` 和迁移 `0011_analysis_diagnostics.sql`；
- 诊断配置、输入分析、全部实际贡献雷达 QC URI、渲染器和 bundle 的不可变身份；
- 完成边界对精确 7 个网格字段及每雷达 4 个极坐标字段的二次校验；
- 诊断失败只标记诊断作业失败，不回退或污染已就绪分析；
- `GET /api/v1/analysis-cycles/{analysis_id}/diagnostics`；
- `GET /api/v1/diagnostics/{job_id}/layers/{layer_id}`。

图片 API 只允许读取成功 manifest 中列出的对象路径，并限制对象大小、校验 PNG
签名、返回不可变缓存头和 ETag；调用方不能借该接口读取任意 MinIO key。

## 4. React 诊断工作台

新增“分析诊断”工作区，包括：

- 分析时次与 5 分钟 UTC 证据链；
- 工程降级原因、覆盖率、QI、低质量、有雨/无雨和雨强摘要；
- 网格/PPI 几何切换及冻结图层切换；
- 原始/质控反射率同屏对照；
- 透明棋盘、三态说明、字段/几何/尺寸/版本/图例证据；
- 实际贡献雷达、scan、时差、QI、拼图与 QPE 版本台账；
- 对旧 RP-004 simulation 周期的兼容，同时默认进入真实 RadarAnalysis 诊断。

界面采用证据工作台而非装饰性卡片，颜色只表达物理量、质量和降级。实际浏览器
在 1440、768、375 px 视口通过；平板摘要断点经截图复核后改为 2×2，最终页面
首次加载真实分析且控制台为 0 error、0 warning。

## 5. 本地验证

```text
make test      # Python algorithms 59 tests；Go/Web/contracts 全部通过
make lint      # go vet、ruff、eslint、生成契约漂移检查全部通过
make build-linux
git diff --check
```

专项测试覆盖配置 Schema、透明 PNG 编码、零值可见/缺测透明、完整 11 图层 bundle、
路径穿越拒绝、Worker 路由、Go 请求身份与幂等、API manifest/图片白名单、React
网格/PPI 切换及历史 simulation 默认选择规则。

## 6. 测试服务器真实验收

部署目录：`<remote-project-dir>`
运行版本：`rp012-v1.1-ff46a14-20260825`
运行状态：13 个常驻 Compose 服务全部 healthy；数据库最新迁移为
`0011_analysis_diagnostics.sql`。PostgreSQL、NATS 和 MinIO 既有卷原地保留。

确定性身份：

- analysis：`6c05c243-0a73-59a0-93f3-50da55248d1e`
- run：`74415ed9-b8f7-5c39-9732-43936fbafbe5`
- diagnostic job：`c3064158-9305-52cf-b1e3-c910efa9d9c7`
- contributor scan：`d8d89100-3558-5651-8428-1991170ad888`（Z9598）

输出：

```text
s3://rainpulse/diagnostics/6c05c243-0a73-59a0-93f3-50da55248d1e/
radar-diagnostic-renderer-1.0.0/diagnostics
```

| 指标 | 值 |
|---|---:|
| 运行时间 | 2,502 ms |
| bundle 对象数 | 12 |
| 图层数 | 11 |
| 网格图层数 | 7 |
| 雷达数 | 1 |
| bundle 总大小 | 75,041 bytes |
| 网格 PNG | 1002 × 402，Alpha=yes |
| PPI PNG | 640 × 640，Alpha=yes |
| PPI sweep / 仰角 | 0 / 0.50° |
| PPI 最大距离 | 459.875 km |

Worker 从 MinIO `_SUCCESS.json` 清单重新读取全部对象后再次通过 bundle 校验。
两个图片 API 返回 `image/png`、`immutable`、`nosniff` 和 ETag。相同命令重复执行
返回同一 job；`diagnostic_runs` 仍只有一条 `SUCCEEDED`，分析周期保持
`ANALYSIS_READY`，`updated_at=2026-08-25 08:16:05.335631+00` 未变化。

### 6.1 分析网格 GIS 化补充验收（2026-08-26）

分析网格不再作为脱离地理上下文的普通图片显示，而是复用短临工作区的
OpenLayers/EPSG:4326 栅格地图内核。地图严格使用 DiagnosticBundle 中冻结的
像元边界，不从文件名、图片尺寸或前端常量反推范围；当前真实图层边界为
`117.995,24.995,123.005,27.005`。默认图层调整为 `DBZH_QC`，雨强、QI、波束
高度、来源雷达、质控标志和三态掩膜仍可在同一地图中切换。标量图例采用连续
色阶，分类/标志图例采用可滚动列表，透明区继续明确表示缺测。

极坐标 PPI 没有 EPSG:4326 边界，因此仍保留原始/质控对照诊断图，不进行错误
地理配准。真实产物浏览器复核包括 `DBZH_QC`、`QC_FLAGS` 和 PPI 切换；对应
诊断图片请求均返回 200。1440 px 桌面和 390 px 移动端通过，移动端页面宽度与
视口同为 390 px，无页面级横向溢出。较新但尚未生成 DiagnosticBundle 的分析
时次继续显示空态；这不回退到旧图层，也不影响有诊断产物时次的 GIS 显示。

## 7. 未解除的业务门槛与下一阶段

诊断已把问题暴露出来，但不会替代业务标定：Z9598 ready 元数据、静态杂波、
海杂波/AP、垂直基准、代表性天气案例、第二部同期雷达和雨量站真值仍未解除。

下一阶段进入 RP-013 `NowcastInput`：从连续 5 分钟分析时次构造严格 3–6 帧输入，
冻结缺帧、时间抖动、低质量覆盖和降级门槛，并保证缺测、有效无雨、低质量语义
继续分离。当前只有一个真实验收时次，因此可以先完成契约/控制面/合成序列纵向
链路；真实序列验收需要补充至少 3 个连续质控体扫并完成对应拼图/QPE。
