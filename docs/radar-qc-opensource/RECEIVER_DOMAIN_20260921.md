# 径向源独立矩观测域：实现与验收

基线：`bf9a4157d8de13c2ecc2023c83f2d030e2b2f63b`。
新增 `volume_review.receiver_domain`，在已有 VOR/NMR 后执行；不修改杂波、晴空背景或旧径向分支。
默认不配置、不改变历史参数序列化；所有新配置 `operational_eligible=false`。

## 修复内容

09:00 的断续 DBZH 不能再阻止连续实测 SNR 进入源参考。将 SNR、配对 DBZH/SNR、极化参考分开，
统一在同一个留出模型中保存实际支撑，而不是先取五矩交集。无需先通过旧 DBZH 对象入口。

09:12 类型的高相关、相位稳定可以同时符合源与局地天气相容性。
`source_joint_review` 允许已有明确局地 VOR 原因与新源模型共同解释；不把高相关直接当绝对天气否决。
明确空间/外部天气支持、NP 天气/混合/小强单体保护，以及无法确认来源的旧保护仍不覆盖。
没有完整线上 QC 输出时，不能推测这些强保护实际上是否存在。

模型只覆盖稳定相干接收源，非相干、低信噪比、强弱跳变的其它源仍由原路径处理。
不是一个包打天下的分类器，也不沿整条方位删除。最终每一个门必须满足目标测量条件。
保留目标和保护块留出；目标两侧若有实测 SNR 与参考侧翼不相容，会进入混合而非自动隔离。
缺极化的相容门默认只诊断；可单独选择限制 CR，永不由此隔离 QPE。可疑 ZDR 尾部不走此捷径。

## 配置生成

```bash
python scripts/make_receiver_domain_profiles.py \
  --parent configs/qc/radial-residual-20260920.yaml \
  --output configs/qc/receiver-domain-20260921
```

生成五档：audit、conservative-cr、joint-cr、joint-quarantine、joint-quarantine-partial-cr。
生成器不选择线上配置、不触发重算。第一轮先 audit 和 joint-cr；确认正常天气后再评估 quarantine。
`joint-quarantine` 才会影响使用 QPE 资格的上方单仰角业务图；CR-only 和 partial-cr 不会强制清空该图。

## 本次实测（开发案例，不是准确率验收）

本环境从已交付的源码中恢复 VOR/NMR 子树并与当前 GitHub Git blob SHA逐文件核对；
没有完整 checkout。两个冻结原始包含8个体扫88层，72层含反射率。按包内元数据校验8个原始内容哈希。
当前 QC 输出未包含，图片版本早于案例元数据，不能计算相对线上最新版的净新增隔离门数。
回放直接调用此次工程核心（显式 portable Blosc Zarr-v2 reader），不读红框或旧QC标签作模型输入。

目标统计域：50–240km、原始 DBZH>=10dBZ。方位仅用于结果定位，不在算法输入中。

| 原始目标 | 原始域门数 | 完整匹配 | 部分匹配 | 完整源资格（无未提供的强保护时） |
|---|---:|---:|---:|---:|
| 09:00，SITE_A最低反射率层，332.11° |265|214|51|214|
| 09:12，SITE_A第三切面，11.53° |396|360|24|360|

对旧QC PNG非透明像素对应的唯一原始采样门：09:00共21个，完整15、部分6；09:12共81个，完整73、部分8。
这不是PNG反推出的全门资格，也不是净清除数。全扫描的完整匹配只出现在SITE_A的四个低层切面；
其它站54个含反射率切面未出现该类完整匹配。大量全匹配门可能已由旧算法处理，不能全部算新收益。

## 自动化验证

重点包含：目标/保护块变值不能改变训练参数与支撑哈希；方位旋转、北向跨缝、原采集顺序恢复；
相位原点平移；缺SNR、缺侧翼、缺目标极化、尾部、目标极化和侧翼冲突；audit不动旧结果；
CR-only不动QPE；quarantine派生值失效；VOR→NMR→RDR原适配器与旧验证器递归；
实际CR函数的赢家逐门重建、拒绝新旧配置混合、拒绝隔离门泄漏；资源超限全体扫弃权。
本地182通过、2跳过（既有真实wradlib/Py-ART环境项）。这不是完整worker/Go/React测试。

## 可复现命令

```bash
python -m pytest -q algorithms/tests/receiver_domain_20260921 \
  algorithms/tests/volume_review_20260919 algorithms/tests/near_measurement_20260919
python scripts/replay_receiver_domain.py --cases /data/qc-case-bjt0900 /data/qc-case-bjt0912 \
  --output /data/receiver-domain-replay
```

正式环境默认用 zarr；只读研究环境缺zarr时可显式 `--portable-reader`（需blosc2）。不静默替换解码器。
`--targets` 可读匿名结果定位JSON，仅用于统计，不进入算法。

## Codex 整库验收必须补齐

在同一输入、配置、原生可用性和上下文下做父/子完整Worker回放，保存完整QC Zarr。
逐层统计旧已隔离、新增隔离、保护拦截和部分资料未定，再由四站全部仰角重建CR并验证每个赢家。
确认 source_joint_review 的局地保护复核没有覆盖NP或独立天气保护；不要为了图面清空关闭这些保护。
新增原型不恢复原来的任何拒绝，无法评价旧算法是否过删。用独立天气过程及不同干扰类型验收漏检/误删。
未经上述验证，不能直接合并切换在线配置或宣称已彻底解决两图所有径向。
