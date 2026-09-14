# V6：细线、断续残留与观测范围复核

## 本次基线和目标

基于已合并 V5 的 `e7a835fe567d8b751018d3869e8d5af0464f0a86`，其完整源码树为
`984fbb6c3063a15b8dbf2d47b39d1b0216b4e735`。本次在隔离工作区实现，不部署、不回算现场、不改原始资料。

目标不是把 PPI 清成空白，而是补齐当前 V5 对细线和碎片的能力，保持确认污染、疑似隔离、原始缺测和天气存在四种语义。全部阈值仍是工程候选，`operational_eligible=false`。

| 步骤 | 当前实现 | 交付验收证据 |
|---|---|---|
| 1. 追溯残留源门 | 统一 PNG 与像素追溯采样，检查角度缺口、重复方位和距离边界 | PPI 数值测试、实际 QC Zarr 像素追溯测试 |
| 2. 长强门段关联 | 对象身份允许跨越有限的未决有效离群门；拟合支持仍只取合格门 | 250/500/1000 m 分辨率反例，健康间隔阻断 |
| 3. 窄/弱/短/远端通道 | 原始极坐标多尺度侧翼对比、有限断点关联、窄对象约束 | 连续、断续、粗糙、短、远端与天气负例 |
| 4. 原对象外围 | 只从冻结原始确认门或已合格的原始测量模型出发，单轮有限搜索 | 原始父门、范围上限、缺口阻断、无级联扩张 |
| 5. 散点复核 | 实际水平采样面积、原始观测密度、原始回波占比与局地噪声证据 | 不把删除制造的空白或原始缺测当晴空；强小单体负例 |
| 6. 版本与对照 | 冻结 V5 上下文的 V5/V6 Worker 对照、逐站最差组检查、独立消融开关 | Worker/本地回放字节一致，QC→Hybrid→Grid→拼图→QPE 回归 |
| 可选成熟窄线参考 | `native_bropo` 真实调用入口已写，不复制算法；没有本地原生扩展时明确未运行 | 本次只验证未执行/错误边界，**没有运行原生 Emitter/Emitter2** |

## 算法和保护边界

### 长强模型关联

`range_signature.py` 默认调用完全保留 V5；仅 V6 通过 `association` 开启新规则。
整体拟合合格后，允许对象身份跨过最多 1 km、累计不超过 10% 的未决门，且其模型偏差不得超过 8 dB。
原本合格的拟合门不再因为稀疏 +5 dB 有效离群点而被切成不足 80 km 的子段。离群点本身只记为待复核，不自动进入拟合支持或确认掩码；原始缺测仍然无对象门值。
明确天气支持的间隔阻断关联。原 V5 的 100 km 跨度、80 km 实测支持、增长与高值要求不变，弱线交给独立通道。

### 窄、弱、短、远端线

`narrow_spike.py` 使用原始 `DBZH` 及 1/2/4/8 度侧翼，只有左右都实际观测、拓扑可比时才形成横向差异证据。
缺少侧翼时可以依靠已有原始对象或可靠极化异常进入检查，不虚构侧翼晴空。候选需要至少 12 km 跨度、8 km 实测支持，短缺口最多 1.5 km、累计不超过 20%。实际角宽与径向长宽比限制同样适用。

粗糙性不再是所有窄线进入候选的否决条件。单凭形态只能形成候选；特别严格的反射率测量模型（足够实测长度、窄角宽、可靠侧翼、沿距残差）可以隔离，不能凭此新增确认污染。
局地可靠极化佐证仍可支持确认，但低 RHOHV、高反射率、某个方位或小面积都不是单独确认依据。没有设置 Z9591/Z9598 的方位删除列表。

### 外围复核

`residual_association.py` 的起点仅为原始 V5 确认径向门，或已通过的、非未核实数值平台的 V5 长强模型。
**不是所有 QPE 不可用/隔离门都能成为起点。** 最多搜索 1 km、1.5°，且横向实际距离不超过 1.5 km。
搜索使用原始值，在测量缺口或明确天气屏障处停止；新增隔离永不成为下一轮种子。
每个相容门保存原始 `V6_PARENT_RAY`、`V6_PARENT_GATE`，均为原始数组索引，不是排序后索引。

### 散点

`speckle_review.py` 先在 V5 剩余实测回波上找拓扑连通对象，但邻域统计始终来自原始观测。
使用水平采样面积（由真实距离、门距、方位和仰角近似计算，**不是波束体积**），而不是只按像素个数。
较小、较弱、原始邻域有足够实测、又有实际噪声/极化异常证据的门才隔离；同一小块其它门不被连坐。
缺测多的区域不能因为看起来零散就剔除。强回波、小但有明确天气支持的回波受到保护。
该方法仍可能误隔离真实弱天气，必须用独立标签评价，不能承诺零误删。

### 最终动作

V6 以完整 V5 为基线，只增加可追溯动作。原 V5 拒绝、隔离和 QPE 不可用都不会被恢复。
新增形态隔离降低质量并收缩反射率、极化矩、相位处理和 QPE 的可信域，不乘系数降低原始雨强。
新增确认必须满足可靠局地测量证据；未核实数值平台保持未决，不在此扩展中被晋升为确认。
所有 V6 开关关闭时，原 V5 的数值、动作、标志和质量完全相同。

## 显示版本独立切换

新诊断配置：`configs/diagnostics/qc-residual-diagnostics-v6.yaml`，
renderer 为 `radar-diagnostic-renderer-1.2.0`；其极坐标图层声明 `sampling_version=native-footprint-v2`。
新采样不外推到缺方位、扇扫外部或首个距离门之前，重复方位中心属于歧义，保持透明。
旧 1.0/1.1 renderer 仍使用原投影函数，旧配置及其 PNG 生成语义不偷偷改变。
因此比较 V5/V6 科学效果时必须使用同一渲染版本，或优先比较原始数值和资格掩码；不能把修复显示范围造成的差异算作算法召回提升。

```bash
python -m rainpulse_algo.radar.qc_engine.trace_pixel \
  --qc-zarr /data/frozen/qc.zarr --sweep sweep_000 \
  --size 640 --row 200 --column 310 --output /data/review/pixel.json
```

此命令是新采样规则下的 PNG 本地图像行列追溯，不是任意 WebGIS 坐标逆变换。对应 PNG 必须核实图层采样版本、图像尺寸、体扫和 cut。输出含原始值、动作、各 V5/V6 判据和父门，不连网、不发布。

## 版本与配套部署（本次未执行）

新增 `configs/qc/fujian-qc-residual-v6.yaml`：`qc-opensource-6.0.0` / `residual-v6`。
`deploy/docker-compose.qc-residual-v6.yaml` 选择 V6 镜像/QC 配置及新诊断配置，复用已贯通的 flags v2、Hybrid/拼图/QPE 配置。
切换前暂停规划、排空相关队列，并协调原生 Go 规划器与五类 Worker：

```text
RAINPULSE_RADAR_QC_CONFIG=.../configs/qc/fujian-qc-residual-v6.yaml
RAINPULSE_DIAGNOSTIC_CONFIG=.../configs/diagnostics/qc-residual-diagnostics-v6.yaml
RAINPULSE_QC_FLAG_DEFINITIONS=.../configs/qc/flag-definitions-v2.yaml
```

路径按宿主机/容器实际挂载调整。不要只改容器而遗漏宿主规划器；不要新建第二套现场服务。新建镜像标签6.0.0，不覆盖旧镜像。原始体扫、原 V5 配置、镜像和最后成功结果保留。
合并补丁不会部署，不会重算已生成图片。按现场现有全链路重算/离线冻结回放流程执行并核验产物身份，失败时不删除最后可用结果。

## 同输入验收及消融

现有 `network_compare` 同时支持相邻 V4/V5 和 V5/V6。V6 使用**完全相同的冻结 V5 输入上下文**比较，并验证内嵌基线的拒绝、隔离和最终资格与独立 V5 相等。

```bash
uv run --project algorithms python -m rainpulse_algo.radar.qc_engine.network_compare \
  --manifest /data/frozen/v5-v6-network.json \
  --output /data/review/v6-comparison --inspect-ray 0 --inspect-ray 120 --save-bundles
```

manifest 使用既有 `rainpulse.qc-network.v1` / `rainpulse.qc-network-case.v1`；baseline 指向 V5 原文 SHA，candidate 指向 V6 原文 SHA，任务也必须原样选择 V5。当前和过去/邻站输入清单及 SHA 全部冻结。不同参数组合分成不同套件，禁止把同时调参隐藏在升级中。

原报告中的逐站、距离段、资料能力分组继续采用固定原始/标签域。新增残余连续实测长度、片段数、最大反射率；无残留最大值为空而不是 0。确认召回与隔离覆盖损失分开，单站退化不能被总体平均掩盖。开发/合成样本、重复物理体扫、缺站、独立过程不足均不能证明真实准入。

建议冻结 Z9591 的08:15/08:20/08:35/08:40，以及 Z9598 的已改善过程和其它站干净天气；核实页面时间对应唯一物理体扫。模块消融顺序：全关闭→仅关联修正→再加窄线→再加外围→再加散点。每个配置必须另存并记录原文 SHA，不覆盖冻结版本。

```bash
uv run --project algorithms python scripts/demo_qc_residual_v6.py --output /tmp/qc-v6-demo
# /qc-review 打开 /tmp/qc-v6-demo/comparison/report.json
make test-qc-residual-v6
```

演示仅有合成测量，明确不是现场观测或 IQ 仿真；两个案例通过真实 Py-ART/wradlib、Worker 核心和 Zarr 校验，网络门禁必须仍为 INSUFFICIENT。

## 原生 bRopo：已实现调用边界，但未执行成功

`native_bropo.py` 使用上游公开 API `_fmiimage.fromRave` 和 `_ropogenerator`，分别调用 Emitter/Emitter2 并读取 classification；从不调用 restore/restore2。
第一版有意只接受完整、规则的360°实测 PPI，拒绝对尚未核验的扇扫/缺测语义给出假参考；显式周向填充后还原原索引。0.5 dB 量化仅用于参考输入，原始数据不改变，数值不能无声截顶。
使用显式 native-unit 参数、声明的源码 revision 和实际扩展二进制 SHA；源码声明不能替代构建审查。分类代码不是校准概率，也不是最终拒绝。

```bash
python -m rainpulse_algo.radar.qc_engine.native_bropo \
  --normalized-zarr /data/complete/native.zarr \
  --profile configs/qc/fujian-qc-residual-v6.yaml \
  --flags configs/qc/flag-definitions-v2.yaml --source-revision <已核验的40位提交> \
  --emitter-args -10 20 --emitter2-args -10 20 2 \
  --output /data/review/native-bropo
```

本环境没有 RAVE/bRopo 原生扩展，**本次没有完成其构建、运行结果或参数效果验证**。缺依赖返回 `not_executed_native_dependencies`，不输出零掩码冒充成功。V6 默认链路不依赖这个可选基线，不能把本地适配测试计作原生算法性能/效果通过。
参考接口已核对：
- https://github.com/baltrad/bropo/blob/master/pyropo/ropo_realtime.py （周向边界、分类与恢复分离）
- https://github.com/baltrad/bropo/blob/master/ropo/rave_ropo_generator.c （Emitter/Emitter2 native 参数）
- https://amt.copernicus.org/articles/15/261/2022/ （分型检测与前序处理导致碎片的研究）
本项目窄线模块是独立工程实现，不称为 bRopo/SPIKE/RDD 复现。

## 尚需现场完成

截图原始体扫未提供；真实污染召回、天气误拒/误隔离、雨量站 QPE 和跨天气过程泛化均未验收。未完成内网 Docker 联调、实屏 PPI 和目标机器 P95/内存测试；原生 bRopo 也未运行。V6 不是“效果最优”结论，不启用 SWAN3、未取得原文的 RDD、IQ 滤波或新训练模型。
