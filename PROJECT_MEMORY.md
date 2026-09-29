## 2026-09-29 全问题 X 站按 fc757e8 重算（r7x，本会话；不改算法，仅配置+重算）

- 用户指令：另一 LLM 的最新算法（fc757e8，radial_source/source_blocks/source_fans 三代模型）已解决大量径向/扇形问题；用最新算法重算"当前已有的、有径向等问题"的 X 站，全仰角，重新出图；算法本身不许改。
- 部署核对：105 运行 `x-fan-fc757e8-mb/-qc`（healthy），网络 SHA f54dbe66（仅 zf701 有新模型键）；zf701 70 卷已由对方 LLM 重算并审计（勿重提）。
- **问题站定案（原始数据逐站体检：形态学辐条 + 逐射线极化异常门 + 远距 ghost/扇区集中度）**：A 组=窄径向辐条站 **zf401**（7 条射线≥100 异常门）、**zf505、zf702**（各 1 条，zf702 与 r4 观察 6187 径向门一致）、**zf402**（cut4 38 条 ≥15dBZ/20km 辐条射线）；B 组=宽扇形/海杂波/噪声站 **zf101-105**（ghost 15k-82k 门/卷、无窄辐条极化特征）；zf900（无 SNR 字段、远距回波 24 门）、zf605/zf602-604/zf201-203/zf501-504 干净或无径向特征，排除。
- 配置动作（零算法改动）：A 组四站 enhancement 增加 zf701 同款 5 键（radial_source_enabled=true、maximum_width_deg=7.0、block_model=true、fan_model=true、maximum_new_exclusion_fraction=0.65），逐字复制；网络备份 `.build/x-qc-v2-network-20260928/network-before-agroup-<ts>.json`，新 SHA c9e3a3d2…；三 workers 重建健康（--scale 2）。B 组保持 r6 档（censor 3dB/either flank 已在其 enhancement）。
- 试点 zf401 尾部 2 卷（run 4ec02402）：radial_source 在新站点火（source_gates 4008/10102 cut0、1361/3908 cut2），全 20 切 EVALUATED 无弃权，REJ 门 QC 全 NaN，20/20 map_qc 重生成；视觉：窄辐条基本清除、宽扇形清 85-95%、高仰角干净、无降水误删迹象。
- 批量：9 站 570 卷（A 组 200 + B 组 370）分 80 个计划（≤8 卷、≤55min 窗、分钟对齐；注意 plans API 幂等键需 16-128 字符、时间须对齐分钟），后台提交器+轮询器+完成守护于 105 `/tmp/r7{submit,poll,wait}.*`，状态 `/tmp/r7state.json`。zf101 首批 320 切（40 切/卷）全 EVALUATED，censor 清 135.9 万门（B 组效果主要来自 r6 噪声底 censor），map 全生成；视觉扇形清除 80-90%。
- （终审数字与逐站清单见下一条补充。）
- **r7x 终审（79/79 计划 SUCCEEDED + 3 补卷计划）**：9 站全部预期扫描重算完毕，共 ~20,700 切；逐站——zf101 3160 切/censor 5161 万门；zf102 3120 切/7704 万；zf103 2920 切/7741 万；zf104 2800 切/8553 万；zf105 2800 切/4551 万（B 组五站 source=0、效果来自 r6 censor）；zf401 170+20 切/source 62,864 门（79 切有点火）；zf402 414 切/source 71,537；zf505 594 切/source 749,270（160 切点火，7 切预算弃权）；zf702 603+9 切/source 1,913,208（118 切点火，30 切预算弃权）。**全部切 EVALUATED 或预算弃权（共 37 切，均为源候选 >65% 观测的极重污染切——按护栏整体不动作，保留为已知残余；是否放宽 maximum_new_exclusion_fraction 需单独评审**，对方 LLM 曾为 zf701 从 .35 提到 .65）。map_qc 零缺失；zf702 缺卷 0ba02b76 已补跑（run 886aa7f8）；zf401 缺卷 2ab1fab7 输入 zarr 从未存在（无 _SUCCESS 标记），不可重算。
- **吞吐**：用户要求提速后，停闲置追溯容器 retro-boundary-qc-0806（09-22 完成后占 21.8GB，docker stop 保留可重启），ops-multiband-worker 由 2 扩到 12（24 核主机，load ~10，内存 0.8-2.2GB/个），批量尾段从预估 1.5h 缩到 ~15min。S 波段 worker 与四 S QC worker 未动。
- 用户样例 zf505 40700aec（00:44:51Z）新 result `9af85a8b-6543-4c52-9490-1b3a81305340.83c46127-1559-4c5d-91e5-dc4cf5a92091`：cut0 source 14,218 门（辐条群基本清除，视觉对比确认）+ censor 3,280；cut4/5 再清 7,156/5,430。

## 2026-09-29 strong fan deployed fc757e8 — 70 scans / 630 cuts verified

- User requires generic obvious radial/fan QC across elevations and times. Local main/origin source `fc757e8a99a046596b5cfafd49ee895bd0dc68d8` pushed before105 deployment.150 XQC/multiband tests pass; strong-fan, mixed-mode, trend, isolated-strong-spoke, sparse-family and REF-dropout regressions observed red then green. Source only changes X modules; defaults off, no site/bearing/time/elevation hardcoding.
-105 images `rainpulse-cpu-worker:x-fan-fc757e8-mb/-qc`, two ops multiband + one ops QC healthy. source_fans SHA1241460418f36496ec686d06e2253b16becce8478f6ba50b953061e9e0bb3218; network SHA f54dbe6688d6e613c2d21280b138908d42744bb194f5d36805ceb043e65dc6b4. OnlyZF701 enables fan model; reviewed candidate budget.65 (nextcut2 previously62.6% caused whole-cut abstention at.60). Budget enforcement unchanged. S profiles/workers untouched. Hourly automation PAUSED; no trustedfusion/QPE/forecast.
- Reused remote context `.build/x-radial-1d81cd1/src`; release/rollback `release-fan-fc757e8.json`, `network-before-fan-fc757e8.json`, `compose-before-fan-fc757e8.json`, `compose-chain-fan-fc757e8.json`. Active override `.build/x-qc-v2-e549510/compose.json`. Actual MinIO available257779306496bytes/107231867inodes; do not confuse system-root inode count with MinIO volume.
- First run `dc0c65e1-45f7-46d6-b3ea-04044a72bf67` 2/2SUCCEEDED,18cuts verified raw hashes unchanged/source actions2/QCNaN/QPEdisabled. Original07bdd490 result `cdf17f1f-dd23-4448-8319-f621ac206a0b.68fc6e8a-77ce-498d-af70-f52babbe3f69`; adjacent93d2d253 result `176858fc-dc52-446e-a852-84e8f735d870.d91d9053-24a6-4832-95a8-738874bc4b60`. Live UI originalcut3,adjacentcut0+2 inspected, dominant strongfans removed; sparse residual/local echoes remain, not a zero-error claim. Source-diagnostic native counts beyond25km>=35dBZ: originalcut3 1830/1929, nextcut0 3705/3801,nextcut2 5560/5642 removed.
- Remaining68 registeredZF701 scans completed in13 bounded plans (first00:12–01:00, then30min windows01:00–07:00Z). API limits1hour/8scans; initial larger requests rejected before submission, no forced override. Run IDs in local `.build/fan-hour-runs.jsonl`, corresponding remote receipts `run-fan-fc757e8-hour0.json` and `run-fan-fc757e8-half2..13.json`. Do NOT resubmit successful plans. Poll `python3 .build/poll-fan-hours.py`.
- Final audit70unique volumes630cuts ALL EVALUATED; all70tasks SUCCEEDED; scan-ID set equals frozen70-scan catalog. All raw hashes unchanged, source actions2/QCNaN, QPEfalse; no hidden action-budget abstention.173cuts contain source detections. UTC00:03:32.136–06:56:19.288 (Beijing08:03–14:56); this is registered coverage, NOT a full24hour claim. Audit SHA953e38687ac9e34a36ea0de0c2a014d366d64ba635df3583315ebcae6f96f09e. Local+remote fan-release-summary.json/fan-audit.jsonl; release receipt status REGISTERED_SCANS_REPROCESSED_VERIFIED. Additional live UI08:15 and11:03 inspected. Sparse local residuals remain; not zero-error/all-station acceptance.

- ZF101 control firstcut + ZF605 seven nonemptycuts addzero source gates; laterZF70103:26 ninecuts addzero. These are controls, not independent rain truth. Local-proxy hypothesis was disproved in principal residual; corresponding policy change/test removed. Weather protections unchanged.
- All code/test/docs work only rainpulse-nowcast. Preserve unrelated untracked files. Source commit fc757e8; subsequent documentation-only commit records final evidence. Details docs/X_RADIAL_ZF701_INVESTIGATION_20260929.md. Old result URLs remain immutable; give new pinned result links.

## 2026-09-29 intermittent radial correction deployed (d075740)

- Latest user marked north/east/south residual strips and required concentrated correction. Source commit `d075740` is on local main and origin. New opt-in `source_blocks.py` recognizes interrupted range blocks and recurring amplitude modes; no bearing/station/time hardcoding. Missing REF no longer defeats source continuity.135 XQC/multiband tests pass; red regressions were run before fixes.
-105 ops images `rainpulse-cpu-worker:x-bursty-d075740-mb/-qc`; source file SHA9ec6a38d... matches local. OnlyZF701 enables block model with width7degrees (observed southern lobe6.22), candidate action budget0.6 (old0.35 abstained on newly detected contamination). Budget enforcement remains tested. Other station settings unchanged; S workers unchanged, hourly and full-day jobs stay paused.
- Deployment reused `.build/x-radial-1d81cd1/src` build context. Receipt/rollback `.build/x-radial-1d81cd1/release-bursty-d075740.json`, `network-before-bursty-d075740.json`, `compose-before-bursty-d075740.json`, `compose-chain-bursty-d075740.json`. Current compose override `.build/x-qc-v2-e549510/compose.json` references new images. Do not blindly run older rollback scripts.
- Run `01af4bba-a6c6-4ad0-bea2-017fa247f3df` 2/2 SUCCEEDED.07bdd490 result `dd9c37cf-5714-4c2c-ae5a-fa7df0d6c3ae.a7a1bde0-92db-4f73-aab4-7ec61da0c707`; firstcut source8733/reject16605 (previous11851).93d2d253 result `e6019c1e-c90f-41b3-86b3-20fd7f9c76c1.41774257-9a82-4feb-b842-7c803c7026cd`; firstcut source5497/reject14313 (previous9490). Published source actions2, QCNaN, QPE eligibilityzero verified; both DBZH_RAW hashes match frozen normalized input.
- Live UI screenshots17:46:32Z and17:47:07Z inspected. Marked first frame's long north/east/south strips substantially cleared; sector remaining >=15dBZ beyond25km: north1022→44, east651→13, south1693→132. Sparse residual points remain. Do not call network-wide XQC complete or claim zero rainfall loss.
- Next discrimination target: adjacent08:07 frame still has a strong broad southern wedge. It was retained; it is NOT verified rain and NOT automatically declared noise. This needs separate source/precipitation evidence; do not revive old southern rain proxy. No trustedfusion/QPE.18cuts offline completed; ZF605 seven nonemptycuts and ZF101 firstcut new-source0.
- Evidence in `.build/zf701-bursty-live-run.json`, `zf701-bursty-live-verification.txt`, `redbox-corridor-comparison.txt`, `zf701-bursty-allcuts.jsonl`, `zf701-next-bursty-allcuts.jsonl`; design/test details in docs/X_RADIAL_ZF701_INVESTIGATION_20260929.md. New links must pin the new result; old result URLs intentionally remain immutable.

## 2026-09-29 targeted radial-source candidate deployed; residual work remains

- Local main source commit `1d81cd1` pushed to origin. 105 images `rainpulse-cpu-worker:x-radial-1d81cd1-mb/-qc`, layered ONLY config/core/radial_source onto r6; COPY files0644. Three ops workers healthy; S radar-qc workers not restarted. Network SHA7fc94daf...; onlyZF701 has radial_source_enabled=true, maximumwidth5degrees. Hourly/all-day remain paused.
- Release/rollback receipt105 `.build/x-radial-1d81cd1/`: network-before.json, compose-before.json, compose-chain.json, release.json. Active override remains `.build/x-qc-v2-e549510/compose.json`, current images are new1d81cd1 tags. Do not apply obsolete r6 retag instructions without inspecting this receipt.
- Run `d28c261b-63cd-440b-96d0-c18533cc9d83` both tasks SUCCEEDED. User scan07bdd490 result `9c1ed4b8-af5c-4b71-9f9b-2af26c9bdb54.874de744-ae0d-4be7-bc5f-2f329e5efd60`: firstcut reject11851 (old9154), source3924 (overlapping causes), raw preserved, source action2+QCNaN verified, QPE eligibilityzero. Actual rays321.41/31.41/214.30 >=15dBZ beyond25km remaining0/20/10 out of578/512/568. New pinned live UI inspected; main narrow spokes reduced.
- NOT COMPLETE: actual rays13.36/195.23/110.33 retain245/360/304 such gates; wider/discontinuous southern/eastern strips remain. Adjacent93d2d253 firstcut adds0, quarantine9490 unchanged. User labels obvious radial morphology a QC target; old southern-sector rain proxy is invalid as ground truth. Next investigate interrupted/broader source support rather than claim evidence-floor inevitability or indiscriminately widen/lower thresholds. Keep candidate, no trustedfusion/QPE.
- 127 tests pass; ZF605 control seven nonemptycuts282/172/219/549/113/744/158obs havezero newsource. This is no quantitative rainfall-loss proof. Fresh artifacts `.build/zf701-live-rays.jsonl`, `zf701-live-verification.txt`, `zf701-radial-run.json`, `zf605-allcuts-evidence.jsonl`. Current local geographic preview matches live south-to-north sampling; earlier drift suspicion not reproduced.

## 2026-09-29 user-confirmed radial target and source candidate (not deployed)

- User: obvious radial forms should be QC, including southern strips; do not use the old165–210degree proxy as rain truth.
- Local opt-in `radial_source.py` detects bilateral measured-quiet SNR shoulders plus long narrow stationary receiver corridors, held-out blocks, weather protections and budget. Defaults disabled; no S edits.
- 127 XQC/multiband tests pass. Offline 5degree variant: firstcut source3924/totalquarantine11851; actual strong rays321.41/31.41/214.30 source-removal578/578,492/512,546/568. All9cuts evaluated. Nextscan firstcut adds0; broader/sparse forms remain unresolved. ZF605 abd4e26b firstcut adds0.
- Changes still local, no new105 result; r6 remains live. Do not claim issue resolved. Investigation/evidence in docs/X_RADIAL_ZF701_INVESTIGATION_20260929.md and .build/*integrated*/zf701-allcuts-evidence.jsonl. Hourly automation remains paused.

# RainPulse Project Memory

Updated: 2026-09-28 (Asia/Taipei)

This file is the concise handoff for a new Codex session. Stable engineering
rules remain in `AGENTS.md`; implementation details remain in the referenced RP
documents. Do not add passwords, tokens, private data-source details, or raw
operational data here.

## Single working directory (2026-09-28)

- User requires all future RainPulse maintenance and branch work in `rainpulse-nowcast`; no new worktrees, clones or branch directories (including temporary checkouts). This overrides historical worktree workflows mentioned below.
- 2026-09-28 cleanup completed: removed all ten historical sibling worktrees with `git worktree remove` and pruned stale registrations; branch history retained. All committed HEADs were already contained in main at `3757cbb`, so no merge was needed. Uncommitted deploy-build and sx-qc-ui changes, plus local build/runtime/validation evidence, are preserved as patches and archives under `.build/worktree-retirement-20260928/` (inventory and removal receipt included). Reinstallable dependencies and caches were excluded. Archived uncommitted changes were not applied to main; inspect them if needed. Only `rainpulse-nowcast` remains as the working directory.
- Further uncommitted pre-existing work (diagnostics/clutter-fusion/qc_zarr/Go operations_adapter modifications plus height-datum-pkg deletion, 2026-09-28) is preserved at `.build/x-qc-v2-apply-20260928/uncommitted-tracked-vs-3757cbb.patch` and was not applied to main.

## X radial re-investigation (2026-09-29, unresolved)

- Latest user explicitly reauthorized X radial debugging for ZF701 scan `07bdd490`; hourly automation stays paused. See `docs/X_RADIAL_ZF701_INVESTIGATION_20260929.md`.
- Fresh live inspection of latest result `6472afda...6d707899` still shows substantial spokes. Earlier r6 "near clean"/"zero rain removal" claims below are NOT accepted; reject totals and round-angle probes were insufficient. Actual ray321.41 has578 surviving >=15dBZ gates beyond25km, ray31.41 has512, ray214.30 has454.
- Confirmed current limitations: REF-required flank gate ignores available low-SNR neighbors; rho<=.9 conjunction excludes high-rho interference; spatial phase jitter missing on isolated spokes; coherent S receiver model produces0 models even in diagnostic lower-SNR replay.
- Candidate flank fix passed synthetic tests but added60 exclusions in the historical southern-sector control (not independently labeled truth). Bilateral restriction still added53; long continuous restriction made no change. All experimental source/test changes reverted, no new deployment. Need independent weather evidence and a different source-family acceptance path, not further blind threshold tuning. Forensic scripts/patch under `.build/`.
- Live geographic preview y-axis source differs from committed local product.py. Inspect/reconcile before redeploying whole module; do not overwrite this drift accidentally.

## X QC v2 shared-core candidate (2026-09-28, e549510)

- Delivery package `rainpulse-x-qc-v2-20260928/` (x-shared-qc-20260928-v2, gpt-pro) applied on baseline `3757cbb` and committed as local main `e549510`: opt-in `station.x_qc.enhancement` reusing S radial cores with X-scale receiver/clutter/isolated-object policy and independent attenuation/quantitative readiness. Default behavior unchanged without enhancement config; no Go change; delivery dir stays untracked user input.
- Integration deviation: the package's network.schema.json recipe targeted a package-side baseline differing from the committed blob; only that edit was applied manually (enhancement `$ref` inserted at the real `properties/stations/additionalProperties/properties/x_qc` location). All other files applied through the installer's blob-hash guards; rollback receipt `.rainpulse-xqc-v2-backup-271304ccb40e43218ad16f12ff373a7f/`.
- Verified locally: xqc_v2 34 tests; clutter_fusion+receiver_domain 247 (author-side shared-regressions failure was their incomplete checkout); `scripts/test_multiband.sh` (Go/Python/Web); contracts 50; performance AB/CD via official scripts. New-code lint errors are style-class only and left unfixed to preserve payload SHA auditability.
- Deployed to 105 during an idle queue window: `ops-multiband-worker` x2 on `rainpulse-cpu-worker:x-qc-v2-e549510-mb` (from sx-full-extent-c1e30be), `ops-qc-worker` on `x-qc-v2-e549510-qc` (from performance-cd-full-20260927-r1); layered images copy committed multiband source; release dir `.build/x-qc-v2-e549510/` with release.json receipt; all healthy, web 200, rainpulse.service and s-backfill unaffected. Enhancement profiles not generated/deployed; activation needs `make_x_qc_v2_profiles.py` output plus explicit review. No real ZF701 case acceptance yet (manifest `actual_zf701_tested=false`).
- 105 notes: the recorded compose chain referenced deleted `/home/yons/rainpulse-optimized-b3-20260922-stage/optimized-images.yaml`; restored as empty `services: {}` placeholder. The transient `rainpulse-sx-priority1-x-qc` submitter unit is gone while qc-backfill state.json still says RUNNING with 2 queued (updated 06:54Z); inspect before any rerun. `rainpulse-sx-storage-recovery` remains historical failed.

## 2026-09-28 X QC v2 启用 + 预算回归修复（db153c2）

- 用户要求启用 v2 并修 bug。页面实证残留：ZF701 径向辐条、ZF101 海上强回波环（v1 X 质控不清理）。全库排查后定位实质代码 bug：e549510 无条件扩展 `X_QC_FIELDS`（ZDR/VR/SW+各矩掩码）但 `MAX_X_QC_INPUT_BYTES` 仍 512 MiB，双矩 X 站（zf101 实测 40×(360,1998) 全矩解码 625.7 MiB）任何重算都在 `read_x_qc_sweep` 抛 "selected decoded X sweep exceeds memory budget"，与是否启用 enhancement 无关。修复 `db153c2`：常量 512 MiB→1 GiB；`xqc_v2/profiles.py` 生成网络 `maximum_input_bytes`≥1 GiB（schema 上限 2 GiB）；回归测试用适配器自身记账把 zf101 体量钉在预算内（multiband+xqc_v2 共 100 测试全绿）。
- 105 r2 发布：`.build/x-qc-v2-e549510/src` 同步修复后重建 `rainpulse-cpu-worker:x-qc-v2-e549510-r2-mb/-r2-qc`，release.json 已记 r2 块（含验证 run id）。compose 链 = 容器标签里 37 个文件原样（勿再用三文件子集起独立 project，会造出 sx-priority1-* 重复容器；且 `cat >` 原地换挂载网络文件会让运行中旧 worker 立即失配变 unhealthy，必须伴随镜像/网络同步重建）。
- 网络启用（仅 zf701+zf101 加 enhancement，其余站不变）：`.build/x-qc-v2-network-20260928/` 存 parent-v3 备份与 r2 四档（audit/radial/all-cr/all-quarantine，wradlib 后端）。当前挂载活跃网络 = `sx-full-extent-20260828-v3-xqc2-all-quarantine`。真实运行验证：zf701 audit run 6ee7f498（x.v2.pipeline 9 cut 0 错）、radial 5ff4744b（仅隔离 16 门，极化/侧翼证据门保守）；zf101 修复前 b44d30fc FAILED、r2 后 700b0008 SUCCEEDED；all-quarantine 4a6becd4（zf701 拒绝/隔离 2658/2662 与 8315/8382 门）、8d96208d（zf101 155/155、91/91）。native.npz 数值核对：zf701 sweep0 REJECT 972 门分布 98 射线全 ≤30 dBZ（均值 13.5），DBZH_QC 在拒绝门全 NaN。强回波辐条/环按设计保留（强度不单独触发剔除；交付 profiles 的孤立对象固定 audit 模式），更激进清除需按包验收流程离线调参（radial_maximum_dbzh 等）并做冻结输入四档对照，不得为截图直接放宽。
- 质控排查页 http://192.168.28.105:4173/?preset=qc&band=X 复核：全天 QC 数据完整（zf101 79/79、zf701 70/70、zf402 46/46 NORMALIZED）；qc-backfill state.json 的 RUNNING 为死提交器陈旧状态（receipt 到 01-45，后续结果由其他路径完成），保留原状未改。


## 2026-09-28 X 径向质控调参 r3 + 全 X 站扩展 r4（d88469b）

- 用户目标：X 波段径向质控普遍无效（S 波段正常），需修复且不得影响 S（X/S 参数分开）。根因两条：(1) S 接收域核心在 X 上结构性弃权——X 参考块 SNR 实测 mu≈-5dB/p90 散布 4-12dB，对其硬契约 mu≥20dB（ge=20）、spread≤2dB（le=2）不可能满足，config 边界也无法放宽，属设计上只适用 S SNR 语义；(2) v2 径向确认合取过紧——用户帧 zf701 93d2d253 上 1230 几何候选仅确认 84 门（RHOHV≤0.8 只留 40%，抖动≥20° 只留 33%，SNR≥12 只留 67%，双侧翼 8dB/6° 再杀 70% 幸存者；真实辐条 RHOHV 0.63-0.88、弱辐条反差仅 6-13dB）。
- r3 修复（本地 main `d88469b`，纯网络调参无镜像重建）：`xqc_v2/profiles.py` 增 `RADIAL_TUNING`（rho≤.90、jitter≥10°、dbzh<45、侧翼反差 6dB、SNR≥8、侧翼窗 10°），仅写入生成网络的 `station.x_qc.enhancement`；新增 `algorithms/tests/xqc_v2_20260928/test_profiles.py` 2 测试（调参随档携带、守保守边界）。雨的结构保护不变：rho>0.90 永不标记（雨 ~0.95+）、天气保护/代理掩码排除、几何+双侧翼实测+极化证据仍全部必需，强度单独永不触发。
- 105 r3 验证：三窗口重算全 SUCCEEDED（zf701 9c86a900/32772fd3、zf101 6d65fbff）；用户帧拒绝门 2658→25379（9 cut），cut0 2529 门/104 射线全覆盖方位角，无 rho>0.9 误杀，DBZH_QC 在拒绝门全 NaN；map_qc 对比 map_raw：辐条明显稀疏/部分消失、南部降水楔完整保留。最强 spokes 仍有点状残余——证据不足门按设计保留，不得为截图继续放宽。
- r4 扩展：tuned enhancement 从 zf701+zf101 扩至全部 22 个合格 X 站（zf101-105/201-203/401-402/501-505/602-605/701-702/900；排除 zf703/zf801——全天仅 1 条扫描，ray 时间缺陷）。网络 `.build/x-qc-v2-network-20260928/profiles-r4/`（all-quarantine SHA fea09e38…），活跃网络已切换；三 ops workers 重建健康（注意：compose up 会丢 multiband 副本 2，须 `--scale ops-multiband-worker=2` 恢复）。抽查：zf702 run d944c0b3、zf605 run 6ea71dc9 全 SUCCEEDED；zf702 cut2 径向模块确认 6187 门（新站生效），原始图无成片降水（仅辐条/点杂波/发散条带楔）故无降雨可伤，强楔形按设计保留；zf605 干净站三卷仅 33/0/19 门动作。S 四站无 enhancement、radar-qc workers 未重启。
- 回执与回滚：`.build/x-qc-v2-e549510/release.json` 已记 r3/r4 块；r3 切换前备份 `active-r3-backup-20260928T150141Z.json`（SHA d312dd65…）。遗留：ZF101 海上强回波环为孤立对象 audit 模式，需单独策略决策才可行动。


## 2026-09-28 X 径向片段补全 r5（8f8c79d）：S 关联契约移植

- 用户追问"是否需要重新出图；S 用 scikit-image 识别径向形态并去除效果好，X 是否一样；彻底解决"。排查：页面 result=cd62b6f0 是 06:03Z v1 旧结果（该 scan 最新结果当时为 14:48Z f1f3174a），无需重新出图，切结果选择器即可。S/X 差异：S 的径向清除靠 fragment 阶段闭环（`fragment_radials.associate_fragments`：锚=已拒绝的 RFI 门，同射线关联目标门仍需本地极化证据 + 距离律强度匹配，"identity never confers pollution"；本地极化用 PHIDP 圆方差≥0.085），另有 broad/narrow source 等模型级覆盖；X v2 只复用了 scikit-image 形态候选提取 + 逐门确认合取，无补全环节——这就是残余点状辐条的来源。
- r5 实现（本地 main `8f8c79d`，新 `xqc_v2/fragments.py`）：锚=确认合取门；目标门需 X 极化合取或 S 式 PHIDP 圆方差、共享 rho/snr/dbzh 上限、距锚 ≤15km、20log10(r) 距离律差 ≤8dB、每射线锚门数 ≥4；rho>0.9 永不可关联（雨结构排除），缺测门不伪造。默认 `fragment_maximum_distance_m=0` 关闭，仅 profiles 显式启用；S qc_engine 零改动。测试 109 全绿（新增 test_fragments.py 7 例 + profiles 边界扩 fragment 断言）。
- 用户帧 93d2d253 离线复算：径向路径覆盖 9701→19885 门（9 cut），总隔离 25379→32609，10184 补全门 rho>0.9 污染 0（注意掩码已还原原始行序、rho 在排序序，核对必须先做 order 映射，否则假阳性）；无 ACTION_BUDGET 弃权。105 r5 部署：镜像 `x-qc-v2-e549510-r5-mb`(025ccce6)/`-r5-qc`(5e0d62ee)（src-r5 构建上下文 build-r5；compose 引用的 r2-mb/r2-qc 标签现指向 r5，r2 回退保留为 -r2-keep/-r2-qc-keep）；网络 profiles-r5 all-quarantine（SHA c51f9005…，22 站），r4 切换前备份 active-r4-backup-20260928T153720Z.json；三 ops workers 重建健康（S workers 未动）。重算 run 1449f93e（zf701 00:00–00:15Z）SUCCEEDED，线上数字与离线逐一相同（32609/9701/10184）；map_qc cut0：0°/40-45°/105-115° 辐条基本消失、杂波环大部分清除、南部降水楔完整保留；225°/290-295° 仍有稀疏点状残余 = rho>0.9 或无极化佐证门（cut0 共 537 个），按降雨保护契约保留，S 同样不剔此类门。
- 对用户口径：质控图随 result 工件走，页面选最新 result 即为 r5 效果；"彻底清除"受证据边界约束——无目标门本地极化证据的辐条段不能剔（会伤雨），这是与 S 一致的设计底线。

## 2026-09-28 X 噪声底 censor + 单侧翼 r6（98f919c）：07bdd490"镜像"伪影根因与清除

- 用户对 r5 提出异议：07bdd490（00:03:32Z，r5 结果 2cb2cd37）质控图"还是各种镜像"。取回该 scan r5 产物逐门分解，找到两个 r5 未覆盖的失败类，并否定两条放宽路线（证据地板）：
  - **主因：噪声底被当回波显示**。SNRH<2 门的 DBZH 中位数随距离精确爬升（10-20/20-40/40-60/60-75km → 4.5/10.5/15.5/17.5 dBZ）= 处理器噪声底+20log10(r) 映射；占 cut0 观测门 31%，方位成扇区结构（0-20°/130-140°/180-220°/290-300°），就是页面上的"镜像扇形/点线"。真信号（snr≥8）只集中在真实雨区。
  - **双侧翼被干扰渗透卡死**：radial&polar 门上 flank 通过率仅 2.3%（X 干扰渗入相邻射线）；"either" 单侧翼实测雨区暴露 4 门（彻底去掉 flank 为 64 门）。
  - **否定的路线**（写档防止再走）：SNRH 是距离代理（雨区 z≥15 门中位 30-50km 6.0、50-75km 3.0），放松 snr 等于无界；长距雨 rho 真实 0.70-0.74 与干扰重叠、37% 雨门 rho≤0.6，rho 上限不可再收；snr≥8 雨射线同样紧贴 20log10 律（p90 中位 3.0dB）且极化异常比例 0.34-0.41 与点状辐条（0.34-0.79）重叠——**断续强信号辐条（10°/30°/105°/320° 约 1267 门）在此雷达矩量上与雨统计不可分**，为已定档证据地板。
- r6 实现（本地 main `98f919c`）：`noise_censor_snr_db`（默认 None=S 原行为；观测&SNR 可用&低于门限&非硬保护 → 非气象拒绝）+ 完整性护栏（coverage<0.5 或占比>0.8 整体退避，SNR 场损坏不误删）+ 独立于启发式 ACTION_BUDGET（校准底线非启发式）；`radial_flank_mode: both|either`（默认 both）。NOISE_FLOOR=16384 原因位、XQC_NOISE_FLOOR_MASK；RADIAL_TUNING += either + censor 3dB。S qc_engine 零改动。xqc_v2 套件 53 全绿（新增 test_noise_censor.py 7 例 + test_radial_flanks.py 3 例；本地全量除 measurement_v8——sklearn 缺失为既有环境问题）。
- 验证：07bdd490 cut0 拒绝 753→9154（censor 7183/radial 579/frag 857），形态学辐条 ≥12dBZ/25km 1→0，censor∩snr≥8=0，雨区（165-210°,snr≥8,z≥15）移除 0/351；93d2d253 cut0 3162→9490、雨区 0/9176 移除、map 视觉"接近干净"。zf605 净空站安全：空 cut 正确退避（ABSTAINED_SNR_FIELD_INVALID），有观测 cut 移除 30-60% 亚门限斑点（即净空杂点），无崩溃。
- 105 部署：镜像 `x-qc-v2-e549510-r6-mb`(a01b7162)/`-r6-qc`(a5a60d59)（build-r6/src，本地 98f919c 树），compose 引用名 r2-mb/r2-qc 重打为 r6；r5 保留 -r5-mb/-r5-qc（及 r5-pre-r6-*），r2 原始 -r2-keep/-r2-qc-keep。网络 profiles-r6 all-quarantine（SHA 79eb09af…，22 站），r5 备份 active-r5-backup-*.json；三 ops workers 重建健康（S workers 未动）。live run 12b9be68（zf701 两卷）SUCCEEDED，数字与离线逐一相同（9154/7183/579/857 与 9490/6125/431/815）；zf605 安全 run e43cc388 SUCCEEDED。视觉评级：07bdd490 残余 ~2/10（0° 一条断续中等辐条=证据地板类+弱短点线），93d2d253 ~1/10。回滚：r5 标签回打 r2 名 + 网络恢复 active-r5-backup + workers 重建（--scale ops-multiband-worker=2）。release.json r6 块已记。
- 对用户口径：用户看到的"镜像"主体是噪声底渲染 + 双侧翼卡死的强辐条，r6 均已消除且雨区零误删；残留断续弱辐条属于该雷达矩量与雨不可分的证据地板（S 在同类无证据门上同样保留）。


## Current S/X mainline: Priority 1 (2026-09-27)

- 2026-09-28 ~06:01Z follow-up: S and X failed decode recovery verified,
  control + separate cmd/orchestrator binaries both built from42f8cb1.
  Four batches resumed, but legacy X QC run e56dbcd1 had2 COMMITTING
  attempts from storage-full window on old dead worker IDs. API recover
  found no committed marker. After checking expired leases/old workers absent,
  used supported abandon action to mark failed, then normal retry was blocked
  by v2→v3 network drift. Preserved old receipt as
  qc-backfill/00-45-zf604.failed-worker-lost-v2.json; created new v3 plan
  ca68c75e and run884e84a9, both tasks SUCCEEDED, runner resumed from
  00-45-zf604 checkpoint. At~06:01Z X QC72 batches and advancing; X import
  111station-hours, S46station-hours waiting capacity, composite0 waitingQC;
  all four units ACTIVE. MinIO free~290GiB/108M inodes; RAM~13GiB available.
  Continue monitoring resource budget and validate outputs. Old failed run
  is retained; no SQL state changes or fake success. Historical
  storage-recovery unit STOPPED_ERROR remains from missing transient x-qc unit,
  which was recreated then started; rely on fresh batch states.

- 2026-09-28 05:55Z: source/main/105 control 42f8cb1. A narrowly gated
  draft-S failed decode rebuild now accepts only frozen raw URI/SHA, same
  config version, FAILED scan and FAILED original decode job. Go unit tests,
  controlplane/operations/orchestration/postgres tests, vet, and isolated
  PostgreSQL integration test passed. Both server and separate orchestrator
  binaries built from this commit; do not copy the root server binary over
  cmd/orchestrator again. 105 S scan93a30121 rebuilt to NORMALIZED via job
  31f85290-1443-5b13-b228-adc931e9e2a2 SUCCEEDED, original job62c43a4d
  retained FAILED. Receipt storage-recovery/decode-93a30121...json.
- Storage recovery launched X import and S backfill, then failed because
  transient rainpulse-sx-priority1-x-qc unit had been garbage-collected.
  Recreated it with systemd-run, MemoryMax4G/CPUQuota150%, then restarted
  composite unit. At05:55Z all four units ACTIVE: X import103station-hours,
  X QC71 batches, S46station-hours waiting QC, composite waiting QC0.
  Those are starts/checkpoints, not full-day acceptance. Recovery unit state
  remains historical STOPPED_ERROR for the missing transient unit; inspect
  active batch unit/checkpoints before rerun. MinIO free~294GiB, inodes108M.

- 2026-09-28 03:46Z follow-up:10/10 existing catalog frames now verified as
  full-grid v3 (UTC00:06..01:00). Regrid state COMPLETE at03:59:31Z;
  each catalog manifest has the same full geographic bounds and each receipt
  SUCCEEDED. Storage~295GiB
  free/108M inodes. Independently recovered storage-failed X scan8f7da415 via
  normal draft-X decode rebuild: job32ca4ec4-ca71-5d41-99f3-e921286e151d SUCCEEDED,
  catalog NORMALIZED with URI, receipt storage-recovery/decode-8f7da415-...json.
  Old failed job retained; reviewed-failures ledger links successful replacement.
  Unit rainpulse-sx-recover-x-decode finished. Recovery state now
  WAITING_S_DECODE_RECOVERY; remaining S decode restriction still needs a
  tested recovery path. Do not blindly restart storage-recovery before fixing it.

- LATEST 2026-09-28 full-extent fix: user requests complete uncropped composite.
  Source/main/105 control+Web c1e30be; multiband image sx-full-extent-c1e30be
  derives from 41ea4b2 with only Grid budget patch (performance code preserved).
  Network grid v3 EPSG:32651 [-553000,2417000,685000,3494000],1238x1077/1km
  contains native range+1km for all26 usable stations (3600 bearings each).
  Python2/Go multiband/Web7 tests and build passed. Map fits product bounds.
  Initial real-data run7cb0d9d3-494a-4327-ae1c-01ea9b4e2649 SUCCEEDED
  in608.7s; old-grid-outside valid cells S6317/X861/S+X7169, exact fmax.
  Live1920x1080 S/S+X comparison verified; map bounds111.9894,21.5459,
  124.9493,31.5810. Receipt full-extent-validation.json on105. All10 existing frames
  regenerated successfully UTC00:06..01:00; full-day backfill still pending. Check
  .build/sx-priority1/full-extent-regrid/state.json and receipts; script
  /tmp/sx-full-extent-regrid.py. Verify arrays outside old625x656 grid, map
  full range and S/S+X UI before acceptance. Existing clipped assets retained.
- Model relocation COMPLETE:83 files/298906239661 bytes SHA256 verified,
  original path bind-mounted persistently; old verified duplicate removed.
  Data free recovered296GiB. Original MinIO overlay unchanged. Storage recovery
  parser fixed to read structured rebuild receipt amid BDP startup logs;
  4failed S QC replacements SUCCEEDED. Recovery then found2decode failures
  (verified XMinioStorageFull artifact_publish in worker logs): S scan
  93a30121-d5bd-50f4-940d-44766f8c1bd7/job62c43a4d-f864-5d05-9d48-299ac941dbac
  and X scan8f7da415-e838-5460-a698-b0cb07e6275b/jobddadc54f-b537-5407-ae0c-8b857a65c576.
  Recovery script extended for normal decode rebuild but STOPPED_ERROR:
  current API restricts rebuild to draft X, so S submission was rejected.
  No original failed state changed. Next must add/test narrowly scoped failed-S
  decode recovery or use an existing sanctioned regeneration path; do not bypass
  lifecycle/SQL states. X rebuild pending behind this S check. Batch units remain
  stopped; full-extent regrid is independently running successfully.

- LATEST 2026-09-28: user explicitly authorized Qwen3.5-9B-MTP-GGUF
  relocation preserving old path. Original MinIO upper/mount unchanged; failed
  staging copy fully removed, root inodes recovered~620k. Do not resume old
  MinIO staging. New `rainpulse-model-relocation.service` copied279GiB/83files
  to /home/yons/hwapp/models/Qwen3.5-9B-MTP-GGUF and is SHA256-verifying both
  copies. It switches original path with bind mount/fstab and deletes only
  verified old duplicate. State/hash receipts under rainpulse-minio-storage/
  model-relocation.json and model-sha256.json. COMPLETE required before claims.
- `rainpulse-sx-storage-recovery.service` waits for model COMPLETE, tests real
  S3 put/get/delete own probe, restarts idle S QC workers only if queue empty,
  rebuilds4 verified storage-failed S scans through normal API, then resumes
  batch units in dependency order. Script .build/sx-priority1/storage-recover.py,
  checkpoint storage-recovery/state.json. Currently WAITING_STORAGE.
- Source/main/105 control and Web now cb73a87: frontend selection, resolution
  and probes support32 stations with unchanged4-way backend concurrency.
  Regression tests first failed on16 limit, then Go operations and7Web tests
  passed; Web build passed; actual105 browser selected28/32 with no alerts.
  Historical worktree rainpulse-sx-priority1-followup beside main (old/tmp removed); superseded by the single-directory rule above.
- X QC runner now includes next-midnight00:00–00:06 tail, waits on source23h
  checkpoint; four runners check actual MinIO disk bytes AND100k free inodes.
  Resumed state clears stale error text. Keep failed-storage receipt before
  retrying X import04/ZF501. Storage recovery handles this preservation.
- Capacity follow-up:187 recent X QC results average63.26MiB/max84.13MiB;
  model relocation restores~279GiB but does not prove capacity for entireday.
  Continue bounded processing; estimate whole-day remaining footprint and
  address capacity before hitting reserve, without unauthorized data deletion.

- STORAGE CORRECTION 2026-09-28: staging FAILED because root filesystem inode
  count reached100%, despite547GiB free bytes. Original MinIO source/mount
  untouched. Cleanup of ONLY unactivated new storage/upper copy is running;
  inode/free-space recovery verified, control7307d05 remains ready. Do NOT
  restart stage unit or trust old COPYING JSON. Original upper measured438GiB;
  direct migration cannot retain120GiB reserve plus room for all-day outputs.
  User approval requested for relocating unrelated Qwen3.5-9B-MTP-GGUF279GiB
  to root disk preserving old access path, or another storage choice. Await
  answer before touching models. All batch submissions remain stopped.
  Heartbeat updated to enforce this correction. Capacity guards must check
  both free bytes and inodes on actual MinIO filesystem before resuming.

- 2026-09-28 storage blocker: MinIO overlay upper is on a different filesystem
  from deployment root. Its disk is99% full (18GiB free); root has573GiB free.
  X import stopped at04/ZF501 due XMinioStorageFull;4S QC jobs also failed
  publication. S46 station-hours/X98 station-hours complete; X QC71 batches,
  full-day composite0. All batch submitters are stopped; control7307d05 ready.
- All four operational runner guards now check actual upper filesystem rather
  than deployment directory, retaining120GiB reserve. Storage evidence is
  `.build/sx-priority1/storage-blocker.json` on105. No data deleted.
- Authorized non-destructive migration staging is ACTIVE in systemd unit
  `rainpulse-minio-storage-stage`: copies old upper with rsync -aHAX to
  `/home/yons/hwapp/rainpulse-minio-storage/upper` on root disk, stops below
  120GiB reserve, preserves old source. State/log are migration-state.json and
  rsync-stage.log in that new directory. Script /tmp/sx-storage-stage.py.
  COPYING or STAGED_REQUIRES_FINAL_SYNC is NOT migration completion.
- Next: inspect stage outcome; controlled final sync with writers quiesced,
  preserve overlay whiteouts/xattrs, update mount/fstab with rollback, verify
  S3 read/write and old assets, repoint capacity guards, then rebuild4failed S
  jobs normally and resume retained batch checkpoints. Do not remove unrelated
  model directories or original source data. Migration not yet switched.
- Old /tmp/rainpulse-sx-completion worktree was removed externally; use this
  original main for reading and a new isolated worktree for code modifications.
  Current-task heartbeat updated with storage recovery and this durable path.

- User requires Priority 1 (source completeness / full-day availability / per-frame
  evidence) to finish before Priority 2 (QC and fusion effect quantification).
  Do not mix algorithm tuning, QPE/forecast enablement or unrelated redesign.
  Acceptance and remaining work: `docs/SX_PRIORITY1_20260927.md`.
- Source work is in `/tmp/rainpulse-sx-completion`; changes are fast-forwarded to
  local main and pushed. Preserve unrelated dirty files in the original main.
- All 5,466 X file headers audited; 24 candidate station configs created with
  complete observed moment mapping. 22 stations have normalized real samples;
  ZF703/ZF801 remain failed due to native subsecond time reversals. Do not guess
  time units or claim these are valid/verified stations.
- Twelve S QC rebuilds succeeded; repaired three mixed-generation cycles and
  verified source-probe diagnostic coverage 106/106 existing analysis times.
- Source main/origin includes `7307d05`; 105 control and Web are `7307d05`.
  Full-network config and 1 km experimental regional grid remain active.
  Decoder image `sx-priority1-decode-3d460f4`: 2 replicas, 8 GiB/2 CPU each,
  packed normalized storage enabled. Multiband `sx-priority1-multiband-41ea4b2`:
  2 replicas, 6 GiB/2 CPU each. Preserve existing S QC performance workers.
- All 22 usable X station representative QC samples passed, including fixed
  ZF504 duplicate bearings. Packed X and S real-data QC passed. Full-network
  run `af9ebd66-22ef-4347-853b-0b64c1e709fe` passed numeric/source checks:
  requested28 = used23 + skipped5; S+X exactly fmax(S,X); all 14,507 X-winning
  cells uncertain. Candidate horizontal composite is not trusted fusion.
- 105 active bounded systemd units: `rainpulse-sx-priority1-x-backfill`,
  `rainpulse-sx-priority1-x-qc`, `rainpulse-sx-priority1-s-backfill`,
  `rainpulse-sx-priority1-composites`. Scripts/checkpoints/receipts are under
  `.build/sx-priority1/{backfill,qc-backfill,s-backfill,composite-backfill}`;
  script names respectively backfill.py, qc-backfill.py, s-backfill.py,
  composite-backfill.py. UTC source day 2026-08-28 spans BJT Aug28 08:00 to
  Aug29 08:00. X imports target24hours (state.hours=1 is old milestone).
  Units reserve120GiB and stop on failures; do not blindly restart without
  inspecting retained receipt. Composite waits for each hour's S/X QC.
- At 2026-09-27 09:02Z: X raw imports49 station-hours complete (hour02),
  X QC18 quarter-hour station batches complete, S40 station-hours complete
  (hour10 waiting QC), composite waiting first-hour QC. All-day NOT complete.
- 10:05Z follow-up: new ZF401 03:12:19Z scan failed with `cut 2 radial
  times are not monotonic`, job bff93645-5f8b-5e2b-8a96-842db95e240b.
  Retained FAILED; explicit review ledger `backfill/reviewed-failures.json`
  allows subsequent imports without suppressing new/unreviewed failures.
  Backfill guard now subtracts only reviewed job IDs. X QC resumed at ZF503.
  S reached44 station-hours, X76; no full-day acceptance yet.
  Available RAM fell to7GiB; radar-qc-worker-2 retained51.5GiB after completing
  all QC jobs (SQL pending/running radar.qc=0). Restarted only that idle
  container preserving image/settings to release retained memory. Monitor
  recurrent growth before any algorithm/performance configuration change.
- Full-day composite catalog/timeline fix deployed: latest non-retired result
  per analysis time, max1500 frames/24h. Isolated PostgreSQL240-frame test,
  seven targeted Web tests, Web build and Go operations tests passed. Live
  catalog returned10 unique existing frames; new full-day products pending.
- Next P1 work: monitor/fix full-day runs, handle X next-midnight QC tail,
  generate S single-station diagnostics beyond old106 cycles, resolve16-layer
  UI/API selection/probe cap versus28 stations, verify every frame's sources,
  missing/age and numerical identities, both BJT dates and browser playback.
  ZF703/ZF801 format docs are optional pending user info, remain excluded.
  Raw extra S Z9595 is outside configured4S network; do not silently add it.
- Hourly current-task heartbeat `rainpulse-s-x` is active for authorized P1
  follow-through; quiet unless meaningful milestone/failure/input/completion.
  Do not alter unrelated NowcastNet automation `rainpulse`. P2 only after P1
  acceptance. Temporary PostgreSQL DB `sx_priority1_test_20260927` remains.

## S/X multiband candidate (2026-09-24)

- 2026-09-26 correction: X QC now uses the same two basemaps as S, with
  raw/QC/flag echoes sampled from native site, azimuth, per-ray elevation and
  gate ranges onto EPSG:4326 (WGS84 geodesic, 4/3-Earth ground geometry).
  The separate third station map is removed. Both bands' range-ring labels
  are numbers only; X ring spacing follows the actual sweep range. Map geometry
  and immutable asset URLs are attached to each result/sweep; old results need
  “刷新资料” to load the newly generated map result.
  Source is merged into local main through `076e568`. On 105 the Go service
  reports `17d3b1eaf374` (ready), and the healthy candidate Worker image is
  `rainpulse-cpu-worker:x-map-17d3b1e`. Four ZF101/ZF505 scans at 00:00/00:06 UTC
  succeeded in run `48b87c0c-21dd-4c15-86ee-cdbf24fe8ad5`, publishing map assets
  for all 98 sweeps. The final Web index SHA-256 is
  `0ceeb7c5d2aa9a7b1b4503af5815d1626288e66dab2cd36c3ae9de3d3f8bb6a5`.
  Numerical cardinal-direction/mask tests, Python multiband tests, Go test/vet,
  generated contracts, Web tests/build and real-browser checks passed.
  Native site coordinates remain unverified; candidate map display does not
  enable cross-station overlay, trusted fusion, QPE, or forecast eligibility.
  Runtime rollback is `.build/rollback-x-map-17d3b1e`; previous Worker image
  is `rainpulse-cpu-worker:x-shared-5aa638b`.
- Unified S/X QC workspace design and phase-A Web implementation are on local
  `main` through `a44e105`. The QC route now uses one shell, one time axis,
  native S/X station and sweep selection, paired raw/QC views, and candidate
  gating for overlay/fusion. See `docs/UNIFIED_RADAR_QC_WORKSPACE_IMPLEMENTATION_20260926.md`.
  Web lint (0 errors, 6 existing warnings), 140 tests, and build passed.
- On 105 (`192.168.28.105`), the Web dist built from `a44e105` was deployed
  in place; `index.html` SHA-256 is
  `e65ff3f083c313ef73636dcf5597f6658ae974a5ec9df4b72b015c2eaff3aaa1`.
  The prior dist is at `apps/web/.dist-rollback-before-a44e105`. Browser
  verified the 2026-08-28 ZF101 X pair, S Z9591 at 08:06 BJT, band switching,
  and one-map overlay gate; service remained active. No Go or Worker restart.
- X site metadata remains unverified; single-station candidate map display is
  enabled, while cross-station overlay and S/X fusion remain gated. The current S timeline indicates analysis cycles, not
  per-station raw/QC availability; a Go summary contract is needed for that lane.

- 2026-09-25 UI integration candidate adds standalone productless X QC
  previews and S-only, X-only, S/X, and jointly-valid X−S outputs for one
  frozen six-minute task grid. The workspace links to the comparison task
  flow; task details render four projected-grid quicklooks. This is not the
  planned product catalog or a georeferenced workspace-map layer.
- Candidate is being finalized in an isolated worktree on top of
  `origin/main` `98ebe0c`. `bash scripts/test_multiband.sh`, Web test/build,
  and Ruff pass. PostgreSQL integration is skipped without a disposable
  database; full Go test/vet/build, release gate, push/CI, and 105 deployment
  remain outstanding.
- 105 has no verified X geometry/MSL/frequency/calibration config. Publish
  candidate code only; do not enable spatial X fusion or claim real-data
  acceptance until the 20260828 paired S/X volumes pass numerical and resource
  checks. Do not modify the dirty 105 source checkout.

- Performance batch 1 was merged to `main` at `47c7876` on 2026-09-25.
  On 105, both active radar QC Workers run
  `rainpulse-cpu-worker:qc-perf-batch1-47c7876` with the grouped CF statistics
  update and reported healthy after replacement. The separate image
  `rainpulse-cpu-worker:multiband-perf-batch1-47c7876` passed import and NumPy
  warmup checks, but the multiband pool remains DRAINING with no Worker or
  trusted X spatial network. Streaming is not enabled; no real-data speedup
  or multiband operational acceptance has been established. The 105 source
  checkout remains dirty and older than `main`; runtime images were built from
  the committed source without overwriting that checkout.

- Historical S/X candidate code is on `main` through `dd691f3`;
  `docs/SX_20260828_INTEGRATION_PLAN.md` records current scope.
  On 105, the unified Go process reports `dd691f3`; the decoder Worker uses
  `rainpulse-cpu-worker:sx-candidate-20260924` and is healthy. Two ZF101 and
  two ZF505 representative X volumes are `NORMALIZED`; the matching S volumes
  already existed in the earlier QC chain. X configs remain draft, and no X
  spatial network, trusted QPE or multiband Worker has been enabled.
- Source commit `5ecb44a` is on `main`; `docs/MULTIBAND_V1_20260924.md` records
  the candidate-only scope and acceptance boundary.
- On 105, Web assets from `5ecb44a` and unified Go from `dd691f3` are live; the
  API reports ready. Operations schema is v4 with the `multiband` pool DRAINING.
  Candidate image `rainpulse-cpu-worker:multiband-5ecb44a` was built and its
  module imports checked, but no X network config or multiband Worker is active.
- The 105 source checkout contains pre-existing uncommitted changes and is not
  synchronized with `main`. Deployment updated runtime artifacts in place;
  original binary/Web index backups are in `.build/multiband-rollback` there.
  Do not infer real X data readiness from the candidate deployment.

## Architecture batch 2 (source-only handoff, 2026-09-22)

- Based on merged main `eb99368c07d9fce6bb41a1f2b1e46a5f005d845a`.
- Bounded immutable-byte caching, verified selective reads, and opt-in CPU
  realtime/background queues with explicit container budgets and frozen routes.
- No 105 deployment, measured operational speedup, or weather-algorithm change.
- Read `docs/ARCHITECTURE_BATCH2_20260922.md` before activation. Preserve the
  active near-radar profile. Old packed assets retain full verification.

## Architecture batch 1 (source-only handoff, 2026-09-22)

- Bounded planner reads, version-independent QC completion summary, native
  unified admission pause/release verification and a single Compose entry are
  supplied as a baseline-checked patch against `3d84719eb73750d3fa2fa0dd6541eded606c7d52`.
- No 105 deployment or algorithm-threshold change is implied. Keep the latest
  near-radar/QC configuration and operational eligibility unchanged.
- Read `docs/ARCHITECTURE_BATCH1_20260922.md` for installation, rollback and the
  distinction between isolated tests and required full-stack acceptance.

## Current source state

- Repository: `https://github.com/dc365/rainpulse.git`.
- Local source of truth: `main`; before follow-up work, refresh `origin/main`
  and use its current commit as the delivery baseline.
- The active architecture baseline is
  `docs/RainPulse_技术架构与实施方案_含雷达质控_v1.1.md`.
- The current realtime-workspace implementation record is
  `docs/RP041_福建实时影子链路与统一工作台实施记录.md`.
- Manual regeneration is recorded in
  `docs/RP044_统一算法数据手动重生成实施记录.md`.
- User-owned untracked files currently present and excluded from normal commits:
  `docs/report/20260831.md`, `rainpulse-feat-ui-overhaul.patch`, and
  `rainpulse-ui-overhaul-round2.patch`. Do not delete, stage, or modify them
  unless the user explicitly asks.

## Prelaunch convergence

- 2026-09-18: Local fragment-line-v3 adds isolated missing-flank morphology,
  bounded30km same-ray fragment identity (no gap filling), valid-ray/weather
  barriers. 08:42 source-stage projection Z9591 north890→0, Z9598 selected
  rays808→0/482→7; another373-gate ray remains. 243 tests pass. Not deployed
  or committed. See QC docs FRAGMENT_LINE_20260918.md and v3 child profile.

- 2026-09-18: User-authorized fragment-line-v2 adds independent measured
  bilateral-edge morphology quarantine. Frozen22-layer source-stage replay:
  Z9591 adds842 gates, Z9598 adds447 (lowest70) vs deployed step3. Missing
  flanks stay unknown; several weak target lines remain. Profiles/tests/docs
  prepared locally, not deployed. See FRAGMENT_LINE_20260918.md in QC docs.

- 2026-09-18: Local opt-in fragment-line-v1 adds scikit-image Hough/RANSAC
  nominations and strict held-out strong coherent-line evidence. 22 frozen
  sweep source-stage replays: Z9591 lowest cut adds776 isolated eligible gates
  over e9241cd step3; Z9598 adds0. Not deployed/full-worker acceptance.
  See docs/radar-qc-opensource/FRAGMENT_LINE_20260918.md. Unrelated dirty work
  remains untouched; production stays radial step3 until explicit rollout.

- 2026-09-17: Uncommitted candidate7.3.6 integrates near-range targets and
  source-coherent sector morphology with unified quarantine. 62 tests pass.
  105 four full-volume replays complete,11 sweeps each: Z9591 red sector
  10–50km >=30dBZ remains347/2529; Z9598 lowest-cut new exclusion0.
  Live still7.3.4; no promotion. See docs/质控_7_3_6_近距离扇区接入与完整回放_20260917.md.

- 2026-09-17 deployment: QC 7.3.4 now live on105 (source9dd040d, build fix73aa24b).
  Four QC workers and diagnostic worker healthy; planner uses source-edge profile.
  Batch75f75a97-1725-4652-b855-ce810d74bf8c SUCCEEDED:8/8 scans,2/2 PNG bundles
  for Beijing10:42/10:48. API image versions/hashes verified. Other times and
  grid/QPE/forecast unchanged. Geometry limitation and10:42 residual remain.
  See docs/质控_7_3_4_边缘关联接入与完整回放_20260917.md.

- 2026-09-17: Candidate 7.3.4 source-edge one-hop association integrated with
  weather/conflict/SNR protection and unified quarantine. 58 tests pass. Four
  full-volume raw/context compute replays on 105 complete (11 sweeps each);
  lowest-cut high residual Z9591 10:42/10:48 = 341/39, Z9598 controls no new
  lowest-cut exclusion. Geometry config unavailable in frozen deployment context;
  no truth acceptance. Not committed/published, live remains 7.3.1. See
  docs/质控_7_3_4_边缘关联接入与完整回放_20260917.md.

- 2026-09-17: QC 7.3.3 source-constrained scikit-image radial opening implemented
  as an opt-in candidate. 105 lowest-cut replay: Z9591 10:42/10:48 high-domain
  residual 341/1596 (7.3.2: 1230/6558); Z9598 controls add zero isolation.
  55 relevant tests pass. Not full-volume acceptance; live remains 7.3.1.
  Code uncommitted. See docs/质控_7_3_3_长条开运算实施与回放_20260917.md.

- 2026-09-17: Opt-in QC 7.3.2 distance-conditioned cross-ray polar reference
  implemented locally. Four lowest-cut incremental replays on 105 completed:
  Z9591 10:42/10:48 high-domain residual 2665→1230 / 17915→6558;
  Z9598 controls add zero isolation. 10:48 requires review; independent weather
  evidence unavailable. Not full-volume acceptance or production promotion;
  live remains 7.3.1. See docs/质控_7_3_2_距离极化参考实施与回放_20260917.md.

- 2026-09-17: QC 7.3.1 shared paired-moment range term implemented as an opt-in
  broad-source candidate; target/guard blocks held out, independent ray groups,
  explicit fallback and observed-range coverage. 53 relevant tests pass. Four
  lowest-cut incremental replays on 105 match research: Z9591 10:42/10:48 high
  diagnostic-domain residual 2665/17915; Z9598 controls add no isolation. Full production recompute on 105 subsequently completed for these two times:
  8/8 scans and 2/2 diagnostic sets; live API layers identify 7.3.1 and PNG hashes
  verified. This is not meteorological truth acceptance. Code committed.
  Priority completion batch: 428654db-68d2-4a26-a514-bb4a222406fd. Previous
  full-day 7.2 batch superseded/stopped; other times were not recomputed here.
  See docs/质控_7_3_1_距离项实施与回放_20260917.md.

- 2026-09-17: P0-P2 opt-in QC candidate 7.2 prepared on the c3e768 baseline.
  Separates CONFIG_NOT_READY-only administrative status from physical QI penalty;
  retains fitted broad-sector measurements and reviews OC1 coherent self-protection;
  isolates supported budget-overflow proposals while surfacing manual-review status.
  Original configs frozen. 1062 Python/config/contract tests pass, 3 native Emitter
  tests skipped. Nine sanitized lowest-cut target-stage replays completed; NOT a
  full historical-context rerun or independent weather acceptance. No 105 deployment.
  See docs/radar-qc-opensource/GENERALIZATION_P0P2.md and its validation companion.

- 2026-09-16: Local timeline/data-chain implementation now uses 6-minute cadence:
  observed -60..0, forecast +6..+180 (41 fixed slots). LK/STEPS 30 leads;
  NowcastNet retains native 10-minute protocol and adapts to 20 display leads
  through +120 only. Cross-origin accumulation combines past QPE with future
  model output; no missing-as-zero. Migration 0022 required on deployment.
  Not yet deployed or regenerated on 105. See `docs/TIMELINE_6MIN_3H_20260916.md`.
  Changed runtime profiles have distinct `-6m180` identities; five-minute input
  filenames were renamed to `6min`. Frozen RP016/RP024 scientific profiles remain
  unchanged for MRMS reproducibility, not as parallel live product versions.

- 2026-09-16: Historical admin recomputation now uses durable QC-only batches
  (migration 0020, `/api/v1/admin/qc-batches`). Old forecast_all panel requests
  cancelled; 105 batch 3389d299-6a7f-4dde-b7ad-d67e96c57218 covers 452 scans
  and 123 display times. QC and diagnostics only, no grid/QPE/forecast.
  QC completed 452/452; display failed on legacy v1 flag compatibility.
  Fixed renderer + migration 0021; display-only repair batch
  a3ac4b79-e438-465c-94ca-f34f24bc7d3d reuses QC and successful display jobs.
  Diagnostics regeneration uses its own completion-marker prefix. 09:05/09:10
  new images verified; remaining displays running. See docs/HISTORICAL_QC_ADMIN.md.

- 2026-09-15: Height experiment completed (98 pairs; third frozen donor missing).
  Stable positive support 2511 gates. Paired lowest-cut QC replay on 105 completed:
  zero action/visibility changes, Z9598 08:35 SW window remains 6404 gates;
  stable support has zero overlap there. Do not promote or pursue height support
  as this case's residual-removal fix. See HEIGHT_SENSITIVITY_20260915.md.

- 2026-09-15: Offline height sensitivity launched on 105 as
  rainpulse-height-sensitivity-20260915 (2 CPU/6GiB), Z9598 08:35 scan
  5172b858-0406-5498-89fd-16db363507d1. First donor completed two of 49
  height pairs; second pair has 1635 comparable / 1290 echo-support gates.
  These are scenario counts, not stable/accepted gates. Production untouched.
  See docs/radar-qc-opensource/measurement-v8/HEIGHT_SENSITIVITY_20260915.md.

- 2026-09-15: User confirmed all four station/antenna heights use China's
  1985 national height datum (EPSG:5737), not EGM2008. New local versioned
  configs are under configs/radars/fujian-1985-20260915; not selected online.
  Trusted cross-radar support remains blocked until a traceable conversion to
  DEM EGM2008 is available. Availability audit fix is local/uncommitted.
  See docs/radar-qc-opensource/measurement-v8/CROSS_SUPPORT_HEIGHT1985_20260915.md.

- 2026-09-15: V7 maintenance r2 deployed on 105 (4 healthy QC workers only).
  Final projection refactor, graph capacity fallback, stage timing and texture
  reuse included; V8 and weather split remain disabled. 08:45 run
  7422e03a-b259-5ab2-bf7f-55df4b475e37 started with 20 QC tasks RUNNING;
  background script then submits 08:30. See
  docs/radar-qc-opensource/measurement-v8/MAINTENANCE_R2_DEPLOY_20260915.md.

- 2026-09-15: V8 real extraction started on 105 in rainpulse-v8-real-20260915,
  29 existing V7 scans, lowest cut only, 2 CPU/6 GiB. First two succeeded;
  no inference or online product switch. Admin regeneration now accepts
  multiple selected times through existing persisted rerun requests. See
  docs/radar-qc-opensource/measurement-v8/BATCH_105_20260915.md.

- 2026-09-15: V8 measurement package applied as 71 checksum-bound paths, plus
  standalone experiment Dockerfile. 105 has rainpulse-qc-measurement:8.0.0 with
  compiled original bRopo Emitter/Emitter2 core and scikit-learn 1.8.0. All 83
  focused tests passed on 105 with no skips; installed-image synthetic full
  chain passed without network. This is OFFLINE AUDIT tooling: no real weights,
  no online V8 Worker, no planner switch or new history rerun. Online remains
  V7 r1; its three previous replays are now ALL_DONE. See
  docs/radar-qc-opensource/measurement-v8/DEPLOYMENT_105_20260915.md.

- 2026-09-15: V7 interrupted work package integrated and pushed as `76e60c4`.
  Actual Worker serialization required V7 action-validation integration; fixed.
  51 focused Python tests, Go API/workflow tests, Web and Linux BDP build passed.
  105 now uses qc-opensource-7.0.0 on native planner and 14 healthy workers.
  Completion-event payload overflow fixed in `bebb660`; runtime image revised
  to qc-opensource-7.0.0-r1. First 08:45 rerun SUCCEEDED/PUBLISHED in 1072s;
  20 QC/20 Grid/6 Mosaic/6 QPE/6 Diagnostics succeeded. 08:50 running and
  08:30 queued in the serial background script. 08:30/40/45 four-station raw
  PNGs are unchanged; QC changes only 0–83 displayed pixels per image. Major
  westward Z9591 and multiray Z9598 residuals persist; effects NOT accepted.
  See docs/radar-qc-opensource/EVIDENCE_GRAPH_V7_105_RESULTS_20260915.md. Native bRopo Emitter
  remains unverified and is not enabled. See docs/radar-qc-opensource/EVIDENCE_GRAPH_V7.md.
  Subsequent four-station V6.1 sheets still show westward Z9591 residuals;
  the earlier V6.1 report's “west line disappears” observation is not generalizable.

- 2026-09-14: V6.1 integrated and pushed as `414d241`; `2e522e3` supplies
  missing radar/terrain environment and read-only mounts to QC workers.
  Deployed on 105 as qc-opensource-6.1.0, renderer unchanged at 1.2.0.
  First 08:45 rerun SUCCEEDED/PUBLISHED (776 seconds), second 08:30 running.
  08:40 Z9591 strong north/west lines disappear, but 08:45 retains them despite
  sharing a scan: context/asset linkage requires investigation, not accepted.
  See `docs/radar-qc-opensource/RESIDUAL_V61_105_RESULTS_20260914.md`.

- 2026-09-14 package baseline (deployment superseded by entry above): Capability-based
  geometry loading fixes the missing residual-v6 branch; optional local narrow
  branch rescue and footprint-edge peripheral review retain original measurement
  gates and weather/missing barriers. Frozen old YAML remains unchanged, but old
  buggy V6 execution requires its original source commit. New read-only forensic
  export binds native 640x640 PNG/QC identity and ns timestamps; explicit context
  modes and resource hashes support controlled comparisons. Local validation:
  881 Python tests, 79 Web tests, Go test/vet/build pass. No real-case skill claim.
  See `docs/radar-qc-opensource/RESIDUAL_V61_REPAIR.md` and
  `docs/radar-qc-opensource/RESIDUAL_V61_VALIDATION.md`.

- 2026-09-14: Residual V6 integrated on main as `0614a62` and deployed to 105.
  QC `qc-opensource-6.0.0` and diagnostics renderer `1.2.0` switched together
  in the native planner and 14 workers. 08:30 rerun SUCCEEDED/PUBLISHED
  (665 seconds); 08:45 queued in the same background script. Partial fragment
  reduction observed at 08:15, major radial artifacts remain; not accepted.
  See `docs/radar-qc-opensource/RESIDUAL_V6_105_RESULTS_20260914.md`.
  V6 source package/ZIP remain untracked user inputs, excluded from commits.

- V6 residual candidate package is based on upstream e7a835f (V5 integrated).
  See `docs/radar-qc-opensource/RESIDUAL_V6.md`. Frozen V5 decisions are embedded;
  measured outlier association, narrow/interrupted candidates and original-domain
  speckle review add traceable decisions only. Renderer 1.2.0 uses native sampling
  footprints; earlier renderer semantics remain frozen. No source-data changes,
  deployment, history reruns or operational promotion. Native bRopo is optional
  and not executed locally. Real-weather / multi-station acceptance is pending.

- 2026-09-14: V5 cross-station capability/range-signature candidate implemented
  on a verified snapshot of main 51ed346d (tree a18b5f11); not deployed or
  promoted. See `docs/radar-qc-opensource/CROSSRADAR_V5.md`. Frozen V1–V4
  profiles/hashes retained. Default new long-strong radial path quarantines
  uncertain measurements (not confirmed truth), including unverified numeric
  plateaus. Fixed V4-context multi-station replay rejects duplicate/split-leaked
  inputs and distinguishes confirmed recall from withheld weather coverage.
  Actual Z9591/Z9598 labeled replay and station resource acceptance are pending.


- Paper-comparison V4 candidate (2026-09-13) builds on merged V3 `50781c2`.
  See `docs/radar-qc-opensource/PAPER_COMPARISON_V4.md`. It adds parameterized
  AFL/full-ray and AFL-local, a strict external RDD result boundary (native RDD
  is NOT implemented), and additive measurement fusion. Figure knots and
  unspecified AFL thresholds are explicit assumptions, not official SWAN code.
  Same frozen V3 task/context comparison, per-case labeling, duplicate-scan and
  split-leakage checks, QC-review JSON and optional same-palette PPI export.
  Integrated on main as 9fdff7e and deployed to 105 with qc-opensource-4.0.0.
  Two real reruns SUCCEEDED/PUBLISHED (38 QC executions, about 19.2 minutes);
  seven checked times read new assets, raw PNGs unchanged. 08:30 southern strong
  stripe improves markedly; 08:15 and 08:35–45 retain artifacts. Overall effects
  NOT accepted, operational_eligible=false. Deployment/evidence:
  `docs/radar-qc-opensource/PAPER_FUSION_V4_105_RESULTS_20260913.md`.
  V4 package directory/ZIP are user inputs excluded from normal commits.

- 2026-09-13 preceding test deployment (now superseded by V4): RFI Objects V3,
  superseding the V2 deployment below. Commits: 9d47305 integration, b42ad55
  Worker contract/geometry fixes, fa8c03b failed-scan regeneration recovery,
  ed8d3e4 unified BDP heartbeat lifecycle. Two reruns SUCCEEDED/PUBLISHED,
  38 QC executions succeeded; seven checked history times read new assets.
  08:30 improves, but 08:15 and 08:35–45 retain radial artifacts: effects NOT
  accepted, operational_eligible=false. See
  `docs/radar-qc-opensource/RFI_OBJECTS_V3_105_RESULTS_20260913.md`.
  V3 package directory/ZIP are user inputs and excluded from commits.

- 2026-09-12: `fujian-qc-rfi-objects-v2` / `qc-opensource-2.0.0` adds bounded
  native-polar objects, raw/axial/2D availability separation, per-gate causal
  RFI votes, unresolved-RFI quantitative quarantine and bounded residual checks.
  See `docs/radar-qc-opensource/RFI_OBJECTS_V2.md`. Frozen v1 profiles/parameter
  identity are unchanged. New compose override is a full coordinated candidate
  set; native planner must select the same QC config/SHA on drained queues.
  Added exact frozen-task offline replay and algorithm-specific /qc-review data.
  Genuine-library synthetic and Worker-path tests are NOT real screenshot-case
  acceptance. No deployment, historic regeneration, raw-data changes or promotion.

- Open-source QC candidate engine and review tools are implemented on
  `feature/radar-qc-opensource-20260911`; see `docs/radar-qc-opensource/README.md`.
  Default business profiles remain unchanged. New engine uses flags v2 and a
  coordinated QC/Hybrid/mosaic/QPE/diagnostics profile set, never mixed with v1.
  Genuine Py-ART/wradlib tests are distinct from pending real-weather acceptance.
  RADVOL SPIKE is a validated reference-import boundary, not a native run proof.
  No deployment, source-data cleanup or operational promotion was performed.

- 2026-09-11: Z9598 QC candidate `fujian-qc-evidence-2.1.0` / profile v3 adds
  radar-scoped long-range polarimetric evidence; three real Worker replays and
  105 Python/config tests pass. Regeneration QPE ownership race is fixed in
  source and PostgreSQL-tested. Deployed on 105 at 2026-09-11 08:35:28 UTC;
  native service healthy, first regeneration succeeded. QC/grid scaled to four
  healthy replicas each on 105; optional deploy/docker-compose.qc-backfill.yaml
  preserves this backfill capacity. Requests remain serial.
  Do not claim the site has switched or all reruns finished.
  See `docs/Z9598_径向干扰优化与验证_20260911.md`.

- The 2026-09-08 source convergence restores frozen NowcastNet artifact identity;
  deployment locations remain separate from the frozen profile.
- New LK jobs use model `pysteps-lk-2.0.0`, the prelaunch LK v2 config, and the
  distinct `pysteps-lk-v2` Worker profile/queue. Legacy LK replay retains its
  original queue and profile. See `docs/PRELAUNCH_CONVERGENCE_20260908.md`.
- Historical STEPS regeneration defaults to v6 with native NaN member support
  and LK v2. These changes require engineering replay before operational use.
- Verification replay pins the issue cycle and compares only native rain-rate
  samples at matching valid times and grid coordinates; its N=1 result is not
  a whole-field skill score.
- Source integration does not deploy services, regenerate historical products,
  or promote scientific/operational acceptance gates.

## Test deployment

- 2026-09-11: 105 now uses one native Go process (`rainpulse.service`) for Web,
  API, ingest and orchestration. Public port 4173; loopback 8080 compatibility
  remains for GPU scripts. CPU containers share `rainpulse-cpu-worker:latest`.
  Include deploy/docker-compose.unified.yaml for container operations; never
  start legacy Go containers alongside the host service. Old containers/images
  are stopped/retained for migration rollback; data volumes are unchanged.
  See docs/SINGLE_PROCESS_DEPLOYMENT_20260911.md. The old cmd binaries are thin
  rollback entrypoints; shared code moved to internal/apiapp, ingestapp and
  controlplane. Platform setup follows the ruiyun-bdp-go integration.

- Existing test host: `yons@192.168.28.105`.
- Active deployment root (migrated on 2026-09-03):
  `/home/yons/hwapp/ruiyun-bdp/bdp-dp/bdp-dp-rada/bdp-dp-rada-rainpulse`.
- Web entry: `http://192.168.28.105:4173/`.
- Update this BDP deployment root in place; never create a second deployment
  directory or parallel Web instance. Future server-side development is based
  from this Ruiyun BDP path.
- The server checkout can contain runtime/local changes and may not be on `main`.
  Treat local `main` as source of truth and use targeted synchronization/builds;
  do not reset, clean, or broadly overwrite the server checkout.
- Compose uses `deploy/docker-compose.yaml` together with
  `deploy/docker-compose.realtime-shadow.yaml` and `deploy/.env`.
- Deployment configuration guide: `deploy/README.md`. The simplified `.env.example`
  contains deployment inputs only. `RAINPULSE_RADAR_DATA_ROOT` is an absolute,
  same-path read-only bind boundary covering BDP metadata input roots; legacy
  `FMT_L2_Z959X_SBD` mounts were removed. Host GPU launcher paths derive from the
  project root; `/opt/rainpulse` remains a container-only convention. These
  configuration simplifications require the next deployment to take effect.
- The Go control services follow the Ruiyun BDP runtime integration. Their
  program/configuration code is `bdp-dp-rada-rainpulse`; `RADA_L2_FMT` input
  roots come from BDP metadata when the platform is available, with the
  deployment manifest retained as the compatibility fallback.
- Existing SSH authorization is configured outside the repository. Credentials
  are intentionally omitted from project memory and must never be committed.
- For routine UI deployments, the user prefers an in-place update with only
  proportionate checks, then direct visual validation in the browser.

## Current product and UI behavior

- 2026-09-16 质控排查四图第 3 格默认改为雷达拼图（RP-010 网格 `DBZH_QC` 诊断层
  `analysis:dbzh_qc`），工具栏「证据图层」两个按钮可在拼图与质控标志间手动切换；周期缺拼图层时
  自动降级到标志并隐藏切换。见 `docs/质控排查_拼图面板设计_20260916.md`。
- 2026-09-16 历史案例面板恢复 6 分钟档位：一档一行、档内取最接近该档的案例，5 分钟旧结果的实际
  起报时间以小字保留；此前“按实际起报时间逐条列出”的改法使用户看到 5 分钟一行，已按用户确认
  回退。见 `docs/TIMELINE_6MIN_3H_20260916.md`。
- 105 前端产物当天两次发布（本地构建后上传 dist）：回退目录
  `apps/web/dist.pre-mosaic-20260916162154`、`apps/web/dist.pre-picker-20260916164914`；
  两次发布后均核对 index 引用、资源 200 与关键文案。

- 2026-09-10 verification replay now computes native-frame whole-field CSI, FSS,
  event-any neighborhood CSI, MAE/RMSE through Go and the existing product worker.
  No map click required. Threshold/km changes reuse metric rows. Missing remains
  missing; common and neighborhood coverage are displayed. Deployed to 105 and
  tested at 08/28 16:30 +30/+110 for LK, STEPS and NowcastNet. This is per-selected
  frame radar-QPE comparison, not historical batch statistics or model promotion.
  See `docs/SPATIAL_VERIFICATION_20260910.md` for single-frame verification.
- 2026-09-10 added expandable verification analysis: cross-lead curves linked to
  the original timeline, common-domain PSD, and manually started batch reports.
  A single CPU job runs at a time; identical settings replace the saved report,
  at most eight reports persist under runtime/reports/workspace-verification.
  PSD requires a complete common square (at least 32 cells per side), never
  fills missing cells with zero, and exposes crop bounds/coverage. Batch scores
  are equal-record macro means on shared finite samples, not pooled CSI.
  Deployed and checked on 105: three 08/28 cycles, 36 lead groups, 34 matched and
  two skipped. See `docs/VERIFICATION_ANALYSIS_20260910.md`.

- 2026-09-09 simplified interval selection uses the original five-minute timeline:
  click selects a single rain-rate frame; press/drag/release selects accumulation,
  with 0–1/1–2/0–2 h shortcuts. No mode buttons or separate handle-based timeline.
  React requests Go
  `/api/v1/workspace/accumulations`; existing product-builder worker computes from
  numerical sources (including future observed QPE), without GPU/model reruns.
  STEPS integrates members before P50; NowcastNet integrates its retained mean.
  Missing stays missing. PNG/exact values share a bounded 10-minute memory cache;
  no extra persistent product versions. Deployed to 105 and exercised with the
  08/28 16:30 CST case across four intervals and four algorithms. See
  `docs/ACCUMULATION_TIMELINE_20260909.md` and `contracts/data/workspace-interval.md`.

- Historical analysis and workspace catalogs now follow keyset pagination before
  presenting all available cycles. Never treat a `limit=200` response as the full
  history: duplicate calculation versions previously hid morning results.
  The case picker adds Beijing-time start/end filtering and an all-day reset;
  it filters existing results only, not raw files or replay configuration.
  See `docs/HISTORY_CATALOG_PAGINATION_20260908.md` for tests and deployment proof.

- The unified realtime workspace is the active interface. It defaults to four
  synchronized maps and allows switching any selected algorithm to a single-map
  view.
- The workspace is intentionally compact: map labels live inside the map,
  legends are small and translucent, the timeline is compressed, and the full
  four-map/timeline view should fit the viewport as far as screen size permits.
- Basemap is enabled by default. Rendering modes include grid, smooth, and point
  values. Hovering a valid data cell shows longitude, latitude, and the direct
  value; missing cells show nothing.
- Radar inspection includes site metadata, station coordinates and range rings.
  QC flag labels are Chinese and must stay consistent between map labels,
  legends and diagnostics.
- Workspace cycle choices were intentionally reduced to the concrete Fujian
  sample data on 2026-08-28. Do not restore June, 08/24-08/25 or 08/29-08/30
  synthetic/engineering cycles without an explicit request.
- Five- and ten-minute gaps in the sample cycles reflect the available test
  source/derived products and were intentionally left unchanged.

## Algorithms and regeneration

- NowcastNet atlas v2 is an offline overlap candidate, not the active default.
  Follow-up fixed-tile experiments isolated spatial weighting and log-midpoint
  attenuation. Neither sharper weights nor rain-space midpoint averaging has
  demonstrated consistent accuracy gains; both remain offline opt-ins. The
  three-step experiment is now closed: 192x256 v3 context ran on three starts,
  including temporal rechecks, but long-lead skill was not consistently better.
  Same local RNG seed is not geographic noise consistency; the frozen model
  noise path was inspected, not rewritten. No candidate was promoted.
  Temporal defaults remain unchanged.
  A controlled 16:30 case reduced old-boundary jumps but also reduced strong-rain
  area; do not switch the worker or mass-regenerate history based on visual
  continuity alone. See `docs/NOWCASTNET_OVERLAP_20260908.md`; the standalone
  comparison runner never publishes products.

- Phase-1 critical path remains radar QC, Hybrid Scan/grid, mosaic, QPE,
  NowcastInput, pySTEPS-LK and application products. pySTEPS-STEPS and
  NowcastNet remain comparison/controlled paths rather than blockers for the
  baseline product.
- Radar QC has been expanded for the visible 08/28 artifacts and the open-source
  candidate is now deployed on 105 in shadow/candidate mode. The candidate image
  is `rainpulse-cpu-worker:qc-opensource-1.0.0` with Py-ART 2.2.5 and wradlib
  2.9.5. Three serial replays (08:20, 08:25, 08:30 Beijing time) completed
  QC→grid→mosaic→QPE→diagnostics; the 08:15 analysis frame was refreshed as a
  prerequisite and the workspace no longer uses the old fallback for that time.
  Preserve raw and normalized radar inputs; QC remains polar and cause
  flags/QI/provenance must stay traceable. The candidate is still not
  operationally eligible: static ground/sea assets are skipped and the default
  profile leaves the experimental local RFI supplement disabled. Use
  `docs/radar-qc-opensource/VALIDATION.md` for the exact run IDs and metrics.
- The public-weight NowcastNet comparison path uses tiled inference/stitching to
  cover the Fujian target grid. It can be regenerated through the existing
  controlled offline path.
- In the Web data-regeneration panel, `forecast_all` now runs the complete chain:
  radar QC -> Hybrid Scan/grid -> mosaic -> QPE -> diagnostics -> NowcastInput
  -> pySTEPS-LK -> application products.
- `pysteps_lk` and `products` remain smaller downstream regeneration presets.
  pySTEPS-STEPS and NowcastNet continue through the controlled script/offline
  entry described in RP044.
- Browser users do not enter an admin token. The Web gateway injects the
  server-side `RAINPULSE_ADMIN_TOKEN` only for the exact bounded rerun route and
  blocks other `/api/v1/admin/*` paths.
- PostgreSQL migrations `0016_manual_regeneration.sql` and
  `0017_full_pipeline_regeneration.sql` support repeatable lineage and the
  full-pipeline regeneration state machine.
- The latest end-to-end server proof is the 2026-09-12 candidate replay. Target
  runs are `8562fe7f-9ac7-582c-9fa3-5ac12b13d315`,
  `b77771a0-3490-5058-ae65-3ca41246c74d`, and
  `7da8a66c-20c6-530c-80de-dab6081832d7`; all requested QC, grid, mosaic, QPE,
  diagnostics and downstream stages succeeded. The older 105 replay record in
  `docs/雷达质控_105重算与测试交接_20260911.md` remains historical evidence and
  must not be read as the status of this candidate deployment.

## Data-version and retention intent

- History replay packaging: `scripts/package_history_case.sh --date YYYY-MM-DD`
  creates a separate case ZIP (Beijing calendar date by default), with scoped
  database lineage, current derived products and point indexes, not raw/model
  data or queues. The ZIP contains `import_history_case.sh`; fresh target case
  database and stopped application services are required. See
  `docs/内网离线部署.md`. 08/28 export: 123 cycles, 57,035 files, approximately
  3.24 GB ZIP; checksums and isolated PostgreSQL rollback acceptance passed.
  Program image packaging remains a separate step and must match its Git config.

- The workbench should expose only the latest successful version for a cycle and
  algorithm. Recalculation must not leave multiple selectable product versions.
- Raw/normalized radar data and database lineage remain immutable/auditable.
  Derived product cleanup or supersession must never delete the only currently
  usable result when a regeneration fails.
- The test server has finite disk capacity. Avoid per-release source copies,
  database dumps, and indefinite duplicate derived bundles.

## Useful continuation checks

- Begin with `git status --short --branch -uall` and `git fetch origin`; preserve
  unrelated dirty files.
- Proportionate verification for the recent control/Web work is:
  `go test ./services/control/...`, `make test-regeneration`, `make test-web`,
  `make lint`, and `make build`.
- Before debugging data visibility, distinguish CST display time from UTC storage
  and verify the exact cycle/run lineage rather than matching only the displayed
  minute.

## 2026-09-13 RFI Objects V2 整合

已核验交付包并整合对象引擎，保留门级上下文修复。完整 Python/Go 与前端 74 测试通过。初次连接超时，用户恢复网络后已部署到 105 并开展真实重算。详见 docs/radar-qc-opensource/RFI_OBJECTS_V2_INTEGRATION_20260913.md；不能将 V1 部署记录视为 V2 已上线。

105 V2 三组重算现已全部 PUBLISHED/SUCCEEDED（49 次 QC）；08:15/25/30 改善有限，08:35–45 仍残留，不通过径向效果验收。详情见 docs/radar-qc-opensource/RFI_OBJECTS_V2_105_RESULTS_20260913.md。

- 2026-09-15 support audit: 29 lowest-cut frozen scans audited and paired
  initial decide experiments completed, zero action changes. 208,690 visible
  gates already quarantined. All 29 V8 native detectors returned
  unsupported_angular_geometry with zero calls (feature completion was NOT
  native execution). Default-off source-aware support switch added; online
  remains unchanged. See measurement-v8/SUPPORT_AUDIT_20260915.md.

### 2026-09-15 原生 Emitter1 稀疏适配
- 新增离线 sparse_emitter.py：实际相邻三射线共同有效连续段调用原版核心，无插值/缺测填充；Emitter2 未改，生产未切换。
- 105 两个 Z9598 ROI：08:35 6404 门中计算1620、阳性0；08:45 1728门中计算101、阳性0。旧适配两处计算均0。覆盖提升≠残留改善，不应上线或全站重算。
- 三项实际核心回归通过。见 docs/radar-qc-opensource/measurement-v8/SPARSE_EMITTER_20260915.md；后续区分共同观测不足与实际方位对比不足，不能填缺测造阳性。
- 稀疏 Emitter 后续逐门归因：08:35 已计算1617门对比<8dB、3门≥8dB，2704缺肩部/几何、2080共同段短；08:45分别101/0/1182/445。±1/2/4/8/16射线尺度均无ROI内≥8dB连续4km段。扩大肩部不能解决本次残留，后续应做断续对象证据，勿补缺测或据此宣称算法已改善。
- 已实现默认关闭的 interrupted_objects_enabled：同射线有界断续对象、冻结干扰锚点、目标局部双偏振确认及邻站冲突保护。18项相关测试通过。105 两时次对象候选 ROI 731/1077，新增业务去除均0，未上线；见 INTERRUPTED_OBJECTS_20260915.md。识别推进不等于质控效果改善。

### 2026-09-16 OC1 test-site activation
- Pipeline qc-opensource-7.1.0 / decision object-consensus-oc1 now appends OC1 quarantine with separate baseline/addition provenance and first-decider code8. New worker serialization checks passed; no confirmed-pollution additions.
- 105 uses rainpulse-cpu-worker:qc-opensource-7.1.0-oc1 across QC/grid/mosaic/QPE/diagnostics; control.env points to fujian-qc-object-consensus-oc1.yaml. Append deploy/docker-compose.qc-object-consensus.yaml after existing overrides for future compose actions.
- 4173 HTTP200. Background runtime/reports/oc1-online-20260916/refresh.py serially regenerates 08:35 and08:45 forecasts (preceding frames cover08:15/35/40/45). First request d8890a9d-1804-52ea-ba58-1aabfbe8aa4a observed QC_RUNNING. This is not completion evidence; inspect refresh.log before resubmitting. Raw data preserved. Old derived disk cleanup not yet verified.

- 2026-09-19: 105 deployed near-background-20260919-v1 CPU image / QC7.3.9.
  Pinned Z9591/Z9598 single-day background only applies to UTC2026-08-28;
  other dates abstain. QC/grid/mosaic/QPE/diagnostic services use new profiles.
  `/tmp/rp-near-recompute.py` on105 runs08:12 then08:18 all4stations and downstream;
  progress `/tmp/rp-near-recompute.log`, final IDs `/tmp/rp-near-recompute-results.json`.
  First08:12 Z9591 succeeded;41510near-background gates verified excluded from CR/QPE.
  Remaining recomputation was still running at handoff; do not claim web switch complete.
  Source committed and pushed as fbfa0d5.
  Details: docs/near-background-clutter-20260919.md.

- 2026-09-20: Integrated near-measurement-20260919 candidate over fbfa0d5.
  105 image `rainpulse-cpu-worker:near-measurement-20260919-v1`; append
  `deploy/docker-compose.qc-near-measurement-20260919.yaml` to the deployed stack.
  Selected strict-cr-snr8-nonmet-quarantine child of the real near-background profile.
  Nonmet quarantine affects trust/QPE; low-SNR withholding affects CR only.
  All worker flag definitions explicitly use v2 (old mosaic used v1 and failed).
  Local near-measurement/volume tests: 136 passed; server real wradlib/Py-ART parity passed.
  Two-cycle four-station recompute: `/tmp/rp-nmr-recompute.py`, progress
  `/tmp/rp-nmr-recompute.log`, results `/tmp/rp-nmr-recompute-results.json`.
  Check completion before claiming refreshed imagery or improved clutter performance.

- 2026-09-22: Diagnostics CR now uses CR admission for legacy full-range fusion
  and withholds weak (<25 dBZ) returns within 10 km of a source radar before the
  multi-radar maximum. This is CR-only; QPE inputs remain unchanged. Commit
  `9d65244`; 105 diagnostics worker image
  `rainpulse-cpu-worker:diagnostic-cr-near-weak-20260922`, with compose override
  `deploy/docker-compose.diagnostic-cr-near-weak-20260922.yaml`. BJT 09:00 proof
  job `432b68f3-3184-4707-9be7-ca4c91ab5fee`: 598 weak CR pixels removed overall,
  including the 244-pixel Z9598 station-edge component; new grid PNG SHA
  `d4a9ae2d8db3dfbff95469d8e138e5ec6e64b4357c6394166d2e1d6807a2b3d9`.

- 2026-09-22: Replaced the fixed 10 km/<25 dBZ diagnostic-CR hole with
  `near-object-cr-20260922-v1`. The new polar object policy requires evidence
  families, weather protection, and bounded growth; it withholds CR before the
  multi-radar maximum and records `CR_NEAR_OBJECT_WITHHELD`. It does not alter
  raw/QC/grid/mosaic/QPE. BJT 09:00 replay increased Z9598 0–10 km trusted CR
  pixels from 17 to 170 while reducing 10–50 km weak residuals. See
  `docs/near-object-cr-20260922.md`. 105 diagnostics image
  `rainpulse-cpu-worker:diagnostic-cr-near-object-20260922`; BJT 09:00 job
  `0fd8bdec-3668-40c9-9073-b999b6336936` succeeded and the API selects its
  `grid-dbzh-qc` SHA `14b2daa8b6041d6b36c31a7a8b80c32e3c5f04fb129953f6192bae265d1dac92`.
  User visual acceptance remains pending.

- 2026-09-22: Added audit-only `near-temporal-object-20260922-v1` and read-only
  replay script. BJT 09:00 Z9598 replay took 5.592 s / 916.8 MiB, found 415
  objects, but covered only 40/1,265 latest weak CR winners (35/1,019 at 10-50 km).
  Threshold relaxation only reached 47/1,265, so temporal recurrence is not yet
  effective for the visible residual and remains out of production. See
  `docs/near-temporal-object-20260922.md`.

- 2026-09-22: Implemented audit-only `residual-texture-isolation-20260922-v1`
  with native polar multi-scale texture, object shape/area, 3 km context, polar
  evidence, and separate textured-blob/isolated-speckle outputs. BJT 09:00 Z9598
  replay: 3.299 s / 596.4 MiB, 4,260 blob + 2,949 isolated audit gates, but only
  69/1,265 weak CR winners covered (45/1,019 at 10–50 km). Broad thresholds
  reached 163/1,265 and an over-broad small-object bound 532/1,265; A follow-up matched vertical context + speckle pass reached 78/1,265
  (53/1,019 at 10–50 km) in 4.160 s / 729.2 MiB. Production removal remains off. See `docs/residual-texture-isolation-20260922.md`.

- 2026-09-25: X native-QC browsing now has independent read-only station/scan/result
  APIs and `/?preset=qc&band=X`; it does not depend on S forecast cycles. Current
  registered X stations on 105 are ZF101 and ZF505, each with two successful
  candidate scans after acceptance run 81fa570b-9724-4514-8d8d-3e38d47c8689.
  The remaining 22 disk-inventory stations still require identity/config registration;
  do not claim full-network or spatial-fusion readiness. See
  docs/X_RADAR_WORKSPACE_IMPLEMENTATION_20260925.md for scope and evidence.
  Workflow: integrate and verify locally, merge local main, then deploy artifacts
  to 105. The test account supports sudo; passwords must never be persisted.

- 2026-09-25: User requires X/S to share the same UI language, timeline, and
  reflectivity palette. X now uses SharedTimeline in observation-only mode and
  the same radar selector/control styles. A package JSON palette supplies both
  S/X rendering and the shared Web legend (14 discrete colors, 5–70 dBZ).
  Candidate QC intensity images retain uncertain reflectivity; actions live in
  the separate flags layer. Historical results retain their original legends.
  See docs/X_SHARED_WORKSPACE_20260925.md.

## 2026-09-27 多站 S/X 地图首批部署

- 已按本地合并再部署流程，将 `feat/multistation-sx-workspace` 合入 main：`fe04d7d45065`。新增多站混合地图、每站图层控制、S 已发布组合和同次 S/S+X 地图产品接口。
- 105 为 `192.168.28.105`，账号 yons 通过 sudo 重启服务；认证信息不得写入仓库。应用路径仍为 `/home/yons/hwapp/ruiyun-bdp/bdp-dp/bdp-dp-rada/bdp-dp-rada-rainpulse`。
- 应用在线版本 `fe04d7d45065+fe04d7d45065bc454bb117a07fa290d9c1d10918`，Worker 为 `rainpulse-cpu-worker:multistation-fe04d7d`，健康。部署目录 `.build/multistation-fe04d7d`，回滚 `.build/rollback-multistation-fe04d7d`；原 Worker image `rainpulse-cpu-worker:x-map-17d3b1e`。
- 健康检查须兼容 `Version+Revision` 返回形式。首次切换因精确字符串匹配触发自动回滚，修正后再次切换成功。
- 线上 08:06 实测 4 S + 2 X 目录，2 S + 2 X 地图可用，2 S 缺测显式保留；多站站点选择不改变计算产品来源。组合入口把站点/六分钟时间传入现有预检查计划。
- 当前冻结网络只有 zf101/zf505，enabled=false、geometry_verified=false、calibration_verified=false，products={}。地图候选叠加已开放，数值 S+X 不能声称启用；没有组合产品时明确 X 未参与。
- 本批验证：143 前端测试；60 多波段 Python 测试；Go 全包测试/vet；专用临时数据库站点/组合目录 SQL 集成测试；实际 Worker 地图预览 smoke；1440×1000、375×812 浏览器。
- 批量源数值点查、贡献专题图、像素缓存预算及 4 S + 11 X 压力测试尚未完成，详见 `docs/MULTISTATION_SX_IMPLEMENTATION_20260927.md`。本批未新增 batch-resolution API，复用现有只读目录解析。

## 2026-09-27 S/X performance A+B

- Integrated the recipe-based package against c5c7b47, preserving main's multistation map changes. Runtime timings remain outside frozen identities; grouped S validation, X ray pruning, shared preview sampling and layout warmup preserve product semantics. Selected NaN object IDs now fail closed, with helper and full disposition regressions.
- 105 X image `rainpulse-cpu-worker:performance-ab-bbf5cb2` is based on its live multistation-fe04d7d image; the targeted overlay is `deploy/docker-compose.performance-ab-20260927.yaml`. Use the active container's complete Compose file chain plus `deploy/.env`; never replace the dirty server checkout. Roll back X to multistation-fe04d7d if necessary.
- Read-only real paired X replay: ZF101 (40 sweeps/240 PNG) 21.092s -> 13.225s; ZF505 (9 sweeps/54 PNG) 4.944s -> 3.330s. Every output object hash matches; telemetry-off also matches. These are single samples, not throughput or p95 evidence. No spatial S+X eligibility is enabled.
- S image `rainpulse-cpu-worker:qc-performance-ab-bbf5cb2` overlays only modules verified against the live automatic S baseline. Its guarded override is `deploy/docker-compose.qc-performance-ab-20260927.yaml`. Managed S QC now uses the fully reviewed `rainpulse-cpu-worker:performance-ab-bbf5cb2` image via `deploy/docker-compose.ops-qc-performance-ab-20260927.yaml`; its former 6 GiB cap was below measured full-volume use, so memory/swap limits are both 40 GiB, with one task at a time.
- AB regression: 146 passed, 7 Numba skips locally. Clutter-fusion 110 and volume-review 87 passed; multiband suite passed. Existing radar-QC 3 failures, batch1 3 failures and object-store 2 failures reproduce at pre-change b4d7818. Full CI remains red on existing contracts/identity/lint failures; dedicated test-performance-ab is now a separate CI matrix target.

- User explicitly accepted existing full-CI failures; remote main includes `1edd42e`. Preserve the original local main checkout's unrelated uncommitted work.
- S rollout recovery: replayed genuine stored completion events (6 old jobs recovered), restored 18 jobs to their recorded failure state, and skipped 4 superseded old QC jobs without changing current product pointers. `/home/yons/rebuild_bjt_followup.sh` was then found to bypass the release gate and create retries without the scan FAILED -> QC_RUNNING transition. Stop/retire this legacy SQL helper; do not restart it. Its original body is retained below an explicit exit. Repair receipts live under `runtime/control/performance-ab-*-recovery.json` / `performance-ab-retry-repair.json`. The 16 real retries it had already issued were allowed to finish after restoring their missing scan admission transition.
- Real S validation uses the same frozen Z9598 request: 11 sweeps, 3,994 rays, 28,614 logical objects. All metadata and every decompressed chunk byte match the existing result. Blosc encoding is not canonical: compressed digests can differ even when decoded bytes match, independently reproduced by re-encoding old bytes. Do not confuse compressed-object hash differences with scientific changes, or weaken transport checksum verification. End-to-end timing was about 150–156 seconds versus the old worker's 155 seconds; no material whole-worker S speedup is established by this sample.

- 105 S automatic release completed under the gate at 2026-09-26T17:51:57Z: two healthy new replicas, frozen config/flags/runtime/image identities verified, zero jobs/outbox/intents/consumer backlog at resume. The 16 legacy-helper retries all succeeded. Managed S QC is healthy on the new image; actual two-volume QC/preview acceptance run: `29ca4dd9-c4f3-4daf-9cbf-67bc0aedab85`. The active Compose manifest includes the X, automatic-S and managed-S overrides; do not reconstruct it from an older partial stack.


## 2026-09-27 S/X performance C+D

- Integrated exact-baseline C/D package from `4c87cc8`; source/review commits `1b2e806` and final lint cleanup `8ad34ac` are on remote main. Preserve the unrelated dirty original main checkout.
- 105 active Compose appends `deploy/docker-compose.performance-cd-20260927.yaml`: automatic S retains two replicas on `rainpulse-cpu-worker:performance-cd-qc-20260927`; managed S and X use `rainpulse-cpu-worker:performance-cd-full-20260927-r1`. Managed S retains 40 GiB memory/swap and one task lane. All changed image files were hash-checked against local source. No Go/Web binary change was needed.
- Automatic S was drained and image/config/flags/runtime identities verified before resume; managed pools were independently drained. Active manifest preserves the complete previous stack. Receipts: `runtime/control/performance-cd-release.json`, `performance-cd-r1-release.json`, `performance-cd-verification.json`, and `performance-cd-acceptance.json`. Use the same release gate and prior overrides for rollback; never replace the dirty remote source tree.
- Real paired S: 149.379→149.168 s, all 28,614 metadata/decompressed chunks equal; no material whole-worker gain. Real X: 3.558→3.244 s, all 55 object bytes equal. Linux C synthetic full-budget case 1047.2→149.7 ms with identical products and zero height scratch writes. These are single samples, not p95/throughput claims. Streaming remains false and decoded cache zero on the live X configuration; spatial S+X eligibility remains disabled.
- Live acceptance succeeded: X run `9fbe671b-46d2-4a78-98f2-7d5e49ac2f5c` (one task), S QC+preview `97e547af-4413-4f19-9ad2-3208b0ece676` (two tasks). Final managed image revision only changes unused imports/import ordering/line wrapping and passed the same Linux suite.
- C/D 131 passed/3 Numba skips locally and in Linux images; actual Zarr 2 executed. Existing A/B, multiband, VOR, RDR, CF, worker/contracts tests and relevant Go suites passed. Dedicated CI C/D target uses fixed Git reference source rather than delivery ZIPs. New C/D lint is clean; existing algorithm lint is 3349 vs baseline 3350. Existing OpenAPI and RFI/paper identity CI failures remain under the previously accepted exception. See `docs/PERFORMANCE_CD_20260927.md` for invariants and measurements.

## 2026-09-27 多站续批：源值点查与贡献分析

- `b91b81b` 已合入本地 main 并部署 105（Go/Web）。新增 16 站批量解析与点查，内容绑定数值块、贡献专题图、并发 4 和 128 MiB 图片缓存驻留预算。详见 `docs/MULTISTATION_SX_IMPLEMENTATION_20260927.md`。
- 105 部署目录 `.build/sx-probes-b91b81b`，回滚 `.build/rollback-sx-probes-b91b81b`；现场 Compose 完整链后追加该发布目录的 `compose.json`。X/自动 diagnostics/managed diagnostics 镜像分别 `rainpulse-cpu-worker:sx-probes-b91b81b-0/-1/-2`。S QC、render 与 C/D 参数保持既有版本。
- 实际 X 验收 run `5d65e5bf-75a5-472d-9a5f-c22179b08342` 两任务成功（ZF101 40 层、ZF505 9 层）；真实点查数值与校验后的数值块相符。管理池恢复 ACCEPTING，无活动/排队任务。
- 旧 S 历史图件无数值索引，须由更新后的 diagnostics 生成新资产，不覆盖历史。4 S + 11 X 仅完成合成缓存负载测试；实际目录仍仅 2 X。真实 S+X 数值融合仍受站点几何/标定和空产品配置限制。

## 2026-09-27 S+X 未标定水平试验

- 用户同意继续尽量完成 S+X，验证阶段可使用明确标注的未标定试验。105 实际地址为 `192.168.28.105`；`yons` 可用 sudo 管理服务，认证信息不写入仓库。
- 新增独立 `experimental_horizontal_max` 产品方法和 `experimental_enabled` 站点开关，沿用 operations、现有地图/色谱/六分钟轴。未修改 verified 标记或可信等高融合准入；QPE/预报不接入该产品。
- 真实四 S + ZF101/ZF505 于 08:12 北京时间数值组合成功：S 36,359 格点、X 9,496 格点、联合 37,087 格点，最终有 S 和 X 获胜。X 缺少 SNR 时保留不确定候选，实测低 SNR/污染/阻塞继续排除。源值样本 25 dBZ 与数组一致，返回 low_quality、原射线/距离门/仰角编号，未知高度/质量为 null。
- 已修复重复方位角（最新射线、同刻取原始首索引）、浮点秒与微秒目录边界比较、API 遗漏试验标识、有限值不确定点错误标为 valid、组合色谱 CSS 冲突。
- Go 105 当前 `f82d18c`，Web 含 `675b4cd` 色谱修复；试验发布目录 `.build/sx-experiment-108e920`，其 `compose.json` 追加完整历史链。最新 Worker 发布应以现场容器 image/labels 为准，后续镜像包含 `71084d7` 时间精度修复。网络仅四 S + 两 X，网格 1 km、6 分钟，不能声称 24 X 全部接入。
- 两 X 站 00:00–01:00 UTC 各 10 份体扫已通过正式历史导入并全部 NORMALIZED。连续组合验收仍需以最新 operations 运行状态为准；早期缺资料或修复前失败尝试保留。
- S 点值补建 v16 成功 99/106；4 个超大消息失败已用 v17 新任务全部补建成功，自动诊断已缩回一副本。另 3 个周期实际混用不同 clutter-fusion generations，未绕过一致性校验，需统一 QC 输入后补建。v17 当前配置/Compose 为 `deploy/*diagnostic-source-probe-retry-20260927.yaml`。
- 最终一小时组合 run `126aaf50-043a-4acd-b276-509259e0773c`：10/10 成功，逐数组验证 joint=fmax(S,X)，X 获胜格点均带不确定标记。08:06 仅有因果可用 X；08:12–09:00 九帧均有 S/X 获胜。最后 Worker 为 `rainpulse-cpu-worker:sx-experiment-71084d7`，健康；Go `f82d18c`。X 单站图件补建分为三批（每计划最多8体扫），运行状态另查对应 operations 记录。

- X 单站质控最终 20/20 成功（三批 7+7+6），公开 scans API 两站各 10/10 READY 且有结果资产。临时三副本已恢复一个健康 multiband Worker；最终运行记录见 `docs/SX_HORIZONTAL_EXPERIMENT_20260927.md`。

## 2026-09-27 第一优先主线（进行中）

- 用户明确要求先完成全站接入、S 三周期修复、全天产品与逐帧状态，再进入第二优先效果评估；完成后主动沿主线提示下一步。见 `docs/SX_PRIORITY1_20260927.md`。
- 原始目录实际为 24 X / 5,466 文件；本次逐文件头部核验一致。更正此前缺 SNR 判断：23 站原始样本有 SNR，旧候选解码配置遗漏了映射；ZF900 样本未见 SNR。新增完整字段候选配置。
- 21 站样本全字段解码通过；ZF900 显式 reserved-tail 配置后通过；ZF703/ZF801 射线亚秒值反复回绕，尚未安全解释，不能伪造精度或绕过校验。ZF502 首文件 bz2 截断，其下一份样本通过。
- S 三周期 02:36/02:54/03:00 UTC 混用两代 CF/volume-review 配置；普通幂等 QC 命令命中旧任务，不构成修复。新增带独立 UUID 的 QC-only rebuild 命令，需部署执行后再补建诊断。
