# 福建案例整场检验接入

当前检验回放在原有双图、单格点对照上增加整场 CSI、FSS、邻域 CSI、MAE、RMSE。
进入页面或切换原生时效即计算，无需点击地图。阈值和邻域选择直接切换已计算行，
不重复读取数组；重新检验可重试服务失败。沿用现有轻量布局，未增加独立看板。

React → Go `/api/v1/workspace/verification` → 现有 product-builder 内部计算服务。
复用累计服务的校验源读取、单计算锁和有界队列，不运行模型、不占用 GPU、
不建立数据库结果副本。数值定义见 `contracts/data/workspace-verification.md`。
结果缓存按源 URI、SHA256、时效、格网和计算版本隔离，最多 64 项、10 分钟。

事件严格大于阈值。CSI 在共同有效域计算；FSS 与邻域 CSI 仅使用窗口完整有效的中心，
域边界和缺测不补零。邻域 CSI 是双方窗口内有无事件的 CSI，不是逐对象匹配。
公里窗口按区域中纬度换算并显示离散后的实际宽度。无事件、无支持返回不适用。
STEPS 检验每个时效的成员中位数，NowcastNet 检验保留均值；不是集合概率评分。

## 验证与部署

2026-09-10 更新 105 的 api、web、product-builder-worker，未重启 GPU/训练服务。
Go workspace/webgateway 测试通过；Python 新增及累计回归共 14 项通过；
前端整场组件及回放回归 16 项通过，生产构建通过。
Playwright 已在实际页面验证 8.28 16:30、+30 分钟，显示 CSI 0.242、FSS 0.700。

复核命令：

```sh
python3 scripts/check_spatial_verification.py \
  --base-url http://192.168.28.105:4173 \
  --cycle-id ZnV6aG91XzExOF8xMjNfMjVfMjdfMHAwMWRlZ192MXwyMDI2LTA4LTI4VDA4OjMwOjAwWg
```

8.28 16:30，5 mm/h 阈值，10 km 请求窗口：

| 算法 | 时效 | 共同有效覆盖 | CSI | FSS |
|---|---:|---:|---:|---:|
| LK | +30 | 46.2% | 0.242 | 0.700 |
| LK | +110 | 36.6% | 0.079 | 0.246 |
| STEPS P50 | +30 | 34.1% | 0.167 | 0.538 |
| STEPS P50 | +110 | 5.4% | 0.000 | 0.000 |
| NowcastNet 均值 | +30 | 48.2% | 0.204 | 0.601 |
| NowcastNet 均值 | +110 | 40.7% | 0.072 | 0.333 |

算法有效域不同，此表仅作工程证据，不能直接排名或宣称泛化技巧。
雷达 QPE 不是独立雨量站真值。本次是逐所选时效整场检验，不是全部历史批量统计。
PSD、跨过程统计与整条时效曲线尚未接入；后续应单独确定缺测域与频谱窗口策略。
