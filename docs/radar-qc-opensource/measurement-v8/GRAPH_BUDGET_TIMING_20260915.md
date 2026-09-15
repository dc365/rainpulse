# P1 图预算降级及独立证据计时

## 改动

GraphBudgetExceeded仅表示已知节点/边容量耗尽。统一graph_with_fallback
只捕获构图阶段的该异常，不捕获输入、几何及仲裁异常。超限时舍弃部分图，
返回完整前序决策及degraded_budget回执，记录baseline_preserved/partial_graph_used。

两个调用点均已覆盖：runner最终图阶段和独立Stage A。
Stage A预算不足时不提供可信天气donor，也不提供“未出现干扰”的时间负票；
原始前序动作继续保留。不能让不完整证据变成保护污染的理由。

正常未超预算路径仍执行原有构图与仲裁。Stage A代码指纹随此次变更改变，
历史缓存不应当作新代码结果使用。

独立证据新增运行日志qc_stage_a_timing，分为identity、library、radial_objects、
initial_decision、paper_evidence_fusion、range_crossradar、residual、graph。
新计时不加入资产字段。旧elapsed_ms字段本轮未调整。

## 验证与范围

105实验镜像挂载新代码测试，包括节点/边回退、普通错误仍抛出、
降级Stage A禁止天气投票、真实Worker在预算耗尽后仍能序列化。
没有切换线上Worker镜像；本轮不宣称识别率或整体速度提高。

下一步根据预热后的计时定位库函数内部热点；同时保持静态先验资产盘点
为独立待办，不以单场降水数据替代晴空统计。

## 实测完成

相关20项测试先通过，新增Worker降级序列化测试后5项预算测试全部通过，
合计21个不同测试通过。
同一真实最低仰角连续执行两次（2CPU限额）：library冷启动3956ms、预热730ms；
预热residual282ms、graph227ms、paper236ms、range107ms。
冷启动含依赖初始化，不能将3956ms当作每扫稳定成本，也未进行前后优化测速。
