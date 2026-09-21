# 近站杂波启用与联合审查 v1

本轮依据现有 near_clutter 开关审计、部分极化/因果时间对照及 DEM—CR 入口审计实施。
不修改径向算法，不采用整幅中值滤波，不把上层 NaN 当无雨，也不把近站所有弱回波当杂波。

## 集成结构

1. 从实际运行父配置生成独立子配置，开启 near_enabled、允许 near_nonmet，并使用统一
   ZDR可靠性保护。旧 near_clutter 仍使用真实 Py-ART/Gabella 纹理与 NP 天气保护，沿原NP
   定量隔离链运行。保守开启档与单独调强档分开，不能把三个门槛同时放宽后声称已验收。
2. CF 内补一条不依赖 ZDR、也不强求高反射率纹理的当前测量路径。目标自己需要可靠SNR、
   RHOHV、PHIDP和相邻相位对。只统计实际样本。该路径只有一个极化证据家族，新增动作仅限CR。
3. 原有已验证 raw temporal_context 经 prepare/apply_basic_qc/runner/VOR 传入CF。
   全部捐赠早于目标，同站同处理配置、时空配准受限。最近可比实测冲突阻断挑选更早匹配。
   这不是长期晴空背景，不用既有QC掩码作为时间真值。
4. 保留现有 NP回溯背景和CF短时段背景绑定。新字段记录模型状态、当前偏离和几何误差，
   用实际赢家审计区分无资产、缺覆盖、不匹配、增强与天气保护。没有资产不伪造模型。
5. 在CF处置内加入DEM可靠性限制，所有仰角先限制资格再取CR最大值。PBB局地相交与CBB
   上游遮挡分别记录，不用遮挡一票确认地物。复用既有波束/阻挡原语和已加载地形；新路径
   要求高程基准已核验。不强制把1985国家高程当作EGM2008。

## 配置

在实际仓库环境运行（默认会用完整 OpenSourceQCProfile 校验父/子配置）：

```bash
python scripts/make_near_joint_profiles.py \
  --parent /path/to/actual-active-parent.yaml \
  --output /path/to/new-near-joint-configs
```

输出 audit、near-on、near-on-tuned、joint-cr、joint-cr-dem 五档。
除了 audit 外均显式开启 near_enabled；joint-cr-dem 是待验收的完整候选，不自动部署。
所有旧径向、背景路径、库后端、定量动作保持父值；pipeline_version 保持原Literal，
新增身份由 profile_version 和扩展配置哈希表示。不得从脱敏配置生成线上资产路径。
`--subconfig-only` 仅供离线源码子集测试，不可代替整库配置验收。

## 不同产品的变化

near-on 分支的NP隔离可改变QPE和上排定量资格单站图；新增CF部分极化、时间和DEM路径
只收紧可信CR，因此上排图未必与下排CR同时变化。不要为追求显示一致再修改QPE或色标。
新增CF路径不恢复旧不合格门，保留所有独立/局地/未知天气保护；现有CF的局地冲突策略仍遵守父配置。

## 核验

```bash
python scripts/audit_near_joint.py --qc-zarr /path/to/full-qc1.zarr /path/to/full-qc2.zarr \
  --composite-npz /path/to/composite.npz --composite-json /path/to/composite.json \
  --output /path/to/new-audit.json
```

需要完整解包的QC Zarr及CR数值/来源回执，不从PNG恢复门级资格。检查有效开关、背景漏斗、
时间来源、DEM可用性/基准状态、被保留原因以及每个实际赢家的资格和值。
默认将不兼容或缺失的附加资料明确弃权，不以提高误删率追求图面全空。

## 数据与验收限制

当前脱敏包移除了 radar_config_version，所以严格同处理配置的过去体扫动作无法在这些
原始包上得到完整实证；自动弃权，而不是用“同站”代替处理身份。真实背景NPZ和DEM栅格
也未提供。当前测量回放仅对比独立NMR+CF核心，未重建原有NP/VOR/RDR等全部父级保护。
完整Worker/Zarr/四站业务链、真实背景/DEM效益与独立弱天气误删率仍须实际环境验证。
