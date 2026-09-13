# 径向对象 QC v2：实现与验收契约

基线 main e150cc90；本轮工程候选 qc-opensource-2.0.0，不代表真实天气效果准入。

1. 输入能力分开：原始极化矩、沿径向统计、二维纹理分别记录。纹理缺邻域不否定原始矩。
2. 检测两条路线：真实双侧背景差异；无背景时用对象自身的径向形态、距离形态和极化异常。
   缺测从不视作零/晴空。没有足够独立证据时允许未决，不强行删除。
3. 先关联有界局部区段，再形成多方向对象。最大短缺口、总缺口比例、角宽、径向长宽比有限制。
   连接只改变对象身份，不填补观测。全圆仅在完整 PPI 首尾连接；缺方位/重复射线不可跨越。
4. 天气存在与测量可信分别判定。高风险未决门保持 DOWNWEIGHT，另标 QUARANTINED，禁止进入
   定量场及相位订正；不加 NON_METEOROLOGICAL 确认原因。确认污染才硬拒绝。高相关不是全局真值，
   但本候选无足够证据时保护该门，不对整扇区一刀切。
5. 过去资料仅用原生、逐门、同 cut、配准且观测可用的候选。统计分母为实际可评价的过去样本数。
   至少两份资料才启用时间辅助；时间缺失不拦截新生明确污染，时间持续性本身不能删除无候选门。
6. 残余传播从本次确认种子出发，仅在同对象、当前有可靠测量且有极化异常的门上有限传播。
   可信高相关门、缺测、长间断和对象边界是停止条件；不循环扩大到收敛。

## 新字段（每个原始 sweep 的 ray × gate）

- OS_POL_RAW_MOMENT_COUNT / OS_POL_AXIAL_MOMENT_COUNT / OS_POL_TEXTURE_MOMENT_COUNT: uint8。
  旧 OS_POL_MOMENT_COUNT 保留二维隶属函数支持语义，不能用它判断原始矩是否存在。
- RFI_OBJECT_ID: uint32，0为不属于候选；ID只在单个sweep内局部有效，不跨任务当持久身份。
- RFI_STRUCTURE_AVAILABLE_MASK / RFI_BACKGROUND_AVAILABLE_MASK / RFI_SELF_SIGNATURE_MASK /
  RFI_CONTRAST_MASK / RFI_LINKED_OBSERVATION_MASK / RFI_BOUNDARY_OBSERVATION_MASK: uint8二值。短缺口中的实测弱门可以关联对象，
  但不成为即时硬拒绝种子；未观测门始终ID=0，不把缺背景记成无污染。
- RFI_AXIAL_STD_DB / RFI_AXIAL_SUPPORT: float32，一维实测支持统计；缺测门诊断为NaN。
- RFI_RISK_STATE: uint8，0无候选、1候选、2高风险隔离、3确认RFI。
- RFI_QUARANTINE_MASK / RFI_RESIDUAL_PROMOTED_MASK / RFI_TEMPORAL_USED_MASK /
  RFI_MIXED_MASK: uint8二值。隔离与确认拒绝互斥，不能用隔离当作干扰召回提高。
- TEMPORAL_RFI_SAMPLE_COUNT: uint8；TEMPORAL_CANDIDATE_PERSISTENCE: float32，零样本为NaN。

REFLECTIVITY_TRUST_MASK = observed & ~(REJECT | RFI_QUARANTINE)。QPE_ELIGIBLE_MASK必须是其子集。
DBZH_RAW保持不变；DBZH_USABLE在不合格门是NaN。未知/隔离/拒绝均不等于有效无雨。
QC_ACTION仍为0/1/2/3；QC_DECISION_REASON保持uint16，新增原因位由decision.py定义。

## 不变的范围与验收边界

保留Py-ART/wradlib主干、v1配置文件和v1参数摘要。只在新profile上启用对象引擎。
不重写平台、不自动部署、不自动覆盖旧结果、不启用业务准入。
本轮必须先通过合成机理、缺矩/低SNR/高相关天气、短缺口/长缺口、扇扫/0°、多方向、时间、
序列化和下游定量隔离回归，再用08:10/08:35/08:40/08:45四个真实体扫逐对象验收。
实际数据未取得时须明确未验收，不得用合成场当截图对应真实值。


## 测试与源码边界（2026-09-12）

新增回归入口：

```bash
uv sync --project algorithms --locked --dev
make test-qc-rfi-objects
```

新增测试在真实安装的 Py-ART 2.2.5 / wradlib 2.9.5 上运行：细射线缺二维邻域、缺双侧背景、
250m短缺测、长缺口切分、多方向对象、0°闭合/扇扫缺方位、强相关天气、缺矩/低SNR、时间计数和
迟到/未来资料、重复物理体扫、混合污染、有限残余传播、资源上限、原始字节与重试一致、
QC序列化、算法切换报告以及实际Worker与离线回放产物一致。
这是机理回归，不是四张截图的原始体扫。完整输出和运行记录随补丁交付；真实体扫、标签、雨量站、
本地PPI与部署镜像仍须验收，不把合成目标内的通过率称为真实召回率。

本轮的补充检测为自主实现的有界对象关联，并非复刻SPIKE论文或原生库。
保留Py-ART/wradlib原始候选及v1配置对照，不声称已运行原生RAVE/SPIKE或bRopo。
缺SNR或低SNR时不以低RHOHV确认为RFI；可以隔离未决，但必须报告覆盖损失。
当前参数是明确版本的工程候选，尤其最大角宽、最小段长、最低SNR需要本地独立过程确认。
大于65°的整体对象、15km内结构、非典型距离形态等仍可能未决/漏检，不能宣称解决任意干扰。

## 两种回放，不混淆实验口径

**1. 单体扫算法对照（无外部上下文）**

沿用README的cases.json，在每个案例显式加入 `"rfi_objects": true`；保留 `"experimental_rfi": true`
可同时比较旧局部门段算法。运行：

```bash
uv run --project algorithms python -m rainpulse_algo.radar.qc_engine.review \
  --manifest /path/cases.json --output /path/review.json --inspect-ray 110
```

`/qc-review` 本地打开报告，选择 `rfi_objects_v2`，查看实际profile/parameters_hash、原始射线索引、
对象ID、原始/一维/二维极化计数、确认/隔离/可用比例及原因。切换算法时读取该算法自身字段，
不会继续显示默认基线的门值。无标签报告不输出伪造的精确率/召回率。

**2. 冻结任务回放（实际Worker上下文/核心/序列化）**

```bash
uv run --project algorithms python -m rainpulse_algo.radar.qc_engine.replay \
  --manifest /path/task-replay.json --output /path/new-exclusive-output
```

清单格式参见本目录 `task-replay.example.json`。导出原 `RadarQCRequested` 任务JSON和所有被引用的
normalized Zarr，计算任务/配置/flags文件SHA以及现有artifact_sha256。不是ZIP摘要。
使用新v2候选必须形成新的任务身份/参数SHA，不能把旧任务直接改名说成原任务复现。
验证后输出 `qc.zarr` 和 `receipt.json`；不访问数据库/队列，不发布，不覆盖旧目录。
附加资产仍走既有配置校验；提供确实兼容的本地站点与DEM资产，否则显式记录不可用。

清单中的目标分析时间与 `actual_observation_end_utc` 分开保存。四个开发案例为北京时间
08:10/08:35/08:40/08:45（分析目标分别UTC00:10/00:35/00:40/00:45），实际cut/体扫时刻以工件为准。
不能因文件名或页面分钟相同就视为相同扫描。所有上下文固定其可获得时间和唯一物理身份。

## 配套候选部署（本次未执行）

1. 使用同一源码/锁文件构建CPU镜像，默认候选tag `qc-opensource-2.0.0`；记录实际digest。
   新 `deploy/docker-compose.qc-rfi-objects.yaml` 完整覆盖QC、Hybrid、拼图、QPE与诊断五类Worker。
   在已有base/realtime-shadow/unified文件之后使用本文件，不依赖另一个候选覆盖文件的顺序。
2. 原生Go规划器设置 `RAINPULSE_PIPELINE_QC_CONFIG=<host_repo>/configs/qc/fujian-qc-rfi-objects-v2.yaml`。
   其余四条PIPELINE路径使用本目录RUNBOOK中的既有open-source候选Hybrid/mosaic/QPE/diagnostics v1，
   flags仍为qc-flags-v2。它们按输入QC参数摘要隔离，兼容v2资格字段；不是改成旧flags-v1。
3. 停止新任务规划、排空共享durable队列，再同步切换Go规划器和全部配套Worker。新旧QC参数不能
   在同一拼图混用，不滚动随机混跑，不强制清空队列或数据库。旧成功工件留作回退。
4. 首先回放一个完整开发案例，核验receipt、QC Zarr、Hybrid及QPE来源一致，然后再运行另外三例。
   四例通过不等于独立验收，之后补不重叠天气过程。回退只选择旧一致配置/镜像及成功产物。

## 需要审核的代价

- 时间辅助使用逐门可评价样本，零样本是NaN；至少两份才晋升中等相关异常，不把未来扫描纳入。
- 背景缺失时自体结构路线要求较长且符合距离形态的对象；仍可能漏掉不符合模式的真实RFI。
- 高风险隔离可以降低污染进入QPE的概率，也损失定量覆盖；必须和确认拒绝分别统计。
- 残余检查仅有一轮有界距离传播和一轮邻射线传播，绝不迭代“直到干净”。
- `RFI_RISK_STATE=3` 是本候选判据下的确认动作，并非拥有独立真值的统计置信度。

统计窗口横跨突变边缘可能截去真实污染端点。v2在已接受对象周围最多扩展4km（同时不超过半个
局部窗口），要求端点实测低相关、正回波及与种子距离形态相容。允许配置内短缺测仅作对象
关联，不赋予缺测门ID；边界假设单独标记并默认隔离，只有有限残余确认规则才可晋升。
真实强相关门、有效无回波、长/累计过多缺口和其它对象都会停止关联；不迭代扩张。
