# V5：跨站能力补全、测量异常与固定资料对照

日期：2026-09-14。工程候选，`operational_eligible=false`。基线是 `main@51ed346d8423f0167c3f8fc81ece46b0ec429fe9`，源码树 `a18b5f113f6e5651fdcd60c4f7c3b47f997bded6`。不接入 SWAN3；不改变现行部署、不批量重算、不修改原始资料。

## 1. 本轮目标与交付边界

| 步骤 | 实现目标 | 代码与状态 |
|---|---|---|
| A | 冻结 V1–V4，明确跨站能力缺口 | 旧 YAML 不变；4 个原参数摘要锁定回归；新配置 `fujian-qc-crossradar-v5` |
| B | 长距离强径向不再因高 RHOHV / 缺极化而完全失去检测 | `range_signature.py`：原始极坐标、真实米制距离、独立拟合；默认输出高风险隔离，不冒充确认 |
| C | 区分数值平台与已核实的反射率输出上限 | 无元数据只能记录平台假设；外部核实回执按雷达配置版本+原始 cut 绑定 |
| D | 按实际门的字段能力使用证据 | `crossradar.py`：原始矩计数、SNR 可用性/可靠性、候选与最终动作分别输出 |
| E | 文献候选允许有界短缺口关联 | 不改 AFL/V4 原公式，V5 独立关联；缺测不变为观测，可靠健康间隔终止关联 |
| F | 防止一个站改善掩盖另一个站变差 | `network_compare.py` / `network_gate.py`：冻结同一 V4 上下文，逐站×距离×能力分组门槛 |
| G | 显示原因并进入实际处理链 | Worker、QC Zarr、原 Hybrid/Grid/拼图/QPE 资格机制；`/qc-review` 展示 V5 字段及分组结果 |

本版不是“旧 Z9591 函数直接恢复”，也不是原始 AFL/RDD/SWAN 算法完成全面复现。它迁移的是长强径向异常类型的覆盖能力，保留 V4 作为内嵌基线，并增加独立测量假设、隔离和审计。真实 Z9591/Z9598 体扫、独立标注、站点性能验收尚待现场执行。

## 2. 算法语义

### 2.1 独立长强径向测量假设

对连续或有限短缺测分隔的回波段，以实际 `range_m` 计算 `20 log10(r/1 km)`。检查真实观测长度、跨度、近远端增长、高反射率比例、拟合残差、方位宽度和径向长宽比。相邻射线关联服从原始缺方位/扇扫拓扑；无站号白名单、固定方位删除表，也不把缺背景当晴空。

默认参数要求至少 100 km 跨度、80 km 实测长度、10 dB 近远端增长、足够距离比与 45 dBZ 以上样本比例。这些是显式工程候选参数，不是论文保证，也不应针对一张截图反复调小。100 km 以下异常仍由原 V4 路径处理；新模块不是所有 RFI 的通用检测器。

模型 1：`z = a + 20 log10(r/1 km)`，用实测未截顶样本的中位数残差拟合。模型 2：有核实回执的反射率输出上限，低于上限的样本拟合；达到上限者只提供潜在值不低于上限的不等式。模型 3：检测到数值上近乎相同的长平台，但无上限核实资料，只作未核实假设。

**显示红色/色标上限、反射率产品编码上限、接收机功率压缩不是同一件事。** 不从图片反推上限，不把恒定高回波一律当污染，不拟合接收机 IQ。长平台的自动猜测不能产生新增确认剔除，最多隔离。

`verified_ceilings` 默认为空。登记项必须包含实际 `radar_config_version`、原始 `sweep_000` 等 cut 名、上限、资料 SHA256 与来源 URI。Worker 验证回执字段、绑定身份及数值一致性，**不在线读取/核实该资料**。必须由现场人员先审阅源文件与厂商定义；任意填写一个 SHA 字符串不是完成了科学核实。

### 2.2 能力、动作与保真

能力 0：无原始反射率观测；1：只有反射率；2：部分极化或极化/信噪可靠性不足；3：至少两项原始极化矩且有实测可靠 SNR。这是输入能力档，不是天气类别或概率。缺 SNR 不等于 SNR 低，也不能使用默认高 SNR。

V5 先执行同参数 V4，再添加动作。可靠极化异常可佐证测量污染；高 RHOHV 不再阻止独立强径向假设进入检查。默认 `single_field_action: quarantine`：仅凭强结构拟合不能直接宣称非气象真值，但可以隔离出 QPE 与 KDP/相位处理。

实验选项 `research_reject` 必须另给 `single_field_validation_sha256`，且不允许用于未核实平台，也不突破强天气支持保护。这只是显式研究开关，回执不等于自动业务准入；默认不开启。

原 V4 确认与隔离不会因 V5 恢复。`DBZH_RAW` 不变，`DBZH_QC` 延续既有诊断语义，**定量使用看 `DBZH_USABLE`、`QPE_ELIGIBLE_MASK`，不能只读取 DBZH_QC**。确认剔除、疑似隔离和缺测分别报告；不以质量分数乘反射率/雨强；不向原始缺测创建对象编号。原始矩可信掩码同步收紧。

### 2.3 缺口处理

AFL/V4 冻结公式、12 km 原有判断均不改写。V5 附加文献路径按真实测量长度关联短缺口，限制单缺口（默认 1 km）及累计比例（0.15）。健康实测间隔不能被桥接；缺测仍无 ID、无确认。关联到的实际较弱门不直接成为硬拒绝种子，须有附加证据，默认至多隔离。

## 3. 全站对照命令

安装以仓库锁文件为准：

```bash
make bootstrap
make test-qc-crossradar-v5
uv run --project algorithms python -m rainpulse_algo.radar.qc_engine.network_compare \
  --manifest /data/frozen-qc/network.json \
  --output /data/reports/qc-v5-run01 --inspect-ray 0 --save-bundles
```

`--inspect-ray` 可以重复给出 1–6 条原始射线编号，必须在每个 cut 范围内。不选默认 0。报告在 `report.json`，用现有 `/qc-review` 本地文件导入；没有 API 发起、任务发布或网络取数。`--save-bundles` 另外保存每个案例的已验证 V4/V5 QC Zarr，供既有本地 Hybrid/QPE/诊断工具使用，不自动重跑全站产品。

顶层与案例机器契约位于 `configs/schemas/qc-network-v1.schema.json`、`qc-network-case-v1.schema.json`。每个文件必须有 SHA256。顶层例子（占位符必须替换）：

```json
{"schema_version":"rainpulse.qc-network.v1","expected_radars":["z9591","z9598"],
 "cases":[{"path":"cases/case01.json","sha256":"<64位SHA256>"}],
 "limits":{"minimum_processes":2,"minimum_weather_gates":100,"minimum_interference_gates":100}}
```

案例结构：

```json
{"schema_version":"rainpulse.qc-network-case.v1","case_id":"case01", "partition":"validation",
 "process_id":"independent-weather-process-id","data_kind":"real",
 "task":{"path":"task-v4.json","sha256":"<SHA256>"},
 "baseline_profile":{"path":"fujian-qc-paper-fusion-v4.yaml","sha256":"<SHA256>"},
 "candidate_profile":{"path":"fujian-qc-crossradar-v5.yaml","sha256":"<SHA256>"},
 "flags":{"path":"flag-definitions-v2.yaml","sha256":"<SHA256>"},
 "artifacts":[{"uri":"<task的原始input_uri>","path":"normalized.zarr","sha256":"<artifact_sha256>"}],
 "labels":{"sweep_000":{"path":"labels.npy","sha256":"<文件SHA256>"}}}
```

task 使用真实冻结 V4 `radar.qc.requested`，当前及每一个过去/邻站 artifact 必须齐全并准确哈希。对照只准备一次冻结 V4 原始上下文，并逐文件/字段检查 V5 内嵌 V4 结果等价，拒绝同时暗调 V4 参数。原版 `replay` 工具可以单独运行 V5 实际任务；已测试它与真实 `_execute_basic_qc` 路径工件逐字节一致。不要把“冻结 V4 上下文对照”与不同新规划任务完全等同。

`normalized.zarr` 的哈希使用项目 `artifact_sha256`，不是 ZIP 文件哈希；其它 JSON/YAML/npy 使用文件 SHA256。所有路径是本地路径，可绝对或相对案例目录。标注必须是原始 cut 的射线/门顺序，uint8，0=不确定、1=可信天气测量、2=确认污染、3=混合。不得用 QC 输出有效掩码决定评分域。没有标签仍能生成诊断，但不能 PASS。

同一批上限 32 个物理体扫，每个最多当前+6份上下文；逐个处理避免并发争抢。不同物理日期/过程的数据集可以拆批，但每批门槛只对该批声明范围有效。严禁把多个批次 PASS 直接当完整全网验收。

## 4. 最差分组门槛

按站、实际距离段、能力档汇总；真实 validation 案例才进入门槛。必须有各组足量可信天气和污染门，并分别来自足够独立天气过程。重复体扫、相同输入的换名重投、跨开发/验收过程或输入/上下文泄漏拒绝运行。

指标包含确认召回（隔离不算命中）、天气误拒、天气定量覆盖损失、>=35 dBZ可信天气覆盖损失、残余可定量使用污染的最长连续径向。与冻结 V4 作非劣化比较。任一组 FAIL 则全网 FAIL；任何缺站/样本不足阻止 PASS。PASS 也只说明本批声明资料满足配置条件，**输出 operational_eligible 始终 false**。

默认门槛延续研究工程目标，未经真实样本校准。数据真假与标签真实性必须由资料审阅确认；CLI 不可能仅从 `data_kind: real` 声明自动证明其为真实独立资料。相同天气过程的多次体扫不能冒充多个独立过程。

已有 `qc_engine.review` / `paper_compare` / `paper_batch` 保留旧引擎、AFL 和外部 RDD 参考入口。本次网络 CLI 是 **V4—V5 固定上下文对照**，并未再次实现原生 RDD 或信号层算法。

## 5. 合成演示与测试

```bash
uv run --project algorithms python scripts/demo_qc_crossradar_v5.py --output /tmp/qc-v5-demo
# 导入 /tmp/qc-v5-demo/comparison/report.json 到 /qc-review
```

这是显式合成的高相关强长径向，含人为反射率上限，不是任何实际站点数据、不是 IQ 仿真。它证明 V4 放行但 V5 隔离的判定缺口与界面；**隔离比例不是确认检出率**。网络门槛为 INSUFFICIENT，这是正确行为。

专项包括真实库调用、真实 Worker/离线 replay 字节一致性、QC Zarr 校验、V5→Hybrid→Grid→拼图→QPE 兼容、不同量程分辨率、长缺口、高值天气、满圆环、平台误认、缺字段、错误声明、文件摘要、重复输入和最差站点等回归。最终数量见交付包 validation，不在未跑前写死成绩。

## 6. 现场接入与回退

新路径 `configs/qc/fujian-qc-crossradar-v5.yaml`，镜像候选 `qc-opensource-5.0.0`，覆盖文件 `deploy/docker-compose.qc-crossradar-v5.yaml`。原 planner 同步新 QC YAML 与 flags v2；参数原文 SHA 纳入任务身份。先排空版本相关队列，再协调 QC/Grid/Mosaic/QPE/Diagnostics；避免一个共享队列由两个版本抢任务。下游沿用现有 qc-opensource 对应 Hybrid/拼图/QPE/诊断配置，读取工件中的版本与参数指纹。

仅合并源码不会切换部署，也不会改变历史图片。需要现场在副本/候选路径先跑真实回归，再决定是否重新生成产品。回退必须同时恢复规划器参数与 V4 Worker 配置，并保留最近成功版本；不得删除原始资料或仅有的可用结果。

## 7. 仍需现场或后续研究

- Z9591 与 Z9598 各 cut 真实残留、量程、SNR/极化字段审计；没有原始体扫不能保证截图现象已修复。
- 上限是否真实、含义是否为反射率编码，需要厂商/基数据证据；不能凭同色红扇区登记。
- AFL 隶属函数与图示参数仍是 V4 明示工程近似，本版不称完成原文精确复现；RDD 仍为参考导入，未取得定义就不猜测。
- 静态杂波/AP 晴空资产、独立标签、雨量站QPE检验、IQ、训练模型、站点CPU/RSS/P95和内网浏览器尚未验收。
- 本版没有把全部剩余RFI都纳入范围；单反射率长径向只是新增一种专家。避免继续用单站演示替代跨过程保真验证。

## 来源

文浩等（2020），《基于模糊逻辑的新一代天气雷达径向干扰回波识别算法》，气象学报78(1)，式(1)及径向特征组织： https://html.rhhz.net/qxxb_cn/html/2020010.htm 。
RADVOL-QC（2022），Atmospheric Measurement Techniques 15,261–278： https://amt.copernicus.org/articles/15/261/2022/ 。
这里只借鉴物理关系与分类型组织；新增删失拟合、默认隔离和固定上下文验收是RainPulse工程设计，不是上述论文原算法代码。源论文/图片没有随交付包再分发。
