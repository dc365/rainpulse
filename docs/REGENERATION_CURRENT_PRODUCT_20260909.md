# NowcastNet 重算与当前产品

- 后台支持 NowcastNet 单独重算：复用已有 QPE，不重建雷达、QPE、LK，也不增加基础 forecast_run。
- 每次请求返回独立 regeneration_job_id；面板跟踪该任务状态、配置版本及工作台是否切换到该任务结果。受理不等于完成。
- 同周期同算法正在运行时拒绝重复任务。新结果成功后替换当前产品；失败保留原产品。
- NowcastNet 历史大文件在新结果完成至少一分钟、且没有同周期运行任务后清理；保留数据库运行记录。删除范围严格限定到该旧产品目录，不涉及原始数据或 QPE。
- 本次没有启用实验性拼接参数，不能把重算成功理解为分块问题已解决。

## 105 验证

- API、orchestrator、Web 已重新编译部署。
- 2026-08-28 16:30（北京时间）单独重算：任务 `4e194d8f-3745-55b7-a865-a36b0ef5484b`，状态 SUCCEEDED。
- 工作台 cycle 接口的 nowcastnet_bundle_id 已切换到同一任务 ID。
- GPU worker 日志确认 publication_reused=false，产生 26 个对象。
- orchestrator 已连续记录旧 NowcastNet 产品清理成功，每批 10 个。
- Go 相关包、前端重算测试、前端构建和基础设施脚本检查通过。浏览器自动化连接超时，未宣称完成浏览器点击验收。
