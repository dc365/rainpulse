# 20260917 增量包集成

基线 3b80351。集成窄射线、多尺度候选、非降水诊断、晴空背景注册表及原始门/拼图权重追溯。

修复新增 review/ 遮蔽原 review.py 的导入冲突，新包统一使用 review_extension；保留旧回放接口。新增审计配置，保持现有 7.3.6 动作，新增分支仅输出诊断。没有真实晴空背景资产，不能声称固定地物隔离或效果验收完成。

验证：独立基线工作树运行新增模块、既有 QC/网格/拼图/宽扇区/近距扇区测试；追加审计配置 QC→Grid→Mosaic→QPE 集成用例及审计动作一致性与 Zarr 序列化用例。

部署在现有 Compose 覆盖文件之后追加 deploy/docker-compose.qc-review-20260917.yaml，控制面 RAINPULSE_RADAR_QC_CONFIG 指向 fujian-qc-review-20260917-audit.yaml。实验隔离需后续实况对照，不在本次快速部署中启用。
