# RainPulse 第一批架构优化：实现与验收

基线：`3d84719eb73750d3fa2fa0dd6541eded606c7d52`，2026-09-22 06:22:57 UTC。
交付范围：有界调度查询、固定 QC 完成摘要、QC 版本协调切换、统一默认容器运维入口。
这是源码交付，不是 105 部署记录，也不是性能或气象质量验收报告。

## 1. 本次改变与保持不变的边界

| 位置 | 本次实现 |
| --- | --- |
| `internal/planning` | 明确时间窗口、雷达/网格/状态过滤、200 条稳定游标分页、有限缓存淘汰 |
| `internal/postgres/planner_queries.go` | 调度专用 SQL；不修改已有列表 API 的排序和游标契约 |
| `internal/controlplane/planner_reads.go` | 实时窗口与显式回放窗口取并集；分析/预报不再只看第一页；重算按输入时段取数 |
| `radar/completion_summary.py` | 不依赖 QC 算法版本的固定摘要；64 KiB 总上限和嵌套诊断限额 |
| `internal/releaseguard` | 同机共享锁与持久暂停标记；暂停新调度/API/CLI 请求，保留完成事件处理 |
| `worker/release_identity.py` | Worker 启动配置、标志表和运行时代码摘要，检测启动后的文件变化 |
| `scripts/rainpulsectl.py` | 记录实际 Compose 覆盖顺序与副本数；冻结、暂停、排空检查、校验恢复 |

不修改雷达解码、近站杂波检测、阈值、源域测量、QC 标志、QI、DEM、格点/拼图/QPE、预报算法或 `operational_eligible`。不替换 NATS/PostgreSQL/MinIO，不引入工作流平台。底层幂等事务与资产原子发布保持原状。

### 调度读取的精确语义

状态、时间、雷达/网格和 QC-only 排除条件在 SQL 的 LIMIT **之前**执行。每页至多 200 条，按不可变时间与唯一工作流 ID 排序，下一页携带值游标，不通过可变状态记录反查游标。不再使用 OFFSET；处理中前一页状态变化不会导致偏移跳过。

自动 QC/Grid 路径继续排除所有 QC-only 批次扫描，不只排除活动批次。拼图仍允许已明确生成格点的历史输入。分析查询继续排除重算请求拥有的产品。显式重算仍能使用 NORMALIZED/QC_READY/GRID_READY/带可复用归一化输入的 FAILED 扫描。

实时与显式历史窗口是并集，不扩大到两个窗口之间的所有日期。`RAINPULSE_PIPELINE_LOOKBACK=0` 现在拒绝启动：过去的无界读取必须改为正值加明确的历史起止时间。检验保留至少六小时回看；手动预报重算保留原有时效豁免，不将该豁免误加到检验任务。

一次遍历固定窗口，每个数据库页有 10 秒查询超时，遍历可取消。**本批不是每个 tick 固定最多 200 条任务**：会继续翻页，避免积压遗漏；为不把某时次的多站拼图输入截断，当前仍先收集完整的有限窗口。显式超大回放窗口和大量历史手动重算仍可能较重，下一批再评估预算式调度，不能把本批描述为恒定内存或完整实时优先队列。

10 组 `planned*` 缓存改为保存加入时间，两小时淘汰、每组上限 8192，入口和退出均清理。它们仍只是加速缓存；数据库任务身份、事务与已提交资产负责正确性。缓存淘汰不是覆盖资产的许可。

### QC 完成摘要

完整诊断仍保存在已校验的 QC 资产中，消息仅引用 `qc/summary.json`。新契约 `contracts/schemas/qc-completion-summary-v1.schema.json` 与算法版本独立。保留身份、门数、健康状态、质量统计和原始资格布尔值，也保留旧 Go 消费端需要的杂波计数、模块状态和测量时间。

嵌套可选诊断限制为深度 4、256 个节点、每个映射 64 项、序列 32 项、字符串 256 字符及每块 8 KiB。过大时整块移出消息，在 `summary_omitted_fields` 中标记，不伪造一个看似完整的部分计数。必需身份/数值类型不合法时拒绝，不截断身份，不把空值变成零。总摘要最多 64 KiB；这是 QC 摘要预算，不是声称所有其他任务类型的完整 NATS 事件也经过相同限额处理。

## 2. 应用源码与回退

使用交付包的 `apply.py`。脚本会先核对所有待修改文件的 Git blob 摘要、替换锚点、新文件摘要和 Go 语法。全部预检完成之前不写源码。它不会连接数据库、重启服务或修改运行配置。

```bash
python3 /path/to/rainpulse-batch1-3d84719/apply.py --repo /path/to/rainpulse --check
python3 /path/to/rainpulse-batch1-3d84719/apply.py --repo /path/to/rainpulse --apply
cd /path/to/rainpulse
make test-architecture-batch1
```

需要常规 diff 可使用 `--check --write-diff /tmp/rainpulse-batch1.patch`。若待修改文件已被后续提交改变，脚本拒绝覆盖；在干净基线分支应用后生成 diff，再审查合并到后续版本，不能跳过基线检查。仓库其他未涉及文件不受影响。

原文件备份与应用回执保存在 `runtime/patches/`。撤销：

```bash
python3 /path/to/rainpulse-batch1-3d84719/apply.py --repo /path/to/rainpulse --rollback
```

后续人工编辑会阻止自动回退，防止抹掉新工作。单文件替换是原子的，但多文件写入不是断电事务；进程意外退出后根据 prepared 回执执行回退。回退源码不等于回退已经部署的二进制/镜像；运行中存在暂停标记时必须先按旧发布身份确认恢复，不能先回滚 Go 绕过暂停机制。

## 3. SQL 索引与数据库验收

本批不改变表结构或删除数据。新增索引放在 `deploy/sql/20260922_planner_indexes.sql`，是**显式运维脚本**，不混入事务型自动迁移。

```bash
python3 scripts/rainpulsectl.py indexes
```

上述命令需要已初始化的本机部署清单。也可在测试数据库用 `psql -X -v ON_ERROR_STOP=1 -f ...` 执行。不得使用 `--single-transaction`：索引使用 `CREATE INDEX CONCURRENTLY`。先在测试库验证，再评估目标库空间和负载。脚本可重复执行，但 `IF NOT EXISTS` 不替代索引定义校验；遗留 INVALID 索引会报错，需人工检查，不自动删除。

索引覆盖雷达站/结束时间、扫描状态、QC-only 反连接、分析网格/状态/时间、重算归属以及预报网格/状态/时间。未在 105 执行 EXPLAIN 或压力测试，因此不提供虚构的吞吐提升比例。

真实数据库测试：

```bash
# 由本机安全配置提供连接串，仅使用隔离测试数据库，不使用生产库。
export RAINPULSE_TEST_DATABASE_URL='postgresql://...'
bash scripts/go_control.sh test ./internal/postgres -run TestBatch1Postgres -count=1 -v
```

测试使用单连接的会话临时表，不更改永久表。验证 451 条同时间戳记录完整分页、上一页变更状态后不遗漏、QC-only/异网格/重算归属过滤以及手动重算时效豁免。不设置测试连接串会明确 SKIP，不能把跳过写成通过。

## 4. 统一部署入口

适用默认形态：同一主机的 native `rainpulse.service` + 一个 Compose 项目的 Workers/基础设施。不是跨主机分布式锁，不适用于未纳入清单的外部 QC Worker。

已有部署先采集真实容器标签，不根据仓库默认值猜测正在运行的实验版本：

```bash
python3 scripts/rainpulsectl.py init --adopt-running --project-name <实际项目名>
python3 scripts/rainpulsectl.py config-check
python3 scripts/rainpulsectl.py status
```

将真实的基础 → realtime-shadow → unified → 实验覆盖文件顺序及 Worker 副本数写入 `runtime/deploy/active-compose.json`。QC 副本标签不一致、路径不存在、模式不匹配或清单已存在时拒绝猜测/重置。示例项目名不是硬编码的 105 项目名；以本机 Docker Compose labels 为准。

全新安装才使用 `init --new-install --project-name ... --qc-replicas ... --override ...`。环境参数默认从 `deploy/.env` 读取，其他位置通过入口的 `--env-file` 显式指定。不要在 JSON 清单中添加凭据。

`make deploy-up`/`deploy-check`/`deploy-status` 走统一入口。Legacy 清单只能显式 `--legacy` 或 `make deploy-up-legacy`；unified 模式拒绝选择旧 Go 容器。旧部署/打包脚本未批量删除，作为已有回退手段保留。

入口不自动构建源码、不拉镜像、不自动修改 BDP，也不代替 `systemctl` 管理 native Go。它管理容器组合与校验，更新源码后仍使用已有构建/安装命令生成二进制和镜像。

## 5. 首次接入暂停机制

首次部署此补丁之前，旧 Go/Worker **没有**这些检查，不能用新 CLI 宣称已暂停旧服务。先安排受控维护窗口，用原有流程暂停新的入库/重算提交，确认旧任务排空，再部署新 Go 与包含新 health 身份的 Worker。完成测试后再启用以下版本切换流程。

Go 与 CLI 必须使用相同绝对 `RAINPULSE_RELEASE_GATE_FILE`，例如部署目录下 `runtime/control/release-gate.json`。若未设置，Go 使用工作目录相对路径，CLI 使用部署根相对路径；因此必须保证 systemd WorkingDirectory 正确。使用服务所属账户执行 CLI，或预先设置合理的共享组权限，不能靠 `chmod 777` 处理。现有 systemd/BDP 环境配置应保留所有业务参数。

Go 每轮调度写出 `planner-release.json`，包含解析后的真实 QC 配置路径、启动摘要、当前摘要、执行模式、进程与时间。CLI 要求该记录新鲜（30 秒内）、进程存在、gate 路径相同；禁用调度或 Go 未升级时校验会拒绝，而不是猜测成功。

发布暂停针对新调度、变更 API 和 CLI 提交。自动接入可以继续保存/解码原始数据；旧任务仍可由原 Worker 执行，NATS 完成事件处理保持运行。暂停期间取消/重算 API 同样返回 503，避免最终校验时改变积压。直接 SQL 写入、绕过标准入口的脚本及未登记 Worker 不在锁保护范围，发布窗口内禁止这些写入方式。

## 6. 后续 QC 版本切换步骤

以下步骤是操作入口说明，不代表本次已经替用户执行。先冻结**目标**发布身份与现有执行模式：

```bash
python3 scripts/rainpulsectl.py release-plan \
  --qc-config /path/to/target-qc.yaml \
  --qc-flags /path/to/qc-flags.yaml \
  --image <已经构建并加载的目标镜像> \
  --execution-mode realtime_shadow \
  --output runtime/deploy/target-qc-release.json
python3 scripts/rainpulsectl.py release-pause --plan runtime/deploy/target-qc-release.json
python3 scripts/rainpulsectl.py release-drained
```

`realtime_shadow` 仅为示例；必须使用实际当前模式。发布工具不以“本次任务成功”为理由提升业务资格。暂停会等待已经进入的调度/变更请求退出，确认没有尚未生成任务的 QC 批次/全链路重算请求，再原子写入持久标记。若有未生成的任务意图，拒绝暂停，让旧调度器先提交或完成/取消该请求，避免“队列空但旧请求尚未发出”的误判。排空检查同时检查 PostgreSQL 的 `radar.qc` 任务、QC outbox、每个 Worker 使用的 durable consumer pending/ack_pending。旧 Worker 必须留到排空，不能先换镜像再发现旧版本任务仍在队列中。

只有排空成功后，使用原有运维方式同步更新 BDP/本地 Go QC 配置和 Worker 配置、镜像；需要改变覆盖文件列表时，在暂停下执行：

```bash
python3 scripts/rainpulsectl.py set-overrides \
  --override <保留的覆盖层1> --override <目标QC覆盖层>
python3 scripts/rainpulsectl.py up --service radar-qc-worker
# 使用既有安装/服务管理流程更新和重启 native rainpulse.service。
python3 scripts/rainpulsectl.py release-check --plan runtime/deploy/target-qc-release.json
python3 scripts/rainpulsectl.py release-resume --plan runtime/deploy/target-qc-release.json
```

`set-overrides` 的参数是完整覆盖列表，不是仅追加最后一层。副本数沿用采集清单。若目标镜像也承担本次其他改动，相关 Worker 应按现有发布计划一起更新；本工具的严格身份校验范围是 QC 发布，不冒充整个 CPU/GPU 栈的统一发布系统。

恢复前再次核对真实 Go 启动/当前配置摘要及模式、QC 副本数量、不可变 Docker image ID、配置和标志表摘要、运行时代码摘要、启动后文件未变化以及数据库/NATS 已排空。恢复使用排他锁；全部成功后先记录验证回执再删除暂停标记。任意失败保持暂停，没有 `--force` 跳过检查。

代码覆盖挂载会破坏镜像身份，本批校验拒绝覆盖 `/opt/rainpulse/algorithms` 的挂载；部署使用源码挂载时，应先改为可核验的镜像，不能用假哈希绕过。更换执行模式不属于这次 QC 参数发布。

回到旧版本时：保留当前暂停的 release_id，将目标计划换为已知旧镜像/旧配置的真实哈希，同步恢复 Go 和全部 QC 副本，再检查并恢复。不要简单删除损坏/遗失身份的暂停标记；先查明积压、配置和进程身份。

## 7. 测试范围与剩余验收

交付包 `validation/` 保存本次实际执行日志与汇总。本地执行的是标准库 Go 包单测、Python 摘要/身份/运维假对象测试和补丁引擎测试；这些不需要生产凭据，也不是实际 Docker/NATS 集成测试。

`make test-architecture-batch1` 在完整仓库、匹配的 Go/Python 依赖环境运行，对 controlplane、postgres、apiapp 一起编译回归。另应执行仓库原有 QC/Worker/契约回归，检查新增 health 字段与固定摘要的消费者兼容性。Compose 必须用实际清单 `config-check`，不能从没有本机 .env 的环境声称已通过。

上线前仍需：真实 PostgreSQL 三组分页验收；Go 全模块编译/测试；实际 Compose/NATS 排空与两个不同 QC 版本切换；固定雷达样本比较资产数组、缺测/无雨/低质门数、QC flags、QI 和定量资格；记录调度 SQL 扫描量与关键路径时延。不以静态 SQL 测试代替 EXPLAIN，不以软件单测代替气象效果验收。
