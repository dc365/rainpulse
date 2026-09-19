# 近站大片非气象回波：背景与可靠偏振邻域分支

目标仅为改善Z9591/Z9598站旁残留。独立实验分支不要求ZDR异常、纹理异常或
近零速度，避免把大面积连续弱非气象回波排除在检测范围外。未接入生产runner。

候选要求：当前组合资格有效；距站<75km；反射率<=20dBZ；单日背景有效观测
覆盖>=80%、至少20份、当前强度位于背景P10-3至P90+3dB；当前实测SNR>=8dB、
RHOHV在0–0.85。3条相邻射线、约2km距离窗口内，可靠实测覆盖>=80%，其中
低RHOHV且弱反射率占比>=70%。保留所有已有天气保护门。未知、低SNR、缺测
不作非气象支持；几何间断/重复射线附近弃权。两类核心证据为背景和偏振，
邻域一致性用于排除孤立异常，不计作独立观测。

修改的是内存中的原始极坐标组合资格，重新执行四站全仰角组合，非图片擦除。
原始数据不变，原生产结果不变。这里识别非气象候选，不强行归类地物或生物。

测试覆盖连片弱背景、孤立低RHOHV、降水型高RHOHV、低SNR、缺测、强回波、
天气保护、无资格与距离窗口边缘。相关四组共29项测试通过。

仍使用9月18日晴空背景回看8月28日，无独立天气真值，不能声称已验证误删率
或实时泛化。新增隔离门数只表示作用范围；以近站区域实际组合变化评估效果。

## 实测与近站区域结果

| CST | 站 | 关闭组合资格门数 | 75km内组合变化像素 | 占原有效像素 | 无剩余合格回波 |
|---|---|---:|---:|---:|---:|
|08:12|Z9591|36361|326|3.38%|21|
|08:12|Z9598|17192|307|4.15%|37|
|08:18|Z9591|40167|405|4.01%|26|
|08:18|Z9598|15510|386|6.43%|81|

全域组合变化分别633、791像素，均落在两个目标站75km区域。评分版分别为38、46。
变化多数是移除一个贡献后由较弱层/邻站接替，并非覆盖消失；图面大片弱回波
仍在。因此这是对近站问题有作用的分支，但还不能称为解决大片杂波。
强组合回波>=35dBZ变化0，天气保护门和强原始门改动0。

对照图使用-10至30dBZ固定色标展开弱回波，在75km圆内显示，未改动原始反射率。
证据：`artifacts/clear-air-20260918/near-background-clutter/`，包括results.json、
near-summary.json、两时次全域数值以及`*-near.png`近站对比。

复现：联合回放脚本使用`--near-background`；然后执行
`python scripts/plot_near_clutter_comparison.py output_directory`。
下一轮继续在近站剩余贡献上检查可靠性与邻域支持，尤其不能将被清理层后的
次强回波自动视为干净；不扩展到射线、色谱或其他功能。

## 105 接入与重算（2026-09-19）

生产服务镜像：`rainpulse-cpu-worker:near-background-20260919-v1`，QC pipeline
`qc-opensource-7.3.9`。实际近站接入在nonprecip project之前，尊重天气和mixed保护，
分类为near_nonmet；统一关闭所有trust、QPE资格并设置LOW_QUALITY，随后执行P3
组合资格计算。RAW不改。新接入额外尊重各矩available、原生geometry_good和
no-rain下限，与早期研究回放相比可有更保守的结果，以真实worker产物为准。

资产通过profile指定文件SHA256；固定研究网格及仰角一致性检查。仅限目标UTC
日期2026-08-28，其他日期不采用这份未来背景。Z9593/Z9599没有此背景资产，
使用同版本其余算法重算，避免跨站pipeline混用。

新配置：QC `configs/qc/radial-20260918/near-background-20260919.yaml`；gridding、
mosaic、qpe下同名配置；出图`diagnostic-near-background-20260919.yaml`，renderer1.5.2。
各阶段显式新profile身份，避免复用旧job。服务部署覆盖现有最后一个compose
volume overlay，新overlay源文件另存`deploy/docker-compose.qc-near-background-20260919.yaml`。
旧105 overlay备份`/tmp/rp-near-previous-compose.yaml`，控制环境在原目录留有限权备份。

后台脚本`/tmp/rp-near-recompute.py`、日志`/tmp/rp-near-recompute.log`，顺序处理08:12、
08:18：各4站QC→格点→新analysis拼图→QPE→诊断。任一步FAILED/CANCELLED即终止，
不会把未完成阶段标记成功。最终回执`/tmp/rp-near-recompute-results.json`包含新analysis ID。
首批QC任务已提交并确认worker开始执行；此记录不表示所有重算已完成。

隔离worktree验证review/volume/integration共219项通过（另有既有NumPy ABI警告）；
新增真实augment→project→序列化检查、日期/availability屏障和hash拒绝测试。
只读检查发现的class allow-list遗漏已补上，部署配置明确包含near_nonmet。

首个生产实算回读成功：08:12 Z9591，QC job
`380891a9-1f57-54b2-9e62-006346775dba`，pipeline7.3.9，
`NP_NEAR_BACKGROUND_MASK`计41510门。逐门断言QPE_ELIGIBLE与
REFLECTIVITY_ELIGIBLE_FOR_CR均为0通过。完整worker与离线回放的进入资格
边界不同，门数不等同于离线36361；不能将该数字直接当成组合像素减少量。
其他站、格点、拼图、QPE、图片在后台顺序重算中，尚未宣称网页已全部切换。
