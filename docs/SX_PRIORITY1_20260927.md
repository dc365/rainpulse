# S/X 主线第一优先：资料完整性

## 执行顺序与完成门槛

1. 全部 X 目录逐文件身份核对，记录别名、格式、坐标变化、六分钟覆盖和异常；逐站候选配置不得冒充几何或标定已核验。
2. 通过现有历史导入、解码及 operations 路径接入可用站点；按六分钟资料窗口处理，保留原始文件和真实观测时间。
3. 修复三个 S 历史周期的 QC 输入代际不一致，创建新的可追溯计算，不删除旧失败或绕过一致性门控。
4. 按明确的 UTC/北京时间范围生成全天 S、X、S+X；逐帧记录参与站、缺测、资料年龄、排除原因。不存在的资料必须显示缺测。
5. 验证完整性、数值一致性、界面状态及服务器资源；本地 main 合并后部署 105。

全部完成后主动汇总第一优先验收，再按主线进入第二优先：质控与融合效果量化。第一优先不混入新算法调参、QPE/预报接入或新界面设计。

## 初始核查

- 当前仅四 S、两 X 登记；X 源目录有 24 个站点目录、5,466 个压缩文件。
- 先前一小时组合 10/10 成功，两个 X 站共 20 份 QC 结果 READY。
- ZF602/ZF603/ZF604 文件头别名为 XMKG1/XMKG2/XMKG3，既有导入和解码支持显式别名；必须逐文件核验。
- 既有试验网格仅覆盖东部局部，接入全网前必须核查覆盖并冻结新的产品网格身份。
- 全天范围以文件头 UTC 为准，同时显示北京时间，不能把 UTC 日冒称为北京时间日。

## 第一批发现与修复

- 全 5,466 份文件的站号/坐标头部一致；名称六分钟桶存在缺口和重复，不能按文件数推断完整率。
- 更正此前“X 原始资料缺少 SNR”的判断：ZF101/ZF505 等 23 站样本含 SNR，旧候选配置未映射。新增 `fujian-x-20260828-full` 候选配置，补齐已观测的 SNR/双偏振字段；ZF900 样本未见 SNR。
- ZF502 首份样本 bz2 截断，下一份可完整读取；坏文件必须保留失败凭据，不能伪造有效扫描。
- ZF703/ZF801 头部 task 名称为空；draft null 仅匹配原生空字符串，仍拒绝已命名任务不符。
- ZF703/ZF801/ZF900 原生径向尾部按显式 `reserved_tail_20` 配置解释，未知噪声保持缺测，默认格式仍拒绝压缩标记；所有 moment 长度、比例和扫描边界校验保留。ZF900 样本解码通过；另两站还有射线时间倒序，需继续调查。
- S 三个失败分析时次为 UTC 02:36、02:54、03:00，确认为两组 clutter-fusion/volume-review 摘要混用。普通幂等 QC 命令只命中了旧成功任务，未修复当前资产，因此新增独立 QC rebuild 身份；沿用正常入队/完成状态机，不写 SQL 更改完成状态。
- `radar-qc-rebuild SCAN_UUID QC_CONFIG_YAML REBUILD_UUID` 是明确单站 QC 重算维护命令；同 UUID 幂等、独立输出路径，不触发整条预报重算，也不占用全流程 regeneration 外键。

## 历史修复与批量边界

- `radar-decode-rebuild SCAN_UUID CONFIG_YAML REBUILD_UUID` 仅允许 draft X 已标准化/失败扫描。固定原始资产与扫描身份，使用新作业和不可变输出前缀，重算时清空当前派生指针；旧作业和对象保留。重复提交不再次清空成功结果；并发重算、原始资产变更和可信站重解码被拒绝。
- 独立 PostgreSQL 测试实际执行上述事务、历史保留与幂等检查。
- 历史导入新增显式 `--cadence-seconds 360 --skip-invalid`：按原生文件头开始时间选择每六分钟桶首份有效体扫，重复资料和坏文件均返回报告；默认严格行为保持。
- 网络/数值组合上限扩为 32，覆盖四 S + 二十四 X；单站 QC 批次与前端图层选择的现有边界另行保留，资源预算不自动放大。

## 逐帧凭据进展

- 十二个 S QC rebuild 已全部成功，沿用运行中 Worker 的冻结 profile；三个分析时次的诊断图件重生成与 106/106 检查仍待完成。
- 新组合请求冻结 `requested_radars`，水平试验 manifest 保留未取得可用因果输入的站点；该原因包括缺测、未就绪或超龄，不臆测细分原因。
- 组合界面新增“本帧资料”：按计算输入列出站点、波段、体扫结束北京时间、资料年龄及未参与原因，与勾选显示图层分开。
- 105 小批量完整 Worker 验证暴露健康检查 `float(None)`：draft 允许未知方位分辨率，但健康模块假定必填。新增回归先复现失败，再修复为空时保留 `SCAN_GEOMETRY_UNKNOWN`、不推断完整率合格；S 已知分辨率计算保持。须重算失败样本后再扩大导入。
- 端到端完成事件还暴露 Go/数据库对 `expected_radial_count > 0` 的旧约束。`0023` 迁移与 Go 验证仅允许 draft + UNAVAILABLE + 完整率零 + `SCAN_GEOMETRY_UNKNOWN` 保存零（未知）值；不降低 ready 站约束。独立 PostgreSQL 事务验证未知候选可存储、正常结果可存储、伪装健康的未知结果被拒绝；完成事件将从不可变对象重放。
- 105 实测 X 解码约 3 秒、1.4 万对象上传约 23 秒。新增显式 `RAINPULSE_NORMALIZED_PACKED_STORAGE=1` 复用 schema 3 打包索引，保留逻辑数组字节与摘要，减少全天处理的小对象开销；默认关闭。打包/非打包往返及损坏索引拒绝测试通过。对象存储全套有两个既有异常文案断言失败，隔离 HEAD 基线复现相同失败，不属于本次变化。
- ZF504 真实样本在单站 QC 触发重复方位拒绝。增加仅非空间 X 极坐标 QC 可保留重复射线的显式验证选项；数组不删行、地图同方位确定性选择最后原始行。默认空间校验仍拒绝重复射线，水平组合沿用已有显式解析。两个回归先失败后通过，73 项 multiband/experimental 测试通过。
- 22 个 X 站的样本已 NORMALIZED；ZF703/ZF801 为真实径向时间倒序而 FAILED。S 诊断成功时次已核验 106/106。第一批 7 个完整字段 X QC 全部成功；其余逐站 QC 和全网组合样本验证进行中。
- S/X 计划均使用派生资产最近目录更新时间作为可用时间，避免 X 重解码后仍把原始到报时间误当作新资产可用时间。
- 全网数值样本 `af9ebd66-22ef-4347-853b-0b64c1e709fe` 成功：请求 28、参与 23、未参与 5；来源集合完整且互斥，S+X 与 S/X 逐格最大值完全相等，14,507 个 X 胜出格点全部保留不确定标记。组合界面“定位产品”使用真实产品范围，避免空选站时无法查看全网网格。

## 全天执行与回放修复

- 22 站代表性 X QC 与打包输入 QC 已成功；S 打包输入的真实 QC 也成功。两站时间异常继续明确排除。
- 全天 X 导入、X QC、S 导入/QC 已由有界、带检查点的服务器任务运行；未把任务启动当作全天完成。
- 修复组合目录旧 100 条上限：24 小时内按时次去重，选最新成功且未退役结果，最多 1500 帧；独立 PostgreSQL 回归验证 240 帧、重复版本及退役排除。
- 组合模式直接读取当天组合目录并加入共用时间轴，不依赖旧 S 分析周期或已勾选 X 图层；北京时间跨日范围和切日期清除旧结果有回归覆盖。

## 全网站点选择边界

- 前端全选/持久化、图层解析和多图层点查统一为最多32站，覆盖4S+24X；不改变数值组合资格或每接口4路并发。
- 回归先复现28站全选被截断到16、32图层点查被拒；修复后28站完整保留，32请求接受、33请求拒绝，旧用例保持通过。

## Full native composite coverage (2026-09-28)

The previous 625 × 656 km product grid clipped native S coverage (459.875 km
radius). Version `fujian-full-horizontal-1km-v3` uses EPSG:32651 bounds
[-553000,2417000,685000,3494000], 1238 × 1077 cells at unchanged 1 km spacing.
Bounds enclose native geodesic range circles for all 26 usable stations with a
1 km margin, using decoded range coordinates. ZF703/ZF801 remain excluded.
Horizontal experimental grids permit up to 2 million cells; quality/height
fusion retains its 1 million cell budget, tile and dimension bounds unchanged.
The comparison map fits actual product bounds. Existing clipped products need
regeneration; camera changes alone cannot recover discarded cells. Verify new
numerical coverage and live S/S+X products before declaring deployment complete.

Validated real UTC00:12 run `7cb0d9d3-494a-4327-ae1c-01ea9b4e2649`
SUCCEEDED (608.7 s): shape1077×1238; outside former bounds, S6317/X861/S+X7169
valid cells; S+X equals fmax(S,X), preserving NaNs. Live1920×1080 S/S+X
comparison shows complete product extent. Server receipt
`.build/sx-priority1/full-extent-validation.json`. All ten previously published frames UTC00:06–01:00 have `SUCCEEDED` receipts;
the live catalog returns full grid v3 and identical complete geographic bounds
for each. This does not establish full-day completion.

## Failed S decode storage recovery

The 105 storage incident left one S decode job failed at object publication.
`radar-decode-rebuild` now accepts a draft S station only when the scan is
`FAILED`, the original decode job is `FAILED`, the raw URI and SHA-256 match that
job, and the scan's radar configuration version is unchanged. Its output uses
an isolated rebuild prefix, retains the old failed job, and remains idempotent.
Draft X behavior is unchanged. A unit regression and an isolated PostgreSQL
integration test verify S admission, failed-source checks, idempotency, and
rejection of ready stations. Real 105 success must be checked separately.
