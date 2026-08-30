# RP-029：Alertmanager 与只读告警中心实施记录

## 1. 目标与边界

本轮在 RP-028 的 Prometheus 规则基础上补齐本地告警管理链路，使值班和研发人员能在 RainPulse Web 内查看规则持续判定、活动告警和通知链路状态。

本轮明确不做：

- 不配置企业微信、钉钉、邮件、短信或通用 Webhook；
- 不开放 Alertmanager 写接口；
- 不在 RainPulse Web 提供确认、静默或删除操作；
- 不把工程默认阈值解释为福建业务 SLA；
- 不因 Compose 服务健康而启用实时 ingest、预报自动发布或概率产品发布。

## 2. 运行链路

```text
PostgreSQL persisted evidence
  -> RainPulse /metrics
  -> Prometheus rule evaluation (pending / firing)
  -> Alertmanager grouping, silence and inhibition
  -> RainPulse read-only alert aggregator
  -> React alert operations workspace
```

Prometheus 是规则状态来源，能提供仍处于 `for` 持续计时阶段的 `pending`；Alertmanager 是分发状态来源，能提供 `silenced` 和 `inhibited`。控制面并发查询两者并按完整标签集归并，不把其中一个上游故障误报为“当前无告警”。

## 3. Alertmanager 配置

- 镜像固定为 `quay.io/prometheus/alertmanager:v0.34.0`；
- 配置和存储均位于 Compose 内部，端口默认只绑定 `127.0.0.1:9093`；
- 活动状态保留 120 小时；
- 按 `alertname`、`severity`、`radar_id` 分组；
- `RainPulseRadarDataMissing` 会抑制同一雷达派生的数据陈旧、完整率、QI 和干扰告警；
- `rainpulse-local` receiver 不包含任何外部通知集成。

雷达规则同步改为保留 `radar_id` 和 `lifecycle` 标签，避免多雷达场景只生成无法定位来源的全局告警。

## 4. 只读 API

新增：

```text
GET /api/v1/alerts
```

响应包含：

- 聚合状态 `ready` / `degraded`；
- Prometheus 和 Alertmanager 各自的可用性；
- `pending`、`firing`、`silenced`、`inhibited` 计数；
- 告警名称、级别、摘要、当前值、标签、开始时间和稳定标识。

任一上游失败时返回 HTTP 200 和 `status=degraded`，保留仍可读取的另一侧证据。两侧都不可用时列表为空但状态仍为 degraded，前端不会显示“当前没有活动告警”。响应使用 `Cache-Control: no-store`。

## 5. Web 告警中心

顶部业务导航新增“告警中心”，支持可分享的 `?view=alerts`：

- 首屏显示两个上游状态和四项状态计数；
- 支持全部、告警中、待生效、已抑制四种本地筛选；
- 每条告警显示严重度、状态、规则名称、摘要、当前值、作用域标签和持续时间；
- 明确显示只读边界，不提供尚未实现的处置动作；
- 桌面采用扁平证据台账，窄屏改为纵向记录，避免横向表格滚动。

## 6. 验证

```bash
make test-alerting
make contracts-check test-go test-web
make lint
pnpm --filter @rainpulse/web build

docker compose --env-file deploy/.env -f deploy/docker-compose.yaml config --quiet
docker compose --env-file deploy/.env -f deploy/docker-compose.yaml exec -T alertmanager \
  amtool check-config /etc/alertmanager/alertmanager.yaml
docker compose --env-file deploy/.env -f deploy/docker-compose.yaml exec -T prometheus \
  promtool check rules /etc/prometheus/rainpulse-alerts.yaml
```

部署后还需确认 Prometheus 到 Alertmanager 的 discovery 状态、`/api/v1/alerts` 双源状态、桌面和 375px 页面无溢出，以及真实 firing 告警能在 Alertmanager 中出现。

## 7. 下一步

在不依赖福建实时雷达的范围内，下一轮补齐失败任务与 outbox 关联追踪、磁盘和对象存储容量指标。外部通知 receiver、责任人、升级路径、静默审批和正式 SLA 必须由业务责任人确认后单独启用。
