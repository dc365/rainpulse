# RP-026：NowcastNet 离线适配基础实施记录

## 1. 结论

本轮开始 v1.1 第二阶段的 NowcastNet 离线研发，但不改变一期生产关键路径：

- MRMS 继续用于算法研发和可重复回归；
- pySTEPS-LK 仍是确定性基线，STEPS 仍是离线概率基线；
- NowcastNet 仅允许作为可失败的离线增强模型；
- 福建真实数据到位前，不声明福建技巧、业务资格或本地适用性；
- 官方源码、预处理协议和权重已完成审查：代码为 MIT，胶囊数据和权重为 CC0 1.0；
- 真实官方后端、105 CPU/GPU 数值冒烟和离线 Worker 对象链路均已完成；历史回算、
  实时影子和发布门禁继续关闭。

当前已经完成配置契约、官方双通道数组适配、真实权重加载、四成员随机推理、可重复性
修复、CPU/GPU 数值冒烟、长驻 Worker 和独立离线 Zarr 输入/输出契约。尚未完成新月份
MRMS 历史回算和技巧对比。

## 2. 官方协议基线

依据论文和官方 Code Ocean 胶囊 `10.24433/CO.0832447.v1` 冻结当前协议：

| 项目 | 值 |
|---|---:|
| 输入时次 | 9 帧 |
| 总序列/预报时次 | 29 帧 / 20 帧 |
| 时间分辨率 | 10 分钟 |
| 输入/输出变量 | 降水率，`mm h-1` |
| 雨强输入上限 | 128 mm/h |
| 集合成员 | 4 |
| 论文测试空间尺寸 | 512 × 512 |

官方运行脚本固定 `input_length=9`、`total_length=29`，所以可执行模型边界是 20 帧
输出；本轮已据此纠正早期按论文说明预估的 18 帧。RainPulse 的 0–2 小时应用产品只
使用前 12 个十分钟时次。不得依据第三方重写自行猜测剩余协议。

官方加载器用 `pixel / 10 - 3` 解码 PNG，负值位置的雨强置零并将掩码通道置零，随后
把输入组织为雨强/有效性双通道。官方网络实际只读取第一个雨强通道，因此本阶段仍
拒绝任何缺测裁剪窗口，避免缺测被网络当成无雨；双通道接口本身保持与官方一致。

胶囊和权重已冻结：

| 对象 | SHA-256 | 许可证 |
|---|---|---|
| Code Ocean 完整胶囊 | `3607858ca1fe0cd4a22b0c5ef51dc91f76ca156c182c348a08eecf41dbd66821` | 代码 MIT；数据见下项 |
| `mrms_model.ckpt` | `5faee618c4532dff0eec27cb79c29bd7109396a968f9b173a906f8592a2059a5` | CC0 1.0 |
| RainPulse 设备兼容补丁 | `7a42637adacb6d37ffec1b559d6a31ba05c45338e01fb2d054448f3c0dfe7f32` | 仓库代码 |

## 3. 已实现内容

### 3.1 配置和门禁

新增：

- `configs/schemas/nowcastnet-profile.schema.json`；
- `configs/nowcast/rp026-nowcastnet-offline-v1.yaml`；
- `algorithms/rainpulse_algo/nowcast/nowcastnet_profile.py`。

离线真实推理需要全部满足：

1. 官方源码审阅完成；
2. 官方代码 MIT License 已核对；
3. 权重保存于固定运行 URI，并冻结 SHA-256；
4. 权重来源审阅完成；
5. 官方预处理协议核对完成；
6. 运行环境及兼容补丁版本完成审查；
7. 显式开启离线推理。

源码、许可证、权重哈希、预处理协议、兼容运行环境和 GPU 验收已经完成。权重固定为
`file:///opt/rainpulse/nowcastnet/official-v1/data/checkpoints/mrms_model.ckpt`，只打开
`offline_inference_enabled=true`。实时影子、产品发布和业务资格在 RP-026 中仍固定为
`false`。

### 3.2 输入适配

`prepare_nowcastnet_input` 要求：

- 雨强和有效掩膜均为 `9 × 512 × 512`，适配后为 `9 × 512 × 512 × 2`；
- 掩膜只能包含 0/1；
- 当前版本拒绝任何缺测，不把缺测填为无雨；
- 有效雨强必须有限且非负；
- 超过 128 mm/h 的有效像元显式截断，并返回截断像元数量。

这只是模型原生数组边界。RainPulse 五分钟 `NowcastInput` 到模型十分钟序列的生产转换
尚未启用，MRMS 回算必须直接构建十分钟源序列。

### 3.3 后端边界

真实后端已通过可注入接口接入。它只加载固定哈希的胶囊文件和权重，输出必须是：

```text
member × lead_time × y × x
4 × 20 × 512 × 512
```

官方生成器没有非负激活，样例会产生负值。适配器按
`clip_to_zero_with_diagnostic` 显式截为 0，并记录
`clipped_negative_output_pixel_count`；NaN、无穷值或错误形状仍使整次任务失败。
增强模型失败不得影响 LK/STEPS 已有离线结果，更不能伪造输出。

官方源码把坐标网格和随机噪声硬编码到默认 CUDA 设备。版本化兼容补丁仅完成设备
绑定、非持久网格 buffer 和显式权重目标设备，不修改网络层或权重。官方自定义谱
归一化会在 `eval()` 中继续更新内部 `u/v`；长驻后端在每个任务前恢复这些状态，既
保留单次四成员的官方生成顺序，也保证同输入/同种子重复结果一致。

## 4. 验证

复现命令：

```bash
make test-nowcastnet
```

当前测试覆盖：

- profile 和 JSON Schema；
- 未就绪原因完整报告；
- 有效无雨与缺测严格区分；
- 128 mm/h 截断诊断；
- 后端输出形状、负值显式诊断截断和非有限值拒绝；
- 胶囊/权重/补丁哈希门禁和四成员种子回绕；
- 注入式合成后端完整运行且 `operational_eligible=false`。

## 5. GPU 服务器核查

2026-08-30 核查和离线部署 105 测试服务器：

- GPU：NVIDIA RTX 6000D，约 85 GB 显存，计算能力 12.0；
- NVIDIA 驱动：595.71.05；
- `nvidia-container-cli`：1.20.0；
- 独立运行环境：Python 3.13、PyTorch `2.12.1+cu132`、CUDA 13.2；
- 活跃仓库处于 `main` 且部署目录存在；
- 已审查胶囊部署在 Git 忽略目录 `runtime/nowcastnet/official-v1`，权重远端哈希复核
  一致；
- `yons` 直接访问 Docker socket仍被拒绝，但本轮使用独立 Python 环境，不依赖
  Docker 权限。

官方原始环境为 CUDA 11.7 / PyTorch 1.12.1，而 NVIDIA 官方架构矩阵显示计算能力
12.0 首次由 CUDA 12.8 支持，因此 105 不能把原环境直接当作兼容性证明。本轮冻结的
CUDA 13.2 运行环境已经识别目标 GPU。

胶囊 MRMS `00000` 样例的 CPU 真实推理结果：

- 模型加载约 0.82 秒；
- 4 成员 × 20 时次 × 512 × 512 推理约 7.0–7.2 秒；
- 两次同输入/同种子 `max_abs_delta=0`；
- 原始输出负值 8,578,544 个，显式截零后最小值为 0；
- 成员 0/1 截零后平均绝对差约 0.439 mm/h，随机成员并非重复副本；
- 原始四成员输出 SHA-256 为
  `efe6347d95aeb0192ca8842f5f2a66be5e65e2d2e890a8d2729013510caf937e`。

初次 GPU 加载时 85,651 MiB 中仅剩约 435 MiB，显存由既有的两个 `llama-server`、
VLLM 和语音服务占用。经授权临时停止 Qwen3.8 后，可用显存增至约 38,084 MiB，完成
四成员 GPU 冒烟：

- 模型加载约 0.895 秒；
- 四成员首轮约 0.387 秒，热态约 0.153 秒；
- 峰值已分配显存 1,061,443,584 bytes；
- 两次同输入/同种子 `max_abs_delta=0`；
- 输出形状 `4 × 20 × 512 × 512`，无 NaN/Inf；
- 原始负值 8,597,696 个，适配器按契约截零并记录诊断；
- 截零后成员 0/1 平均绝对差约 0.397 mm/h，集合成员不是重复副本；
- 原始输出 SHA-256 为
  `d42c71b957b6d8114cea58f74dce0d17549ef14b6ae9b38b59064c8838d4b073`。

测试完成后 Qwen3.8 已按原参数恢复，8004 `/health` 返回 `{"status":"ok"}`，恢复后
其显存占用约 37,518 MiB。结构化证据保存在
`configs/verification/rp026-nowcastnet-gpu-smoke-v1.json`。

## 6. 离线 Worker 与对象链路

新增 `forecast.nowcastnet_offline.requested.v1`，只在
`rainpulse.jobs.requested.nowcastnet_offline` 上消费。生产实时编排不会产生该事件。
Worker 首次接受任务时加载官方模型，后续任务复用同一进程内实例；任务最多投递一次，
失败不会阻塞或改写 LK/STEPS 结果。

输入和输出分别使用独立的
`rainpulse.nowcastnet-offline-input/1.0` 与
`rainpulse.nowcastnet-offline-output/1.0`，不冒充五分钟业务 `ForecastOutput`。输入保留
9 个源资产、十分钟时次、经纬坐标和全有效掩膜；输出保留 4 成员、20 个十分钟时效、
固定种子、权重身份和截断诊断，并强制
`operational_eligible=false`、`product_publication_enabled=false`。

105 集成验证结果：

- 固定 `/opt` 权重 URI 与胶囊实际文件解析到同一对象且哈希一致；
- 同一进程两次取得相同缓存运行实例，模型加载约 0.874 秒；
- 四成员 GPU 推理约 0.420 秒；
- 原始 8,597,696 个负值按契约截零，输出无非有限值；
- 离线 Zarr 为 2,900 个对象、52,469,911 bytes，共同有效覆盖率 1.0；
- systemd 常驻进程连接 NATS 后 `/healthz` 返回 `ready/nowcastnet-offline`；
- 单元安装后保持 `disabled/inactive`，避免与共享 GPU 上的 Qwen3.8 常态争抢显存；
- 测试后 Qwen3.8 已以 `yons` 用户按原参数恢复，8004 `/health` 返回 `ok`。

结构化证据保存在
`configs/verification/rp026-nowcastnet-worker-gpu-smoke-v1.json`。

## 7. 下一步

1. 从未用于 RP-021/RP-024 独立结论的新 MRMS 月份冻结 NowcastNet 开发集；
2. 运行分批回算，记录 GPU 利用率、延迟、显存、失败案例和对象容量；
3. 另选未触碰的留出月份，与 persistence、translation、LK、STEPS 做共同有效域对比；
4. 只有福建连续 QC/QPE 数据到位后，才讨论实时并行灰度和本地适用性。
