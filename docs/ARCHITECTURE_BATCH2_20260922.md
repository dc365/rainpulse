# RainPulse 第二批：数据访问与资源优化

基线：`eb99368c07d9fce6bb41a1f2b1e46a5f005d845a`，2026-09-22 10:14:07 UTC。
该提交已合并第一批。本次是源码交付，不是 105 部署记录，不是气象质量或性能验收报告。

## 1. 改动范围

| 内容 | 实现 |
| --- | --- |
| 不可变资产缓存 | 每进程共享的已验证 bytes 缓存；字节容量、条目数、单对象大小、TTL、并发下载数均有上限；同一对象并发请求合并；失败不入缓存 |
| 按需读取 | 原 `.load()` 保留；新增固定清单会话与 `load_selected`；schema 1/2 可读指定字段/目录；旧 schema 3 packed 保持完整校验 |
| 实际使用点 | 集合降水区间计算只读雨强、成员有效掩码、坐标与时效；点值文件的已提交资产回退使用统一读取器；原 QC/上下文完整读取路径直接受益于 bytes 复用 |
| 资源通道 | 原主题/durable 为 realtime；CPU 历史重算、旧资料任务和非关键诊断/检验进入独立 background 主题与 durable；默认关闭 |
| 路由持久化 | 在原有 outbox 行首次发布前冻结通道；重试、CLI replay 使用原通道；已发布旧任务不自动搬队列 |
| 容器预算 | 从实际 Compose 配置派生回放副本，不猜测近站 profile；分别限制 CPU、内存、进程数、数值库线程、对象 I/O 并发和共享 consumer 的待确认数 |
| 第一批兼容 | 保留 release gate，扩展为检查两类 QC 池及全部 CPU 池；默认部署仍走 rainpulsectl |

未修改雷达解码、近站杂波算法、阈值、QI、质量标志、业务发布资格、网格、QPE、外推或预报步长。未替换 Zarr/MinIO/NATS/PostgreSQL，未新增网络缓存或通用工作流引擎。数值库线程上限变化仍须通过真实回放验证数值稳定性，不能把“未改算法源码”当成完成气象验收。

## 2. 缓存与按需读取边界

默认保留缓存容量为 **0**，避免未经测量给所有现有 Worker 增加内存；新资源预算启用后由每个池显式分配容量。即使关闭保留缓存，仍保留完整性校验与有界下载。同一配置应通过现有发布流程重启 Worker 生效，不支持任务途中热改。

| 环境参数 | 兼容默认 | 意义 |
| --- | --- | --- |
| `RAINPULSE_ASSET_CACHE_BYTES` | 0 | 最大保留数据字节；0 禁用跨任务保留 |
| `RAINPULSE_ASSET_CACHE_ENTRIES` | 4096 | 条目数上限，包含零字节对象 |
| `RAINPULSE_ASSET_CACHE_MAX_OBJECT_BYTES` | 16777216 | 超过此值只读取，不保留 |
| `RAINPULSE_ASSET_CACHE_TTL_SECONDS` | 600 | 绝对 TTL，不因命中无限续期 |
| `RAINPULSE_ASSET_READ_CONCURRENCY` | 4 | 每进程同时读取数据对象的上限 |
| `RAINPULSE_OBJECT_STORE_MAX_WORKERS` | 4 | 每个读取器/发布器的现有并发参数；新读取器要求 1–32 |
| `RAINPULSE_MAX_INPUT_ARTIFACT_BYTES` | 原有 2 GiB | 仍检查完整资产的声明大小，不因只读摘要绕过 |

缓存的是压缩/序列化对象 bytes，不是跨任务 NumPy 数组，也不是新几何 LUT 缓存。缓存容量不等于 Worker RSS 上限：算法数组、中间结果、待发布结果、正在读取的大对象仍会占内存。容器内存限额必须依据真实峰值加余量设置，不能简单等于缓存容量。

清单每次都读取。schema 1/2 按完整清单复算组合哈希，再逐个验证选中对象；不下载不需要的数组。schema 3 为兼容旧 packed 资产，仍下载全部包并校验全资产，只减少不必要的解包复制。没有声称所有质控/格点路径已实现懒加载或按时效块读取。

明确的内部契约见 `contracts/internal/asset-access-v1.md`。完成消息仍使用第一批固定摘要。

## 3. 队列与路由规则

Go：`RAINPULSE_RESOURCE_LANES_ENABLED=false` 为默认。启用前先部署新 Worker、准备数据库列并完成资源校验。

Python：`RAINPULSE_WORKER_LANE=realtime|background`。realtime 使用原 subject 和 durable，background 使用：

```text
rainpulse.jobs.requested.background.<原阶段后缀>
<原 durable>-background
```

任务载荷、任务 ID、事件 ID、输入引用、输出路径不改变，结果仍走原有完成/失败通道。启用后，明确重算请求、分析诊断、预报检验，以及资料在**任务创建时**已超过 `RAINPULSE_RESOURCE_REALTIME_MAX_AGE` 的任务进入 background；默认分类阈值 1 小时，只影响资源通道，不是气象质量门槛。来源时间不明或明显在未来的任务走 background，不凭此放宽或更改算法校验。

判断使用数据库已有扫描结束时间/分析时次/预报起报时间和任务创建时间，不用每次重试的当前时间。原有 outbox 增加两个字段冻结 realtime 和 background 两种决定，避免重试时因配置变化切换消费者。旧的已发布事件保持原主题。未识别的 GPU/离线专用/模拟主题不改动。

本批隔离 CPU Worker 队列、容器配额与线程/I/O 并发；**不保证**共享磁盘、MinIO、NATS 单流容量或 Go 调度器在无限回灌下具有严格实时 SLA。第一批仍收集完整有限窗口；本批未改为每 tick 固定预算调度，也未实现任务抢占。不可无限扩大历史窗口。训练、独立 GPU 服务及不属于登记项目的外部进程不在该预算内。

## 4. 上线准备：先验证，不自动猜硬件

在完整仓库应用交付包后：

```bash
make test-architecture-batch2
# 原有 Worker、QC、格点、拼图、QPE、预报与契约回归继续执行。
```

先按照现有方式构建/加载新 Go 二进制和 CPU 镜像。不要只复制 Python 新文件而漏掉现有文件补丁。当前 Docker Compose 必须支持 `config --format json --no-interpolate --no-env-resolution`；不支持时工具拒绝生成，不能改为导出包含展开凭据的配置。

第一批只是合并、尚未部署时，必须先按第一批文档建立真实 release gate 和启动身份，不能用新 CLI 宣称已暂停旧程序。

从已登记的当前部署生成预算模板：

```bash
python3 scripts/rainpulsectl.py resources-template \
  --output runtime/deploy/resource-budget.json
```

未知硬件/现有配额用 0 表示，**0 会阻止激活**。编辑模板填入实际分配给 Worker 的 CPU/内存总预算，以及每个 CPU 阶段 realtime/background 的副本数、单副本 CPU/内存。总预算应在扣除 Go、数据库、MinIO、NATS、操作系统、GPU/训练等资源后确定；不要直接填整机全部内存。

预算契约：`contracts/schemas/resource-budget-v1.schema.json`。工具额外验证副本乘以配额后的总和、数值库线程不超过该容器 CPU 预算、缓存不超过容器内存的八分之一、consumer 的待确认上限覆盖共享该 consumer 的所有副本。模板保留既有 realtime 副本数，不将四个 QC 副本悄悄改为一个。

```bash
python3 scripts/rainpulsectl.py resources-plan \
  --budget runtime/deploy/resource-budget.json \
  --output runtime/deploy/resource-target.json
```

默认沿用实际登记的基础、realtime-shadow、unified 和近站/实验覆盖顺序。切换目标镜像/profile 时使用新不可变覆盖文件，通过重复 `--override` 指定完整目标覆盖顺序。不要原地编辑正在使用的旧冻结文件。生成的 `.compose.json` 是派生产物，不要手改。

此命令只写计划，不启动容器、不改 BDP、不启用资源路由。background 从所选实时服务复制算法参数、镜像和挂载，移除重复宿主机端口/网络别名；生成前拒绝源配置中可检测的明文凭据，生成后再调用真实 Compose 检查展开后的两类服务身份是否一致。

## 5. 受控启用顺序

1. 用现有接入控制暂停新的自动入库/任务创建，使旧任务可排空；保留结果消费者及旧 Worker。资源切换涉及全部 CPU 池，不能只看 QC 队列为空。
2. 冻结目标 QC 发布计划，并显式绑定上一步资源计划。使用实际当前运行模式，不借此提升发布资格：

```bash
python3 scripts/rainpulsectl.py release-plan \
  --qc-config /实际目标/near-qc.yaml \
  --qc-flags /实际目标/qc-flags.yaml \
  --image <已加载的新CPU镜像> \
  --execution-mode realtime_shadow \
  --resource-plan runtime/deploy/resource-target.json \
  --output runtime/deploy/qc-resource-release.json
python3 scripts/rainpulsectl.py release-pause --plan runtime/deploy/qc-resource-release.json
python3 scripts/rainpulsectl.py release-drained
python3 scripts/rainpulsectl.py resources-schema
python3 scripts/rainpulsectl.py resources-register \
  --plan runtime/deploy/resource-target.json \
  --release-plan runtime/deploy/qc-resource-release.json
```

`resources-schema` 是显式的加列准备：不删除数据，短锁超时会失败而非无限阻塞。`resources-register` 要求拥有暂停标记、没有未物化重算意图、数据库全部计算任务和请求 outbox 排空，以及当前 CPU consumers 没有 pending/ack-pending。接入未停会导致拒绝，应停止新增工作后重试，而不是绕过检查。

3. 登记后用原入口更新容器，并按已有构建/安装流程更新 native Go。先保持 `RAINPULSE_RESOURCE_LANES_ENABLED=false`，核对新 Worker 的镜像、线程、缓存和实际 Docker 配额：

```bash
python3 scripts/rainpulsectl.py up
# 用项目既有方式更新并重启 native rainpulse.service，保留 gate 和所有业务参数。
python3 scripts/rainpulsectl.py resources-check --expect-routing disabled
```

Worker 启动时检查既有 durable 的 pending 上限。若需要改变，只允许在该 consumer 排空时更新可变限额；不删除/recreate durable，不重置确认位置。未排空则拒绝启动，先用旧配置排空。

4. 在真实 BDP/服务环境中显式设置 `RAINPULSE_RESOURCE_LANES_ENABLED=true`，重启 native Go。Python 背景池的环境来自生成的 Compose，不能仅修改 `.env` 就假定 Go 已读取。

```bash
python3 scripts/rainpulsectl.py resources-check --expect-routing enabled
python3 scripts/rainpulsectl.py release-check --plan runtime/deploy/qc-resource-release.json
python3 scripts/rainpulsectl.py release-resume --plan runtime/deploy/qc-resource-release.json
```

恢复接入并观察。release-resume 会核对所有 realtime/background QC 副本的配置/代码/镜像身份，且要求整个 CPU 池预算与实际容器及 consumer 匹配；任一步失败都保留暂停标记。不支持只打开 Go 路由开关而没有相应 background Worker。

后续更换 QC 镜像/profile，需要重新派生资源计划：两类池必须一起切换，不能单改实时池。已登记资源计划时，旧 `set-overrides` 会拒绝，避免背景池仍运行上一个近站版本。

## 6. 回退

源码回退：使用交付包 `apply.py --rollback`，会保护后续人工编辑。源码回退不等于运行回退。

运行回退必须先暂停新增工作并排空两类队列，再用上一份已验证镜像和配置更新全部池。保留 `resource_route_frozen` 和 `resource_route_reason` 列，不能为回退删除数据库历史。不要把已冻结 background 任务直接改写到 realtime 重放。

可先关闭 Go 的**新任务分类**开关，但已经发布/已冻结的 background 任务仍需要原 background consumers 完成；原主题的幂等事件并不能授权把任务搬到另一队列。若完全退回第一批程序，应在受控窗口证明所有背景任务/consumer 排空，恢复旧 active-compose 清单、原副本数、旧 Go/镜像，再按照第一批发布校验恢复。该跨版本运维回退不自动执行。

## 7. 验收与已知限制

交付包 `validation/` 记录本地实跑日志和环境。标准库核心 tests 直接加载生产缓存/读取/资源模块，不假装安装了 MinIO、NATS 或 Zarr。部署测试使用明确的 fake Docker/SQL 响应，不冒充容器验收。

包含完整 SDK/Zarr 区间计算一致性测试；缺少 SDK 时明确 SKIP。数据库集成测试 `TestBatch2Postgres*` 使用隔离连接上的临时表，缺少 `RAINPULSE_TEST_DATABASE_URL` 时明确 SKIP。当前交付环境未运行完整仓库编译、真实 PostgreSQL/NATS/Docker、真实雷达回放、Ruff、105 压测。不能从新增单测推断所有原有测试已通过。

目标环境至少验证：冷/热缓存的 SHA 与输出一致、缺测掩码不变、schema-3 回退、>200 历史积压与实时并行、两队列不互取任务、重试/服务重启仍使用原路由、镜像或配置漂移拒绝恢复。固定测试输入和模式；比较下载字节数/对象 GET 数、核心耗时、端到端关键路径 P50/P95、峰值 RSS 和当前时次延迟。测量后才决定提高缓存或副本数，不提供虚构提升百分比。

合成 I/O 示例命令：

```bash
python3 tests/architecture_batch2/io_probe.py
```

它只证明机械性的对象读取减少及字节一致性，不是网络/磁盘/天气系统测速。共享存储瓶颈、原始资产保留/清理、几何资源缓存、完全懒加载以及预算式调度仍需后续数据决定，不在本批伪装为已完成。
