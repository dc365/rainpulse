# RainPulse 运行管理后台：首个可用流程

基线：`bd645c381d7321b14d51672cb8e254ba05c6cdb2`，2026-09-22 14:11:14 UTC。
范围：任务中心、关联日志、冻结预检查、失败项恢复、候选图件预览。
这是源码实现与部署交接，**不是105部署记录、全仓库验收或气象质量报告**。

## 1. 用户可以完成什么

`/admin` 切换到独立 `apps/web/src/admin`；不再使用旧 AdminWorkspace 长页。
天气主工作台和 `/qc-review` 不变。后台只有四个有实际能力的入口：任务中心、新建重算、执行资源、系统日志，没有先铺七个空菜单。

任务中心区分管理作业和原自动链路任务。支持筛选、搜索、分页、作业依赖、单任务/执行尝试、错误、日志、输入/配置、耗时、候选图件。失败不能只归成一个红色状态：直接失败、上游受阻、部分成功、排队、正在提交、失联提示分别显示。

新建重算支持：

| 预设 | 输入与结果 | 不会做的事情 |
| --- | --- | --- |
| 单站 QC 与对照图 | 真实已解码体扫 → 原 QC 算法 → 独立原始/QC/QI 图 | 不要求已有 QPE/预报，不覆盖当前业务 QC 指针 |
| 仅重建对照图 | 已登记 QC 资产 → 图件 | 不重新执行 QC |
| 重建区域诊断 | 原 `analysis.diagnostics` 任务冻结请求，匹配当前配置 → 独立诊断包 | 不将任意任务当作可安全重试对象 |

原自动链路任务可查询其原始状态、尝试、错误、消息与日志入口；首版不直接重置/删除其终态 inbox，也不修改已发布数值产品。旧任务只有明确支持的诊断类型可转成新预检查计划。旧任务的 `RUNNING` 仍标明“旧派发口径”，没有伪造真实历史开始时间。

首版的“可靠重算”指上表三个预设的完整候选闭环，不是任意算法/预报全链路重算，也不包含默认产品切换、用户团队管理、自动数据补采和全机性能大屏。

## 2. 保持简单的实现

仍为一个 native Go 主程序、既有 PostgreSQL/NATS/MinIO，以及按需启用的长期 Python 管理 Worker。没有 Kubernetes、通用工作流引擎、Redis、每任务子进程或额外业务数据库。

- `operations`：标准库实现的作业/尝试状态与管理 HTTP。
- `controlplane.OperationsBuilder`：复用当前 QC 上下文选择器，只读取，不调用旧 Create* 改写自动链路。
- `apiapp.operations`：通过 pgx 的 `OpenDBFromPool` 复用现有连接池。不是另开一套连接预算。
- `ops_*` 表：独立候选作业的唯一状态来源；原 jobs/产品表继续是自动链路来源。
- 复用 `outbox_events`，`aggregate_type=ops_task`。派发完成只写 `ops_tasks.dispatched_at`，**不进入旧 jobs/RadarScan 状态转换**。按 generation 防止旧消息的迟到派发回执改写新尝试时钟。
- 复用 NATS `RAINPULSE_JOBS`；管理引用使用精确主题 `rainpulse.jobs.requested.ops.qc/render/diagnostics`，不与自动/旧后台池竞争。
- 管理 Worker 使用 HTTP 回执领取/心跳/登记。大数组仍只读写对象存储。
- 原算法配置、QC 数据语义、QI、极坐标/DEM逻辑和业务资格字段保持不变。

候选使用 `s3://rainpulse/operations/{run}/{task}/attempts/{attempt}/...`。每次尝试独立目录，候选生成成功不等于默认发布、不等于获得业务资格。

## 3. 计划、状态和失败恢复

### 预检查与提交

范围为含开始、不含结束，最多24小时、16站、64个实际体扫、128个阶段任务；区域诊断最多32个源任务。超过上限明确拒绝，不静默漏算。

目录来源为 radar_scans/radar_scan_runs，不从成功产品反推数据日期；缺某个选中站的数据会 BLOCK。连续到报率不按六分钟产品步长猜测。

预检查冻结输入 URI、完成标记 SHA256、资产总摘要与长度；冻结配置/标志表/Worker代码身份及现有时间上下文策略。它核对完成标记和元数据，**不等于已读完所有数组或完成气象质量验证**。实际执行时原读取器继续逐对象完整校验；冻结标记变化或缺对象会 BLOCK。

计划15分钟有效。提交复核有效配置、输入标记及匹配 Worker，不悄悄替换输入。计划使用规范化嵌套 JSON 摘要，避免 PostgreSQL jsonb 改变键序导致正常提交失败。计划 ID + 幂等键 + 数据库锁使重复点击和响应丢失返回同一作业。页面保存待提交计划 ID，关页重开可以读取。

### 执行状态

`WAITING → QUEUED → RUNNING → COMMITTING → SUCCEEDED`，并有 FAILED/BLOCKED/CANCELLED。真实开始时间来自 Worker CAS 领取，不来自 NATS 派发。只有 VERIFY_INPUT/COMPUTE/UPLOAD/COMMIT 等有真实证据的阶段，没有虚构百分比。

心跳每10秒，执行租约120秒，Worker登记新鲜度75秒。失联作为 STALLED 提示，**不自动重新启动一个昂贵算法实例**。确认结果归属使用当前尝试 ID 和只存哈希的随机执行令牌。

暂停停止新领取，当前任务允许完成；取消先阻止新任务，活动任务在安全检查点退出。算法核心未加入任意中断代码，因此一次长算法内部可能直到返回才响应取消。超时停止容器可能中断计算，不可将“发出了取消请求”显示成“进程已经停止”。

### 四种恢复动作

1. **仅重试失败项**：同输入/同配置；成功任务复用，仅失败/受阻任务进入新 generation/attempt。旧回执无法覆盖新尝试。
2. **恢复结果登记**：读取该尝试已经原子提交的完成标记，校验确切URI、job/run/trace、摘要和长度，补登记；不重新计算算法。这里是提交记录校验，不是全数组深度复验；候选转业务前仍需完整验收。
3. **撤销失联执行权**：人工先停止原Worker；租约已过期且原Worker无新鲜登记；找不到可登记完成标记时才允许。不是网页 kill/任意终端。随后另外点击失败项重试。
4. **重新投递排队引用**：明确修复消息保留期届满等情况，只投递仍未领取的任务，不创建第二个并发执行实例。

UPLOAD/COMMIT 网络故障可能已成功写标记，不能立刻判为算法失败并重算。这种不确定情况保留可恢复尝试；重投递可触发登记修复，或由操作员点“恢复结果登记”。没有标记时再按第3步受控处理。

未知数据不会改成无雨；取消不解锁下游；修改配置不自动复用旧计划；结果不注册到默认产品目录。

## 4. 日志和性能的真实口径

管理任务日志直接由 Worker 捕获算法标准输出/错误和生命周期，关联 task_id/attempt_id，含堆栈与错误分类。稳定递增序号使重复回执不产生重复日志。每批20条，每条4096字符，每尝试2000条；达到上限有显式记录。浏览器保留最近2000条已加载记录，导出和过滤只针对该范围。

这不是全机日志采集系统。`RAINPULSE_OPS_LOKI_URL` 是可选只读 Loki 入口，固定标签 `{app="rainpulse"}` 和精确 job_id，最多六小时、200条、4MiB。未配置时明确 not_configured。原日志采集器仍需在本机独立接入 journald/容器；本交付未安装/启动 Loki 或 Alloy，不声称既有历史日志已采集。

任务尝试记录领取、心跳、结束、当前阶段。新计算路径记录计算墙钟、提交墙钟和原QC提供的阶段指标。当前进程RSS、任务窗口采样峰值、进程生命周期 `ru_maxrss` 分开命名；共享进程采样不冒充任务独占峰值。旧记录没有指标时显示未记录，不补零。不提供未经现场测量的加速百分比。

## 5. 首次安装：先在独立测试环境

### 5.1 应用和编译

```bash
python3 /path/to/rainpulse-admin-v1-bd645c3/apply.py --repo /path/to/rainpulse --check
python3 /path/to/rainpulse-admin-v1-bd645c3/apply.py --repo /path/to/rainpulse --apply
cd /path/to/rainpulse
make test-admin-ops
```

这是源码包，不带运行二进制、镜像、权重或真实雷达。使用既有构建流程生成新的 Go/Web/CPU Worker 镜像，保持当前近站 profile 和覆盖文件。不得用旧镜像直接启管理 Worker：它不包含新 Python 模块。

### 5.2 显式初始化管理表

使用本机安全配置连接测试库：

```bash
psql -X -v ON_ERROR_STOP=1 -f services/control/internal/operations/schema.sql
```

PGHOST/PGDATABASE/PGUSER/.pgpass 或本机已有连接机制提供凭据；不把密码写到仓库/文档/终端命令。此脚本新增7个 ops_* 表和索引，不重写/删除旧任务。它使用事务和部署锁，可重复执行；IF NOT EXISTS 不是任意旧表定义的修复工具。尚无本版表时 `/status` 明确 database_ready=false。

### 5.3 主程序和 Worker 必须一致配置

Go 的真实运行环境（systemd control.env 或既有 BDP 配置）设置：

| 变量 | 内容 |
| --- | --- |
| `RAINPULSE_ADMIN_TOKEN` | 复用现有管理凭据；浏览器使用这个 Token |
| `RAINPULSE_OPS_WORKER_TOKEN` | **独立随机 Worker 凭据**，Go 与管理容器相同；不要用浏览器管理Token代替 |
| `RAINPULSE_OPS_QC_CONFIG` | 本机当前采用的 QC YAML；未设置时从 PIPELINE_QC_CONFIG 获取 |
| `RAINPULSE_OPS_FLAG_DEFINITIONS` | 本机同版本 flags 文件；未设置时从 QC_FLAG_DEFINITIONS 获取 |
| `RAINPULSE_OPS_DIAGNOSTIC_CONFIG` | 区域诊断重建使用的 YAML（可先不启这个预设） |
| `RAINPULSE_OPS_LOKI_URL` | 可选内部Loki地址，不配置不影响任务日志 |

注意 Go 路径是宿主机可读路径；容器路径可以不同，但原始文件字节摘要必须一致。主要配置目录、上下文配置目录、阶段文件和算法代码会进入 Worker 指纹，换文件需要新预检查。

管理 Worker 的真实 `.env` 同时配置：
`RAINPULSE_OPS_WORKER_TOKEN`、`RAINPULSE_OPS_CONTROL_URL`、`RAINPULSE_OPS_NATS_URL`，以及原 MinIO Worker 访问凭据。

例如控制面URL可为 `http://host.docker.internal:4173`（仅示例；以实际native地址为准）；NATS 使用同项目实际地址。该URL只填协议、主机和端口，Worker自动附加 `/internal/ops/v1`；不要重复填写路径。Worker Token 不进入URL。统一 webgateway 已加入精确的新管理/Worker路径转发，**不会替调用方注入管理员Token**。

修改后的 `deploy/minio/worker-policy.json` 增加 operations/* 读写范围。用现有运维流程刷新 policy，并确认 **Go读取凭据**同样可读 operations/*；否则图件无法预览。没有新增删除原始/业务对象的权限。

### 5.4 派生管理 Worker，保留实际近站配置

先保证第一批登记的 `runtime/deploy/active-compose.json` 正确，记录的是实际覆盖顺序而非仓库默认值。

```bash
# 以下预算仅演示参数格式，不是对105的资源测量结论。
# 正式数值需先测现有单体扫峰值及本机剩余资源。
python3 scripts/configure_admin_ops.py \
  --extra-cpus 2 --extra-memory-mib 12288 \
  --qc-cpus 1 --qc-memory-mib 8192 \
  --render-cpus 1 --render-memory-mib 4096
python3 scripts/admin_opsctl.py check
python3 scripts/admin_opsctl.py up
python3 scripts/admin_opsctl.py status
```

生成器从现行QC/诊断服务派生镜像、挂载和算法配置，不写凭据到生成文件；仅用ENV引用。新文件默认 `runtime/deploy/ops-workers.compose.json`，已有文件会拒绝覆盖。生成器只验证声明配额，不测量机器空闲资源。每种默认一个实例，CPU/内存硬上限、单数学线程，不复制旧端口、特权或Docker socket。

`admin_opsctl up` 固定只启动 `ops-*` 三类服务，使用既有覆盖顺序加管理覆盖，`--no-deps --no-build --pull never`，不会启动旧Go容器或替换实时Worker。它**不自动把文件写回 active-compose.json**；因此正常运维清单仍需明确记录管理池是通过此辅助入口运行的。不要用含孤儿删除选项的旧全栈命令清理这些管理容器。整个部署使用同一Compose项目和已有基础设施。

仅增加 `diagnostics` 时可 `--kinds diagnostics --diagnostics-cpus ... --diagnostics-memory-mib ...` 生成单独覆盖文件并用 `--override` 操作。qc/render最先开启足以完成独立单站闭环。

### 5.5 发布维护约定

既有 releaseguard 会暂停新管理变更和领取，日志/心跳/结果登记保持可用。但第一批旧发布脚本的队列检查**不自动包括本批ops池**。修改共用配置、镜像或执行代码前：在新后台暂停所有活动管理作业，确认无 RUNNING/COMMITTING，再按既有发布流程切换。已有冻结计划不会随之升级，需重新预检查。

## 6. 必须完成的端到端验收

以下是交付给目标环境执行的验收，不是本机已执行记录。

| 场景 | 通过条件 |
| --- | --- |
| 真实正常单站重算 | 预检匹配输入/配置/Worker，QC与对照图成功；图件清单绑定候选URI和摘要；业务默认结果不变化 |
| 原始记录存在但无解码资产 | 预检查明确BLOCK，不产生算法任务 |
| 配置或完成标记在预检后变化 | 提交拒绝；不暗中换输入/配置 |
| 重复提交/刷新重试 | 返回同一run_id；根任务只有一组；实际计算没有重复 |
| 一个体扫/图件失败 | 展示PARTIAL_SUCCESS或依赖BLOCKED；重试只重新执行失败部分，成功attempt ID不变 |
| HTTP登记中断、资产已提交 | 恢复登记后成功，不再调用数值算法；幂等重复恢复不重复解锁子任务 |
| 长计算取消 | 先CANCELLING；安全检查点结束后CANCELLED；不继续派发依赖 |
| Worker被停止/重启 | 显示租约失联，先尝试标记登记；无标记且确认停止后才撤销旧执行权 |
| 旧尝试迟到 | 不能heartbeat/finish新尝试、不能覆盖新候选、不能解除下游阻塞 |
| 实时与管理同时运行 | 管理池资源配额正确；观察实时主链路延迟，出现挤占则减少管理并发/配额 |
| 管理Token与WorkerToken混用 | 被拒绝；无秘密写入页面URL、计划或日志 |
| 缺Loki/断日志服务 | 任务日志仍可用；系统日志显示未配置/错误而非正常空结果 |

数据库事务测试使用独立临时schema，明确只对一次性测试库：

```bash
export RAINPULSE_OPS_ALLOW_INTEGRATION=1
# 在本机安全配置提供 RAINPULSE_OPS_TEST_DATABASE_URL，不使用生产库。
GOWORK=off go -C services/control test -tags=integration ./internal/operations -run TestOpsPostgres -count=1 -v
```

## 7. 回退和旧代码处理

原 `/admin` 页面入口已切换到新实现，旧 AdminWorkspace/AdminRoute/PipelineInspector 不再从该入口加载。没有盲删共享 workspace 代码、QC审阅页面或旧API，避免破坏仍被主工作台/脚本引用的模块。本轮不是兼容所有旧后台功能。

源码回退通过包内 `apply.py --rollback`；它先核对应用后文件摘要，存在用户后续修改时拒绝擦除。**源码回退不是运行环境回退**：先取消或排空管理作业并停止ops容器，确认 `aggregate_type=ops_task` 的 outbox 无待派发项，再退回旧二进制，否则旧 dispatcher 不认识 ops_task。保留 ops表和候选资产供审计，不自动drop或删除。

新表、MinIO policy 和生成的运行配置均由操作员显式管理，源码安装器不会连接数据库、停止容器、修改凭据或删除历史数据。
