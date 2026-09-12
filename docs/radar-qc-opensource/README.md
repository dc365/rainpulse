# 雷达质控开源引擎：实现与审核入口

基线：`5c16866ad99ca47803483ae6fe6cdb0213650833`。工程分支：`feature/radar-qc-opensource-20260911`。

这是可执行的候选引擎和评估工具，不是已经达到气象最优、已完成真实过程准入的声明。默认业务配置没有切换，未部署、未批量重算，也未修改原始观测或既有成功产物。

## 1. 实施拆分与代码状态

| 步骤 | 本次交付 | 主要位置 |
| --- | --- | --- |
| 1. 原始几何、字段与依赖 | 按 cut 适配、方位排序和原序回填；有效邻域/缺口/相位折叠约束；真实 Py-ART 2.2.5、wradlib 2.9.5 调用和锁文件 | `qc_engine/adapters.py`、`algorithms.py`、`profile.py` |
| 2. 证据与决策 | 独立记录来源和可用性；KEEP/DOWNWEIGHT/REJECT/MISSING；天气支持不能复活已确认污染数值；单一纹理/低相关/小对象不直接硬删 | `evidence.py`、`decision.py`、`runner.py` |
| 3. 上下文与物理量 | Worker 读取冻结过去体扫和邻站，复用几何约束；Vulpiani 在可信连续段计算；wradlib 遮挡数学 | `context.py`、`phase.py`、`geometry.py` |
| 4. 定量链路 | 新 bit 15 原因、字段可信掩码、绝对资格过滤；QC→Hybrid→RadarGrid→mosaic→QPE；同一拼图拒绝不同 QC 参数代际混用 | `qc_zarr.py`、`hybrid.py`、`grid_zarr.py`、`mosaic.py`、新配置集 |
| 5. 对照和检查 | 同输入的 Py-ART/wradlib 候选、融合、旧引擎、可选局部门段增强；标签固定分母；精确径向报告；Web `/qc-review` 查看本地报告 | `review.py`、`qc_metrics.py`、`QCReviewWorkspace.tsx` |
| 6. 复现和接入 | 参数/库/输入/上下文摘要；新任务强制配置原文 SHA；任务重试工件字节一致；候选 Compose 配置和回退步骤 | `qc_worker.py`、Go `orchestration`、本文和 `RUNBOOK.md` |

所有阶段均通过现有 Worker/Go/React 边界接入，没有再造在线任务系统。旧引擎仍是独立回放路径，新引擎不调用旧径向种子扩张链。

## 2. 必须准确理解的边界

- 默认 `fujian-qc-opensource-v1` 是成熟库融合；`fujian-qc-opensource-rfi-v1` 是**显式 opt-in 的局部门段增强实验**。局部条带、宽扇区的合成反例测试不是该算法对福建真实资料增益的证明。
- **RADVOL-QC SPIKE 本次仅实现了原生对照产物的严格导入和粒度校验**。未构建/运行 RAVE 原生算法，不宣称完成 SPIKE 基线实验。无原生产物时报告 `not_executed`，不生成模拟结果，不把整射线 QI 伪装为逐门掩码。
- Vulpiani PHIDP/KDP 已实际调用并测试；Z-PHI 有条件调用适配器仅输出诊断候选，默认关闭。当前 Worker 未增加新的温度/融化层资产取得通道，不能据此宣称已完成自动衰减订正业务链，更不能将未验证订正直接写入 QPE。
- 静态杂波先验构建 API 已实现，但没有本项目多过程晴空观测可供生成真实资产；海岸线不冒充海杂波分类。新 v1 拼图要求统一 QC 参数身份；不同站点使用不同独立先验配置时，需在后续版本显式定义同一候选组，不能绕过一致性检查。
- 对照 CLI 当前是**单体扫＋可验证的体内垂直支持**，不读取外部时空上下文。在线 Worker 的上下文路径另有回归测试。不能把 CLI 结果说成完整线上上下文复演。
- `/qc-review` 是本地报告检查页：精确字段、动作、候选和径向剖面，不是新做的在线任意雷达门查询 API。真实底图 PPI 同色标截图、内网完整链路和独立天气标签尚待现场验收。
- 不同矩的源几何必须相容。同形状不等于已经证明配准，适配器拒绝显式错配；既有解码器未提供的几何/定标事实不补造。原始数据仍是最终核查依据。

## 3. 数据语义

`DBZH_RAW` 不改；`DBZH_QC` 仍为原始极坐标诊断值，保留已被标记的污染便于对照。真正用于候选定量资格检查的是 `REFLECTIVITY_TRUST_MASK` / `QPE_ELIGIBLE_MASK` / `DBZH_USABLE`；Hybrid 必须先应用这些掩码和硬原因，随后才进行 QI 加权。这样即使仅有一部雷达，低权重归一化也不会恢复已禁止使用的测量。

`VALID_MASK` 表示原始有效观测，REJECT 可以位于 VALID_MASK 内；REJECT 和原始 MISSING 是不同原因。质量降权不等于将反射率或雨强乘一个系数。`METEO_SCORE` 是未校准的气象隶属评分，不是降雨概率。

字段级 `PHIDP/RHOHV/ZDR/VR/SW/SNR_TRUST_MASK` 独立保存；相位处理不跨缺测或污染门段。几何/DEM 未知不视为零遮挡；未知上游遮挡沿程传播为未知。高相关回波和邻站有雨均不是本站测量的无条件保护符。

## 4. 如何运行

在仓库根目录：

```bash
uv sync --project algorithms --locked --dev
make test-qc-opensource
uv run --project algorithms python -m rainpulse_algo.radar.qc_engine.review \
  --manifest /absolute/path/cases.json --output /absolute/path/qc-review.json \
  --inspect-ray 10 --inspect-ray 120 \
  --legacy-profile configs/qc/fujian-qc-evidence-v3.yaml
```

`--inspect-ray` 是原始射线下标，不是方位角；每个报告最多选择 12 条。报告在浏览器 `/qc-review` 页面本地打开，不会上传到外部服务。

最小清单（占位值必须换成真实摘要；路径相对清单位置）：

```json
{
  "schema_version": "1.0",
  "cases": [{
    "case_id": "case-001",
    "partition": "development",
    "process_id": "20260828-process-a",
    "normalized_zarr": "native/scan-001.zarr",
    "input_sha256": "REPLACE_WITH_ARTIFACT_SHA256",
    "experimental_rfi": true,
    "labels": {"sweep_000": {"path": "labels/scan-001-sweep-000.npy", "sha256": "REPLACE_WITH_FILE_SHA256"}}
  }]
}
```

没有标签时删除 `labels`，工具会明确输出无标签、无精确率/召回结论。标签必须与原始射线/门形状一致：0不确定、1可信天气测量、2确认干扰、3混合污染。禁止随机拆门作为独立验收；清单的 development/validation 不得共享过程 ID 或相同输入摘要。现有 08:10/08:15/08:25/08:30 失败时次属于开发回归，不冒充独立样本。

输入摘要使用现有 `artifact_sha256(objects)`，不是 ZIP 文件 SHA；读取本地 Zarr 时忽略 `_SUCCESS.json`。标签 SHA 则是 `.npy` 文件原文 SHA。所有路径和数据保留在内网，不应提交真实基数据到公共仓库。

## 5. 审核顺序

先复核输入配准和有效邻域，再看成熟库原始候选与融合差异，然后在不扩大可信降水误拒的前提下评价局部增强。检查：平滑低 RHO 干扰、宽扇区、局部段、强小单体、混合天气/污染、扇扫、0/360、缺方位、全缺测、晴空、缺矩、过去上下文、不同配置混拼、重复任务。

气象目标仍是候选目标：确认干扰召回≥90%、可信降水误拒≤1%、≥35 dBZ可信降水保留≥99.5%；没有独立标签时不得写“达标”。还需核验定量覆盖、残留最长条带、保留值偏差以及独立雨量站 QPE 误差。每个站点/过程分别报告，不能用删掉评价域获得好分数。

## 6. 本次附带的必要维护

原冻结身份测试仍引用已移动的 `cmd/orchestrator/planner.go`，改为当前 `internal/controlplane/planner.go`，保留原断言。若干既有 Python 超长行只做格式整理。累计量 hook 去掉效果中的同步状态清零，并将一次请求的完成计数限定在该请求，避免 StrictMode/重新启用时累积旧计数。旧 NowcastNet 时间适配新增纯格式换行，不改数值。

测试和构建记录见最终交付报告；软件回归通过不取代真实雷达与内网验收。
