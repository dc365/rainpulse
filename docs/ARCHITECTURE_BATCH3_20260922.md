# RainPulse 第三批：整理长期维护边界

基线：`beb34035240d9ebdc8a7180828e048a46fb4eccb`（2026-09-22 13:05:44 UTC）。
目标：在保留前两批成果和当前气象行为的前提下，减少层间反向依赖与高频内部
HTTP 编解码，把页面加载和刷新策略分开，建立可检查的维护边界。
这是一份源码交付记录，不是部署或气象验收记录。

## 1. 交付内容

### 1.1 Go 查询服务

新增 `internal/readquery`，按已有 workflow 领域类型提供运行／分析查询接口。
不增加数据库表，不改公共 API/OpenAPI，不修改第一批规划器的过滤与游标语义。
原生 `apiapp` 构造一次，API 与 workspace 共用。工作台五类高频读取直接使用它：
预报目录页、分析目录页、分析详情、QPE 摘要和诊断包（含历史与旁站参考图）。

仍保留既有选取顺序、分页遍历、历史版本候选、QPE 谱系检查、降级警告与缓存。
注入查询失败时走原有错误分支，不会悄悄降级为一次新的 HTTP 查询。旧构造器
未注入依赖时保留 `query_legacy.go`，用于既有测试与明确的兼容调用。

不把这次完成范围说成“全部查询已重构”：产品／集合产品视图、区间计算代理及
其它尚未迁移的 Handler 调用保持原状。没有新通用 Repository 框架、服务容器或
缓存平台，后续新增共享读能力应继续采用具体的小接口。

#### 行为兼容细节

原分析 API 中 float64 指针先转换为 float32，再经 JSON 被工作台读成 float64。
直接做 `float64(float32(x))` 会改变显示小数；新适配器按原 float32 最短十进制表示
规范化，保留有效数据下的旧视图值，并拒绝非有限被使用数值。空指针仍为空，
ANALYSIS_READY 不意味着 operational_eligible=true。

原诊断接口输出 legend `value`，旧工作台子集读取 `minimum`；原来该 numeric
minimum 没有传入视图。这是已存在的字段不一致，本批刻意保留，而不是在重构中
隐式修正。以后修正应独立补契约／显示测试。已有 HTTP 契约转换函数不做搬迁。

### 1.2 QC 几何资源依赖

将 Worker 的几何资源加载实现移到 `radar/qc_resources.py`，定义六个显式资源工厂。
`qc_engine.context` 不再导入 qc_worker；在线路径注入兼容包装器，独立回放可以
直接注入冻结资料。可选路径解析移到 `qc_resource_paths.py`，原环境变量和严格
路径行为保留。

保留能力门控、缺目录／身份错配／配置变化／DEM 错误的返回与审计结果，保留
高度基准状态与 DEM 延迟校验，不重算阈值，不新增静态杂波资产，不开启近站分支。
安装器对原函数与迁移后的函数执行 AST 比较，除了精确的 provider 选择和原资源
调用替换，不允许几何加载可执行语句发生变化。该检查不是全 QC 数值相等证明；
真实算法回归仍需在目标依赖环境完成。

### 1.3 页面分包与刷新

App 保留原路由匹配，但主工作台、QC Review、AdminRoute 使用 lazy/Suspense。
AdminRoute 仍包含原 AdminWorkspace 与 PipelineInspector。增加显式加载态和
分包失败的手动重新加载入口；不加自动无限刷新，也不重写 GIS 或状态管理框架。

`refreshPolicy.ts` 是纯策略，`useWorkspaceData` 管理副作用，原 reducer 不变。
目录／详情分别失效；SSE 普通事件在历史固定模式只刷新目录，选中资产身份变化
才联动详情。相同ID的后台重算由120秒详情安全校验覆盖，不能把周期ID当内容哈希。

| 场景 | 请求策略 |
| --- | --- |
| 跟随模式收到变化事件 | 目录与选中详情刷新 |
| 历史模式收到无关变化事件 | 目录刷新，详情不随每条事件重复请求 |
| 当前选中资产／能力变化 | 详情刷新 |
| SSE 正常而没有事件 | 120秒安全轮询 |
| SSE 不可用 | 30秒目录／跟随详情回退轮询 |
| 固定历史详情 | 无论 SSE 是否正常，120秒安全校验 |
| 页面隐藏 | 自动timer/event刷新暂停；不阻止初次／显式操作 |
| 页面恢复、联网、重连 | 两类数据重新校验 |
| 手动刷新 | 两类数据刷新，保留错误／陈旧提示 |

5秒策略tick可能使请求触发多等待至5秒；网络、后台标签页调度和服务端缓存可能
进一步延迟，这不是“125秒内必有新数据”的SLA。AbortController、响应周期身份
核验和已提交快照保持逻辑不变。普通事件不能覆盖掉同一防抖窗口中的重连刷新。

### 1.4 文档、边界与 CI

`docs/ARCHITECTURE_CURRENT.md` 成为当前架构入口；README 明确区分当前六分钟／
30时效与历史冻结记录。`contracts/internal/maintenance-boundaries-v1.md` 写明
内部职责，不新建外部协议。`scripts/check_architecture_boundaries.py` 检查
readquery 不导入 HTTP/SQL/环境层、QC 不反向导入 Worker、原生接线共享实例、
App 不恢复重型同步导入、刷新策略不含运行副作用。现有 CI test 项增加本批检查。

## 2. 应用源码

在独立源码分支上操作，不在运行目录里边编译边覆盖：

```bash
python3 /path/to/rainpulse-batch3-beb3403/apply.py --repo /path/to/rainpulse --check
python3 /path/to/rainpulse-batch3-beb3403/apply.py --repo /path/to/rainpulse --apply
cd /path/to/rainpulse
make test-architecture-batch3
```

需要 diff 时用 `--check --write-diff /tmp/rainpulse-batch3.patch`。这是增量包，
必须使用安装器：仅复制 overlay 不会接上旧 API、Worker、Workspace 和 CI。
安装器逐文件检查完整 Git blob 摘要、替换锚点、新文件SHA256、Go语法和Python语法。
任何原文件已变更、新文件冲突或语法不通过时，在写入前拒绝。未涉及文件不受影响。

这次没有 full checkout 可供本地完整 apply/compile 验证，因此安装器的目标仓库
预检和下述全量回归是必做步骤，不能跳过或强制覆盖摘要检查。

## 3. 完整仓库验收

```bash
make test-architecture-boundaries
make test-architecture-batch3
make test-architecture-batch1
make test-architecture-batch2
make contracts-check
make test-radar-qc
make test-worker-sdk
pnpm --filter @rainpulse/web build
pnpm --filter @rainpulse/web lint
```

本批脚本执行真实 readquery/api/workspace/apiapp 测试、旧 QC 几何依赖相关测试、
新增 React 行为测试、旧 useWorkspaceData 乱序／断流测试和 tsc 项目类型检查。
依赖缺少应报错，不把SKIP写成通过。完整Go集成用例
`TestBatch3NativeReadParityWithActualAPI` 对同一数据比较旧API解码和新类型化结果，
并证明注入查询失败不触发隐藏HTTP回退。

还应在同一组真实输入及冻结配置上比较关键 QC 资产、标志／QI、谱系、PNG身份和
工作台周期选择；检查历史同ID重算、断流恢复、隐藏页面、分包404和深链接。
不能仅以页面能打开判断所有边界都已验收。

## 4. 实际本地验证与限制

ZIP `validation/RESULTS.md` 和原始日志是本次实际运行依据。Python资源/边界测试
使用注入I/O和小型源码夹具；Go隔离测试用测试专用workflow/UUID类型替代缺少的
模块；TypeScript纯策略由真实tsc编译后用Node测试。它们证明被测逻辑，不等价于
全仓库Go链接、React渲染、完整依赖兼容或真实雷达效果。

本次没有运行完整Go1.26仓库、React/Vitest整包、真实PostgreSQL/NATS/Docker、
105服务、真实资料回放或生产性能压测。没有声明首屏体积或延时改善的百分比。
代码分包需由实际Vite产物核验；内部读路径成本改善需由同输入、同并发测量。

## 5. 发布与回退

没有新数据迁移，没有调整第二批缓存容量、线程数、资源预算或后台路由开关。
新增 Python 文件改变运行时代码身份，Go 和镜像必须按前两批发布协调重新构建、
校验与同步更新；保留当前实验覆盖顺序、近站profile、Worker副本与所有发布门控。
源码包不是可直接运行的离线部署包，本次未操作105、未推送GitHub。

源码回退：

```bash
python3 /path/to/rainpulse-batch3-beb3403/apply.py --repo /path/to/rainpulse --rollback
```

备份与回执在 `runtime/patches/`。后续人工编辑会阻止自动回退，避免抹掉新工作。
单文件替换原子，多文件应用不是断电事务；中断后按prepared回执恢复。
回退源码不等于回退镜像／二进制，更不能通过回退绕过前两批发布暂停。
