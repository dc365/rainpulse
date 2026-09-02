# RainPulse NowcastNet 训练文档

更新日期：2026-09-03

当前状态：演化网络 300,000 步训练与最终检查点验收完成；生成阶段正式长训练尚未批准

唯一执行方案：[TRAINING_PLAN_v1.0.md](TRAINING_PLAN_v1.0.md)

夜间运行手册：[RUNBOOK_NIGHTLY.md](RUNBOOK_NIGHTLY.md)

阶段状态清单：[STATUS.md](STATUS.md)

首轮 MRMS 资产审计证据：[`../../configs/training/evidence/nowcastnet-mrms-audit-manifest-v1.json`](../../configs/training/evidence/nowcastnet-mrms-audit-manifest-v1.json)

## 1. 为什么单独建目录

本目录是 RainPulse 自训练 NowcastNet 的唯一入口，不使用 RP 编号。

RP 系列记录 v1.1 工程主链的实现和验收，包括雷达基数据解码、极坐标质控、Hybrid Scan、多雷达 QI 拼图、QPE、NowcastInput、pySTEPS 和产品服务。模型训练是可暂停、可失败、必须离线验收的研发支线。如果继续沿用 RP 编号，容易把“训练完成”“MRMS 上有效”和“福建业务可用”误认为同一件事。

两条路线的关系固定如下：

```text
v1.1 业务主链
原始雷达 → 极坐标质控 → Hybrid Scan → QI 拼图 → QPE → NowcastInput → STEPS/LK
                                                                  ↓
                                                  福建训练样本与独立验收样本
                                                                  ↓
NowcastNet 训练支线
MRMS 基础模型 → 冻结父权重 → 福建微调子模型 → 离线验收 → 影子运行 → 人工放行
```

任何 NowcastNet 结果都不能绕过质控和 QPE，也不能因为训练任务完成而自动进入业务发布。

## 2. 文档职责

| 文档 | 职责 | 变更规则 |
|---|---|---|
| `TRAINING_PLAN_v1.0.md` | 冻结目标、数据切分、训练协议、制品谱系和验收门槛 | 改变关键决策时升版本，不直接覆盖历史版本 |
| `RUNBOOK_NIGHTLY.md` | 规定夜间训练、暂停、恢复、检查点和共享 GPU 恢复流程 | 操作方式改变时同步更新 |
| `STATUS.md` | 记录当前阶段、完成证据、下一动作和阻塞项 | 每完成一个可验收步骤后更新 |
| RP-026 文档 | 官方权重适配和既有 MRMS 离线对比证据 | 只作历史输入，不在这里继续追加自训练进度 |

## 3. 已冻结的核心决策

1. 先用 MRMS 训练 `RainPulse-NowcastNet-MRMS-v1`，使用随机初始化，形成 RainPulse 自有训练权重。
2. 福建连续真实资料到位后，必须先经过 v1.1 的极坐标质控、拼图和 QPE，再微调得到 `RainPulse-NowcastNet-Fujian-v1`。
3. 福建模型是 MRMS 模型的子制品，不能覆盖父权重。
4. 正式产品网格保持 EPSG:4326、`0.01° × 0.01°`。`0.02°` 协议只用于训练器基线对照，不代表 RainPulse 最终产品分辨率；其样本必须从 0.01°、512 × 512 原生窗口降尺度为 256 × 256，不能直接缩小现有 0.01°、256 × 256 试制块。
5. 训练采用 9 帧输入、20 帧目标、10 分钟间隔和 256 × 256 训练裁剪，雨强上限为 128 mm/h。
6. MRMS 训练集覆盖 2019 至 2023 年；开发集和独立留出集使用尚未被 RP 验证消耗的 2024、2025 月份。
7. 允许每天 20:00 至次日 08:00 分段训练，但必须支持完整、可验证的断点续训。
8. 自训练模型必须同时优于官方权重和 STEPS 的冻结基线，才能进入福建影子验证；训练结束本身不是发布条件。

## 4. 防走偏规则

- 只从本 README 进入训练工作，不从某个 RP 文档临时延伸训练任务。
- 开始每个阶段前检查 `STATUS.md` 的准入条件，结束后补齐证据和哈希。
- 未通过 1,000 步 GPU 冒烟前，不启动完整训练。
- 未冻结开发门槛前，不读取独立留出预报结果。
- 不把缺测填成无雨，不在训练加载时反复解压原始 GRIB，不按年份依次训练。
- 不把官方 0.02° 权重直接当作 0.01° 模型继续训练。
- 不在仓库中保存原始数据、训练样本、权重、检查点、服务器地址、账号、密码或服务控制信息。
- 训练失败时保留 STEPS 和官方 NowcastNet 离线基线，不改变现有业务链路。

## 5. 下一动作

第二个正式窗口从第 243,046 步恢复到第 300,000 步，完成剩余 56,954 步。累计 300,000 行指标连续且全部有限，最终检查点通过完整 SHA-256、状态指纹、随机状态和 CPU 回读验收；nightly report、Qwen3.8 恢复及 Qwen3.6 健康检查均通过。两个 timer 和一次性许可均已关闭，独立留出继续保持 0。

下一门槛不是直接启动生成阶段：先把第 300,000 步检查点晋级为冻结演化父权重，完成生成阶段正式资源协议、恢复预检和短程导入验证。生成阶段批量 16 的既有峰值保留显存约 39.21 GB，当前共享 GPU 余量不足以安全长训；在批准额外资源窗口或验证等价的微批次/梯度累积方案前，不启动 500,000 步正式训练。

## 6. 重现入口

以下路径均由部署环境注入，不写入仓库。0.02° 对照集只需制作一次，完成标记和样本索引哈希不一致时加载器会拒绝训练。

```bash
cd "$RAINPULSE_REPOSITORY_ROOT"
PYTHONPATH=algorithms "$RAINPULSE_TRAIN_PYTHON" \
  -m rainpulse_algo.training.conformance \
  --run-profile configs/training/nowcastnet-mrms-run-v1.yaml \
  --pilot-profile configs/training/nowcastnet-mrms-pilot-v1.yaml \
  --repository-root . \
  --pilot-plan "$RAINPULSE_MRMS_PILOT_PLAN" \
  --audit-root "$RAINPULSE_MRMS_AUDIT_ROOT" \
  --dataset-root "$RAINPULSE_MRMS_ARCHIVE_ROOT" \
  --output-root "$RAINPULSE_MRMS_CONFORMANCE_ROOT" \
  --workers 4
```

单步 GPU 更新和检查点回读使用同一个入口，通过 `--track` 明确区分两种空间协议：

```bash
cd "$RAINPULSE_REPOSITORY_ROOT"
PYTHONPATH=algorithms "$RAINPULSE_TRAIN_PYTHON" \
  -m rainpulse_algo.training.evolution_smoke \
  --profile configs/training/nowcastnet-mrms-run-v1.yaml \
  --repository-root . \
  --data-root "$RAINPULSE_MRMS_PILOT_ROOT" \
  --output-dir "$RAINPULSE_TRAIN_RUN_ROOT/evolution-smoke" \
  --track foundation_0p01 \
  --device cuda \
  --batch-size 16 \
  --precision bf16
```
