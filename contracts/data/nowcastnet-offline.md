# NowcastNet 离线适配边界

`rp026-nowcastnet-offline-v1` 只定义 MRMS 算法研发使用的 NowcastNet 适配边界，
不启用实时推理、产品发布或福建业务资格。

## 输入

- 字段：`RATE_QPE`；
- 单位：`mm h-1`；
- 9 个历史时次，固定 10 分钟间隔；
- 官方胶囊总序列为 29 帧：9 帧输入、20 帧输出；
- 模型输入数组形状：`time × 512 × 512 × channel`，两个通道依次为雨强和
  `VALID_MASK`；
- `VALID_MASK` 必须与雨强数组同形，且当前基础版本拒绝任何缺测；
- 有效雨强必须有限且非负；超过 `128 mm h-1` 的值在进入后端前截断，并记录
  `clipped_pixel_count`，不得静默修改。

RainPulse 业务 `NowcastInput` 仍使用固定 5 分钟步长。NowcastNet 必须经过独立的
10 分钟序列和空间预处理适配，不能把 5 分钟序列直接冒充模型原生输入。

离线对象使用 `rainpulse.nowcastnet-offline-input` contract `1.0`，包含：

- `rain_rate[time, lat, lon]`：`float32`；
- `valid_mask[time, lat, lon]`：二值 `uint8`；
- `lat[lat]`、`lon[lon]`：`float32`；
- `issue_time`、`grid_id`、9 个不可变 `input_asset_ids` 和来源分组身份。

该对象只能通过已提交对象标记读取，事件载荷只传对象 URI 和身份元数据。

## 输出

- 4 个成员；
- 20 个模型原生未来时次，固定 10 分钟间隔；0–2 小时 RainPulse 产品只取前 12
  个时次，不能把后 8 个时次混入两小时产品；
- 数组形状：`member × lead_time × 512 × 512`；
- 单位：`mm h-1`；
- 非有限值导致整次离线任务失败；
- 官方生成器没有非负激活，负雨强必须按配置显式截为 0，并记录
  `clipped_negative_output_pixel_count`；非有限值仍使整次任务失败；
- 在真实后端通过审查前，不生成业务 `ForecastOutput` 或应用产品。

Worker 输出使用独立的 `rainpulse.nowcastnet-offline-output` contract `1.0`，而不是
五分钟业务 `ForecastOutput`。对象保存 `rain_rate[member, lead_time, lat, lon]`、
`member_valid_mask`、`output_valid_mask`、10–200 分钟坐标、固定随机种子、模型/权重/
输入身份和截断诊断；`operational_eligible=false` 与
`product_publication_enabled=false` 必须同时存在。

## 激活门槛

离线真实推理只有同时满足以下条件后才可启用：

1. 官方 Code Ocean 源码已经审阅；
2. 官方 `code/LICENSE` 的 MIT License 已经核对；
3. 预训练权重具有固定对象 URI 和 SHA-256，且完成来源审阅；
4. 论文与官方代码中的时间、空间、归一化和反归一化协议已经核对；
5. PyTorch/CUDA、目标计算能力和设备兼容补丁已经冻结并核对；
6. 配置显式设置 `offline_inference_enabled=true`。

任一条件不满足均失败关闭。第三方重写和来历不明的权重不能替代上述门槛。

## 官方胶囊核对说明

官方运行脚本把 `input_length` 固定为 9、`total_length` 固定为 29，因此可执行模型
实际返回 20 个未来时次。RP-026 以可执行胶囊为适配边界，不再沿用基础实现中预估的
18 帧。

官方加载器把 PNG 像元按 `pixel / 10 - 3` 解码为雨强，并构造雨强/有效性双通道；
官方网络随后只读取雨强通道。RainPulse 当前因此继续拒绝包含缺测的裁剪窗口，避免
网络把缺测位置等价看成无雨，同时仍将全有效掩码传入官方双通道接口以保持协议一致。

官方生成器输出不受非负约束。RainPulse 不静默接受负雨强：适配器执行
`clip_to_zero_with_diagnostic`，并把原始负值数量写入结果诊断。`128 mm h-1` 只约束
模型输入，不能继续用作输出上限。
