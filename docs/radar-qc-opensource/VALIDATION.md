# 工程验证记录

本次以候选实现提交 `e150cc9` 为基线，在 Linux 隔离环境、已安装的真实库上执行；随后将候选镜像部署到 105 服务器做了真实链路回放。候选仍保持 `operational_eligible: false`，以下结果是测试环境证据，不是业务准入结论。

| 检查 | 实际结果 |
| --- | --- |
| `pytest algorithms/tests configs/tests contracts/tests` | 656 passed，退出码0；18条既有 pySTEPS 数值警告 |
| 新 QC 两个测试文件 | 24 passed，退出码0；包含真实 Py-ART/wradlib、Worker 重试字节一致、字段/掩码/版本、QC→Hybrid→mosaic→QPE合成链路 |
| Go全包 | `scripts/go_control.sh test ./...` 通过 |
| Web | 19个测试文件，73 passed；TypeScript和Vite生产构建通过 |
| Ruff | algorithms、configs/tests、contracts/tests通过 |
| ESLint | 0 errors、3 warnings；均为既有页面 Fast Refresh/Hook依赖警告 |
| Compose候选覆盖 | YAML解析和五类Worker配置一致性通过；105 上候选镜像 `rainpulse-cpu-worker:qc-opensource-1.0.0` 已构建并运行，Py-ART 2.2.5、wradlib 2.9.5 镜像内导入通过 |

Go 使用1.26.8工具链和离线依赖缓存，并沿用仓库缺少私有BDP源码时的公开桥接测试机制。此结果不是私有BDP运行环境/真实PostgreSQL/NATS/MinIO部署证明。Python使用3.13，arm_pyart 2.2.5、wradlib 2.9.5；依赖解析结果保存在 algorithms/uv.lock，未关闭版本检查以通过测试。

第一次全量测试发现冻结身份保护测试引用旧的 planner.go 文件位置，已修正为主线现有目录并保留原断言。最终全量测试结果见上表。新增链路测试也捕获并修复了参数摘要字段名不一致，避免只测独立算法而遗漏下游接入。

尚未执行：独立真实天气标签/多站多过程回放、原生RADVOL SPIKE、站点性能P95/RSS及雨量站QPE精度。以上不计入“通过”，不启用业务准入。具体模块边界见 README。

## 105 服务器候选回放（2026-09-12）

候选配置已经写入 `/etc/rainpulse/control.env`，五类候选 worker 使用同一镜像和 `qc-flags-v2` 身份。服务为 active，QC 4 个、格点 4 个、拼图 2 个、QPE 2 个、诊断 2 个 worker 均 healthy。部署标记保存在服务器 `runtime/reports/qc-opensource-20260912/stage/INSTALLED`。

按北京时间 08:20、08:25、08:30 串行提交 `forecast_all`，避免同时重算造成内存争用；每次都从 QC 走到诊断和 NowcastInput，结果如下：

| 北京时间 | 目标 run | QC/格点 | 拼图/QPE/诊断 | 总体 | 最新分析配置 | 覆盖率 / 平均 QI |
|---|---|---:|---:|---|---|---:|
| 08:20 | `8562fe7f-9ac7-582c-9fa3-5ac12b13d315` | 11/11 | 3/3/3 | SUCCEEDED | `qc-opensource-mosaic-v1` | 0.4514 / 0.4335 |
| 08:25 | `b77771a0-3490-5058-ae65-3ca41246c74d` | 15/15 | 4/4/4 | SUCCEEDED | `qc-opensource-mosaic-v1` | 0.4489 / 0.4440 |
| 08:30 | `7da8a66c-20c6-530c-80de-dab6081832d7` | 15/15 | 4/4/4 | SUCCEEDED | `qc-opensource-mosaic-v1` | 0.4423 / 0.4080 |

08:15 没有独立的基础 `forecast_runs`，但作为 08:20 的前置分析帧已由同一候选链路生成；工作台接口当前返回分析 `81821688-50c6-5dfe-8142-eac9859e8c72`、配置 `qc-opensource-mosaic-v1`、生成时间 `2026-09-12T05:02:30Z`，不是旧的 `analysis-fallback`。四站均参加，Z9598 平均 QI 为 0.2273。

三次请求共执行 44 个 QC 和 44 个格点任务；任务级平均/最大耗时分别约为 QC 26.8/39.9 秒、格点 25.6/37.2 秒、拼图 2.2/2.4 秒、QPE 0.2 秒、诊断 15.1/15.7 秒。当前瓶颈是逐体扫 QC 和格点写入，4 个并行 worker 已在工作；继续增加并发会先受到每个格点任务约 1 GB 峰值 RSS 和对象存储写入的限制。

### 视觉和算法复核结论

08:15 的 Z9598 原始/质控诊断图显示，候选链路已明显压低南向宽扇形强回波并保留主要回波结构；但东向/东南向径向样式仍有残留。对应 QC 指标为 `qc-opensource-1.0.0`、`qc-flags-v2`，Z9598 平均 QI 0.5239、低质量门 294242、径向干扰射线计数 0。当前默认配置的局部门段 RFI 补充检测是显式关闭的，静态地物杂波和海杂波先验也显示 `skipped`，所以不能把这张图解释成已经完成地形/海杂波/径向干扰的全量识别。

部署启动时由旧结果自动触发的拼图任务曾因旧 `hybrid-scan` 结果与新 `qc-flags-v2` 不一致而拒绝；这批旧任务不是本次回放结果。三次新回放的 QC→格点→拼图→QPE→诊断任务均使用同一候选版本并成功完成。

下一轮应单独建立 `fujian-qc-opensource-rfi-v1` 对照批次，量化径向残留和真实降水误删后再决定是否启用；同时补齐站点静态地物/DEM、海岸和垂直基准资产。没有标签前，不以画面变干净或单站 QI 下降/上升宣称气象效果达标。
