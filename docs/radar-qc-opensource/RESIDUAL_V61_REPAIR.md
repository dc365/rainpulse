# V6.1：运行路径修复、局部窄线与只读取证

## 交付定位

基于 upstream `dbcfa73853cf809ca3076e2ce33a86a6814d0a1f`（包含 V6
`0614a625e09e231df6293c1e64886f338424161b`）。本次是代码可证问题的修复候选，
不是基于截图追加站号/方位名单，也不是已经通过真实天气验收的 V7。

新配置 `configs/qc/fujian-qc-residual-v61.yaml`：pipeline `qc-opensource-6.1.0`，
decision `residual-v6.1`。V1–V6 YAML 的字节摘要保持冻结。没有改变原有反射率、
相关系数、SNR、长度、最大角宽、最大缺口、QI、隔离或确认门槛。

## 修复及边界

### 1. 几何资源加载遗漏

`radar/qc_worker.py:_load_qc_geometry_resources` 由版本白名单改为引擎能力判断。
所有 open_source profile 都能进入资源加载，包括此前遗漏的 residual-v6。
旧 legacy 路径仍只对 evidence-v2 请求几何资源。

**这是源代码行为修复，也会影响用新源码运行旧 V6 profile 时的结果。**
不能用新源码加旧 YAML 冒称复现了曾经有加载缺陷的现场 V6。
重建历史缺陷路径必须使用原始源提交/镜像；新作业应明确采用 6.1 身份，
不要以原 V6 job ID 重用缓存或覆盖旧产物。

6.1 会在 radial_context 中保留 geometry_resources：本站配置 SHA256、config_version、
波束加载状态、高程基准状态、地形配置 SHA256、DEM manifest SHA256。
地形影像块由原 VerifiedDEMTileStore 按需验证，加载 manifest 不等于已经读取所有影像。
未配置、无效、未核实高程基准不等于晴空或无降水。邻站原始可读数量与经过几何匹配
生成可比支持的门数分开统计。

站点和地形的外部挂载仍需要现场配置。本次没有生成 DEM、晴空杂波资产、重新核定
设备校准信息，也不伪造 SNR 或未提供的极化字段。

### 2. 局部窄线分支

新模块 `narrow_local.py` 首先完整保留原 narrow_candidates 输出，然后只增加独立的
局部窄分支。沿每个距离层计算实际拓扑连接的局部角宽；粗大父对象不能自动把旁边的
窄分支一起否决。用双侧真实肩部差异形成的局部窄通道可以独立评估。

局部切开宽交汇区以后，重新验证原来的跨度、实际支持长度、缺口、证据比例和长宽比。
没有满足条件的短尾巴不会因为有父对象就直接被接受。原始缺测不分配候选 ID。
原来合格的窄对象/模型不被覆盖；新增对象 ID 与已有 ID 分开。

新增的是候选路径，不是单凭形状删除。确认仍需要 V6 的可靠测量异常组合；
反射率单字段形态模型最多产生原规则允许的隔离。可信天气屏障和低质量语义保留。

新增字段 `V61_NARROW_STAGE_REASON` 记录**本地补充分支**的失败原因，不代表能解释所有
上游 V1–V5 阶段。字段缺失意味着模块未执行，不是“该条件为假”。枚举见契约。

### 3. 外围观测足迹

`residual_association.py` 在 6.1 中从射线中心间距改成有界采样足迹边界间距，
不改变原 1.5° 最大角半径和 1500m 边界间隙阈值。典型 1° 相邻射线在250km
中心距超过4km，但采样单元可以相接；中心距过大不再成为唯一否决理由。

这不是扩大成整片扇区删除。每条中间射线边、每个沿距离经过的门仍必须满足原始有效
观测和天气保护屏障；缺方位、缺门、不可靠路径不得越过。一次扫描只使用原始确认
或合格测量模型作为锚点，新隔离结果不再作为下一轮种子。

保留选中原始父 ray/gate、中心间距和足迹边界间隙。存在合格模型锚点时优先保留模型
见证，避免新达到的非模型见证挡掉原本可用的模型路径。这些是测量假设，不是校准概率。

### 4. 取证与上下文

`forensic_export.py` 是本地、只读、校验绑定的源 PNG 到 gate 报告工具。它验证：
PNG 内容 SHA、图层 JSON SHA、整个 QC Zarr 文件树 SHA、scan/radar/sweep、QC asset ID、
采样版本及原始 640×640 RGBA 尺寸；拒绝合图、网页坐标、缩放图或 renderer1.1原图。

每个选定像素保留全部二维门字段，包括原始极化、SNR、QI、动作/资格、V5/V6/V61字段，
并读取有限相邻门。真实 `datetime64[ns]` 射线时间以纳秒精度 UTC 字符串输出；
不将其转为损失精度或报错的浮点数。观测与动作数组分别生成语义摘要。

同 scan 在不同分析时次只算一个独立体扫；比较观测数组、动作数组与现有 prepared_context。
旧资产缺少完整资源/上下文摘要时明确返回未证明，绝不把两个空字段解释为上下文相同。
analysis_id 到 QC asset 的关联由现场冻结清单声明；工具不访问105 API去独立证明此关系。

输出 accuracy、recall 都是 null；样本 role 只是人工挑选角色，不被转成天气真值。
非零 alpha 对应无观测足迹、被拒绝或 QPE资格为零时报告显示矛盾；反方向不强推结论：
资格为真且 alpha为零可能是低于色标阈值，不自动判错。

## 执行步骤

1. 在独立审核分支应用补丁、运行专项和全工程验证。不要直接覆盖带有未提交修改的服务器。
2. 固定原始体扫、所有过去/邻站输入、真实配置、辅助资源和同一 renderer1.2.0。
3. 先做 geometry-only：用6.1配置把 residual_repair 的两项开关设 false，记录新配置SHA；
   用相同源码下的旧V6控制测量与动作一致性。该组不能声称重现旧的加载缺陷。
4. 分别打开 local_narrow_width、footprint_peripheral，最后打开两者。每组产生独立配置和清单。
   不在同一网络验收清单中混入不同调参版本。
5. 比较候选变化、保护原因、原始父门、确认/隔离/保留和天气负例。未提供真实标签则只报告工程统计。
6. 再决定是否部署候选，维护全部旧产物和原始数据。启用不等于发布准入通过。

## 只读逐门取证（105上本地执行）

先从实际 API 与对象存储导出独立目录，保留原 `image-provenance.json`，每个单站视图对应：
原始640PNG、该图层的单个JSON对象、对应QC Zarr。不要导出整个.env或凭据。
获取文件/目录摘要可用：

```bash
uv run --project algorithms python - <<'PY'
from rainpulse_algo.radar.qc_engine.forensic_io import file_digest, directory_digest
print(file_digest('/export/original.png'))
print(file_digest('/export/layer.json'))
print(directory_digest('/export/qc.zarr'))
PY
```

按 `configs/schemas/qc-forensic-v1.schema.json` 组装清单。结构示例（所有大写占位值
必须替换；坐标由原PNG实际选取，不是给出的真实案例ray/gate）：

```json
{
  "schema_version": "rainpulse.qc-forensic.v1",
  "panels": [{
    "panel_id": "z9591-original-panel-id",
    "analysis_id": "ACTUAL_ANALYSIS_ID",
    "expected_qc_asset_id": "ACTUAL_QC_ASSET_ID",
    "qc_zarr": {"path": "/export/qc.zarr", "sha256": "ACTUAL_DIRECTORY_SHA256"},
    "png": {"path": "/export/original.png", "sha256": "ACTUAL_PNG_SHA256"},
    "layer": {"path": "/export/layer.json", "sha256": "ACTUAL_LAYER_SHA256"},
    "sweep": "sweep_000",
    "samples": [{"label": "人工选定的残留点", "role": "unlabeled_review", "row": 0, "column": 0, "radius_gates": 4}]
  }]
}
```

注意样例中的(0,0)只是结构占位；不能当作真实残留位置。sweep也必须按图层实际值选择。

```bash
uv run --project algorithms python -m rainpulse_algo.radar.qc_engine.forensic_export \
  --manifest /export/forensic.json --output /new-output/forensic-report
```

输出目录必须尚不存在。一次清单上限24面板、每面板20点；半径最多20门。
工具只读，默认每个目录最多4GiB/200000文件；超限应先采用可控的单体扫冻结导出，
不能随意去掉内容校验。比较同体扫的08:40/08:45必须分别引用实际发布的QC资产，
而不是把同一个路径复制两次就证明两次运行相同。

## 冻结上下文对照

复用 `qc-network-case.v1` / `qc-network.v1`。case中的 task 应选 V6基线配置；
baseline_profile为V6，candidate_profile为6.1，flags相同，所有current/过去/邻站URI与清单精确对应。
新增 `resource_environment`：只接受下面3种资源，path可以是审核后的绝对本地路径。

```json
{
  "RAINPULSE_RADAR_CONFIG_DIR": {"path": "/frozen/radars", "sha256": "目录摘要"},
  "RAINPULSE_ANCILLARY_CONFIG": {"path": "/frozen/ancillary.yaml", "sha256": "文件摘要"},
  "RAINPULSE_ANCILLARY_ROOT": {"path": "/frozen/ancillary", "sha256": "目录摘要"}
}
```

没有的资源省略，不能编造。未列入清单的这3个环境路径在本次离线比较期间被清空，结束后恢复。
这避免同一清单在不同机器上偷偷使用不同地形资源；旧清单默认{}，不再继承ambient挂载。
读取前后校验资源内容。该工具按case串行执行，不应把其环境作用域包装为共享进程并发服务。

```bash
uv run --project algorithms python -m rainpulse_algo.radar.qc_engine.network_compare \
  --manifest /frozen/network.json --output /new-output/shared \
  --context-mode shared_baseline --save-bundles --inspect-ray 0

uv run --project algorithms python -m rainpulse_algo.radar.qc_engine.network_compare \
  --manifest /frozen/network.json --output /new-output/per-profile \
  --context-mode each_profile --save-bundles --inspect-ray 0
```

`--inspect-ray` 是原生射线序号；样例0不是本案真实残留。最多选择6条，避免研究报告失控。
shared_baseline/shared_candidate对两个core复用同一套prepared数组，用于控制变量；each_profile
各自准备证据，用于当前源码Worker路径检查。报告分别保存两者资源、准备数组SHA和耗时。
保存的QC Zarr也嵌入这份上下文身份。不能把它们混称历史完全复现。

无标签不输出skill；有标签的准确率只在可信二类标签上计算，未知/混合不能自动算正确。
隔离率与确认召回分开。网络准入仍检查真实、独立过程和逐站最差组，不会因合成demo通过而PASS。

## 性能与恢复

局部角宽是两次 O(ray×gate) 扫描；复用原算法后增加一次局部分支，所以实际增量应实测。
外围仍是固定有限偏移集合，足迹计算只是附加数组运算，不产生无界迭代。
全部诊断字段会增加内存和序列化开销；本站配置及DEM manifest哈希不等于逐作业全量加载DEM。
离线冻结目录哈希是额外I/O，不能计入QC核心；线上仍按核心、上下文、序列化、上传分别报告。

compose覆盖文件 `deploy/docker-compose.qc-residual-v61.yaml` 使用同一renderer1.2.0。
需要同步规划器QC配置和Worker镜像标签，确认旧任务排空/版本隔离后再重算。
资源挂载沿用现场真实配置；覆盖文件不自动创建资源、不部署原生Go、不访问凭据。
回退同时恢复原镜像、QC配置与规划器环境，保留最后一份可用结果；不要删除原数据或历史唯一产物。

## 当前没有完成的工作

没有访问105、下载本案真实基数据/上下文/PNG清单，没有对真实Z9591/Z9598产生新的ray/gate结论。
没有独立人工标签、雨量站、真实PPI/浏览器和目标硬件验收。
没有构建原生bRopo，也没有实现原生RDD、SWAN、IQ处理或训练新模型；它们不计入本次成熟算法效果。
当前复用的Py-ART2.2.5/wradlib2.9.5已由真实库/Worker回归调用，新增局部逻辑明确是项目补充。
