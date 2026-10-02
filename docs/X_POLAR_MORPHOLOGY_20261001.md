# X 原生极坐标形态质控契约

## 目的与边界

以原始反射率的空间形态作为独立入口，识别细长径向、断续径向条带和扇形。无需稳定功率拟合、低相关系数或已验证的厂家速度扩展码。不得按站号、固定方位、时次或仰角写特例；不得从已删除的图件反推原始边界。

原始数据保持不变；仅 X 候选配置显式启用。形态可疑不等于射频来源已确认，也不能证明所有径向天气都不存在。独立天气保护和上下文冲突、动作预算继续执行，不取得可信融合/QPE/预报资格。

## 检测与证据

- 在实际原生方位与距离上运行，不重采样到固定射线数量。以多个物理距离窗口，追踪冻结的两侧方位边界。原始可见回波下限固定5 dBZ；尺度2–20km（1–4个），强度级5–55dBZ（1–6个），长度/唯一性/数值边界同时写入公开 JSON schema。
- 同时记录角宽、跨度、有效径向支撑、重复窗口数、两侧实测对比及形态类别。连续、断续和宽扇形采用同一对象追踪。
- 跨北向连续；重复射线、角度/时间/仰角缺口和硬天气保护均为屏障。缺测连续长度在完整原生距离轴上统计，不能在窗口边界重置；边界容差由实际局部射线脚印决定。未知反射率不能当作零；明确无回波或有效低 SNR 可作为两侧实测支持。
- 对象需要足够径向跨度、支撑和重复窗口，中心与两侧边界均相对初始模板保持稳定。扇形还要求距离倍率，避免远处短段的固定公里宽雨带伪装成恒定角宽。
- 默认保护本地偏振天气代理；明确的 joint_review 候选配置可记录代理冲突并送待复核，但不得跳过独立硬天气保护。不把高 RHOHV 单独作为否决理由。天气团块、弯曲雨带、固定公里宽雨带、缺测肩部、受保护区域、跨缺口对象为必测反例。
- 两侧实测覆盖和对比均分别检查，不能由一侧补足另一侧缺测。紧邻射线缺测时，在最多3°内查找最近的实测外侧样本；遇到已测出的强回波停止，不能跳过强回波再找安静样本，不能跨角度/天气屏障。缺测本身不计入安静证据，对象记录实际参考角距。检测有硬上限：单层200万门、5000万工作计数、2万个追踪对象；参数不能提高这些上限。任何资源超限整模块弃权，无部分掩码泄漏；记录完整，不能截断后冒充完成。

## 处置

新增独立形态掩码、对象编号和原因位，经现有 X 唯一 finalizer 处置；不伪造 receiver/source_kind。audit 模式无动作；cr_only 与 quarantine 中，仅形态支持的门保留原始值和 NONMET_CANDIDATE / QC_ACTION=3 待复核标记，质控显示和 CR 排除，不升级为已确认污染。独立来源确认重叠处仍由原处置流程决定。既有全层动作预算不放宽；新增形态超预算不产生硬删除，全部保留为预算待复核候选，保留已合格的基线处置，不任意截取像元。

## 验证门

先运行通用正反例及 finalizer 集成回归，再绑定正常任务的源 SHA/原生坐标/图件 SHA，逐层回放六体扫 54 层。重点验证 ZF701 不同时次/仰角，以及 ZF702 08:08 第4/9层、08:26 的预算弃权。只读回放不等于正常发布；部署必须经正常任务生命周期生成新结果后看真实界面。

## 本次证据（2026-10-02，本地时间）

- 新模块 SHA256 `2b27c36ff7cccb127ccd6f9484491596ae313e352d36b5f7e94414a4b4aeb508`；只读复核脚本 `8be4c3ee3d12ec616bc70cce9a8f3b31b38021bbe2042ccdc1522cc97b0ad52b`。
- 105 的 `polar-morphology-v9` 六份正常旧任务、54层全返回 EVALUATED，无资源弃权、硬天气命中0；原始 DBZH/方位/距离/仰角/采集时间逐值与发布的正常产品核对，六个 JSON 与回执均做 SHA 核验。本地证据在 `.build/xqc-polar-morphology/v9/`，不进入 Git。
- ZF702 08:08 第4层原 ray364 剩余205个可见门中命中201；第9层原 ray208 剩余996门全部命中。此层其他射线也有命中，总新增候选分别800/1319；总数不能冒充这两条径向的命中数。
- 保留旧算法和预算规则。原 ZF702 08:26 第2/9层仍涉及旧预算待复核；新增形态把若干层带到预算边界，处置为 action3，不伪造已确认污染，也不撤销已有合格的硬处置。
- 弯曲雨带、固定公里宽雨带、团块、未知肩部、最近强 REF 与 quiet SNR 冲突、跨北向/不均匀角距、跨窗口缺测、资源与预算弃权均有回归；实际观察过相关失败后修复。49项形态/审计专项测试通过，最终整套 XQC-v2 191项通过（1项已有 NumPy 二进制兼容警告）。
- 架构与安全复核已修复具体问题。旧 config/pipeline 存在历史 lint 报错，不把新增文件 lint 与相关测试绿色冒充全量 CI 绿色。

此节记录只读回放证据，不代表新 Worker、正常任务图件、真实天气独立对照或界面已经验收。候选范围先为 ZF701/ZF702，算法不包含它们的站号或固定方位特例；扩站须追加同样的真实资料复核。


## 候选部署与正常发布（2026-10-02，UTC 17:49 后）

代码724500b已合并本地main并push。105四个ops multiband Worker切换到`rainpulse-cpu-worker:xqc-polar-morphology-724500b-compat-mb`，image ID `sha256:0e8a8f84e24ba2a0f0ce291ae5f6f9615adfa2c4d9a05e8224d5d07bf339b84c`。安装后合成扇形2613门运行检查通过，原始值不变、仅形态候选action3、不取得QPE资格。

镜像从原f6b6b39父镜像派生，只变config/core/pipeline/polar_morphology四个模块，791个既有Python文件逐文件SHA相同。当前main的父pipeline包含尚未部署的radome_quality依赖，因此实际pipeline由已提交724500b的pipeline增量应用到父镜像原pipeline生成；未同时部署该radome/相位扩展及decoder2.2.1。生成pipeline SHA `a5fceda6385b7036609a047135a7effd4e525d01cc11b792f981397c4764da6c`，父pipeline SHA `4b617bf66e6625f4c540658fb5946d50df57dbb4b7ab98291e27cc6f62b59124`，增量SHA `b5c5943008f8a346e1fc41ead9aa85734b508cbe0cfdee1c5fc2594d59353e3b`；不能宣称整个main后台已部署。

候选网络SHA `aa2356cc45846c510754ffffd7d70a836b5aeec1bdb1bbe72ba72a38e5870381`，只改变release_id以及ZF701/ZF702的morphology字段。r2生成器保持父配置显式参数；拒绝旧r1生成器隐式写入默认参数的首个草案。release fingerprint `b69c8995849aad480776cd337008f62e3a1c90185556ca21d669cb3f87fb9575`。S主Worker未变，decoder/controller未切换，小时跟进未恢复。

六体扫均按正常API规划/提交，源输入身份与旧计划一致；未改SQL状态，旧失败/旧产品保留。正常任务及attempt：

|体扫|新task|新attempt|最新结果|
|---|---|---|---|
|ZF701 08:03|04214061-40d9-4fe5-b87b-59aed7b69c2e|f836dd21-1550-483a-9c5b-d37dc7da2e15|SUCCEEDED，9层检查|
|ZF701 08:07|94d7d228-305e-4d52-a564-f3d5432f59fd|4aef42de-9372-4214-9ff7-f6bc23ca617f|SUCCEEDED，9层检查；第2层新增形态预算待复核|
|ZF701 08:49|3d2059ae-8812-42ad-89ca-b3114fb485ed|7f1f5164-bedd-4a92-8c28-bb74b17cede5|SUCCEEDED，9层检查|
|ZF702 08:08|3c5afa78-076a-48ed-a9a1-69f9d810bba1|65eab672-4040-4dbd-bc58-0ee87890cb66|SUCCEEDED，9层检查|
|ZF702 08:33|d4d590bc-a610-470e-82b8-52c919f1222d|0b7decee-419f-4681-865c-88b76be0a9af|SUCCEEDED，9层检查|
|ZF702 08:26原问题|198c0180-ac2c-4edd-a377-8e517e8a8f13|4cb3b6a6-9bfb-4d66-9067-410affdf516d|RUNNING/COMPUTE，必须继续观察，不作为完成|

新正常图件45层校验原始反射率/方位/距离/仰角/采集时间相等，图件与原生数组SHA通过；形态候选质控图/CR排除、硬天气重叠0。44层机械门通过，ZF70108:07第2层标记`DEGRADED_MORPHOLOGY_ACTION_BUDGET`，不能忽略警示或改判通过。

新旧ZF70208:08正常产品对比SHA `3b6e72167bbab5ff6722a90e9e9bbf8be8ccb0ab4be64359425dbf8efca07836`：第4层ray364剩余205门降到4离散门，新增形态命中201；第9层ray208剩余996门降到0。原始身份逐值相等，9层均无新增可见门。action3候选不能称为已确认污染。

实际浏览器观察并保存五张完整页面截图：ZF70108:03第0/3层、08:07第0层，ZF70208:08第4/9层；明显长径向和南侧扇形已消除。文件在本地`.build/xqc-polar-morphology/normal/*-ui.png`。第1层截图工具超时未计入观察，不能把未观察层算进UI验收。尚未进行独立真实天气负样本验收；只读54层、新正常45层和实际UI5层是三个不同计数。

后台收尾driver PID2022470，目录105`.build/xqc-acceptance-20261001/polar-morphology-normal-724500b`，状态/日志持久化。原问题任务新鲜heartbeat，计算Worker约100%CPU/1.34GiB，未重启。实际MinIO卷发布前可用567588171776字节/100256917 inode，保护线通过。任务运行不等于全部验收，目标仍active。

代码CI36900071920已结束failure：build通过；parameter hash旧断言、fusion对照、lint与通用test仍失败，日志在本地build。相关191项XQC-v2通过不代表全CI绿色。

复验：在仓库根执行`PYTHONPATH=algorithms algorithms/.venv/bin/python -m pytest algorithms/tests/xqc_v2_20260928 -q`。下一步完成原问题体扫正常发布和预算层真实地图复核，然后补独立天气反例及扩站复核；当前不得开启可信S+X/QPE/预报。


## 正常六体扫完成与扩站审计修复（UTC 2026-10-01 18:14–18:28）

本节终态优先于上节五完成/一运行的快照。六正常任务全部SUCCEEDED，后台driver2022470正常终止，状态NORMAL_PUBLICATION_AUDITED。54层均完成原始/坐标/时间/图件校验；48层EVALUATED，6层预算待复核：ZF70108:07 cut2，ZF70208:26 cut0/2/4/7/9。其中cut2/9保留旧ACTION_BUDGET_ABSTAINED，其他为新增形态DEGRADED_MORPHOLOGY_ACTION_BUDGET；不改判通过。

实际安装模块2b27c36f…对六份新正常产品重新回放54层，全EVALUATED、被形态选中但仍显示的门0、硬天气命中0，六份JSON/receipt均SHA核对。原问题体扫回放SHA `dd5a1f373e4b504ac8584499187ba5453bf28db07ee27bac8c296b70ebf50aa8`。剩余4离散门核对为64.3125/69.7875/173.6625/208.9125km、6/8/17.5/20.5dBZ，不冒充独立降水真值，也不扩展已冻结对象去补删；回执SHA `b9b4ef606eecf77b232594de266ca7ea3cd10a97b8a1c7ebe704ddd1f0b9a74a`。

真实浏览器后续连接超时，未新增界面观察计数，仍为此前5张实际截图。正常任务原生图件检查不代替全层界面和真实天气误删验收。原问题任务runtime1455779ms，性能日志显示主要耗时聚合在X v2 evidence链，不能据此归因具体子模块；只读纯形态回放约数秒级。未调低旧来源预算以冒充快速通过。

为验证站间通用性，在看雷达值前冻结九个非试点站首个UTC00:00–01:00已发布native-v2样本：ZF101/102/103/104/105/401/402/505/605，共268层。算法/参数冻结不变，只有只读回放，不改这些站线上网络。另11站选定旧结果无native-v2数组，不混入这次计数；ZF703/ZF801继续按缺乏格式证据排除。目录105polar-morphology-expanded-frozen-v1/protocol.json保留选样及源身份。

首轮expanded-replay-v1仅ZF505成功，其余旧产品缺XQC_CONTEXT_WEATHER_MASK或XQC_RADIAL_SOURCE_MASK导致审计脚本KeyError；失败回执保留，不能当形态检测失败或成功。新增两个实际RED/GREEN测试：缺历史可选诊断输出null，显式0仍为0；不完整旧来源掩码输出缺项清单，基线/预期预算比例null，不拼部分掩码或伪造无上下文冲突。必需原始/几何/时间/可用性身份及形态规则不变。下一步用同样冻结九样本重跑v2；独立真实天气负例仍未完成，目标active。


### 扩站v2完成（UTC 2026-10-01 18:34）

旧审计缺字段修复a9d2e85已push后同步，脚本SHA `99e7fcbe9272a76d903534b96ff55504ad31351e718c951f0cb4587b63411dfa`，算法仍2b27c36f…，网络/镜像未再变。九份冻结同源样本全部exit0、268层EVALUATED，资源弃权0、硬天气命中0；各JSON及receipt下载本地expanded-v2后SHA核对。ZF505命中738原始门，其中40个为旧QC可见残留，本地天气代理重叠0；其余八站未提名对象。此结果证明可在其他原生几何/40层扫描上运行，不代表八站都是已标注天气负样本，也不代表ZF50540门已获污染真值或新正常发布。

累计11站15体扫322层纯形态回放（两试点6体扫54层+九非试点268层）。新正常产品仍只为两试点6体扫54层；独立天气负样本、扩站新正常图件、全部层实际UI均未验收。审计兼容新增2项实际RED/GREEN，整套XQC-v2现193项通过，ruff相关脚本/测试通过；不冒充全量CI绿色。v1失败回执保留。下一步先审查ZF50540门的完整对象与原始/质控图件，再走同样冻结配置和正常发布；11个无native-v2旧产品须建立明确的源级只读路径，不能伪造历史数组或混算322层。目标仍active。


## ZF505 同参数扩站冻结（2026-10-02）

第三站正常发布前冻结：同一模块2b27c36f…、同一镜像0e8a8f84…、完全相同的joint_review形态参数；不添加站号、方位或仰角算法特例。父网络aa2356cc…，子网络SHA `288690f1589161eb4ddf3eff279b2708df9cb3c9f009348f38ef93983c047845`，r2生成器只改变release_id及`/stations/zf505/x_qc/enhancement/morphology`，其他所有显式参数不变。

冻源scan0b350987-720c-5b4b-a927-cb47d3b99d63，输入SHA119e2f02…，旧正常task02e19228-ecf6-47e9-b5d4-49d6b8740cf9。原生sweep_number=8，实际9.8900003°、界面sequence=7；不能用列表索引8（另一个19.45°层）替代原生编号。完整RAW对象51.3625–104.6875km，角宽约1.99°，六个10km窗口支撑45.75km，两侧已知/安静最低比例0.8803，提名738门，其中40门是旧QC可见残留，全在ray163附近132.28°。没有独立污染真值，形态只能生成候选action3。

原生编号匹配的PNG已下载并观察，原始SHA `74a089cd97ce71b9702b42015071bcc4164a9e60aa85850a6693b4683c264789`，旧QC SHA `bbc3c320d00119082191d59a69e747f74ff1357d52c7ff3ac3dfde74c8cec1ac`；当前观察为原生PNG，不冒充真实地图UI复核。误取列表第九项的早期PNG留存，不作为本层证据。

105新鲜四个X Worker均ready且idle、当前channel revision19/fingerprintb69c8995…；实际MinIO卷560768090112字节/100115713 inode可用，满足120GiB/100k保护线。先推此冻结记录，再原子切换配置、仅重建四个X候选Worker，走正常API规划/提交。新任务发布、40门残留变化、RAW/坐标/时间、图件以及天气保护尚待复验；不能把冻结配置视为完成。S并发工作与小时跟进保持其现有状态。


### ZF505 正常发布终态与源级审计契约

冻结记录6c0f877先push后部署。105四个X候选Worker在空闲门通过后仅重建自身，镜像0e8a8f84…未改变；子网络288690f1…，新fingerprint `59b7e186677d495f14bb4ea10444ecf348480896cc0d6ad10a4688f086b6d0ed`，没有取得业务融合资格。新正常task `2d7b0ad4-910a-4b5c-b476-bcd4449ceebb` 已SUCCEEDED，driver2453270正常退出/NORMAL_PUBLICATION_AUDITED，9/9层原始/坐标/采集时间/PNG机械门通过，验证回执SHA `a009c79220ff35978a17800824f30e67492d89bc88ba16671737e6e71f622fa3`。

新旧同源正常对比SHA `44d3f8407066530a85ba97acb33a3603005cfbb506f82f7c898a35b27b8d38d1`：原生8层ray163的40可见门全部退出QC显示与CR候选；该层可见153→113，其他层无新增可见门，整体newly_visible=0。40门中29为候选action3，11与source_kind4重叠而走硬处置；须继续核对其旧来源诊断，不把11门称为纯形态硬删除。独立天气真值验收尚未完成。现累计新正常3站7体扫63层，但预算待复核六层保留；浏览器连接超时，没有增加此前五张真实UI观察。

为11个缺历史native-v2数组的站新增明确的`--source-only`审计路径，规则：只接受成功X任务的单一冻结source/input，URI、站号、scan及SHA一致；源对象存储SHA验证、512MiB整体/64MiB单对象/256MiB单层及pack上限继续执行。根部原生sweep_number索引经既有有界解析器验证，完整组清单必须一致且≤64，按原生编号选REF层，不把序号当编号；单层仅加载实际需要字段，不带其他层。原始字段与几何/时间分别保留内容摘要；源级审计无动作，不加载历史产品，历史可见残留、预算和上下文冲突一律null。缺独立天气掩码时known-hard-weather命中也为null，不能假报0。资源超限是弃权，不截断后报成功。

新增四个边界/执行测试；前三项实际RED缺函数后GREEN，完整源级运行测试另发现把根sweep_number数组当分组编号的问题，修正后GREEN。测试明确原生8/10混合非REF编号、错冻源URI、多源、64层上限、外层/未使用字段排除、没有历史结果时不读取产品且不伪造比较计数。生产形态模块/阈值未修改。复验仍为完整XQC-v2套件，另`python scripts/audit_x_polar_morphology.py --source-only < frozen-task.json`须在安装算法且绑定只读源对象访问的环境执行。下一步用此前冻结首体扫策略回放11站，源级与正常原生对比两种证明范围分别计数；目标未完成。

首轮11站源级回放v1全部在分组解析处exit1并正常终止，失败回执保留。实际有界对象索引显示根数组`sweep_start_ray_index/sweep_end_ray_index`；它们的名字以sweep_开头但不是原生分组。追加实际RED/GREEN反例，只排除根坐标数组，仍拒绝非法分组/编号、完整根索引漂移、超64层，不调算法阈值。完整XQC-v2现197测试通过（既有NumPy二进制兼容警告1）；相关ruff及diff-check通过。修复先push后同步，再按原冻结11样本重跑v2，v1失败不计检测完成。

ZF505旧来源对比发现这11个新硬处置门旧source_kind均为0（旧结果代码/网络身份不同）；不能仅凭新结果source_kind4就断言来源不变。追加独立反证：同一安装镜像、同一冻源、同一配置但morphology显式关闭，复算原生8层的来源检测，并与新正常全层source_kind逐值对照。该核验尚待运行；形态不能制造确认来源的要求保持不变。


### 本轮终态：第三站正常结果、11站源级回放均完成

ZF505独立来源反证v2成功，回执SHA `7836b1616aa7bc8fb4681ab320e49b66672fdd2322b76511eab5eeda449bf213`：同一安装代码/源/配置、morphology关闭，原生8层全层XQC_SOURCE_KIND与新正常结果逐值相同，目标11个新硬处置门仍为来源检测kind4。原始值不变；形态没有制造确认来源。首版复核辅助脚本错误地把整层所有重叠硬处置当成旧可见11门，断言失败留存；v2显式加载旧正常原生数组，以旧可见子集定位11门，不改算法或任务。

新正常ZF505 result `2d7b0ad4-910a-4b5c-b476-bcd4449ceebb.045dbec6-1104-4f69-aaa9-8067b653cca6`，原生8层RAW PNG SHA仍74a089cd…，新QC PNG SHA `00eb9455a8bae19afbae7bafa1959736c9a46f188d9e2bd558520b2df1ff4154`。新QC原生PNG实际观察、目标条带消失；剩余其他回波保留，不等于它们已获天气真值。通过实际4173入口核对站点28条、ZF50566体扫及新结果目录，HTTP200/约0.12秒与0.01秒；实际浏览器一度恢复并导航，但随后截图/状态连接再次超时，首屏仍是未加载状态，不能增加真实地图UI观察计数。此前五张截图仍为已验收观察范围。

源级审计脚本SHA `c95bbc462a246ed305ae15adecc3014752e9fd278bb9ab1c949b54b3ae7ed0f9`，代码57cda3f先push后同步。legacy-source-replay-v2后台PID2588230终态SOURCE_REPLAY_COMPLETED且进程消失；11/11 exit0，原生REF层405（ZF201/202/203/602/603/604各40；ZF501/502/503/504各39；ZF900九层），全部EVALUATED，资源弃权0，提名0。覆盖360射线/1998门、272–273射线/1472门、826射线/1020门等实际几何；存在未合格review对象，不把未提名当污染/天气真值。11份JSON与receipt下载本地并SHA核对，证明范围verified_source_only，旧QC可见/独立天气缺项/预算仍null。v1失败证据保留。

累计冻结真实源范围22站26体扫727层，分为322层正常原生对比回放及405层源级回放，不能混称727层正常发布。正常新产品3站7体扫63层；六个预算层仍待复核，独立真实降水负样本与更多跨时次/界面验收尚未完成。197项相关测试及ruff绿色，未声称全量CI绿色。候选业务融合/QPE/预报未启用，小时自动跟进未恢复。

下一步保持形态主线：冻结不同时间段/扫描家族的天气与污染对照，复核预算层和不合格完整对象的具体边界；不得按固定站号/方位补删，不以源级0提名冒充真实天气误删验收。浏览器恢复后再补新正常地图及预算警示观察，目标仍active。


## 跨时次冻结验收（2026-10-02）

继续采用同一安装镜像0e8a8f84、模块2b27c36f及原joint_review参数，没有站号/仰角/方位特例或阈值调整。先冻结所有22个支持站的UTC02–03和05–06目录，再读取值：实际可获得10站20份已发布体扫，12站在两个窗口都无已发布资料，缺样24项保留。API以volume_end半开区间筛选，再按volume_start/scan_id选第一份；三个体扫起始早于窗口属于跨界体扫，不能误写成所有volume_start均在窗口。原protocol SHA e4d476a9134544891a2daa79c6e028ebd92b3caa24776c2ed2add9696ce2870b未修改，selection-clarification.json追加说明和控制平面代码位置。

源级只读20/20成功、492层全部EVALUATED、资源弃权0；各输出和冻源task SHA本地逐项核对。目录105.build/xqc-acceptance-20261001/polar-morphology-cross-time-replay-v1，driver2749069终态SOURCE_REPLAY_COMPLETED；本地.build/xqc-polar-morphology同名目录及verified-summary.json。此批纯源级并无正常发布/天气真值，不能把提名368571门称为清除了同样数量的污染。累计唯一冻结源范围22站46体扫1219层，其中新增492层与后续72层配对回放同源，不重复累计。

对8份同样冻源补做已发布原生结果配对审计（ZF402、ZF505、ZF701、ZF702各两时次），72/72层成功，两个后台driver2764202/2778621均终止；目录cross-time-paired-v1与cross-time-paired-other-v1。18层ZF402与54层其他三站所有JSON/receipt/task身份下载核验。形态未上线ZF402，本轮不会把只读提名冒充新图件发布。

- ZF402 UTC02 cut4：完整断续径向约81.7–109.8km/0.98°，两侧安静最低0.984；提名197门，其中旧QC可见191门。UTC05 cut4/5旧QC可见提名1793/514门；cut4与局部天气代理重叠14，必须复核，不能假定污染真值。
- ZF505 UTC02 cut7旧可见提名298门。UTC05全层提名266870门，主要是5–276km长径向、4–11.5°宽扇形；不同层边界安静最低约0.89–0.999。大量命中需完整图件和独立天气复核，不因为形态看起来整齐就发布硬删除。
- ZF701 UTC05 cut0/1/2/3旧可见提名61/82/1/48门；UTC02没有合格提名。
- ZF702 UTC05 cut7约20.8°宽、30–150km扇形，提名67229、旧可见60509，预算弃权true；cut5局部代理重叠1439，cut7重叠29。不存在“所有更晚时间都已验收通过”的结论。

此前六个新正常预算层专门复核：ZF70108:07 cut2基线/合并候选比例0.6443/0.6703；ZF70208:26 cut0为0.6047/0.7189，cut2为0.7349/0.7768，cut4为0.6633/0.7205，cut7为0.6250/0.7154，cut9为0.7372/0.7489。六层形态选中但仍QC显示或CR准入均0；前四类保留已合格基线并把新增形态整批置review，旧cut2/9保留基线全增强预算弃权。告警是诚实处置状态，不能提高预算或删除告警冒充全通过。

四个线上X候选Worker仍健康，同镜像/网络；实际MinIO可用557842051072字节/100052722 inode，保护线通过。本轮浏览器getTab再次连接超时，未新增真实地图UI观察；既有5张实际UI截图仍是证明范围。新正常仍3站7体扫63层，独立真实天气验收仍未完成。生产算法、配置、S流程、小时自动任务均未改变。

下一步按形态主线集中复核ZF402新长径向、ZF505更晚时次大范围提名、ZF702更晚宽扇形及局部天气冲突：完整RAW对象与动作图并排检查，绑定独立天气/邻站观测身份；合格样本再走冻源正常发布，不补固定方位，不把missing邻侧当安静，不将候选升为确认污染。目标仍active。


## 完整对象图复核与扩宽扇形分支（2026-10-02，尚未生产启用）

同源完整图8层渲染并实际观察4层：ZF505 UTC05 cut4/5、ZF702 UTC05 cut7、ZF402 UTC05 cut4。四栏分别RAW、旧正常QC、形态提名、从旧QC扣除提名的研究预览；最后一栏不是正常QC发布，不能算界面验收。图显示ZF505仍有明显残留长条带，旧固定边界判据无法覆盖所有扇形。只读回放记录cut4一个完整169.6km对象双侧安静最低0.919但边界漂移2.96个原生足迹；cut5若干166.6km长对象双侧最低0.9995但边界漂移3.84–4.87足迹。不是再调源功率阈值能解决。

新增显式、默认关闭的`expanding_fans_enabled`，必须绑定`x-polar-morphology-20261002-v2`，JSON schema同步约束。保持v1固定边界判据不变：新分支使用完整RAW距离历史，中心方向漂移仍受原1.05足迹约束，角宽增长必须超过两侧量化容差，历史回缩不得超过双侧量化容差，全部宽度有原上限；已知/安静双侧、原跨度/支持度、歧义/几何/天气/未知间隙及资源门仍共用。固定公里宽雨带随距离收窄、弯曲回波和团块反例不得靠远端重启通过。新分类为expanding_fan，仍shape-only候选，不能制造RF来源或业务资格。

9项新增测试先实际RED（v2契约/开关缺失），实现后54项形态相关测试通过。首版连续合成边缘包括不足500m支持的单门尖端，完整性断言失败；测试用足够原生支持的阶梯扩宽对象核验完整提名，不放宽既有500m角向入选物理条件。源码SHA `c2173ca56243617cfbc08bd2e0e40204705f409a612bdcee5c2644c3fe1c4eb2`。下一步先用同一冻结20体扫和8份正常原生结果只读回放，比较新增提名、天气代理冲突和旧v1资格保留；没有正常候选镜像/网络/图件切换。

首次渲染v1四份因容器输出目录UID权限失败，失败记录保留；v2显式以目录所属yons UID运行，四份成功/8张PNG及task/audit/renderer/PNG SHA绑定。105polar-morphology-cross-objects-render-v2和本地同名目录；renderer SHA a0b758f965b2aaf6fd45f8f5a8b32b2fbd2c24d3a15f636540881a4e22f26452。查询UTC05:00–05:12四个S站目录均无已发布体扫，不能假称已使用同期S真值；此缺口不阻止原始对象排查。


### v2冻结真实回放终态与可复验入口

9a90a78先本地main提交push后同步源文件，安装准备源码c2173ca56243617cfbc08bd2e0e40204705f409a612bdcee5c2644c3fe1c4eb2。首次同步发现105活动algorithms路径缺multiband子目录且父目录root所有，普通scp/mkdir失败、未启动任何driver；随后通过已有Docker权限仅写活动源路径该文件，SHA一致，不创建替代checkout、不改变运行中镜像。正式source driver2918816/paired driver2918817均已终止SOURCE_REPLAY_COMPLETED，各20/8份exit0；使用原协议e4d476a9冻源，两个只读容器各自绑定v2模块，生产四X候选Worker仍v1模块与网络。

源级492层/配对72层全部EVALUATED，资源弃权0；全部v1合格mask是v2子集，492层v1计数也与前次实际安装v1回放逐层一致。新增v2-only合计10240门：ZF50505为8452、ZF70105为857、ZF70205为931，其他冻源0。配对与源级是同一批对象，不重复累加。28份JSON/receipt/task SHA本地核验、summary留存，目录polar-morphology-expanding-source-v1和expanding-paired-v1。比较程序每层分别执行v1/v2两次有界detect，comparison_work明确记录总工作，不能把两次总成本说成一次50M预算。

完整图显示残留尚未解决：ZF505 cut4一完整169.6km对象宽增长2.96足迹、回缩0，但中心漂移1.48，故v2仍不合格；另166.6km对象宽增长约8足迹、中心漂移2.00，同样未通过。cut5一个170km对象中心0.49但中途回缩3.07足迹、总增长0，不能直接按扩宽分支通过。下一步须用完整边界轨迹/持续核心解释不对称扩宽及回缩，不能单纯调高中心/边界容差、切掉失败前缀重启尾端或丢弃天气反例。

可复验只读入口新增`python scripts/audit_x_polar_morphology.py --expanding-fans < frozen-task.json`；没有历史native产品时加`--source-only`。必须在安装对应v2模块且绑定只读冻源对象访问的环境执行。CLI显式选择v2，默认v1；两类审计的原有SHA/资源/身份/缺项语义保留，无发布动作。3项CLI实际RED（原选项/路由不存在）后GREEN；相关XQC-v2整套209测试通过，既有NumPy二进制兼容警告1，ruff/diff-check通过，不声称全CI绿色。目标active，生产尚未启用v2/新正常图件，不以研究预览代替地图验收。


CLI安装烟测已实际完成：4a8a9f2先push后同步活动scripts/audit_x_polar_morphology.py，SHA01f397ffbf3c790c27f958f1400852c3ab7c10e238b16c1785068bead879a8d0；独立只读容器绑定活动v2模块，对同一ZF50505冻源分别跑source-only与paired两种--expanding-fans入口，均exit0/9层，逐层提名计数与之前冻结wrapper回放一致。source输出SHA946a4d8c744fa12e0394122a4ae683cc59bd6b17eb2793a275899548a8d55c8e；paired输出SHA43ce63544e3e890b9b2cea24c6a6962b7a015342a46519018696f7a90bdf9273。JSON/receipt已下载本地并验证。未新增正常产品/唯一源样本计数。

最终现场核对四X Worker均healthy，容器内真实形态SHA仍2b27c36f（v1），网络仍288690f1；新的活动源文件不会自动替换运行镜像。没有悄悄启用v2或把研究预览当网页更新。下轮优先验证完整稳定边界锚定/持续核心假说，再决定正常候选子镜像/配置及重算范围。


## 原始边界锚定的扩宽形态分支（2026-10-02，待只读实样复核）

新增显式、默认关闭v3 `anchored_fans_enabled`，必须同时启用expanding_fans_enabled并绑定x-polar-morphology-20261002-v3；契约先于实现。完整原始track分别记录左、右边界足迹漂移，允许一侧锚定在原1.05量化足迹内、另一侧扩宽；仍要求全部距离历史无超量回缩、有净角宽增长、两侧实测/安静及原距离/支持/歧义/天气/未知区/资源门。没有增大全局中心/边界容差、没有固定站号/角度或切掉近端失败历史。新分类anchored_expanding_fan仍只生成候选。

合成不对称扩宽/北缝样本原v2不能通过，v3完整原始成员通过；固定公里宽雨带、弯曲/团块、两侧均移动且宽度增长、缺测邻侧及天气保护反例拒绝。v3显式身份/JSON契约和CLI --anchored-fans支持源级与正常原生只读审计。6形态/2新增CLI案例实际RED后GREEN，另契约和两侧漂移反例通过；此前218整套通过，追加漂移反例单测通过。模块SHA `d55e966600868a536d3b9ad664a818fccd22e58393191e7defc82c4ef0d1c07c`。

下一步同协议e4d476a9和相同20冻源/8配对只读回放；先统计真正新增、v2资格保留、完整对象锚边漂移和天气代理冲突，再做图件和正常发布。生产仍原v1，不因本地测试通过自动改候选Worker。


### v3真实回放终态（2026-10-02）

同一冻结协议e4d476a9，source driver3059711与paired driver3059712均SOURCE_REPLAY_COMPLETED且退出；20份492层和同源8份72层全部EVALUATED，全部原v2合格mask保留，逐层v2计数与前次冻结回放一致。28份输出SHA与receipt核验。v3-only新增1452门，仅ZF505 UTC05 cut4；不能重复累加源级与配对，也不能称它们均为真污染。正常发布仍为原v1的3站7体扫63层。

只读v3完整图四层成功，实际观察ZF505 cut4：一侧锚定的约170km对象新增提名，但其他中途回缩/两侧漂移的长条带仍未解决。四栏研究预览不是地图验收、正常发布或天气真值。目录polar-morphology-anchor-source-v1、anchor-paired-v1、anchor-render-v1。下一步必须针对完整原始距离历史的持续核心检验，而非加大全局边界容差或按方位补删。

219项XQC-v2整套通过，另新增正常x_qc链路合成集成测试通过：非对称扇形RAW逐值不变，所有提名门为action3、QC反射率不显示、CR准入0，source_kind0。该测试证明候选动作语义，不能替代真实天气误删验收。生产四X Worker尚未启用v3。


## 完整原始稳定边界的回缩扇形分支（v4，默认关闭）

真实v3图件仍有原始单侧稳定、另一侧先张开后回缩的长条带，单调扩宽分支会漏掉。v4增加显式pulsing_fans_enabled，须同时开启v3锚边/v2扩宽父分支并绑定v4身份；JSON契约同步。完整原始距离历史仍保留：一侧相对最初边界始终在原1.05足迹内；相对初始宽度不能出现超过原两边量化容差的收窄；必须有超量化容差的真正张开和随后回缩；原总宽度/双侧测量与对比/跨度与支持度/距离比/缺测/歧义/天气/资源门均保留。没有切掉近端失败历史重新启用远端尾巴，只有原始已观测门参与候选。

这识别原始稳定的径向边界；不宣称任意有扇形外观的天气都可删除，不根据站名/固定方位/厂家波形扩展码决定。shape-only候选仍原action3，不能提升为确认RF来源或业务融合。测试含北缝、非单调宽度、单边固定公里宽雨带、持续偏转的边界、曲线/团块、未知肩部、全保护和资源上限；9项新形态测试实际RED后GREEN（首版测试阶跃平移造成独立长对象，改用连续漂移反例，未放宽算法）。CLI --pulsing-fans显式源级/配对只读v4入口，2项CLI实际未识别选项RED后实现。231项XQC-v2整套通过；另v3/v4正常QC动作集成测试随后复核。下一步同一冻结20源/8配对实样回放，验证v3资格保留和真实残留改善；未启用生产v4。

v3兼容子镜像ece577d已在105构建并安装烟测通过：只改变一个形态模块，其他794个Python文件含生产decoder/pipeline逐SHA不变，正常候选1400门RAW不变/action3/source_kind0/QC不显示/CR0。镜像9d8c8e5f，只构建未切换。首次Dockerfile直接FROM配置sha被BuildKit当远端tag而超时，失败保留；改为已有parent标签且前后核验固定imageID，成功。不能把未运行的子镜像称为线上更新。


### v4冻结真实回放与剩余形态（2026-10-02）

main e2995f2先push再同步活动源：模块SHA3ca9d0df80037277c86e19f3d6b1cc92e95494820af92420c9638aec4e521729，审计脚本83380395。source3273741/paired3273742均终止SOURCE_REPLAY_COMPLETED，20源492层和同源8配对72层全部EVALUATED，所有v3合格mask保留，逐层v3计数与实际前次回放一致；冻源task和28输出SHA本地核验。新增v4-only23222门：ZF505 UTC05 cut5为18960、ZF402 UTC05 cut4为4262，其他0。source/paired不能重复累加。ZF402新旧提名局部天气代理重叠从14升至55，这是复核冲突，不是独立天气真值，也不能据hard_weather0说不会误删。

完整预览四层生成成功且task/audit/renderer/PNG SHA核验，实际观察ZF505 cut4/5：cut5东南长条带得到处理，但西南短扇形与两条细长径向、cut4一些原始条带仍残留。目录polar-morphology-pulsing-source-v2、pulsing-paired-v2、pulsing-render-v1；预览不能冒充正常新产品或网页验收。源级总提名403485不是污染清除真值。下一步复核剩余完整对象的原始边界和跨层/时间支持，以及ZF402局部天气冲突，再决定正常候选网络和重算范围；不切掉失败历史、不按固定方位补删。

兼容v4子镜像18c5ccff已构建，只换一个形态模块，其余794个Python文件SHA不变。实际安装烟测1080门通过RAW不变、action3、sourcekind0、QC不显示、CR0；v3/v4正常x_qc两项动作集成测试通过。前序完整231测试通过，不声称全CI绿色。发布图件仍原v1、3站7体扫63层、真实UI观察5张，唯一冻源1219层不增加；v4尚未切换生产，独立天气验收未完成。

活动源码同步最初因容器默认非root/远端scripts文件权限失败，均未触发正常任务；改为Docker --user0只写两个活动源文件，逐SHA验证。首启动辅助命令因嵌套引号失效/缺driver在前置门退出，未创建driver输出目录；正式先生成上传全部helper，再冻结v2目录和启动，只存在3273741/3273742两条已完成只读批次。原v1命名未实际启动。MinIO可用557842075648字节/100052728 inode，保护线通过。保留失败诊断，不修改任务数据库或既有正常失败状态。


## 原始带窄缝的完整扇形 v5（默认关闭，待实样回放）

剩余ZF505 UTC05 cut5原生336/340/353号射线对应196.905°/200.920°/213.875°，不是排序后336号。初版row-profile探针误按排序号取样，v2明确通过original_indices还原；旧探针证据保留。finish/exterior测量独立回放逐entry断言与原known_sides/clear_sides完全一致，完整140.7/153.075/165.225km对象被相邻条带挡住：336右侧不安静385门中可见362门，首测非安静主要339/340；340左右主要337/342；353左侧主要350。主要问题是相邻径向被窄缺测缝拆开后互为非安静外侧。低于5dBZ背景不足6dB对比仅少量，不是主因，试写的背景判据测试已丢弃，未添加该算法/配置。探针boundary-decisions-v1因numpy.bool序列化失败留存，v2修复，v3实际复演first-measure语义通过，不改变原生数据/正常产品。

新增显式grouped_envelopes_enabled，须v5身份，默认关闭。完整原提取不变，随后有界辅助提取仅把两个实际入选射线之间的单个未入选原生射线作为内部窄缝连接（角宽不超过现有3°查肩范围），不得跨几何gap/坏射线或天气保护。内部缺测不充当安静外侧、不插值、不变零、不写入候选；最终仍完整RAW历史/稳定形态/跨度支持/宽度/双侧实测/天气门合格，只有原始实际观测且>=5dBZ门返回。未知量记internal_unknown_gates，grouped_envelope记来源；候选action3、sourcekind0、无可信融合资格。两次提取共享一次50M总工作和原20k/2M限额，不能声称每次独立增加预算。

合成三个径向在两条缺测射线处分裂，原判据全部漏检，v5完整提名实际三条射线且缺测仍未提名。北缝/0.5°与1°几何、几何和天气屏障、固定公里宽雨带/曲线/团块、缺测外肩、资源限额与v5契约反例通过。7项原始新测试与2项CLI实际RED后GREEN，扩展几何后243项XQC-v2整套通过；正常QC新集成单测随后通过（实际观测候选3，内部missing仍不可用且QC非finite，RAW不变、sourcekind0、CR0）。CLI --grouped-envelopes显式选择v5并保留默认v1；两类audit实际转发待实样验证，不能把路由mock当数据入口验收。

下一步同协议e4d476a9的20源/8配对冻结回放：确认v4资格保留、资源、天气代理与完整图件，合格后准备唯一兼容候选镜像/网络与正常重算。不先按固定方位清理，也不将新内部未知量视为无风险。生产仍v1；v4兼容镜像未激活。


### v5原始冻源回放终态与历史预算口径修复

main e9ada37先push再同步，模块12fc0cb51ec0913a5136e9855bf50a71baae1ff96b446ac2c3183902b3772601、审计d43d9199。source3537411/paired3537412均SOURCE_REPLAY_COMPLETED；492源层与同源72配对层全部EVALUATED/资源弃权0，v4合格mask保留且逐层v4计数等于前次实际回放。28输出/冻源task SHA核验。v5-only新增74350门=ZF50505 69983+ZF70105 2193+ZF70205 2174，其余0；不能重复累加源级和配对。目录polar-morphology-grouped-source-v1/paired-v1，正常发布/天气验收仍未完成。

复查旧ZF50505 cut0/2/4/5均RESOURCE_OR_GEOMETRY_ABSTAINED，但历史XQC_AVAILABLE_MASK全零，旧审计却据此输出候选比例0/预算未超限。这不是正常新任务的预算通过证据。新candidate_budget核对冻结RAW当前原生可用mask与旧可用mask；有漂移或缺旧分支mask则历史/未来比例与预算结论全部null，记录两类可用数量和漂移门数。相同可用集也只称HISTORICAL_MASK_ESTIMATE，不替代正常任务。2回归测试实际RED后GREEN，21审计边界测试通过。新paired输出同时提供正常历史detail，追查真实资源弃权原因。旧冻结输出不覆盖，接下来新目录复核这项口径及真实正常状态。


### v5配对预算复核、完整图与兼容安装（2026-10-02）

6380c9f先push后同步审计a1bc1909759f1f54ec312efbb4fa3b0e8f347105d4b2f2153194c6a8ece3d933；新paired-v2同8冻源72层全部完成，JSON/receipt SHA本地核验。ZF50505 cut0/2/4/5历史detail均为“X evidence record budget exceeded”，原生可用252147/233726/224579/187220门但旧可用全零，新未来预算均null。ZF70205 cut7是ACTION_BUDGET_ABSTAINED且原生/旧可用集相同，仍只给历史估计，不和资源弃权混淆。初次验证脚本误把所有非EVALUATED（含动作预算）要求null而失败，修正为核对资源弃权，未改算法或数据。

grouped-render-v1四张完整PNG/冻源task/audit/renderer SHA已核验且实际观察。ZF50505 cut5西南相邻条带明显改善，但两条细径向、cut4西侧与东南条带和天气团块内径向仍残留；不能称彻底解决。ZF70205 cut5剩余长细径向被候选覆盖，cut4没有新增候选。均是研究预览，无正常发布/UI/独立天气真值声明。

兼容镜像rainpulse-cpu-worker:xqc-polar-morphology-e9ada37-v5-compat-mb，imageID sha256:ec887aacc8f373afb4dc54a31e5141694938972370cc6bc7e565abb7eb79ce72，只替换形态模块12fc0cb5，其余794文件（含生产core/decoder/pipeline）逐SHA不变。安装真实正常QC合成603观测门通过：内部缺测保持不可用/不被选中，RAW不变、action3/sourcekind0/QC不显示/CR0/QPE0。四生产X Worker fresh healthy且仍v1模块2b27，网络288690不变。镜像只构建未启用。

生产core已含无损证据压缩，历史四层弃权来自旧任务代码；需使用当前正常路径重算核实。额外installed cut core probe针对50505 cut4/5运行，不提供邻层context且不是正常发布；其结果另存grouped-core-v1，不能据源级detect回放替代正常状态验收。下一步优先确认这两个实际core状态、正常冻源重算/完整地图，然后继续解释残留完整轨迹，保持动作预算和天气门。

候选配置已冻结于grouped-release/child-network.json，SHA2e54f00c0e7c238c66f12762801f0771b1b04e1b2a93ee335789ca9f87ddebd4，只改release_id和ZF505/701/702形态v5开关/身份；保留其他噪声/源证据/动作预算/天气政策，ZF402仍未启用。未替换现场网络或发布通道。当前installed真实core探针约747.5MiB/4GiB、100%单核运行，尚未完成，不以启动称通过。105run-grouped-core.py/本地exec66158续读，结果grouped-core-v1/zf505-05/core-receipt.json待生成。相关形态+审计测试本轮104项通过，既有NumPy警告1；正常大体扫需等实际结果。

续证：installed真实core探针已exit0，auditSHA c186dc3ec34635b8258f8e26e03cbd4c9fa19bb969bad1884bd7a6807cabd4c9，本地receipt/SHA核验。50505 cut4 DEGRADED_MORPHOLOGY_ACTION_BUDGET，提名156506/形态102741，证据1650329字节；cut5 ACTION_BUDGET_ABSTAINED，提名16434/形态102912，证据1448026字节；均低于原4194304字节cap，RAW不变。证明当前core确实越过旧证据容量弃权，但此时仅检查core，尚未核验最终显示动作，不能据预算状态判定清除失败；未提供邻层context/未正常发布。exec66158已完成，不能再称运行中。下一步追踪原基线/新增形态候选比例及预算降级语义，保留原告警/cap，随后正常冻源重算和UI核验。


## v5候选部署与正常跨时次重算（2026-10-02，复核中）

进一步追踪真实pipeline finalizer纠正前次预算推断：ACTION_BUDGET会限制确认拒绝，但原始proposed及budget-withheld仍转为候选action3，DBZH_QC/DISPLAY不显示且CR准入0；告警保留，不提高预算。installed完整x_qc探针对ZF50505 cut4/5实际验证形态选中门QC可见0、CR准入0、RAW不变，证据字节1650971/1448666低于4194304。pipeline-receipt/audit SHA ad10b10afc2b984e7662922e78190a94f39c68134a1dd3b97db12bd9094f53c6。core提名数量不能替代最终显示验收。

四X ops Worker已实际切换唯一兼容v5镜像ec887aacc8；其余794模块含decoder/pipeline保持原SHA，S Worker不涉及。活动网络SHA 2e54f00c0e7c238c66f12762801f0771b1b04e1b2a93ee335789ca9f87ddebd4，fingerprint c23048ccabec7ec20076b96afa38db406aebd1ad134ffb3f5e042afeb1b69830，仅ZF505/701/702显式启用v5，其他政策与动作预算不变。正常生命周期六冻源任务提交完成；promotion.json保留原镜像/配置及发布凭据。246项XQC-v2整套测试通过，已有NumPy ABI警告1，不宣称全CI通过。

截至UTC22:37Z，六正常跨时次任务已有5份SUCCEEDED且原生9层逐份图件/RAW/坐标/时间/准入校验完成，共45层，其中ZF70205 cut7保留CUT_ACTION_BUDGET_ABSTAINED复核告警；ZF50505任务ad439bbd-812b-4384-9620-46055b230135仍RUNNING。后台finisher PID3812107持续核验，不把启动称完成。目录polar-morphology-grouped-normal/state.json及各verified.jsonl可复验。实际IAB网页ZF70105新task7fa1aa0e、attempt ea21449b、scan e42d2745原生sweep0，RAW12:57:45和质控时间一致、底图/数字同心圆一致；实际截图看到长径向/南部扇形已隐藏。新增实际UI仅此1张，不代表所有层天气与界面验收完成。

完整轨迹探针发现ZF50505两条长外轮廓跨度130/160km、双肩清晰率约98%，仍被ambiguous标记拒绝。当前只认定关联分支为疑点；继续捕获实际分裂/合并节点与全部原始分支，再制定完整连通对象验证。不能直接取消歧义门、截断近端历史或按固定方位补删。独立天气真值尚不完整，shape-only仍候选action3，不启用可信融合/QPE/预报；小时跟进保持停止。


### 六正常任务终态及完整分支对象 v6（默认关闭）

UTC22:41:47Z finisher已NORMAL_PUBLICATION_AUDITED并结束；6/6任务SUCCEEDED、54原生层全部RAW/坐标/时间一致、候选可见0/CR0、每层4PNG校验、六receipt SHA本地核验。50层MECHANICAL_GATES_PASSED；另4层告警保留：ZF505 cut2 DEGRADED_SOURCE_RESOURCE_LIMIT/SOURCE_INCOMPLETE，cut4 DEGRADED_MORPHOLOGY_ACTION_BUDGET，cut5 ACTION_BUDGET_ABSTAINED，ZF702 cut7 ACTION_BUDGET_ABSTAINED。不是“54层全面天气验收通过”。正常新累计3站13唯一体扫117层；唯一源回放22站46体扫1219层不重复增加。701新网页真实截图已观察；702选择原生cut7后截图工具超时，不能增加UI计数。

junctions-v1实际原生探针确认：ZF50505 cut5 5km尺度父轨迹336–354持续至bucket35，bucket36分成336–337和340–353，两子节点都关联同父索引4；10km尺度同样bucket17→18分裂。旧关联逻辑因此把130050/160050m完整父历史标ambiguous拒绝。probe/audit SHA aeb3fc054c9dea18fd53b22bde23d50f2b6d94e1c45fd65c5cf30d9bc6a9a853，未改RAW/已发布结果。

v6新增branching_envelopes_enabled显式身份/JSON契约，默认关闭且要求grouped父分支。原始逐距离窗口节点按相邻窗口重叠建立连通关系；只对存在分裂/合并的完整组件重新测量每个窗口的全部外轮廓、原始body和独立外侧肩部，再用原跨度/角宽/初始边界/固定公里宽反例/双肩/资源政策验收。不继承旧父或碎片的合格权，不截断原始失败历史，不填缺测门，不跨hard-weather/显式角缺口。节点/关联/重复测量纳入既有20k对象/50M工作上限。旧线性与v5路径保留；shape-only仍候选action3。

两末端分裂正例（普通方位/北缝）先在已支持v6政策但未实现graph的旧逻辑实际RED（原RAW body未全选），实现后GREEN。另split→rejoin的两原生分辨率、原始固定公里宽/弯曲/团块天气反例、缺肩、hard-weather/几何隔断、默认关闭/版本契约、资源弃权及完整正常x_qc动作验证通过。CLI --branching-envelopes源级和配对路由实际RED后GREEN。全XQC-v2 261测试PASS/NumPy ABI警告1、ruff和diffcheck通过。实样v6尚未回放/生产启用；先同20源/8配对冻结样本核查v5资格保留和新增完整形态，再决定候选部署。


### v6首次实样回放发现资源回归，保留失败并减少重复测量

源3993700/配对3993701均终止SOURCE_REPLAY_COMPLETED，20源492层/同8配对72层；ZF50505 cut0/2发生morphology work budget exceeded，不能称v5资格全部保留或通过。cut4/5/6新增19534/11522/6844门，提示完整分支根因有效但仍需修复资源回归。v1目录全部保留。原因是每个组件、每个窗口重复生成全层signal，即使单节点窗口已有完整不可变RAW测量。

修复只复用单节点原始测量事实；多节点分裂/合并窗口仅生成完整对象rows×cols信号、重新测外侧和全历史，不复用资格、不改变门限。75m/0.5deg合成相同16340成员，工作计数10244704→7565313；冻结9M工作限的已提交e07493e内存隔离fixture实际RED，当前修复GREEN。完整形态/正常动作与既有政策保持，下一步v2独立目录同冻源复验，不能覆盖v1失败。

同时读取ZF50505正常cut2已验证SHA的真实证据：radial_source PARTIAL_RESOURCE_LIMIT、failed_module fan、reason X source model-trial budget exceeded；不是形态未运行或显示缓存，已有source_gates86932保留。该模块完整性告警尚未解决，不提高预算伪造验收，也不凭shape补造确认RF源。独立天气/所有网页仍未验收。


### v6优化回放终态和真实正常v5对照图

c4fc70c先main push后仅同步形态模块到活动源、镜像与发布仍v5。源4040542/配对4040543均终止SOURCE_REPLAY_COMPLETED；20源492层及同8配对72层全部EVALUATED/资源弃权0，v5 mask逐门子集保留。28task/output receipt SHA本地核验，verified-summary.json留存。v5源总477835，v6-only151238；同源配对新增101718不重复增加unique。新增跨ZF103/401/402/505/701/702，证明非站号/固定方位专门规则，但不证明无误删。ZF40202 cut4局部天气代理选中253，ZF50505 cut6为40，ZF70205 cut7为49，保留复核冲突，不能用候选数替代天气验收。

branching-render-v1绑定当前已正常发布v5任务ad439bbd（不是旧失败产品），完整RAW/当前QC/形态mask/研究减去mask四栏。505cut4/5两PNG SHA d500918b…/a443e477…本地核验并实际观察：cut4仍可见的长径向进一步覆盖，cut5当前v5已大幅清除，v6再覆盖13个可见原始成员。cut4新mask亦涉及团块附近回波，尚无独立真值，不能称只删污染或将v6直接上线。预览不是正常新v6产品/网页。render/audit SHA dad338bed122d8d9ac0a848754012faec6f7af5bc716f9bb8ae181aacaec58e0。

当前262全XQC-v2测试PASS，ruff/diffcheckPASS，c4fc70c和e07493e gh run list无结果，不称CI绿。IAB截图后getTab恢复仍超时，新增UI仅此前70105一张，累计6实际观察；保持地图复核未完成。正常v5六任务54层已完成但四复核告警保留；radial_source fan model-trial cap部分弃权另需优化。下一步优先完整组件与紧凑天气回波叠加反例、独立S/天气对照及源模块资源诊断，再决定v6候选安装/正常新任务，而非调整全局门限或按方位删图。目标active/小时automation停用/业务可信融合QPE预报不启用。

## 2026-10-02 v7 compact shape counterexamples (default off)

A synthetic compact core attached to an anchored fan reproduces an unsafe v6
proposal: 587 of 589 core gates selected. Explicit v7 retains original compact
contours as geometric ambiguity diagnostics before applying whole-object radial
candidates. It does not label those contours as precipitation or confirmed RF.

Complete native 35/25/15 dBZ contour components are measured in physical XY.
Declared angular gaps and invalid geometry remain barriers; a measured north
seam remains adjacency. Compact qualification uses original component support,
physical covariance and range growth. No hole filling, dilation, station/bearing
exception or weather-label inheritance is used. A higher contour core does not
grant its attached lower contour parent protection. Weak mixed components can
remain ambiguous, including some radial members: independent weather evidence
is still needed to resolve them. Resource caps and atomic abstention remain.

`compact_counterexamples_enabled` requires explicit v7 and the complete branch
graph. `XQC_MORPHOLOGY_COUNTEREXAMPLE_MASK` is a native uint8 diagnostic, default
zero; it does not grant hard-weather or source-kind authority. Existing defaults
and the installed v5 candidate remain unchanged.

Actual RED/GREEN covers attached cores, north seam, native resolutions, weak
mixed shapes, clear radial/fan/broken forms, schema dependencies, resource
abstention and normal QC diagnostics. Scoped XQC-v2 suite: 277 passing, one
existing NumPy ABI warning. Ruff and diff whitespace checks pass. Read-only
source and paired audits accept explicit `--compact-counterexamples`; real
replay and normal publication acceptance are pending. No CI success claimed.

### v7 frozen replay result and remaining counterexample

The 20 RAW replays (492 cuts) and the same eight paired replays (72 cuts)
terminated successfully, with zero morphology resource abstentions. Output
SHA256 checks pass. The per-gate invariant is exact: v7 equals v6 candidates
minus original compact ambiguities. RAW source qualified counts change from
629073 to 610407, withdrawing 18666 ambiguous gates; the paired 16983 withdrawn
gates are a subset of that source set, not additional unique coverage.

The current normal v5 ZF505 05 sample was separately bound and rendered at cuts
4 and 5. Both full five-panel PNG hashes were checked and inspected. Cut 4 still
selects part of the northeast compact-looking body (122275 candidates remain;
481 counterexample gates elsewhere). Cut 5 has 114434 candidates and no compact
counterexample. Thus the synthetic guard does not resolve all real contour
ambiguity. This is an explicit remaining counterexample; v7 is not activated,
weather acceptance and new normal publication remain incomplete. Next diagnosis
must measure complete original contour connectivity and radial/body overlap,
without station exceptions, contour filling or assumed weather labels.

## 2026-10-02 v8 physical transverse counterexample (default off)

The remaining cut-4 contour was measured from unchanged RAW: at 25 dBZ it
contains 2863 selected native gates over 29 rays and 19.575 km of range. Its
range growth is 1.246, physical axis ratio 3.229, radial variance 23.719 million
m² and transverse variance 100.820 million m². The v7 axis-ratio cap rejects
this contour despite most variation being across, rather than along, the radar
radius. The 15 dBZ complete parent also includes long radials (growth 9.685)
and must not inherit the body's protection.

Explicit v8 adds resolved physical direction to the original contour test:
transverse variance at least radial variance is a geometric counterexample to
radial elongation. The original compact-axis cap remains unchanged. All support,
range-growth, native-gap, original-contour and resource constraints remain;
undefined bearings near the origin cannot grant transverse protection. This is
still ambiguity, not precipitation truth or confirmed source evidence.

Two attached transverse-body regressions failed on v7 and pass on v8, including
a measured north-seam case. Coarse/fine native resolution, radial elongation,
pure line/fan/broken forms, explicit schema identity and both read-only CLI
routes are covered. All 285 scoped XQC tests pass, with one existing NumPy ABI
warning. Ruff/diff checks pass. Real v8 replay and normal publication are pending;
production is still v5, and this section does not claim final acceptance.

### v8 real replay and installed image proof

All 20 source tasks / 492 cuts and the same eight paired tasks / 72 cuts reached
terminal success, zero morphology resource abstentions. Task and output hashes
were verified locally. Relative to v7, v8 withdraws exactly 2863 candidate gates
in ZF505 05 cut 4; zero previously protected gates are reintroduced in this
frozen corpus. Source qualified count is 607544 (paired 559707 is a subset).
Both current-normal-bound full PNGs were hash checked and inspected: the measured
25 dBZ transverse component is now retained in the preview. Lower-intensity
mixed body/radial overlap still requires independent context; these images are
not normal publication or weather truth.

An immutable two-module image was built from pushed a3581a4:
`rainpulse-cpu-worker:xqc-transverse-a3581a4-candidate-mb`, image ID
`sha256:0957efe71f8801e8a7047e6ab44a16202eee1ce64d93543559344616516f5218`.
Its parent is installed v5 ec887aacc8. Only core and polar_morphology differ;
793 other Python modules, including decoder and pipeline, have identical hashes.
Installed full-QC synthetic smoke preserves 1373 transverse body gates, removes
external radial candidates from QC/CR, preserves RAW, and grants no confirmed
weather/source or operational authority. First verifier helper failed before
algorithm execution due to an undefined synthetic mask; repaired v2 helper and
receipt are retained separately. The image has not replaced production workers.

Full installed x_qc on the actual cut-4/cut-5 source and model-trial geometry
instrumentation on the existing cut-2 partial-source failure are running as
bounded read-only probes. Their completion and normal publication are pending.

### Complete existing frozen corpus and actual pipeline terminal evidence

The complete pre-existing corpus (22 stations, 46 unique scans, 1219 native
cuts) was re-evaluated with the same v8 policy, four bounded workers. Every task
terminated successfully and every cut was EVALUATED; no morphology resource
abstention and no previously v7-protected gate was reintroduced. All 46 output
hashes and frozen task hashes match the protocol, SHA256
`c26cca2749709904396c519e63284937e7710d337e3874394a3d91ee2ca89629`.
Counts: v6 candidates 2165502, v7 2015500, v8 2012637. The only v8 subtraction
is the measured 2863-gate transverse contour. These are proposal counts, not
weather-verified pollution removal. Unique source coverage remains 1219.

The installed full x_qc probe completed both real cuts and its audit hash was
verified. Cut 4: 119412 morphology gates, 3344 contour ambiguity gates, 2863 of
those still visible in QC; morphology-selected QC visibility and CR admission
both zero. Cut 5: 114434 morphology gates, both selected visibility/CR zero.
Existing action-budget warnings remain; evidence sizes 1655679/1452549 bytes
are below the original 4194304 cap. RAW is unchanged. This proves installed
per-cut behavior, not normal object publication, neighbor context or UI.

Cut-2 source trial diagnostics independently terminated and were hash checked:
500001 trials are consumed, with 185114 repeated eligible training keys within
the same original detector-call/raw-ray context (block 26609, fan 158505).
Geometry prechecks alone exclude only about twenty-four thousand trials. Next
work is bounded reuse of identical reference fits, preserving the target/guard
exclusion, target-dependent reference coverage, model-record cap and all action
criteria. It is not implemented or accepted yet. Normal candidate rollout and
meteorological acceptance remain incomplete; installed production stays v5.

### Bounded reference-fit reuse (2026-10-02)

Implemented a call-local cache recreated for each original RAW ray. Keys are
actual reference-block members after excluding the current target and guard.
Quantile, domain, fan mode, RAW and configuration cannot cross cache scope.
Coverage and target admission are always recomputed. Failed reference fits may
also be reused; neither target decisions nor native gate arrays are cached.
Scalar fits and immutable member keys have conservative byte accounting and
LRU eviction inside the original 32 MiB summary headroom. Trial/model caps and
all numerical criteria remain unchanged. Zero headroom recomputes normally.

The regression fails on the frozen original at an 18th trial with a 17-trial
budget; reuse completes with exactly the same 781 gates and 13 model records.
Five new tests compare masks and complete model records against the original
Git module in RAM across domains, quantiles, fan modes, protected gates,
changed targets, two different RAW rays and guard widths. They also exercise
zero headroom, LRU eviction, cached failure and pre-fit trial rejection.
The complete XQC suite passes 290 tests (one existing NumPy ABI warning).
Scoped Ruff and diff whitespace checks pass. Concurrent S work is preserved.

Real cut-2 replay under the original 500000 budget, high-budget reference
comparison, normal candidate publication and actual UI remain required.
This optimization alone does not constitute weather truth or full acceptance.

Installed actual ZF50505 cut-2 comparison is now terminal and hash verified.
Both use frozen task SHA
`7ed9da6b616ba7e77ba36a63c19a425c5286611e0b64cf479e602f9a69c0da50`.
Old v8 high-budget reference uses 575242 trials; the cached candidate uses
347720 under the unchanged 500000 limit, with 227522 reference-cache hits.
Both produce 32068 model records and exactly identical SHA256 values for all
74 native output arrays. Both terminate `EVALUATED`, with RAW unchanged,
shape-selected QC-visible/CR-admitted counts zero. Summary workspace1333944
plus cache peak1715600 bytes remains below32MiB; evidence3963736 bytes below
4194304. These are actual full per-cut x_qc results, without adjacent-cut
context, object writing or normal publication.

Candidate image
`sha256:c2afb43ac626fa5a3e0486e8cb43b15e607c44320a3fbe1681d2cd89a718cb5d`
changes only source_blocks/source_summary/reference_fit against the previous
v8 image; 793 other Python modules are identical. Production remains v5.
Private hash-checked receipts live under reference-fit-{candidate,reference}-v1;
comparison is reference-fit-actual-verification.json. No job remains running
for these two probes. Next gate is broader real source-cut validation followed
by normal candidate recomputation, publication and UI, plus weather ambiguity
acceptance. Repository CI for cac7a4a is failed (test/lint/performance-CD);
build and independent QC comparison suites pass, so full CI is not claimed.

### Full native comparison and normal candidate rollout (2026-10-02)

The same frozen ZF505/ZF701/ZF702 05 volumes completed both candidate and old
high-budget-reference runs: 6 jobs, 3 unique volumes, 27 unique native cuts.
All 2044 native-array SHA comparisons match. Complete module model records
also match after removing only computation-work counters. Trials total
1024737 vs1562161, with537424 cache hits; no source resource-limit cut remains.
Three existing action-budget warnings persist at ZF505 cuts4/5 and ZF702cut7;
shape-selected QC-visible and CR-admitted counts are zero throughout. This is
per-cut actual x_qc evidence, not neighbor-context or publication evidence.

Candidate rollout now completed on 105's four ops multiband workers:
image `sha256:c2afb43ac626fa5a3e0486e8cb43b15e607c44320a3fbe1681d2cd89a718cb5d`,
network SHA `0b88aba83f774288c1e524d9c431c8f446de26f5c35371803014a02bb128da47`,
fingerprint `453032980fd593912f2a7bf2e234a662ed8732e0c598e54a439a5a03c17d4e39`.
All four workers were idle before replacement and ready afterward; the release
channel was selected with its original revision21 CAS. Only the release ID and
ZF505/ZF701/ZF702 morphology version and three v8 switches change in the
network. Installed manifest verification against previous production shows
five changed/new modules and791 unchanged. Installed smoke preserves1373
compact gates while isolating2613 radial candidate gates, RAW unchanged,
without confirmed source/weather or operational/QPE admission.

Release preparation v1 rejected the image's old generator because it expanded
unrequested defaults. V2 failed before generation because its helper path was
absent/unwritable. Both failed artifacts remain; v3 uses the already committed,
SHA-verified generator in the active remote deployment and enforces the narrow
13-path network diff. These were preparation failures; no production network
was replaced before the successful candidate deployment.

Thirteen normal historical tasks were submitted through genuine plans/runs,
including the user-reported early/next/08:49 and ZF702original/08:08 scans,
the six frozen cross-time volumes, and the original ZF505 volume. Every plan
checks exact input identities and source objects, and stores an idempotency key.
Normal publication and audit are ongoing. The latest verified local snapshot
has11 audited tasks/99 native cuts: RAW/coordinates/time unchanged, source
complete, withheld/shape gates absent from QC and CR, PNGs verified. Warnings
are retained as failures in individual mechanical ledgers. This snapshot is
not a completion claim for all13 tasks, meteorology or UI. Browser reads are
currently failing; computer tool reports a locked Mac, and UI remains pending.

## 2026-10-02: centered pulsing envelopes (v9 candidate)

Real map review after v8 publication (13 tasks / 117 cuts audited) remains
**not accepted**. ZF701 08:03 cut 3 has a narrow southern residual. A frozen
normal-native probe shows those visible gates have HARD_WEATHER=0,
LOCAL_WEATHER=0, BUDGET_WITHHELD=0 and MORPHOLOGY=0. The complete grouped
5 dBZ envelope over 1.5385–68.8885 km has 7 original 10 km windows,
center excursion 1.029924 native footprints, both edge excursions
2.337201/1.757444, initial contraction 0, peak growth 4.004904 and
peak-to-current narrowing 3.917477. Bilateral measured known/clear fractions
are 0.994438. Existing anchored pulsing rejects it because neither edge is
fixed; the expanding branch rejects its return after widening.

The explicit v9 `centered_pulsing_fans_enabled` flag (default false) adds a
fixed-centre alternative to the anchored pulsing branch. It retains the same
native-footprint centre tolerance, full initial-width contraction guard,
minimum span/support/windows, bilateral measured flank contrast, fan range
ratio, immutable RAW membership, gaps, protection and compact/transverse
counterexamples. No fixed station/time/angle, mask dilation, Doppler assumption,
threshold reduction, source claim or confirmed contamination action is added.

Regression: original v8 misses the full double-moving pulse; with the new
identity/flag but unchanged detector the new test ran RED on actual mask
assertion. Detector patch ran GREEN: 11 tests across spacing, gate length,
north seam and elevations plus moving-centre, constant-km/curved/blob,
unavailable flank, protected membership and JSON/Pydantic identity checks.
All scoped XQC tests: 301 passed, one pre-existing NumPy ABI warning.
Actual source replay, normal candidate publication and fresh map acceptance
are still required before deployment/acceptance of v9.


## v9 实际发布复核（2026-10-02）

`12ce5954fa310ba5249747f699576d9ceaf2c1f9` 已合并本地 main、push 后部署。4 个候选 Worker 使用 `xqc-centered-12ce595-candidate-mb`；仅 ZF505/ZF701/ZF702 三个既有试点启用 v9，新规则保持默认关闭。RAW、可信融合、QPE 和预报资格不变。

- 22 站冻结 46 个唯一体扫、1219 原生层逐报告 SHA 核验通过；全部 EVALUATED，无资源弃权；旧候选全部保留、反例掩膜不变。新增 4 层、35830 个候选门点：ZF401 cut0、ZF701 首体扫 cut3、ZF702 用户 08:24 样本 cut4/5。它们是形态候选，不是独立污染真值。新发现的 ZF401 尚未正常重算，不能把只读回放算作该站上线。
- 正常 API 发布 13 个体扫、117 层全部 SUCCEEDED，并逐份核验 source 完整、RAW/原生几何/时间一致、withheld 未进入 QC/CR、PNG 完整。保留 13 层 `DEGRADED_MORPHOLOGY_ACTION_BUDGET` 或 `ACTION_BUDGET_ABSTAINED`，不能计入完整验收通过。
- 实际地图复核：ZF701 08:03 cut3 原残留南向长条已清除；ZF701 08:07 cut1 主扇形清除但仍有弱蓝色向西细线；ZF702 08:08 cut0 主东南扇形/西向直线清除并保留西南紧凑块；ZF702 08:24 cut4 多方位大径向体清除，页面仍正确显示动作预算未完成告警。截图和 SHA 绑定审阅记录保存在私有 `.build/xqc-polar-morphology/`，不提交雷达图件。
- 105 系统盘普通用户可用空间归零，正常复核 observer 退出；计算任务仍有新鲜心跳并最终成功。仅迁移本任务 `.build/xqc-acceptance-20261001` 至数据盘 `rainpulse-xqc-evidence-20261002`，2243 文件 SHA256 校验并保留原路径符号链接，恢复约 742 MiB；修复迁移后证据目录写入所有权，恢复相同 SHA observer。未重启计算任务或删除其他模型。系统盘仍空间紧张。
- 完整回放 v1 wrapper 使用错入口，已终止并保留失败记录，其输出不计验收；v2 使用 `run_source_only`，46 份报告完成本地 SHA 与冻结来源核验。

当前结论是**部分实际地图通过，整体未验收完成**。下一步逐门分析弱残线的形态/保护/动作记录，解决动作预算层的完整处理与安全验收，并对新增 ZF401 候选完成正常管道对照，之后再扩大各站全天发布。不能通过扩大清除比例、降低保护阈值或隐藏告警宣称彻底解决。

弱线逐门续证：正常新 `next` 体扫 cut1、显示阈值 5 dBZ、10 km 以外，270.40° 与272.38°分别仍有41/46个可见门点（15.79–54.64 /12.34–65.36km）。这些门点 morphology/proposed/withheld/budget/hard/local/counterexample 全为0，故不能将当前细线归因于保护或动作预算。此为残留定位，不是完整 RAW 形态资格证明；下一轮需核对它们所在原始历史的支持/间断/肩部测量，不能仅降低阈值强行清除。原始逐门凭据 SHA `1a21b6995a12984cfed60d4ded81613c0e3e88bac881b57c40c701df19ad4228`。该 probe 第一次因系统盘满写入损坏而失败，v2 在迁移恢复后通过，原失败保留。

## 近噪声弱径向残留：独立 RAW 参考研究（2026-10-02）

ZF701 08:07 cut1 的 270.40°/272.38° 两条残线不是受保护或预算恢复的回波。完整 RAW 中，前一条支持率约 42.6%；多个独立 5 km 距离块的 `DBZH - 20 log10(range/km)` 稳定，SNR 主体约 1–3 dB，仅少量门点越过 3 dB 显示剔除门限。原 `source_blocks.detect` 只用高于门限的样本作参考，稀疏越限点无法提供足够参考；这解释了本例弱残线，但不代表所有残留都是同一原因。

新增 `source_blocks.detect(..., near_floor_references=True)` **仅为默认关闭的诊断入口**，未接入 `radial_source`、配置 schema 或正常动作。它允许既有 spread 界限内、REF/SNR 实际成对且未保护的近噪声 RAW 作参考；目标仍必须高于既有噪声门限。保留原双侧实测安静/对比、宽度、参考块覆盖、跨度、范围响应、功率边界、目标与 guard 留出、缺失语义和资源预算。没有修改噪声门限、扩大 source spread 或添加站点/方位例外。默认模型与生产输出保持原路径。

复现测试：

```sh
PYTHONPATH=algorithms algorithms/.venv/bin/python -m pytest algorithms/tests/xqc_v2_20260928/test_near_floor_references.py -q
```

9 个回归覆盖稀疏越限、跨北向/分辨率、未知 SNR、宽回波、范围响应不符、保护门点、越限功率不符合独立边界以及目标不能训练自身。初始入口缺失 RED，首次拟合又因既有双侧对比条件不满足而 RED；保持对比条件，构造符合该条件的信号后 GREEN。完整 XQC-v2 范围 310 项通过，保留已有 NumPy ABI warning。扩大到历史 hardening 范围发现 9 个旧 harness/reference 不匹配失败；内存载入未修改 HEAD 的 `source_blocks` 复测仍为同样 9 个失败，不能宣称全仓/CI 通过。

真实冻结新 `next` 产品探针：50/87 个西向可见残留符合独立参考（40/41、10/46），RAW 不变、hard-weather overlap 为零。其余 37 点中有 1 点 SNR 4.5 dB 超出独立参考边界，另一个原生径向多数待判块未形成通过全部留出资格的参考模型（部分这些块能作为其他目标的参考，故不能直接归因于双侧不足）；不能据肉眼形态直接放宽参考边界。单层凭据 SHA `ea562593335e3aefe5b05817355a68a696995b5323e89fb6a99a02c7baeaded7`。

105 使用 4 个只读容器完成三既有试点 13 个冻结体扫、117 层参考研究，所有输出与输入 SHA 本地核验、RAW/保护不变量通过。它们与旧 117 层是同源，不增加唯一来源样本数。进一步记录全部可见候选与 local-weather/compact 歧义交集的复核正在进行；未完成前不启用诊断候选到正常动作，也不宣称界面残线已经清除。此前 13 个预算告警层的候选仍全部 withheld/QC NaN/CR=0；预算影响确认类别，不能将告警数直接当作恢复显示的污染点数。

补充拒绝证据：272.38° 的完整块表中，block4/5/12 双侧块级几何为 false；block7/9 的独立模型存在但目标/guard 留出后参考覆盖仅 2/3，低于原 0.7；其他早段目标还没有通过全部参考跨度/样本/模式条件。block2/3 可以成为其他目标的参考，不能把它们没有自身模型误解成全部双侧测量缺失。追踪 SHA `c14a25ba6ae22293dd76eb487dfb83ec4a0519784bc344928b0e9368954bc890`；未降低任何条件。

全部 117 层 v2 诊断选中 394069 门点，其中 2592 是当前 5 dBZ、10 km 之外可见回波，hard/local-weather 交集为零；但有 14376 个既有 compact 歧义门点交集（当前可见 100，涉及 5 层）。因此 v2 **不能直接进入正常动作**。后续 v3 将完整 hard/local/compact 原始保护对象同时排除出目标和参考，不能仅在输出末端删掉交集；只有这套保护入口验证通过后才考虑正常候选试点。v1/v2/v3 都没有改写现有发布结果。

v3 已完成 13/13、117/117 层且本地逐输入/输出 SHA 核验：376778 个诊断候选、2447 个当前可见；hard/local/compact 总交集及当前可见交集全部为零，RAW 不变。冻结 `next` cut1 的西向效果仍为 40/41、10/46；南向 194.49° 的 53 个当前可见门点未被本方法选中。比简单减去 compact 输出交集还额外撤回了部分候选，说明保护参考也有实际作用。完整协议和逐层摘要保存在私有 `near-floor-normal-corpus-v3/verified-summary.json`，不计入新增唯一样本或界面上线。下一步重点是用实测门点双侧对比核对块级中位数的掩盖效应与独立参考覆盖，保留原约束，再扩大冻结来源及正常发布验收。

## 完整近噪声源家族与候选动作入口（2026-10-02）

逐门实测否定“块中位数掩盖双肩”的初始假设：270.40°/272.38° 只有 6/41、14/46 个可见点的左右距离之和符合现有窄径向宽度。完整源带应使用现有最大 45° 的多射线模型；没有扩大窄径向 7° 上限。`source_fans.detect(..., near_floor_references=True)` 将近噪声成对 RAW 参考带入既有 primary/upper 距离留出模型、完整家族支撑/相干条件及有限内部关联。实际冻结 next cut1：西向 40/41、46/46（86/87），南向 53 个可见点仍未选中，hard/local/compact 交集全部为零。唯一余点仍不符合独立功率边界，未放宽门限。单层凭据 SHA `0b312bfbcf2e68ff8ba2e53ca1c8d31ff5759118822651dd32c2e42bb0f329c6`。

新增显式默认关闭的 `near_floor_source_candidates_enabled`，Python/JSON 都要求 receiver floor 和完整 compact 保护。正常入口先完成 RAW 形态保护，再用 hard/local/compact 整个对象排除目标及参考；新 `XQC_NEAR_FLOOR_SOURCE_MASK` 与原因位 262144 单独记录。进入既有唯一终处理器及既有候选预算，动作 3、QC NaN、CR=0；不加入已确认污染，不改变 RAW、QPE 或可信融合资格。Audit 保留原显示/动作/CR。源模型 trial/model 总额度继承当前层此前源模块消耗，不能新建 pass 重置额度；新模块资源/保护失败整模块弃权并明确降级，保留既有完成证据。

回归新增完整弱扇形、天气/未知/保护、正常候选动作、动作预算、保护失败、摘要资源失败、audit、JSON 依赖及额度不能重置。完整 JSON 校验同时发现既有 schema 缺少 11 个实际 X 参数（noise censor、fragment、radial_flank_mode），已按原 Python 定义补齐并保留全部旧 allOf 身份/保护约束；没有重生成并丢弃既有约束。

实际完整 standalone `x_qc` 对照 v1 因本地 pipeline 中既有 radome producer 与当前候选镜像依赖不匹配而 13/13 FAILED，保留失败、不计验收；v2 以当前安装 pipeline 加本次精确补丁验证。单独冻结 next 体扫 9 层已完成，随后 4 路 13 体扫/117 层对照正在进行。它验证 RAW/原生坐标/旧源与形态掩膜不变、旧 withheld 全保留、新候选 QC NaN/CR=0/新增动作3；仍不替代正常任务发布、上下文及真实界面验收。候选镜像尚未切换，推广前还需完成共享源额度最终版本核验。


完整管道 v2 中，ZF505 05 时次第二个 REF 层（原生 cut2）出现核心整层弃权后处理 `module_records` KeyError；初始失败凭据保留。新增两条实际 RED/GREEN 回归：后处理兼容整层弃权；新增模块无损证据超限时仅撤回该模块，重新执行同一终处理器并保留全部旧掩膜/证据，不重新拟合、不重置预算、不截断证据。完整 XQC-v2 326 项通过，保留原 NumPy ABI warning；独立新文件/核心/source Ruff 与 diff check 通过。最终源额度与资源修复版本准备 4 路真实 13 体扫/117 层对照 v3，原始弃权原因需从复跑状态核实。105 SSH 随后连接超时，安装 pipeline 补丁读取的结果尚未知，未启动最终复跑、未正常启用或界面验收。

## 最终候选镜像真实对照进展（2026-10-02）

`39b0083` 候选镜像校验通过：5 个目标模块变化、791 个 Python 模块一致；pipeline 使用安装父版本加已提交的精确候选/资源补丁，未带入父镜像缺失的 radome 依赖。镜像 SHA `8a3ab6a43739206f14d592a10dca20f6b3c23b1fd53cf2de0d28f0a726ca6eca`。运行中的 Worker 与网络仍为 v9。

完整 standalone 对照已完成并本地逐输入/输出 SHA 核验 9 个体扫、81 层；RAW、原生坐标、既有 source/morphology/保护掩膜和 withheld 全部保留，新增候选 QC NaN/CR=0/动作3。新 `next` cut1 实际为 40/41、46/46 两条西向弱残线进入候选，共 113 个原先可见门点；试算 source trial 为旧2497＋新增2250，未重置500k额度。这里只是完整管道只读对照，尚未正常发布或地图验收。

旧 v2 对照已终止于 FAILED：12 体扫成功、ZF505 05 时次第二个 REF 层失败。旧记录的“cut1”是顺序位置，原生层号应为 cut2，已纠正。最终版本该层实际输出 `EVIDENCE_BUDGET_ABSTAINED` 并保留旧结果，不再崩溃；新增可见门点为0。该体扫尚在计算，不能计作整个体扫完成；证据容量告警也不能计作模块完整通过。

22 站、46 体扫、1219 原生层的完整 RAW 源研究已排队，等待前一完整对照结束后使用4路计算，不与现有4路竞争。它记录共享试点接收门限向其他源的诊断转移，**不代表其他站门限已核验或可启用正常动作**。候选配置准备的第一版被严格差异检查拦下（父镜像内旧生成器显式带入默认值），未改动运行配置；后续使用 main 已核验的生成器保持只增加三个试点开关。新版本发布、完整范围地图以及跨站点/时次/仰角的最终效果仍待验收。
# 2026-10-02：无动作目标的参考块消耗计算预算

ZF702 原问题体扫的最终安装镜像对照仍在运行。原生 cut 2 的旧规则消耗
355,972 次拟合，新近噪声底规则再消耗 144,029 次后触发共享 500,000 次上限；
cut 4 则触发共享 50,000 条模型记录上限。两层均完整退出新模块，不能算新规则验收成功。

定位到一个通用的无效工作来源：近噪声底模式把低于噪声底的实测门保留为训练参考，
但也为完全没有合格动作目标的距离块做目标排除拟合。新增回归在旧代码上实际消耗
16 次拟合而没有任何候选门或模型记录。修复仅跳过这些空目标块，全部训练参考、
目标保护块排除、原阈值和资源上限保留；未改变旧模式。完整 XQC-v2 327 项测试通过。

这只是减少无效拟合的修复；尚未证明它解决真实 cut 2/4 的全部资源退出，不能宣称
地图问题已经解决。下一步须对同一冻结体扫复核，并继续检查重复模型记录的开销。
正常发布切换和重算脚本已经准备，尚未执行。只读跨站批次完成前保持网络配置稳定。

继续减少三种重复工作：参考覆盖必然失败的组合先做覆盖检查；目标门全部已有完整
第一份证据后停止拟合其他组合；近噪声底主模式只为上一个模式尚未证明的门生成动作
证据。已经证明的门仍作为原始训练参考，禁止把它们从参考域删除。旧模式和近噪声底
模式均与冻结代码比较掩膜、完整证据；主/上分位组合验证候选掩膜不变。336 项 XQC-v2
测试通过。13 体扫最终对照为 117 层：114 层新模块完成、2 层计算/记录上限退出、1 层
证据字节上限退出；新增可见候选 4,029 门全部 QC 缺测且 CR 禁入。此计数不证明三层
退出已经解决，也不代表新正常发布或真实地图验收已完成。

安装镜像 `be7226e` 的复核发现 ZF505 05 时段 cut 2 仍触及证据字节上限：保留的旧
完整证据约 3.96 MB，其中径向对象约 2.40 MB、源模型约 1.12 MB；新增模块没有空间。
统一对所有扇形模式减少重复动作证明：主分位仅补充上分位未证明的门；窄扇形仅补充
已通过完整家族支撑/独立走廊证据的门之外的目标。训练参考完全保留，单条稀疏射线
没有家族支撑时仍要求窄扇形独立证明。方法标识更新为
`receiver-fan-family-heldout-v3-first-proof`。旧/新模式对照掩膜相等；340 项测试通过。
不增加原资源上限、不截断证据、不采用固定站点/方位/时次特例。仍须实际安装复核。

网页实查确认用户给出的 ZF702 链接固定在历史结果 `sx-quality-v2-z10-20260930`，
页面已显示历史结果提示及“查看最新结果”。该事实与实际算法预算退出分别记录；
不能用旧链接的问题代替当前算法和新图件的验收。
