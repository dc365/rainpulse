# 实施设计：先使融合可核验，再固定算法

## main 集成修订（2026-10-08）

原交付包基线为 `7585486`。安装器发现现有来源追踪改动后拒绝覆盖；此次逐项集成，没有使用跳过哈希的安装方式。原包及其哈希清单保留为交付证据，实际实现以本仓库为准。

系列接口复用已发布的 `sx-configured-series-v2` SQL 身份：完整 Worker 身份、冻结的 S QC 策略、请求站网及不完整历史任务的 run 隔离均保留。新响应合同 `rainpulse.sx-series-v1` 只是读接口封装，不重新编号旧系列。只有选中系列具有帧数统计；其他系列计数为未知，超过 1500 帧明确标记截断。

前端保留完整站网优先、当前时次的最新同站网结果、显式固定系列及时间轴待定期间不发请求的行为。时次改为精确匹配，不把 08:13 归到 08:12。新清单和详情的结果身份、系列和时次必须一致。

生产者新增来源合同；旧产品原样读取，不补造来源。S、X、联合产品各自使用自己的原生赢家。`WINNER_BAND` 是类别码（0 无有限回波赢家、1 S、2 X），0 不表示零雨量；反射率缺测仍为 NaN。新增统计与审计不参与 QC 准入或融合选值。

同时集成已有的 S QC 冻结身份及 Z9595 单站诊断关联，以满足本次来源合同。新增镜像继承 105 已有五站融合镜像，仅覆盖所列融合与来源模块；S/X 质控科学算法、现有后台批次、阈值和配置不随此次发布更换。

## 105 发布验收（2026-10-08）

实现提交 `99e333a`、旧组合标量点查兼容修订 `8fb83a2` 已在本地 main。Go/Web 以 `8fb83a2ab1eba771a449c9ce5ae184c999184d66` 构建并原位发布；Go 服务 PID 2667953，二进制 SHA-256 `3f5ce91b852cc4274db31caddf26ea08946ddea7dbdaf6beaf8c6fff5274a83f`。15 个新网页资源可用，保留 14 个上一版资源；16 个配置文件、41 个原有容器镜像及默认算法频道保持不变，任务准入已恢复。

本地 Python 177 项、Web 198 项、Go 全包测试、Go vet、网页构建及生成合同检查通过。已发布 08:18 旧系列 `a7789d41…` 的原 result_id 和 series_id 保持一致；三个主产品点查均可用。旧产品缺新版来源合同，诚实返回 `legacy_unresolved`，不因此拒绝原有数值。浏览器实测自动选取完整请求站网，显示实际输入 5 S + 20 X、S+X 图层及回波；控制台无错误。该网页检查使用现有历史产品，没有把它标成新算法产物。

候选镜像为 `rainpulse-cpu-worker:sx-finish-20261008-v3`，镜像 ID `sha256:801bc0403bd337eac18f99004c39cca428c51813d2113b5454fb33bf55791120`。容器 `rainpulse-ops-sx-finish-20261008-v3` 注册 ready，指纹 `6c436aa55009019fdeae4308473a0842aeda32e00365e918dcb89c903becea69`；4 GiB 内存上限、2 CPU，三个原配置挂载均只读。默认频道尚未选择此指纹，以免改变未完成的冻结五站批次。

镜像打包验收发现，仓库的 `quality.py` 依赖较新的湿罩和相位模块，不能单独覆盖旧镜像。初版只在未发布的回放容器中失败；最终 v3 不覆盖 `quality.py`，完整继承基础镜像现行 X 质控实现及参数身份。基础镜像固定为 `sha256:6f1caf801b9049f558f4261eb7f1fb314a8c3015d02203a627c2f21ca2a7356b`。覆盖文件为 adapters、comparison_finish、comparison_provenance、composite_sampling、experimental、fusion、fusion_audit、horizontal_plan、managed、product、qc_identity、stream_fusion、stream_managed 共 13 个模块，各自 SHA 记录在 `worker-build-source-v3.json`；避免把仓库中其他科学改动带入运行时。

真实 2026-08-28 08:18 北京时间、Z9591 + ZF101 原生资料在最终镜像内只读回放通过：51 个切面，S 有限回波格点 27130、X 1023，两站都有获胜贡献；联合水平反射率逐格等于 S/X 的 fmax，缺测一致。来源合同 `rainpulse.sx-source-v2`、请求/输入站数、非 QPE 资格及点查 24 字段/2 MiB 上限通过；耗时 96.8 秒，11 产品、2580 对象、约 9.57 MB。这是两站工程回放，不代表 29 站全网重算、等高科学验收或气象精度提升。

105 回执位于现有部署目录下 `.build/sx-finish-20261008/`：`deployment.json`、`actual-image-replay.json`、`candidate-ready.json`、`worker-build-source-v3.json`。没有数据库迁移、RAW 修改、默认算法切换或新增全日重算；原有冻结任务继续运行。下一阶段先做全网代表时次来源与性能验收，再单独安排版本切换和补算。

验收命令：`PYTHONPATH=algorithms algorithms/.venv/bin/python -m pytest -o addopts='' -q algorithms/tests/sx_finish_20261008 algorithms/tests/multiband`、`bash scripts/go_control.sh test ./...`、`bash scripts/go_control.sh vet ./...`、`pnpm --dir apps/web test`、`pnpm --dir apps/web build`、`bash scripts/check_generated_contracts.sh`。本地测试与 105 上的镜像、链接、点查验收分别记录；这些检查不代表气象精度提升，也不代表历史补算已经全部完成。

## 保持的科学边界

本次不引入新融合权重、跨波段经验偏差、时间外推、插值填洞、形态删除或自动标定。旧QC资格、路径/湿罩条件和时效继续决定准入；同高度质量竞争与垂直最大值顺序不变。S-only、X-only和联合产品仍各自运算，不把后者等同于无条件全站最大值。未决和缺测不变成零雨量。

## 第一批：来源/产品合同

`comparison_provenance.py` 是当前生产者的规范化出口，不是历史资产猜测器。旧global source index保持不动：水平路径按体扫记录并补充sweep_numbers，等高路径仍按切面记录。每个产品有自己的完整sources表及明确source_indices/winner_source_indices，不能过滤数组后忘记重映射表。

新增站点表把同站不同切面合并为显示身份；WINNER_SOURCE保留精确观测身份，WINNER_STATION_INDEX/WINNER_SITE负责站点分类。counts明确区分requested/input/native-qualified/spatial-qualified/winning-echo station以及source entries/cuts。缺请求集合或未收集空间证据时为null，不把未知显示为零。

比较包保留 `*_S_ONLY` / `*_X_ONLY` 原生赢家及质量覆盖数组。三个主图点查都以规范字段名CR_DBZH、WINNER_SOURCE、WINNER_SWEEP_NUMBER等返回本图数据。源表SHA写入点查identity；Go核对原始manifest内的紧凑source JSON哈希、范围、波段及原生索引，返回受限来源摘要。没有有限回波赢家时不伪造原生行/门。

原Python点查上限24字段而Go是16；当前完整主图使用23，Go改为24并将受限tile JSON上限从1MiB改为2MiB。解压后尺寸仍严格为64×64×字段数×8，并执行压缩内容哈希和有限数值检查。

已有系列前端与旧hook存在接口不一致。新增系列查询只读现有成功任务，不改变默认清单查询；每组按冻结产品/网络/执行/代码身份和请求站点形成稳定摘要。无完整身份时legacy=true，不能因此升级资格。自动查询在请求时次内挑选已完成最新系列，显式series_id找不到就无结果，不返回其他系列。后端清单有1500条/32MiB上限，截断是部分清单，不宣称完整全日统计。

## 第二批：准入与选源审计

使用ContextVar隔离同步任务/线程上下文；任务异常也恢复上下文。`fusion.update_tile`及`finish_tile`只添加诊断调用，选择核参数、运算顺序和写入保持一致。原始数组只读，不在审计中重新生成新的资格。

原生门分区按实际准入先确定未获准集合，再依次说明缺测、硬拒绝、withheld、标定、路径、湿罩、无效量值、其他原因；获准门独立归入qualified。这个顺序只是互斥统计，不是气象原因优先级。同一门命中多个原因的位计数另列，不能将其相加作删除数。水平试验可能合法保留action=3且未标定的X值；审计必须记录其实际试验准入及不确定诊断，不能把通用action解释再覆盖该方法。

投影统计单位是“来源×网格×配置高度层的一次机会”。分开计算geometry_or_age_unsupported、height_unrepresented、unobserved、inadmissible、nonpositive_score、qualified；由于旧Footprint合并了空间和年龄布尔值，不能虚构其独立因果分解。统计不是互不重叠的整网面积。

同高度最终获选计数从完成的层状态读取，不以暂时成为赢家的次数代替；qualified减去最终层赢家数是未在同层获选的机会数。层回波赢家与最终柱最大值计数之差是垂直未获选，不应叫QC删除。

采样位置在读取回波前固定为全网格/层线性索引的最多4096个位置，tile大小改变不会改变采样点。仅等高方法比较S/X；实际波束中心高差≤500m、年龄差≤60s、分辨尺度比≤2，才能进入配对统计。缺一方、时间不够可比、高度不够可比、尺度不够可比、有限回波配对、真实clear/echo冲突分开报告。此抽样受当前准入与质量选择影响，不是无偏全场标定样本、雨量真值或自动订正参数。

新只读CLI逐格核对已导出比较包的原生赢家、子集及声明对象哈希，NPZ头尺寸与实际文件大小预检查；不需要新的标注、雨量或其他观测。

## 第三批：等价性能

水平路径仍使用实际原始射线、原1°角容差、原右侧差分距离门宽度和年龄条件。不强行与quality_height的波束半宽/相邻最小门宽规则统一，因为那属于科学/几何政策变更。

任务内GeometryCache用原执行配置的字节预算，缓存网格经纬度和站点到格点的方位/距离；键含实际经纬度，不按波段猜几何。每切面方位排序一次，斜距和门/时效仍各自计算。无交集剔除采用由最大距离门及门宽推得的保守地面半径，再加1m；只在所有格点都不可能被覆盖时跳过块，不改变边界内掩码。

比较出图的plan复现原transform_bounds(densify_pts=41)、像元中心、floor最近格点、float32输出与north-up翻转。每个字段独立处理finite，绝不借用另一个图的有效掩码。一次编码最多保留一个≤32MiB计划；不同网格自动替换，结束释放。最终arrays.npz仅编码一次，保留来源、比较和诊断字段。

## 接入与回滚

旧已有函数由完整Git blob哈希保护，先解析AST再添加import/decorator/诊断调用或替换两个产品编码函数体。新模块全部独立文件，详见changes.json。没有数据库迁移；读取旧manifest缺source_contract时继续显示标量但不猜测来源。新产品字段不能悄悄写回旧结果；回滚旧Worker时保留它不支持的新产物，以明确版本方式读取。

安装器一次预检查所有目标，再备份/逐文件原子写入；每步复核并保留回执。故障恢复/回滚不覆盖应用后的新工作。不修改线上配置，不触发新任务或重启已有控制器。
