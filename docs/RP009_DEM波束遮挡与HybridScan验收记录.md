# RP-009 DEM 波束遮挡与 Hybrid Scan 验收记录

验收日期：2026-08-25  
状态：核心工程垂直切片通过；业务发布门槛未解除

## 1. 本次范围

本次从 RP-008 已提交的不可变 `QCRadarVolume` 开始，完成以下真实链路：

```text
QCRadarVolume
→ 原生 GLO-30 DEM 采样
→ 极坐标逐射线波束高度、部分遮挡率和累计遮挡率
→ 最低可用仰角选择
→ 福州 0.01° 单雷达 RadarGrid / Hybrid Scan
→ MinIO、PostgreSQL、REST API 和幂等事件账本
```

输入 QC 体积没有被修改。逐仰角遮挡诊断随 `RadarGrid` 一起保存，避免把某次
网格算法的派生结果反写到版本已经冻结的 RP-008 产物。

## 2. 冻结算法和配置

配置：`configs/gridding/rp009-hybrid-v1.yaml`

```text
profile_version: rp009-hybrid-v1
algorithm_version: hybrid-scan-1.0.0
grid: fuzhou_118_123_25_27_0p01deg_v1
DEM: copernicus-dem-glo30-2022-v1
effective Earth radius: 4 / 3
blockage: circular_beam_partial_blockage
cumulative blockage: maximum_along_ray
maximum usable blockage: 0.70
polar mapping: nearest ray/gate, azimuth <= 0.75°, range <= 0.55 gate
Hybrid Scan: lowest usable elevation
```

波束中心高度使用等效地球半径模型，波束半径使用雷达配置中的垂直波束宽度；
地形与圆形波束的相交面积给出部分遮挡率，沿同一射线取累计最大值。网格点先
直接注册到每个候选仰角的最近极坐标库，再按 QC 有效态、拒绝 flags、源 QI、
累计遮挡率和相对地面波束高度选择最低可用仰角，没有进行会混淆缺测和无雨的
反射率插值。

DEM 运行时必须同时通过版本、清单、验收状态、文件大小、SHA-256 和 EPSG:4326
检查。实际用到的瓦片首次打开时校验 SHA-256，之后才进入有限 LRU 缓存；经
GSHHG 验证的源端纯海洋空槽位按海平面处理。

## 3. 产物和控制面

`RadarGrid` 契约升级为 v1.2，主要二维字段为：

- `DBZH`、`QUALITY_INDEX`、`QI_BLOCKAGE`、`QI_BEAM_HEIGHT`；
- `QC_FLAGS`、`VALID_MASK`、`LOW_QUALITY_MASK`；
- `SOURCE_SWEEP`、`BEAM_HEIGHT`、`TERRAIN_HEIGHT`、`BLOCKAGE_RATE`、
  `DATA_AGE`。

每个有效 DBZH 仰角还保存 `azimuth × range` 的 `PARTIAL_BLOCKAGE`、
`BLOCKAGE_RATE`、`BEAM_HEIGHT`、`TERRAIN_HEIGHT` 和 `SUPPORT_MASK`。速度专用
仰角没有伪造 DBZH，会在摘要中明确记录为跳过。

真实 worker 消费 `rainpulse.jobs.requested.radar_grid`，输出按算法版本隔离：

```text
s3://rainpulse/radar/grid/{radar_id}/{scan_id}/{hybrid_scan_version}/grid.zarr
```

任务、配置、outbox、完成 inbox、资产和网格指标在同一控制面闭环；完成事件
重放不会增加第二条指标记录。新增接口：

```text
GET /api/v1/radar-scans/{scan_id}/grid-summary
```

## 4. 测试服务器真实回放结果

运行版本：`rp009-v1.1-bf6c6bf-20260825`

```text
radar_id: z9598
scan_id: d8d89100-3558-5651-8428-1991170ad888
run_id: ba8dfa4c-67c1-50a7-bd47-a9e8a42b2b36
job_id: 0ffd80d7-d6a3-5b60-90aa-c1aa19d253c8
input QC: rp008-basic-1.0.4
output: s3://rainpulse/radar/grid/z9598/d8d89100-3558-5651-8428-1991170ad888/hybrid-scan-1.0.0/grid.zarr
status: RADAR_GRID_READY
```

业务网格结果：

| 指标 | 结果 |
|---|---:|
| 总格点 | 100,701 (`201 × 501`) |
| 有效格点 | 3,363 |
| 缺测格点 | 97,338 |
| 有效覆盖率 | 0.033395894 |
| 低质量有效格点 | 3,112 |
| 平均 QI | 0.31420428 |
| 因严重波束遮挡而缺测 | 0 |
| `sweep_000` / 约 0.5° 贡献 | 2,465 |
| `sweep_002` / 约 1.5° 贡献 | 873 |
| `sweep_004` / 约 2.4° 贡献 | 25 |

Z9598 位于业务网格西侧，福州网格大部分区域超出这一单站的有效 DBZH 覆盖，
因此 3.34% 覆盖率是当前站位、网格范围和实际回波共同作用的结果，不能填充成
无雨。`sweep_001` 和 `sweep_003` 是速度专用切层，因没有有限有效 DBZH 被明确
标记为 `no_finite_valid_dbzh`。

输出 Zarr 共 951 个对象、2,039,748 字节。九个 DBZH 仰角均产生了极坐标 DEM
诊断；实际支持路径采样到的地形约为 `-6.5–1752.55 m`。该体积和当前站点几何
下累计遮挡率最大值为 0，因此没有遮挡触发的抬升或缺测。单元测试另用合成山脊
证明 0.5° 严重遮挡时会改选 1.5°，同时验证累计遮挡单调性。真实结果为零不能
替代代表性山地遮挡案例的业务验收。

## 5. 业务发布门槛

该真实产物明确记录：

```text
vertical_datum_status: unverified_engineering
operational_eligible: false
operational_reasons:
  - radar_config_not_ready
  - vertical_datum_unverified
  - qc_static_ground_clutter_not_applied
  - qc_sea_ap_not_applied
```

Z9598 配置仍为 `draft`，天线高度基准尚未证实与 EGM2008 DEM 一致；静态地物
杂波和海杂波/AP 模块也还没有业务资产。因此本次只证明数据、算法和系统链路，
不可进入正式 QPE 或短临发布。

## 6. 验证命令

```bash
make test-radar-grid
make test
make lint
make build-linux
make build-worker-linux
make deploy-up
make infrastructure-smoke
make control-plane-smoke
make worker-smoke
make radar-grid-smoke
make smoke
```

本地全量测试、Go vet、Ruff、ESLint 和生成契约检查通过；测试服务器上基础设施、控制
面、Worker 成功/失败/重放、真实 RP-009 和 API/Web smoke 均通过。Worker 镜像
补入 Rasterio 需要的 `libexpat1`，同时使用延迟导入隔离非网格 worker 的原生
栅格依赖，四类 worker 均保持 healthy。

## 7. 下一目标

工程主线进入 RP-010：冻结分析时次和多雷达时间对齐规则，使用多个单站
`RadarGrid` 按 QI 选择/线性 Z 加权，生成带来源雷达、质量和覆盖字段的
`RadarAnalysis`。开始真实多雷达验收前仍需补齐其他雷达的 ready 配置和代表性
同期体积；Z9598 垂直基准、晴空静态杂波图、海杂波/AP 概率和代表性山地遮挡
案例继续作为业务发布门槛。
