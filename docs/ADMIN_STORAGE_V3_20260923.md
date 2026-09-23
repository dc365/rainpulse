# RainPulse 算法版本与存储生命周期

基线：`7b395662445c230933bfb2174598f3d988c6ffc4`（2026-09-23）。
本轮是代码交付，不是105现场inode诊断或磁盘清理记录。

## 1. 两个不能混淆的“最新”

界面用“当前默认 / 上一版 / 历史”组织**管理执行身份**。每种任务（qc、render、diagnostics）有一个显式默认指纹。指纹仍包含真实代码、配置文件和版本信息；不按版本字符串、文件名或最近提交号自动挑选。

数据保留的“1份”指同一个实际体扫（scan_id）或区域分析（analysis_id）、同一种产物阶段的最新成功候选，不是全站只剩一个时次，更不是覆盖原始资料。同一作业的QC和依赖图件整体保护：任一子任务需保留，就保留整个作业。

默认版本选择只约束**新的管理预检查**。没有设定时保持原匹配逻辑；设定后，Go当前配置与指定Worker身份不一致会阻止预检，不能静默换参数。选择按钮不负责部署镜像、改BDP/YAML、热切换实时算法，也不修改已冻结任务。上一默认指纹仅用于管理展示和48小时保留缓冲，不冒充全系统发布回滚。

历史源码、配置摘要、作业和尝试日志继续保留。它们是追溯依据，不把删除小型Git配置当作缓解大量数据对象的主要办法。页面最多展示200个实际使用/登记身份，当前和上一版优先，默认隐藏其余历史。

## 2. inode问题：测量实际文件系统

本次未登录105，不能确认100%来自MinIO、Zarr目录、Docker层、构建缓存、训练样本还是临时文件。对象数不等于物理inode数，压缩包逻辑字节也不等于实际可回收容量。

只读首查（按实际挂载点替换，不执行删除）：

```bash
findmnt -T /实际数据目录
df -h /实际数据目录
df -i /实际数据目录
# 下列目录遍历可能产生I/O，仅在低负载下对已确认目录使用，不加 -L：
du --inodes -x --max-depth=2 /已确认的目录
```

新脚本读取宿主机 `statvfs`，同时记录bytes、inode、设备号和采样时间：

```bash
python3 scripts/storage_ops.py inspect \
  --path /实际数据挂载点 --id node1-data --label 雷达数据盘 \
  --output /健康磁盘/rainpulse-storage-sample.json --upload
```

通过已有安全环境配置提供 `RAINPULSE_OPS_CONTROL_URL`、`RAINPULSE_ADMIN_TOKEN`。不要在代码或提交的配置里保存凭据。inode已满时，报告和清理回执须写到另一个健康文件系统；脚本会在退役任何作业之前检查回执可写。

可选 `--sample-directories` 最多访问10万条目录项、10秒，不跟随符号链接、不跨文件系统；输出 `complete=false` 表示被截断，结果不是精确inode普查。页面不会自动遍历磁盘。`inspect` 为一次采样，不创建系统定时任务。启用门禁前，应由运维用现有调度机制每60秒运行一次上述采样，并先确认持续上传正常。

存储与清理页可选择一个真实采样源作为管理存储门禁。默认**关闭**；UI启用值为inode已用≥95%、可用容量<1GiB、采样超过180秒、未提供inode计数时阻止新预检/提交/领取。这些是保守启动设置，不是该服务器已测出的最佳阈值；API可调整。已有心跳和结果登记不停，自动业务链路不被本模块暂停。多盘/多节点不应拿一个采样源代表全部存储。

## 3. 减少新文件：复用已经存在的packed QC

仓库的 `AtomicObjectPublisher` 已经支持 `RAINPULSE_QC_PACKED_STORAGE=1` 和schema3，不能再叠加一套自创容器。本轮在 `configure_admin_ops.py` 生成管理QC Worker配置时，对未配置这一项的来源默认补1；来源显式配置0仍保留0，需人工核对后修改。

逻辑Zarr内容与bundle校验身份不变，物理小对象聚合为现有8MiB目标的包（单逻辑对象更大时包可超过目标）。PNG图件和一般业务Worker不在本轮批量变更范围。旧包完整读取与校验路径保留。

这不会自动打包已经落盘的旧Zarr，也不能承诺某个inode下降比例。不要直接压缩/移动MinIO底层文件。全自动链路启用packed以及旧资产迁移需先验证所有消费者；本轮没有开启它们。

## 4. 保留规则（实际实现）

| 对象 | 规则 |
| --- | --- |
| 原始雷达、标准化输入、正式analysis/products、训练资料 | 本轮不删除 |
| 每个(kind, scan_id/analysis_id)的成功候选 | 至少最新1份，可选1~5份；未知分组身份保守独立保留 |
| 当前管理默认指纹的成功候选 | 每组额外保护最新指定份数；不因新实验覆盖而删除 |
| 上一默认指纹 | 默认切换后的48小时内额外保护 |
| 人工标记保留的作业 | 保护，取消标记后仍需重新预览 |
| 非终态、暂停、RUNNING/COMMITTING尝试 | 保护；租约过期仍不视为进程退出 |
| 被别的管理任务、未过期计划、正式jobs或资产表引用 | 保护；引用查询失败就停止，而非忽略 |
| 其它成功候选 | 至少24小时缓冲后可预览清理 |
| 失败/取消/部分成功作业的冗余尝试 | 至少7天，并满足全部其它保护 |
| 数据库任务、尝试、输入/版本摘要、日志 | 保留；只新增退役/删除标记 |

这是“最新1份 + 必须的例外”，不是强行保证每组只有1份。长序列预报所需的多个时次、标定/训练留样不属于重复版本。完整生产派生资产GC尚未覆盖，不应宣称全磁盘只剩最新版。

应用内每批最多20个作业；最多检查100个候选作业的元数据、每个作业最多2048个登记尝试。数据库为正确选择各组最新结果仍会做全局排名，不声称恒定SQL成本。API有超时。长历史库上线前需真实EXPLAIN和压力测试。

## 5. 清理协议和不可逆边界

```
预览（无删除） → 下载冻结计划 → S3全版本只读清点
 → 排空并停止管理Worker → 确认digest
 → 事务复查保护/引用/身份 → 逻辑退役
 → 按精确版本删除 → 全版本复查为空 → 写回执
```

每份预览有效15分钟，包含对象存储endpoint、作业/任务/尝试身份、登记的marker摘要和安全目录。提交前任何保留策略、引用或相关状态变化都会要求重新预览。UI不提供“删除全部”。

仅允许如下已经登记的尝试目录（含尾部斜杠）：

```
s3://rainpulse/operations/<runUUID>/<taskUUID>/attempts/<attemptUUID>/
```

仅删除其中已知的 `qc.zarr` / `review-images` / `diagnostics` 原子发布布局，意外布局、宽泛前缀、错误bucket、缺版本ID或超过10万对象版本预算会整体拒绝开始。未登记的任意孤儿目录、MinIO临时multipart和底层数据文件不在自动范围。

`begin` 持有数据库控制行锁，复查所有池已DRAINING、没有RUNNING/COMMITTING尝试、没有新鲜就绪/忙碌Worker，随后退役目标并冻结新管理入场；不在删除期间长期占用数据库事务。创建计划、提交、领取、版本选择、pin操作通过同一控制行串行化。

租约失效不能证明算法进程结束。运维必须排空，确认失联进程真实停止，必要时走现有撤销执行权操作，再停止管理Worker并明确传 `--workers-stopped`。整个维护期间禁止绕过标准接口导入/修改指向候选目录的正式引用，禁止外部程序写这些目录；本模块不是对任意外部数据库写入/集群用户的通用分布式GC。

冻结目录一旦退役不自动复活。退役任务不能重试/唤醒、退役输入不能被新计划采纳，图件请求返回410，界面保留原执行结论但明确数据状态。失败回执释放全局维护门禁，但对象保持退役，必须恢复**原plan_id**；不能重新选一个更大范围继续删。

`ERROR` / `DELETING`恢复可越过预览有效期，但仍校验原计划摘要、endpoint、目录和停止Worker条件。旧版本API不认识退役标记，发生退役/删除后不可直接降级到旧Go继续服务。

## 6. 运维命令

通过后台“算法与存储 → 存储与清理”生成/下载计划，或：

```bash
python3 scripts/storage_ops.py plan --keep-latest 1 --minimum-age-hours 24 \
  --limit 5 --output /健康磁盘/cleanup-plan.json
python3 scripts/storage_ops.py inventory --plan /健康磁盘/cleanup-plan.json \
  --output /健康磁盘/cleanup-inventory.json
```

清点使用只读对象凭据 `RAINPULSE_OBJECT_STORE_ACCESS_KEY/SECRET_KEY`。服务端冻结的endpoint必须与本机 `RAINPULSE_OBJECT_STORE_ENDPOINT` 一致；若服务端使用Compose内部DNS，应在可解析同一endpoint的维护环境执行，不能悄悄换到另一套MinIO。

排空、停止管理Worker（使用现有已登记部署流程，不执行Docker全局stop/prune），人工核对清点和保留清单之后：

```bash
python3 scripts/storage_ops.py purge --plan /健康磁盘/cleanup-plan.json \
  --confirm <计划中的完整digest> --workers-stopped \
  --receipt /健康磁盘/cleanup-receipt.json
```

物理删除需单独提供 `RAINPULSE_STORAGE_DELETE_ACCESS_KEY/SECRET_KEY`；参考 `deploy/minio/operations-retention-policy.json` 创建**仅operations前缀**的专用维护权限，不替换现有Worker策略、不把删除凭据交Web。不绕过Object Lock/保留期限。后台共享管理员身份，不伪造个人操作者。

MinIO启用版本化时，普通DELETE只新增删除标记，旧版本仍可能占用空间。本脚本逐个列举和删除准确version_id（包括旧版本和delete marker），最后检查该目录全版本为空；不调用无范围桶清空、不修改生命周期总规则。已登记marker的内容和任务归属在删除前校验，数据对象先删，success marker最后删。物理删除**不可撤销**，应只用于接受永久丢弃的可再生冗余候选。

失败时非零退出并记录各目录错误；修复后重新执行同一命令，恢复同一计划和回执。进程崩溃/API写回失败留在DELETING，新管理任务保持阻止，不能手动删数据库门禁行。重试的已删除版本计数来自已记录回执，异常断电时可能少计；目录最终为空才是完成条件，随后还要重采宿主机inode。不存在“回滚zip就能恢复磁盘数据”。

本轮不自动定时执行purge，也不接管Docker、journald、训练缓存或正式产品生命周期。若现场inode主要来自这些区域，本轮候选清理只能解决其中一部分，必须按只读报告定位后制定对应策略。

## 7. 升级、测试与回退

1. 在完整仓库独立分支应用源码并运行 `make test-admin-ops`。
2. 对测试库执行新增 `services/control/internal/operations/schema_v3.sql`。生产升级先排空管理任务，保留当前配置，按既有运维窗口操作。
3. 在现有schema1/2基础上显式执行同一增量脚本，再部署Go/Web；此脚本不删除业务数据。不要先部署依赖schema3的Go再忽略迁移失败。
4. 管理Worker Python算法代码未变；只有生成部署配置的QC packed默认值变化。需要应用该参数时先核对生成配置，再使用现有受控更新流程，不能在冻结任务中间热改配置。
5. 先用真实测试MinIO准备两个同体扫候选和一个人工保留候选，验证预览、并发引用、停止条件、删除故障、同计划恢复、标记与实际inode采样。
6. 只读/仅预览阶段可回退源码，保留追加表。**已经退役/物理清理后不能回退到忽略退役标记的旧后端**；维持本版数据生命周期读写约束，修复前进。物理数据没有本模块恢复能力。

真实数据库测试（必须独立测试库与显式允许）：

```bash
export RAINPULSE_OPS_ALLOW_INTEGRATION=1
# DSN通过安全环境提供：RAINPULSE_OPS_TEST_DATABASE_URL
GOWORK=off go -C services/control test -tags=integration ./internal/operations \
  -run TestOpsPostgresRetention -count=1 -v
```

新增测试覆盖保留最新/当前/pin/正式引用、临时pin改变后提交拒绝、退役后的入场限制和原计划恢复。未设置真实测试环境不算通过。本交付的实际执行结果单独写入ZIP内 `validation/RESULTS.md`。

## 8. 官方机制依据（与本项目实测区分）

- GNU Coreutils `df --inodes` / `du --inodes`：https://www.gnu.org/software/coreutils/manual/coreutils.html
- MinIO版本删除语义及不可逆性：https://min.io/docs/minio/linux/administration/object-management/object-delete.html
- MinIO对象版本化：https://docs.min.io/aistor/administration/objects-and-versioning/versioning/

上述资料解释工具/S3行为，不证明105的inode归因，也不证明此补丁已通过生产集成。
