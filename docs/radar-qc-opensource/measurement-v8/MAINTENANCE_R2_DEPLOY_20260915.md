# V7工程维护r2部署

2026-09-15，105四个QC Worker切换到rainpulse-cpu-worker:qc-opensource-7.0.0-r2，
全部healthy。部署前QC队列无待处理任务，Go服务active。

镜像在r1基础上仅替换已验证QC工程模块：最终质量出口、图容量降级、分阶段计时、
纹理复用及其默认配置依赖。pipeline仍为qc-opensource-7.0.0，线上配置仍V7；
天气支撑拆分默认关闭，未启用V8原生实验或训练模型。其他计算Worker保持r1。

新增Dockerfile.qc-maintenance及docker-compose.qc-maintenance.yaml；Compose文件
必须追加在现有V7 override后。回退时在同一Compose命令前设置
RAINPULSE_QC_MAINTENANCE_TAG=qc-opensource-7.0.0-r1，仅更新radar-qc-worker，保留4副本。
旧镜像保留，回退不删除任何资产。

后台脚本顺序重算北京时间2026-08-28 08:45和08:30，覆盖四站。
08:45运行ID：7422e03a-b259-5ab2-bf7f-55df4b475e37。
确认20个radar.qc任务RUNNING后停止监看；不是重算完成或效果验收。
输出runtime/reports/qc-maintenance-r2-20260915，保存build.log、refresh.log、
各次请求回执、before-qc-uris.json（重算前资产地址）。脚本失败即停止下一次提交。

本次交付是主线P1工程改动上线。下一验收：核查任务终态、四站前后资产差异、
实际Worker阶段耗时及是否出现graph_degradation。阶段单样本21%收益不代表整链加速。
静态先验多日数据不足仍为待办，不再增加bRopo阈值支线。
