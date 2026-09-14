# V7 证据图测试候选

2026-09-15 接收 `RainPulse_QC_V7_Current_Work_Repack_20260915`。该包是中断工作树，17 个交付文件经 SHA256 核验后合入当前 main，不覆盖其他源码。不是完整 PR-0～PR-6 验收版。

实际接入：诊断 PNG 与 QC 资产/参数/内容摘要绑定；当前及参考体扫使用独立 Stage A；局部门段假设及动作仲裁；首次排除阶段取证。已有 Py-ART/wradlib 路径继续使用。包内没有可验证的原生 bRopo Emitter，不能声称启用了原生 bRopo/RDD。

集成修复：V7 纳入完整输出校验，V6 校验使用 V7 之前的冻结决定，V7 单独验证新增动作的候选域、原始有效性和不恢复已排除观测。否则交付包在实际 Worker 序列化阶段失败。V7 前基线质量独立保存，避免把新隔离产生的质量降低混入旧基线。取证测试遵循新的“身份及选点资格已检查，完整重渲染未执行”语义。

验证：`uv run --project algorithms pytest algorithms/tests/test_evidence_graph_v7_integration.py algorithms/tests/test_residual_v61_integration.py algorithms/tests/test_residual_v61_repair.py algorithms/tests/test_diagnostics.py -q`；相关 Go API/workflow 测试、Web build、带 BDP 集成的 Linux Go 构建。真实库合成测试不是气象效果验收。

测试部署使用 `deploy/docker-compose.qc-evidence-graph-v7.yaml`，镜像 `rainpulse-cpu-worker:qc-opensource-7.0.0`，QC 配置 `configs/qc/fujian-qc-evidence-graph-v7.yaml`。保持 `operational_eligible=false`；原生规划器与 QC/Grid/Mosaic/QPE/Diagnostics 在队列排空后协调切换。诊断采样版本不变，元数据新增字段用于直接确认每张图的 QC 版本。

现场脚本和结果位于 `runtime/reports/qc-evidence-v7-20260915/`。不改原始/标准化体扫；已有成功产品保留至新结果发布。回退使用该目录中受限的环境配置、旧二进制、已改文件备份及 V6.1 镜像。

105 首轮真实任务发现 V7 图节点明细使完成事件超过 NATS 上限（result_publish / MaxPayloadError）。修复为完成事件只发有界标量和 `qc/summary.json` 定位；完整取证明细仍在受摘要校验的 QC 资产中，不改变算法动作或提高消息上限。运行镜像修订为 `qc-opensource-7.0.0-r1`，算法配置版本仍为 7.0.0。
