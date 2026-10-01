# S 波段径向残留研究与下一版方案（2026-10-01）

## 微碎片与实测噪声侧翼诊断

新增 `scripts/probe_s_microfragments.py`，对冻结原生快照分别测试短片段提名和
实测 SNR 侧翼两个因素，保存输入/脚本/检测器 SHA、完整拒绝原因分区与证据数组。
输出仅为 `RV2_SPARSE_PROBE_*`，动作数固定为 0，不写质控产品；未接生产开关。
缺测仍不等于低噪声，天气/冲突/几何屏障保留；原 `detect` 条件保持不变。

正确 Web 体扫 Z9598 08:18 最低层西南 210–270°、100 km 外的 367 个残留门，
四种组合提名/证据均为 0。扩展只读搜索到 3 个原生半波束、最大 6° 后仍为 0，
拒绝主要来自局部条带识别、片段数量、实际支撑及距离跨度。不能将诊断扩宽当成
新版删除条件，或将其他区域提名数宣称为目标改善。

RAW 家族复核显示这 367 门分属 4 个短原始对象，距离跨度约 7、13.5、12、33 km，
均未达到既有信号模型的 60 km 参考跨度。下一步应核验原始碎片的角边界与独立
实测证据，评估有限关联；不能简单降低长度门槛或从新增弱尾递归产生来源。

```bash
python scripts/probe_s_microfragments.py /path/to/frozen-sweep.npz \
  --azimuth 210 270 --range-min 100000 \
  --output .build/s-microfragment-probe-new
```

## 原生时间复核：固定位置重复不是主要解法

四个08:18短RAW对象的角范围分别约257.43–262.36°、265.33–271.29°、
210.06–218.96°、224.85–229.76°。它们不是同一角向条带，不能跨对象串接
来补够模型长度。这个测量排除了先前关于四个短对象可组成一条长射线的假设。

新增 `scripts/audit_s_native_temporal.py`，按真实射线时间、半个原生方位/距离
采样间距、仰角差≤0.1°匹配独立历史体扫，限制参考年龄并拒绝未来/重复输入；
原生缺口及天气/冲突屏障排除。分别记录几何覆盖、DBZH观测、SNR观测、
SNR差≤2.5dB的诊断一致性及明确命名的历史径向标记。标记不是独立天气真值，
SNR一致性不是污染证明；不启用动作或从时间重复产生新来源。

两份新Web配对快照通过传输字节数/完整包序列/SHA验证：08:36 scan
`5172b858-0406-5498-89fd-16db363507d1`，08:42 scan
`d12ac95a-4039-5ae7-99f8-6e134da05e7f`。当前QC减来源阶段投影后的诊断统计：

| 目标与范围 | 残留门 | 历史参考 | 双方DBZH观测 | SNR一致 | 历史径向标记 |
|---|---:|---|---:|---:|---:|
| 08:36／210–270°／100km外 | 1079 | 08:18 | 2 | 2 | 0 |
| 08:42／160–280°／100km外 | 781 | 08:36 | 302 | 22 | 250 |
| 同上 | 781 | 08:18 | 397 | 113 | 211 |

08:42同时有两个历史DBZH观测的仅36门，两个历史径向标记的仅2门，两个
历史SNR一致的0门。原生射线实际时间差约17分钟、11分钟及28分钟，不能
从界面标称时次猜测匹配。单个历史标记数量也不能当作本次新增删除数。

9项测试覆盖跨北、不同距离范围、角向错位、缺口、仰角/时间上限、未来体扫、
重复输入、缺测与明确标记定义，以及重复天气始终不获删除资格。复核只写
新的诊断JSON；RAW/QC与Worker策略均不修改。下一步需核验角向变化的原始
形态与实测信号联合证据，固定位置重复性不足以解决这些目标。

```bash
python scripts/audit_s_native_temporal.py target.npz past1.npz past2.npz \
  --azimuth 160 280 --range-min 100000 --output .build/s-temporal-audit-new.json
```

> **2026-10-01 输入身份纠正：** 通过105实际Web周期原始/QC帧scan_id核对，旧8个
> 截图样例中4个错配：Z9591 09:48、11:24及Z9598 08:18、08:36。旧数据/图件仍
> 是有效的独立研究输入，但不能证明对应截图的遗漏或效果；其中“无锚/高层无源”
> 结论也不能套到实际08:18截图。10:18、10:24、10:42、08:42身份匹配；08:42的
> 428/53来源轮廓增益仍是同一scan上的源阶段提议，未证明Web图件生成版本一致。
> 身份回执web-scan-identity-comparison-v1.json，复核脚本已默认按Web配对scan解析，
> 禁止最近时次猜测。以下旧研究章节须按此边界阅读。


## 正确Web体扫上的原始来源分束修复

实际08:18最低层原parent31的来源射线为162–165、167–168及170：旧规则
每个参考窗要求整个来源射线集合连续，导致两束长期稳定的来源一并被拒绝。
source_footprint v2先冻结原始来源的连续行岛，每束独立满足原10km支撑、
3外部20km参考块/60km跨度、原生几何与边界漂移限制，目标及邻块不能训练；
不填166/169行空隙，单独170行不满足2射线门槛，不借另一父对象或短束支撑，
天气/冲突屏障及实测可靠偏振保留维持。原始范围固定、弱尾不成新来源。

正确08:18 scan9ee02e8c…快照SHA7c5bdea0…/38f46799…相较v1新增
最低层424可见门（扇区417，全部来自parent31），第3层47可见门
（来自parent6/15，目标扇区0）。未撤回原合格门；序列化原始证据writer重算通过。
两个新测试覆盖原始分束/空隙屏障与父对象、短束不能互借支撑；相关整套通过。
图件web-aligned-0818-components-images-v2已经打开：东南方向一条残留带减少，
西南长线仍保留，不能把417扇区计数写成西南红框已解决。

仍为default-off源阶段候选提议，完整Worker/Hybrid/组合/PNG、独立天气与
泛化验证尚未闭合；105同一Web体扫只读引擎验证已通过：RAW与baseline完全相等，profile/QC SHA一致，
v2单开关新增610可见门，较v1净增424且原提议损失0；不代表部署。

## 最新研究结论：窗口追踪仍不是完整解法

### 后续实施回执：宽RAW父家族诊断已接入

新增raw_fans.py、默认关闭raw_fan_families_enabled（依赖完整账本），在旧来源
拟合后独立运行。以20km块实际支撑提名2–90°原生角域；原始支撑射线之间的
单条短片可进入对象，但不补缺测。链接距首块左右边界最多两个波束，不沿
新增边缘递归扩张；60km块距上限、天气/冲突和几何断裂切断对象。
保存RAW父ID、逐块边界/占用、冻结首块角域和实测总支撑、原始账本来源ID。
同一家族含多个来源只是线索，不认定同一发射源；全候选保留独立资格未证实。

| 固定扇区 | 剩余 | 旧窄提名 | 新宽提名 | 宽对象内有原来源线索 |
|---|---:|---:|---:|---:|
| Z9591 10:18/2 | 26 | 24 | 26 | 18 |
| Z9591 10:24/2 | 106 | 62 | 106 | 106 |
| Z9591 10:42/2 | 38 | 33 | 24 | 24 |
| Z9591 11:24/2 | 185 | 169 | 168 | 168 |
| Z9598 08:18/0 | 405 | 245 | 362 | 10 |
| Z9598 08:36/0 | 1406 | 174 | 1354 | 1145 |
| Z9598 08:42/0 | 781 | 64 | 554 | 524 |

宽与窄互补，不能替换窄路径；尤其10:42宽提名更少。08:18有352个宽候选
对象缺任何原来源线索，需要无锚独立判别；08:42还有227门未进宽模型。
该计数不是确认干扰召回率或删除数，没有独立天气真值。

新增8测试，相关164passed，包括三门距、空隙天气屏障、原生几何切断、
首块边界防接力、短片、篡改来源/边界拒绝，以及engine旧全部输出逐字段
不变、source writer保存/校验。全部八快照校验通过，RAW不变、动作/补门0。
快照及模块SHA保留raw-fan-v2/report.json和source-clue-audit.json。
两例图raw-fan-visual-v2红色仅表示候选，图件SHA及原输入SHA另存receipt.json。
plot_s_raw_fans.py保留复核入口，不执行服务器/产品写入。

```bash
algorithms/.venv/bin/python scripts/replay_s_raw_families.py \
  .build/s-discontinuous-20261001/research-diagnostics --raw-fans \
  --output .build/s-discontinuous-20261001/raw-fan-review-new
algorithms/.venv/bin/python scripts/plot_s_raw_fans.py \
  .build/s-discontinuous-20261001/research-diagnostics \
  .build/s-discontinuous-20261001/raw-fan-v2 \
  --case z9591_1024_sweep_002 --case z9598_0836_sweep_000 \
  --output .build/s-discontinuous-20261001/raw-fan-visual-new
```

下一实施点：原始独立来源与宽对象的模型身份核验（来源/目标窗口分离），
只放行有真实证据且不跨天气屏障的有界尾段；同时补无源路径可用的偏振/相位
证据。不能因1354个候选覆盖就部署大面积删除。完整B/C、留出及产品链尚未完成。

复核window-source-v1八份固定输入并按scan_id/输入SHA关联原快照。
七个有残留的抽查区合计2947门，新增窗口资格仅10门（非生产删除、非干扰真值）。
这说明继续只优化窄源关联收益有限；现在需要把宽对象表示与独立判别分开推进。

| 抽查对象 | 上轮剩余 | 新窗口资格 | 已有窗口但父支撑不足 | 窄提名但无窗口父 | 未进窄提名或窗口 |
|---|---:|---:|---:|---:|---:|
| Z9591 10:18/2 | 26 | 0 | 0 | 24 | 2 |
| Z9591 10:24/2 | 106 | 0 | 10 | 57 | 39 |
| Z9591 10:42/2 | 38 | 3 | 0 | 30 | 5 |
| Z9591 11:24/2 | 185 | 0 | 0 | 169 | 16 |
| Z9598 08:18/0 | 405 | 0 | 0 | 245 | 160 |
| Z9598 08:36/0 | 1406 | 6 | 8 | 171 | 1221 |
| Z9598 08:42/0 | 781 | 1 | 0 | 64 | 716 |

这些互斥分类严格覆盖剩余；本轮目标区没有父歧义候选。09:48输入仍未精确
复现截图，不参与效果统计。源阶段快照沿用旧保护掩膜，不等于完整Worker验收。

### 三个必须改变的环节

1. **宽扇区RAW对象优先补齐。** 代码中的power_fan目前先逐射线要求至少
   八个20km块、每块5km及总80km实测支撑，之后才组合角域。这会把离散边缘
   和弱扇区排除在父对象建模之前。先用距离块×实际方位角的占用统计建立
   可变宽RAW扇区候选，再用已有独立强源确认父身份；候选与处置分离。
   稀疏占用可参与对象表示，但不能凭未观测门形成清空资格。不降低原强源门槛。
2. **父对象须保存窗口模型，而非逐射线掩膜。** 记录独立原种子、左右角域、
   每块有效/未知状态、支撑和模型身份。来源拟合与目标核验块及相邻保护块分离；
   原始来源确定后冻结角域和范围，不用新残留更新模型。功率距离一致性只作为
   同源佐证，不能伪装成独立非气象票；天气/冲突、几何断裂仍切断关联。
3. **无父碎片补独立判别。** 当前rho纹理和PHIDP圆统计已有诊断但动作未启用。
   先核实相位是否未处理/已滤波、缺测码、低SNR误差及样本支撑，使用独立确认
   天气/干扰样本估计阈值，再用于对象提名后的逐门核验。缺偏振走实际覆盖内的
   Doppler/邻站/上层或过去确认来源；无可靠票保留unknown，不删整条径向。

### 文献核对带来的边界

已完整读取[MIT ATC-454第3.1–3.4节](https://www.ll.mit.edu/sites/default/files/publication/doc/radio-frequency-interference-censoring-scheme-cho-atc-454.pdf)。
其结构先认定受污染径向再逐门核验，并针对弱干扰造成REF仍有效、偏振先被
信号处理器截掉的情况增加断续spike路径。但spike仍用SQI限制混合天气，
不能在我们无SQI时只移植稀疏形态条件。末端speckle清理也不能无条件照搬，
本项目仍限制在有资格的冻结来源内。文献的缺测默认值与门数窗口不适用于
我们的三态数据契约，应保留实际可用状态与物理尺度，并重新做站/日期留出。

[DWD信号域研究](https://amt.copernicus.org/articles/15/6625/2022/)使用SQI和脉冲
功率STD识别RFI，并结合干扰源定位。由此推断：若矩量级证据始终不足，
最有价值的补充是设备输出SQI/脉冲功率统计或I/Q与干扰源排查；SNR/谱宽不可替代。

### 实施与停止条件

- 先完成宽父对象诊断，验证08:36/08:42未提名残留是否进入正确原始对象；
  同时验证天气带、小单体、缺测条带不会被并进相同来源。仅提高提名不算改善。
- 再接独立联合资格和有界尾段处置，逐对象输出接受/拒绝原因，并对来源范围、
  原种子与目标角度建立持久化交叉校验，防止篡改诊断范围逃过检查。
- 冻结配置后用其他站/日期与天气对照验证，之后完整QC→Hybrid→组合→图件
  同版本复核。任何未完成层的旧产品不得充当新版效果。
- 若大部分剩余因观测缺失而不能分类，停止放宽形态阈值，转向信号域/来源治理。
  不能同时承诺“全部射线消失”和“天气零损失”；可靠目标是确认干扰被排除、
  可信天气受保护、无法分离的混合门显式未知。

复核脚本保留：

```bash
algorithms/.venv/bin/python scripts/audit_s_source_window.py \
  .build/s-discontinuous-20261001/research-diagnostics \
  .build/s-discontinuous-20261001/window-source-v1 \
  --output .build/s-discontinuous-20261001/window-source-v1/failure-partition-new.json
```

本轮只新增只读原因复核脚本/方案记录，没有新部署、生产重算或Web更新。
现有窗口及相关径向回归156项通过；这不是独立气象效果或泛化验收。

结论：现有第3/4点尚不能彻底处理这些残留。应补齐“原始短碎片对象建模 →
原始来源关联 → 可用观测联合判别 → 有条件的小对象清理”，不能继续单纯降
长度、SNR或双侧证据门槛。以下保留研究结论与分步方案；A已本地实现并复核（见文末回执），B/C尚未完成，尚未部署。

## 只读实数据诊断

保存8个时次/层快照，使用105实际S配置和候选模块；原始数据不变，天气/冲突
保护动作检查通过。不重启Worker或写生产产品。回放沿用存储的保护掩膜，未
重建完整体扫上下文；没有独立天气真值。抽查扇区包含潜在天气，剩余数不是
已确认的干扰真值数。

| 站点／时次／原生层 | 抽查区旧可见 | 新增隔离提议 | 剩余 | 剩余进入原始范围对象 |
|---|---:|---:|---:|---:|
| Z9591 10:18／2 | 194 | 168 | 26 | 0 |
| Z9591 10:24／2 | 139 | 33 | 106 | 0 |
| Z9591 10:42／2 | 106 | 68 | 38 | 0 |
| Z9591 11:24／2 | 268 | 83 | 185 | 0 |
| Z9598 08:18／0 | 431 | 26 | 405 | 0 |
| Z9598 08:36／0 | 2670 | 1264 | 1406 | 0 |
| Z9598 08:42／0 | 4795 | 4014 | 781 | 0 |

Z9591抽查：距离≥250km，方位285–340°；Z9598抽查：距离≥100km，方位160–280°。
Z9591 09:48选到的最近前序体扫抽查区可见0，与用户截图北侧对象位置不同，
因此不计作解决或该截图复现；须绑定Web实际scan_id。出图脚本现支持
`--scan-id SITE HH:MM SCAN_UUID`，无精确ID时记录选择方法，不能把时次标签
当作同一输入证明。

### 已证明的漏检路径

1. Z9591 10:24剩余106门在RV2候选与原因字段均为0，没有进入后续处置路径。
   其中62门通过局部RAW窄条走廊，但不是已有LINE/GROUP提名，也没有可用的
   原始独立种子。许多连通片段仅1–2km；个别射线最大连续片段只有0.75km。
2. `discontinuous.detect`在对象关联前先丢弃<1km片段，再要求每条原生射线
   ≥4片、支撑≥8km、跨度≥80km和长宽比≥12。短碎片、跨相邻射线的碎片家族
   容易在候选阶段消失。不能把这些门槛直接改成删除条件。
3. `source_envelope.detect`冻结的是旧SOURCE/LINE/GROUP提名经过窄走廊筛选
   后的范围，要求原始种子支持≥10km；尚未实现“所有RAW短碎片构成的对象”。
   原始强核缺失、对象太宽或边界破碎时没有可追踪父对象。
4. 反证：在独立本地进程把提名改成所有原始观测门，保留其他条件，对Z9591
   10:24/11:24、Z9598 08:42的剩余抽查门新增命中仍为0。仅扩大提名不够，
   需要对象表示和来源证据共同改进。该试验没有用于产品处置。
5. Z9591 10:24剩余106门中只有6门有偏振观测，SNR中位数6dB。Z9598 08:42
   剩余781门中201门有RHOHV，中位数0.965；单看RHOHV高低不足以删整片。
   该扇区45门已被天气/冲突保护，不能因视觉上像射线就解除保护。
6. 两站REF与Doppler扫描确实分开：低层REF覆盖约460km，Doppler层VR/SW
   覆盖约230km，且时间、方位数不同。Z9591西北≥250km完全超出低层Doppler
   覆盖；给Py-ART增加速度纹理不能凭空解决这部分。近处可建立真实位置、
   波束高度和时间受限的Doppler上下文，但不得拷贝成REF同门观测。

## 下一版按此顺序实现

### A. 原始短碎片候选与对象证据（先诊断，不直接删除）

- 在原生极坐标中保留短片段为候选，不先按1km或80km排除；统计实际支撑，
  缺口不补门，不增加有效观测长度。结合相邻原生射线形成有界碎片家族。
- 几何尺度来自真实距离门、方位分辨率及已知波束宽度；缺失波束参数时显式
  标记代理尺度。保存每片长度、窄带边界、径向一致性、占空比及各窗口证据。
- 对宽扇形、天气带和保护走廊分开建模，避免在天气背景中把随机小片无限串联。
- 为每个未处置对象保存明确原因：未提名／无父对象／支撑不足／边界不稳／
  观测缺失／天气冲突。复核脚本直接显示这些原因。

### B. 对象级来源关联与三路联合判别

- 有原始强对象：依据一次性冻结的RAW对象、源ID、范围、边界及模型残差关联
  弱尾段，不从新增尾段扩大范围或角度。宽源扇区要显式建模，不能套用仅窄带
  的父对象条件，也不能据扇区来源把区域全部清空。
- 无强对象：原始碎片家族只提名；要求多尺度几何证据之外的独立佐证，例如
  有效偏振纹理/相位圆统计、可用Doppler上下文、已验证的背景统计或过去体扫
  中已确认来源的一致性。时间重复或低SNR本身均不是充分证据。
- 偏振缺失时不能判为非气象；PHIDP使用圆统计处理0/360°，没有足够实际样本
  就弃权。DBZH/SNR的距离处理关系属于接收机处理特性，不能当第二张独立票。
- 邻站/上层缺测不是无降水；有可靠天气正证据时保护。被干扰与天气混合覆盖
  而无法可靠分离的门标为未知/隔离，不恢复为可信天气，不补成无雨。
- 可用历史只取当前扫描之前；2026-09-18晴空数据晚于2026-08-28测试个例，
  可用于明确标注的回顾研究，不能当该个例的因果先验或声称在线泛化通过。

### C. 最后清理与验收

- 小碎片清理仅作用于已确认非气象对象或其冻结范围内的合格残留，避免全场
  按像素数清理。按物理面积/长度检查不同分辨率下行为。
- 先固定scan_id、实际仰角、代码/配置/资产SHA及待核对对象，复核以上7例并
  找到09:48截图精确输入；加入真实小降水单体、雨带边缘、晴空背景和其他站/
  日期作为对照及独立留出。不得只根据这一天红框调阈值。
- 分别统计候选召回、确认干扰残留、真实天气误删、缺测动作及保护动作，不
  以总删除门数充当效果；通过后才完整QC→Hybrid→组合→图片/Web验收。

## 原始资料与适用边界

- [MIT ATC-454，2023，S波段RFI基数据处置](https://www.ll.mit.edu/sites/default/files/publication/doc/radio-frequency-interference-censoring-scheme-cho-atc-454.pdf)：
  针对加拿大S波段基数据，以偏振、非偏振射线和小碎片处置组合处理，保护天气
  信号；也评估相关系数纹理、相位圆方差。本研究借鉴组合结构，未照搬其阈值
  或宣称在中国S波段数据上已验证。全文网页提取可读，后续本地下载403，未
  取得可离线复核的完整PDF；实现前仍须完整核对细节。
- [wradlib官方模糊分类接口](https://docs.wradlib.org/en/stable/generated/wradlib.classify.classify_echo_fuzzy.html)：
  可供偏振纹理、背景和多普勒联合评分参考，明确返回全偏振缺测掩膜；缺测
  不应直接变成非气象资格。它不会自动解决跨射线碎片家族和来源追踪。
- [Py-ART速度纹理接口](https://arm-doe.github.io/pyart/API/generated/pyart.retrieve.calculate_velocity_texture.html)：
  需要速度与Nyquist信息，适合有实际覆盖的Doppler上下文，不解决超出量测范围。
- [DWD RFI研究](https://amt.copernicus.org/articles/15/6625/2022/)：
  使用SQI与脉冲功率波动识别，并聚合时间/角度对象；这些量不同于仅有DBZH/SNR，
  不能用SNR替代SQI或用SW替代脉冲功率STD。说明信号域证据和来源排查的价值。

图像全部干净且天气无损不能由当前基数据保证。对弱混合回波，获取SQI、
脉冲功率统计或I/Q、并排查真实干扰源，可能比持续放宽图像形态门槛更有效。

## 复核工具与证据

```bash
algorithms/.venv/bin/python scripts/plot_s_radial_review.py --diagnostics \
  --case z9591 10:24 2 --case z9598 08:42 0 \
  --output .build/s-discontinuous-20261001/new-diagnostics
algorithms/.venv/bin/python scripts/audit_s_radial_residuals.py \
  .build/s-discontinuous-20261001/new-diagnostics
```

当前证据：`.build/s-discontinuous-20261001/research-diagnostics/`中的NPZ、report.json、
residual-audit.json、corridor-probe.json、nomination-probe.json、doppler-inventory.json；
体扫矩量目录另见`research-moment-inventory-v2/report.json`。诊断脚本已保留，算法
行为未因本次研究修改，没有新部署或生产重算。

## A 步实现回执（2026-10-01）

第一步已在当前工作目录实现，B（独立联合判别/来源关联）和C（确认对象内
清理及完整产品验收）尚未完成，本目标继续。新增raw_families.py与配置开关
raw_fragment_families_enabled，默认关闭；不把诊断候选输入旧来源拟合或动作。

- 保留单原生门及不足1km片段；相邻径向按原始首片边界一次关联，最大60km
  距离缺口、180km范围，角度仅首片一个原生波束跨度。连续长RAW片段按范围
  上限分段，同时保留ORIGINAL_FRAGMENT_M，不丢失原始连续片长度。
- 输出原始对象ID、物理宽度、实际支撑/跨度、片段长度、20/60km双侧实测
  窗口和弃权原因。缺失双侧回波可提名，但不构成实测空场票。缺少真实波束
  宽度时BEAM_PROXY_MASK明示角分辨率代理。
- 候选与删除解耦，独立证据未接入前NO_INDEPENDENT_EVIDENCE始终有效；
  序列化验证拒绝没有ID、越界、缺测/保护区候选及非法诊断。新增测试11项，
  径向修订及子配置相关130项通过；未做完整worker/产品/Web验收。
- 首次实数据暴露超长连续片未分段的问题，已补回归并修复；另两例短片超过
  原诊断10000片上限，已改成按固定首片原生射线/距离索引关联，避免全场
  二次扫描，显式100000片预算超限仍仅该诊断路径弃权。最终8例全部非预算
  弃权，每例约0.2–8秒（本地离线测量，不是生产时延）。

| 固定输入扇区 | 上轮剩余门 | A步骤进入诊断的门 |
|---|---:|---:|
| Z9591 10:18/2 | 26 | 24 |
| Z9591 10:24/2 | 106 | 62 |
| Z9591 10:42/2 | 38 | 33 |
| Z9591 11:24/2 | 185 | 169 |
| Z9598 08:18/0 | 405 | 245 |
| Z9598 08:36/0 | 1406 | 174 |
| Z9598 08:42/0 | 781 | 64 |

这些是提名数，不是确认干扰/清除数；Z9598宽扇形残留仍需B的宽源对象模型，
不能用窄家族替代整个方案。09:48精确Web输入仍未绑定，不算截图复现。
全8例新增动作0、填门0、保护/缺测候选0。没有独立天气真值，不宣称误删率0。

保留复核入口：

```bash
algorithms/.venv/bin/python scripts/replay_s_raw_families.py \
  .build/s-discontinuous-20261001/research-diagnostics \
  --output .build/s-discontinuous-20261001/raw-family-review-new
# 后续从服务器取新快照时追加 --raw-families --diagnostics
```

最终证据 `.build/s-discontinuous-20261001/raw-family-a-final/report.json`，包含
输入scan_id、快照SHA、实现模块SHA和逐对象原因；同目录NPZ保留全部证据。
没有部署、重启或写服务器生产产品。下一步B必须从RAW冻结对象关联独立来源，
并完善偏振/相位圆统计和可用上下文证据；短片、低SNR或时间重复仍不能单独删门。

## B 步窄家族联合判别初版回执（2026-10-01，完整B尚未完成）

- family_joint.py：新增family_joint_enabled，依赖RAW提名开关。原有来源拟合
  仍使用原候选，联合资格在其后计算；身份关联放到新资格合并之后，避免
  step3的ID缺失。天气/冲突/几何/数值平台屏障保留，audit不执行隔离。
- 同一扫描内独立来源仅来自原先严格接收机匹配、原始线来源及已接受的形态
  路径；不使用新增家族动作作为种子。窄家族内原始种子实际支撑≥10km、
  边界稳定、距离原种子≤120km才可带出弱尾段；范围始终为冻结RAW家族。
- 无来源对象要求8km实测支撑、80km跨度、长宽比≥12、稳定边界和20/60km
  双侧窗口；再叠加已有可靠偏振门策略（SNR≥10，rho<0.7，或rho<0.85且
  |ZDR|>3）。低SNR/缺偏振/时间重复均不是新增票。相位圆方差和rho纹理
  按实际5km样本记录，至少8样本/2km；没有接受独立阈值，不用于单独删除。
- 父ID、原始种子ID、距离、弃权原因、偏振实际值与可用性、圆统计样本数
  可持久化；序列化校验复算偏振票、原种子支撑及最近距离，拒绝伪父ID、
  不足种子、伪偏振可用状态。新增8测试，相关138项通过。

8个旧快照只读联合回放：所有7个红框抽查扇区额外隔离为0；10:18全层新增3门、
10:42全层新增2门，均不在抽查扇区。其他快照全层新增0。Z9591 10:24的62个
新窄提名全部缺可靠偏振、双窗口及几何质量线；9门有少量来源但不足原种子
支撑。Z9598 08:42的64个提名中只有3门有可靠异常偏振票，全部仍未满足完整
几何/双窗口质量线。剩余回波不是已确认干扰真值。

105实际Worker内两例完整来源阶段回放（未发布产品）：Z9591 10:24仍全层45门
新隔离提议、Z9598 08:42仍9702，与上一版相同，RAW不变、保护/缺测动作0，
动态加载与序列化验证通过。保存NPZ/图/配置及模块SHA：
`.build/s-discontinuous-20261001/family-joint-transport-probe/`（Z9591）与
`family-joint-transport-z9598-probe/`（Z9598）。
窄家族单独证据：`.build/s-discontinuous-20261001/family-joint-b-final/`。

可复核命令：

```bash
algorithms/.venv/bin/python scripts/replay_s_raw_families.py \
  .build/s-discontinuous-20261001/research-diagnostics --joint \
  --output .build/s-discontinuous-20261001/family-joint-review-new
algorithms/.venv/bin/python scripts/plot_s_radial_review.py --family-joint --diagnostics \
  --case z9591 10:24 2 --case z9598 08:42 0 \
  --output .build/s-discontinuous-20261001/family-joint-live-new
```

**下一步不能跳过：** 完整B还需冻结原始独立来源的全部RAW范围/宽扇区，避免
以窄家族截断来源；补齐可用邻站/多仰角与受限多普勒上下文、必要时过去已确认
来源的一致性，缺测仍不投无雨票。随后C只清理确认对象内残留及完整产品验收。
目前没有新部署、重算或Web发布，不能声称红框已改善或全目标完成。

补充传输验收：大体积诊断快照合并一条Docker/SSH输出曾出现不完整base64/
缺块，失败目录不算成功回执。脚本现按体扫分别取流、3072字符分块、序号+
完整NPZ字节数/SHA校验后原子落盘。两例单独快照均全部分块通过（992/2743块），
分别为2,284,342与6,319,484字节。保存transport-diagnostic.json，不发布缺块图件。
单独只读邻域诊断full-source-neighborhood-probe.json进一步证明：Z9591 10:24
有17个、11:24有75个窄残留在原始已识别源附近120km内且直连无保护屏障；
这些是待核对来源线索，不是删除资格，需要下一步从完整来源冻结父对象。

## 继续研究：完整来源、宽扇区与可辨识性（2026-10-01）

本轮核对代码和已有固定快照回执，不新增生产动作。当前
family_joint.qualify 中 `original_source & candidate` 将原始源先截进窄家族，
再计算10km来源支撑，确实可能丢掉本属于同一原始对象的种子。180km家族
分段也不应成为原始来源身份的终点。修正需要独立的父对象契约，不能把所有
邻近种子并成一个源，也不能把120km邻域probe直接升级为删除。

### 研究确定的实施顺序

1. **先冻结完整原始来源账本，再关联窄残留。** 在任何新尾段动作之前保存
   原始SOURCE/LINE/GROUP来源类型、原始种子、父ID、实际径向支撑、距离起止、
   各距离窗口左右边界及拟合残差。原始种子不能先与窄候选相交；来源被保护
   或扫描几何切断时分为独立对象。旧路径只给掩膜的部分，须显式重建并验证
   原始对象，不能拿连通域ID当作已确认同源证据。
2. **固定父对象的一次关联。** 每个窄家族可以指向完整父对象，但必须通过
   原始边界、径向方向、实际支撑和距离模型一致性；多个不兼容父对象争用时
   弃权。在可观测的间隙检查天气/冲突屏障，在不可观测间隙保留unknown；
   不把空隙补成无雨。禁止新尾段成为种子、扩大父边界或接力越过长度限制。
3. **宽扇区单独建模。** 当前POWER_FAN仅保存逐门命中及残差，没有完整父
   扇区身份。需保存原始角域、径向窗口、边界轨迹、功率距离模型参数和有效
   支撑；对离散边缘/尾段用窗口统计复核。宽扇区内只有实测且符合模型的门
   才有处置资格；整个扇区存在源不等于整扇区全是源。拟合和检查窗口分离，
   避免用同一组门拟合后以其低残差自证泛化。模型残差仍不是独立偏振票。
4. **无父对象走独立证据路径。** 多尺度形态提名后结合可用偏振、受限的
   Doppler、真实位置/波束高度匹配的邻站与上层、过去已确认来源。当前体扫
   一部分窗口确认后可以检验其余窗口，但不能以待判残留更新来源。低SNR、
   相同方位、重复出现及缺上层回波，均不能独自授权隔离。
5. **最后才清理与发布。** 只在有资格的冻结对象内处理残余小片。按物理尺度
   定义参数、固定scan_id与版本，复核天气小单体、带状天气和其它站/日期；
   完整QC→Hybrid→组合→图件链必须证明同版本及排除门不回流。图中仍保留
   的门逐对象标注“未提名/无父对象/几何不稳/缺独立观测/天气冲突”，而不是
   把不确定回波以无雨输出。上述新父账本及宽模型尚未实现。

### 为什么不能只做来源关联

| 固定抽查区 | 剩余门 | 窄RAW提名 | 原始源邻近且直连无屏障的线索 |
|---|---:|---:|---:|
| Z9591 10:24/2 | 106 | 62 | 17 |
| Z9591 11:24/2 | 185 | 169 | 75 |
| Z9598 08:36/0 | 1406 | 174 | 8 |
| Z9598 08:42/0 | 781 | 64 | 24 |

这不是清除预测或召回率：线索仍未通过完整来源契约，剩余也含潜在天气。
但它否定了“修完窄家族来源关联即可彻底清除”的推断，特别是Z9598宽对象
覆盖不足。09:48尚未找到截图精确输入，不作解决证据。

### 库与信号域边界再次核对

- [wradlib官方接口](https://docs.wradlib.org/en/stable/generated/wradlib.classify.classify_echo_fuzzy.html)
  可将实测偏振、Doppler和背景变量组合成模糊分类，并返回偏振全缺测掩膜；
  可用作对象内证据计算组件，默认隶属函数需本地校准，输出不能冒充已校准
  误删概率，更不能自动补齐来源身份。
- [Py-ART官方速度纹理接口](https://arm-doe.github.io/pyart/API/generated/pyart.retrieve.calculate_velocity_texture.html)
  要求速度字段并处理Nyquist。现有REF约460km/Doppler约230km，不足以解决
  Z9591≥250km残留；必须保留实际覆盖、时间和高度注册，不广播成同门速度。
- [DWD原始研究](https://amt.copernicus.org/articles/15/6625/2022/)
  利用SQI和脉冲接收功率STD联合识别RFI，研究指出混合干扰与天气不能从其
  基数据可靠分离。该结论来自C波段系统，不能直接套其阈值或来源类别到S波段；
  对本项目的推论是：若矩量层证据仍不能区分弱混合目标，下一条有效路线为
  核实设备是否能提供SQI/脉冲功率统计/IQ，并定位真实干扰源。SNR不是SQI，
  谱宽不是脉冲功率STD。仅当前矩量不能承诺全部识别且天气零误删。

本轮只更新研究方案，未部署或重算，七抽查区的窄联合初版新增隔离仍为0。
下一实施目标明确为完整原始来源账本与宽扇区对象，之后才判别/清理/全链验收。

## B 完整原始来源账本落地回执（窄关联诊断，2026-10-01）

- 新增source_ledger.py及source_ledger_enabled，默认关闭且依赖RAW提名。来源
  在新家族资格前冻结，保留所有独立原始来源及来源类型，包括缺窄边界的宽源
  和短源；不再与180km家族分段求交后计算来源支撑。账本独立于候选、接受尾段
  和DBZH_QC。来源距离缺口>60km或原生屏障分开ID。
- 支撑≥10km、原始窄边界稳定才作几何关联；原始种子一次搜索全部一跳相邻RAW
  的有界范围，最大距原种子120km。新关联不产生来源票或动作；两个及以上来源
  竞争永久弃权，第三来源不得覆盖歧义。RAW所有权也保留永久歧义。原始来源
  不是已确认干扰身份，宽源目前按原始射线记录，不宣称已建成完整宽扇区父ID。
- 序列化复验来源类型/归属、实际支撑/起止、父ID和原种子最近距离/RAW范围。
  增加8项测试，包含跨180km分段、短/宽源、不补空隙、空隙保护、几何断裂、
  三来源争用、无角向接力、原始强核从QC删后仍保留、源writer持久化；相关
  146项通过。该开关不改变旧拟合、候选或任何动作，测试逐字段复核旧输出。

8个固定快照最终回放：七抽查区新增几何关联仍为0；全层仅10:18/10:42有5/1
个此前可见残留获账本几何关联，尚不是动作。其余全层0。新增动作/填门0，
RAW与缺测/保护不变量保持。最终输入/模块SHA回执：
`.build/s-discontinuous-20261001/complete-source-ledger-final/`。

真实105 Worker内两例完整来源阶段回放通过，串行按体抓取的分块协议也实际
通过；全层隔离提议仍45/9702，与前版一致，非生产产品或Web更新。证据：
`complete-source-ledger-live-v1/`的NPZ/PNG、transport-diagnostic.json和最终
校验补充latest-validation.json（保留原运行模块SHA，未改写旧快照身份）。

新增诊断改变了下一实施重点：Z9591 10:24两个完整原来源分别支撑14.75/11km，
不是支撑不足；它们的全部门不满足窄走廊边界，因此账本拒绝链出17个邻近线索。
11:24九来源中三短、三缺窄边界、五边界不稳（原因可叠加）。Z9598 08:42共1036
个原始射线范围来源，862缺窄边界、39不稳、835短。不能清空这些父范围来追求
视觉结果。下一步应做距离窗口的可变宽度边界/宽扇区父模型，并分段记录有效与
未知几何；全源“每一门必须窄”的条件不能承担这一步。多角/邻站上下文、动作
资格、C清理及完整QC→Hybrid→图/Web仍未完成。没有部署/重启/生产写入。

复核入口已保留：

```bash
algorithms/.venv/bin/python scripts/replay_s_raw_families.py \
  .build/s-discontinuous-20261001/research-diagnostics --ledger --joint \
  --output .build/s-discontinuous-20261001/ledger-review-new
algorithms/.venv/bin/python scripts/plot_s_radial_review.py \
  --source-ledger --family-joint --diagnostics \
  --case z9591 10:24 2 --case z9598 08:42 0 \
  --output .build/s-discontinuous-20261001/ledger-live-new
```

## 再研究：覆盖与判别分离，不能以整扇区清空闭环

### 最新用户约束与基数据主线

用户明确：目前只有基数据，无SQI/脉冲功率统计/IQ，改用其他方法。该约束
覆盖本文早先关于核查设备信号域资料的建议；它们不再是后续实施的前置条件。
105固定八体扫source_moments实际均不含SQI，且KDP/TREF出现在原始清单但未
进入当前标准化字段。不能假定后者具有已确认单位/处理语义，也不能充当SQI。
保留audit_s_source_moments.py与original-moment-inventory-v1.json。

跨射线模块fan_angular.py只作诊断：独立原始功率状态、双侧两波束内插值、
至少三原始参考射线、内部参考射线验证、目标/保护块排除、120km距离约束、
无天气/几何越界。5新增用例及相关套件通过；未接生产动作。原始门邻近探查
显示08:36有272门两侧两波束内存在原来源门，但严格独立模型实际0；不能把
门的存在当可靠来源模型。最终fan-angular-probe-final-v1的1354候选：653缺三
原射线、663缺三邻近射线、38缺三独立模型，互斥分区之和等于全部候选；其它
六ROI模型也0。此反证否定直接跨射线插值即可清除的假设，不证明残留都是天气。

基数据上下文核验有新发现：固定七目标扇区QC的V7_VERTICAL_SUPPORT_SCORE、
V7_CROSS_RADAR_SUPPORT_SCORE与WEATHER_SUPPORT_SCORE实际有限门均为0，状态
是未知，不是无天气。Z9598历史持续性有较多实测样本；源码证实该字段聚合的是
过去候选票，不能等同过去已确认干扰来源。base-only-context-inventory-v1/v2
保留字段、来源SHA与实际冻结上下文身份；下一实现转向核验过去独立Stage A
确认来源的时空匹配，再关联完整来源范围。禁止从本轮弱碎片或历史候选持续性
单独生成删除票。邻站/仰角继续只使用真实可比观测，未知不填零。当前无部署、
服务重启、产品写入或Web更新；完整QC/Hybrid/组合/天气对照仍未完成。

base-only-context-inventory-v3已正确解析QC的radial_context JSON：Z9598三例各
恢复6份实际冻结上下文artifact、decision_cutoff与参数SHA，下一步可以沿已有
基数据输入重算过去独立确认来源，既不依赖新增设备字段，也不重新选未来体扫。

### 原始功率状态实现与否决回执

fan_states.py已落地，默认关闭的fan_power_states_enabled依赖fan_joint。
只用同原始射线来源、排除目标与保护块的原始门发现最多三个状态；各状态
独立验证实际支撑/跨度/块数、功率残差、交替块一致性和实测SNR区间。
多源/多状态争用永久弃权，匹配只诊断，不增加旧拟合输入、动作或新来源。
源writer保存字段并重算验证；六新增用例覆盖原始双状态、目标不得自训、
缺SNR/保护空隙、参考不足、篡改和完整来源入口输出不变。相关套件通过。

固定八快照最终回放`.build/s-discontinuous-20261001/fan-power-states-final-v1`
保持动作/补门0、RAW不变。七目标扇区，Z9591 10:42/11:24匹配1/4门，
其余0，与旧单状态原模型相比没有增益。重放入口新增--power-states。
因此本模块不启用生产，不把合成双状态通过冒充实际红框解决。

逐门互斥原因回执fan-power-state-partition-v1.json：Z9591 10:24的106门中
93无有界同射线原来源、13无足够独立状态；Z9598 08:36的1406门中52未提名、
1294无有界同射线原来源、60原状态不足；08:42为227/352/202。剩余不能主要
归因于目标残差阈值过严。下一实现必须核验完整原始父对象的跨射线来源/角向
增益与真实上下文，不能从目标弱碎片新增低功率状态来制造匹配。

```bash
algorithms/.venv/bin/python scripts/replay_s_raw_families.py \
  .build/s-discontinuous-20261001/research-diagnostics --power-states \
  --output .build/s-discontinuous-20261001/power-state-review-new
```

本轮核对现有默认关闭的宽RAW对象与fan_joint原始来源模型，并对固定八快照
补充只读实验。结果只代表来源阶段抽查区，不是污染召回率或生产Web验收。

| 抽查区 | 来源阶段残留门 | 宽RAW提名 | 原来源模型可用 | 全偏振实测且SNR≥10dB |
|---|---:|---:|---:|---:|
| Z9591 10:18/2 | 26 | 26 | 0 | 0 |
| Z9591 10:24/2 | 106 | 106 | 0 | 0 |
| Z9591 10:42/2 | 38 | 24 | 9 | 0 |
| Z9591 11:24/2 | 185 | 168 | 16 | 0 |
| Z9598 08:18/0 | 405 | 362 | 0 | 121 |
| Z9598 08:36/0 | 1406 | 1354 | 38 | 276 |
| Z9598 08:42/0 | 781 | 554 | 148 | 141 |

结论：Z9591 10:24已经被提名106/106，继续扩形态不能弥补原始参考不足。
Z9598 08:36已提名1354/1406，但原来源模型只有38门可用；问题主要转到来源
参考及独立证据。目标扇区可含天气，表中“残留”并不等于已确认污染。
Z9598实测RHOHV中位数约0.965–0.980，也不支持把全部残留当低相关杂波。
09:48现有快照仍没有截图中的对应对象，必须先绑定准确scan_id。

### 反证实验：接收机距离修正不是主解

保留scripts/audit_s_fan_processing.py。从全部实际配对DBZH/SNR估计既有接收机
距离处理关系，目标20km块及左右保护块在所有射线上排除；奇偶射线组独立估计
且一致才记实测。使用完整原始来源拟合，目标不能训练自己，缺测不补票。
该距离关系不是干扰证据；脚本只输出诊断，不产生动作或QC图件。

模型可用的四组目标均得到实测距离项。按原一致性标准，Z9591 10:42通过由1变3、
11:24由4变5；Z9598 08:36/08:42仍为0。故不能以补距离项或增大残差容忍度作为
主修复。接收机修正后的诊断通过不自动成为新的删除资格，也不是3/5已证实污染。

回执fan-processing-audit-v1.json与fan-residual-availability-v1.json位于
`.build/s-discontinuous-20261001/`。新版脚本保留输入、模型、模块、脚本SHA；
旧回执不覆盖。复现：

```bash
algorithms/.venv/bin/python scripts/audit_s_fan_processing.py \
  .build/s-discontinuous-20261001/research-diagnostics \
  .build/s-discontinuous-20261001/fan-joint-v1 \
  --output .build/s-discontinuous-20261001/fan-processing-review-new.json
```

### 后续实施顺序与否决条件

1. **宽原始父对象与弱功率状态。** 在冻结原始边界内，用已有独立判定的强源
   训练角向响应和分段功率状态；按独立距离块检查。弱状态若没有原始训练支撑
   则诊断保留，不能从待删弱尾自建状态。禁止跨天气屏障、借邻门连续接力。
2. **实测偏振联合与弱信号专用路径。** 对实际有RHOHV/PHIDP支持的门计算物理
   窗口纹理与圆方差，先对象提名再门级检查；需要纯天气/纯污染样本冻结校准。
   PHIDP是否未经处理、质量阈值与编码先核实。弱信号偏振缺失时不造偏振票，
   用户已确认仅有基数据、无SQI/IQ/脉冲功率统计；不再依赖新增设备数据，转用过去独立来源与实测上下文。SNR不能替代SQI。
3. **无锚对象的上下文补证。** 只用过去体扫已确认来源，不从本轮未决片段扩张；
   邻站/仰角按真实位置、射线时刻和波束高度配对，缺测保留未知。重复出现、固定
   方位只是线索，不能单独删除；REF460km外圈不借用Doppler230km的虚假同门数据。
4. **C步清理与独立验收。** 仅在已有处置资格的固定对象内去残余；冻结跨站、
   跨时次、其他日期和带状/小单体天气对照后评估，再完整运行QC→Hybrid→组合→
   PNG/Web并核验版本与禁入门。没有独立天气对照不能宣称彻底解决。

MIT S波段研究明确描述弱信号可保留反射率而偏振被处理器屏蔽，且有天气形似
spike的误删，因此采用分数连续性配合SQI，而不是形态无限放宽。DWD研究用
SQI+脉冲功率STD支持来源检测与实际干扰源处置；此为C波段设备方案，不能照搬
阈值到本项目。已有官方参考链接见上文。本轮未部署、未重算或更新Web。


### 仅基数据路径：过去独立来源复核（2026-10-01）

用户明确没有SQI、IQ或脉冲统计，不以此为前提。新增只读脚本
`scripts/audit_s_past_sources.py`，使用旧QC回执中的实际历史输入URI/SHA，
按当前服务器配置重新计算独立Stage A。仅统计径向＋偏振实际拒绝门；
时间重复候选、普通杂波拒绝、预算降级不算这一证据。
严格过去时刻、截止时间、输入SHA、原生距离/仰角/方位几何和健康状态均检查。
当前配置与旧回执不同会明确记录，不能冒充原版本重现。

初次08:36复核：1,406个剩余ROI门中1,364有过去实际观测，1,018与至少一个
过去径向＋偏振拒绝位置重合，两个过去体扫同时命中0。此为来源线索而非
当前污染真值，处置动作0，未部署/更新Web。回执
`.build/s-discontinuous-20261001/past-source-0836-v1.json`。

另发现旧邻站/垂直上下文支持虽有输入清单，实际匹配被`current_beam_missing`、
`terrain_missing`阻断。不能将支持分数缺测当没有天气，也不能宣称上下文已生效。
下一步需要冻结过去来源对象及范围，再结合当前原始父对象边界、实测偏振和
天气屏障进行单跳资格判断；补齐可计算的波束几何输入并核验上下文实际可用率。

复核命令：
```sh
python scripts/audit_s_past_sources.py \
  .build/s-discontinuous-20261001/base-only-context-inventory-v3.json \
  .build/s-discontinuous-20261001/research-diagnostics \
  --output .build/s-discontinuous-20261001/past-source-review-new.json
```

全八例只读回放完成，回执`past-source-all-v1.json`：Z9598 08:18/08:36/08:42
过去至少一体扫独立径向＋偏振证据重合分别11/1,018/314门；08:42有124门获
两体扫证据。Z9591四个非空ROI全部0，过去实际观测分别0/1/0/3门，不能把
缺少过去同门数据当反证。09:48快照仍无目标ROI，不算截图复现。所有动作0。
因此过去来源可优先补Z9598，但不能单独覆盖Z9591或所有无锚碎片。


### B：固定过去来源与当前实测联合资格（2026-10-01）

新增`radial_revision/past_sources.py`及回放`--past-sources`。只从过去独立
Stage A径向＋偏振拒绝门冻结每原生射线来源范围，支撑≥10km、跨度≥60km、
三个20km块；原始断段间隔≤60km。当前目标只在此原始范围内、距原始支持
≤120km，按同cut/实际射线几何映射，不因当前残留关联而扩大来源。

当前仍须冻结RAW父对象、无天气/冲突屏障、实际SNR≥10dB及rho≤0.8，
20/60km两个窗口至少25%门距支持、有效偏振占观测至少50%、异常占实际
测量至少70%。此门槛是待校准研究条件，不以截图剩余门数为污染真值。
历史两次确认仍不能代替当前偏振、天气样态或缺测保留。模块仅离线资格，
不改引擎动作，未接入生产。

6项新增测试通过，径向修订全套185项通过。冻结输入
`past-source-bounds-all-v3.json`（含当前输入SHA/cut身份）及回放
`past-source-joint-v2`：Z9598三个ROI关联候选11/1255/306，合资格全部0；
08:36候选993门缺当前可靠信号、262门偏振像天气；08:42为271/35，
08:18为9/2。不能拿前轮历史重合1018宣称已清理。
详细分区`past-source-joint-partition-v1.json`；缺测/保护未处置、RAW不变。

旁证资源实机复核：105实际S Worker的RAINPULSE_RADAR_CONFIG_DIR、
RAINPULSE_ANCILLARY_CONFIG、RAINPULSE_ANCILLARY_ROOT均未设置；
load_geometry_resources对Z9591/Z9598返回radar_config_directory_unavailable。
配置bind已在容器可见，但不是加载成功。实际使用的Compose文件链未包含
提供这组资源的V7覆盖层。仅设置资源后仍须核验地形资产和垂直基准，
不能把1985高程或文学偏移直接伪称verified_egm2008。
下一轮优先补现有资源的加载与实际几何支持审计，或使用同站相对波束
几何的正向天气支持；跨站或高仰角缺测仍不得作为污染证据。


### 资源加载与同站相对几何正向支持（2026-10-01）

105只读一次性容器核验：绑定现有configs和runtime/ancillary/assets并指定
三个资源变量后，Z9591/Z9598均返回resources_loaded，DEM清单SHA为
9f4108b05da5b8a119b06e96225370be0d02a1d75aad1d3b2aa127b5ee571f44。
原站点1985基准返回incompatible_with_epsg_3855，不能冒称可信跨站几何已恢复。
新增deploy/docker-compose.qc-geometry-context.yaml仅补S Worker资源变量/只读
资产挂载，保留原镜像与profile，尚未加入线上Compose链。

新增relative_vertical.py和只读audit_s_relative_vertical.py：同一真实站点/体扫，
按原生仰角计算地面弧长与相对天线高度（共同天线绝对高度项抵消），不是
相同斜距门直接配对；检查实际方位、时间≤300s、相对高度差≤3km、射程
内插支持以及波束足迹。只使用独立Stage A donor_usable、实际rho≥0.95、
SNR≥10dB、三邻门实际支持的上层天气观测。仅正向score≥0.7，缺测/不可比
保留NaN，绝不提供“上层无天气”负票、不宣称绝对高程已验证、不可作跨站
或地形证据。此为研究保护通道，未接入引擎/生产。

四项新增测试通过，径向相关全套189项通过；恒定天线高程变化、缺测/不可信
上层、不同站体扫、实际时间/高度/原生地面足迹、孤立上层及不外推均覆盖。
真实08:36/08:42回放正向ROI支持均0，不能宣称改善。08:42
relative-vertical-0842-v2：实际几何匹配97,384观测对，干净上层三邻门匹配0。
上层独立可用8809/8302门，经真实偏振/SNR筛选183/48门，未形成可靠连续
上层支撑。所有动作0，原RAW/旧产品未写。

额外current-and-past-source-all-v1按当前105 profile重算当前独立Stage A：
八固定输入剩余ROI径向＋偏振拒绝全部0，且非预算降级。这排除了“仅这次
独立Stage A更新即可清除”的假设，仍不能将旧快照称为当前Web状态。
下一步主线仍是弱/离散当前测量的原始来源联合模型、独立天气对照与完整
源/QC/Hybrid/组合/图件验证；当前资料不足时保留unknown，不能靠恢复资源
加载或新增诊断覆盖冒充目标完成。

相对几何复核命令：
```sh
python scripts/audit_s_relative_vertical.py \
  .build/s-discontinuous-20261001/research-diagnostics \
  --time 08:42 --output .build/s-discontinuous-20261001/relative-review-new.json
```


### 原始来源轮廓追踪：首次非零目标增益（2026-10-01）

`raw-parent-source-footprint-v1.json`定位到多个RAW父对象具有足够长的已识别
原始来源、残留位于来源方位范围内，但不是同一功率状态，旧功率拟合不能
关联。新增source_footprint.py和replay --source-footprint：仅原始来源门训练
轮廓，目标及相邻20km块不参与，至少3个外部块有相邻原始源射线、10km
支撑/60km跨度，边界漂移≤2原生间距、宽度≤12°，不跨已知天气/冲突屏障，
距原始来源≤120km且只在原始范围内。强度状态不一致不自动否决已确认
来源内部的有界几何关联，但无原始来源绝不凭RAW家族单独资格。
当前rho≥0.95且SNR≥10dB则保守保留，不将该票称天气真值。没有递归/新源/
补门，也尚未产生实际引擎动作。

5个新增用例通过，全套194测试通过，分别覆盖冻结范围、无锚/局部训练、
天气屏障/缺测、原始源角向间断/边界游移及实际偏振保留。
source-footprint-v1为探索版，v2增加原始源射线连续性和当前偏振保留；
最终八例ROI新增资格0/0/0/8/0/0/7/53（依脚本顺序09:48,10:18,10:24,
10:42,11:24,08:18,08:36,08:42）。09:48仍未复现截图，不能算已清理。
08:42全层可见提议428门（含ROI外北向残留），08:36为8门，10:42为9门。
这些是离线提议，非独立污染真值，未上线。

可重复出图脚本scripts/plot_s_source_projection.py读取冻结原快照及回放SHA，
比较原RAW、上一源阶段投影、新来源轮廓投影，红圈为增量提议。
source-footprint-images-v1图件已实际打开08:42检查：北向约200–450km
径向带减弱，南侧仅部分碎片改变，多条长残留仍存在，未解决全部问题。
图片明确注明不是完整QC/Worker/Web。

```sh
python scripts/replay_s_raw_families.py \
 .build/s-discontinuous-20261001/research-diagnostics --source-footprint \
 --output .build/s-discontinuous-20261001/footprint-replay-new
python scripts/plot_s_source_projection.py \
 .build/s-discontinuous-20261001/research-diagnostics \
 .build/s-discontinuous-20261001/footprint-replay-new \
 --case z9598_0842_sweep_000 --output .build/s-discontinuous-20261001/footprint-images-new
```

下一步将这一有实际增益的有界关联接入默认关闭的引擎资格，保留完整可
重算的writer证明与几何弱证据标志，再实际105源阶段回放及完整流水线。
同时处理08:18/10:24/11:24无锚或断裂目标，不能提高增益数字代替其完成。

### Engine integration and proof replay

Added the default-off `source_footprint_enabled` configuration dependency and
engine candidate/geometry/action wiring. It does not change the frozen original
source ledger and audit mode remains action-free. Serialized validation reruns
qualification using native geometry/order, original source IDs, observed/barred
inputs and actual polar availability, rejecting forged qualification/parent,
reference scalars, coordinates, source removal and gaps/barriers. The integration
fixture has nonzero weak-tail proposals and exercises source writer validation.

Eight immutable snapshots replay successfully with serialized proof validation
in `.build/s-discontinuous-20261001/source-footprint-validated-v1`: remaining ROI
increments remain 8 / 7 / 53 for 10:42 / 08:36 / 08:42. Other target cases are
unresolved. Related suite: 197 tests pass; default-off means no production change.
The reusable 105 read-only source-stage script now supports `--source-footprint`
and injects candidate modules only in a separate diagnostic process.

105 read-only candidate engine replay is now confirmed for exact Z9598 08:42
scan `d12ac95a-4039-5ae7-99f8-6e134da05e7f` in
`.build/s-discontinuous-20261001/live-source-footprint-v2`. Current profile file
SHA `63fd29b17a14c844ac0e2a1ad8c7062bffe899ad78946699e4c3f08fa6f8e351`.
Footprint qualification contributes 428 visible incremental proposals against the
same candidate configuration with only that switch off; 53 lie in the south ROI.
RAW unchanged, revision writer evidence replay passes. Total candidate proposal
count 10130 includes other research paths and is NOT the footprint improvement.
No worker restart, object write, deployment or Web publication was performed.
The first live v1 accidentally also enabled window source tracking via CLI
implication; preserved as diagnostic evidence, superseded for isolated footprint
comparison by v2 with window tracks explicitly false and switch-only baseline.

### Remaining-target decision audit

`source_footprint.py` now persists the precise rejection stage and validates it
by original-evidence replay. `scripts/audit_s_source_footprint.py` binds frozen
snapshot/replay SHA and scan identity, partitions every remaining ROI gate and
records original support/kind, RAW extent and actual outer-SNR availability.
Evidence: `.build/s-discontinuous-20261001/source-footprint-decision-audit-v2.json`.
Eight prior eligibility/proof arrays remain exactly unchanged; 198 related tests
pass. This is diagnostic improvement, no extra QC deletions/deployment.

08:18 has 352 unanchored candidates of 362, so relaxing the existing footprint
boundary cannot solve this main case. 08:36 has 486 outside original extent,
389 guard-excluded support failures and 209 unanchored; 08:42 has 292 outside
extent,172 reference support failures and227 outside candidate. These explain
why a global threshold reduction would break different protection boundaries.

A possible next evidence path is measured bilateral SNR with multiscale RAW
tracking, retaining native missing/gap/weather barriers. Unanchored parent59 at
08:18 has257 remaining gates,32km RAW span,3.96deg angular width,target measured
SNR median9.5dB, both outer rows100% measured and100% <=3dB. Parent107 at08:36 has
205,27.25km,2.95deg,median8dB,both outer rows100% measured/quiet. These are measured
signal contrasts, not missing-as-zero, but their short spans and missing reliable
nonweather evidence are insufficient to delete. Next inspect native RAW family
segmentation and window-specific boundaries rather than widening source ancestry
or counting recurrence alone as pollution.

### Measured signal-space alternative: tested, not promoted

Added diagnostic `signal_tracks.py` and reusable replay flag `--signal-tracks`.
It nominates only actually measured REF/SNR>=5 gates on native geometry, freezes
RAW initial angular bounds, requires >=60km identity, <=8deg width, native-safe
fixed outer rays, >=3 target/guard-excluded20km reference blocks over>=60km with
>=20km aggregate observed support. Both outer rays require90% measured/<=3dB
signal in reference and target windows; >=80% reference blocks must satisfy the
bilateral condition (small local flank contamination is excluded, never treated
as quiet). Reference median>=7dB,p90window variation<=2.5dB,split<=1.5dB; targets
must match centre within2.5dB. Reliable currentrho>=.95/SNR>=10 retains weather-like
gates. Missing is not quiet. Model matches are diagnostics, not confirmed RFI,
not sources, not actions; no engine/writer promotion.

Five tests exercise target/guard exclusion, bilateral missing/contamination,
short/natively gapped/protected objects, local contaminated reference exclusion,
weather-like retention and RAW immutability. `signal-tracks-v1/v2` contain8 actual
frozen replays with module/inputSHA. Both have0 additional model matches within
every target ROI. Whole-cut v2 model matches394/566/3902 for10:42/11:24/08:42 are
NOT evidence of target improvement or pollution. v1 weather-like veto retains
5440/7680 on10:18/10:42, showing signal-shape nominations can overlap weather-like
returns and must not be blanket deletion.08:18 has no sufficiently longsignal
families; selecting SNR>=5 does not reconnect its27–32km unanchored patches into
>=60km independently supported objects. Keeping this branch diagnostic avoids
promoting zero target gains or noisy assumptions. Next work must address actual
native width/segmentation and independent available volume support; not lower
reference requirements simply to force these patches to match.

## 2026-10-01 新版发布恢复与西南无锚分区

v3的首个正常QC Worker完成计算/数组验证后，因继承名称追加超过摘要512字符
而发布失败；v4改为短版本名+完整父配置语义摘要，parser在数组读取前拒绝
超长名称。新job b9460c88…已在105正常SUCCEEDED，对应grid完成，顺序脚本
正在下一个雷达站继续。未宣称组合/PNG已更新；状态和日志为权威证据。

复核脚本audit_s_source_footprint.py默认全原生视场，可显式选择跨北方位角和
实际米制距离，不再把站点ID当区域规则。命令：

```bash
.build/xqc-zf702-investigation/venv/bin/python scripts/audit_s_source_footprint.py \
  .build/s-discontinuous-20261001/web-aligned-0818-v1 \
  .build/s-discontinuous-20261001/web-aligned-0818-components-v2 \
  --azimuth 210 270 --range-min 100000 --exclude-qualified \
  --output .build/s-discontinuous-20261001/web-aligned-0818-sw-v2.json
```

实际Z9598 08:18 scan9ee02e8c…西南210–270°/100km外，在已有提议之外剩余
367可见门，全部source-footprint理由为no_original_source；第3层同区0。
367门均有实测SNR，中位8.5dB；239门有RHOHV，中位0.98，247门有
ZDR/PHIDP。中心两侧±2原生射线，367个目标分别有281/287个“DBZH不可用但
SNR实测≤3dB”的侧邻；这和侧邻全部缺测不同，不可填零伪造无回波。
原discontinuous候选/实测判定/动作均0，还未证明单片1km门槛是唯一原因。

下一诊断：保持原始几何、窗口总支撑、边界稳定及天气保护不变，分离连续
单片长度与整个对象支撑的影响，复核真实短片是否只是被候选提名挡住。
实测低SNR侧邻可作为有效噪声观测，不能自动转换为气象真值；缺测侧邻仍
弃权。高相关回波及独立天气证据必须保留，不能为清除367门而直接降低删除
阈值。证据JSON保存于上述输出和web-aligned-0818-sw-moments-v2.json。

### 2026-10-02: actual published lineage corrects the 11:24 residual diagnosis

The reusable published-QC audit now has explicit `--selection source-added` in
addition to the unchanged default `source-remaining`. Both selections require
matching finite binary masks and are observations only, with the original
snapshot/scan/RAW and exact diagnostic-consumed QC URI checks preserved.
Eight audit tests pass. This does not activate a morphology candidate.

Fresh 105 v5 refresh state is DONE for all eight slots. Actual Web catalog and
stored QC were independently read for Z9591 sweep 2, NW 270–360 degrees and
range above 300 km:

- 11:24: 130 locally proposed gates; all 130 quarantined in the exact Web-consumed
  QC, zero renderer-eligible. Diagnostic 9262c85d-6088-5a8e-ac87-120aa60e518d.
  The fetched current QC PNG was inspected: the remote NW fragment group is gone.
  Earlier zero new candidate count was not proof that this case remained unresolved.
- 10:42: 36 local residual gates; six already quarantined, 30 still eligible.
  Diagnostic cdcd1a34-d2a5-5212-a501-4a532b6120b1. Current fetched PNG inspected;
  the remote NW fragments remain. Both QC parameter hashes equal the active v5
  parameters, and original RAW equality passes. This is not a stale-profile explanation.

Receipts and actual PNGs are retained locally as
`.build/s-discontinuous-20261001/published-source-added-1124-v1.{json,png}` and
`published-source-remaining-1042-v1.{json,png}`. They are private local evidence,
not committed data or independent weather truth.

The complete original 10:42 constellation history contains qualified-looking
and insufficiently observed members; unknown side measurements are retained.
Its remote group spans only about 21 km. Current segment requirements therefore
cannot authorize it independently; one longer segment also spans only three
20-km windows. Next investigate a separately validated short-object shape rule
or genuinely observed repeated geometry evidence, without globally lowering
length/window thresholds, borrowing support across failed members, or treating
missing observations as dry. Broad weather-connected fans remain a separate
unfinished requirement. No claim that all eight cases are solved.

### 2026-10-02: short-segment geometry assessment within complete RAW parents

Added a research-only assessment for original consecutive member segments. It
uses 1/2/5-km windows anchored at the object's start (not arbitrary absolute
range-bin phase), at least four members, 15–60-km span, 5-km actual support,
complete measured bilateral acceptance, center drift <=0.25 beam and width
drift <=0.5 beam. All full-parent weather/geometry and failed-member interval
barriers remain. The assessment cannot authorize engine actions; unanchored
short objects outside the existing full-parent detector are not yet handled.

Real full-RAW 10:42 replay (short-segment-assessment-1042-v1) still gives 36
selected residuals / 18 nominations / 0 strong. Remote original runs have only
three acceptable members: one 7.5-km/4.25-km support run contains incomplete
bilateral observations; another 17.5-km/4-km support run has insufficient support
and unstable measured width. A failed member splits the apparent remote group.
The proposed stronger short geometry therefore does not justify deleting this
case. Long 61-km segments are explicitly outside this short-object scale.

Two new tests establish range-origin translation invariance, incomplete-side
rejection, weather-parent/barrier protection and unstable-boundary rejection.
The full scoped suite (285 tests before the final short-scale upper bound) passed.
No production activation or added deletion authority. Next test observed exterior
windows against local contamination and actual multi-cut corroboration, retaining
missing observations and full original object boundaries; do not tune the short
rule solely until this screenshot passes.
