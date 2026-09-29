# S/X 单站订正与质量分层融合（2026-09-29）

## 交付边界

基于 `a4032eea9202c0ed0063ed4fbf4f9fb850cd6eb1`。本版接入现有多波段 Worker、限定时段预检查和 S/X 候选产品，不另建任务平台，不引入 Kubernetes，不修改现有 S 质控和 X 非气象回波识别阈值。保留最新 `xqc_v2` 候选显示修复、原始门和隔离证据。

新增 `quality_height_v2` 为明确选择的质量融合模式。原 `quality_height` 和 `experimental_horizontal_max` 仍可运行；不会把未标定水平试验改名成可信产品。新模式依然是候选，`operational_eligible=false`，不进入 QPE、累计雨量、预报输入或默认业务发布。

## 1. 数据流与职责

```
X 本站原始矩、字段有效性与处理声明
  → 现有 X 污染筛查/相位前支持约束
  → correct_sweep：Z–Φ / 相位线性 / 已核验上游订正 / 有依据的无需订正
  → 数值 + 路径状态 + 湿罩状态 + 标定资格
                                  ┐
S 已有 QC + 原 CR 资格/拒绝 + 标定 ├→ 同高度质量选源 → 垂直最大值 → 候选与来源
                                  ┘
```

X 订正接口不接受 S 观测，没有“S 到齐才能计算”的条件。S 只作为融合候选之一。融合不回写任何 X 数值；没有合格 S 或其它 X 时保留缺测/不确定，不给 X 补出一个假 `Z_corrected`。

`quality.py::phase_linear` 的原公式仍保留，统一入口额外遵守已提供的 VALID/AVAILABLE 掩码。新 Z–Φ 是 NumPy 的归一化路径积分实现，不是给相位线性法换名，也不是 Py-ART 函数调用或其逐位等价实现。旧 `radar/qc_engine/phase.py` 暂不改动；本次统一的是多波段 X 运行入口，未宣称全仓所有历史相位实现已经合并。

## 2. Z–Φ 数学与适用条件

仅对可靠液态降水连续段求解。设观测反射率的线性量为 Z_a，比衰减—真实反射率关系的幂指数为 beta；alpha 为 A_H/K_DP 的系数，单位 dB/degree。采用双程 Φ_DP，设段内增量为 ΔΦ。

```
k = ln(10) * beta / 10
J(r) = ∫[r0,r] Z_a(s)^beta ds
u(r) = J(r) / J(r1)
T = alpha * ΔΦ
L = 1 - exp(-k*T)
PIA(r) = PIA(r0) - log(1 - L*u(r)) / k
A_H(r) = L*Z_a(r)^beta / [2*k*J(r1)*(1 - L*u(r))]
Z_corrected_dBZ = Z_observed_dBZ + PIA(r)
```

PIA 是双程 dB；A_H 是单程 dB/km。直接用双程 ΔΦ 时不再额外乘 2。J 在实际距离坐标上用梯形积分；指数归一化抵消于比值，使用 `expm1/log1p` 降低数值问题。核心求解器可处理已准备好的非等距距离点；相位中值窗口按本切面中位门距换算为奇数窗口，非等距数据的物理窗口近似需要另行验收，绝不隐式重采样。

该方法利用 Z 的径向分布分配沿程衰减，区别于逐门 `alpha*ΔΦ(r)`。端点自洽不等于独立验证成功。alpha、beta、频率范围、系数资料标识必须显式给出，不能把库默认频率系数当作本地标定。

物理前提包括液态适用区、足够 SNR/RHOHV、有效反射率和相位、可用的初始路径损失。混合相态、湿冰、严重相位噪声、消光和湿罩不能通过增大订正上限解决。没有普适的“65 dBZ 消光开关”。

参考：
- Testud et al. (2000), *The Rain Profiling Algorithm Applied to Polarimetric Weather Radar*: https://journals.ametsoc.org/view/journals/atot/17/3/1520-0426_2000_017_0332_trpaat_2_0_co_2.xml
- Py-ART 官方接口（区分 Z–Φ 与 philinear）：https://arm-doe.github.io/pyart/API/generated/pyart.correct.calculate_attenuation_zphi.html
- Py-ART 官方实现参考及系数/温度边界：https://arm-doe.github.io/pyart/_modules/pyart/correct/attenuation.html

本包的合成恢复测试是对上述模型和离散实现的验证，不代表已经标定真实 X 雷达，也不主张与 Py-ART 默认预处理完全一致。

## 3. 路径与其它质量状态不能混为一谈

| PATH_STATE | 含义 |
|---|---|
| 0 UNKNOWN | 无法确认完整路径订正；不进入 v2 可信融合 |
| 1 NOT_REQUIRED | 有独立声明/掩码支持衰减可忽略；PIA 保持 NaN，而不是伪造实测 0 dB |
| 2 UPSTREAM_VERIFIED | 使用明确已订正的厂家产品及其有效性声明；不二次订正 |
| 3 PHIDP_LINEAR | 原相位线性对照方法 |
| 4 ZPHI | 新 Z–Φ 路径解 |
| 5 VERIFIED_CLEAR_PATH | 已确认无回波且传播损失可承接的清空段；不制造相位或反射率 |

另输出 `PATH_VALID_MASK`、`PATH_REASON`、`PIA_DB`、`AH_DB_PER_KM`、`KDP_EST`、`DBZH_ATTENUATION_CORRECTED`、`RADOME_QUALIFIED_MASK` 与 `CALIBRATION_QUALIFIED_MASK`。

1. 未知/污染/非液态路径断开后，不在后段重新设初始 PIA=0。只有带显式已知 PIA 和证据标识的后段锚点才能重新开始。
2. 弱相移不足以证明零衰减；相位跳变、段长不足、订正超限均保留原因。超限退出而非裁剪到上限后声称可靠。
3. 明确的清空段与未知间隔不同：前者可以在已知损失基础上承接，后者不能。
4. 订正值超出反射率有效范围会失去路径资格，不把巨大放大作为修复。
5. 相位有效性与速度退模糊独立；本模块不要求已退模糊速度，不将相位订正当速度订正。
6. 径向分段按支持边界跳转；不会对长段未知门逐门做 Python 订正，也不跨缺测平滑。

### 输入声明和证据来源

Zarr 全体扫适配、单切面适配都透传实际存在的字段，不虚构可用性。

- 既有 `PHASE_VALID_MASK`、`LIQUID_MASK`、`OBSERVED_MASK`、SNR/RHOHV 与其 VALID/AVAILABLE 别名共同约束支持。
- 全局近端锚点使用既有 `phase_anchor_verified` 和 `pia_at_first_gate_db`，仍检查最大近端距离。
- `CLEAR_PATH_MASK` 必须同时是明确观测到的 `NO_ECHO_MASK`，并携带 `clear_path_evidence_sha256`。
- `PATH_ANCHOR_VALID_MASK` 与 `PATH_ANCHOR_PIA_DB` 必须携带 `path_anchor_evidence_sha256`，损失必须在上限内。
- `ATTENUATION_NEGLIGIBLE_MASK` 必须有 `negligible_attenuation_evidence_sha256`，不能仅凭 ΔΦ 很小推断。
- 湿罩独立使用 `radome_status=verified_negligible/upstream_corrected`、`radome_evidence_sha256` 和可选 `RADOME_VALID_MASK`。这里只检查和传递资格，不进行未知的湿罩数值订正。

这些 SHA 是可信生产者的来源声明，当前数值模块验证格式、冻结后的输入与对应掩码，不会在线下载这些证据并独立证明物理正确。生产者必须保证证据属于当前站点、扫描、几何和有效时间。不得为了看到图而写一串哈希、全 1 液态掩码或 `verified=true`。

现有 S 资产若没有 `calibration_id`，或不能与站点已核验标定身份对应，v2 会把它保留为未定，不偷偷补 ID。补齐真实资料来源是启用前任务，不是这次自动代办的标定工作。

## 4. v2 融合资格

`fusion_quality.prepare_source` 在每个切面进入空间块循环前，生成独立用途掩码，不改输入数组。

- S：既有 CR 资格、观测有效性、硬拒绝/withheld、正质量值，以及标定身份匹配；不将 S 当真值。
- X：上述条件再加路径状态可用与独立湿罩资格。既有 `QC_ACTION=2/3`、XQC withheld、budget-withheld 不可借显示场重新进入。
- 没有新路径契约的 X 按未定处理；畸形掩码或“UNKNOWN 却标 valid”直接报错。
- 仍使用现有波束几何、实际射线年龄、到达截止时间及空间代表性权重；不把旧 S 体扫改写为当前一分钟观测。
- 同高度按质量优选，而非最大反射率；然后取配置高度层的最大值。X 低层细节不抹掉合格 S 高层强回波。
- 这里没有引入跨波段 Z 的经验换算或自动偏差追平。几何可比不意味着所有散射条件下 S/X 等值；站点质量尺度仍是启发式而非概率，需留出过程验证。

输出新增 `AVAILABLE_BAND_BITS`、`QUALIFIED_BAND_BITS`、`WINNER_BAND`、`UNRESOLVED_MASK`、`INPUT_QUALITY_REASON`，以及 `S_SELECTED_WITH_X_AVAILABLE_MASK` / `X_SELECTED_WITH_S_AVAILABLE_MASK`。

这些 band bits 是**配置高度柱内的 OR**，不是同高度重叠率或融合权重；“S 获选且 X 可用”是事实，不被命名为已确定的“X 衰减失败所以 S 兜底”。具体原因由源记录和输入原因查询。

未定回波保存到 `CR_UNCERTAIN_DBZH`，不覆盖 `CR_DBZH`。只存在明确衰减屏蔽、没有有限反射率时，仍能保留不确定覆盖。一个高度的清空不能证明其它未观测/未定高度也没有回波。产品始终是“配置的已观测高度层内最大值”，不是完整三维真值。

## 5. 流式、存储与已有链路

直接与流式路径共用门资格和 `update_tile/finish_tile`；NumPy/Numba 仍使用同一选源规则，保留现有共享高度工作区、内存预算和落盘清理。新资格字段约增加 8 字节/门的融合视图预算，在流式路径准备前检查。路径输出会增加额外逐门数组，真实 RSS 和时延必须测量，不能据单测声称更快。

新增的 2D 来源掩码是有界数组，不新增一套全网常驻三维缓存。原始资产不修改、不删除。路径证据并入已有每切面 `native.npz/evidence.json`；仅在单独启用 Z–Φ、没有非气象分类器记录时新增同格式的 path-only 记录。保留原 256 MiB 解码、64 MiB 单 NPZ 与整体输出限额，超限报错，不暗中丢证据。

Worker、管理 API、预检查和持久化任务机制继续沿用。Go 仅扩展网络产品方法枚举，不新建数据库表，不改变自动派发范围。已有任务采用冻结网络/代码身份，改代码不等于旧任务可无条件接着运行。

## 6. 配置和启用

不改任何现场网络文件。工具只创建新的网络发布：

```bash
python3 scripts/prepare_sx_quality_release.py \
  --network /path/to/current-network.json \
  --grid /path/to/reviewed-height-grid.json \
  --product-id sx-quality-v2 \
  --release-id sx-reviewed-20260929 \
  --x-settings /path/to/reviewed-x-settings.json \
  --output /path/to/new-network.json
```

`--x-settings` 可省略，保留现有方法。需要 Z–Φ 时给出站点到配置的对象，示意（REQUIRED 必须用核验结果替换，不能直接运行）：

```json
{
  "实际X站ID": {
    "attenuation": "zphi",
    "alpha_db_per_degree": "REQUIRED: verified numeric coefficient",
    "zphi": {
      "beta": "REQUIRED: verified numeric exponent",
      "coefficient_id": "REQUIRED",
      "coefficient_source_sha256": "REQUIRED: 64 lowercase hex characters",
      "frequency_min_hz": "REQUIRED",
      "frequency_max_hz": "REQUIRED"
    }
  }
}
```

网格需要显式 metre CRS、MSL 高度层、范围、分辨率和 60/360 秒步长，不能把原水平试验的一层占位高度默认为已核验三维网格。工具保留旧产品、现有 X enhancement 和所有 enabled/geometry/calibration 标记，不覆盖已有输出文件；达到产品数上限时拒绝，而非删除历史配置。

正确的上线顺序：先在测试环境执行完整回归；停止产生新的旧身份重算并处理完旧冻结任务；保留旧 Worker 镜像以完成旧任务；构建本版 Go/Web 所需产物及算法镜像；加载新网络到匹配的管理 Worker 和预检端；新建一个小范围候选计划；检查数值、来源和日志。不把试验产品切换为业务默认，不重算整段历史作为第一步。

本次无数据库迁移。仅回退源码不会将已有新产物变回旧算法结果，需按产品方法和冻结身份分开查看。不要让旧 Worker 执行 zphi / quality_height_v2 计划。

## 7. 验证与尚未完成的验收

新增用例覆盖：已知前向衰减恢复、PIA 双程因子、Z 径向分布影响、锚点/未知断段/清空段、弱相移/订正限额、厂家已订正输入、显式有效性别名、原始数组不变、X-only/S-only、X 不可靠时 S 获选、S 自身不可靠时保持未知、时间过期、高低层强回波、来源排序、分块、真实落盘、Numba、最后输入失败清理，以及配置文件不覆盖/不提高核验标记。

本地数值测试用了真实 NumPy/SciPy/pyproj/Numba 和所列源码。仅缺失的性能遥测模块在隔离测试启动器中替换为无操作装饰器；科学计算、文件落盘和选源没有替身。真实 Zarr SDK 回读测试已提供，但当前环境没有 SDK，因此跳过。Go 对实际 plan.go 和新增用例运行独立标准库测试、race、vet；不是全仓构建。

仍需 Codex 在完整仓库完成：锁定依赖下完整测试/lint/构建、真实 Zarr 适配回读、XQC-v2 与近期 hardening 用例、管理预检查到 NATS/对象发布的集成、地图探针属性显示、相同配置下直接/流式对照、真实雷达与独立雨量/标定资料验证、现场 CPU/RSS/inode/时延测试。Py-ART 数值对照要对齐相位预处理、系数、积分边界和掩码，不直接将默认参数差异判为实现错误。
