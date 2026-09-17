# 2026-09-17 审核优化增量实现与现场验证手册

本轮基于 `3b8035104061cfe2db9da654006cffe0007665c8`，扩展版本 `qc-review-20260917-v1`。它是工程候选，不取代现有 v1.1 系统实施基线，不宣称通过实际雷达过程验收，不修改 Go/React 职责边界。

## 1. 接入位置与动作

`profile.py` 增加显式子配置与扩展版本；关闭时从参数哈希输入剔除新增空字段。`broad_source.py` 的原始留出拟合和强长分支保持原条件，新分支在同射线留出拟合可用后、宽角度约束之前记录目标匹配。目标和相邻 guard 块不进入参考拟合；配方记录的是 `reference_eligible_blocks`，即允许作为参考的几何块，**不冒充每一块实际都有足量观测**。已有拟合的实际支持判据仍由旧函数执行。

`source.py` 复用原 `narrow_source`，并合并 `multiscale.py` 形态候选。候选、参考可用、目标匹配、合格隔离建议分别输出。新射线建议在旧边缘/近站扇区分支完成后才合入 BWS，不能反过来成为其扩张种子。仍由现有 P2 统一隔离、天气保护和预算路径决定是否扣留，不新增“已确认干扰”。

非降水分类位于 `runner.py` 既有决策融合之后、相位处理与最终资格投影之前。`NP_QUARANTINE_MASK` 独立于 RFI：`QC_ACTION=1`、LOW_QUALITY、全部矩值信任和 QPE 资格归零、`DBZH_USABLE=NaN`，保留原始 DBZH 和 VALID_MASK。新确认数恒为 0，混合/天气保护门不能产生新动作。新增隔离超过损失预算时保留隔离并要求复核，不为“达标”恢复疑似污染测量。

关闭扩展时各接入点应不改变原输出；审计模式只新增诊断。此兼容性已对局部模块测试，但完整旧版本配置/Worker/Zarr 字节级回归必须在项目环境补做。

## 2. 算法与缺资料降级

形态初始尺度为 5/10/20/50 km；短片段至少 1.5 km、关联间隔不超过 3 km、单个关联身份跨度不超过 50 km。侧向反差先保留连续分数，再做候选门槛，不把 DBZH>35 改成一个更低的全局删除条件。并排束使用束外实际侧翼，缺侧翼或方位缺口不推定反差。以上参数均为候选实验设置。

断续线仅为原始实测片段赋身份，缺口既不填值、不赋候选身份，也不执行隔离。实测天气/测量冲突阻断身份关联。对象上限触发时明确弃权，不做截断式选择性删除。短对象不套用长距离 RANSAC 外推，本轮保留局部轴向一致性；未重新校准原 RANSAC 几何模块。

分类分为未定、天气、固定地物、异常传播、海杂波、生物、混合和原始缺测；有限 DBZH 低于配置无雨门槛有单独类别，不能解释为设备未检出。反射率纹理与 Gabella 合为同一证据族，不能重复投票。默认只有“可靠历史 + 当前多普勒 + 原始时间复现/可比垂直负证据”的固定地物路径允许实验隔离，且须经过天气/增强/混合保护。没有背景或可靠多普勒/过去体扫时可以整片弃权，这不是程序漏执行。

AP 与海杂波具有独立规则路径，但目前没有自动提供海陆、低波束或可比垂直负证据的新增在线加载器。单填旧 `coastline_asset_uri` 不能激活这些能力。生物回波始终仅诊断；本轮没有本地训练模型、权重或真实标签。

`temporal.py` 使用已验证的过去原始体扫，要求站点、切面和原生坐标完全一致。既有 QC 标记不作为标签。运动补偿后的稳定度未实现，明确输出不可用；不能用固定坐标持续性代替该证据。`verified_vertical_absence` 是严格比较接口，要求实际观测、有效未检出、覆盖可比和检测限；没有自动从“高仰角 NaN”推导低层杂波。

## 3. 版本化背景资产

统一核心为 `review/background.py::build_background`。两个历史入口通过 `canonical=True` 显式进入此核心；历史默认实现保留用于旧结果复现，**没有偷偷降低其样本标准，也没有将 v1 资产改名冒充 v2**。

每个样本必须提供这些实数数组：`dbzh[ray,gate]`、`azimuth_deg[ray]`、`range_m[gate]`、`elevation_deg[ray]`；以及严格二值 `observed_mask`、`no_echo_mask`。可以另外提供实际 `vr`、`sw`。未检出且观测有效的门允许 DBZH=NaN，进入分母；缺测门不进入分母；已检出而 NaN 非法。先核实实际解码协议，不能从 NaN 猜测两种状态。

元数据必须包括雷达/配置/扫描策略/硬件版本、切面、scan_id、UTC 观测结束时间、原始导出文件 SHA-256、`reviewed_clear_air=true`、`case_category=clear_sky`、人工或正式审阅记录 `clear_air_review_id`。同一文件、同一扫描/切面、同一观测时次不能被当成独立样本重复计数。按 UTC 时间顺序提供样本；不同扫描策略、配置或硬件必须分资产。

默认要求每门至少 20 个有效观测日期和 200 次有效观测。每门输出有效/回波/无回波计数、日期数、日均衡频率、频繁出现日期的 Wilson 下界、DBZH/VR/SW 均值/样本标准差、精确几何和来源元数据。下界描述“多日重复性支持”，不是经过标注校准的污染概率。季节/时段需要分别构建、选定不同资产；没有自动跨季节权重模型。

构建命令（所有输出不覆盖已有文件）：

```bash
python scripts/qc_review_20260917.py build-background \
  --manifest /data/clear-air/site-samples.json \
  --output /data/assets/site-background-v2.npz \
  --receipt /data/assets/site-background-v2.receipt.json

python scripts/qc_review_20260917.py build-registry \
  --manifest /data/assets/background-registry-input.json \
  --output /data/assets/fujian-background-registry-v1.npz \
  --receipt /data/assets/fujian-background-registry-v1.receipt.json
```

样本清单每条含 `npz_path`、`input_sha256` 和 `metadata`；SHA 是该 NPZ 文件字节哈希，NPZ 内为上述数组，禁止 pickle。注册表输入每条含 `asset_uri` 和背景构建产生的 `receipt_path`。把各站背景和注册表上传到既有对象存储后，所有同代 QC 配置绑定同一注册表：

```yaml
static_ground_clutter:
  asset_uri: s3://YOUR_BUCKET/assets/fujian-background-registry-v1.npz
  asset_version: rainpulse-clutter-registry-v1
  asset_sha256: REPLACE_WITH_REGISTRY_ARRAY_CONTENT_SHA256
```

这里 `asset_sha256` 是回执的 `asset_content_sha256`（排序数组内容哈希），不是 ZIP/NPZ 文件哈希或 S3 ETag。修改绑定会改变新配置哈希，应重新冻结任务。先生成 child profile，再按既有模型校验流程完成资产绑定；不要把模板占位符用于任务。

注册表为各 `(radar_id, radar_config_version)` 选择唯一背景 URI/哈希。加载时校验当前雷达/配置/几何、禁止包含当前扫描或未来样本，记录使用回执。原始体扫若缺硬件或扫描策略字段，只接受显式注册表绑定到不可变 radar_config_version，并在回执区分该来源；不能据此声称自动验证过硬件真实状态。物理配置变化必须同步更新配置身份。

## 4. 实际来源链和同产品比较

新增 Hybrid 输出保存真实映射的 `SOURCE_RAY`、`SOURCE_GATE`，而不是事后最近邻推算；现有 SOURCE_SWEEP 保留实际切面。Review 拼图保存实际用于线性 Z 融合的每站权重、Grid/QC asset_id 和参数哈希，验证权重之和与 contributor count。旧权重公式不变。缺测网格索引为 -1，权重为 0。

先完整重算同一冻结任务组的 QC、Grid/Hybrid、Mosaic、QPE，再导出不可变产物进行审计。只更新单站 QC 或诊断图不能证明新拼图已经生效。

```bash
python scripts/qc_review_20260917.py digest /data/exports/qc-asset
python scripts/qc_review_20260917.py audit-lineage \
  --manifest /data/exports/lineage.json --output /data/reports/lineage-audit.json
python scripts/qc_review_20260917.py trace-cell \
  --manifest /data/exports/lineage.json --mosaic-id ACTUAL_MOSAIC_ASSET_ID \
  --row 120 --column 160 --output /data/reports/cell-120-160.json
```

审计清单从实际 Zarr 根属性读取 asset_id 和父资产，不信任清单另外声称的版本；每个导出目录提交预期内容哈希。归一化体扫是明确的上游边界，原始二进制不必由此工具重新解码。`analysis_time` 为 UTC；QC/Grid 观测时次不得超出配置年龄/未来范围。`expected_qc` 按站明确这次希望使用的 QC 资产 ID 和参数哈希。若清单遗漏上游、混入旧 QC 或哈希不符，失败；不猜测当前线上资产。

格点追溯读取实际权重，定位真实 Grid→切面→ray/gate，核对有效性、QPE 资格和反射率，再以线性 Z 重建拼图值。没有实际记录权重/原始索引的旧资产必须重算，不能从截图猜。QPE 的支持仅限资产依赖审计；本轮没有新增逐雨量格点反解算法。

同产品比较另外提供两侧字段、聚合方式、取样层级、单位、显示门槛、色标、网格哈希、分析时次。Composite 垂直最大值与 Hybrid 近地分析不能当同一产品。缺比较定义时 `same_product_comparison_proven=false`，即使来源链一致也不能宣称对照实验有效。没有新增组合反射率生产器，也不改 QPE 输入为最大反射率。

## 5. 必须补做的完整环境验证

本地交付测试不包含完整项目依赖，不能直接批准切换现场服务。Codex/现场依次执行：

1. `apply_review.py --check` 在精确基线工作树通过，再应用。检查 `git diff --check`、项目 Python 格式/静态检查、全套 QC、Hybrid、Mosaic、Zarr 契约测试；Go/React 未改逻辑，仍按项目受影响契约要求执行对应检查。源字段附加内容不能使完成事件超过现有总线限制。
2. 使用真实固定输入，冻结旧配置与审计子配置；对已有测量、动作、资格、QI/flags 验证新模块只读行为。关闭整个扩展时，特别校验旧配置参数哈希与序列化产物保持兼容。
3. 构建独立晴空背景；不能用待评估降水过程、旧 QC 输出掩码或同一小时多扫伪装多日数据。登记每个场景缺失的矩值、有效未检出语义和资产回执。
4. 按四站、按天气过程冻结基线与隔离实验，重算整条下游链。测试无降水杂波、弱雨、强对流、小单体、海上降水、地物与天气混合、缺极化、远端稀疏和扫描缺口。检测之外必须报告天气误删与可定量覆盖损失。
5. 追溯红框实际格点，检查真实贡献站、仰角、波束高度、原始门、动作和权重。比较同定义产品；报告逐站最差情形，不以平均删除面积替代气象验收。

通过前维持 `operational_eligible=false`；缺任一现场资料时记录“未评估/弃权”，不得生成“已通过”示例报告。源码/配置变更、资产建设和真实过程验收是三个独立状态。
