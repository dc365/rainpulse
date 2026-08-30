# RP-032 NowcastNet 空间地图包实施记录

状态：完成；MRMS 完整回算、地图包发布和控制面接入均已验收

## 1. 目标

RP-032 为 RP-026 冻结的 NowcastNet/STEPS 离线验证增加空间证据，使算法人员不仅能看
CRPS、Brier Score 和覆盖率汇总，也能在同一 EPSG:4326 GIS 视野中检查雨区位置、形态
和随预报时效的演变。

该任务不修改 RP-026 的独立留出结论，不重开模型选择，不拟合任何参数，也不启用实时
影子链、概率发布或福建业务资格。

## 2. 地图包内容

每个案例、每个起报生成一个不可变地图包；每个 +10 至 +120 分钟时效包含：

- MRMS 实况雨强；
- NowcastNet 4 成员集合平均雨强；
- STEPS 12 成员集合平均雨强；
- LK 确定性结果；
- 持续性结果；
- 独立相位相关平移结果。

六类图层共用雨强色标、像素边界和地理范围。集合均值只在全部冻结成员均有效的位置
有效；透明像素表示缺测或无覆盖，不表示无雨。地图包保存逐图层 SHA-256、大小、网格
维度和有效/无雨/有雨/缺测计数。

本阶段没有把集合均值冒充阈值概率图。超阈值概率仍由 RP-026 指标汇总表达；如后续
增加概率空间层，必须使用独立的 0–100% 色标和单位契约。

## 3. 控制面与 Web

Go 验证目录现在允许 `probabilistic_ensemble` 报告携带地图索引，并从索引安全构造案例
和起报时间选择器。以下条件任一不满足时均拒绝地图证据：

- 汇总、索引的 profile、renderer、bundle/layer 计数一致；
- 每个 issue identity、UTC 时间和目录键一致；
- manifest 只引用受控 PNG，相符的 SHA-256、大小和维度；
- `operational_eligible=false` 且产品发布仍关闭。

算法验证页为带地图的概率运行显示三幅联动 GIS：MRMS 实况、NowcastNet 集合均值、
STEPS 集合均值。三幅地图共享缩放和平移状态，支持参考底图、格点/平滑显示、透明度、
案例、起报和时效播放。移动端通过页签保留同一信息结构。

## 4. 可复现命令

```bash
bash tests/rp032_probabilistic_map_test.sh
uv run --project algorithms pytest -q \
  algorithms/tests/test_algorithm_verification_map.py \
  algorithms/tests/test_mrms_nowcastnet_hindcast.py
go test ./services/control/internal/verification ./services/control/internal/api
pnpm --filter @rainpulse/web test
pnpm --filter @rainpulse/web lint
pnpm --filter @rainpulse/web build
```

105 上的冻结回算使用新的 run ID，避免修改既有 `holdout-v1`：

```bash
uv run --project algorithms python -m \
  rainpulse_algo.verification.mrms_nowcastnet_hindcast hindcast \
  --profile configs/verification/rp026-mrms-nowcastnet-v1.yaml \
  --split holdout \
  --run-id holdout-map-v1 \
  --device cuda:0 \
  --root <MRMS_ARCHIVE_ROOT> \
  --capsule-root <NOWCASTNET_CAPSULE_ROOT>
```

完整回算通过后，使用只读地图运行发布脚本把三级运行目录原子发布到控制面要求的两级
目录；脚本会先核对离线门禁、索引、每个 manifest 以及全部 PNG 的大小和 SHA-256，且
拒绝覆盖同名目标：

```bash
scripts/stage_probabilistic_verification_map_run.sh \
  <FULL_HINDCAST_RUN> \
  <CONTROL_PLANE_REPORT_ROOT> \
  holdout-map-v1
```

## 5. 验收边界

- RP-026 原始 `holdout-v1` 保持不变；
- 新回算只增加可视化空间证据，不形成新的技巧声明；
- Web 必须明确标注“集合均值，不是阈值概率图”；
- MRMS 结果不能替代福建真实极坐标质控、QI 拼图、QPE 和业务实况检验；
- 福建数据到位前，NowcastNet 继续是离线候选，STEPS 继续是主概率基线。

## 6. 105 完整回算证据

2026-08-30 在 105 的 RTX 6000D 上执行冻结留出集，新运行 `holdout-map-v1` 完成：

- 50/50 个起报完成、0 失败；
- 50 个不可变地图包、3,600 个 PNG 图层；
- 15,000 行概率评分、150,000 条可靠性分箱记录；
- 地图渲染耗时 P50/P95/最大为 `0.841/1.069/1.316 s`；
- 完整单起报耗时 P50/P95/最大为 `31.411/42.218/47.282 s`；
- 控制面发布目录大小约 56 MiB；
- `operational_eligible=false`、`product_publication_enabled=false`。

新运行与原始 `holdout-v1` 的配置哈希、样本选择证据、评分行数、覆盖率行数、可靠性
行数、近远时效汇总和分类汇总逐项一致，说明增加地图渲染没有改变冻结验证结论。
发布脚本随后核验全部图层大小和 SHA-256，并原子发布到：

`/data/Weather/RADA/RADA_MRMS/reports/rp026-mrms-nowcastnet-v1/holdout-map-v1`

GPU 窗口结束后，8004 上的 Qwen3.8 已按原参数恢复，`/health` 返回 `ok`。
