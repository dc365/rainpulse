# 单进程部署约定

目标：单个 Go 进程负责 Web/API、雷达接入和任务编排；Python 保持独立常驻计算，CPU Workers 共用镜像。保持既有数据、队列、产品协议与管理接口白名单，不增加产品版本。

统一 Go 入口位于 services/control/main.go。平台启动一次；后台模块使用可取消上下文，不在可复用运行函数中退出进程。旧 cmd 入口暂作迁移回退，不复制业务实现。

验收：Go 单测与竞态检查、同进程 HTTP 管理接口白名单、SSE、累计与检验；接入目录缺失不得退出 Web；后台重试必须先结束上一轮，不得重复创建规划循环。切换前停止旧接入和编排；不删除数据库、对象或旧容器，回退仅切换程序。

实施期间仅通过验收的阶段可以部署。镜像合并与单进程迁移可分别完成，未验证的入口不能替换现网。

## 已实施及105验证

- 统一入口 `services/control/main.go`；`apiapp`、`ingestapp`、`controlplane` 共用实现，旧 cmd 仅保留回退包装。前端不改接口。
- `rainpulse.service` 已运行，版本 `unified-20260911`；4173提供Web与受限API，127.0.0.1:8080保留GPU脚本兼容。后台健康8090/8092仅回环绑定。
- 11个CPU容器共享 `rainpulse-cpu-worker:latest`，均健康；PostgreSQL/NATS/MinIO保持原容器与原卷。模拟和监控已停止，不触碰GPU训练服务。
- 旧4个Go容器停止保留，旧镜像标签未清理，因此 `docker images` 仍能看到旧名称。当前活跃业务仅使用四种镜像；离线包增加MinIO客户端，共五种。
- Go全量单测、相关竞态检查、go vet、BDP集成单测及Linux构建通过。依赖准备脚本适配新版公共库的本地RuiyunDB SDK，并统一genproto拆分模块版本；未修改公共库源码。
- 真实8/28 16:30复验：四算法×四累计区间通过（保留原缺测）；PSD共同有效34,362格，64×64分析区；双时效批量检验保存完成。服务重启后版本/接口正常。
- 迁移曾因systemd组解析、挂载语法失败，自动回退已实际执行；修正后再次切换成功。回退不删数据。

## 运维

日常：`sudo systemctl restart rainpulse`；状态：`systemctl status rainpulse`；日志：`journalctl -u rainpulse`。

首次迁移在项目根执行：

1. `bash scripts/build_realtime_shadow.sh --unified`（有BDP源码的构建机）。
2. `sudo docker build -f algorithms/worker.Dockerfile -t rainpulse-cpu-worker:latest .`。
3. `sudo python3 scripts/configure_unified.py --root "$PWD" --user yons`，账户按目标机已有普通账户设置。
4. `sudo bash scripts/switch_unified.sh`；失败自动停新服务并恢复存在的旧容器。

配置准备从现有Compose配置推导宿主机环境，敏感值仅写 `/etc/rainpulse/control.env`（0600）；不要手工维护第二份密码。修改原部署配置后重新运行准备脚本。利用systemd私有挂载映射原容器路径，不复制产品。保持UID，使用65532数据组兼容旧容器的目录访问权限。

手动回退：`sudo bash scripts/switch_unified.sh --rollback`。仅适用于保留旧容器的迁移机；全新离线安装没有旧程序，失败时停止服务并保留数据供排查。

## 离线包

现有工具新增 `--unified`，包含统一Go二进制、前端静态文件、部署配置、安装脚本及五种镜像，不包含凭据、历史案例、原始雷达或模型权重。继续单独打包历史案例。

`bash scripts/package_airgap_deploy.sh --unified --output /绝对路径/rainpulse-unified.zip`

在目标BDP项目目录解压并准备该环境的配置，然后执行 `./install.sh --mode unified --service-user <普通账户>`。更新已有环境需增加 `--replace-existing`。依赖为Linux/systemd、Docker Compose（支持 !override）、Python3标准库和现有中间件配置；内网不需要Go/Node编译器或BDP源码。

105已经生成精简包；全新内网机器的安装验收尚未执行。迁移不会自动删除旧镜像，待用户验收后再定向清理。
