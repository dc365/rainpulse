# Object Consensus OC1 / 独立实验数据契约

状态：research；operational_eligible=false。本契约不是已有 QCRadarVolume 版本。

输入使用不可变原始 `ray × gate` float 数组和独立 available 布尔数组，射线原顺序、方位、仰角、gap_after、geometry_good、range_m 和 phase_period_deg 明确。原始数值包含 NaN 时，即使 available 为真也不视为有效。不接受无限值、对象 dtype、隐式填充、逆序/不等距距离。无 DBZH/SNR/极化分支所需数据时输出原因，不伪造值。

算法入口不接收 ROI、基线动作、锚点、站号、真实时间或定位。基线只在独立 policy 中用于保留已有动作和计算新增变化；ROI 仅在报告阶段分组。跨站证据只能以带可比性掩码和资源回执的输入提供，不能从 baseline_* 分数自行宣布可信。

输出分开保存 model/policy/code/input/evidence hash；同一模型输出可由 audit 和明确确认的 experiment_quarantine 两种策略评价。两种策略不能共用同一个产物身份。

- family_code：0 无源模型；1 噪声型跨块签名；2 高相干签名。不是污染分类。
- state：0 未观测；1 未提出；2 参考不足/模型失败；3 有模型但目标不相容；4 天气反证/混合冲突；5 源假设暂占优、可实验隔离；6 宽束或单边参考未决。
- reason：uint32 位图，参考不足、缺矩、相位对不足、边界、天气反证等可并列；null/NaN 表示未计算，绝不将其记为阴性。
- domain_id 仅赋给实际原始回波门。短缺测区间可关联身份但无 ID/动作，真实无回波或长缺口会分割。reference_ranges 包含实际门区间；任何参考不得与目标块及 guard 相交。bundle_id 仅作已观测相邻对象关系，不用于递归传递动作。
- confirmed_addition 恒为 0：当前无外部真值与已校准确认模型。实验仅改变 quarantine/action/QPE 资格及低质量标志，不声称确认 RFI。
- audit 模式原 baseline 所有数值、掩码、动作逐数组相同。experiment 模式保留所有既有拒绝/隔离，禁止新增有效门，禁止修改原始字段，禁止恢复原值。总新增资格损失超预算时回退整个切面，不部分截断为达预算。

所有输出只写新目录，临时目录验证后原子重命名；使用确定性 NPY/ZIP 序列化。详细 folds/domains/逐门字段只存文件，不放 NATS。默认关闭且无规划器选择。旧 V7 资产不得写入本实验动作后继续标为原来的 V7。

## Test-server pipeline integration (7.1.0)

Pipeline `qc-opensource-7.1.0`, decision `object-consensus-oc1` adds OC1 after
V7 graph decisions. OC1_BASELINE_QUARANTINE_MASK and
OC1_ADDED_QUARANTINE_MASK record its exact monotone projection; validators
check both the pre-OC1 V7 ledger and final union. First decider code 8 is
OBJECT_CONSENSUS. No new confirmed-pollution flags are created. The pipeline
remains operational_eligible=false and uses the acknowledged experimental
10% whole-cut eligibility-loss budget. Successful derived publications replace
prior visible products through the existing regeneration flow; raw assets stay immutable.
