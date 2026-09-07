2. NowcastNet 升级为一等 NATS 算法任务

新增独立控制面：

rainpulse-nowcastnet-coordinator

它负责：

检查最新 RadarAnalysis
→ 每 5 分钟寻找一个可起报周期
→ 精确选择 9 个十分钟输入帧
→ 创建 algorithm_run / job / trace
→ 发布 NATS 请求
→ 跟踪完成或失败事件
→ 更新运行状态

新增正式 Worker Profile：

nowcastnet-shadow

新增事件契约：

forecast.nowcastnet_shadow.requested.v1

新增 PostgreSQL 表：

algorithm_runs

新增只读接口：

GET /api/v1/algorithm-runs
GET /api/v1/algorithm-runs/{run_id}
GET /api/v1/algorithm-runs/{run_id}/assets/{asset_id}

Workspace 现在优先读取正式 Algorithm Run，而不是扫描历史脚本生成的本地目录。旧 NowcastNet 文件产品目录仍暂时保留为迁移回退路径。

NowcastNet 依然保持：

lifecycle=shadow
operational_eligible=false

其输入不合格、GPU 不可用、运行失败或超时，都不会影响 QPE、LK 和 STEPS。

3. NowcastNet 五分钟产品适配

现在明确分离了三个时间概念：

起报频率：5 分钟
模型输入间隔：10 分钟
模型原生输出间隔：10 分钟

例如 10:05 起报时，仍严格选择：

08:45、08:55、09:05、09:15、09:25
09:35、09:45、09:55、10:05

模型原生结果仍为：

+10、+20、+30、……、+120

新增五分钟运动适配器，为以下时效生成派生帧：

+5、+15、+25、……、+115

适配过程不是普通线性平均，而是：

前一原生帧向前光流变形
+
后一原生帧向后光流变形
+
log1p 雨强空间融合

每个帧都明确记录：

{
  "frame_kind": "derived",
  "derivation": "bidirectional-advective-v1",
  "source_leads": [10, 20]
}

原生十分钟帧记录：

{
  "frame_kind": "native"
}

时间轴使用不同点形态区分原生和派生结果。以后做算法检验时，也可以将两类时效分别评分，不会把派生五分钟结果算作 NowcastNet 原生技巧。

4. 固定 Tile Atlas、Halo 和批次推理接口

新增固定福建 Tile Atlas，替代实时场景下每个周期都重新贪心寻找大量不规则小窗口的方式。

当前结构为：

固定输入窗口
  ├── 中心可信区域：进入最终拼接
  └── 四周 Halo：只提供天气系统上下文

主要改进：

Tile 位置和版本固定，结果更容易复现；
输入窗口存在缺测时整块拒绝；
Halo 区域不直接发布，减少窗口边缘伪影；
只对中心可信区域进行加权拼接；
同尺寸 Tile 组成批次；
Atlas 版本进入模型谱系；
继续保留缺测透明区域。

已实现同尺寸批次接口和分组调度。若目标 Official Backend 支持原生批量前向，将直接使用 GPU Batch；兼容环境仍允许逐 Tile 回退。是否真正获得 GPU 批量加速，需要在 105 服务器上记录实际 batch size、显存和吞吐后确认，不能仅凭单元测试宣称性能提升。

5. Workspace PostgreSQL 持久化读模型

新增：

workspace_http_projections

Workspace 周期目录和周期详情现在可以持久化到 PostgreSQL，并支持：

ETag
If-None-Match
304 Not Modified

投影身份包含：

Analysis ID
LK Run ID
STEPS Bundle ID
NowcastNet Algorithm Run ID

任何一种算法发布新版本，都会生成新的详情身份，旧缓存不会继续冒充当前结果。

本阶段采用兼容式持久化读模型：

领域数据
→ Workspace 聚合
→ PostgreSQL 投影
→ React

它已经能够减少服务重启后的重复聚合和多浏览器重复请求。后续可以进一步把“请求触发更新”迁移为“领域事件触发更新”，而不需要修改前端契约。

6. 后台周期数据流 DAG

后台新增当前周期处理链路：

接收
→ 四站解码
→ 四站质控
→ 四站格点
→ 多雷达拼图
→ QPE
→ 诊断
→ 短临输入
→ LK / STEPS / NowcastNet
→ 产品
→ 检验

每个节点可展示：

等待、运行、成功、失败或跳过；
排队耗时；
运行耗时；
Job ID；
配置版本；
算法身份；
失败或不可用原因。

四站阶段会在紧凑模式下聚合显示，例如：

质控 4/4
格点 3/4

鼠标停留后仍可看到节点的详细信息，避免后台重新变成复杂大页面。

7. 重算进度和协作式取消

新增管理接口：

GET  /api/v1/admin/regenerations
POST /api/v1/admin/regenerations/{request_id}/cancel

后台现在可以：

查看最近重算请求；
查看当前阶段；
查看重算原因；
查看失败或取消原因；
取消尚未完成的重算。

取消采用协作式语义：

请求标记为 CANCELLED
→ 不再规划后续阶段
→ 已经开始的原子 Worker 任务允许安全完成
→ 当前可用产品不删除
→ 记录取消时间和原因

这样不会因为强制终止而留下半个 Zarr 工件或破坏当前可用产品。

管理接口仍由 Web Gateway 在服务器端注入管理令牌，浏览器不会持有令牌。

派生产品保留策略

继续沿用上一阶段确定的策略：

默认保留当前成功版本 + 上一个成功版本

适用于：

STEPS 文件产品；
NowcastNet 文件产品；
后续接入保留任务的其他派生产品。

配置为：

RAINPULSE_DERIVED_PRODUCT_RETAIN_VERSIONS=2

需要进一步节省空间时可改为：

RAINPULSE_DERIVED_PRODUCT_RETAIN_VERSIONS=1

以下数据不参与此清理：

原始雷达基数据；
NormalizedRadarVolume；
训练样本；
模型权重；
当前正在运行的任务；
新产品完整发布前的上一版本。