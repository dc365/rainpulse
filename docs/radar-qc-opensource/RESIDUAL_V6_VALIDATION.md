# V6 本地验证记录（2026-09-14）

## 实际运行

- Python `pytest algorithms/tests configs/tests contracts/tests -o addopts='' -q`：**836 passed，18 warnings，0 failed**，最终稳定算法代码上全量执行。
- V6 三个专属测试文件：**48 passed**（已包含在上述全量），另将 V6 加入已有完整 QC→Hybrid→Grid→拼图→QPE 生成/缺测测试。
- Go 全包 `test ./...`、`vet ./...`、`build ./...`：全部退出码 0，使用仓库 `scripts/go_control.sh` 和 Go1.26.8。
- Web Vitest：**79 passed / 19 files**；TypeScript 和 Vite 生产构建通过。
- Ruff：通过；Web ESLint：0错误、3条既有警告（HistoryPicker、MainWorkspace 的 Fast Refresh，VerificationAnalysis 的 effect 依赖）。
- 19 个既有 QC/diagnostics 配置文件与上游逐字节一致，旧参数摘要冻结表通过。
- 新 PNG 采样独立版本测试通过：旧 renderer 保留原语义，新1.2.0声明 native-footprint-v2。

详细原始日志在交付包 `validation/`。上述为本地真实执行，不是 GitHub Actions 结果；本次没有远端发布。

## 使用环境

Python3.13.5、NumPy2.5.2、SciPy1.18.1、Py-ART2.2.5、wradlib2.9.5、Zarr2.18.7、pytest8.4.2；Go1.26.8，Node22.16.0。
本环境使用既有离线工具包。没有更新项目依赖锁，也没有把依赖二进制、字体或 node_modules 放入源码包。
18条 Python 警告来自原有 pySTEPS 合成输入中的奇异矩阵/对数零值路径，不是通过忽略新失败来获得通过。

## 回归中发现并修正

首次全量有两个旧摘要测试失败：测试直接对增加了 `residual=None` 的 model_dump 哈希，而运行时正确排除了缺省扩展。同步测试的旧口径（排除不存在的扩展），**没有改变任何冻结配置/旧参数摘要期望值**；固定摘要表仍单独校验。

原生数值平台负例以实际 model_code=3 域为判定域，不能把尚未进入平台模型的近距门也称为平台域。V6 不将未知平台域提升为新增确认污染。

新增 V6 全关闭检查也覆盖质量指数与标志，而非只比较动作；全关闭保持 V5 精确值。

## 合成演示（不是真实气象验收）

两组同输入、同冻结上下文的真实库核心对照：

|案例|V5确认/隔离/定量可用门|V6确认/隔离/定量可用门|
|---|---|---|
|稀疏有效离群点切割长强结构|0 / 0 / 5520|0 / 5076 / 444|
|弱断续细线|0 / 0 / 5509|0 / 416 / 5093|

这些数字是合成全扫描的工程计数，不是 Z9591/Z9598 的漏检率。新增都是隔离，**不能报告为确认干扰召回增加**。演示包含原始矩、标签、冻结任务、配置和报告，重跑脚本为 `scripts/demo_qc_residual_v6.py`。其网络验收结论为 INSUFFICIENT，绝不提升业务资格。

## 未完成的现场验证

未取得用户截图的原始体扫；未运行真实过程/人工标签/雨量站验证、内网 Docker 联调、实际浏览器 PPI 验收及目标硬件 P95/峰值内存测量。原生 bRopo 扩展不在本环境，真实 Emitter/Emitter2 未运行；其调用入口和未执行边界不是性能/气象效果证据。
