# 历史案例：仅雷达质控重算

2026-09-16 修复：旧入口逐起报调用 forecast_all；可用贡献站不足两个会在全链路输入检查失败，同时重叠输入帧重复计算。该状态不代表 OC1 算法失败。

后台“历史案例 · 雷达质控重算”现在一次提交当日批次。Go 将当天所有扫描和所选分析的精确贡献扫描按 scan_id 去重，写入 PostgreSQL；关闭浏览器后仍运行。缺少 normalized_uri 的扫描明确失败。无须起报 run_id。

只调度 radar.qc 和 analysis.diagnostics，各最多四个待完成任务。网页每五秒查询持久进度，分别显示质控和对照图完成数及具体错误。对照图依赖的扫描完成后即可刷新，其他扫描失败不阻塞无关时次。

对照图选用各时次和格网最新的 ANALYSIS_READY 分析，读取最新 QC；复用已有格网分析，不生成格点、拼图、QPE、LK 或其他预报。其他产品仍是原结果，不表示已经同步重算。QC-only 扫描排除自动后续格点调度；未来显式全链路重算仍可提交。

批次身份复用 pipeline_regeneration_requests，preset=radar_qc_only，无 forecast source/target；不会制造已发布预报。失败批次允许重新提交，当日活跃批次重复提交返回原批次；不同日期同时提交受限。诊断失败保留原图，不能视为更新完成。

部署需应用 0020_qc_only_batches.sql，再更新 Go 二进制和 Web 静态文件。计算镜像及 OC1 算法未改变。

验证：Go workspace/controlplane/postgres/webgateway 相关测试；Web HistoricalQCPanel 与 AdminWorkspace 测试及 production build。线上必须确认 QC 批次任务类型与实际完成情况。

105 实测：2026-08-28 批次 3389d299-6a7f-4dde-b7ad-d67e96c57218，共 452 个去重体扫、123 个最新网页对照时次；已确认四个 radar.qc 任务运行，重复 POST 返回原批次。尚不代表全日完成或算法效果已验收。

## 2026-09-16 出图补修

原批次 452/452 质控成功（09:00 后 414/414），但旧 RadarAnalysis 的 qc-flags-v1 与当前 v2 出图配置不符，123 个对照图未发布。兼容仅限 v1→v2 且全部原有位定义一致；保留 analysis_flag_definition_version，格网图例仍按 v1 解释，不重算或冒充新 QPE。测试覆盖成功混合版本及错误位映射拒绝。

迁移 0021 取消 diagnostic_runs 的 analysis/config/renderer 唯一约束；同一分析的 QC 输入可更新，重算幂等由包含 regeneration ID 的 job_id 保证。旧已成功诊断保留至新图发布。

105 使用 diagnostics 镜像 qc-opensource-7.1.0-oc1-display1。补图批次 38f8ca9a-bb2f-4e80-a3c1-64e58a529a61 复用原 452 个成功 QC job，仅重新排队 123 个 display。优先09:05与09:10已经成功；09:10 Z9598 参考体扫的新图绑定 qc-opensource-7.1.0，PNG 非透明像素由15087降为11097，3990像素有变化。这是显示差异证据，不是气象误删率或完整效果验收。
