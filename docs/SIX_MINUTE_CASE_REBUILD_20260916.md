# 2026-08-28 案例的 6 分钟重建（105 实测记录）

状态：**已完成并通过验证**（旧 5 分钟数据待清理）。

## 目标

工作台时间轴已改为逐 6 分钟，但 105 上的 08-28 案例仍是迁移前的 5 分钟派生结果。
本记录说明如何把该案例按 6 分钟节奏重建，以及过程中修复的部署与实现缺陷。

## 重建结果（2026-09-16）

- 452 个体扫全部按 v2 配置重新格点；归位为 **106 个 6 分钟时次**（104 个四站齐全）。
- 106 个 6 分钟拼图分析（`qc-opensource-mosaic-v1-6m180`），全部 6 分钟对齐、ANALYSIS_READY。
- 104 个预报运行 **PUBLISHED**，pySTEPS-LK 每个 30 个时效（+6…+180 分钟）。
- 312 条产品记录（104 时次 × 3 类产品）。
- 工作台核对（10:36Z 起报）：时间轴 41 档、步长 6 分钟；QPE 面板 −60…0 共 11 帧；
  LK 面板 30 帧（+6…+180）；`lk:accumulation_60` 2 帧、`lk:accumulation_120` 1 帧。
- 按需累计复测：观测 −30…0、−60…0 与跨起报 −30…+30 均为 ready（mm）。

## 过程中发现并修复的问题

1. **worker 配置漂移**：worker 容器最近一次重启只用了基础 compose 栈（rp043 / `qc-flags-v1`），
   而 control.env 与已落库的质控产物是 qc-opensource（`qc-flags-v2`）。服务发出的格点请求被拒绝：
   `requested hybrid_scan_version differs from mounted configuration`。
   新增 `deploy/docker-compose.sixmin-worker-configs.yaml` 对齐五个链路 worker 的配置（镜像仍用
   `rainpulse-cpu-worker:latest`）后重建容器。
2. **旧代次格点产物**：00:06–00:54 的 30 个体扫，其格点产物是 `qc-opensource-1.0.0/2.0.0/6.1.0`
   时代生成的；worker 因发现已有完成标记而跳过重算，导致拼图拒绝混用代次
   （`mosaic cannot mix different open-source QC generations`）。清掉这些旧产物与 job 后重跑。
3. **JetStream 10 分钟去重窗口**：job 事件的 `Nats-Msg-Id` 是稳定 ID，10 分钟内重发会被静默丢弃
   （`Duplicates: 10m`）。重发请求需等过窗口。
4. **失败分析不能完成拼图**：拼图完成事件被状态机拒绝（`analysis status "FAILED" cannot complete mosaic`）。
   修正为 `MOSAIC_RUNNING` 后重发。
5. **pySTEPS-LK 注册表**：`model_versions('pysteps-lk','pysteps-lk-2.0.0')` 仍指向旧 profile
   `prelaunch-pysteps-lk-v2`。按"新配置使用 -6m180 标识"的口径登记
   `prelaunch-pysteps-lk-v2-6m180`（config_versions + model_versions，均已备份）。
6. **RP-015 产品套件常量漏改（代码缺陷）**：`validateApplicationProductManifest` 仍按 79/84 条唯一
   路径校验，而 6 分钟套件是 30 时效 × 3 资产 + 1 点查索引 + 2 + 1 = **102** 条，导致产品完成事件
   一律被拒（`incomplete RP-015 product suite`）。已在
   `services/control/internal/postgres/product_store.go` 接受 102（保留 79/84 兼容），
   本地交叉编译统一二进制后替换部署（旧二进制备份为 `rainpulse.bak-20260916`）。

## 并行度

worker 使用共享 durable pull consumer，可安全多副本。105（24 核 / 156G）本次实际并行：
格点 16、LK 8、诊断 6、拼图 4、QPE 4、NowcastInput 4、产品 1（该服务映射了主机端口，不能多副本）。
格点阶段实测从约 2 个/分钟提升到约 13 个/分钟。

## 复现命令

```
# 1) 格点阶段（显式补齐 QC-only 体扫的格点，不动批次台账）
REBUILD_BINARY=<root>/.build/linux-amd64/rainpulse-orchestrator-6m \
  REBUILD_DATE_UTC=2026-08-28 scripts/rebuild_six_minute_scans.sh

# 2) 分析与预报链路（拼图→QPE→诊断→NowcastInput→LK→产品，可断点续跑）
REBUILD_BINARY=<root>/.build/linux-amd64/rainpulse-orchestrator-6m \
  REBUILD_DATE_UTC=2026-08-28 python3 scripts/rebuild_six_minute_case.py
```

两条命令都只走显式请求：不依赖历史回放窗口，不改写旧 5 分钟分析。

## 备份表

`jobs_backup_20260916_stale_grid`、`job_attempts_backup_20260916_stale_grid`、
`inbox_events_backup_20260916_stale_grid`、`outbox_events_backup_20260916_stale_grid`、
`jobs_backup_20260916_first_hour`、`jobs_backup_20260916_failed_mosaic`、
`jobs_backup_20260916_product`、`model_versions_backup_20260916_sixmin`、
`config_versions_backup_20260916_sixmin`。

## 旧 5 分钟数据清理（2026-09-16 已执行）

删除范围：08-28 上所有非 6 分钟配置的分析与运行，及其派生行与对象存储前缀。
原始体扫、归一化体扫、质控产物、格点产物与仅质控批次台账全部保留。

数据库（单事务，先备份后删除）：

| 表 | 删除行数 |
| --- | --- |
| analysis_cycles | 1563 |
| forecast_runs | 796 |
| products | 1311 |
| product_assets | 34718 |
| jobs（含 attempts/inbox/outbox） | 15155 |
| mosaic_runs / qpe_runs / diagnostic_runs | 1563 / 1468 / 1302 |
| analysis_cycle_radars | 6252 |
| nowcast_input_runs / model_runs / product_build_runs | 447 / 437 / 437 |
| algorithm_runs / nowcast_input_frames | 143 / 2563 |
| pipeline_regeneration_requests / frames / frame_scans | 521 / 2973 / 对应子行 |

- 有 248 个 job 因被 `qc_batch_items` 引用而保留（质控台账链接不可断）。
- `pipeline_regeneration_requests` 的 run 引用有 CHECK 约束不能置空，故按记录删除（已备份）。
- 备份表前缀 `legacy5m_20260916_`。

对象存储：并行删除 4905 个前缀（mosaic / QPE / 诊断 bundle / `products/<run_id>/`）。

清理后核对：08-28 仅剩 106 个 6 分钟分析、104 个运行、312 条产品；工作台目录只列出 6 分钟时次
（`minute%6=0`），时间轴 41 档，QPE 与 LK 面板、观测/预报累计全部 ready。
