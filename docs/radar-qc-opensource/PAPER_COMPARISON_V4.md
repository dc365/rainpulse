# V4：V3—AFL—RDD参考—融合对照

基线：`50781c2fcb924e722d7466caadef3f66b71b519e`（含已合并 V3）。

## 本次交付是什么

这是一套可运行的同输入对照工具和独立融合候选，不覆盖 V3，不自动部署、重算或升级业务准入。继续使用锁定的 Py-ART 2.2.5 / wradlib 2.9.5。源码中的 `operational_eligible` 仍必须为 false。

| 路径 | 当前状态 | 不能冒称的内容 |
|---|---|---|
| V3 | 原配置、原算法保留，真实调用 | 不是用 V4 的结果模拟 V3 |
| AFL 参数化 | 已实现文献公式、图示隶属函数近似和显式候选阈值 | 不声称是作者完整原代码、SWAN/国家局官方实现 |
| AFL-local | 在固定局部距离窗上使用单独的覆盖率与尾部参考 | 不是原文全径向定义 |
| RDD | 严格的外部逐门参考接入；没有结果则未运行 | 本次没有取得完整公式/决策树，未实现原文 RDD 原生执行器 |
| 融合 V4 | V3 结果加论文候选复核；已接入现有 QC Worker/序列化 | 不是把几种算法掩码取并集直接删除 |

**RDD 仍是本次未完成的原算法复现项。** 已核对出版社摘要和备用站点，正确文章编号为 20220603；全文入口返回维护、404 或浏览器验证页面。本次不猜测 CZ/CD/ME/SE 定义或决策树。已有可信 RDD 实现的外部结果可以直接按本文接口加入；后续取得全文后另做原生执行模块与原文单元测试。

## AFL 来源、公式与近似边界

来源：文浩等，2020，《基于模糊逻辑的新一代天气雷达径向干扰回波识别算法》，气象学报78(1):116–127。DOI `10.11676/qxxb2020.010`。公开全文：<https://html.rhhz.net/qxxb_cn/html/2020010.htm>。

`afl.py` 实现：

- R_REF：原始有效反射率门数 / 当前完整几何径向门数，单位百分比。
- B：Z − 20 log10(r_km)，不称为实测接收功率。
- D_B：有符号的 B − 尾部参考均值，不取绝对值。
- T_DBZ：11对相邻反射率差的平方平均，单位 dB²；不是反射率标准差。
- S_PIN：11个中心点的左右绝对变化均值超过 Z_thresh 的次数。
- T_DBZ > 3 dB² 使用粗糙型隶属函数；权重采用原文表2第8组，平滑型(2,2,1,0)、粗糙型(1,2,1,1)。

图4折线断点由原图近似读取，**不是原文未刊出的精确参数表**。`spin_jump_db=2`、`decision_threshold=0.6` 是原文未给数值时的显式工程假设；全部写入 profile 和每次报告。缺测尾部采用末尾几何10%门中的有效均值、至少80%支持；这是明确的缺测策略，不把缺测当作无雨。连续11对纹理、13门范围的跳变统计若跨缺口则不可用。

AFL-local 的默认距离窗为50 km，边缘没有完整窗口就不可用，不截短窗口伪造更高覆盖率。其字段以 `AFL_LOCAL_` 开头，始终与全径向结果分开报告。源码、字段元数据与 UI 均将分数称为未校准隶属分，而不是概率。

## 融合的实际判定

1. V3 先独立完成门级与对象判定。融合比较要求 V3 与 V4 除版本身份和 literature 配置外的参数完全一致；单次离线对照使用同一冻结 V3 上下文，避免隐含调参和未来资料优势。
2. AFL、AFL-local、外部 RDD 候选属于同一个反射率结构证据族；三者同意不是三份独立物理证据。
3. 新增确认需要实际观测的局部连续结构，并有足够 SNR、原始极化矩和联合异常。高相关不是绝对免检，但高相关情况下必须同时满足相位异常与 ZDR 异常，不能仅凭形状剔除。
4. PHIDP 使用周期相位差，避免359°→0°伪大跳变；当前门无相位时不能因邻域有相位而获得该门证据。仍未引入完整融化层分类和独立极化校准，所以必须做强对流保护试验。
5. 单项异常或过去体扫支持可产生疑似隔离，确认与隔离分别计数。时间规则使用整数命中数（默认至少2个独立有效样本且至少2票），不以0.67近似2/3。
6. V3 已拒绝或隔离的测量不会被新层恢复。强污染和天气共存时，不因邻站有雨而恢复本站数值。无充分证据时不能新增确认。
7. 新增输出 `PAPER_CONFIRMED_ADDITION_MASK`、`PAPER_QUARANTINED_ADDITION_MASK`、`PAPER_BASELINE_*`、`PAPER_DECISION_REASON`。新原因用独立字段，不改变 V3 已用完的位码解释。VALID_MASK/DBZH_RAW保留原始观测身份；DBZH_USABLE/QPE_ELIGIBLE_MASK阻断不可信值。新判定不修改旧对象ID来伪造命中。

这是一版有意保守的**参数化候选**。局部分段、相位和极化阈值都需真实资料评价；没有保证比 V3 更好，更没有进行 IQ 级干扰分离或降水恢复。

## 从现有真实回放清单开始

先按 `RFI_OBJECTS_V2.md` / `replay.py` 现有方式冻结真实 V3 task JSON，以及它引用的当前/过去/邻站 normalized Zarr。输入不能从截图反推。若现场有地形/静态先验，其本地资源与环境配置仍须按既有 runbook 准备；缺少资产不应伪称齐全。

```bash
uv run --project algorithms python -m rainpulse_algo.radar.qc_engine.paper_manifest \
  --replay /path/to/v3-replay.json \
  --output /path/to/comparison.json \
  --fusion-profile configs/qc/fujian-qc-paper-fusion-v4.yaml \
  --process-id fujian-20260828-development --partition development

uv run --project algorithms python -m rainpulse_algo.radar.qc_engine.paper_compare \
  --manifest /path/to/comparison.json --output /path/to/new-comparison-output \
  --inspect-ray 120 --save-bundles --save-images
```

运行后 `report.json` 可在既有 `/qc-review` 页面本地打开；`images/index.html` 显示同几何同色标原始、V3、AFL、AFL-local、RDD（无结果时明确未运行）、融合图。候选预览不是QPE产品，不能与最终拒绝混为一谈。PNG不能代替原始掩码评分，透明也包含低于绘图阈值的值。角向缺口不做整圈外推。

`--save-bundles` 保存两套经过序列化校验的完整 QC Zarr，可用于独立下游回放，**不会发送完成事件或发布业务产品**。报告最多50MiB、径向最多12条、显式数值总量最多200万；超限报错，不默默截掉难例。既有输出目录不能覆盖；失败删除的只有本次新建临时目录。

批次清单为 `rainpulse.qc-paper-batch.v1`，`cases` 是每份 comparison manifest 的 path/sha256 列表。每批1–8个任务，串行执行：

```bash
uv run --project algorithms python -m rainpulse_algo.radar.qc_engine.paper_batch \
  --manifest batch.json --output new-batch --inspect-ray 120 --save-bundles
```

预检拒绝相同 radar_id/scan_id 或相同当前工件 SHA 重复计分；同一 process_id 不得分到开发与验证两边，当前/过去/邻站工件也不得跨声明的分区。它检查的是已提供身份与哈希，不声称能够辨认伪造身份的源数据。汇总按案例展示，不生成未经说明的总分或自动推广结论。

## RDD 外部结果格式

`configs/schemas/qc-rdd-reference.schema.json` 规定完整元数据：algorithm、source_doi、implementation_revision、parameters_sha256、review_record、input_sha256、geometry_sha256、sweep、ordering、granularity、interpolated、mask_file、mask_sha256。

掩码为非pickle `.npy`，dtype uint8，shape `[ray, gate, 2]`，最后一维分别是 available 与 candidate。顺序必须为原始射线/距离门顺序。`geometry_sha256` 使用 `rdd_reference.geometry_sha256(adapt_sweep(...))` 计算，包含排序到源索引的映射。导入将恢复正确的内部排序。

comparison 清单中加入 `rdd_references: {sweep_000: {path: rdd.json, sha256: ...}}`。摘要验证只证明身份/内容一致，**不证明外部代码来自作者**；review_record必须说明人工复核记录。参考不可创建新观测，不接受射线级结果冒充逐门标签，不使用插值后的反射率。不提供参考时，RDD命中数和分数为null，available为false，不能显示为零检出成绩。

## 标签和验收

标签沿用 qc_metrics 的原始门数组：0不确定、1可信气象测量、2确认干扰、3天气/污染混合；冻结文件摘要，不能把 V3/AFL 的输出直接当标签。AFL不可用门仍在原始冻结评价域中，单独报告不可用数量，不剔除后提高分数。没有标签时不计算召回、误拒或保真率。隔离/定量不可用的测量指标单独统计，不冒充确认检出。

已有08:15、08:25/30、08:35/40/45属于开发回归，重复物理体扫不可增加独立样本数。必须检查全部可用仰角、强回波、弱雨边缘、海区、缺矩和混合污染。未参与调参的过程用于独立验收，雨量站验证QPE偏差。无真实数据不作效果提升百分比承诺。

## 候选接入与回退

`deploy/docker-compose.qc-paper-fusion-v4.yaml` 是显式覆盖文件；不会更新 native Go planner、排空队列或部署服务。只有完成冻结回放后，才按既有协调切换流程让规划器与QC Worker同时选择新 profile 和其文件 SHA，下游使用配套flags v2与Hybrid/mosaic/QPE/diagnostics配置，旧成功结果保留回退。外部 RDD 参考仅用于离线对照，本次未给在线任务增加任意文件注入接口。

V3和V4的参数身份不同；不得将不同版本体扫混拼。新字段追加，不更改已有flag位。回退选择旧profile/镜像和旧成功分析，不覆盖原始资料，不把“代码合并”当作历史产品重算完成。

## 待完成与明确未做

- RDD原生算法：缺全文公式和决策树；当前仅接口及验证，不算算法完成。
- AFL作者精确图示断点、Z_thresh和最终判定阈值核实；目前全部显式记录近似/假设。
- 当前展示案例的真实Worker/全链路回放、独立标签、雨量站与现场P95/RSS测试。
- SWAN同文件、同仰角的合法可获得参考结果；不存在“已接入SWAN”的声明。
- 学习模型、在线校准、IQ处理、自动推广、新的融化层/衰减订正主链均未引入。

## 文献来源

1. AFL全文：<https://html.rhhz.net/qxxb_cn/html/2020010.htm>，图4与式(2)–(9)、表2。
2. RDD出版社页面：<https://rdqxxb.itmm.org.cn/cn/article/doi/10.16032/j.issn.1004-4965.2022.073>。
3. RDD备用出版社摘要：<http://rdqx.ijournals.cn/ch/reader/view_abstract.aspx?file_no=20220603&flag=1>。

仓库不附论文全文或原图；保存必要引用与参数出处，不将数学公式复现冒充官方源码。

## 验证与回归命令

```bash
make test-qc-paper-comparison
uv run --project algorithms pytest algorithms/tests configs/tests contracts/tests
make test-go test-web
make lint build
```

新增专项检查实际调用已安装的 Py-ART/wradlib，并验证公式数值、缺测与字段可靠性、重复任务工件一致性、假参考结果拒绝、冻结输入身份、固定标签分母、批次去重/分区隔离与图像几何。原生 RDD 未运行状态不能被解释为零检出。

复核还修复了两个已有结构测试中的 Go 控制面迁移旧路径；保持原断言内容，未移除门禁。原开源算法警告捕获改为显式 always，避免进程全局警告注册状态改变 qc/summary.json 的字节内容；这仅稳定诊断，不改变数值算子或原生数据。重复测试在不同 warning filter 下验证工件一致。

工程测试不代替真实体扫和独立降水标签验收。交付包包含实际运行日志，现场运行需另外记录输入、代码、配置、镜像及结果摘要。
