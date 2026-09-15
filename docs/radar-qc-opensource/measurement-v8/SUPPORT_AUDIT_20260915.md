# 天气支撑拆分与真实数据归因

## 实施内容

- ContextConfig 新增 split_radial_weather_support，默认 false。关闭时保留原始参数 hash 和逐门 max(vertical,cross) 行为。
- 独立实验配置 fujian-qc-support-split-experiment.yaml 开启该开关；只对已有径向候选改用跨雷达支撑，未知仍为 NaN，不构成污染证据。后续多证据拒绝条件不变。没有修改线上 planner 或 Worker 镜像。
- qc_support_audit.py 读取冻结 QC 和 V8 输出，按原始 ray/gate 生成六图及重叠类别统计。不是标注真值，不能据此计算准确率。
- qc_support_counterfactual.py 使用同一冻结垂直/跨雷达支撑做初始 decide 阶段的成对实验；不是全流程重放，不声称等同已发布 V7。不写线上资产。

## 105 实测

29 个独立扫描，sweep_000：

- 29 份归因和对照图成功生成。
- 208,690 个门 DBZH_QC 仍可见且已 quarantine。统计域为原始 DBZH>=10 dBZ、有效、QC有限值；隔离保留显示不能解释为未执行质控。
- 已有候选且仅垂直强支撑：0 门。该批样本不支持“垂直误保护是主要残留原因”。
- 29 个初始 decide 成对实验成功，动作变化均为 0。
- **29 个 V8 原生检测全部返回 unsupported_angular_geometry，calls=0。** 特征提取成功不等于 bRopo 已执行；原生分数缺测不等于阴性。
- 49 项测试通过（measurement_v8 + support_split）。环境报告 numpy ABI warning；未出现测试失败，仍应在下一次镜像依赖维护中核查。

服务器产物：runtime/reports/qc-measurement-v8-real-20260915/results，包含逐扫描 support-audit/comparison.png、support-audit.json、support-counterfactual.json 和逐门成对动作 NPZ。

## 后续重点

原生检测几何适配优先于阈值调参。先量化真实方位增量、重复射线、缺口和连续有效域，再设计局部规则采样或有误差约束的映射；保留原始门映射，不补零，不将不支持区域当作无干扰。另需让展示明确区别 DBZH_QC 与 DBZH_USABLE/隔离区域。

本轮未提升线上算法版本，未宣称质控效果改善；避免部署已实测无效的支撑开关。
