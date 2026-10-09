# qc-clearair-recurrence-v1：近站弱杂波晴空重现排除（合同与集成设计）

状态：**已验证候选；引擎接缝已接线（默认关闭，2026-10-09 第四轮），worker 资产/锚点窗供给与上线门槛待完成**。研究验证证据见 `docs/NEAR_SITE_RECURRENCE_SCHEME_20261009.md` 与 `docs/NEAR_SITE_QC_VALIDATION_GALLERY_20261009.md`；算法实现 `algorithms/rainpulse_algo/radar/qc_engine/clearair_recurrence.py`（含单测 `algorithms/tests/test_clearair_recurrence.py`，9 PASS / Ruff PASS）。2026-10-09 第四轮已落地：profile 配置块（默认 None，缺席时参数哈希身份不变）、runner 扩展接缝（volume_review/s_morphology 之后调用 `clearair_recurrence.review_result`，背景与锚点体扫经 kwargs 注入、缺失即弃权不改任何数组）、`CLEARAIR_RECURRENCE_EXCLUDE_MASK` 经 optional_qc_fields 自动导出；单测+集成测试 13 项（`algorithms/tests/test_clearair_recurrence.py`）。剩余：worker 侧背景资产读取（`background_asset_uri/sha256`）、锚点窗体扫装载（现有 temporal context 仅 3 体扫 ±15 分钟，比验证期 2h 桶紧，启用前须按真实窗语义重放验证）、summary schema 追加、QC_FLAGS 数值位、replay 一致性。

## 1. 背景资产（qc-clearair-recurrence-v1）

每个 S 站一个资产（npz），由离线/定例任务从**晴空时段**（人工确认无降水，或未来滚动判定的无雨窗口）体扫聚合：

| 数组/属性 | 类型 | 语义 |
|---|---|---|
| `RECURRENCE_FRACTION` | f4 (360, 400) | 足迹格（1° 方位 × 250 m 门，0–100 km）最低反射率切层的检出占比；NaN=无样本 |
| `STATION` | str | 站号（小写） |
| `SOURCE_VOLUMES` | int | 参与聚合的晴空体扫数（≥1） |
| `BUILT_AT_UTC` | str | 构建时间（UTC，RFC3339） |
| `CONTRACT` | str | 固定 `qc-clearair-recurrence-v1` |

检出语义与现行 normalized 一致：`DBZH_RAW_CODE >= 5` 且非 absent sentinel（等价 finite DBZH）；缺测不计分母、不推断无回波。资产带源体扫清单与 SHA（实现时随资产发布，本表为最低字段）。

## 2. 判据（v3.1，已冻结）

帧时刻 T、站 s：

1. `anchor_counts`：活动窗（生产实现为滚动真窗 ±90 min；验证期为包含 T 的 2 小时桶）内每体扫取各切层锚点门（`DBZH≥15 & SNR≥15 & 0.97≤RHOHV≤1`，全部有效）在足迹格的逐体扫最大命中，跨体扫累加（uint16）。无 ZDR 站点数据时允许省略 ZDR 条件（保护偏保守）。
2. `prone = RECURRENCE_FRACTION ≥ 0.5 ∧ dilate(anchor_counts ≥ 2, 3×3) == 0 ∧ 连通域 ≥ 8 格`（方位接缝连通）。
3. 切层排除：`weak = DBZH_RAW ∈ [-10,5) ∧ REFLECTIVITY_ELIGIBLE_FOR_CR`；排除 = `weak ∧ prone(足迹) ∧ ¬protected ∧ 切层仰角 ≤ 6.0°`；`CF_CLASS==1`（天气兼容）额外要求 `RECURRENCE_FRACTION ≥ 0.7`。
4. `protected` = 既有全部生产保护并集（CF_HARD_WEATHER / CF_LOCAL_WEATHER / NP_WEATHER_PROTECTED / RDR_UNKNOWN_PROTECTION / SRC_REVIEW_WEATHER_PROTECTED / 后续新增保护），语义为“该模块永不越过任何既有保护”。
5. 效果仅改 CR 资格（与负值域候选同一集成点）；RAW、QC 值、QI、QPE 字段不变；原因位 `CLEARAIR_RECURRENCE_EXCLUDE`（字符串常量，数值位分配在接线时向 flags 定义追加，走 flags 版本升级流程）。

## 3. 接线点（待实施清单）

1. `qc_engine/profile.py`：新增 `clearair_recurrence` 配置块（enabled 默认 **false**、参数即 §2、背景资产 URI/SHA 解析，复用既有 ancillary 资源解析路径 `qc_resource_paths`）。
2. `qc_engine/broad_source.py` 或 `finalize.py`（按 WIP 收敛后的最终结构）：在 CR 资格写出处调用 `sweep_exclusion`，从 temporal context 取活动窗体扫矩量计算 `anchor_counts`；无背景资产或窗口体扫不足时整模块旁路并记录原因，不 fail-hard（“增强失败不得阻塞基线”）。
3. `qc_zarr.py`：导出 `CLEARAIR_RECURRENCE_EXCLUDE_MASK` + 摘要计数（进入 completion summary 契约前先补 schema 变更）。
4. 回放/重算路径 `replay.py` 同步支持，保证重放一致性。
5. 配置示例与验收命令写入 Makefile/README 前先补集成测试（mock 体扫窗口 + 合成背景，断言资格变化与保护不可越过）。

## 4. 上线门槛（沿用项目验收纪律）

- 全天 110 帧离线重放（分桶聚合已具备，z9591/z9595/z9598/z9599 四站）：≥5 dBZ 零变化、组合层消失格与弱门排除量与验证期同量级、天气支持代理重叠 ≤ 0.1%。
- Sep18 晴空回放：站心环状回波落在 prone 区域的比例不低于验证期（14.5%/56.5%/56.3%/z9595 伪晴空）。
- z9595 依赖伪晴空参照（当天无雨窗口）时，必须先升级为真实晴空背景资产方可默认启用；z9593（对照站）不启用。
- 启用走配置灰度（先 shadow 输出原因位不改资格，复核一轮后再改资格）。

## 5. 已知边界（不因上线而消失）

无独立真值的误删率未闭环；生物散射按非降水处理；混合降水门被锚点保护保守保留；晴空背景有季节性，需滚动重建（v1.1 长期晴空概率图能力）；锚点窗在验证期为 2h 桶近似。
