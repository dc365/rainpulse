# P0–P3 多仰角质控工程候选：实施与接入

## 交付目标

基于 `c3df57c570594891c0cae796382420c4d385679e`，按《RainPulse 多仰角形态、源判别与组合反射率：研究方案》P0–P3实现。新代码位于 `radar/qc_engine/volume_review`。它是明确 opt-in 的整站体扫审查，不取代原始数据、不重新定义原有 QC 标志，也不自动接管线上任务。

核心顺序为：原生数据与可用性 → 每层能力盘点 → 原始对象与层次 → 留出源参考 → 雷达/物理两种坐标关联 → 门级天气/源/混合/未定 → 独立动作 → CR 资格与实际最大值来源。

## 已实现范围

P0：QC 数值、掩码和定义一起保存，实际生成的标量 PNG 与其数值输入、有效掩码、投影 RGBA 及配置哈希绑定；增加离线重投影复核。图数不一致会报错，不通过改透明度来掩盖。

P1：复用 scikit-image 多阈值连通域和区域属性，使用物理距离尺度；高层不再必须满足旧分支160 km等长距离条件。跨0度闭合但不跨真实扫描缺口，原始小片段仍依其真实支撑描述。不使用同一固定径向上的 RANSAC 作为新增污染证据。

P2：训练端排除目标/保护块；原始SNR状态和极化决定源模型是否可用。实际采样几何、不同仰角与时间约束决定同源关联；可比物理空间中的正天气证据保护门值。形态不是直接动作，缺极化或缺时间不被强行补齐。新隔离不新增“已确认干扰”，预算超限保留隔离但要求审查。

P3：从反射率信任资格而非雨量算法资格定义 CR；分别保留原始、可信、未定最大值和次高可信值，实际赢家/次高来源及相对雷达高度全部进入对象存储。audit 只增加研究产物，experiment 才选择候选图层。没有可信观测时为不可用，不是无雨。

## 与既有链路的兼容

改动点只有6个旧文件：profile、QCResult、runner、qc_zarr、QC validator、diagnostic renderer。Go/React、数据库、默认配置、消息合同中的大数组传输策略均未改变。完整对象/参考/关联JSON在对象存储侧，完成消息只保留计数、状态、哈希与路径。

特别处理了两个后置阶段问题：

1. 旧 V7/OC1/NP 校验器要求其最终动作严格等于该阶段基线+增量。新阶段通过保存精确 `VOR_BEFORE_*`、重建旧视图来执行原校验，再校验新阶段；不改宽旧校验条件。
2. phase/KDP/attenuation 在旧阶段已经计算，新隔离可能使其输入不再可信。与新隔离相交的原信任连续段会失效相应派生产品，等待携带真实环境上下文的重算，避免原始门虽隔离但订正产品仍泄漏。

捐赠射线索引本身也转回原采集顺序；仅重排二维矩阵而不转换索引值会产生错误追溯，本实现有专门测试。

## 配置生成与消融

```bash
python scripts/make_volume_review_profiles.py \
  --parent /path/to/frozen-live-qc-parent.yaml \
  --diagnostics-parent /path/to/frozen-diagnostic-parent.yaml \
  --out /path/to/new-volume-review-profiles
```

生成 P0 audit、P1 audit、P2 audit/experiment、P3 audit/experiment 共6个QC子配置；可选生成独立的全仰角诊断配置。父配置不会被写入；每个子配置有独立 profile_version 和继承回执。实际需要用项目 `load_qc_profile` / 诊断 profile loader 校验后，由Codex在离线任务显式选择。

默认不撤销旧规则。要比较停止旧直接形态动作的影响，另建一组：

```bash
python scripts/make_volume_review_profiles.py \
  --parent /path/to/frozen-live-qc-parent.yaml \
  --out /path/to/no-direct-shapes-ablation --retire-direct-shapes
```

此开关仅关闭旧 fragment-line 中明确的直接形态实验开关，不承诺关闭整个历史QC，更不会复活已经生成的旧产品。主对照与消融不能混用同一配置身份。

## 离线复核

```bash
python -m pytest -q algorithms/tests/volume_review_20260919
python scripts/audit_volume_review_case.py \
  --case /path/to/radar-qc-case-a-multisweep/multisweep_data \
  --out /path/to/new-audit.json
python scripts/verify_volume_review_evidence.py \
  --qc-root /path/to/exported-qc-object-directory \
  --diagnostics-root /path/to/exported-diagnostic-object-directory
```

最后一个命令要求新版本已生成绑定快照；不能用于假装旧附件已有缺失的资格和来源。单独 `--qc-root` 仅验证数值快照，不声称验证PNG。

## Codex必须完成的整库验证

交付环境无法取得完整可运行checkout，缺Zarr/Py-ART/wradlib；包内已经运行的是核心算法、适配器合同、绑定、CR与安装器测试。下述整库步骤尚未执行，不得以包内测试替代：

- 在固定提交独立工作树中应用，审查6处diff，确认旧配置parameters_hash不变、新配置加载成功、继承关系真实。
- 使用项目锁定依赖执行 Python 全部相关QC、旧review/radial、Zarr、diagnostics、worker事件测试；同时按仓库规则执行合同及相应Go/React检查。未修改Go/React不等于已经运行其全量测试。
- 一个完整合成体扫跑真正 `run_open_source_qc → build_validated_qc_zarr_store → build_diagnostic_bundle`，验证独立字段、旧阶段校验、PNG逐像素重现、CR每个赢家及次高数值可追溯。特别覆盖两种原始射线顺序、缺极化切面、资源回退和审计模式零动作。
- 完整体扫冻结输入和配置后，依次比较P1、P2、P3，正常天气独立过程不用于调参。检查弱雨、浅雨、窄雨带、海上降水、小强单体、冰相/冰雹、混合区及缺资料，报告误删和可信覆盖损失而不只报候选数量。
- 绑定错配不得进入气象验收；失败时保留父产品，不覆盖生产历史；未经人工审查不能切换线上。

## 有意保留的边界

没有训练对象机器学习模型，没有把SQI/CPA/IQ用综合QI代替，没有做运动补偿或恢复被污染的天气测量，没有用本次只有DBZH等字段的包推断真实业务掩码，也没有宣称所有宽扇区/弱碎片已被正确识别。当前新增 source 路线在缺原始极化、SNR或相对时间时会保守弃权。它提供了可验收的统一机制，气象阈值与泛化仍需要正确绑定的独立资料。
