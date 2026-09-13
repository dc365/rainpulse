# RFI V3：联合测量判定与有界外围复核

实现基线：`main@1251ea8fc8b628e48baf9ccaf28fd9349d3fb991`，包含已合并的 V2 和门级上下文修复。
本版是 `qc-opensource-3.0.0` 候选，不自动部署、不自动重算、不提升气象准入。V2 在 105 的实际部署与残留记录已经核对；不再把主要残留归因于旧页面缓存。

## 1. 本版范围与实现顺序

1. 新增独立 V3 配置和 Schema，冻结 V1/V2 的配置字节与参数摘要。
2. 实现原始相位/ZDR/RHOHV/SNR 联合测量证据，保留成熟 Py-ART、wradlib 算法主干。
3. 将结构对象、可靠核心、待检查外围分开；粗糙径向不再必须通过低标准差门槛。
4. 从原始核心进行有界外围复核，接通逐门过去样本的精确票数与测量佐证。
5. 把确认、未决隔离、其它原因拒绝和可信测量送入已有 QC/Hybrid/拼图/QPE 链路。
6. 增加固定原始域的残留原因审计、独立候选任务准备、真实 Worker 路径回放及 Web 检查字段。
7. 回归与实地验收分开；不能将合成反例的改善写成截图对应真实体扫已经通过。

核心模块：`multivariate.py`、`objects_v3.py`、`refinement.py`、`audit.py`、`experiment.py`。
沿用 `objects_for_profile` 做显式版本分派；旧对象算法与旧决策分支保持原行为。

## 2. 判定语义

### 相位与极化

- 使用原始 PHIDP 的周期二阶差分：比较两段沿距离相位增量，再做周期差。0/360 跨界、常斜率强梯度不因绝对跳变被判为相位噪声。
- 默认物理滞后 750 m，局部窗口 3000 m。所有统计仅在完整的原始相位连续支持上有效；缺口、距离端点、字段不可用输出 NaN，不补零。
- 相位不规则占比与 ZDR 越界占比分开保存，均不是干扰概率。原始极化矩、沿距离支持、二维纹理仍是三个不同能力。
- 低 RHOHV 分支仍要求径向结构、可靠实测 SNR 和足够原始极化矩。高 RHOHV 不再是绝对否决：可靠 SNR、相位不规则与实测 ZDR 异常相互佐证时可走独立联合分支。
- 只有相位异常或只有 ZDR 异常，不能独立确认高相关 RFI。实测 SNR 缺失/过低时不确认；可对有其它异常的结构做未决隔离，不能给它确定的非气象原因。
- 高相关、可靠 SNR、有效且规则的相位、合理 ZDR 联合形成天气保护边界。它用于限制本 RFI 通道，不意味着其它质控异常永远不得处理。
- 极化变量属于同一测量族，不能因两个库都使用它们就重复当作独立物理证据。默认参数均为待真实资料验证的工程候选，不是已校准分类器。

### 结构、核心和外围

- `RFI_STRUCTURAL_SEED_MASK`：原始数据中通过局部门段、长度、角宽、长宽比等检查的结构假设。
- `RFI_CORE_SEED_MASK`：结构假设内具有强局部测量证据与可靠 SNR 的原始核心。
- `RFI_SEARCH_MASK`：原始结构及其有界待复核区域。它不是删除掩码。
- `RFI_PERIPHERY_MASK`：搜索区域中不属于原始结构种子的门，不能自动继承核心结论。
- 粗糙条带采用实际支持、分段中位数/IQR、局部异常占比、背景差或距离变化等分支；不再把沿距离标准差 >4 dB 作为统一拒绝检测的条件。标准差仍保留为诊断。
- 门段关联允许有限短缺口；关联只建立对象身份，缺测门不变成观测。外围传播从原始种子出发，最大搜索 4 km/2°，确认范围 1.5 km/1.5°；两个范围独立。
- 传播不跨原始缺测、明确无雨、方位缺口、重复射线、联合天气保护边界或其它对象。不使用已清洗图作为新证据，不迭代到收敛；分辨率使迭代预算不足时显式报错。
- 局部粗糙或单个低相关值不会单独建立删除结论；小而强的气象单体仍需满足对象和测量联合条件，不能按面积直接删除。

### 时间证据

保留已合并的同 cut、同距离、有效方位/高度匹配、过去可获得资料、重复物理体扫检查。
V3 将每门有效样本数与支持比例恢复为整数票数；默认至少 2 个支持票，且支持比例不少于精确的 2/3。
`0.67` 与 `2/3` 的差异只在新 V3 版本修正，不改 V2 身份。

时间证据不是多数投票真值。时间辅助确认仍要求当前结构、可靠 SNR、适度的实测相关异常以及相位或 ZDR 佐证；时间独自不能创建核心，也不会阻止新生的强联合污染立即被识别。证据数量不足或上下文关闭时不晋升。

### 动作和数据

QC cause flags 继续为 v2，旧 bit 不改。V3 路径与阻断条件另用版本化诊断位，详情见 `contracts/data/qc-rfi-v3.md`。
`DBZH_RAW` 不修改；`DBZH_QC` 仍可保留原始诊断值。定量与相位必须检查 `QPE_ELIGIBLE_MASK` / `REFLECTIVITY_TRUST_MASK` / 各矩 TRUST_MASK。
隔离是未决且不用于 QPE，不等同于确认剔除。最终若被其它 QC 原因拒绝，路径 8 明确表示“其它原因拒绝”，不会继续冒充未决隔离或 RFI 确认。

## 3. 固定输入对照（无需部署）

```bash
uv sync --project algorithms --locked --dev
make test-qc-v3
uv run --project algorithms python -m rainpulse_algo.radar.qc_engine.review \
  --manifest /absolute/path/cases.json --output /absolute/path/v3-comparison.json \
  --inspect-ray 120 --inspect-ray 180
```

在已有 case 项增加：

```json
{"rfi_objects": true, "rfi_multivariate_v3": true}
```

保留 `normalized_zarr`、原始工件 SHA、分区和过程身份。该命令是单体扫对照，不带外部时间/邻站上下文；不要与完整 Worker 重演混淆。`/qc-review` 打开报告后可分别切换 V2/V3，各算法读取自己的核心/外围、相位占比、路径、阻断原因和精确值。
`--inspect-ray` 是原始射线索引，不是方位度数。

## 4. 从冻结 V2 任务准备独立 V3 实验

不应直接覆盖原任务 JSON 的配置和版本。已有冻结 `rainpulse.qc-task-replay.v1` manifest 时：

```bash
uv run --project algorithms python -m rainpulse_algo.radar.qc_engine.experiment \
  --manifest /absolute/path/v2-capture/manifest.json \
  --profile configs/qc/fujian-qc-rfi-multivariate-v3.yaml \
  --output /absolute/path/experiments/v3-task
```

此命令检查原任务/配置/flags SHA，保留 occurred_at、输入与上下文选择，按原任务和新配置摘要生成独立的确定性任务 ID。输出保留 `offline_experiment` 反事实身份，绝不当成原部署任务；不发送任务到 NATS。
把新 manifest 的 `source_revision` 填为实际执行 checkout 的 `git rev-parse HEAD`，然后：

```bash
uv run --project algorithms python -m rainpulse_algo.radar.qc_engine.replay \
  --manifest /absolute/path/experiments/v3-task/manifest.json \
  --output /absolute/path/experiments/v3-result
```

产出 `qc.zarr`、`receipt.json`、`residual-audit.json`。上下文使用和序列化复用实际 Worker；缺少冻结资产就明确失败，不悄悄联网补其它版本。每次选择新的输出目录；不覆盖旧证据。

## 5. 残留原因审计

```bash
uv run --project algorithms python -m rainpulse_algo.radar.qc_engine.audit \
  --qc-zarr /absolute/path/experiments/v3-result/qc.zarr \
  --sha256 ACTUAL_OUTPUT_SHA_FROM_RECEIPT \
  --output /absolute/path/audits/v3-residual.json
```

可选 `--roi native-roi.npz --roi-sha256 FILE_SHA`。NPZ 每个原始 sweep 必须显式给出同形状二进制掩码；未选择的 sweep 用全 false。不允许在候选结果中随机选剩余门作为评价域。

审计的默认分母为原始有效且 DBZH≥5 dBZ 的门，不取候选留下来的部分。报告包括确认剔除、隔离、定量可用/不可用、原始射线上的最长连续保留段、阻断原因直方图和原始体扫/作业/参数/库/上下文身份。
“最长保留回波”包含真实天气，不是自动 RFI 标签。阻断条件有重叠，不能相加成互斥分组，也不能把隔离数加到确认检出率中。无标签时不输出精确率、召回率或准入结论。

## 6. 显式候选切换（本次不执行）

配置：`configs/qc/fujian-qc-rfi-multivariate-v3.yaml`。
镜像候选：`rainpulse-cpu-worker:qc-opensource-3.0.0`，覆盖变量 `RAINPULSE_QC_V3_IMAGE_TAG`。
Compose：在现有 base/realtime-shadow/unified 文件后增加 `deploy/docker-compose.qc-v3.yaml`。

复用 flags-v2、已有 `qc-opensource-hybrid-v1` / mosaic / QPE / diagnostics 下游配置；这些 v1 名称属于开源代际的通用下游配置，不是说仍运行旧 QC 引擎。新 QC 参数摘要随产物传播，不允许与 V2 输入混拼。

原生 Go planner 的 `RAINPULSE_PIPELINE_QC_CONFIG` 必须与 Worker 的新配置同步，不能只切一个容器；禁止启动旧 Go 容器与原生统一服务并行。部署前先停止规划、排空相关 pending/ack_pending、保留旧镜像与配置、核对五类 Worker 和实际导入源码摘要，再恢复规划。

先单个真实冻结体扫验证核心/上下文/上传分段耗时和峰值内存，再做完整案例重算；不得把新增实例数当作吞吐提升证明。没有在目标机测量前不承诺 P95/RSS。
合并源码不会更新旧的历史 PNG；必须明确重算并追溯新分析/诊断资产。08:25 与08:30可能属于同一个物理体扫，按 scan_id/内容去重，不冒充独立样本。
回退恢复 V2 planner 配置、镜像和配套选择；不覆盖原始观测，也不删除最后一份成功结果。

## 7. 必做真实验收与未实施边界

本地无法读取 105 原始体扫，未验证 08:15/08:30/08:45 的真实剩余射线是否因本版消除；也没有做实际 PPI 浏览器验收、独立标签/雨量站验证或部署。
原生 RAVE/SPIKE、bRopo 未新增构建执行，已有参考导入不冒充实际执行。
未训练小分类器或分割模型，未取得 IQ/SQI/NCP 等信号级资料；这两条仍是后续需真实数据的路线。
不新增温度/融化层资产获取，不把未验证的 Z-PHI 诊断订正写入定量产品。

在同一原始体扫、相同 cut、色标和定量语义下比较 V2/V3：记录最长残余径向、边缘/远端残留、确认与隔离分离、可信天气保留和定量覆盖；特别检查08:45东侧真实块状回波与近站回波。已用于开发的时次不是独立验收集，扩展过程必须隔离上下文窗口。不能用“图更黑”判定更准确。
