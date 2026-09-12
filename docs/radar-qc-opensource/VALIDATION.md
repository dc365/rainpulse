# 工程验证记录

本次以主线 `5c16866ad99ca47803483ae6fe6cdb0213650833` 为基线，在 Linux 隔离环境、已安装的真实库上执行。未读取两幅截图对应的内网原始雷达体扫。

| 检查 | 实际结果 |
| --- | --- |
| `pytest algorithms/tests configs/tests contracts/tests` | 656 passed，退出码0；18条既有 pySTEPS 数值警告 |
| 新 QC 两个测试文件 | 24 passed，退出码0；包含真实 Py-ART/wradlib、Worker 重试字节一致、字段/掩码/版本、QC→Hybrid→mosaic→QPE合成链路 |
| Go全包 | `scripts/go_control.sh test ./...` 通过 |
| Web | 19个测试文件，73 passed；TypeScript和Vite生产构建通过 |
| Ruff | algorithms、configs/tests、contracts/tests通过 |
| ESLint | 0 errors、3 warnings；均为既有页面 Fast Refresh/Hook依赖警告 |
| Compose候选覆盖 | YAML解析和五类Worker配置一致性通过；未执行内网Docker Compose部署验收 |

Go 使用1.26.8工具链和离线依赖缓存，并沿用仓库缺少私有BDP源码时的公开桥接测试机制。此结果不是私有BDP运行环境/真实PostgreSQL/NATS/MinIO部署证明。Python使用3.13，arm_pyart 2.2.5、wradlib 2.9.5；依赖解析结果保存在 algorithms/uv.lock，未关闭版本检查以通过测试。

第一次全量测试发现冻结身份保护测试引用旧的 planner.go 文件位置，已修正为主线现有目录并保留原断言。最终全量测试结果见上表。新增链路测试也捕获并修复了参数摘要字段名不一致，避免只测独立算法而遗漏下游接入。

尚未执行：独立真实天气标签/多站多过程回放、原生RADVOL SPIKE、内网CPU镜像和端到端部署、真实PPI浏览器视觉验收、站点性能P95/RSS及雨量站QPE精度。以上不计入“通过”，不启用业务准入。具体模块边界见 README。
