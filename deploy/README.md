# 部署配置

日常只维护 `deploy/.env`。从 `.env.example` 复制后填写凭据、BDP 配置目录与雷达挂载目录，其余先用默认值。不要把算法 YAML 的参数复制到 `.env`。

## 配置职责

| 位置 | 管什么 |
| --- | --- |
| `deploy/.env` | 端口、凭据、宿主机挂载目录、BDP 连接方式 |
| Ruiyun BDP 配置 `bdp-dp-rada-rainpulse` | Go 程序业务参数；模板见 `configs/platform/bdp-dp-rada-rainpulse.json` |
| BDP 原始数据元数据 `RADA_L2_FMT` | 实际雷达采集目录 |
| `configs/` 下版本化 YAML | 算法阈值、网格、模型、质控与验证配置 |
| Compose | 容器地址、挂载目标、服务依赖和缺省值 |

`.env` 是 Compose 的变量替换输入，不会自动注入所有容器。只有 Compose 中 `${变量名}` 引用的值才会被读取。过去示例中的很多 `RAINPULSE_PIPELINE_*_CONFIG`、服务 URL 和固定路径在 Compose 中没有对应替换点，填写它们不会覆盖固定值，现已从示例移除。

Go 启动时 BDP common/component 配置会覆盖相应进程环境变量。Python Worker 不直接读取 BDP；更改算法 profile 时必须同时更新编排器与对应 Worker，不能只改配置中心的一端。当前实时覆盖层使用 `fujian-qc-evidence-v2`，C1/C2 物理订正仍按数据门控启用。

## 路径只有两类

- 宿主机部署根：105 为 `/home/yons/hwapp/ruiyun-bdp/bdp-dp/bdp-dp-rada/bdp-dp-rada-rainpulse`。Compose 的 `../configs`、`../runtime` 都相对第一个 Compose 文件所在的 `deploy/`，因此自然落在本项目内。
- 容器路径：`/opt/rainpulse` 是 Python 镜像内部代码/配置目录；`/ruiyun-bdp/conf` 是 BDP 配置挂载目标；`/var/lib/rainpulse` 是容器数据入口。它们不是服务器上额外安装的项目，不需要在宿主机创建或迁移。镜像、配置清单和容器挂载约定继续保持一致。

BDP 标准目录层级下，`RAINPULSE_BDP_CONF_HOST_ROOT=../../../conf` 即指向 `/home/yons/hwapp/ruiyun-bdp/conf`；非标准部署目录填写绝对路径。目录内须有 `configcenter-client.conf`。

`RAINPULSE_RADAR_DATA_ROOT` 使用绝对路径，同路径只读挂载给编排器和接入服务，必须包含 BDP 元数据最终解析出来的目录。Docker 在进程读取 BDP 前就要建立挂载，因此仍需要这个部署参数。路径不存在会直接报错，不会自动创建一个空目录后表现成“无数据”。旧 `FMT_L2_Z959X_SBD` 挂载已移除。

`required` 模式下 BDP 配置/元数据不可用会报错；`prefer` 允许回退至仓库 manifest，`off` 用于无 BDP 的独立部署。后两种模式使用 `configs/ingest/fujian-realtime-shadow-v1.json` 中的 `arrival_root`，如更换目录须同步该 fallback manifest，不能把挂载边界当作采集路径配置。

## 启动

在项目根执行：

```bash
cp deploy/.env.example deploy/.env
# 编辑 deploy/.env，填写凭据及目录
docker compose --env-file deploy/.env -f deploy/docker-compose.yaml -f deploy/docker-compose.realtime-shadow.yaml config --quiet
docker compose --env-file deploy/.env -f deploy/docker-compose.yaml -f deploy/docker-compose.realtime-shadow.yaml up -d --wait
```

只加载基础 Compose 用于基础服务/开发；福建四雷达链路必须加实时覆盖层。离线包使用包内 `install.sh --mode realtime-shadow`。不要通过公开输出 `compose config` 来检查配置，其中可能包含凭据；用 `config --quiet`。

已有部署保留原 `.env` 中的凭据与实际路径，不要直接用空白示例覆盖。迁移时移除过期单站变量，将数据挂载改为 `RAINPULSE_RADAR_DATA_ROOT`。新的示例采用 BDP `required`；已有 `.env` 不会被自动改模式。

## 按需配置

这些参数有默认值，仅确实需要时追加到 `.env`，不另建第二份必填配置：

| 参数 | 默认/用途 |
| --- | --- |
| `RAINPULSE_BIND_ADDRESS` | `0.0.0.0`，Web 监听地址 |
| `RAINPULSE_API_BIND_ADDRESS` / `RAINPULSE_API_PORT` | `127.0.0.1` / `8080` |
| `RAINPULSE_INFRA_BIND_ADDRESS` | `127.0.0.1`，数据库、消息队列、对象存储与监控 |
| `RAINPULSE_BDP_MODE` | 示例为 `required`，未设置时 Compose 兼容默认 `prefer` |
| `RAINPULSE_ALGORITHM_VERIFICATION_HOST_ROOT` | `../runtime/reports/mrms` |
| `RAINPULSE_ENSEMBLE_PRODUCT_HOST_ROOT` | `../runtime/products/ensemble` |
| `RAINPULSE_NOWCASTNET_PRODUCT_HOST_ROOT` | `../runtime/products/nowcastnet` |
| `RAINPULSE_ANCILLARY_ROOT` | `../runtime/ancillary/assets` |
| `RAINPULSE_PIPELINE_NOWCASTNET_SHADOW_ENABLED` | `false`，GPU Worker 和权重就绪后开启 |
| `RAINPULSE_MAX_INPUT_ARTIFACT_BYTES` | `2147483648` |
| `RAINPULSE_WORKSPACE_CACHE_*` | 已有合理默认值，保留 Compose 中的可选调优入口 |

派生产物版本保留策略由生成程序控制，默认保留一版。旧示例 `RAINPULSE_DERIVED_PRODUCT_KEEP_VERSIONS` 不是 Compose 服务的全局清理开关，删除该示例项不会删除数据。

C1/C2 诊断影子仅在具备数据、完成验证后追加以下配置；默认均为空：

```dotenv
RAINPULSE_RADAR_PHASE_PROCESSING_PROFILE=/opt/rainpulse/configs/verification/fujian-phidp-kdp-shadow-v1.yaml
RAINPULSE_RADAR_ATTENUATION_PROFILE=/opt/rainpulse/configs/verification/fujian-kdp-attenuation-shadow-v1.yaml
```

静态资料、权重和结果目录均可放在项目 `runtime/` 下；数据库和对象存储的既有 Docker named volumes 不随本次配置精简迁移或删除。

## 运维入口

数据库迁移按文件名顺序运行并记录于 `schema_migrations`。`make dev-down` 停容器但保留卷；普通升级不要使用 `down --volumes`。部署验证可用 `make infrastructure-smoke`、`make control-plane-smoke` 和 `make smoke`。

Prometheus、Alertmanager 默认只对本机开放；Web 经 API 查看告警与 `/api/v1/operations/issues`。node-exporter 不发布宿主机端口。算法报告目录的结构仍是 `{profile_version}/{run_id}/{summary.json,metrics.csv}`，无报告时界面显示空状态。

离线镜像打包与加载见 `docs/内网离线部署.md`。NowcastNet 的独立 GPU 服务部署文件见 `deploy/systemd/`；权重和 CUDA 环境按对应启动脚本配置，不能把 Python 容器内的 `/opt/rainpulse` 当作宿主机权重位置。
