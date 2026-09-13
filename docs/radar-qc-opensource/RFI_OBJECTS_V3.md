# RFI Objects V3：多变量外围复核候选

状态：工程候选，默认不替换业务成功版本。V3 只在显式选择
`configs/qc/fujian-qc-rfi-objects-v3.yaml` 与配套 Worker 覆盖文件时启用。

## 为什么需要 V3

V2 已经将径向污染从“整射线阈值”推进到“原始极坐标对象”，但复盘 08:15、08:30、08:45 的真实图像后仍有三类缺口：

1. 细条或宽扇区边缘落在已识别对象外，残余处理接触不到。
2. 相关系数较高时，明显径向形态仍可能被当成普通有效测量。
3. 起伏较大的条纹不满足“沿距足够平滑”的入口，整段漏进 QPE。

V3 不扩大已删除掩码来追求黑图，而是把对象分成“高置信核心”和“待复核外围”。外围区域需要多变量证据再次确认；确认失败时只隔离定量使用，不计为确认非气象剔除。

## 主要变化

- `qc-opensource-3.0.0` / `rfi-objects-v3` 是独立版本，V2 参数身份不变。
- 新增粗糙径向结构入口：`rough_candidate_enabled` 与 `rough_maximum_axial_std_db` 允许斑驳但仍沿径向组织的条纹进入复核。
- 新增高相关形态隔离：`high_rho_self_signature_enabled` 允许高相关但有明确长条形态的测量进入隔离/复核；`protected_rhohv` 以上仍保护，不被这一路吸收。
- 新增外围复核范围：`RFI_PERIPHERAL_REVIEW_MASK` 只是检查范围，不是对象成员，也不是硬拒绝。
- 新增多变量分数：`RFI_V3_EVIDENCE_SCORE` 汇总对象形态、粗糙结构、PHIDP/ZDR/RHOHV 纹理、时间支持与通用气象评分。天气支持会降低分数，但不会恢复已确认污染测量。
- 新增确认标记：`RFI_V3_CONFIRMATION_MASK` 和 `RFI_V3_PERIPHERAL_USED_MASK` 用于诊断 V3 相比 V2 的实际来源。

## 下游语义

- `REJECT`：确认污染；设置 `NON_METEOROLOGICAL`，径向类同时设置 `RADIAL_INTERFERENCE`。
- `DOWNWEIGHT + RFI_QUARANTINE_MASK`：高风险未决；不进入 QPE，不计作确认剔除成功。
- `KEEP`：仍需通过 Hybrid、遮挡、距离和下游质量门槛。
- 原始缺测始终保持缺测，不因对象关联或外围复核变成观测。

## 建议验收

1. 先用真实 08:15、08:30、08:45 以及 08:10、08:35、08:40 重算，导出 QC review 报告。
2. 对比 V2 / V3 的确认剔除、未决隔离、QPE 可用域和最长残余径向长度。
3. 人工标注可信天气、确认污染、混合污染、不确定四类，不能只看图像变黑。
4. 再使用不参与调参的连续时段验证，尤其检查强对流、弱雨边缘和近站块状回波是否被过度隔离。

## 启用

候选配置：

```bash
RAINPULSE_RADAR_QC_CONFIG=/opt/rainpulse/configs/qc/fujian-qc-rfi-objects-v3.yaml
RAINPULSE_QC_FLAG_DEFINITIONS=/opt/rainpulse/configs/qc/flag-definitions-v2.yaml
```

Compose 覆盖：

```bash
deploy/docker-compose.qc-rfi-objects-v3.yaml
```

不要在同一批分析中混用 V2 与 V3。切换时先排空相关队列或使用独立 profile/queue，随后从 QC 到 Hybrid、拼图、QPE 使用同一条版本链。
