# 管理接口 v1.1 补充语义

源定义：`operations-openapi.json`。事件/通用响应 schema_version 仍为 `1.0`，
数据库能力由 `/status.management_schema=2` 标示。所有新增管理路由沿用原认证、
变更准入与错误处理。没有增加未认证的管理动作。

## 新接口

| 方法／相对 `/api/v1/admin/ops` 路径 | 含义 |
|---|---|
| GET /data/scans | 有界注册体扫页；当前最新工作流指针；时间、站点、阶段在LIMIT前过滤 |
| GET /data/summary | 同时间站点概况，包含已知空站；不应用阶段过滤、不推算应有到报次数 |
| GET /data/scans/{id} | 当前指针、自动任务、候选任务、下游体扫引用与历史标记检查 |
| POST /data/scans/{id}/probe | `{stage: normalized\|qc\|grid}`；服务端解析URI，仅检查完成标记 |
| GET /performance | kind/full fingerprint分组的管理尝试统计；成功尝试的nearest-rank分位数 |
| GET /pools | 管理池持久策略、状态数量和心跳视图 |
| POST /pools/{kind}/action | `{action: drain\|resume,expected_revision,reason}`，CAS更新、事务审计 |
| GET /pools/events | 最新50条资源操作记录及before游标 |

数据列表24小时，单页最大100；游标绑定查询窗口、雷达、阶段。
来源各关联列表最多100并声明truncated。性能查询最多7天、10000次，超限422而非抽样。
性能相同身份不保证相同输入/主机/预算，不产生自动优劣结论。

`marker_checked`只验证标记格式/大小/声明摘要约束；它不等于完整资产验证或气象验收。
`unverified`涵盖无法确认的情况，不能断言全部数据缺失。

## 原接口扩展

Task/Attempt增加可空queued_at；WorkerInfo增加pool_mode，Worker注册回执增加
accepting和pool_mode。客户端不得通过注册覆盖持久策略。新客户端在无显式accepting时
停止拉取任务，但执行中的心跳/完成继续调用原端点。

作业/任务events支持 direction、before、after、level、attempt、q；过滤在服务端分页前。
backward在数据库逆序取页，但返回items仍按id升序。next_before是本页最小id；
forward使用next_after。游标不承诺全局跨作业事务快照，端点必须限定单个run/task。
保留已有有限事件存储策略。界面“跟随”刷新最新窗口，不是无损消息订阅。

暂停池不等于取消任务、停机、降低资源配额或暂停实时自动链路；恢复也不绕过身份校验。
策略revision冲突返回409，必须刷新确认，禁止客户端盲重试新revision。
