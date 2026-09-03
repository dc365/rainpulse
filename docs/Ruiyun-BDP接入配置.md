# RainPulse 接入 Ruiyun BDP

RainPulse 的平台程序编码为 `bdp-dp-rada-rainpulse`，程序配置编码默认同为
`bdp-dp-rada-rainpulse`。公共代码读取时会自动补齐平台前缀，配置中心中的完整编码为：

`bdp_pm_config_common_program_bdp-dp-rada-rainpulse`

可直接以 [平台配置示例](../configs/platform/bdp-dp-rada-rainpulse.json) 的内容创建或更新
ProgramConfig。配置只保存非敏感、经常调整的运行参数；数据库密码、对象存储密钥、NATS
连接串和管理令牌仍由部署环境安全注入。

## 雷达原始数据

`radar_input.data_code` 默认是 `RADA_L2_FMT`。程序通过 Ruiyun BDP 公共代码依次读取：

1. 原始数据元数据的 `dataSourceInfos[source_index]`；
2. 该数据源关联的文件服务凭据；
3. 文件服务根目录和元数据目录模板中首个时间变量之前的静态目录。

当前平台记录会解析为 `/data/Weather/RADA/RADA_L2_FMT/OBS_TEMP`。BDP 可用时，该路径会
覆盖 ingest 清单中四个雷达源的兼容路径，也会覆盖 orchestrator 的旧单源入口路径；路径不在
ProgramConfig 中重复维护。专用 ingest 的扫描间隔、文件稳定等待时间和回看时长分别由
`scan_interval_seconds`、`minimum_file_age_seconds`、`lookback_hours` 管理。

## 运行模式

`RAINPULSE_BDP_MODE` 支持：

- `prefer`：默认值。配置中心可用时读取平台配置和元数据；平台尚未配置时保留现有部署参数。
- `required`：平台配置或元数据不可用时拒绝启动，适合完成平台配置后的正式环境。
- `off`：完全使用现有环境变量和本地 ingest 清单。

`RAINPULSE_BDP_CONFIG_CODE` 可覆盖配置编码，留空时使用程序运行名，最终回退到
`bdp-dp-rada-rainpulse`。105 部署应把
`/home/yons/hwapp/ruiyun-bdp/conf` 只读挂载到容器 `/ruiyun-bdp/conf`。

## Go 构建

RainPulse 使用 Ruiyun BDP 的 `bdp-publiccode-common`、`bdp-publiccode-puremanage` 和
`hw-common`，不复制平台客户端。执行 `make prepare-bdp-go` 会从仓库祖先目录自动定位公共
源码并生成忽略于 Git 的临时 modfile；也可以用 `RUIYUN_BDP_ROOT` 和 `RUIYUN_GO_ROOT`
明确指定源码根目录。常规的 `make test-go`、`make lint`、`make build` 和实时影子构建脚本
都会自动执行这一步，并以 `ruiyun_bdp` 标签编译真实平台适配。

GitHub 公共 Runner 无法访问内网公共源码，因此找不到 BDP 源码时只编译兼容实现，以便继续
验证与平台无关的代码。`make build-linux` 和 `scripts/build_realtime_shadow.sh` 会强制要求
BDP 源码存在，正式部署不会静默生成兼容版二进制。
