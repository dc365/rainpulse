# 候选运行与回退

## 本地对照先行

先执行 README 中的对照 CLI；它不发布任何业务对象、不触发队列、不修改准入。没有真实原始体扫时只能跑合成/契约测试，不能用它代替截图对应时次的回放。

## 配套配置必须同时使用

| 环节 | 文件 |
| --- | --- |
| QC | configs/qc/fujian-qc-opensource-v1.yaml |
| 标志 | configs/qc/flag-definitions-v2.yaml |
| Hybrid | configs/gridding/qc-opensource-hybrid-v1.yaml |
| 拼图 | configs/mosaic/qc-opensource-mosaic-v1.yaml |
| QPE | configs/qpe/qc-opensource-zr-v1.yaml |
| 诊断 | configs/diagnostics/qc-opensource-diagnostics-v1.yaml |

局部门段实验只显式更换第一项为 `fujian-qc-opensource-rfi-v1.yaml`，不得与基线参数混拼。不要更改新 profile 的 `operational_eligible` 来强行推进业务；它在本版本中是严格 false。

## 构建

使用完整仓库及锁文件执行既有 `make build-worker-linux` 和 `algorithms/worker.Dockerfile` 构建。Py-ART、wradlib 使用对应 Linux CPU wheel/依赖；既有目标环境对某些气象依赖有特别暂存规则，必须运行镜像内导入测试和 `make test-qc-opensource`。本次已在 Linux Python 3.13 安装后的真实库上运行测试，**未在内网完成 Docker 镜像构建/硬件性能验收**。镜像构建后应记录 digest、依赖清单和镜像内测试结果，勿将工具准备 artifact 当成可部署镜像。

## 接入隔离测试环境

`deploy/docker-compose.qc-opensource.yaml` 仅覆盖五种 CPU Worker 的候选参数/镜像/线程数，不启动新的 Go 服务。原生统一 Go 服务需要由现有配置入口同步设置以下路径（使用主机真实绝对路径，而不是容器 `/opt/rainpulse`）：

```
RAINPULSE_PIPELINE_QC_CONFIG=<repo>/configs/qc/fujian-qc-opensource-v1.yaml
RAINPULSE_PIPELINE_GRID_CONFIG=<repo>/configs/gridding/qc-opensource-hybrid-v1.yaml
RAINPULSE_PIPELINE_MOSAIC_CONFIG=<repo>/configs/mosaic/qc-opensource-mosaic-v1.yaml
RAINPULSE_PIPELINE_QPE_CONFIG=<repo>/configs/qpe/qc-opensource-zr-v1.yaml
RAINPULSE_PIPELINE_DIAGNOSTIC_CONFIG=<repo>/configs/diagnostics/qc-opensource-diagnostics-v1.yaml
```

禁止旧、新 Worker 同时消费 `radar-qc-basic` 的相同 durable 队列。现有队列尚未按引擎拆分，因此必须先停止新任务规划、等待运行中的 QC/grid/analysis 工作排空，再同时切换匹配的规划器、Worker 和配置。不能“滚动替换一个 Worker”让配置不匹配的任务随机失败。

第一次只提交一个已固定输入清单的完整案例，确认每个工件参数 SHA、flags v2、Hybrid/mosaic/QPE/诊断链路一致。再以唯一体扫+上下文+配置身份去重重算，不能仅按文件名或预报周期复用 QC。候选失败时保留旧成功产物，不覆写或删除旧路径；界面应核对真实数据源版本，避免看到的是旧结果回退。

## 真实资料验收仍待执行

必测开发时次：北京时间2026-08-28 08:10、08:15、08:25、08:30，全部相关站点和仰角。独立时间块与开发/上下文隔离，额外补充强对流、层状雨、台风、晴空、海面真降水、融化层/冰雹和缺站缺矩。先导出无候选提示的标注图进行盲标，再评估；不确定和混合区分开统计。不同站的静态先验资产不允许在本版用更改参数然后绕过一致性校验的方式拼接，需后续定义明确的候选配置集合。

需实测 P50/P95、CPU、峰值 RSS、对象读写和排队；1.5倍基线 P95/四实例32GiB是待测目标，不是实测结果。先限一到两个候选任务，避免争抢已有重算 I/O。

## 回退

停止候选规划，等待候选任务边界，切回先前一致的 Go/Worker 镜像及 v3/flags v1/原 Hybrid/mosaic/QPE/诊断配置集，然后恢复规划。仅切换选择和运行参数，不改原始雷达对象，不将 v2 新 bit 解释为旧含义，不覆盖旧结果。不需要为了回退清空数据库、消息队列或历史存储。
