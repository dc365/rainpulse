# 近站强回波例外（strong_near）

## 动机

2026-08-28 09:00 Z9598 的可见近站杂波主要集中在南—西南侧 39–75 km、30–50 dBZ。
这些门通常有低 RHOHV、高 SNR、明显反射率纹理和两个 CF 证据族，但旧 near 路限
`DBZH≤30`，CF 又把 `DBZH≥30` 作为强回波保护，因此均未动作。

## 规则

`strong_near` 是 `near_revision` 下的可选子策略，默认关闭。候选必须同时满足：

- 观测有效，距离不超过 75 km；
- `30≤DBZH≤50`，且已进入父级 `CF_STRONG_MASK`；
- RHOHV、SNR、极化邻域可用，`RHOHV≤0.85`，`SNR≥12`；
- `CF_TEXTURE_SCORE≥0.40`；
- `CF_FAMILY_COUNT≥2`；
- 未命中 CF hard/local/legacy/weather 保护、weather proxy 或 CF mixed。

背景增强单独不阻断：它可能是晴空背景上的非气象回波，但不能替代当前实测证据。
audit 只记录候选；quarantine 才把候选投影为 QC DOWNWEIGHT、取消 QPE 资格和
`DBZH_USABLE`，并把 CF/CR 资格同步扣除。所有子配置仍保持
`operational_eligible=false`。

`object_propagation=true` 时，强回波核心可以在一个不跨越天气/混合保护、方位
断缝或坏射线的原始观测连通对象内扩展。对象必须仍在 10–55 dBZ、75 km 内，
核心门数至少 3、核心占比至少 30%，且对象不超过 5000 门。传播只补充同一测量
对象内未达核心条件的门；对象数超预算时整个 strong 新增分支弃权并保留父级 CF。

## 真实样本核查

用 09:00、09:12、15:30 四站共 12 个体扫回放该规则：

| 时次/站 | 候选门 | 仍显示命中 | QPE 损失 |
| --- | ---: | ---: | ---: |
| 09:00 Z9598 | 434 | 272 | 0.152% |
| 09:12 Z9598 | 555 | 376 | 0.206% |
| 15:30 Z9599 | 437 | 362 | 0.033% |

12 个体扫的最高 QPE 损失为 0.206%。规则显式排除 hard/local/legacy/weather proxy
和 mixed；Z9598 09:00 的主要可见残留簇分别命中 88/29/11、39/24、10/3/12 门。
这不是独立天气误删率，正式启用前仍需弱雨、混合降水和海上降水样本验收。

按执行点可用的原始观测连通对象回放，object 传播在 09:00 Z9598 将 QPE 损失
从 272 门提高到 401 门（0.224%），09:12 Z9598 为 531 门（0.291%），仍显著低于
5% 业务预算。该结果同样不是独立天气误删率。

## 生成与验收

`scripts/make_near_joint_profiles.py` 会额外生成：

- `near-joint-strong-audit.yaml`
- `near-joint-strong-quarantine.yaml`
- `near-joint-strong-object-audit.yaml`
- `near-joint-strong-object-quarantine.yaml`

先运行 audit 对比候选，再用 quarantine 做同输入端到端复核。`scripts/audit_near_joint.py`
会检查 strong 候选、保护、隔离和 CR 资格泄漏。
