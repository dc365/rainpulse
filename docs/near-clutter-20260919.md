# 近站非气象回波分支与真实数据回放

## 实现

在现有 nonprecip 分类与隔离链路中加入 `NEAR_NONMET=9`。这表示非气象候选，
不强行区分地物、昆虫或异常传播。原始数据不改；隔离门关闭反射率信任和 QPE
资格、清空 DBZH_USABLE；后续 P3 依据反射率信任生成组合反射率资格。

启用参数为 `nonprecip_review.near_enabled: true`，并将 `near_nonmet` 加入
`quarantine_classes`。默认关闭，现有生产配置未启用，尚未部署。

条件：距站不超过 75 km、反射率不超过 30 dBZ、SNR 至少 8 dB、
RHOHV 不高于 0.8 且 ZDR 在 [-1,4] dB 之外、现有 Gabella 或反射率纹理候选。
在三射线和约 2 km 距离窗内，有效测量覆盖至少 80%，其中偏振异常至少 70%。
跨角度缺口、粗角分辨率、缺测及低 SNR 不作为支持。天气支持与原有混合/增强
保护继续生效。各阈值为待验证工程初值，并非气象通用常数。

本分支使用偏振与反射率结构两个证据族。邻域一致性只作覆盖/一致性要求，
不冒充第三个独立证据。速度、谱宽、晴空背景缺失时不合成观测。

## 真实数据回放

2026-08-28 08:12、08:18 CST，使用对应分析实际参与的四站 7.3.8 QC 数据，
重用保存的原始观测、纹理证据和天气保护字段，单独回放新增分类分支。
此为模块回放，不是完整 worker 重算，也不是组合图像素减少数量。

| 时次 | Z9591 | Z9593 | Z9598 | Z9599 | 新增隔离可用门合计 |
|---|---:|---:|---:|---:|---:|
| 08:12 | 53 | 0 | 199 | 240 | 492 |
| 08:18 | 220 | 1 | 222 | 450 | 893 |

与既有天气保护门交集为 0；这不等于已证明真实降水误删率为 0。
两时次回放证明规则可以跨时次执行，但样本不足以证明泛化。
没有新增地物“确认”标签，没有将隔离门改成有效无雨。

Z9598 的 0/2 号低仰角反射率层没有同层 VR/SW，速度在独立扫描层；
现有 NP_FIXED_SAMPLE_COUNT 在本次近站采样中为零。原地物规则又需要验证过的
晴空背景，因而没有触发。不能通过把这些缺失输入填零来让规则触发。

## 结论与下一步

新增隔离有限，不能宣称解决红框的大片近站回波，暂不以此替换生产版本。
继续核查组合图获胜站/仰角的实际门，补接严格因果历史样本；晴空数据到位后
建立分站、分仰角、几何与版本绑定的背景资产。独立速度扫描只有在时间、
几何、覆盖可比性明确时才可关联。任何进一步放宽都须同时检查正常降水损失。

相关测试：
`uv run --project algorithms pytest -q algorithms/tests/review_20260917/test_near_clutter.py algorithms/tests/review_20260917/test_nonprecip.py`

全部 review 扩展、集成及 volume_review 回归共 197 项通过。运行环境有一条既有
NumPy 二进制兼容性警告，测试未失败。
可复现回放：`python scripts/replay_near_nonprecip.py inputs.json`，输入为
`[["case label", "s3://.../volume.zarr"], ...]`，需要工作进程相同的对象存储环境。

参考：
- https://docs.wradlib.org/en/stable/generated/wradlib.classify.classify_echo_fuzzy.html
- https://arm-doe.github.io/pyart/API/generated/pyart.retrieve.calculate_velocity_texture.html
