# S/X 多波段质控与组合反射率——首版实施说明

基线：`fb5fb2159aa7719dd10956f4e6728c54dafb021a`。本轮为独立候选处理能力，不改变旧 S 质控、QPE、六分钟预报、默认发布和原始资料。不部署服务器、不清理现场文件。未取得真实 X 设备基数据和标定资料；此文件不能作为业务验收证明。

## 1. 本版交付到哪里

已接通：现有 `/admin` 新建重算 → `x_qc` / `sx_composite` 预检查 → 原有 operations 的冻结计划、持久任务、NATS 引用、领取租约、日志、取消检查点、重试与产物登记 → 新 `multiband` Python 适配器 → 独立候选数值与图件。

`x_qc`：每个选中的实际 X 体扫独立生成极坐标候选 QC（NPZ＋元数据）以及配置产品网格上的单站分层最大值快视图。快视图不是原始 PPI，标题与产物合同明确区分。

`sx_composite`：按配置产品一分钟时钟展开限定时段，选择各站当时刻之前最新且不过期的可见体扫。S 读取已有 QC，不重跑 S 算法；X 通过独立矩值质控。逐共同高度层质量选源，然后在有观测的离散高度层取最大值。保留来源、年龄、高度和分辨能力。

**尚未接通**：持续运行的实时一分钟自动跟随调度；X 厂家私有原始格式的新解码器；新产品替换天气工作台默认 CR 图层；X 定量估雨/雨量订正；X 一分钟预报；时间运动补偿；全面本地杂波模型、湿天线罩反演和跨波段在线标定。

这不是第二套调度系统。沿用 operations 表与运行时，增加一种任务能力和两个预设。Go/Python 的既有 S 自动流水线仍保留，待 X 真实数据验收后，自动模式可以调用相同 Select 与数值模块，不需要复制它们。

## 2. 架构与模块

```text
S 已质控资产 ── S兼容适配器（原有资格与硬拒绝规则）─┐
                                                    ├─ 原生ray/gate合同
X 已解码资产 ── X独立策略（噪声/相位/衰减/不确定性）─┘
    → 网络发布绑定（不同S/X版本，不强求相同参数哈希）
    → 每分钟有界输入选择（真实扫描/射线时间）
    → 分块投影 + 同高度质量选源 + 垂直最大值
    → 4对象数值/图件候选包 + 完成标记
```

| 模块 | 职责 |
|---|---|
| `internal/multiband/plan.go` | 无HTTP依赖的网络解析、分钟时次、因果选扫与限额 |
| `controlplane/operations_multiband.go` | 现有站点目录与预检查接线，不生成第二种底层任务状态 |
| `multiband/model.py` | 同一网络内的站点、实际频率、MSL几何、产品网格与预算 |
| `adapters.py` | 当前 RainPulse Zarr / canonical NPZ 适配，不猜厂商协议 |
| `quality.py` | X 候选矩值 QC；S 已有质控资格转换 |
| `fusion.py` | 同高度质量优选，随后求垂直最大值；不依赖S内部算法分支 |
| `managed.py` | 现有对象读取/发布运行时适配、有限解码缓存 |
| `codec.py` / `product.py` | 无pickle、大小限制、确定性NPZ、独立图件 |

## 3. 网络发布与输入约束

使用 **一份明确的 JSON 网络发布文件**，环境变量 `RAINPULSE_MULTIBAND_CONFIG`。Go 与 Worker 必须使用内容完全相同的文件；SHA256、代码身份和标志表一起冻结到计划。`configs/multiband/network.example.json` 只是结构样例，站点坐标、扫描策略与参数不是现场值；所有站默认禁用、标定与几何未核验，不可直接作为接单配置。

站点声明实际频率、波段、天线MSL高度、水平/垂直波束宽度、扫描周期、最大年龄、格式能力、标定身份。S 还需声明本次发布允许的 QC 版本。不同 S/X 策略共同属于一次网络发布；合成阶段不再要求两类质控参数摘要相同。

每分钟一个文件不等于完整体扫。输入标明 `volume/ppi/sector`，保留各射线的测量时间；重复方位、无序距离、混乱单位/坐标必须先在格式适配时明确处理，不在融合阶段悄悄修复。

当前输入格式：

- `s_qc_zarr`：既有 `rainpulse.qc-radar-volume`。必须有批准的 `qc_pipeline_version`、原有CR资格、QUALITY_INDEX和匹配标志表。S适配器保留硬拒绝、CR withheld及既有近站对象资格判断。它不会重新判断S天气。
- `normalized_zarr`：既有 `rainpulse.normalized-radar-volume`，用于已经解码的 X 数据。现有 CMA RSTM Level-2 解码器是否能正确读取具体 X 文件，需要以样例头、字段、门数、时间和单位逐一验收。
- `native_bundle`：明确的 `volume.json` + `arrays.npz`，可由设备适配器导出；没有自动注册目录的捷径。管理模式仍要求通过正式登记的体扫 `normalized_uri`（X）或 `qc_uri`（S）进入预检查，并具有完整原子资产标记。

局部原生合同详见 `contracts/internal/multiband/native.schema.json`。数据单位：DBZH=dBZ，SNRH=dB，PHIDP=双程差分相位degree，KDP=degree/km，range=m，ray_time_epoch=Unix秒；不自动混用弧度、厘米、毫秒。NPZ只有数值/二值数组，不接受pickle，对文件数、声明解压大小和NPY维度先限额。

`OBSERVED_MASK`、`NO_ECHO_MASK` 必须区分真实有效无回波与缺测。NaN不能直接转换为无回波。无明确no-return编码时，缺失字段仍保持未知。MSL高度与椭球高不混用：canonical元数据必须给出MSL声明，既有Zarr缺少高度元数据时只采用已人工核验的站点配置，并记录来源。

## 4. X QC 首版的科学边界

原始输入不改写；`DBZH_RAW`在本模块表示原样保留的输入矩值，**若厂家已经订正，它不是“厂家衰减订正前”的原始反射率**。通过 metadata 的上游处理声明区分。

已有实现：有效性/数值范围、SNR、明确非气象掩码、可用遮挡分量、极化异常与有实测支持的物理长度纹理候选、显式天气保护；低RHOHV单独不硬删，低极化＋高纹理仅列为未定候选。没有直接使用S波段的近站半径、门数或阈值。

传播处理三种模式：

1. `none`：不进行衰减订正；X反射率保留为不确定诊断，不宣称具备可信跨波段融合资格。
2. `upstream_verified`：仅在上游明确 `attenuation_status=corrected` 并提供每门有效性掩码时使用，不能自动假设厂家已经处理。可选PIA必须非负、不能超上限。未知订正量保持NaN，不填0。
3. `phidp_linear`：试验性相位线性方法，`PIA = initial_PIA + alpha * (PhiDP - PhiDP_anchor)`。alpha必须在配置中显式给出，**没有预设一个假装适用于现场X雷达的系数**。要求明确raw、可靠近端相位锚点/初始PIA、连续PHASE_VALID_MASK、LIQUID_MASK、有效观测和SNR路径；中途断相位、非液态路径或资料失效后，不在更远处重启并假装前段无损耗。PIA超限后标不可靠，不用上限截断伪造可信测量。KDP_EST仅为诊断导数，不用于估雨。

这一独立候选实现没有声称调用了Py-ART Z–Phi，更不是其复现验收。公开方法参照：Py-ART官方 `calculate_attenuation_philinear`、wradlib官方 `pia_from_kdp`。相位与KDP积分的定义必须一致，双程PhiDP关系不能额外多乘2。实际系数、滴谱/降水相态适用性、噪声窗口、湿罩损耗要另验；仅有矩值不能恢复完全衰减消失的信号。

参考地址（供开发核对）：
- https://arm-doe.github.io/pyart/API/generated/pyart.correct.calculate_attenuation_philinear.html
- https://docs.wradlib.org/en/stable/generated/wradlib.atten.pia_from_kdp.html

所有X输出均 `operational_eligible=false`，`QPE_ELIGIBLE_MASK=0`。QUALITY_SCORE是显式网络启发式排序分数，不是概率，更不表示现有S的QI已与X统计校准。

## 5. 时间、组合与空间

`x_qc` / `sx_composite` 单次半开范围最多一小时、最多64个任务；目录最多4096条，不静默裁掉积压。选中站点必须在网络中启用。S按本身到报周期处理；一分钟产品只复用其已有QC资产。X单站扫描结束在分钟之间时，快视图分析时刻取后一个分钟，源射线时间仍不变。

每个输入固定 observation start/end、available_at、input cutoff。历史管理计划的cutoff是本次预检查时刻，并不证明该资产在历史真实时刻已经到报；页面明确警告。S QC资产可用时间保守取目录更新与接入时间较晚者；已知更晚的原生可用时间会进一步收紧，不会放宽截止条件。

全站不到齐也可生成：每分钟按台站最大年龄选取；射线级还会再拒绝过期与未来测量。全部无可用输入的时次给出告警，不制造一帧全零雨场。当前**不做时间运动补偿**，产品保留真实年龄，不把S旧体扫改成一分钟新实况。

融合步骤：投影到固定米制局地网格，用真实波束脚印和距离库支持范围匹配每个高度层。候选必须先通过用途资格。每层按显式质量、年龄、有效分辨能力和高度代表性选取一个来源；**不是取各站dBZ最大，也不是对dBZ直接平均**。随后才对有观测的高度层求最大值。来源顺序固定，质量相同的选源可重复。

高度是指定离散MSL层；没有垂直外推或填满无观测层，产物准确名称是 **“已观测配置高度层内的融合最大反射率候选”**，不承诺恢复层间未采样极值。可信CR、不确定CR、有效无回波、无有效观测分别表达。一层可信无回波不能把其它层的不确定回波解释为整柱无回波。

输出带 `WINNER_SOURCE/RAY/GATE`、实际赢家波束MSL高度、观测年龄、有效分辨尺度、分数、有效层数与高度范围、来源资产清单。500m网格不是保证500m有效分辨率。北向上PNG与南到北数值数组的行顺序明确；当前PNG是投影快视图，**不是可以直接按经纬度边界叠到天气地图上的瓦片**。

## 6. 存储、缓存和资源

S/X融合资产4个逻辑对象：`manifest.json`, `arrays.npz`, `cr.png`, `uncertain.png`，另有原子发布标记。X单站额外2个极坐标对象 `native_volume.json` 与 `native_arrays.npz`。内部NPZ条目不会逐一变成磁盘文件；物理inode减少幅度仍需现场对象存储验证。没有迁移或删除已有产品。

复用第二批 VerifiedArtifactReader：新鲜标记每次检查；首次读取选择必要坐标/数值/资格，不读取无关实验数组。schema 1/2按已承诺逻辑对象哈希校验；旧schema 3仍全包完整性回退，不能为省I/O削弱校验。解码缓存启用后，同一资产/站点/配置/时间身份可复用已解码QC，命中时仅验证标记。缓存按字节、TTL和条目数淘汰，默认0；Worker单计算lane，无无界并发。

分层数组仅按tile驻留，几何每个sweep/tile计算一次，不每层重复。仍存在输入/输出编码副本，不宣称resident_input_bytes是RSS峰值。网格最多100万格，最多32高度层，tile高度体素最多200万，单次原生体扫最多800万门；部署另用容器硬内存上限。

已有retention按 scan_id / analysis_id / kind 保留。X单站使用scan_id；CR使用product+分析时刻的稳定slot analysis_id，跨网络版本仍归为同一产品时次，不因随机任务ID而永久保留所有旧版本。不同产品或不同分钟不会混删。当前网络版本保护依旧经原有“算法与存储”发布通道操作；代码包不会执行清理。

## 7. 安装与启用

先应用补丁并运行 `bash scripts/test_multiband.sh`。完整仓库要求真实锁定的Go/Python/React依赖；隔离测试不能替代完整编译。

升级顺序：
1. 停止新管理提交，排空/完成旧冻结任务，保留原有成功产品。Python完整包哈希会变化，新旧Worker不能无条件接替同一个旧冻结计划。
2. 在已有operations schema v3上执行 `services/control/internal/operations/schema_multiband_v1.sql`（增量、可重复）。只增加multiband池/发布通道并扩充CHECK；新池默认DRAINING。
3. 核验并写实际网络文件；Go运行环境设置 `RAINPULSE_MULTIBAND_CONFIG`，现有标志表路径不变。
4. 构建明确新镜像。用 `python3 scripts/configure_multiband.py --help`，根据已登记的真实Compose模型、显式镜像tag/digest、额外CPU/内存预算生成覆盖层。工具只生成，不启动/修改清单；不传Docker socket，不复制S算法参数。
5. 协调更新Go/Web/管理Worker；核对Worker网络哈希、版本、就绪状态和实际配额。通过后台恢复multiband接单后，创建小范围候选预检查。不是先用大范围任务试错。

示例生成命令（所有预算和镜像需替换为现场核对值，不是硬件推荐）：
```bash
python3 scripts/configure_multiband.py \
  --network runtime/multiband/network.json \
  --image rainpulse-cpu-worker:multiband-tested-revision \
  --cpus 1 --memory-mib 2048 --extra-cpus 1 --extra-memory-mib 2048
```

不支持只更改浏览器的product_id来任意创建网格或从路径执行代码。UI只选择已发布产品。

源码回退与数据库/数据回退不同。有新kind任务后，不要把旧后端直接指向含未完成multiband任务的管理库；先停接单和处置这些任务，保持新结果为独立候选。

## 8. 无外部服务的数值回放

```bash
PYTHONPATH=algorithms python3 scripts/multiband_demo.py --output /tmp/rainpulse-sx-demo
# 复用同一输入，写一个全新输出目录：
PYTHONPATH=algorithms python3 -m rainpulse_algo.multiband.cli \
  --network /tmp/rainpulse-sx-demo/network.json \
  --request /tmp/rainpulse-sx-demo/request.json \
  --input-root /tmp/rainpulse-sx-demo --index /tmp/rainpulse-sx-demo/index.json \
  --output /tmp/rainpulse-sx-demo/replay2
```

例子只生成明确SYNTHETIC的规范原生观测，使用实际数值算法写NPZ/PNG和回执，不依赖模拟出图。不是105/真实雷达测试。CLI不覆盖已有结果；本地回执最后落盘表示完成，服务端继续使用原有条件写入_SUCCESS，不复用本地文件发布流程。

## 9. Codex合并与验收顺序

- 核对最新main相对基线的差异，特别是 operations身份/池、S原有资格标志与SDK。不要覆盖新近站算法，不用示例S版本替换真实版本。
- 跑完整Go controlplane/apiapp/operations包、锁定Zarr/MinIO/NATS适配测试、React tsc/Vitest及lint。这里提供的PostgreSQL迁移测试必须使用明确同意的可丢弃测试库。
- 核对选择 → 预检 → submit幂等 → Worker → 独立产物 → Preview/日志/资源/性能全过程。测试新池DRAINING时不接单，配置文件改动时拒绝旧计划。
- 给定真实X样例，先完成格式、单位、射线时间、频率、MSL、扫描完整性、厂家订正声明与标定回执；必要时写设备decoder adapter，不在QC里猜文件布局。
- 验证弱雨、小单体、冰雹低rho、衰减阴影、湿罩、缺相位、无S/无X、S延迟、时钟偏差、跨高度强回波、S原有近站硬拒绝无泄漏。逐过程报告误删与保留，不用“画面更干净”替代质量。
- 在明确区域网格与稳定分钟合同后，再接持续自动调度和地图产品目录；X-QPE、默认业务发布、运动补偿分别验收。不得把候选CR接入旧QPE输入以凑完整链路。
