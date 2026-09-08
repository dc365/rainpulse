# RainPulse 上线前收敛：实现与 Codex 复审交接

基线：`3569b4c8e0fccad4085d80dcd34dcebdcdcd2210`。本批修改以此前审查中明确的软件问题为主；不等于所有产品规划和气象效果验收已经完成。没有执行部署、历史回算或业务准入晋级。

## 已调整的软件路径

| 范围 | 实现 | 复核入口 |
|---|---|---|
| 冻结实验 | 恢复 RP-026 原始权重 URI 与原 SHA-256；部署位置由显式 capsule_root 绑定，保留源文件及权重摘要验证 | `test_frozen_profile_integrity.py`、`test_mrms_nowcastnet_hindcast.py` |
| LK 缺测插值 | 新增 `full_kernel_support_v2`；雨强和支持使用同一插值核，部分支持结果为缺测而非被零值稀释 | `test_advection_support.py` |
| STEPS 成员支持 | 新增 `native_nan_member_support_v2`；保留 NaN 进入冻结后端，由后端在实际成员扰动轨迹上携带缺测域 | `test_steps_native_support.py` |
| 版本与运行入口 | 新 LK 配置与默认 Worker/Planner 路径；控制面接受新版本及显式旧版回放 | `prelaunch-pysteps-lk-v2.yaml`、`events.go` |
| 历史集合重生成 | 新 STEPS v6 配置；旧冻结配置不覆盖 | `prelaunch-pysteps-steps-v6.yaml`、`regenerate_forecasts.sh` |
| 周期一致性 | 请求周期与已显示快照分开；失败或乱序响应不混用页头、时间和数据 | `workspaceState.ts`、`workspaceState.test.ts` |
| 实时恢复 | 用户跟随意图独立于新鲜度；断流不退出跟随，恢复后继续；空目录可重试 | `useWorkspaceData.ts` |
| 事件与缓存 | 每个 API 进程一个有界 SSE 目录生产者、心跳、稳定 revision；不再每个浏览器强制绕过缓存；禁止把内部陈旧结果重新包装成新缓存 | `events.go`、`events_test.go`、`cache_test.go` |
| 地图查询 | 删除 OpenLayers 原型改写及 DOM 反推；组件回调携带明确资源/时刻；点值读数值资产，区分缺测与查询失败 | `RasterGISMap.tsx`、`sample.ts` |
| 检验工作流 | 独立的实况/指定模型双图；原生时效匹配、同格点值与差值、严格超阈值事件判断；不冒充全场技巧评分 | `VerificationInspector.tsx`、`verification.test.ts` |
| 界面 | 保留四图默认与显式单图；可变高度状态区域；改善重要文字和图例字号、派生/未校准标记、减少动态效果 | `workspace.css`、`.interface-design/system.md` |
| CI | Test、Lint、Build、新回归独立执行；保留统一 verify 门禁，不能因前一个检查失败而失去其他检查结果 | `.github/workflows/ci.yaml`、`make test-prelaunch` |

## 数值边界

LK 新策略只支持非负权重的 0/1 阶插值。临时数值副本允许填零，但只有支持权重接近完整的结果可以发布。`[10,10,missing,missing]` 的四分之一格点反例不再发布 `7.5`；新策略输出缺测。既有 RP 配置的 legacy 策略保留用于复现实验，并非新的默认业务策略。

STEPS 使用的是库内原生 NaN/domain-mask 机制，不复刻库内随机数，也不把确定性掩膜复制到所有成员后就宣称成员级支持。最终成员掩膜仍与确定性支持及有限值取交集；最小有效成员数规则仍有效。这会改变缺测区附近的支持范围，必须重做工程回放和独立概率检验。原始集合频率仍未校准。

## 合并与部署前必须检查

先运行 `make bootstrap`、`make contracts-check`、`make test-prelaunch`、`make test`、`make lint`、`make build`。使用最终代码重新运行，不能使用早于最后修改的日志充当最终验证。

确认 `RAINPULSE_PIPELINE_PYSTEPS_CONFIG` 与 `RAINPULSE_PYSTEPS_LK_CONFIG` 的实际部署覆盖值一致，均指向新 LK v2 配置。新控制面、Worker 和配置应作为同一版本部署。新任务发送到 `rainpulse.jobs.requested.pysteps_lk.v2`，默认 Worker profile 为 `pysteps-lk-v2`，使用独立 durable `rainpulse-pysteps-lk-2-0-0`。旧 `pysteps-lk` Worker profile 保留原 subject/durable，仅挂载旧配置来排空旧队列或执行显式旧版本回放。不要把 v2 配置挂到旧 Worker profile；迁移时保持旧 Worker 到旧队列排空，新 Worker 不会消费旧任务。旧原始资料、历史工件和冻结实验不覆盖。

对于地图应复核请求失败、快速 A→B→C 切换、断流后恢复、SSE 重连、图层加载失败、原生/派生帧及 1366×768 / 1920×1080 / 390px 三种尺寸。截图测试使用的合成资料不能作为真实回波效果证据。

## 本包没有完成、也没有擅自启用的内容

真实标签、多天气过程、独立雨量站、温度/融化层和相位资料仍需按既有准入规则冻结。没有凭合成回归打开 QC、QPE、STEPS 或 NowcastNet 的业务门槛。

没有把计算网格直接改成米制网格，没有启用新的多雷达平流时次校正，也没有通过扩大数组假装获得新的上游观测覆盖。这些属于需要独立对照实验的算法变更。

当前新增检验是同格点 N=1 的数值对照，尚不是完整区域时序或全场误差图产品。缺测与低概率的文字及点值语义已分开；完整空间缺测纹理仍需独立有效掩膜资产支持，不能从透明像素猜测。

共享 SSE 生产者解决了按浏览器重复强制重建目录的问题，但仍使用共享缓存轮询；没有声称完成了数据库提交事件直接驱动物化视图的全部改造。大范围旧 CSS 清理和所有临时后端存储兼容路径也没有在本批盲目删除。

## 验证记录

交付包 `validation/results.json` 为实际命令及退出码，日志与可能生成的合成界面截图在同目录。任何失败、超时或未执行检查都必须在 Codex 复审时处理，不能按“已提供测试代码”推断“全部通过”。完整 Go 工程和内网真实数据链路应在项目规定的 Go/Python/Node 环境复验。
