# 近站极化候选与可信组合反射率准入

## 范围

以 fbfa0d5a0fe09a9bca2ed87f164bbe3856f9195f 为基线，落地上一轮两时次近站实验。
不是新一代通用天气/杂波真值分类器，不重写径向源、静态背景、MRMS、Go 或前端。
新增 `volume_review.near_measurement`，默认不存在；旧配置序列化和参数哈希不变。
所有新增能力仍是 non-operational 工程候选。禁止通过地图透明度或图片处理宣称 QC 改进。

## 三种处置组合

- `audit`：仅新增诊断，原有 CR/QPE/trust/flags/RAW 逐值不变。
- `nonmet_policy=cr_withhold`：仅将有当前极化支持的非气象候选移出可信 CR；不动 QPE。
- `nonmet_policy=quarantine`：显式选择后，非气象候选按独立 NMR 隔离处置，关闭信任/QPE并降权；仍不是确认杂波。

低 SNR 路径只有 `diagnostic_only` / `cr_withhold`，**无论非气象策略怎么设置，它本身都不能改 QPE或原始观测状态**。
`strict-cr-snr8` 使用极化 CR 隔离和 8dB 低可靠度 CR 隔离，两者分别计数。
3/6/8/10dB 是开发对照档，不是经过独立气象校准的统一标准。

## 核心实现

1. 输入是每层原始 gate 和各矩实际 availability；保留原采集顺序，不用图片或赢家列表作输入。
2. wradlib.dp.depolarization 实际调用返回 DR。仅实际合格的 SNR/RHOHV/ZDR 可参与；ZDR ±7.5dB 边界附近保守退出 DR，不能称为已证实的厂商饱和码。
3. 3射线×约2km窗口计数实际极化样本，至少6份、候选比例至少0.6。缺测不投票，不等于无雨。跨几何间断/重复/坏射线/不适用角间距/距离端点弃权。
4. 原有天气、混合保护以及当前可靠天气相容种子保持有效；≥30dBZ和按父配置定义的有效无雨不动。天气种子不等于独立真值。
5. 所有仰角、所有近站候选门使用相同准入，不是仅移除当前最大值再放任次高值接替。
6. 新源名为 `NMR_*`，未改写 RFI/NP 的历史判断。保存精确 `NMR_BEFORE_*`；验证时恢复真实旧状态，递归执行旧 VOR/NP/OC1/V7 校验，再验证新增量。
7. 非气象 quarantine 触及已计算相位/KDP/衰减的信任段时，整段派生值失效。原始 DBZH、原始 VALID、旧无资格门不改变、不复活。
8. CR 增加 `CR_NEAR_NONMET_CANDIDATE`、`CR_NEAR_LOW_RELIABILITY`、`CR_NEAR_WITHHELD` 数值风险产品；保留原赢家/次高来源追溯。混合新旧 NMR 配置的多站合成拒绝执行。

## 库使用与可验证性

生产后端默认 `wradlib==2.9.5` 的 `dp.depolarization`。可选 `gatefilter_check=pyart` 调用原生
`NativeSweep.to_pyart()`，使用 `arm_pyart==2.2.5` 的 GateFilter.exclude_gates(op='or')
验证最终资格一致性。GateFilter不是新增气象分类器；没有调用另一套去斑点算法来扩大删除量。
现有 Py-ART/Gabella/纹理等基线保持不动。

离线后端 `numpy_reference` 为同公式的独立实现，有不同配置哈希、清晰回执；缺库不会静默切换。
选中的库缺失、版本不匹配或运行错误会明确失败，不生成“已验证”结果。

```bash
python scripts/check_near_measurement_backends.py
python scripts/make_near_measurement_profiles.py \
  --parent configs/qc/radial-20260918/near-background-20260919.yaml \
  --output configs/qc/near-measurement-20260919 --pyart-check
```

生成8份独立子配置。先审计，再比较仅极化、严格CR，最后才评估非气象隔离对QPE的影响。
脚本不改父配置、不提交数据库、不重启、不发布。父配置已有的未来日期背景限制原样保留；
新增 NMR 自身不读取背景或未来时次。不能将基于该父配置的整条链称为“没有背景依赖”。

## UI 与产品边界

低可靠度路径只影响严格CR；上方单仰角业务图若仍使用QPE掩码，可能继续显示对应弱回波，这不代表NMR没有生效。
不要通过同步删除上方图片“对齐效果”。新的风险数值已写入 `volume_review/composite.npz`；本轮不增加React切换控件。
需要上下游标明“可信 CR / 未定覆盖 / QPE”，不能将不可用覆盖显示成有效无雨。

## 回放与验收

仓库没有脱敏原始数据。复现工具接收用户已有的完整导出；输出到新目录，不覆盖源。
当前特殊packs包缺少外层逻辑索引时，恢复工具只恢复与内嵌P0清单SHA256/CRC匹配的NPZ，
不能据此声称恢复了完整业务Zarr或验证了外层发布标记。

```bash
python scripts/recover_bound_near_snapshots.py --archive /data/cases.tar.gz --output /data/frozen-snapshots
python scripts/replay_near_measurement.py --snapshots /data/frozen-snapshots \
  --output /data/new-near-replay --backend numpy_reference --save-arrays
```

此次回放是两站两时次、44切面、7种策略的后置增量；两时次存储版本为7.3.9/7.3.8，分别与各自基线比较。
低可靠度隔离导致的弱回波覆盖下降不是杂波检出率。严格档保留强回波是保护条件的直接结果，不是零误删证明。
缺原生availability时只使用明确标记的finite/bounded fallback；轻量快照的DBZH_USABLE按显式QPE契约重建、LOW_QUALITY按冻结QI<0.5投影，不冒充完整旧状态回归。

真实验收需：真实库回归、完整配置加载、QC Worker→Zarr→绑定PNG→四站CR赢家追溯、不同天气过程误删和覆盖损失。
当前代码可运行与实验见效不等于可直接提升operational_eligible。
