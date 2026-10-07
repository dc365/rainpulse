## 2026-10-08 切换不清屏（第十七批补，已部署 105）

- 用户复测：顶部胶囊 OK，但"上下重绘闪"又回来——布局高度实测仍恒定（436@263），真实感受源是
  **旧图层被清空→空白→新图重绘**（跨时次保留组合帧已被并行会话回归测试禁掉后的必然空窗）。
- 解法（两全）：RasterGISMap 新增 `layersPending` prop——调用方数据源在途时**不清除已渲染图层**
  （旧层降透明 55% 示忙），确认空才清屏。fusion 分支传"清单或周期详情在途"。props 语义不变，
  并行会话测试（mock props 断言旧帧 URL 不得出现）照常通过——保留发生在渲染层而非数据层。
- 105 实测：切换全程画面内容零空窗（采样 min>0），顶部提示 ~300ms 即逝。190 用例全绿；
  提交 `fad44dc` 已 push。

## 2026-10-08 读取提示移出地图中央（第十七批终，已部署 105）

- 用户复测仍在中央闪"正在读取降水图层"。两点处理：
  ① **回退跨时次保留组合帧**（上条引入的 keptFusion"上一帧"机制）——并行会话随后新增两条回归测试
  硬锁"组合结果不得在新时次下显示旧帧"（含 series 切换），尊重该语义；我上批改的 fallback 测试
  断言同步恢复原文。
  ② **空态/加载提示改为地图顶部胶囊**（styles.css：`.gis-layer-empty` 由中央大卡片改为 top:8px
  居中小胶囊，strong+small 横排），在途提示不再遮挡地图中央——这是用户主诉的解法。
- 190 用例全绿；105 实测切换时提示为顶部小条（宽~270px、位于地图顶缘），数据区无遮挡。提交
  `dcffc29` 已 push。教训：改共享组件语义前先看并行会话是否已用测试锁定（本次连续两次被新测试拦下）。

## 2026-10-08 近站杂波研究的数据约束

用户明确当前没有更多数据，近站研究只使用已有资料；新增晴空过程、雨量观测、人工标签、CPA/SQI/IQ 不作前提，长期背景扩充和监督训练暂不列入本轮。方案见 `docs/NEAR_SITE_CLUTTER_RESEARCH_20261008.md`：先追溯实际近站贡献/量测支持，再审查已有时序配准及 TREF/REF，证据足够才比较一个保守候选。历史四份库存44切面中36含TREF/REF；既有径向四例不等于截图四框。此次仅资料回读和文档修订，无新气象回放、算法/配置/部署改动。

## 2026-10-08 组合模式中央覆盖闪修复（第十七批续，已部署 105）

- 用户复测：布局高度已稳，但地图**中央覆盖提示**仍在切换时闪两下（"正在读取降水图层"→"本时次
  无可显示的融合图件"→出图）。根因：fusion 主图 layers 在组合清单/周期详情在途时为空数组 →
  RasterGISMap 空态覆盖；fallback（S 参考图）依赖 detail，到达前又落入"确认空"文案。
- 修复：fusion 主图引入 stale-while-revalidate（对齐 overlay 批次策略）——`fusionPending`
  （清单或详情在途且当前无图）时沿用上一帧 `keptFusion`，地图 header 标注"· 上一帧"防冒充
  （并行会话回归测试原断言"pending 时旧帧必须消失"，已按新语义更新：保留但可见标注）；
  两源确认无图才显示真空态。105 实测三种切换（无→无、有→无跨边界、pending 标注）均零覆盖闪现。
  190 用例全绿；提交 `4ad625a` 已 push。

## 2026-10-08 组合模式地图上下重绘闪修复（第十七批 Web，已部署 105）

- 用户报组合反射率切换时次地图"一闪一闪上下重绘"。根因：有组合时次的 CompositeScope 提示行
  （"请求全部 N 站，实际输入 M 站…"）与 display_warning 行只在有结果时出现（且 warning p 带
  浏览器默认 16px 上下 margin），底部状态行 1↔2 行折行——三者合计让地图容器高度在 383↔456px
  间跳，OL resize→refit→上下闪。
- 修复：上方两条提示收进固定 30px 单行轨道 `.multi-composite-meta`（两行并排横显、超宽省略、
  title 悬停全文、空时隐形占位）；底部状态行 `.multi-composite-footnote` 固定两行 38px 轨道。
  105 实测有/无组合/再有组合三时次地图恒 436px、零位移。190 用例全绿，部署 hash `075253eb→
  6084aca`（含前次部署），提交 `6084aca` 已 push。

### S NMR/RDR ownership optimization, 2026-10-07 15:41 CST

Contract before tests/implementation;5newREDthen116relatedPASS. NMR/RDR copy mutable fields only, unchangedRAW/history borrowed readonly views; parent buffer must remain unchanged, independentbefore/evidence retained. No meteorological/default/threshold changes.5mode exactallarraybytes+summary equality; actual0818 v8 stage replay612/680fields equal,536/587borrowed avoids866043840/926653440 eagercopybytes, NOT wholevolumeRSS/time reduction or residual improvement. Receipts disposition-copy-equivalence-v1/disposition-copy-real-0818-v1 candidateSHAs bound. NewlinesRuffPASS; fullfiles retain oldstylewarnings. Actualtask54b568e2retry2SUCCEEDED171s;stagecontext65/core59/serialize44,notCPUthrottled. No deploy/restart/commit/push/delete. Originalrefresh2315154/auditor3393155 LIVE_MATCH07:40:45Z170/684scans43/106slotsRUNNING(observerv19). Preserve frozenproductionbatch; fullrefresh/cleanup/weakSacceptance unfinished.

### S four original-object unlabeled review pack, 2026-10-07 15:08 CST

User confirmed no independent human labels. New contract/test-first8 + related10 =18PASS/Ruff. Reusable scripts/review_s_calibration_objects.py exports exact full original fan members with original/native gate mapping, measured/missing DBZH/SNR/RHOHV, acquisition coordinates/time/geometry and protection; UNKNOWN only, no oldQC-as-truth/model/production actions. Actual selected0812fam40/0818fam31/0830cut5fam8/0848fam69:7000/10186/1921/6930 members and94/283/58/932 targets. Complete26037 and1367UNKNOWN rows directsource/hash/index/mask/moment verificationPASS. .build/s-current-refresh-20261007/calibration-object-review-four-v3 reportSHA08f7b6eede3ab39f8e010a3d40033aa445af164d7500655faf4a15410a0dd691; atlas viewed. Main0818 targetRHO7measured/276missing,SNRmedian5;0848RHO652measured/280missing,median.96,260jointRHO>=.95/SNR>=10 is descriptive NOT independent weathertruth. No universal lowSNR or completefan deletion. No labels does not block bounded evidence/control research, but supervision/heldout weatheraccuracy not justified. Exact originalrefresh2315154/auditor3393155 LIVE_MATCH07:07:53Z164/684scans41/106slots RUNNING(observerv15). No deploy/commit/push/restart/delete. Fullrefresh/cleanup/mainweak-residual acceptance unfinished; preserve sharedWIP.

### S actual runtime renderer filtering / verified refresh wait, 2026-10-07 14:34 CST

Actual105 analysisdiagnosticsworker build_diagnostic_bundle sourceSHA7c0b5c56cf732f686534bce0afeddd8adb7f8a7a3322fb9a9230d98f86a037c7 read: hardreject flags masked +v2QPE_ELIGIBLE_MASK==1 required, absentarray rejects. Receipt s-runtime-renderer-mask-v1 SHAd4c9be1247d105a1d7bc485e3e0de6d4af73f856a8fbbe9cc030d4db26f8f5de. This retires display-still-draws-all-rejected hypothesis; source semantics only, not independentPNG/weather replay. No algorithm/display/default/deploy/commit/push/delete changes. Exactrefresh2315154+auditor3393155 bothLIVE_MATCH06:34:03Z157/684scans39/106slots RUNNING(observerv14). Fullrefresh/oldcleanup/mainweak-residual acceptance unfinished; finite actual-label calibration remains next QC route, not more failed threshold routes. Local releaseguard path found services/control/internal/releaseguard/gate.go; mutating CLI acquire refuses paused (controller recordsERROR), so do not SIGSTOP/restart blindly or pretend existingcontroller auto-waits a new pause. Actual background preservation remains required.

### S X-transfer feasibility / background runtime audit, 2026-10-07 14:27 CST

Rechecked actual S transfer imports: X RAW geometry/fragment/SNR reuse exists offline; current-parent extra CR candidates19/1/27/10 NOT weather truth/production gain. Main0818 Southfamily31 283targets still unresolved. Actual105 frozenprofile/parameters+3 backgroundZIP hashes and libraries verified; remaining2–75km BGmatch6/1/0/2, matched+stable+currentnonmet0all. Background consumed, sameasset rebuild no new evidence. Receipt s-background-runtime-funnel-v1 SHA9a69f4857abb03d32f7ec5168c96eff3660594376ebaf5cef3945c4239f8c1cc. Read actual S Web cycle-panel path:106catalogdiagnostic slots vs684nativeQC/grid scope; no separate684PNG claim/new backfill/restart. Fresh exactcontroller2315154/auditor3393155 LIVE_MATCH155/684scans39/106slots at06:27:48Z(observerv13). Primary RADVOL paper rechecked; docs finite actual labeled-object calibration/heldout weather+mixed controls, not repeated thresholds/source modules or guaranteed all-shape deletion. No classifier/config/deploy/commit/push/delete changes; fullrefresh/oldcleanup/majorweaktail acceptance unfinished.

### 08:12 four-S composite recomputation and unresolved QC exclusion, 2026-10-07

User actual URL pins dbfb9829 historical FOUR-S horizontal product, so prior two-station selection fix does not explain current report. Normal all28 single08:12 run02288687/taskbf592e6b/attempt31dda4ae SUCCEEDED05:37:31Z (31.6min), newseriesc27516b57eecbe236e281b92960336e2b5a85b0ef56c72ab0600dc41e9b07651;4S+21X,3missingX. PublishedS50987 fieldSHA056d696f... exactly independent same-image four-S recompute, four frozen native-gate values and3PNGhashes verified. OldS53632 vsnew only13newfinite/2658nowmissing/50925equal: NOT meteorological completeness fix. Native16 RAW>=35 missing points:10 trusted/QIpositive/nohardreject excluded solely VOR unknown withhold (CRreason8,STATE2,reason129,UNKNOWN1);6alreadyhardreject/QI0. ActualZ9593sea35–39.5/QI1/flags0 stillCRineligible. No QC/unknown/gate changes or weathertruth claim; user actualmissingROI/reference time remains pending.

Web181PASS/buildPASS/newregression actualREDthenGREEN: true seriesframecount, referenceaxislabel资料时次, latest-seriesbutton keeps time. Only2Websourcefiles differ from frozen previous build. Static-only105 indexSHA1a80af45..., PID1149670/binaryunchanged/28oldassetspreserved, no remote source sync. Postrelease browser navigation timed out; no new browser/button runtime acceptance claim. Actual newhorizontal S/joint public echo probe unavailable: volume source tables lackindex/sweep_number but newnumeric probes includeWINNER_SOURCE; Go strict indexed binding rejects. OldS scalaronly stillavailable/unreported, oldjointsameissue. Underlyingarrays/mapcorrect/outside missing preserved. No Go source/deploy or schema relaxation; doc S_COMPOSITE_COMPLETENESS_20261007 includesactualnewlink, cause/limits/next steps. Current recomputation delivered; reportedweatherhole NOTclosed. WiderM1–M4 remains paused; no commits/push/newneighborjobs/otherScontroller state changes. Private evidence .build/s-composite-recompute-20261007/; actual four-S frozenprofilebf582 retained. Do not blindlyrestoreRAW or changeunknownpolicy without nativeweather/countercontrols.

### S completed cross-source experiment / original-member figure, 2026-10-07

Previous turn PROGRESS geometry code/tests+background actualstarted. Current PROGRESS actualterminal4case report retrieved, not restart: geometry-four-v2 COMPLETE_OFFLINE_EXPERIMENT remoteSHAcd960da26d8beeea6c94f1fa3d2dd72c1bb65bef701cc867a2999121df81ac05. Target/geometry/measured/positive0812=28779/11181/10844/1449,0818=7387/4221/2454/358,0830=273/75/0/0,0848=7867/4152/2712/484.14headerdatumassumptions remain explicit, no weathertruth/negativevotes/production actions. Added contract/test-first review_s_cross_measurements.py+10newtests;17relatedPASS/Ruff. Nativeactualfan/Xcandidate intersection+reusablePNG .build/s-current-refresh-20261007/cross-measurement-review-four-v1, reportSHA41d422610b1f5d5fb73b03b7228a273f2f80e8c8c8cb666a8fed420c62274b60. All2291positivepoints originaltarget/protection/unique/fan counts+image hash independently checked, not secondmeteorologicalreplay. FourXcandidatepositiveconflicts0 notweatheraccuracy. Main0818Southfamily31target283positive0Xcandidate1: stillunresolved; do notloosengeometry/positive thresholds or useabsenceasdelete. Figureviewed/docupdated. Originalrefresh/auditor2315154/3393155 LIVE_MATCH at06:07:52Z150/684scans38/106Webaudited, observerv12; no restart/deploy/config/commit/push/delete. Fullrefresh/oldcleanup/mainweak-tail acceptance unfinished;goalACTIVE.

### S actual header prerequisite / bounded cross-source experiment, 2026-10-07

Four frozen parents plus selected refs: verified manifest/logical-root-header reads of14unique RAW sources all altitude_datum/status null; actual configs EPSG5737. Header receiptSHAe4e5c27b124c717ab1d3cc15deed5374fc1a149452b4f4e030f9d15d10acd7c3. Strict firstgeometry replay abortedheadercheck, no QC/actions; remotev1logs retained. Added contract/test-first offline common-datum matcher/launcher,15relatedPASS/Ruff/embeddedsyntaxPASS. Actual coordinates/time/native samples/500m/300s/beamintersection, independentStageA positive-only; no DEM/height/weathertruth/promotion. Explicit offline untagged-header assumption records every source; conflicts still reject. Background105geometry-four-v2PID2322573, workerinputSHA d95a275bfc3da2802818960f4acf494f2cd6ffed5ba8636a67f4b3d6e0d2d4ec,900salarm/nice10/singleI/O; local launch.json retained. Firstlauncher missing offline relative module/receipt duplicate corrected, no duplicate live restart. Originalrefresh/auditor2315154/3393155 LIVE_MATCH at05:51:36Z147/684scans37/106Webslots, observerv11SHA37dbc15ab9061904a60f8d65ac57a12ced39275f053c65676d79a27de3ac6e84. MainweakSfan/fullrefresh/oldcleanup remainunfinished; no productionconfig/classifier/service/commit/push/deletion changes. Do not report geometry assumption/background launch as weather improvement.

### S frozen cross-context config qualification, 2026-10-07

Fresh105observerv10 at05:29:33Z: exactcontroller2315154/auditor3393155 bothLIVE_MATCH RUNNING142/684native scans35/106Webslots audited; immutableplanSHA9039accfbd98dfe0f1f132a16924b686dbddec7999dcbb7f3e7825ca27ab0741. No restart.

Previous goalturn PROGRESS (primary code semantics narrows route) +verified wait. This turn contract/test-first readonly audit_s_cross_context_qualification.py;4new/8relatedPASS/Ruff. Four accepted parents contain12available selected cross refs; actual105worker2 four-site config bytes matchlocal; all12 declarecommonEPSG5737 butexistingverifiedEGM2008 gate0. Actualgeometryresources RADAR_CONFIG_DIR/ANCILLARY_CONFIG/ANCILLARY_ROOT allunset. Receiptv2SHAacedf2152e30fa5dd5ddf4c7bb52d55ef7042c998cee7abdec2f3d09bf2ea965; independent executedscript/parent/configbytes/paircountsPASS. No nativecontextreload/weather/overlaptruth: all12historicalingest unknown. V1incorrectresource names retainedunaccepted; v2 usesactualqc_resources and requiresactualverifiedstatus beyondsameCRS. Next finite same-declared-datum beam comparison needsactualheaders/time/sampling/independentdonors, no inventedEGMconversion/bypasspromotion. No QC actions/deploy/restart/delete/commit/push/sharedWIP changes. Whole684/106refresh,backendcleanup andprincipalweak-tail acceptance remainunfinished.

### S special-code research closure, 2026-10-07 05:17Z

Rechecked actual S transfer imports: X RAW morphology/fragment/SNR carrier reuse already implemented, no new migration gain. Primary PyCINRAD get_raw source masks codes<5 as reserved information and identifies velocity1 folded; does NOT prove REF0 valid noecho for our files. No authoritative actual REF0–4 table obtained; decoder/unknown states unchanged, whole-ray discontinuous-count branch held, no new thresholds/promotion. Finite next mainline remains qualified positive neighbor/elevation weather evidence within frozen original objects; physical isolated-object validation separate. Actual read-only105 observerv9 at05:16:49Z: originalcontroller2315154/auditor3393155 LIVE_MATCH RUNNING138/684scans35/106Webaudited. Fullrefresh/oldcleanup/mainweak-tail acceptance unfinished; no deployment/delete/restart/commit/push/sharedWIP changes. Docs S_GENERIC_QC_NEXT_STEPS updated; existing four-case figure is QPE eligibility preview, not CR-effectiveness proof.

### S discontinuous SPIKE input/features experiment, 2026-10-07 05:08Z

Previous goalturn was verified wait plus research plan; this turn PROGRESS: contract/test-first offline s_discontinuous_spike.py and research_s_discontinuous_spike.py, 7 new/17 related tests PASS, Ruff/diff PASS. Full-ray echo span lower/upper bounds retain unknown; 4°/40° angular contrasts and measured 2° DBZH/linear-Z variance use native geometry, no NaN-as-clear-air. Four accepted current parents all incremental CR/QPE nominations0; measurable target variances0812/0818/0830/0848=22021/3142/108/3679, all target angular counts uncertain. Fully measured weak-weather counterexample nominates150/150: diagnostic is not source confirmation and not promotable. NOT full upstream RADVOL (no higher-elevation nomination/final ray confirmation); no production changes. Receipt SHA63b7373eb0b4982e72a888c3731606032aee5363e9a3b9bcef0542d2f1b2c534; input/parent/source/output/figure/count verification plus independent full echo bounds and5directangular/variance rows/case PASS, not second full replay. PNG viewed/reusable script retained. Next prerequisite is verified actual base-data special-code semantics; fmt.py preserves raw_gate_codes but0–4 not independently no-echo, no protocol guessing/no IQ request. Fresh105 observerv8 05:07:46Z exactcontroller2315154/auditor3393155 LIVE_MATCH RUNNING137/684scans34/106Webaudited. Whole refresh, MinIO maintenance/old cleanup and main weak-tail acceptance unfinished; goalACTIVE, sharedWIP preserved, no commit/push/deploy/pause/restart/delete.

### S anchored SIR finite experiment closure, 2026-10-07 04:30Z

Previous goalturn PROGRESS terminal-history cleanup preview/literature plan; thisturn PROGRESS actual offline4case experiment. Added contract/test-first s_anchored_sir.py +research_s_anchored_sir.py,10new/34relatedPASS/Ruff/diff. Integer exact80%seed interval rank validated vsallintervalbruteforce; originalray/family only, acceptedrisk3+RAW25seeds, measured/protection/familybarriers, boundedbothoriginalseeds≤5kmgap, noangular/recursivegrowth/newactions. Fourfrozenactualparents0812/0818/0830/0848 nominees0/6/0/16, QPEsame,CR0all,0818Southfamily31extra0. Explicitunprotectedweathercrossing4/4weathergatesnominated proves notautonomousclassifier; protectedloss0NOTweatheraccuracy. CloseSIRstandalonedeletionbranch/nothresholdloosening/promotion. v1/v2countsidentical(v2formatonly), finalreceiptSHA9df741f39883007bd9b9f64a3e0933a00b9dadb1fe62e0b334dc92150014d671; RAW/input/parent/source/output/figure/counts+directoriginalintervalandbracketchecksALL22actualnomineesPASS(notsecondfullreplay). PNGviewed/reusablescript/docsretained. Actual105 observerv5exactcontroller2315154/auditor3393155 LIVE_MATCH RUNNING128/684scans32/106Webverified; productionQC/config/image/Web/deploy/commit/push untouched. MainweakSfan/fullrefresh/MinIOrepair/olddeletionunfinished, goalACTIVE/sharedWIPpreserved.

### S finite morphology research / terminal-history cleanup preview, 2026-10-07 04:22Z

Four S screenshots reviewed against actual current-parent PNG and latest X counterexamples. Added literature/finite case matrix to S_GENERIC_QC_NEXT_STEPS: astronomical SIR may nominate anchored short gaps within frozen original members, not new source/classification or automatic weather-radar removal; no recursive anchors/missing bridges. Long weak0818South unresolved; existing power/fingerprint/Doppler zero-gain branches remain closed. DWD SQI/pulseSTD unavailable, no invented substitute or request for absentIQ. No QC production code/config/actions/promotion changed. Fresh exactcontroller2315154/auditor3393155 LIVE_MATCH RUNNING126/684scans32/106Webverified; allrefresh/weatheracceptance/cleanupunfinished.

Actual1,219stable oldnumeric referencedprefixes haveonlyjobsrefs:3,726uniquejobs/4,448job-prefixpairs allterminal(SUCCEEDED2578/FAILED1867/SKIPPED3 pairs), NOTthe23globalhistoricalnonterminaljobs. Added explicit --allow-terminal-job-history to pruner/contract/test-first8new,27relatedPASS/Ruff; defaultstillallrefsprotect. Exemptionpreservesimmutableauditrows/URIs, requiresother7refszero/nooutstandingoutbox/frozenjobID/status/time/requestSHAfreshmatch. Singlemaintenance script deployed/hashverified throughisolatedcontainertargetscriptsmount afterpermissionfailedscp; noQC/runtimeimage/config/servicechange. ActualZ9593old7.1.0preview15761objects62947704B,2SUCCEEDEDjobs/otherrefs0/outbox0; receiptSHA0dd2681713aeef0adb0f9c42197c9be10f49428d67e19351db43dd09d5c231f6; source/inventory/history/countconsumerPASS(notsecondS3listing). NO--apply/delete/DBmutation. MinIOoverlayrepair/write-drainstillpending. This correctspreviousassumptionallterminalauditrefsneeddirectGo-rowretirement; oldtestbytescanretirewithauditpreserved, withoutfabricatedstates. SharedWIP/commit/push untouched.

### S all-generation reference prerequisite, 2026-10-07 04:07Z

Previous goalturn research review only; this turn concrete cleanup prerequisite PROGRESS. Added readonly audit_s_qc_generation_references.py +contract/test-first8new,19relatedPASS/Ruff. Actual105 all684 inventory/actualplan bound; eightclasses one repeatable-read READ ONLY transaction15s/class, canonical URI occurrences only/100kentries16MiBbound, recipe-only references protect rebuildchildren.6scan currentcatalogdrift withheld;678stable directories: oldnumeric205zero/1219referenced, otherrecipe1029zero/446referenced, sameversionoldrebuild166ALLreferenced,802currentprotected. Counts aredirectories/URIoccurrences NOTdistinctjobs/gates/bytes; ALLdeletion_readyfalse, no object inventory/newmount/delete/reference retirement. Thus mountrepairalonecannotfulfilalloldcleanup; majorityoldnumeric andsameversion retainedreferences needdependencyhandling. v4receiptSHAb47b31ae99946ed19f4850c8dbf53ce5a586554c3c85f566b143158d7db20d78; source/inventory/classifier/all684scope+5sample directoriginalprunerqueriesPASS(no fullsecondDBaudit). v1failedhelperimport(tooltrace),v2receiptmissingurlparse,v3multilinePostgresJSON parse failed; preserved, fixedhelperdependencies/JSONstream+regressioncontrols, NO loweredreferenceconditions. Freshobserverv3actualLIVE_MATCH2315154/3393155 RUNNING123/684scans31/106Webverified04:07:05Z;23historicalnonnullstatuses unchanged, noownedpause/storagefix. GoalACTIVE/refreshandcleanupunfinished/mainweakSfanstillunresolved; noQC/Web/config/deploy/commit/push mutation, sharedWIPpreserved. Next finite retire referencedobsolete workflow/assets through supportedGo control and repairMinIO under ownedactualdrain, not arbitrarySQL/oldresultreplay/rmtree.

### S transfer review and storage drain observation, 2026-10-07

Read-only storage observer receipt SHA4262ca6418ca788d04de2db1407b18a9ed3a5b2cc3be65f2279caef9afb82fae: actual refresh2315154/auditor3393155 LIVE_MATCH,118/684scans30/106Webverified03:47Z.23historical Sep nonterminal jobs(grid12pending/8running,mosaic1,qpe2) all regeneration_request_idNULL; respective request consumers pending/ack0, NOTproof dead/cancellation authority. ops.multiband ACK2 and noownedreleasegate prevent globaldrain claim; old resultsconsumer772pending/currentv2zero, doNOTblind replay. No stop/pause/retirement/storage repair/delete; goalACTIVE/backgroundstillmoving. Receiptcounts/scope/unique23/hashchecked, notindependentlive rerun. Cleanup doc updated.

User S/X reference review: existing native transfer CR extra19/1/27/10 and anchored QPE0/12/0/59 doNOTsolve0818South. Latest X two-dimensional enhancement also fails weather controls(160/161 at1deg,480/483 at.5deg),10/11actualobjects admit bothangle/km-width hypotheses. Thus no direct newXclassifier promotion or blanketfan removal. S research doc now records finite next member-level source/weather/mixed/unknown competition with originalfrozenboundaries, qualifiedpositive weather and separatephysicalspeckle checks; fourcases+weather/samplingcounterexamples and stoponzero mainbenefit/failingcontrols. Actualcurrent-parent4casePNG inspected; candidateonly/notnewpublishedimprovement. No businesscode/deploy/profile/commit/push changes; sharedWIPpreserved.

### S full frozen QC generation inventory, 2026-10-07

Previous goalturn PROGRESS: actual finite foldedtexture closes scalar-threshold branch, notweathercleaning. This turn read-only inventory_s_qc_generations.py contract/test-first5new+6pruner=11PASS/Ruff. Actual105 full684frozenplan S3 shallow scan/recipe/currentrebuild discovery, before/after catalog drift0;3911directories:1442oldernumericrecipes452scans,684currentrecipes,166sameversionnoncurrentrebuilds42scans,126currentrebuilds,1493otherhistoricalrecipes(rp043/rp047/fujian-evidence). These aredirectory counts NOTobject/gate/bytecounts, currentrebuild126 NOTcontrollerprogress. All deletion_readyfalse/referencequeriesfalse/productionwritesfalse; existing pruner coversonlyoldernumeric so clearingoneprefixcannotfulfilalloldcleanup. ReceiptSHA57301fc0622edf4fa587fee0c2e564ac5702749497c887a6767e940d1e9a66c2/source0655106b51c079ad75d6b7da8d4723cd16928a89408fd499f4838d9eef88c727; actualremoteplanSHA9039.../all684IDs+stations/hash/unique3911/currentstable/count consumerPASS, NOTsecondS3enumeration. Next controlledMinIOmaintenance/fullreference+currentgeneration review, then boundedS3prefixpurge; sameversion/otherrecipes needexplicitdependencyreview, no rmtree. Actualexactcontroller2315154/auditor3393155 LIVE_MATCH116/684scans29/106Webverified03:37:26Z; allrefresh/storagecleanup/mainweakfan unresolved, goalACTIVE. No production/runtime/config/profile/classifier/delete/commit/push changes, sharedWIP preserved.

### S folded Doppler texture finite experiment, 2026-10-07

Read-only --velocity-texture requires --object-context; actual donor Nyquist/full original 3x3 measured VR+SW, native good/gaps/time/elevation/range guards, missing stays unknown/no edge padding. Contract/test-first10new/26relatedPASS/Ruff; current four target measurable6451/821/78/1006, medians.93/1.10/1.16/.74 versus protected context.88/1.76/1.63/.97. Overlap prevents universal weak-tail deletion; protected context NOTweathertruth. Exact0818South family31 targets283/fulltexture0, so cannot solve farweakfan by Doppler. Finalv2 receiptSHA dc2e82b2d20e005754d10c6f63f5bbf05c93db55e0e51d894e2bf434123d861e; old availability/context unchanged, source/input/parent/count PASS plus independent actual0830 same-cut direct3x3 count78/allquantiles PASS, NOTsecondfullcrosscutreplay. V1 retained; v2 float64/request-binding correction. Reusable plot mode/PNG/receipt inspected. Close scalar/texture threshold branch; retain localfeature but no classifier/newactions/promotion/profile/restart/deploy/commit/push. Mainweakfan original-object ownership/weather proof stillpending. Exactcontroller2315154/auditor3393155 bothLIVE_MATCH RUNNING112/684scans28/106Webverified at03:20:02Z; allrefresh/storagecleanup unfinished/sharedWIP preserved.

Latest exact-handle readback03:28:44Z: both LIVE_MATCH,114/684scans29/106Webverified; stillRUNNING, notwholegoalcomplete.

### S paired Doppler object context, 2026-10-07

Previous goalturn PROGRESS Doppler coverage. Added explicit --object-context to existing readonlyaudit, contract/test-first4new RED/GREEN;16relatedPASS/Ruff. Actualfrozenfour sourceVR+SW measured186/990/0/563 vsstrongsources988/43106/174/42359. StrongsourceSW medians4/16/unknown/15.5, target4.5/4.5/1.5/3.5, protectedcontext4.5/5/2/5.5. Quantileranges overlap: no universalVR/SW threshold/newclassification/removal/promotion. ControlsareprotectedcontextNOTindependentweathertruth. Exactfamily31(0818South157–167deg) actual10186members/283currenttargets/1Dopplermeasured, strong3902/11measured; prior5263waswholeheldfamilyaggregateNOTfamily31targetcount, originalreport itselfcorrect. ReceiptSHA6df843324a4f38bc09069ae8f2ca9f542e58291a9083b01a91a42e1e1ccbf5f4; oldcoverage/donor results unchanged, source/input/parent/count+independent283recountPASS(notsecondfullcrosscutreplay). plot_s_doppler_context.py retained+PNG/receipt inspected; noWebgain claims. OfficialPyARTvelocitytexture API checked; nextfinite onlydonorcut/actualNyquist/fullmeasuredneighborhood foldedtexture contrast beforeanyclassifier, notthreshold4/6copied; beyond230missingunknown. Initiallivecontroller2315154/auditor3393155 RUNNING109/684scans27/106verified; noproductioncode/profile/restart/deploy/commit/push, sharedWIPpreserved. Fullrefresh/oldstoragecleanup remainunfinished.

### S actual Doppler coverage experiment, 2026-10-07

Previous goalturn PROGRESS observedSNR denominator/domain audit. This turn new read-only original4volume inventory establishes low REFcut0 excludesVR/SW while sameelevationDopplercut1 contains measuredVR/SW onlyto229.875km (REF459.875); target0830cut5hasownVR/SW. Notadapterbug/crosscutmergeallowed. Contract/test-first audit_s_doppler_coverage.py 8new RED/GREEN +4currentparenttests=12PASS/Ruff. Strict uniquehalfcell/samevolumestation/el<=.1/time<=60/native_geometryacquisition/Nyquistfromdonor/noextrapolate. Actualfour targets0812/0818/0830/0848 measuredVR+SW8818/1370/109/1507 vs28779/7387/273/7867, unknown19961/6017/164/6360. Parent/RAW/profile bf... hashes bound; no duplicate donor, newactions0/classification0/defaultoff researchonly. ReceiptSHA4c83ffc71ec08dbd32e369d28cd8724dbdbea5716c195819a7fe4677753fe445; source/input/countproof +independent0830samecut109PASS, notsecondfullcrosscutreplay. InitialremoteNativeSweepmissingnewlocalattr correctedtoexistingnative_geometryAPI +actualfunctionSHA; noproductionfault/restart. Nextfinite actualmatchedDoppler object/weather-counterexample experiment; neverzeroVR/narrowSWalone delete, never missingbeyond230asnegative. No promotion/deployment/commit/push; sharedWIP preserved. Freshstart exactcontroller2315154/auditor3393155 LIVE_MATCH RUNNING106/684scans26/106Webverified, fullrefresh/cleanup/mainweakfamily pending.

### S current observed SNR and distinct output-domain audit, 2026-10-07

Read-only four frozen accepted-parent audit v2 SHA1d9dfaa34b4c4146ea916ef0b992b2952db153206f5b6a9d47a34468df0fbadc binds exactRAW/nativepermutation/input/parent/profile/flags and actualcontainer parametersbf582b..., not localmodel newly-defaulted parameterhash. Actual rain DBZH>=-10 +availableSNR<3:6860/5650/1071/5431, all flag matched0missing/0extra. V1 all-SNR denominator included unavailable/noechoDBZH; v2 corrects, original retained, notflagbug. Current unprotected visibleQPE28094/6963/94/7417 all0lowSNR; CR973/645/194/650 lowSNR8/4/0/0. These includeweatherunknown/notROI truth. Local renderer singlepolarQC usesQPEeligibility; composite usesdistinctCReligibility, do notconflate. SimplelowSNRcutcannotfixmainresidual; no threshold/code/worker changes or newclassification/promotion. First bounded probes had runtime/import/key preflight errors, fixedbeforewriting final v2, no productionjobfault. Officialwradlib classify/fuzzy source rechecked; docs finite objectownership/heldout/weather evaluation, freezezero-gain branches. Fresh105 exactcontroller2315154/auditor3393155 bothLIVE_MATCH RUNNING105/684scans26/106published+verified at20261007T024836Z. Fullrefresh/cleanup/mainweakfamily stillunfinished; sharedWIP preserved.

### S actual worker memory and stage diagnosis, 2026-10-07

Previous goalturn verified wait+actual DB latency. This turn fresh exactprocess+health/logs/smaps evidence: object cache both0bytes/0entries/disabled, so notassetcache leak; current anonymousRSS~43/41GiB measured independently of qc_worker._process_rss_bytes historicalru_maxrss. Three episode assets unpack500910817/450711834/450937468bytes, bounded512MiB each; near assets19.46MB each, not40GiB source. Native maps5944 but only15near-exact64MiB mappings/~960MiB; do notinferwhole40GiB isfreeallocatorarenas or claimmalloc_trim fixes it. Actual last three acceptedQC logs input1.0–1.4s/context48–59s/core45–86s/serialize_validate42–52s; allocationretention rootunknown, no patch/restart/gc/arenaenv tweak/full-QCconcurrency. Huntapplied/report-only rootcause discipline, nohypotheticalfix. One readonly probe footer NameError missingpathlib aftersuccessful memory observations; separate exactcontroller/auditor observation re-polled, notjobfailure/restart. Latest beforefooter98scans25published+verified, bothhandlesLIVE_MATCH. Freezeactivebatch; don'tturnthisdiagnosis intoopenendednewperformanceproject. MainSresearch sparseunknownlimits retained, no newproductionclassification/commit/push; sharedWIP preserved.

### S refresh actual latency observation, 2026-10-07

Previous goalturn PROGRESS (actual persistent mount plan); this turn verified live refresh/auditor handles and DB timings, no restarted jobs or widened QC thresholds. Frozen refresh-timing-v1.json SHA5ac5668fd61b56023c8444de874aea501dd58bbd2c5951c70d11885db2755461 binds actual plan/state/source and exact handles. 98 completed native scans,24published+verified Webslots; current10:30CST diagnostics job2847530b-6027-5320-b7d5-2005b187784f actuallyRUNNING. Completed-stage median QC163.8s/grid28.1s/diagnostics159.5s/mosaic2.4s/QPE0.2s; notstuck/notmosaicbottleneck. Both QCworkers retain43.16/40.97GiB, availablememory~25GiB/noswap; do notadd full-QC concurrency withoutresourceproof. Remaining586scans/82slots, fullrefresh/cleanup unfinished. Actualperformance evidence supports maintaining serial frozenrun and researching QC/diagnostic allocation outsideactivebatch, not changing profile/worker midrun. No production mutations/newpromotion/commit/push; sharedWIP preserved.

### S storage persistent maintenance preparation, 2026-10-07

Read-only plan_minio_overlay_maintenance.py actual105 receipt PREPARED_ONLY_WAIT_FOR_WRITE_DRAIN, SHA4125561e02313d46c91da99a86e32f16408b571d84d155d9fa74004683a58aca. Exact live/fstab sibling layers, original lower bind, systemd SourcePath=/etc/fstab, MinIO RW volume/image/PID and whole-job backlog bound; only unique overlay line proposed redirect_dir=on, unrelated fstab never emitted/changed. Contract/test-first5PASS, independent receipt/source checkPASS. Actual24nonterminaljobs (mosaic1/QPE2/grid pending12+running8/QC1); no global drain established. Linux6.8 legacy remount ignores option changes; need owned admission pause/controller safe boundary/jobs+outbox+consumer drain then MinIO stop/strict unmount/new mount/actualoptions/S3reads/resume. Fullbatch completion not intrinsically required, but do not bypass release gate or cancel unrelated jobs. No production config/mount/service mutation, cleanup unfinished, no commit/push/newalgorithm promotion. Latest exactcontroller2315154/auditor3393155 LIVE_MATCH RUNNING97/684scans24/106published+verified. MainS weaktails stillunknown; currentfour relative-weather654/0/0/0 provides protection notnewremoval. Docs S_REFRESH_CLEANUP updated; sharedWIP preserved.

### S current four relative-height weather evidence, 2026-10-07

Previous goalturn PROGRESS(actual DEM-domain negative audit); this turn PROGRESS. Existing audit_s_relative_vertical.py gains --current-parent (contract/test-first4new+4existingPASS/Ruff/diffPASS), exact frozen parent/input/nativepermutation/RAW/profile/runtimefunction bindings, original support unchanged, historical CLI retained,4case/900s bounds. Actual four current parents support0812/0818/0830/0848 =654/0/0/0 unprotected weather-positive gates; all action0, all overlaps with X-transfer morphology candidates0. 0818physicalpairs54674→cleanupper1→positive0;0848physical63817→cleanupper0. Missing/non-comparable upper remainsunknown, notnegative/deletion, notindependent weathertruth. Finalrelative-weather-current-four-v1.json SHAa363ea3ca40b308e4af4ceb34de6b475834bb10ef36c40e69a204967b889900c; independent receipt/input/domain/source/candidateintersection checkPASS, notsecondwhole-volume run. Reusable plot_s_relative_weather.py and inspected PNG retained; docs/S_GENERIC_QC_NEXT_STEPS_20261003.md commands/results updated. This completes proposed fourcase positive-weather check, notmainZ9598weakfan resolution. No threshold relaxation/workerrestart/newclassification/productionpromotion/commit/push; sharedWIP/default preserved.

Latest105 exactcontroller2315154/auditor3393155 bothLIVE_MATCH RUNNING94/684scans23/106Webslots published+verified, currentdiagnostics20260828T0224Z. Fullrefresh/oldstoragecleanup unfinished; storage maintenance stillrequires completedpublication+write drain. Don't duplicate prior zero-gain distance/relative-upper branches.

### S actual geometry resources and DEM target coverage, 2026-10-07

Previous goalturn PROGRESS(exact-binary isolatedS3proof); thisturn PROGRESS(actual four frozen target DEM-domain negative audit)+verified live wait. Both105 v8 S Workers currently omit allthreegeometryenv andancillarymount. Private samev8 readonly/no-network container loads allfour1985configs +acceptedDEMmanifestSHA9f4108b05da5b8a119b06e96225370be0d02a1d75aad1d3b2aa127b5ee571f44 andactual site raster samples finite; allfourdatum incompatible_with_epsg_3855, literature-offset config notverified. No productionrestart/env/profilechange. New geometry-domain-four-v1.json bindsactualinput/parent/config/functionSHA andnativeorder: unprotected targets0812/0818/0830/0848=28779/7387/273/7867, outsideDEM=0/0/0/2, existing slant vsrelativeground domainmasks identical. Notweatherlabels/action; expandingDEMwon'tfixmainfourresiduals. Existing20261001relative_vertical positive-only module/audits alreadydoneold0836/0842zeroROI; nextfinite adaptexistingaudit toactualfourcurrentparent (NOTnewalgorithm/notyetexecuted), missingupper remainsunknown. Latestexactcontroller2315154/auditor3393155 bothLIVE_MATCH RUNNING90/684scans23/106Webslots published+verified; fullrefresh/cleanup unfinished, sharedWIP/default/commit/push untouched.

### S obsolete-storage exact-binary S3 proof, 2026-10-07

Isolated `probe_minio_overlay_s3.py` completed with the production MinIO executable (SHA060947e66dfcf128a4ede1518fa9131501431b0ff1782c756d34b7a042598c4c) on105 kernel6.8.0-124. No production data/env/credentials copied; no hostbind/productionvolume/network/ports. Private256MiBtmpfs/1GiB/.5CPU/64pids/180s bound. Actual non-inline lower objects reproduce production S3 InternalError/EXDEV in default overlay; redirect_dir=on passes single/bulk delete, empty old prefix, same-key recreation and untouched-current-byte protection. Both private mounts unmounted/containerremoved. Finalv3 PASS_ISOLATED_S3_ONLY, receiptSHA5ece17a89192d1a80618b6d02ae716a21dea8961473831aa8727ea8bd4908448, sourceSHA3c239ff350568030fa4a7fb25ef918609b380afd63cbe96c51f2e100c6769313; independent identity/lifecycle receipt checkPASS,4tests/RuffPASS. v1 inline-only andv2 retry-obscured negatives retainedINCOMPLETE. Earlier isolated kernelv5 proof alsoPASS,3tests; reusable commands/contracts in docs/S_REFRESH_CLEANUP_20261007.md.

Production NOT repaired; old cleanup incomplete. Next storage maintenance only after full refresh/publication audit/write drain, inspect persistent mount ownership/options, then fresh all-reference/current-generation/inventory checks and one-prefix S3 empty proof. Do not alter active underlayers or global sysctl. Latest exact controller2315154/auditor3393155 bothLIVE_MATCH: RUNNING89/684scans,22/106Webslots published+verified. Four renewed S cases already current; X morphology transfer gives little incremental benefit, observed distance-fingerprint branch closed (zero new targets). Main residual remains uncertain weak original-fan membership; no more loose threshold variants, no experimental production promotion. Fullgoal unfinished; accepted worker/profile/default/sharedWIP/commit/push untouched.

### S measured donor-profile closure, 2026-10-07

Previous goalturn PROGRESS (actual temporal/source overlap); thisturn PROGRESS. New readonly audit_s_observed_source_profiles.py contract/test-first7PASS/RuffPASS: independently anchored original same-family donor rows (>=4 strong five-gate windows/span100km excluding target +/-1; entire target row excluded), actual unprotected RAW measurements describe profile but never create anchors/ownership. Same unchanged fingerprint criteria, frozen original strong range bounds, no fill/crop/recursive expansion/action. Four observed-source-profiles-four-v2 audit SHA f35783dd8daf11e3255c33f7c6a56e4b9be1e69909d7fa746d08994e59bb5c8b; input/model/parent stable, RAW unchanged, independent hashes/summary/v1-v2 accounting checkPASS. 0812 13windows onecomplete donor -> insufficient;0818 12windows 4–5complete donors but9profile disagreement/3insufficientvariation;0830 no prior failed windows;0848 36windows 2–4donors but34insufficientvariation/2insufficientdonors. **New compatible targets0 allfour**, diagnosticnotweathertruth. Charges2.87–45.03M<=50M. This changes rootcause: 0818/0848 not simply missing confirmed-cell profiles; restoring actual source RAW doesn't identify shared fingerprint. Close distance-fingerprint expansion, no tolerance relaxation/production promotion. Preserve reusable command and negative evidence in nextstepsdoc; main residual need original-object morphology/weather counterexamples rather than more power-fit variants. Latest105 exactcontroller2315154/auditor3393155 bothLIVE_MATCH RUNNING82/684scans21/106Webslots published+verified; currentQC0477f848-1192-52fc-9784-22bfdbe5aa09. Fullrefresh/oldMinIOcleanup/promotion unfinished, sharedWIP/default/config/commit/push/deploy untouched.

### S temporal confirmed-source overlap, 2026-10-07

Read-only optional --source-generations added to audit_s_temporal_alignment.py; contract/test-first6PASS, Ruff PASS. Exact frozen completed accepted past QC generations only, no older fallback; original RAW equality/native row mapping, V3 confirmed risk3 + RAW>=25 + both original family identities, all inherited source weather/conflict/barred protections. Four actual inputs mapped strong/owned target counts0812 1/1,0818 0/0,0830 1/0,0848 319/305; 14 of0848 and1 of0830 lack current family, cannot create ownership. 0848 other historical RAW source not yet in frozen completed refresh remains unknown. These are source overlaps, NOT new classifications/clearance/weather accuracy. Receipt temporal-source-anchors-four-v1.json SHA847667f28ea6d66958aab2e0cca767021114426e65d07f8cfbdcf7eb67e3aa7f; independent receipt/domain/generation/summary checkPASS (no second full source-array replay). Exact/aligned counts same previous v2, runtime/RAW/script/profile stable, audit work47.16–90.06M<=100M, production50M unchanged. Existing NP recurrence requires>=2 past references, firsttwo haveonly1 and source overlapnone/negligible; enabling matching alone cannot solve main residuals. Stop temporal-as-uniform-removal proposal; next bounded evaluation is same original object/source boundary evidence, especially0818, with independent weather counterexamples. Latest105 controller2315154/auditor3393155 bothLIVE_MATCH RUNNING80/684scans20/106Webslots verified. Production/default/config/deploy/commit/push unchanged, sharedWIP preserved; fullrefresh/cleanup/promotion unfinished.

### S actual RAW temporal alignment gap, 2026-10-07

Previous goalturn PROGRESS:61-window sparse-source negative result; this turn PROGRESS. Actual four accepted parents have1/1/2/2 frozen past RAW refs but exact recurrence geometry rejects all with temporal_spatial_matching=False; cross-radar also config/beam/terrain unavailable. New read-only audit_s_temporal_alignment.py contract/test-first4PASS/Ruff/diffPASS. Server v2 uses existing bounded nearest (no tolerance increases), function-SHA equality plus actual runtime whole-module binding; first whole arrays-module mismatch rejected before reads, inspected invoked functions identical, no worker/source replacement. Actual unprotected target counts28779/7387/273/7867; measured past0→12005/1849/120/5694, two-prior stable0/0/1/281. NOTcluttertruth/action/effectiveness; all6 historical ingest timestamps unknown. RAW unchanged; independent input/parent/mask-domain/source/summary checksPASS. Offline work42.88–85.77M≤100M separate audit allowance, production50M notraised. Receipt .build/s-current-refresh-20261007/temporal-alignment-four-v2.json SHA546ef54e66aab50dc54229154c078907c8d400b54f4459a0a80abcb205a610bd; reusable command/docs retained. Next finite joint original-source qualification with restored measured pairs, not recurrence-as-class. Latest105 exactcontroller2315154/auditor3393155 bothLIVE_MATCH RUNNING77/684scans19/106Webslots; no config/profile/deploy/commit/push/sharedWIP changes. Read-only Z9591oldQC layercheck all21versions presentlower, no unsupported purge retry/mountchange. Fullgoal/cleanup/promotion unfinished.

### S sparse-source feasibility closure, 2026-10-07

Read-only `audit_s_sparse_source_profiles.py` with contract/test-first4PASS/Ruff/diffPASS. Four actual accepted inputs: 13/12/0/36 held windows; all61 minimum measured source rows=1, connected joint feasibility0. Relaxing complete-per-ray representation would not supply absent common measurements; stop this fingerprint branch, no threshold/model/promotion change. Executed source/report hashes and four masks independently match prior baseline; candidates remain0/12/0/59, CRgain0, not weathertruth. Receipt `.build/s-current-refresh-20261007/sparse-source-audit-four-v1/sparse-source-audit.json` SHA421694761e7aa66e7e97e4c0ba70cd64576da00bdba987dfe737856e219ae5af; reusable scripts/figure retained. Docs next finite alternative is original-object temporal/acquisition-state evidence plus actual-height independent weather context, not another zero-gain threshold branch. Last observed105 controller/auditor bothLIVE_MATCH RUNNING73/684scans18/106published slots; fullgoal/cleanup unfinished, production/default/sharedWIP untouched.

### S old QC partial purge observation, 2026-10-07

Postfailure S3preview-v2 exactsameprefix/zeroDBreferences counted18,238objects89,370,615logicalB vsinitial19,236/95,470,602:998namespaceobjects partially deleted before firstbatcherror. FAILEDoriginalreceipt nowrecordsremaining/partialcounts, retainsinitialinventory; physicalspace reclaimNOTverified, noDELETEDclaim. Cleanup/backendrepair stillunfinished; currentRAW/QC/Web/Sbatch/X untouched.

### S old QC purge failure root cause, 2026-10-07

Previous goalturn PROGRESS+livewait; thisturn PROGRESS:09:42normalWeb publication auditor verified17slots, latestexactcontroller2315154/auditor3393155LIVE_MATCH RUNNING70/684scans17/106slots,current09:48diagnostics. New bounded `prune_obsolete_s_qc.py` contract/test-first6PASS/Ruff/diffPASS, only older same-scan numeric QC prefix, all formal/managed/job/catalog/assetmetadata/diagnostic refs zero, ≤50kobjects/2GiB, doublecheckinventory/current-generation, S3-only/empty-prefix proof. ActualZ9591 0812QC1.0.0 19,236objects95,470,602B attemptedS3deleteFAILED InternalErrorEXDEV. FirstreporterwrongDeleteError.object_name fixedtoactualname withreal-library regression; originalfailedreceiptretainedcorrectedFAILED,no successfulcleanupclaim. Singleobjectprobe samebackenderror. Read-onlyexactMinIOPID1mountinfo proves/data overlay lower=/home/yons/hwapp/dis/rainpulse-minio-overlay/lower plusupper/work; same-device data/tmp/trash, lower-directory rename restriction. NewMinIOsourceschecked exactversion; no unsupported filesystemdelete/mount/servicechange. Evidence/docs `docs/S_REFRESH_CLEANUP_20261007.md`, remote `.build/s-all-current-20261007/*cleanup*json`, `minio-backend-mount-observation.json`. Cleanup needs later validated storage maintenance; activeQC/batchnotblocked, no goalblocked/completion. Production/default/profile/X/sharedWIP preserved.

### S refresh cleanup inventory, 2026-10-07

Previous goal turn PROGRESS: finite fingerprint negative-result proof. This turn PROGRESS + verified live wait: exact105 controller2315154/auditor3393155 bothLIVE_MATCH RUNNING66/684native scans16/106published slots,current09:42 diagnostics. Read-only DB inventory `derived-history-inventory.json`:66completed scans have66runrecords/66distinctQC/66grid URIs, so DB overwrites current addresses and is NOT old-object inventory. Bounded nonrecursive MinIO QC-prefix listing for exactZ9591 0812scan found21version directories; saved `qc-directory-inventory-z9591-0812.json` beside remote state. Existing retention API applies managed candidates only, not formal radar artifacts. No deletion: old formal QC/grid may still feed unreplaced downstream/context; after refresh, freeze obsolete-prefix inventory and check complete active catalog/jobs/dependency references before purge. RAW/current products/X jobs preserved. Fullgoal unfinished, no new threshold branches or production changes.

### S independent range-fingerprint finite experiment, 2026-10-07

Optional default-off anchored module v3 adds independent source-ray distance fingerprint after stationary-power failure: target entire ray excluded, ≥3 actual confirmed source rays in all references+held window, reference-only normalization/constant offset, nonmonotonic identifiable variation, same1/1.5dB/protection/frozenbounds/shared50M. Contract/test-first;80related PASS/3existingwarnings/Ruff/diffPASS. Four standalone `anchored-fingerprint-four-v2` + final writer `anchored-fingerprint-writer-four-v1` complete/stable/validated; independent mask/order/bit8/action/QI/QPE-CR delta/source family checkPASS. Exact same 0/12/0/59 candidates as prior, **fingerprint adds0 / CRgain0**, reportSHA74c95f3b5e60212725df92d001804bd9e9abc08044d9a3a1aee803e300ecb14d. All61 previously nonstationary windows lack ≥3 complete independent source profiles; no threshold loosening/promotion/weatheraccuracyclaim. PNG viewed/reusable scripts retained. Freeze this negative-result branch; future sparse-observation original-object joint review is proposed, NOTimplemented. Fresh105 exactcontroller2315154/auditor3393155 LIVE_MATCH RUNNING65/684scans16/106slots,currentgrid3bfc6874; no production/default/config/commit/push changes, sharedWIP untouched. Full refresh/deletion/main-residual acceptance still unfinished.

### S anchored residual final-writer integration, 2026-10-07

Previous goal turn PROGRESS: bounded offline 71 candidates; this turn PROGRESS: optional route now integrated into final S transfer/native permutation/disposition/serialized delta. `routes` morphology(default exact old export/configSHA34963…), anchored_fan or combined; shared50M/atomic withdrawal, original family+accepted V3 risk3 mandatory, dedicated masks and reasonbit8. New candidate config `configs/qc/s-anchored-fan-20261007-candidate-v1.yaml`, dispositionSHA899f16c06ca9c57db5afe8f2b315edcc08e5147412493ccff72d230d5e3a344a. 65related testsPASS/3existingwarnings/Ruff/diffPASS. Actual accepted four final-writer replay `.build/s-current-refresh-20261007/anchored-integration-four-v2/` allEVALUATED, complete parent+delta validated, independentcheckPASS exactcandidate vs previous standalone; 0812/0818/0830/0848 0/12/0/59 QPEvisible quarantine, CRgain0, RAWunchanged/protectedloss0 NOTweatheraccuracy. Work20.747/20.836/13.530/21.143M; reportSHA4056799d546e69a1203f4dfe75bc67c18514e7171ea3363f329e137c2100df9d, PNG viewed. First v1 replay source drift during budget edit rejected, preservednotaccepted; v2sources stable. Old3routes actual43.28/44.85/30.37/46.37M leaves little room for combined, no budgetraise. No commit/push/deploy/default changes. Fresh10507:00CST exactcontroller2315154LIVE RUNNING52/684scans13/106slots currentQC855f1cdc; exactauditor3393155LIVE. Fullrefresh/deletion/weatheracceptance/promotion unfinished. Next finite source-state/weather validation for residuals or accepted refresh publication+unreferenced-derived cleanup; no globalthresholdloosening.

### S anchored original fan residual experiment, 2026-10-07

Offline `s_anchored_fan_residual.py` with reusable `research_s_anchored_fan_residual.py`, contract/test-first; 16 new / 49 related PASS, Ruff/diff PASS. Exact accepted four snapshots replay final `.build/s-current-refresh-20261007/anchored-fan-four-v4/`, independent 71-gate source/bounds/held-reference replay PASS. New candidate gates 0812/0818/0830/0848 = 0/12/0/59, all QPE targets, CR gain 0; hard protection loss 0 and RAW unchanged, NOT independently validated weather accuracy. 0818 gain is original south family31 at az163.19deg424.4–439.6km, within frozen accepted strong-source bounds, not prior east-boundary features. Uses accepted V3 strong anchors, exact original family membership, held-out corrected received-power windows, measured quiet SNR only when DBZH unavailable, plus adjacent already-confirmed interference as context only; never recursively expands membership or lets low SNR override valid foreground. First bilateral DBZH-quiet route yielded zero, actual shoulder evidence explained hold. Gain remains small; principal remaining hold is nonstationary received power/insufficient independent source, do not loosen thresholds repeatedly. Optional standalone 50M budget, not joint-integrated/default-enabled/committed/deployed. Figure `comparison.png` shows accepted parent / offline quarantine preview / candidates, not Web gain. Docs S_GENERIC_QC_NEXT_STEPS updated. Last fresh10506:48CST exact PID2315154 LIVE RUNNING49/684scans12/106slots; exact auditor3393155 LIVE RUNNING. Accepted background image/config remain frozen. Full refresh and candidate promotion unfinished.

### S stored source-family coverage research, 2026-10-07

Read-only audit_s_source_families.py, contract/tests first; 8 new + related total33PASS/Ruff/diffPASS. Actual four accepted snapshots final source-family-coverage-four-v2 complete with exact input/QC URI/params/predecessor/script/outputSHA, independent member/seed/hold replay PASS; diagnostic figure viewed, NOT cleaning/Web gain. Eligible-domain nofamily/seedless/held-seeds/unheld-seed:0812 2526/24162/2091/0;0818 793/1331/5263/0;0830 111/94/10/58;0848 851/1031/5985/0. Domains include weather/unknown, not contamination truth. 0818 ledger51045seeds:48602 hold2 NO_NARROW_BOUNDARY+2242hold3; sourcekinds all morphology combinations, no receiver/coherentline bits. South originalfamily31 157.26–167.14deg80.125–439.875km10186members4842seeds(allhold2). This identifies narrow-ledger path failure, NOT all QC routes/no source anywhere. Freeze boundary-template route; next finite wide-family source qualification/fullRAW membership+available independent context before bounded weak-tail actions; never remove hold2 globally or merge samebearing disconnectedfamilies. Docs S_GENERIC_QC_NEXT_STEPS appended findings/commands/MIT+wradlib+PyART references. No production algorithm/config/deploy/commit/push changes. Fresh10506:23CST refresh2315154LIVE43/684scans11/106slotsRUNNINGcurrentQC958db841; auditor3393155LIVE RUNNING. Fullrefresh/promotion unfinished; goalACTIVE.

### S held-out complete boundary competition, 2026-10-07

PreviousgoalturnPROGRESS publication auditor. Thisturn implemented offline s_boundary_competition.py (contract/test-first9new RED/GREEN), fixed angular edges vs range/inverse-range alternatives + center-only, reciprocal train-only originalwindow folds, half-native samplinguncertainty/notglobaltol, allwindow bilateralcontext/branching abstention. NOsource/weathertruth/action/defaultX/Schanges. Final180relatedPASS0skip/Ruff/diffPASS, existingnumpywarning. Reusable research_s_boundary_competition.py/plot_s_boundary_competition.py actual4newWebparents final boundary-competition-four-v2 complete; work22.93–47.73M incloriginalhistory+fit+membership <=50M; independent-model-check1560folderrors/targetcontainment + executedsource/report/imageSHA PASS, figure viewed. 0812/0818/0830/0848 newfixedfeature0/20/0/4, center-only1/38/0/0; NOTefficacy. 0818actual20 in102–110deg340–460km original5/15dBZ parentFAN_RANGE_RATIO failure, NOTsouthmainfan. Short-target8522/4003/135/4420 +nohistory20257/3361/130/3384 includeweatherunknown andoverlap, notcluttertruth. No promote/deploy/commit/push/productionwrites. Boundaryroutefreeze; next finite fullRAW discontinuous-member family/occupancy+independentcontext model beforeweakmembership, no targetreseeding/infiniteazimuth union/template churn. Fresh10506:15CST actualrefresh2315154LIVE42/684,10/106currentdiagnostics01:06UTC/noerrors; auditor3393155LIVE42/10. GoalACTIVE fullbaseline/substantiveQCweatheracceptance remains.

### S read-only background publication verification, 2026-10-07

Added contract/test-first audit_s_refresh_publication.py: frozen plan/source, successful QC profile/request/output and latest DB lineage, exact grid→mosaic→QPE→diagnostics station membership, actual Web T0 all S raw/QC sweeps+composite/QPE job identity, bounded PNG CRC/decompression/SHA. No radar arrays/job submission/service changes. New tests RED missing helper then final20relatedPASS/RuffPASS, includes every stale stage/partialDONE. Actual105 v2 first38scans/10slots/704uniquePNG verified, NOTweather efficacy/full completion. Detached independent PID3393155 exactcmd verified; remote .build/s-all-current-20261007/publication-audit-v2.json/log/pid, original refresh2315154 untouched stillRUNNING38/684,10/106 at06:01CST; local receipt .build/s-current-refresh-20261007/publication-audit-observed-v2.json. Scope image publication only, no new algorithm action/candidate deployment/commit/push. OfficialMIT/wradlib rechecked; docs bounded next competition/weather-mixed/finite RAW ownership/isolated independent evidence; DBZH/SNR fromsame return notindependentweather votes. GoalACTIVE, fullbaseline and substantive weather/promotedQC acceptance remain.

### S complete measured boundary/member histories, 2026-10-07

Previous goal turn PROGRESS: paired weather/source audit changed next action. This turn implements explicit default-off collect_histories in shared polar_morphology with readonly exact per-window RAW members/offsets/left-right boundaries/native spacing/bilateral measured fractions and exact overlapping original failure_codes; qualified+rejected short histories retained, bounded work/object/export arrays, exclusive carrier/history export. Default classifier masks/IDs/records unchanged by tests, no thresholds/action changes. Contract/test-first 7new RED/GREEN (initial fixture mistakenly omitted required PrefixPolicy geometry corrected to MorphologyPolicy), final164relatedPASS0skip/Ruff/diffPASS, existingnumpywarning. Reusable audit_s_boundary_histories.py final original-boundary-four-v3 binds4actualnewWebparents and exact prior strictmembership; 0812/0818/0830/0848 histories3998/5399/500/5538, target-without-any-measured-history20257/3361/130/3384 (includesweather/unknown, NOTclutter), originalstrict93/32/5/25, work18.33–34.31M. Independent-member-check allactualRAWavailable>=5/nativegood/range/angularwindow/unique members PASS (2195158 membership observations, overlapping scales/levels). Plot script retained; boundary-paths.png inspected, SHA receipt; shows boundary-only average-qualified histories can still have local flank conflict; do not just increase tolerance. First realCLI path/dict error and lazy-import false source-drift guard corrected; finalv3complete and allcurrent executed helper/source hashes verified. No candidate deployment/commit/push/production writes/X-defaultchanges. Fresh105 exactPID2315154LIVE05:34CST32/684scans8/106slots/errorNone; same acceptedv8background unchanged. GoalACTIVE; next finite angular/boundary source-weather mixed heldout classifier plus weather/control acceptance and narrow promotion, not more envelope patches.

### S paired whole-object explanation audit, 2026-10-07

New offline reusable audit_s_object_explanations.py binds exact four new accepted Web parents/RAW/native order/producer parameters/profile/executed helper+source SHA. Paired strict protections vs weather included only in geometry (gaps/conflicts/barred preserved); outputs diagnostic, never actions or weather truth. Final object-explanations-four-v2 report/NPZ/comparison.png/figure-receipt verified and viewed. 0812/0818/0830/0848 strict_source/weather_conditioned_only/no_source:93/1/28685,32/56/7299,5/32/236,25/115/7727. Targets include weather+unknown; no_source NOT contamination. Weather barriers explain small fraction, not main residue cause; object-level overlapping failure histories cannot assign individual target gates from initial rectangle. Work24.13–37.48M pooled<=50M/64MiB; unavailable/resource failure withdraw paired results. Contract/test-first:32 related PASS/0skip, RuffPASS, RED preserved. Research doc records next finite full measured RAW boundary/member history with heldout angular explanation; no more compact patches/tolerance tuning or global weather-veto removal. No candidate deployment/commit/push/X changes. Existing105 refresh PID2315154 independently LIVE elapsed2:17; full completion not claimed. GoalACTIVE, full baseline refresh and substantive weather/promotion acceptance remain.

### S complete source membership experiment, 2026-10-07

Explicit source-prefix-v2/complete_original_carriers test compares EXACT original source members before compact counterexamples, no rectangles/holes/target reseed; legacy/new carriers jointly byte-bounded and 50M work. Contract/test-first; initial fixture invalidly disabled required v9 branches, corrected without weakening validator. Known fragmented prefix864gates legacy0 vs v2>500; proper legacy RED assertion run, 5new testsPASS, related62PASS and final22PASS (overlap), Ruff/diffPASS. Actual four new Web parents candidates EXACTLY unchanged vs v1 (added0/withdrawn0), no newCR, RAWsame/protectedloss0. Whole eligible/complete_source/no_source:0812 28779/93/28686;0818 7387/32/7355;0830 273/5/268;0848 7867/25/7842. Includes weather/uncertain targets, NOT contamination truth/delete list. Work26.54–40.96M standalone, not integrated joint3route allowance. research_s_source_prefix.py --complete-original-membership retains authoritative parent domains and source/input/URI/params receipts. Evidence source-prefix-membership-four-v2/v1-to-v2-proof.json/report.json/comparison.png. No promote/deploy; pivot documented to complete RAW source/weather/mixed competing explanations, not more compact thresholds/templates. 105refresh05:07CST actualPIDlive26/684scans7/106slots currentqc5a8d4031-5780-5196-89b1-47e79b648c3b/noerrors. Goalactive fullrefresh + substantive newcandidate weather/promotion unfinished; currentturn PROGRESS concrete experiment/tests/delta evidence.

### S bounded junction-prefix research, 2026-10-07

Source-trace: future split/join marks entire earlier measured track ambiguous in polar_morphology.detect; explicit freeze_prefixes_at_junctions option closes old history BEFORE new junction and never carries into children; default X unchanged. New contract/qc_engine.s_source_prefix offline S-only, target/protection required, original geometry + matched heldout SNR, one50M/64MiB/4MiB budget with atomic withdrawal. Existing other polar_windows ambiguity kept outside this entry. Same accepted four new Web parents, recomputed authoritative protection/target and exact URI/inputSHA/params binding: 0812/0818/0830/0848 nominations16/32/3/23, visible QPE candidates4/1/0/2, visibleCR0; protectedloss0 RAWsame, not weather truth. 0818 29 unknown+2powerexcess (can overlap), majorfan notsolved; no promote/deploy. Boundaryvariation~1.99footprints remains separate failure, no thresholds changed. Keep research_s_source_prefix.py + source-prefix-four-v2 evidence/figure; docs nextfinite complete RAW weakcarrier boundary+power heldout, not repeated threshold tuning. NewtestsRED/GREEN; final247PASS/0skip, RuffPASS, diffcheckPASS, prefix-final.xml. 105 baseline refresh alive04:56CST24/684scans6/106slots, currentqc5172b858-0406-5498-89fd-16db363507d1, noerrors. No gitcommit/push/remote write. Goalactive fullrefresh+newweather/promotion acceptance pending; standaloneprefix budget not yet merged with prior3routes.

### S actual new-parent replay, 2026-10-07

Four exact Web new-v8 parents frozen/bound to completed refresh QC URIs; same accepted parameter SHA bf582b633a4b19f41a6167de93ca2a2fdc255613cf1430ecfe507d5eb472ef51, same RAW vs old snapshots. Canonical freeze_s_morphology_inputs.py checks URI generation/actualS/acquisition and preserves original parent matrices+native permutation; first real parent validation exposed erroneous sorted index-valued provenance, fixed without weakening validator/remote change. replay_s_morphology.py runs actual final integration on stored accepted parents, recursive exact-before+delta passes. 0812/0818/0830/0848 newbaseline visibleQPE72389/9422/111/9985 vsold73792/9422/1722/16007; no restoration. IncrementalQPE91/2/12/38, CR19/1/27/10; protectedloss0, RAW/QC/VALID unchanged, not weather accuracy/recall. Candidate not enabled/deployed, independent weather acceptance pending. 0818 main NO_PARENT/discontinuousfan remains; next finite original multi-carrier unique ownership/heldout boundary diagnostics, no threshold relaxation or screenshot-specific deletion. Evidence .build/s-current-refresh-20261007/current-replay-four-v4/comparison.png/report.json/old-to-new-baseline.json; snapshots current-inputs-v2 pluscurrent-inputs-0848-v3. 56 relatedtestsPASS/0skip; RufftoolsPASS, three existinglibrarywarnings. 105 actualPID2315154 live0424CST:16/684scans,4/106slots0812/0818/0830/0848 published; QC31c04b2d-a551-57be-97af-a33b73c1927d RUNNING. Background frozen baseline unchanged; no commit/push/X/service modification. GoalACTIVE, fullrefresh+newcandidateweather/promotion unfinished.

### S morphology writer integration, 2026-10-07

Post-parent S-only action/QI/CR writer implemented (defaultNone, existing profileSHA unchanged against original HEAD class), contract/test-first. New s_morphology_disposition.py and s_morphology_integration.py; optional profile→runner finalstage→Zarr metadata→recursive validation integration. Explicit uncalibrated experiment quarantine, not confirmed clutter: preserve RAW/DBZH_QC/VALID, route cause bits, capQI/LOW_QUALITY, withhold trust/QPE/CR, invalidate original trusted derivedsegments, exact-before parent chain validation incl source moments/coordinates. Preserve all inherited RAW/local/external/prior/mixed weather/conflict/barred protection; no restoration of old exclusions. Allocation/volume bounds separate from50M detectorbudget. Added offline configs/qc/s-morphology-transfer-20261007-candidate-v1.yaml valid, dispositionSHA34963e6280fc5ed2eedd712afa6a9ebd7d9285b0b6bdb9dd23139cc8a858c7e9; not deployed/enabled in105. 250 related testsPASS, including full accepted-parent runner/Zarr roundtrip and tamper, newpure action/evaluation/weather/RAW/missing tests; Ruff newfiles/runner/validation/qc_zarrPASS (profile.py has preexisting E402/longline). Evidence .build/s-current-refresh-20261007/writer-final.xml/log. First fullZarr failure exposed 1Dcoords; fixed schema-specific coordinate provenance encoding without loosening normal2Dfield rule. Writerreal-array acceptance/independentweather still pending, no actualnewQCgain claim. 105 0403CST actualPID2315154+job0789e294-e193-5e25-b4b2-45b2d15be337 RUNNING:12scans complete,0812/0818slots published,0830diagnostics current; acceptedv8 all684/106background unchanged. Next newcommon-v8fourcase freeze/replay/figures, then finitecandidate promotion if gates pass;0818NO_PARENT still separate unresolved sourceownership. GoalACTIVE, no commit/push/service or X changes.

### S morphology transfer candidate core, 2026-10-07

Contract/test-first S-native readonly evaluator implemented in qc_engine/s_morphology_transfer.py; explicit S band/native sampling gaps/original row restoration/RAW identity and weather/conflict/barred masks, DBZH + fragmented carrier held-out + SNR complementary routes. One shared50M work/64MB output/4MB evidence budget; resource failure withdraws all candidates atomically, missingSNR skips dependent routes. Reuses same-call original DBZH geometry in fragmented_carriers internal helper (public X masks unchanged), removes already-charged redundant SNR target projections; no threshold/cap relaxation. 178 related tests PASS/Ruff/diff PASS; one existing numpy warning. Reusable research_s_x_transfer.py --joint-budget, final frozen-old-Web fourcase replay .build/s-current-refresh-20261007/transfer-shared-v3/comparison.png:0812/0818/0830/0848 work43,964,096/46,831,857/31,787,321/48,952,422, visible candidates1374/10/1574/3261; bitwise same as separate-route reference, protection/RAW invariant holds. These are candidates, not truth/recall/current-new-v8 Web efficacy. 0818NO_PARENT still unresolved. No candidate writer/production action/profile/commit/push/deployment; acceptedv8 all-current background refresh independent. Next common-v8 snapshots, S action/QI/version integration with weather counterexamples, then complete-source bounded weak ownership and strict unanchored fragments. GoalACTIVE, do not wait full background run.

### S all-current baseline refresh, 2026-10-07

New active user goal authorizes all old S QC/images refreshed in background then continued S improvements from research doc; supersedes prior closure stop within this scope. 105 frozen684 decoded S scans/local20260828 +106Web slots, current accepted two S workers image s-original-object-c33a4d4-v8/profileSHA c506e8ee810f5d38ebf2a252b93504fd03fcf4ea6c1bfd789651f95351ce4701. New scripts/refresh_s_all_current.py sequentialQC/grid +mosaic/QPE/diagnostics PNG, fixedconfig/binary/workerimages, live-job reuse, inputURI/drift gates;8tests/RuffPASS. Remote scripts root-owned, publish script only within writable .build/s-all-current-20261007; no permission/service/worker changes. nohupPID2315154; 20261007 0403CST check PIDlive, completed12scans/2slots0812+0818; current0830diagnostics job0789e294-e193-5e25-b4b2-45b2d15be337 RUNNING DBconfirmed. plan/state/run.log samefolder. Observe exactprocess+jobnext, no restartfromtimeout/stale-lock. OriginalRAW preserved, oldproducts replaced afternewsuccess; no bucket cleanup or full-completion claim. GoalACTIVE; next commonv8fourcaseinputs/figures then boundedDBZH/SNR S incrementalpath/sharedbudget/sourceownership, especially0818NO_PARENT. No codecommit/push/experimentalpromotion.

### S/X morphology bounded research, 2026-10-07

User explicitly resumes only finite S residual/X-method research; other S closure/V27 default-off/V28 deferred remain. Read-only four actual Web inputs frozen: Z9591 08:12 cut0, Z9598 08:18 cut0,08:30 cut5(3.36deg),08:48 cut0. Web QC equals latest stored allfour; only08:18 parameters match active S v8, otherthree historical configs require common-baseline recalculation before incremental efficacy claims. X v9 DBZH/fragment/SNR core fixed-policy offline: selected residual-sector candidates1338/6/1502/2122 of1787/1169/1601/4558 (weather-inclusive diagnostic sectors, not truth/recall); wholevisible1374/10/1574/3261. 08:18 footprint772 no-source, ledger214 NO_PARENT; SNR carrier membership13 visible(6candidate/2excess/5unknown), representative completehistory support.245 or bilateralclear.692 fail qualification. No protection/RAW violations, no budget abstention; 153 existing X core tests PASS, Ruff PASS. Reusable scripts/research_s_x_transfer.py; evidence .build/s-x-transfer-20261007/four-case-v1; docs/S_GENERIC_QC_NEXT_STEPS_20261003.md scoped nextsteps unifybaseline→DBZH/SNR complement→bounded originalfamily ownership→RADVOL-style discontinuous nomination with physical/availability/real-weather validation. No algorithm production changes, commit/push/deployment/recompute; no unbounded restart of QC work.

### NowcastNet common-support CPU comparison, 2026-10-06

Frozen supplementary CPU protocol includes all24 blocks/12windows, 8models/192scores on per-lead common finite-member pixels; FSS only full valid neighborhoods. Six late boundary cases remain physically incomplete; no missing-to-dry conversion. Self CRPS .231698/.341500 vs official native .208566/.298775 and STEPS .205122/.312332; paired skill vs STEPS -12.96%/-9.34%, short95%CI negative, late crosses0. Wet late self rain ratio1.319/spread-RMSE .187/envelope coverage27.8%; clipped evolution parent rainratio2.470/CRPS1.339 reduced by generator CRPS.643. One-step parent sampling outside full input ~92.1% wet late is not cumulative trajectory or proven cause. New CPU official noise distinct from prior CUDA, all24 rerun, no mixed evidence. COMMON_DOMAIN_500K_20261006.md and common-domain-comparison/audit-500k.json; 26 related tests and independent reaggregation/code/support/reliability audit PASS. First metadata preflight failed before inference, fixed installed-distribution version; r2 omitted explicit baseline clip, preserved, final r3 rerun and summary identical (this batch values already within range). Qwen both active/healthy, no GPU/newtraining/holdout/service/production changes; disk~633G. No commit/push or sharedWIP changes. Next bounded evolution training/inference flow-unit/direction/border/intensity/normalization/clip audit and physical input-context confound; propose evidenced candidate before newlineage. PSD/fullscene/peakresource gates pending, no promotion; heartbeat remains paused.

### NowcastNet CPU baseline comparison, 2026-10-06

STEPS/LK/independent FFT phase translation frozen10min/0.02 target comparison completed CPU-only; no Qwen stop/GPU/training/holdout access. 24cases executed;18full-support cases/11windows eligible,6late boundary losses excluded from allmodels/bothperiods; 11median+7wet, coverage-selection limits explicit. Sevenlow-signalmedian STEPS runs labeledpersistence fallback;17genuineSTEPS noexceptions after APIfix. All-caseCRPS self .228960/.348549 vsSTEPS .201351/.326744 andofficialnative .203252/.304833 on identical18subset; self CSI/FSSbetterthanSTEPS is not probabilityqualification. Weatherwindow paired CRPSskillvsSTEPS -13.99%/-6.81%,95%CIcross0. No promotion/newlineage/extra training. Report STEPS_COMPARISON_500K_20261006.md, evidence steps-comparison-500k.json andsteps-gate-diagnostic-500k.json. Next freezecommon-valid-domain supplementaryprotocol for6missingcoverage cases, thenwetlate/flow/structure/spread/reliability error decomposition; GPUrerun onlybounded asneeded with sharedserviceconstraints, noautomaticstops. First17STEPS TypeError outval duplicated with internal positional min; roottrace+actualRED/GREEN guard, removedonlyduplicateforwarding/defaultforecastNaNretained, originalfailedartifactpreserved.5new+6official+6priorregressionsPASS; originalofficialaggregationexact; no commit/push/sharedWIPchanges.

### NowcastNet official comparison, 2026-10-05

Frozen12window/24case development compared500k/persistence/official native0.02 and equal-context0.01 adaptation, commonphysical center128 at0.02, fourfixed members, independentholdout closed. All-caseCRPS self .231404/.332649 vs officialnative .206630/.296976 and persistence .302052/.411278; ~12% worse than officialnative both periods. Native inputcontext larger, auxiliary equal-context also lower meanCRPS but pairedCI crosses0; not pureweights causalproof. Positive BSS vs persistence is not StageD qualification; no promotion/additionaltraining. Report OFFICIAL_COMPARISON_500K_20261005.md; next same-domainSTEPS/LK/translation then bounded error decomposition, newlineage/budget needs decision. Offline oldURI precheck rejected without inference; process-only artifactURI relocation retains all hashes and frozenprofile. Six newCPU tests and six prior tests pass. Qwen3.8 temporarily stopped then bothQwenhealthok, other services unchanged. Metrics only copied locally, sampleNAS/modelserver. SharedWIP untouched, no commit/push.

### NowcastNet final development evaluation, 2026-10-05

Formal generative training reached500000 onOct3; checkpointSHA b888fac7690c4cbbc9b0d24eb921802790673a4c2016e1a9507926015989a0f7. Oct5 user authorized bounded GPU evaluation and temporary Qwen3.8 stop; Qwen3.6 unchanged, both healthy afterwards. Frozen12window/24case expanded development, same256center/fourmember noise, samplehash unchanged; 350k/370k reused, baseline scores exact. 500k wetter MAE .517/.710 (short/late), CSI .141/.054, FSS25km .434/.198, rainratio1.024/1.232. LateCSI only4/12weatherwindows better than persistence; no robust all-scene superiority, no promotion/newtraining/holdout opening. Report docs/nowcastnet-training/FINAL_DEVELOPMENT_500K_20261005.md; evidence under development-components-20260927. Next bounded decision is same-domain STEPS/official probability baseline and StageD gates, not more steps. Three-hour heartbeat remains paused; shared main WIP untouched.

### S QC phase closure, 2026-10-04
User requests phase closure; stop open-ended QC extensions. This decision overrides historical next-step entries below. Freeze default-off V27/proof4; actual eight-case replay has no added residual-proxy coverage and loses prior coverage, so no promotion. V28/proof5 is deferred/unimplemented; retain its five draft tests explicitly skipped, repair V27 helper calls, do not count skipped cases as passed. Preserve reusable audit/figures, immutable RAW/unknown/weather/budget rules and shared S/X/Web/Go WIP. Known sparse/mixed/near-clutter/isolated/independent-weather limits remain documented rather than automatic unfinished development. Resume only on explicit new bounded user scope. This closure does not claim current 105/Web acceptance or deploy new experiments.
Closure checks: radial_revision + unified auditor 855 passed/5 deferred draft skips, exit0; Ruff/directed diff checks PASS. Rechecked eight report/NPZ/PNG pairs and 22 executed source SHA, drift0. Historical offline scope only; no new weather validation, commit/push/deployment. HEAD8e6d355 and shared WIP preserved.

### S V27 multi-carrier and budget repair, 2026-10-04
PreviousgoalturnPROGRESS(V26contracts/tests/nativeidentity/hard3zero-gain+eightcapdiagnosis). Currentdefaultoff completefamily decomposition supportsmultipleoriginalcarriers, sharedmixed nodes/otherretained, observedbroadprefix/tailrequiresknown+echo+weathercompat>=.8, recurringnarrowpredecessor/successor cannotvanishasbackground; per-sourcephysicalsupport/span/heldout+nativewriter preserved. Originalfullnodes+separatewindowmodelkey avoidsparallelidentitycollision. Strictproofversion4 supersedesoffline3, versionstringV27. Contract/RED/GREEN complete; nativeworker/Zarr6cases/rotation/resolutions/negativefork/weather/missing/protectionPASS. Full854radial+auditorPASS/3existingwarnings in122.7sec; addedonlylater exactparentbounds-equivalence1PASS (24sparseparents x20/60km/unknownshoulders), don'tclaimincludedinfull854. Ruff/diffPASS. Real1018unchangedcapinstrumentation rootsparentassociation27380289 (>halfwork); conservativeoriginalnativewindowrow/rangebboxprecheck avoidsimpossibleparentmaterialization, exactmembers stillmandatoryforpossibleoverlap, unknownshoulderswhole-native extent/nocapsraised. EightfrozenV27v1 COMPLETE22executedmodule SHAcurrent/drift0, independentinput/NPZ/PNG/protection/action/caps/deltaPASS; oldPATH bitwiseequal, maxjoint38842523<50M. **NOTeffectiveness:** vsV25added4/newproxy0, withdrawals201905/proxy852; hard0948/1024/1124still0gain. Retainedwholecoveragewithdrawal mayincludeweather, notweathertruth/reducedfalsepositiveclaim; no promotion. Privateaudit-v27-carrier-eight-v1 delta-proof/profilerfail-original retained. Nextreuseexistingvalidcontinuous/response-sourceproofs withinsharedprovider withoutcoverage loss, fullvariableboundaries+qualified-source boundedweakfragmentownership, thenweather/event/siteholdouts/nearclutterisolated/normal105Webacceptance. No commit/push/SSH/105/product/servicechanges; sharedS/X/Web/GoWIPandXjobs untouched. FullgoalACTIVE/NOTcomplete.

### S V26 original carrier research, 2026-10-04
Default-off CarrierFamilies replaces experimentalprofile provider: completeoriginalrelative families/nestednodes/interior broadcrossing, boundedRAWmembers/native samplingquantization/source-weather equalcentreerror/observation-conditionedcontext. Nativeworker+validatedZarr proofversion3 andtamper controlsPASS; explicit strictflag. Sparseconditioning retainsfullunknown, observedcorridor>=.5, occupiedknown>=.95medium; censoredcontinuousweather retained. ExtraRAWwork chargedonlynewprovider; oldprovider unchanged. Final842radial+auditor testsPASS/3existingwarnings, carrier20PASS/Ruff/diffPASS. Frozenhard3v5 complete22executedmodule SHAcurrent/drift0; independentdelta/input/NPZ/PNG/protection/action/budgetproofPASS:0948/1024/1124 added0/withdrawn0/newproxy0 vsV25, work24282257/25850997/27556950. Firstverifier oldfieldshorthand KeyError retained, actualRV2_BARRED_MASK/GEOMETRY_GOOD correctedwithoutalgorithmchange. Extendeightv5 terminalFAIL atthird1018 50Mjointcap after2complete; failure.json/producer preserved, not8acceptance/nocapraise. Actual1124offlinePNG viewed:proxyincludesweather,no newWeb. Sourcecontext originaltworowbands farreturnsoftenonlyonerowknown,corridor .09-.46;1024complexweatherprefix cannotbeexplainedbysinglecarrier. Nextmulti-source/weather fullfamilydecomposition +qualifiedsource boundedweakfragmentownership +sharedfeatures/performance +labelledevent/date/site/weatherholdouts; nearclutter/isolatedroutes stillpending. Docs S_GENERIC_QC_NEXT_STEPS updated/officialRADVOL+wradlibrechecked. No commit/push/SSH/105/products/servicechanges; sharedWIP/Xjobs preserved. GoalACTIVE/fullscopeNOTcomplete.

## 2026-10-02 S/X 质控与融合范围及推进入口

- 2026-10-07 用户新报08:12 S组合不完整：实查51来源记录仅Z9591+X两站局部v2，09:00历史全网4S输入，方法/网格不同。针对查看故障修复自动按最大已声明请求站数优先、显式系列保留、局部范围/实际S/X站数及无参考图缺帧提示、下拉宽度；仅105Web包原子更新，Go1149670/数值算法/Worker与其它刷新作业未变，旧资源保留。180Web PASS/build PASS，08:12自动4S+21X、局部两站、09:00四S历史全网、固定系列缺帧与控件1200px范围核验；三图不可变摘要不变。docs/S_COMPOSITE_COMPLETENESS_20261007.md、私有.build/composite-completeness-20261007。全网v2未补算，历史孤立系列未合并，S/X推进保持用户暂停；新报故障处理不授权恢复M1–M4，无commit/push。

- 2026-10-07 用户最新要求当前批完成后暂时停止检查，S/X 完整推进目标在本批完成后 PAUSED，不当 complete，恢复需用户明确继续。当前一小时输入审核已通过：正常Go计划5d439d4e...未提交（最终run查询404/计划digest不变），00:00双站无因果输入、00:06仅X、其余8双站，完整10slots；实际网络maxage720s，首600s目录不足保留/CLI拒绝，补同cutoff720s/23catalog/19in-hour。同镜像937.../6f1...独立1GiB/1CPU容器c8290ba...完整流读S9+X10原始文件，SHA/长度/前后etag全部匹配，Exit0/noOOM/no rawbytes saved。新审核契约/工具17专项+24既有=41PASS/Ruff/实际短窗口与不覆盖CLI PASS；原五帧inputs/sources/policy/identity exact，独立回执绑定核对PASS（非第二次全RAW回读）。证据.build/sx-m1-20261007/hour-cases-v1，docs第10节。既定7workershealthy/Go PID1149670/HTTP200，无业务计算派发、发布、在线参数/算法更改、commit/push、自动恢复。三态缺口实际登记：未定/缺测已有点查，五帧清单valid_no_echo均0，真实有效无回波与硬拒绝门端到端案例仍不足，新S生产修订/历史55未知仍待补；M1未验收、M2–M4未启动。暂停保留现网与其它任务在跑服务，不全局停Worker；共享WIP保留。
- 2026-10-07 M1 完整重复性续进：新契约/比较工具/22专项，相关51PASS、Ruff及CLI相同/差异/不覆盖/0600核验PASS。原五帧冻结请求、尝试、镜像6f1caf.../指纹937609...相符；独立12GiB/2CPU容器ccaad62f...串行实际重算，每帧全部29数组及2321不可变对象逐字节一致、资产SHA原样，Exit0/noOOM，实测live cgroup峰5,118,865,408B，process maxRSS5,061,240KiB。原环境/只读配置/算法不变，显式禁止发布/对象写入；采集隐藏文件路径断言修正后续读同句柄，未重启。随后同镜像1GiB/1CPU独立原资产+回执回读eeaf870...全5PASS（不是第二次重算）。本机context/driver/toolSHA与执行意图/回执一致；证据.build/sx-m1-20261007/array-repeat-v1，文档第9节。Go PID1149670及HTTP200、候选fresh ready idle，按实际名称S2/MB4/candidate共7healthy；另管理QC unhealthy不当全服务器健康。M1仍未验收，下一小时/三态/硬拒绝案例台账，再独立资源窗口补新S实际生产修订；历史55扫层未知不补写，S数值研究收尾保留。X合格仍0，非天气/衰减恢复/融合收益；M2–M4未启动，无业务发布/定时恢复/commit/push，共享WIP保留，完整goal ACTIVE。
- 2026-10-07 M1 页面续进：实际 Playwright 浏览器已连105，发现选中时间标签截获相邻按钮；workspace.css仅加 pointer-events:none，109项Web输入仅此差异，174测试/构建PASS。远端源码CSS不同，首预检写入前停止；后只发布15项Web包、保留19旧资源及远端源码，Go PID1149670不重启，入口SHAafa0ae42…。旧HTML缓存仍加载原样式，核对实际CSS地址与HTTP摘要后取得新入口；正常鼠标五帧切换/刷新/旧horizontal与新v2系列切换PASS，当前方法按实际完整名字核对，辅助简称断言失败保留。五帧点查32/21/27/29/37dBZ，S/SX胜出资产/scan/sweep/ray/gate/QC身份相同，X同点missing/null；投影外全null。00:30初预期valid失败，实际有限32与柱不确定共存low_quality，按真实掩码验证，非不可采样/没改算法。实际105页面另点27.5双图同源。新登记candidate心跳新鲜idle后只读选读5原生RAW/QC，两者与点查完全一致，属性/冻结标记/资产SHA核对；worker_id字段误用在读取前失败，按id核对后执行。live run仍SUCCEEDED5、原6及候选共7健康。证据 control-release-v1/browser-five-frames-v3、series-switch-v3、five-frame-probe-summary-v2、native-five-points-v1、m1-ui-delivery-v1，截图output/playwright。M1仍未验收：下一步完整五帧数组重复性、新S生产修订、一小时与反例审核；106统一分析位置不当106产品。S数值研究收尾保留，M2–M4未启动/X合格仍0。未提交/推送/新产品任务，见推进记录第8节。
- 2026-10-07 操作授权后 M1：正常终端认证和 systemd 发布 Go/Web 成功，新 PID 1149670、binary SHA457e9448…、15 项 Web 文件/首页验证、暂停解除；本机 Go 222/Web 109 源码输入无漂移。管理 MB 通道改候选 fp937609f3…/rev33，五帧 run `c12350bd-ad20-4c30-b44f-fc9a79ca1675` 全部 SUCCEEDED。00:54 首先成功，00:48 6 GiB 容器 OOM/137，内核 CONSTRAINT_MEMCG 精确句柄证实；recover409→abandon200，available>=24GiB 后同容器提高12GiB/2CPU、原镜像/配置不变，登记 `ops-multiband-aff1db03f016-32fac3d72e2a`；等其它四帧成功后仅 retry_failed 00:48，成功。重启后累计 peak4.75GiB，健康空闲；原 S2/MB4 健康。候选255来源/5清单/1组零矛盾，无缺QC版本；55 S 扫层仍缺生产修订。真实series `3e77ddcbe87821a624c264e168ef69b7379139004b2f7bdbacbe6ce7bf8b6ca4` 五帧不混旧系列。S/SX 合格回波16505/19266/16862/15404/19489，X全0且不确定1347–2519，非无雨/非双波段收益。00:54三图摘要通过，35dBZ实际点查 S/SX 同资产/scan/ray299/gate848，Xmissing/null；投影外null无填零，初断言预期掩码0失败后按实际null核验，没改服务。浏览器工具3超时，未验页面；没有整场融合数组重复性或天气验收。认证阻塞已解除，M1仍待页面/逐帧点查/完整数值/新S生产修订，M2–M4未开始；证据 .build/sx-m1-20261007/control-release-v1，文档第7节。未保存凭据，未改S/X数值、QPE或预报，未提交/推送。
- 2026-10-07 认证等待后续：发布成功回执仍不存在、原服务正常。离线来源审核 v2 增加与所选冻结资产地址/观测结束/到报时间的精确绑定（纳秒、等价时区）、S 窗口与来源配方及清单身份核验、配方分组；回执绑定输入/清单/审核源码摘要、0600、禁止覆盖。原工具新增反例 11 失败，相关组合最终 29 通过、Ruff 通过。旧 95 清单 2,937 来源零矛盾/2,453 缺 QC 版本，五时次四清单 204 来源零矛盾/160 缺版本；旧快照状态不当当前状态，未读数组。见 M1 记录第 6 节及 `.build/sx-m1-20261007/control-release-v1/*lineage-audit*.json`。未改算法、线上配置或提交任务；M1 仍需认证发布和候选五帧验收。
- 2026-10-07 后续 M1：Go/Linux 候选 `sx-m1-policy-20261007-v1` 二进制 SHA `457e94486a9fd5b7051ff866af7d78accb76e2357d88de976bd0041ab1693ff6`、15 项 Web 包已准备；Web 174、Go 相关七包（apiapp 无测试）、契约 50 及生成一致性通过。修复目录加载期间清除 X 深链接 ID，受控延迟旧版失败/新版通过，候选 Web 接 105 原 API 实际刷新与双图核验通过。首次 systemd 发布被 sudo 密码认证拒绝，原 binary/index 已恢复，原 PID 957149 未重启、4173/8090 健康、暂停解除；脚本新增认证前置，需操作员终端认证。五时次旧输入台账零来源矛盾；00:48 原容器缺失、租约过期，recover 409 后正常 abandon 200，旧任务 FAILED/WORKER_LOST，不是假缺资料。单个融合候选 Worker `rainpulse-ops-sx-m1-candidate-20261007` 健康，指纹 `937609f34a2a7a942dcda19be405abc5296ce95b9a4f6b565acfd76a22215a68`、capability=s-qc-policy-v1、6 GiB/2 CPU；原 S 两副本与 MB 四副本保留，默认通道仍旧修订 32，无新候选产品任务。证据 `.build/sx-m1-20261007/control-release-v1/`，详见 M1 记录第 5 节；下一步认证发布后正常选择匹配身份并预检/提交五帧，M1 未验收，M2 至 M4 未启动。
- 2026-10-07 恢复推进 M1，保留 S 研究阶段收尾决定。补 `s-qc-policy-v1` Worker 匹配及来源声明不能覆盖窗口策略；Go 四包、Python 297/1 跳过/1 既有排除、契约 50 通过，对照工具新增权限预检后专项 20 通过（范围重叠）。105 fa8dac4 基线融合候选 v2 仅十项 Python 差异，三组合成数组及真实 S 属性核验通过。串行窗口真实 S 同输入 9,412 数组、配置/上下文/参数全部一致；首轮回执权限失败保留，使用原部署用户重跑后通过。原 S 两副本及融合四 Worker 均健康，没有启用候选 Worker、切换 Go/Web 或发布产品。见 `docs/SX_M1_PROGRESS_20261007.md`；下一步有限候选发布及五时次回放，M1 未验收、M2 至 M4 未启动。
- 2026-10-07 用户本轮要求先整理文档及推进步骤，再逐步实施；维持六分钟及先各站 QC 后融合。推进计划补清 X 订正的代码、配置、实际雨段及强降水验收四层状态，以及 M1 固定五时次台账、候选发布和页面核验顺序。本轮仅文档，不派发任务或切换现网；既有 M1 证据保留，后续恢复从最小任务单开始。
- 用户最新决定：当前 X 只有六分钟资料，S、X 均按六分钟推进；两者先完成各自质控、后融合。一分钟产品等取得真实逐分钟资料后再考虑。
- 本轮先形成文档，后续逐批实施。入口为 [六分钟 S X 质控与融合推进计划](docs/SX_QC_FUSION_ROADMAP_20261002.md)，顺序为 M1 统一质控产物与验收基线、M2 完善单站 QC 和 X 有效订正、M3 六分钟质量融合、M4 逐站扩展与运行验收。
- 2026-10-03 已开始 M1，记录见 [M1 推进记录](docs/SX_M1_PROGRESS_20261003.md)。目录按产品、请求站集合和冻结实现/配置身份隔离；时间轴与逐帧查询固定系列；修复 X 的空 QC 版本遮住实际处理版本，直接与流式共用选择规则。冻结一小时 137 项任务及 95 份成功清单、19 个试点原始资产登记；审计有来源 QC 版本缺口，尚未取得 Z9591＋ZF101 五个连续成功帧。
- M1 当前为本地实现与验证，候选发布及真实地图/点查尚未完成；M2 至 M4 未启动。新增实际 S 资产 QC 参数/库版本与已声明修订传递，X 基础及增强参数身份保留，直接/流式/清单一致；105 冻结 S 属性回读确认有参数摘要、缺生产实现修订。相关 Python 143 通过、1 跳过；Web 173 通过及构建通过。下一步新 S 产物生产代码身份、点查关联与固定回放，再按既有流程发布；保留其它 S 研究修改，不自动启动定时任务。
- 随后补齐 S Worker/离线回放的实际 Python 源码身份及漂移写出门，单波段点查使用自身胜出索引，Go 返回实际来源 QC 身份，缺测/无回波分别判断；Python 208 通过、1 跳过、1 既有用例排除，Go 相关三包通过，Web 174 通过及构建通过，契约 50 通过。105 已构建 `sx-m1-qc-identity-20261003`，与原 S 镜像仅两项 Python 文件差异；未启动候选 Worker、真实任务或切换 Go/Web。下一步真实 S 同输入逐数组对照，再衔接固定五时次融合回放及页面验收。
- 真实 S 对照工具及 X 增强历史基础参数缺口回归 28 通过，相关组合 229 通过、1 跳过、1 既有用例排除，契约 50 通过。旧固定 S 请求原配置 SHA 未在本机/Git 找到；另冻结当前配置匹配的真实成功请求。105 基线审计在 12 GiB 上限下以 137 退出，内核确认容器内存限制；无数组回执、候选未算、未发布或重启现网，六个相关 Worker 审计后仍健康。私有证据在 `.build/sx-m1-20261002/s-real-*-v1.json`。下一步核查远端旧配置并安排足够串行资源，不能记为真实数值通过。
- 后续已核查远端 376 份 YAML，未找回原摘要；上游 QC 完成摘要、Go 窗口策略冻结、Python 新读取及缓存命中核验、系列 v2 已有本地实现。同站不同配方阻止混跑，缺站保留冻结策略，身份不完整按运行批次隔离。相关 Python 274 通过、1 跳过、3 既有用例排除；冻结策略最新专项 15 通过，范围重叠不相加；Go 四包、105 临时表目录三项及真实 S 标记一项通过。真实标记缺实现修订和库版本，未知不补写。S 候选 v2 `sha256:7a85337ff197e606dc9072ceb9e1981dddce09bfaca50709628d81ee32f9b966` 与基线仅三项 Python 差异，未启动 Worker、未算真实数组或切换 Go/Web；发布前仍需验证 Worker 能力匹配，M1 未验收。

## 2026-10-02 质控界面审核微调（第十六批 Web，已部署 105）

- 用户要求整体界面审核（简单/直接/清晰/交互方便，重点质控）。审核结论：模式感知工具栏、顶栏统一、
  防闪系列等既有基础良好；本轮落地三处：① fusion 模式隐藏恒禁用的"对照布局"组（overlay 保留，
  双图=原始+质控有实义）；② fusion 隐藏近乎空白的"地图图例"条（组合图例在图区内，原条只剩
  折叠的资料详情）；③ X 站名去重——下拉与侧栏 "ZF101 · ZF101 X 波段候选站" →
  "ZF101 · X 波段候选站"（model.ts 新增 stationDisplayName 去前导站号）。
- 168 用例全绿+build；部署 hash `d7bd7cc1…`，105 实测 fusion 工具栏仅剩
  验证方式|刷新|更多，overlay/单站不受影响。提交 `29a4483` 已 push。
- 注意：105 web dist 会被并行会话重新构建部署（10-01 出现过），联调前先比对
  dist/index.html hash 与本地构建。其余审核候选（预报页控件分组、时间轴密度）暂缓，待用户反馈。

## 2026-10-01 S 径向残留再研究（非生产发布）

- 用户明确只有基数据，无SQI/脉冲统计/IQ，要求用其他方法；后续不再将额外信号域资料作为主线前置条件。105八固定体扫source_moments实际清单均无SQI；audit_s_source_moments.py保留只读原始矩量/QC上下文复核，original-moment-inventory-v1及base-only-context-inventory-v1回执有输入SHA。
- fan_angular.py诊断原始跨射线曲线落地（不接生产动作），三独立原射线/目标+保护块排除/双侧两波束内插值/中间射线独立验证/几何与天气屏障/120km来源界限。5新增用例通过，相关套件通过；fan-angular-probe-final-v1七ROI模型/匹配全部0。08:36 1354候选分区653不足3原射线、663不足3邻近射线、38不足3独立模型。宽父单侧/原参考不足仍未解决，不能放宽拟合冒充改善。
- 新上下文发现：7目标扇区固定QC的V7_VERTICAL_SUPPORT_SCORE/V7_CROSS_RADAR_SUPPORT_SCORE/WEATHER_SUPPORT_SCORE实际有限门0，不能当无天气。Z9598已有较丰富TEMPORAL_CANDIDATE_PERSISTENCE，定义为候选票而非确认来源；下一步核验过去独立Stage A的确认来源及冻结时空注册。不能仅凭重复出现动作。未部署/生产写入；全链/天气验收仍未完成。
- base-only-context-inventory-v3成功解析QC径向上下文JSON，Z9598三例各有6个冻结上下文artifact及真实decision_cutoff/参数SHA/支持统计，可沿这些既有基数据输入复算过去独立来源；不得重新选择未来体扫或借当前弱片训练。

- 下一轮fan_states.py/default-off fan_power_states_enabled已落地：仅原始同射线训练功率状态，目标+保护块排除，独立块验证、实测SNR、争用永久弃权，只有诊断无新动作/来源。六新增测试含source writer通过，完整相关套件通过。回放fan-power-states-final-v1七ROI新增匹配增益0；10:42/11:24匹配1/4，与原模型重叠，Z9598均0。未部署，不声称解决。
- 互斥原因fan-power-state-partition-v1.json：10:24 93无有界同射线原来源/13状态不足；08:36 52未提名/1294无同射线来源/60状态不足；08:42 227/352/202。后续需完整原始父对象跨射线角向关系+独立上下文，禁止目标弱片自建新状态。两复核脚本新增--power-states，保留原快照SHA。

- 保留scripts/audit_s_fan_processing.py：目标+保护块全部射线排除后，配对DBZH/SNR距离校正只读反证；Z9591 10:42一致性1→3、11:24 4→5，Z9598 08:36/08:42仍0，不生成动作。回执.build/s-discontinuous-20261001/fan-processing-audit-v2.json冻结输入/模型/模块/脚本SHA。
- fan-residual-availability-v1.json：Z9591四抽查区无全偏振且SNR≥10门；Z9598部分实测rho中位数约.965–.980，不能把全部残留当低相关杂波。形态提名不等于污染真值，09:48仍缺准确截图scan。
- docs/S_RADIAL_RESIDUAL_RESEARCH_20261001.md新增顺序：冻结宽父弱状态独立检验→实测偏振/SQI可用性→无锚时空上下文→天气对照及完整QC/Hybrid/组合/Web。相关radial_revision测试通过；本轮仅研究审计，无部署/服务重启/产品写入，未彻底解决。

## 2026-10-01 S 宽RAW对象诊断落地

- raw_fans.py/default-off raw_fan_families_enabled接完整账本，20km块稀疏2–90°角域、首块固定±两波束/60km块距、单条短内侧片保留；天气/几何切断、原始来源ID不晋升，只有诊断不动作。8新增测试，相关164passed，旧fit/动作逐字段不变及writer验证通过。
- 固定八快照raw-fan-v2 SHA0fce5f8a…通过：10:24宽提名106/106（窄62），08:36为1354/1406（窄174），08:42为554/781（窄64）；仅覆盖、非清除/真值。08:18有352宽候选无任何原来源线索，08:42有227未进宽模型，需独立资格和互补窄路径。
- scripts/plot_s_raw_fans.py保留，两例图+输入/replay/image SHA在raw-fan-visual-v2；红色候选仅诊断。下一步原来源/宽父模型身份核验及来源目标窗口分离、独立偏振/上下文，随后C/留出/全产品验证。没有提交、105部署或生产写入；目标仍active。

## 2026-10-01 S 窗口追踪效果与继续研究

- source_window可变宽20/60km窗口本地候选已实现，相关156测试通过，未部署。八旧快照window-source-v1中七有残留抽查区2947门仅10新增资格（10:42三门、08:36六门、08:42一门），不是生产删除/干扰真值；09:48仍未精确绑定截图。
- 新增只读scripts/audit_s_source_window.py，校验输入scan/SHA并互斥分类；failure-partition.json显示Z9598 08:36有1221/1406、08:42有716/781未进窄家族或窗口，Z9591 11:24有169/185窄提名却无合格窗口父。先补宽RAW扇区父模型，再补无父独立观测判别，不继续整体降门槛。
- 已完整网页读取MIT ATC-454第3节：弱断续spike还使用SQI保护混合天气，不能移植形态后忽略SQI缺失。研究方案docs/S_RADIAL_RESIDUAL_RESEARCH_20261001.md顶部追加实施/停止条件；仍需宽父、上下文、独立天气/留出及QC→Hybrid→组合→图/Web验收，无新服务/产品写入。

## 2026-09-30 ZF702 径向源预算修复复核

- 本地 main 已 push a5ed2bb、3027252；105 专用 `rainpulse-ops-xqc-budget-r3-worker` 使用镜像 `xqc-budget-3027252`。保留其他 Worker/历史凭据，未重启全天 X 定时。
- 正常发布 r2 任务60b6be3f：cut0远距>30km、QC≥15dBZ残留77079→1456，raw逐门不变；cut5为81840→1409。cut2仍fan超限、cut4证据超限，不能全层验收。
- r3分离邻接几何计数与500000模型预算，异构诊断列表无损压缩。197项当前相关测试通过，旧参考不适配失败另留日志。离线cut2源完整422067试验；cut4完整EVALUATED、361670试验、证据3042809字节，未提升证据预算。
- r3旧任务0d0406da/a4736904在计算后遭同期网络更新a3dff504→74bfcf66，发布身份门拒绝。旧Worker停止且租约/注册过期后正常abandon，旧凭据保留；retry因网络已改变而正确409拒绝。最新网络冻结只读后新预检查/提交任务 `519cbd32-9c4c-4521-9635-36008b06e9e0` / attempt `0b84533d-44fd-4abf-9cc0-fb475f2e6e38`，run `afc0f416-b3ca-48ef-b987-90863dfa71d2`，Worker `rainpulse-ops-xqc-budget-current-worker`（镜像3027252）正在算全9层；轮询 `/tmp/rp-zf702-poll-current.py`，状态 `.build/xqc-budget-3027252-current/focused-task.json`。原默认release已恢复，未SQL改状态。
- 后续发布镜像 `xqc-budget-d60fbb8` 已构建：上传前先校验身份，计算期间明确的配置漂移在进入COMMITTING前BLOCKED；真正PUT响应丢失仍可恢复。生产Engine红绿及整套测试通过，未替换运行中的验收Worker。
- cut9独立ACTION_BUDGET保护未绕过；Go/Web已部署分层状态告警并在实际界面验证。旧URL固定历史result不会覆盖，需新result链接。下一步等待r3正常发布，逐层raw身份/残留/状态及真实地图复核后决定默认Worker升级。

## 2026-09-30 底图源可配置：内网瓦片为默认（第十五批，已部署 105）

- 用户要求后台支持内网瓦片配置（192.168.18.101:20001 行政/地形/卫星，Mercator XYZ）且默认内网，
  天地图保留可选。
- Go：`operations/basemap_config.go` 配置域（`runtime/basemap-config.json`，env
  `RAINPULSE_BASEMAP_CONFIG_FILE` 可覆盖；缺省 seed=内网 XZ/DX/WX + TDT[vec_w+cva_w，{token} 由
  服务端替换、{0-7} 子域展开]）。代理 `/api/v1/workspace/basemap-tiles/{key}[/…-annot]/{z}/{x}/{y}.png`
  按 key 取源（旧 vec/cva/img/cia 键保留兼容）；公开 `GET /api/v1/workspace/basemap-config` 只回
  key/label/hasOverlay（不泄内网地址）；管理 `GET/PUT /api/v1/admin/ops/basemap-config` 与
  `POST …/reset`（恢复默认 seed）。校验：key 唯一 1-16 位、URL 需 http(s) 且含 {z}{x}{y}、default
  必须在列。单测 5 个（配置 seed/回写校验/代理 key/旧键/鉴权），Go 全量 29 包 ok。
- Web：`src/basemapSources.ts` 运行时源 store（useSyncExternalStore + localStorage
  `rainpulse.basemap.key`，取不到配置回退构建期 env URL）；RasterGISMap 顶栏页脚换为底图源
  下拉（行政/地形/卫星/天地图），切源 setSource 不重建地图；`-annot` 注记层动态增删。
  后台：系统运维新增"底图配置"页签（admin/Basemaps.tsx，行级编辑+保存+恢复默认）。
- **重要坑（OL 瓦片层休眠）**：零尺寸挂载时 OL TileLayer 可能从不发瓦片请求（连初始源都不发，
  与 URL/代码无关；source.refresh()+map.renderSync() 可唤醒）。修复：创建后 600ms kick
  （refresh+renderSync，测试桩无 renderSync 需 typeof 防御）+ 每次切源后 refresh。此为 105 产线
  "部署后瓦片 0 请求"的真因，此前误判为用户网络问题。
- 验证：本地 dev 与 105 实测默认 XZ 12 片 0 失败、DX/TDT(含 11+11 注记) 切换正常、像素确认内网
  行政瓦片渲染；web 161 用例全绿（并行会话 XQCStatus WIP 曾缺 cleanup 后已绿）；dist hash
  `1ab73656…`。服务仍为手动实例（见上条），ingest 待用户 sudo 交回 systemd。仍未提交。

## 2026-09-30 底图天地图改服务端代理（第十四批修订，已部署 105）

- 直连方案（下条）在用户浏览器失败："底图服务暂不可用"。原因：浏览器端无外网直出/被天地图 WAF
  拦截；且该 Key 为浏览器端类型。改为**服务端代理**：Go 控制面新增
  `GET /api/v1/workspace/basemap-tiles/{vec|cva|img|cia}/{z}/{x}/{y}.png`（operations/basemap_proxy.go，
  校验层级/坐标范围、UA+Referer 伪装浏览器、8MB 上限、Cache-Control 1d），token 服务端化：
  env `RAINPULSE_TIANDITU_TOKEN` 或文件 `deploy/tianditu-token`（yons 可写；新增文件回退，
  因 systemd EnvironmentFile root-only）。web 侧 `.env.local` 改同源代理 URL（dist 已无外链/token）。
  单测 2 个；Go 全量 29 包 ok；二进制 stripped 交叉编译（`-buildvcs=false -ldflags "-s -w"`；
  本地 HEAD 6446133 为 105 已部署提交的超集，已核对 42f8cb1→HEAD 历史，不回退他人部署）。
- **105 无免密 sudo 的部署路径与事故**：目标二进制 yons 属主，运行中直接 cp 报 ETXTBSY，须同目录
  cp + 原子 mv；`systemctl restart/start` 需认证；pkill 优雅退出（码 0）不触发 Restart=on-failure。
  本批 pkill 后服务未能拉起且 root env（/etc/rainpulse/control.env，含 DB 密码与 ingest
  manifest）不可读 → 服务中断约 2 分钟。恢复：`deploy/start-control-manual.sh`（source
  deploy/.env，映射 POSTGRES_PASSWORD→DATABASE_PASSWORD、NATS/MinIO 指 127.0.0.1）手动拉起；
  页面/API/瓦片全部 200，但 ingest 后台模块缺 RAINPULSE_RADAR_INGEST_MANIFEST（root env）未运行。
- **已交还 systemd（2026-09-30 13:30，用户授权 sudo 后完成）**：手动实例 pkill 后
  `systemctl start rainpulse`，unit 用新二进制+完整 env；active、页面/瓦片 200、ingest 模块恢复
  （manifest 错误消失）、tianditu-token 文件回退在 systemd 环境继续生效。手动实例日志
  runtime/control-manual.log；旧二进制备份 rainpulse.pre-basemap-proxy.bak。sudo 口令仅使用一次，
  未存任何文件。
- 验证：105 curl 瓦片 200（vec/cva 各 20KB PNG）；浏览器 24/24 瓦片成功、无失败提示、像素分析
  确认浅色矢量底图渲染。前端 hash `ea146a29…`。仍未提交。

## 2026-09-30 ZF702 当前 v2 严重残留诊断（尚未修复）

- 用户链接 task b2fc710d / attempt a2d51715、scan 3b3bf9da 指向当前 sx-quality-v2-z10，SUCCEEDED并非旧审计档。浏览器与逐门证据确认严重长径向/南侧扇形残留。
- 0/2/4/5切面radial_source均RESOURCE_LIMIT_ABSTAINED（model-trial budget exceeded，配置默认500000）；异常使整个源模型结果置零，外层仍EVALUATED/任务SUCCEEDED。切面0 >30km且>=15dBZ残留77079门，其中67103门形态候选却未提出排除；hard保护0、local仅4，非主要降水保护问题。
- 6/7/8/10源模型完成；9另有ACTION_BUDGET_ABSTAINED。需优化重复试验、处理部分完成/降级可见性，再全层与跨站时次验收。此次只完成定位，未更换算法或结果。证据 `.build/xqc-zf702-investigation/REPORT.md` 及 cuts/evidence/task 文件。

## 2026-09-30 底图切换天地图（第十四批 Web，已部署 105）

- 用户提供天地图浏览器端 Key，要求底图改用天地图。实现走既有 env 通道：`.env.local`（gitignore，
  token 不入仓库/记忆）设 `VITE_BASEMAP_URL=…T=vec_w…` + `VITE_BASEMAP_OVERLAY_URL=…T=cva_w…`
  （_w 为 Web 墨卡托，与现有 OSM 相同的自动重投影路径；{0-7} 子域展开 OL 原生支持）。
- 代码仅新增可选注记图层：RasterGISMap 读 `VITE_BASEMAP_OVERLAY_URL`，在底图之上叠一层 XYZ
  TileLayer（className rainpulse-basemap-overlay-layer），跟随底图显隐切换与重建清理；未配置时零变化。
  `.env.example` 补充说明（示例用 YOUR_TOKEN 占位）。归属显示"天地图 GS(2023)2767号"。
- 验证：158 用例/tsc/build 绿；部署 hash `6233234b…`。105 侧 curl 复核（浏览器 UA+Referer）：
  vec_w 200（256×256 PNG）、cva_w 200 且 `Access-Control-Allow-Origin: *`；服务端裸 curl 403
  code=301012（"Key权限类型为:浏览器端"）与 WAF 对 HEAD 的 418 均为预期。本会话内嵌浏览器无外网，
  瓦片渲染以用户浏览器为准。若某环境瓦片不可达，既有"底图服务暂不可用"提示与海岸线兜底仍生效。
  仍未提交。

## 2026-09-30 顶栏页签统一（第十三批 Web，已部署 105）

- 用户反馈界面设计不统一（图1=预报对比页页签在第二行左侧带边框药丸 preset-tabs；图2=质控排查
  页页签在顶栏内居中偏右、无边框青底），要求统一为图2样式。
- 新增共享组件 `workspace/WorkspacePresets.tsx`（button+role=tab，默认整页跳转 `/?preset=`，
  onSelect 可选站内切换）；MainWorkspace 页签从 workspace-controls 移入 workspace-topbar
  （保留 qc 跳转/verification pin/applyTimeSelection 原逻辑，删除 presetLabels 与旧 preset-tabs 块）；
  RadarQCWorkspace 顶栏改用同组件。`radar-qc-presets` 样式从 radar-qc-workspace.css 移入
  workspace.css（含 button 变体与 ≤760px 整行规则；qc css 中重复规则删除；preset-tabs 样式清理）。
- 158 用例（含新增 WorkspacePresets 单测）+tsc 干净+lint 基线（仅既有 CompositeMap.test 1 错）+build
  通过；部署 hash `2dd880d3…`。105 实测：预报对比/检验回放/质控排查三页顶栏一致，页签站内切换与
  qc 整页跳转正常。仍未提交。
- **返工（用户报后台按钮掉到第二行最左）**：首版把 freshness/警告/后台当 5 个平级 grid 子元素，
  超出 `workspace-topbar` 4 列模板，auto-flow 把第 5 个换到第二行第 1 列（grid 隐式列假设错误）。
  修复：MainWorkspace 右侧三者收进 `workspace-topbar-side`（flex+justify-self:end），顶栏恒 3 个
  grid 子元素；重部署 hash `ee347026…`，1280 实测（含警告按钮）与 420 手机、QC 页均单行/整齐折行，
  后台恒最右。

## 2026-09-30 S 全天回填仰角边界修复并恢复

- S 全天批次在 `17-z9591` 被两份体扫阻断。根因是 `sweep_006` 的 float32 仰角 4.04/4.34 转为 float64 后跨度为 0.3000001907°，裸比较误判超过 0.3°；随后时次因引用该体扫作为时间上下文也失败。
- `09eecdd` 已提交并 push：仰角跨度比较加入按源 dtype 精度计算的极小容差，明显超限仍拒绝；专项 44 项通过。扩展集合 87 通过、3 个既有失败位于 `volume_review`/diagnostics 契约，与本次改动无关。
- 105 S Worker 已部署 `rainpulse-cpu-worker:s-elevation-09eecdd` ×2，容器源码 SHA256 均为 `ecd40c550a5466de6012d1d559c989e183be3140fb092f2067bab6a92a3a37d4`。两份失败扫描经 `radar-qc-rebuild` 正常重算成功，原 FAILED 任务保留。
- 全天批次从原检查点恢复于 `.build/s-qc-elevation-09eecdd/s-backfill-resume/state.json`；`17-z9591` 已完成并推进到 `17-z9593`。后台 PID 记录在同目录。容量保护仍为 120 GiB/100k inode。X Worker、X 重算及小时定时保持暂停。

- 后续复核：17-z9593也已完成，当前17-z9598；本轮27成功/2运行，无新增失败。四站最新样本44切面中36个含反射率切面DBZH_RAW与normalized逐值相同，8个速度切面原无DBZH。证据105 `.build/s-qc-elevation-09eecdd/latest-s-audit.jsonl`。
- 发现Worker任务结束仍保留约80GiB内存，队列完全空闲后重启两台释放，可用内存4→89GiB；同部署目录idle-recycler.py仅在runner WAITING_CAPACITY、MemAvailable<16GiB且QC队列0时回收，批次结束自动退出。
- 用户已清理部分数据；03:31Z实测系统盘526.6GiB、MinIO数据盘121.5GiB（不能混淆）。此前下载缓存为空，模型迁移尚未获授权；继续120GiB保护线，待用户清理数据盘或授权两份模型迁移。

## 2026-09-30 zf505/zf702 全天 X 质控按最新算法重算（v2-z10，取代 r7x 审计档展示）

- 用户报 zf702 X 单站"没质控"。根因：09-29 r7x 审计重算把 zf505/zf702 的 results[0] 换成
  all-quarantine 审计档（`…v3-xqc2-all-quarantine-radial-source-1d81cd1-blocks-d075740-fans-fc757e8`，
  qc==raw 字节相同、不剔除任何回波）；zf101/zf401 未受影响。前端无 bug。用户拍板"用最新的
  X 波段质控算法重算"。
- 当晚并行会话已再次部署：workers `x-sxfusion-2e7ba35-mb`(×4)/`-qc`(×1)（比记忆中 e04383c/
  a4032ee 新，迭代频繁、以现场容器为准）；mb 网络档 `.build/sx-priority1/fujian-full-experimental-20260828.json`
  （release `sx-quality-v2-z10-20260930`，挂载 /opt/rainpulse/multiband/network.json）。
- 重算走 admin ops preset `x_qc`（单计划 ≤8 体扫 → 20 分钟窗 ×2 站）：冒烟 zf702 00:00–00:20
  run 683fe767 验证全仰角 raw≠qc 后，21 批（00:00–07:00Z 两站全天数据）全部 SUCCEEDED；覆盖
  zf505 66/66、zf702 67/67，新结果版本 `sx-quality-v2-z10-20260930`，抽检（首/中/末+原问题体扫）
  低仰角 0/2/4 全 DIFF，candidate_only=true（不影响 QPE/预报/默认展示）。旧版本结果仍在目录多版本
  并存，v2 排 results[0]，前端默认即选它。
- 浏览器验收：ZF702 00:26:55Z（原问题体扫 3b3bf9da）双图正常渲染、质控图明显清除近站杂波、
  无报错。提交/监控脚本留存 105 `/tmp/rp-submit-fullday.py`、`/tmp/rp-fullday-runs.json`、
  `/tmp/rp-monitor-fullday.py`。并行会话当时 v2 验证 run 两次 FAILED（v2-probe-cycle0006 等），
  其后续若再换镜像，以 ops runs 与结果版本为准。Web 侧 12 批未提交改动不变。

## 2026-09-30 多站叠加"降水图层暂不可用"提示去除（第十二批 Web，已部署 105）

- 用户反馈多站叠加每切一次提示"降水图层暂不可用"。根因：切时次时 S 详情与 X 图层
  解析同时在途 → 图层列表瞬时清空 → 命中无图层空态（strong 误用产品页通用文案）。
- 修复：图层列表在途保留上一帧；空态 strong 优先用调用方 emptyStateHint（layerError
  分支保留原文案）；多站叠加解除全局 layerError 接线，RasterGISMap layerError/
  onLayerError 改可选。105 实测全选 28 站四档切换提示 0 次、图层计数保持。
  全量 157 用例+build+lint 基线通过。仍未提交。

## 2026-09-30 组合反射率残余闪最终修复（第十一批 Web，已部署 105）

- 用户复测回退区仍闪。逐帧双画布哈希探针定位：① 组合清单在途窗口回放最后一份旧
  组合（第十批守卫未拦"在途"）→ compositeMemory 记录 have/absent 确认态、仅上次
  确认"有"才在途保留，并删除跨区兜底；② 全范围产品逐帧 bounds 随参差站微变（实测
  10:30/10:36 两帧不同）→ 取景实时计算导致每次切帧视图重定位 → 冻结首次取景
  （渲染期 setState），「定位产品」手动重定位仍可用。
- 105 实测四档距离切换仅两态转换、底图哈希恒定（视图零移动）。全量 157 用例+build+
  lint 基线通过；部署 hash `bccfbd80c32e86ac`。仍未提交。

## 2026-09-30 组合反射率回退区切换闪屏修复（第十批 Web，已部署 105）

- 用户反馈 09:06 后组合模式每次切换再闪一下（第九批恢复切换后暴露的回退路径闪源）：
  回退层（已发布 S 拼图）来自逐周期详情，在途时图层清空 → fitExtent 翻转 → 整图重建。
- 修复：回退层在途保留上一帧；组合区→回退区首次过渡用保留的最后组合 S 产品兜底；
  详情到达即换新、确认无帧清空（不冻结）。新增受控延迟回归测试。
- 全量 157 用例+build+lint 基线通过；105 实测回退区连续切换视口/画布零替换、图层
  不清空。仍未提交。

## 2026-09-29 组合反射率 09:06 后切换无变化修复（第九批 Web，已部署 105）

- 用户反馈组合模式 09:06（北京时，最后一份组合产品的时次）之后切换完全无变化。
  根因：第八批 stale 逻辑未区分"请求在途"与"已确认无组合"——09:06 后 API 无组合
  结果，保留逻辑把最后一份组合永久冻结在屏幕上。修复：仅 `compositeState===undefined`
  （在途）时用保留清单，确认无组合立即回退"已发布 S 产品"。
- 新增回归测试（有组合显示产品层/无组合时次回退并消失）；全量 156 用例+build+lint
  基线通过。105 实测 08:54→10:00/12:00/15:00 状态与画面正确切换（截图确认回波
  内容随时间变化）。仍未提交。

## 2026-09-29 X 单站与组合反射率残余闪烁修复（第八批 Web，已部署 105）

- 用户复测 S 单站/多站已不闪，X 单站与组合仍闪。视口标记探针定位：X 是 useXPair
  fetch→blob→decode 校验多一跳，换图延迟>800ms 触发"正在切换时效"浮层闪现——改为
  同步派生图件路径（错误交地图层 imageloaderror），切时次保留用户仰角；组合反射率是
  产品图层清空导致 fitExtent 在 DOMAIN↔产品范围翻转、整个 OL 地图销毁重建——按
  result_id 保留上一份组合清单（渲染期比较用稳定标识，防引用比较死循环）。
- 105 实测：X min 方差 98.6%、组合 98%，视口/画布均不再被替换。全量 155 用例+build+
  lint 基线通过。涉及 RadarQCWorkspace/MultiStationMap/CompositeMap（导出类型），
  仍未提交。

## 2026-09-29 切时效地图闪白消除（第七批 Web，已部署 105）

- 用户反馈每切一次时效地图闪一下。三处根因修复：SMap 在周期详情加载期保留上一帧
  （stale-while-revalidate，确定无帧才空态）；X 的 xResult/useXPair 按站点保留上一份
  结果与图件（切站重置防串图）；RasterGISMap 多图层换层改为新层就绪后再移除旧层。
- 105 用画布像素方差高频采样实测：S 最低方差为加载态 97%、X 96%、多站叠加无下降，
  切换无闪白。全量 155 用例+build+lint 基线通过；顺带清理了重复"资料详情"节点。
  涉及 RadarQCWorkspace.tsx 与 RasterGISMap.tsx，仍未提交。

## 2026-09-29 时间轴统一 S 分析周期网格（第六批 Web，已部署 105）

- 用户要求 S/X 时间轴统一以 S 波段为准（原 X 用各站体扫实际起始时刻，带秒且各站错开）。
  改动（RadarQCWorkspace.tsx）：单站 S/X 与 overlay 合并时间轴全部用 S 分析周期网格
  （overlay 去掉 X 秒级体扫时刻混入）；观察标签统一"分析周期"；X 面板可用性帧投影到
  S 网格。
- X 体扫选择：目标 S 时刻 ±3 分钟内最近带结果体扫；无邻近时回退全天最近带结果体扫
  （防 S 切 X 携带时刻超出 X 覆盖落"无体扫"门控，实测 S 到 18:36/zf101 只到 15:54 场景）；
  地图头显示真实体扫时间，时间轴高亮吸附最近 S 刻度。
- 新增偏移体扫/无邻近回退回归测试；全量 155 用例+build+lint 基线通过。105 实测：
  S/X 均 106 帧同刻度，S(18:36)切 X 显示 15:54:59 最近体扫且高亮同步，无门控。仍未提交。

## 2026-09-29 ZF505显示准入接缝修复a4032ee（最终复核完成）

- `main` 已 push：`a4032ee`。在 `xqc_v2/pipeline.py` 同时将 `DBZH_QC`（单站地图读取）和 `DBZH_QC_DISPLAY`（候选组合读取）对 `XQC_WITHHELD_MASK` 隐藏；原始 `DBZH_RAW`、动作3、原因和证据仍保留，CR准入继续拒绝待复核门，不改S/QPE/预报。
- 105 已部署镜像 `x-residual-a4032ee-mb/-qc`，两台 multiband 与 qc Worker 健康。专项 169 测试通过。
- 最终 ZF505 单站 run `45566924-8a25-4cbc-b0eb-654fd96d1d3d` SUCCEEDED；9 层全部审计，cut4 `ACTION_BUDGET_ABSTAINED`，预算待复核26630门、源26270门，CR漏出0；所有待复核门 `DBZH_QC` 已隐藏，raw/action/flags/eligibility 与前版一致。
- 全网组合 run `e8cf9d1c-8dd4-496f-a18b-fa754a775b07` SUCCEEDED；完整网格 `1077x1238`，有效格点 S/X/S+X=`53632/13682/57854`，S 与修复前逐格一致，`CR_DBZH=max(S_ONLY,X_ONLY)` 通过，旧版相比移除3265格且无新增值/越界支撑；来源25站，ZF402/ZF703/ZF801因无可用因果输入跳过。小时定时仍暂停。
- 真实界面已复核：ZF505 单站原始/质控双图与 S/S+X 组合对照均正常，截图见 `output/playwright/zf505-final.png`、`output/playwright/fusion-sx-compare-final.png`。

## 2026-09-29 ZF505预算准入修复44e7e4b（组合验收进行中）

- 已main commit/push后部署105镜像x-residual-44e7e4b-mb/-qc。网络/阈值不变。pipeline依据ACTION_BUDGET保留待复核WITHHELD，拒绝其组合准入，保留raw及删除预算保护。
- 红测2失败2通过，修复后169专项通过。ZF505单站run e11be507-d381-40bc-b324-2a3b10a47e43 SUCCEEDED：cut4预算hold26630门/源26270门，CR漏出0，raw相同；8个其他层DBZH_QC/action/flags/eligibility逐值不变。
- 全站网08:12新run 53d18a67-6b19-4880-a4ab-49e229b19f57运行中，输入与旧run7806b948完全相同。完成后执行.build/audit-budget-composite-template.py的数组关系与旧S不变检查，再UI查看。正常全网约28min，不重启/重复提交。最新状态.build/budget-composite-live.json；轮询.build/poll-budget.py。
- 169测试日志.build/budget-admission-green.log；预算原值保持可见待复核，不应声称单站强制删净/全网已全部质控。小时定时暂停。

## 2026-09-29 当前未完成项：ZF505预算弃用进入组合

- 完整站网08:12组合数值/地图链路通过，但西南条带经WINNER_SOURCE追到ZF505。不能称全站网质控完成。
- 正常诊断run a10b2832-9d0d-4d28-9bc6-0ef975e41eb0，scan0b5c41f5-978a-5594-8a3b-88f3f70e746d（UTC00:06:31），cut4 ACTION_BUDGET_ABSTAINED，26270源污染门未应用；其余8层EVALUATED。下一步核验实际预算比例/降水保护，解决弃用层进入组合，不能盲升全局预算。
- .build/residual-zf505-audit.jsonl、.build/trace-composite-residual.jsonl存有证据；未修改算法/网络配置。当前所有本轮提交任务都已结束，无需恢复小时定时。

## 2026-09-29 X 扩大回放与组合跟进

- 用户授权清理15GiB pip下载缓存，实际释放15342678016字节；保留缓存目录、未动雷达/模型。数据盘清理后可用148859305984字节。
- 新增6体扫54层（UTC01/02/03附近）均SUCCEEDED/EVALUATED、raw哈希一致、动作实际应用；累计13体扫117层。详情docs/XQC_RESIDUAL_REPAIR_20260929.md。
- 完整网络UTC00:12组合run7806b948-b29c-4b2f-8a5d-37d930ec0cc5已SUCCEEDED并完成数值与S/X/S+X真实地图核验（约28分钟/帧），25站可用，ZF402缺测，ZF703/ZF801排除。完整1077x1238网格，S53632/X16965/SX61119有效格点；逐格max关系、来源、非负年龄通过。其他旧时次未全量更新。现场凭据.build/residual-next-composite-*，54层验收.build/residual-wide-audit.jsonl。小时定时仍暂停。

## 2026-09-29 X 径向/扇形残留修复验收（4ed11c3）

- 已 main commit/push 后部署105：ops multiband/qc `x-residual-4ed11c3-mb/-qc`，网络 `sx-xqc-residual-4ed11c3`。两台multiband健康，S镜像不变。
- 修复多响应模式、间歇源走廊、有限内段关联、无损证据压缩；ZF701/ZF702候选启用joint_evidence，独立天气保护不变；ZF702候选预算0.70通过本批回放。
- 165测试通过；7体扫63层全部EVALUATED、原始SHA不变、源标记实际应用、QPE仍关闭。用户4张问题图及ZF70208:02 cut6真实UI已核验。详见docs/XQC_RESIDUAL_REPAIR_20260929.md。
- MinIO可用约124.5GiB，接近120GiB保护线；未启动全天/组合重算。其他历史图件仍可能旧版，旧result链接固定旧版。下一步容量安全后扩大回放及候选组合更新。小时定时保持暂停。
- 全仓CI仍有既有lint/contract/S参数哈希失败，不能声称全仓绿。保留其他任务Web及memory脏文件。

## 2026-09-29 X 切站“该时刻无体扫”误报修复（第五批，已部署 105）

- 用户反馈：X 波段切换站点提示"该时刻无体扫"，切时间再切回又有了。根因：切站触发
  `radar-scans` 按新站重拉，期间 `scans=[]` 且 `target` 保留，门控把加载态当成确定性
  "无体扫"结论（X 按站取数、S 目录全局一次，故仅 X 切站可见）。
- 修复三处（RadarQCWorkspace.tsx）：① 新增 `xScansLoading`，加载中门控显示
  "正在读取体扫… 请稍候"，不再误报；② 槽位匹配优先选**带结果**的体扫（原 find 可能
  选中无结果体扫直接进门控）；③ 无同槽体扫时回退**最近带结果体扫**（跨站 target 时段
  不重叠也能出图，时间轴 target 不变）。有 scan 无结果时门控显示
  "qc_status · 该体扫暂无质控结果"。
- 新增挂起 fetch 的回归测试（加载态文本 + 释放后出图）；全量 154 用例 + build +
  lint 基线通过。105 部署实测 zf101→zf102 切换全程双图、新站 40 层仰角、无门控误报。

## 2026-09-29 组合反射率去除站点选择（第四批，已部署 105）

- 用户定论组合模式无需逐站选择：侧栏不再渲染站点选择区，改为「组合反射率 ·
  N S + M X 全部参与」标题+说明；「生成组合」radar 清单改为全部已登记站点；
  组合模式不再发起逐站 X 图层解析 fetch（无渲染消费）。多站叠加模式不变。
- 全量 153 用例+build 通过；105 部署实测：侧栏 0 复选框、生成组合链接含全部 28 站
  （chunk `index-Cg8B0ZKs.js`）。仍未提交。

## 2026-09-29 质控排查第三批微调：卷帘移除 + 日期移至时间轴最左（已部署 105）

- 用户定论"卷帘没用"：删除 swipe 布局全链路（Layout 类型、按钮、CSS、
  MultiStationMap layout 类型收窄 'pair'|'single'），对照布局只剩 双图/单图。
- 日期从工具栏移到时间轴最左（SharedTimeline cycleControls 槽，紧贴播放控制左侧、
  间距 7px 成组靠左，状态信息保持右侧），缩短选日期→拖时间轴的鼠标移动；
  修复 QC 壳 context 行 space-between 造成的大空洞。
- 全量 153 用例+build+lint 基线通过；105 部署实测生效（chunk `index-eU7rjKCY.js`）。
  注意 105 的 index.html 有浏览器缓存，验收需带 cache-buster 刷新。仍未提交。

## 2026-09-29 多站叠加/组合反射率 站点选择重设计（Web UI，已部署 105）

- 用户指出「S/X/S+X 全部」点击无反应。根因：`select()` 只有追加语义，已全选时无任何
  变化；且组合反射率复用整个侧栏，逐站仰角/透明度/顺序控件在该模式不渲染图层（死控件）。
- 重设计（先方案后落地，见 `docs/多站叠加_站点选择重设计_20260929.md`）：搜索+波段分段
  筛选（全部/S 站/X 站）+「全选（切换语义，aria-pressed）/清空」作用域=当前筛选；
  目录与"显示图层"两列表合并为单一站点列表（勾选行内展开仰角/透明度/聚焦/上移）；
  列表顺序=叠放顺序（首行在图上层）；组合模式隐藏逐站控件并注明"勾选用于生成组合
  与点查"。sessionStorage 旧 `visible:false` 归一；失效站点选择自动清理。
- 涉及 MultiStationMap.tsx、radar-qc-workspace.css、RadarQCWorkspace.test.tsx；全量
  153 用例 + build + lint 基线通过。105 index SHA 前 16 位 `254ee2b82cdafbee`，
  实测全选 28/32↔取消 0/32、S 站过滤全选=4/32、组合模式裁剪生效。仍未提交。

## 2026-09-29 质控排查布局简化 + overlay/fusion 直达崩溃修复（Web UI，已部署 105）

- 用户反馈 `/?preset=qc` 凌乱、要求简单直接清晰。审查发现 overlay/fusion 模式 URL 直达稳定整页崩溃：
  数据未返回时 `activeTime` 为空字符串传入 SharedTimeline，`new Date('')` →
  `RangeError: Invalid time value`（堆栈 `MainWorkspace.tsx timelineDate`）。修复
  `selectedTime={activeTimelineTime||null}`，新增 overlay 无 time 参数回归测试
  （既有测试都带 time 参数，曾掩盖该路径）。
- 布局 8 条横条简化为 5 条：预设页签并入顶栏；验证方式/波段/站点/仰角/日期/质控标记/
  布局/刷新合并为单一工具栏（日期从时间轴上移）；删除死控件“字段”下拉与摘要条；
  资料详情并入图例条右侧。地图区 664→768px。
- 仅改 apps/web 三文件（RadarQCWorkspace.tsx、radar-qc-workspace.css、其测试）；npm test
  152 用例、lint（无新增问题）、build 通过；变更未提交（保留现场既有未提交工作）。
  `CompositeMap.test.tsx` `_input` lint error 为 HEAD 既有，与本变更无关。
- 105 就地更新 dist：回退目录 `apps/web/dist.pre-qc-simplify-20260929`，原 index SHA 前
  16 位 `36d897c0b1fd2f59`，新 `c5bcf68fb2ea750d`。浏览器验收：单站新布局正常、
  `mode=overlay` 不再崩溃；fusion 同修复路径，未逐一浏览器复测。
  详见 `docs/质控排查_布局简化与模式崩溃修复_20260929.md`。
- 同日第二批（用户要求重设计时间轴，已部署 105）：SharedTimeline 观察模式
  （observationOnly）改为自适应全宽轨道 `.observation-fit`（原 4px/分钟+24px 最小宽
  必然横向滚动），帧宽 `min(48px,max(3px,比例))`，>120 帧密集模式；状态行内联
  `第 N/M 帧`+图层可用性徽章+键盘提示，删观察模式独立可用性条；区间/预报模式渲染
  路径与契约不变（105 实测无回归）。全量 153 用例+build 通过；105 新 index SHA 前
  16 位 `25dc93f08ed21a8b`。改动含 MainWorkspace.tsx（SharedTimeline）与
  workspace.css，仍未提交。

## 2026-09-29 S+X 组合产品用最新 X 质控重算（run 30a5ad71）

- 用户报告融合视图 X 部分仍有很多质控问题。定位：页面 mode=fusion 显示的是独立的 sx-composite 多站产品时间线（MultiStationMap composite），由 sx_composite 计划生成（任务内联跑 X 质控，用执行时刻的代码/网络）；用户 result 链接是单站 x_qc 任务（已最新）但组合模式不读它。旧组合产物=旧 X 质控。
- 重算：preset sx_composite，product_id sx-fujian-full-test，2026-08-28T00:00–01:00Z（9 个时次），radar_ids=4S(z9591/9593/9598/9599)+10 问题 X（zf101-105/401/402/505/701/702），run 30a5ad71 9/9 SUCCEEDED（hardening a9e9116 代码 + c9e3a3d2 网络）。
- 验证 00:12 时次（task 0d5b357f）：单站 d41c8ce4 全切 EVALUATED、source 3411-8960/切；新组合图 X 部分辐条/扇形较旧产物明显减少（残余=证据地板类），组合图进一步平滑。组合任务图层：comparison/{s_only,x_only,sx_composite}.png + map/{winner_*,x_added_coverage,...}.png。
- 注意：Select 要求窗口 ≤1h、产品节拍对齐、radar_ids≤32；X 站需 experimental_enabled（当前全部 28 X 已开）。仅重算 00:00-01:00；其他时段按需批量。

## 2026-09-29 sx-fusion-v2-a4032ee 合入+部署（Z-Φ 独立订正 + quality_height_v2 质量分层融合）

- gpt-pro 包 `rainpulse-sx-fusion-v2-a4032ee`（基线 a4032ee=对方当日提交链顶端，24 文件：8 改 16 新）。工作区有他人未提交前端 WIP（7 文件，与包零交集）→ 保留未提交，只提交包路径（01bc636）。验证：scripts/test_sx_path_quality.sh 全绿（PYTHON=algorithms/.venv/bin/python；63 通过+1 numba 跳过）+ xqc_v2 103 + Go race/vet。
- 105 部署：Python 镜像 `x-sxfusion-01bc636-mb`(3f860dfb)/`-qc`(762bda9d)（build-sx2 上下文 = multiband + clutter_fusion + diagnostics 三层）；Go control 交叉编译（GOOS=linux GOARCH=amd64，services/control）替换 `.build/linux-amd64/rainpulse`（root 属主需 sudo cp；备份 rainpulse.a4032ee.bak）并 systemctl restart rainpulse。
- **部署坑**：compose 覆盖文件 `.build/x-qc-v2-e549510/compose.json` 已被对方于 18:51 改为引用 `x-residual-a4032ee-mb/-qc`——重打旧标签名（x-fan-fc757e8-*）无效且无提示。已把覆盖文件改为 `x-sxfusion-01bc636-*`（备份 compose.json.x-residual.bak），12 副本重建健康，新模块 attenuation/fusion_quality 导入 OK。
- 烟雾：run ad0cd866（zf701 07bdd490）SUCCEEDED。新融合产品为 opt-in 候选（默认不生效）：启用需 `prepare_sx_quality_release.py` 生成新网络/产品身份并核验证据字段（Z-Φ 系数/液态/相位/标定/湿罩），未执行。

## 2026-09-29 xqc-hardening-20260929-r1 合入+部署（gpt-pro 包 + 1 个部署阻断 bug 修复）

- 用户指令：把 gpt-pro 的 `rainpulse-xqc-hardening-20260929`（相邻切面上下文/矩量有效性修复，基线 8959496）合入 main 并部署 105。安装器要求 HEAD==基线，而 main 上只有一个文档提交：`git reset --soft 8959496` 后应用（回执 `.rainpulse-xqc-hardening-backup-ec2a371390424b5ab3c3366b5a5b173a/`），17 文件（multiband 流式/适配 + xqc_v2 全套 + 共享 clutter_fusion engine/features + schema）；分开提交 d8dc19d（包）与 5dfb2ab（r7x 记录）。本地 xqc_v2 84 + 交付 18 测试绿。
- **部署阻断 bug（包作者本地验证未跑真实 X 全字段路径）**：X 地图预览探针新增 XQC_SOURCE/CONTEXT 字段后共 18-20 个，超 `diagnostics/radar_probe.py` ≤16 契约上限 → 每个X单站任务在 map_preview 阶段 `ValueError: invalid probe index dimensions`（x.qc/evidence 本体 0 错误）。修复 a9e9116：上限 16→24（瓦片 JSON 按字段名自描述，非线格式变更）。
- 105 部署：镜像 `x-hardening-d8dc19d`（阻断）→ `x-hardening-a9e9116-mb`(60081561)/`-qc`(1ae06675)（build-h1 上下文：multiband + clutter_fusion engine/features + diagnostics/radar_probe.py 三层 COPY；注意 docker cp 不改名陷阱）；重打 compose 引用名 x-fan-fc757e8-mb/-qc（原 fc757e8 镜像保留 -keep 标签），12 副本重建健康，旧网络 c9e3a3d2 校验通过（新 context 字段默认 disabled，既有开关/阈值保留）。
- 烟雾验证 run 74bdca73（zf701 07bdd490）SUCCEEDED：探针 18 字段含 XQC_SOURCE_KIND/CONTEXT_*，cut0 REJ 16729/censor 7183，9 切 map_qc 全生成。上下文功能默认关闭未启用；启用需 `scripts/upgrade_xqc_hardening.py` 生成新网络发布身份并重启 worker（未执行）。

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

## 2026-09-30 S/X v2 融合验证（z9591+ZF10 系列）——链路全通、校准门现状与 A/B 结论

- 容量链收口 `73a14f9`：v2 几何核验路径拒 40 刀 zf10x 体扫（`MAX_FUSION_SWEEPS=32`，
  model.py "unsupported or incomplete native scan description"）→ 提至 64（与 MAX_SWEEPS 一致；
  `options.maximum_sweeps` 默认 64 不会二次卡）。至此容量修复链：MAX_GATES 8M→40M(e04383c)
  → 网络输入预算 6GiB+代码上限 8GiB(2e7ba35) → 融合层数 64(73a14f9)。
- 镜像坑：本地 `multiband/xqc_v2/*` 部分文件 mode 600，COPY 进镜像后 worker 以非 root 运行
  `PermissionError` 崩溃循环。修法：构建源 `chmod -R go+rX`（本地仓库同步 chmod）。当前镜像
  `rainpulse-cpu-worker:x-sxfusion-73a14f9-mb/-qc`（`.build/x-qc-v2-e549510/build-sx2`），
  镜像内已验证 `MAX_FUSION_SWEEPS=64` 且 xqc_v2 可导入；4×mb+1×qc healthy，融合峰值内存
  3.6–4.2GiB/6GiB。
- 网络访问：`10.15.12.105` 当晚失联（VPN 不再推 10.x 路由，跳板 192.168.18.105 亦不通）。
  同机双网卡地址 `192.168.28.105`（enp130s0，22/4173 均通）全程可用——后续排障先试它。
- 验证结果（admin ops `sx_composite`·`sx-quality-v2-z10`，2026-08-28 数据）：z9591+zf101(run
  5946a500, 00:12–00:30)、z9591+zf102/103/104/105(runs d3c8a04c/39cf2671/605eac9e/6d7fa978,
  00:12–00:24) 共 **10 个双站周期产品全部 SUCCEEDED**。zf101 00:24 合规弃权：z9591 该 S 卷
  (end 00:19:28) sweep0 366 径向仅 365 唯一方位角（69.51°×2）→ 融合几何门拒绝（X 质控路径
  容忍重复方位角，融合路径不容忍；不得放宽）。
- **系统级现状：`QUALIFIED_BAND_BITS` 处处为 0 = 校准门**。站配置 `calibration_verified=false`
  （六站全是），且输入卷 attrs 无 `calibration_id`（X 归一化卷无此键；S 质控卷 "unverified"），
  fusion_quality.py 逐门 admitted 需两者匹配。文档 §86 明言不自动补 ID、补真实定标资料是启用前
  操作员任务——**不可伪造核验标记**。故当前 v2 产品 = 仅不确定层（`CR_UNCERTAIN_DBZH`，产品自标
  `operational_eligible=false`、`qpe_eligible=false`，winner 层空、CR_DBZH 空、合成图层渲染为黑图，
  均为设计行为）。S 侧 sweep0 回执：170,853 观测门中 131,944 被 S 质控 CR_WITHHELD；X 可用度
  分站差异大（zf104 3.0–3.3k 格、zf101 ~0.75k、zf102 0.25–0.5k、zf103 ~25、zf105 0——该卷
  低层 OBSERVED_MASK 全零，无有效观测门）。
- A/B（同站 z9591+zf101 同周期，旧方法 `sx-fujian-full-test` run 2182580a vs v2）：
  旧 CR 覆盖 18.8k/18.9k 格，v2 不确定层 49.1k/52.6k 格（2.6–2.8×，为旧的超集，多出部分是
  被质控保留但显式标注不确定的格）；重叠 17.4k/18.0k 格数值一致性：中位差 0.0/−0.5 dB、
  均值 −0.95/−1.21 dB、|Δ|>5dB 占 16.5%/17.9%。
- 待办（真 winner 层启用前提）：拿到真实定标资料后在站配置登记 `calibration_verified=true`+
  `calibration_id` 并让解码/质控链路把同 ID 写进卷 attrs，重跑即可点亮分层胜出；Z–Φ 衰减订正
  仍未启用（无已核验系数，待真实证据）。

## 2026-09-30 交叉定标落地：v2 winner 层点亮（xcal-z9591-20260828-v1）

- 用户拍板"资料要不到，只有基数据；按气象常用做法做交叉定标"。实现三件套：
  1) **研究**（`.build/sx-quality-v2-20260930/crosscal-study-20260828.json`，提交 6446133）：S 质控卷 ×
     X qc-v2 native.npz 极坐标几何匹配（4/3 地球半径，|Δxy|≤1200m、|Δz|≤350m、|Δt|≤300s；
     X 侧判据= DBZH_RAW≥5 且未被 XQC 隔离/拒绝/噪声底标记——测值级天气回波，非产品准入）。
     结果：zf101 +4.5dB(N=56.0万)、zf102 +3.0(12.0万)、zf103 +8.5(1.9万)、zf104 +0.5(268.5万)，
     分强度 Bin 呈经典形态（低段 +14~+20 噪声底、高段 −9~−17 X 衰减）；**zf105 N=411 不足
     登记门槛，保持 calibration_verified=false（诚实）**。z9591 为参考锚（绝对定标基数据无法
     自证，锚语义已写入研究文件）。
  2) **代码**（8e1f58c，测试全过）：`adapters._station_registered_calibration`——站点经登记
     核验（calibration_verified=true）时，未声明自身 calibration_id 的卷继承站点身份；卷内显式
     身份永不覆盖（中途定标变化仍可检测）。
  3) **登记**：105 网络五站写入 calibration 字段（备份 `…json.before-xcal-20260930-115729.bak`，
     新 sha a3dff504…）；镜像 `x-sxfusion-8e1f58c-mb/-qc` 部署，worker healthy。
- **效果**（00:12–00:24 重跑，zf103/104/105 run a77706d4/6c496407/a8457726）：QUALIFIED 层从 0 →
  15,822/16,332 格/周期，CR 中位 28.5–29.0 dBZ，cr.png/winner_band.png 正常渲染（回波结构完整、
  全 S 胜出）。**X 仍 0 胜出**——非校准原因：XQC 的 CR 准入链（REFLECTIVITY_ELIGIBLE_FOR_CR/
  QUANTITATIVE_READY 全 0，PATH/RADOME 门）在当前周期不放行 X，其根因与 Z–Φ 系数未启用同源。
- 运维踩坑记录：①compose 链里并行会话删掉的 `rainpulse-optimized-b3-20260922-stage/
  optimized-images.yaml` 导致重建失败，已从 /tmp/compose-files.txt 剔除（备份同目录 .bak-*）；
  ②服务中断窗口内被认领的任务成孤儿（JetStream AckWait30s×10 耗尽，任务卡 RUNNING 无管理
  动作可重排——系统的真实缺口），zf101/zf102 两个 run 已 cancel，等完整控制服务恢复后重提；
  ③现 4173 实例是并行会话的 z9598 单雷达回放专用进程（无 RAINPULSE_MULTIBAND_CONFIG），
  sx_composite 计划暂不可提；AdminToken 在该进程环境（临时取用，不入库）。
- 待办交接：完整控制服务恢复后补 zf101/zf102 重跑（预期与三站一致）；X 胜出参与需 XQC CR
  准入链放行（依赖 Z–Φ/路径质量基础设施）；定标偏差值本轮只登记不订正（订正属算法改动）。

## 2026-09-30 Z–Φ 启用攻坚：配置链全通，止步于相位质量基础设施缺口

- 用户拍板启用 Z–Φ。完成链路：①系数研究 `.build/sx-quality-v2-20260930/xphi-study-20260828.json`
  （提交 12a444c）：z9591 锚径向段 PIA(φdp) 拟合，发现并修复 PIA 符号 bug（X_cal−Z_S=−PIA），
  逐径向 α 中位 0.258–0.473（N=5-78，IQR≈估计量级，数据包络括住文献 0.25–0.34 但精度不足）；
  ②登记决策：频段级锚定系数 **α=0.32/β=0.85**（同时落数据包络与文献窗，coefficient_id
  `xphi-9p4ghz-anchor-20260828-v1`，出处 sha=a4885b73…，决策记录含不确定度披露）；③网络五站
  x_qc.attenuation=zphi（备份 `…json.before-xphi-20260930-143911.bak`，新网络 sha 74bfcf66…）。
- 配套代码（均提交、测试全过、镜像已部）：`3537a21` 归一化卷 attenuation_status 缺省=raw（契约）；
  `d13f8dd` 首门 PIA 锚缺省=(verified,0.0dB)（契约）。镜像 `x-sxfusion-d13f8dd-mb/-qc`。
  **发布通道坑**：网络/代码变更改变 worker 指纹 → `ops_release_channels.multiband` 钉住的旧指纹
  拒绝一切计划（"没有新鲜且配置匹配的管理Worker"）——需 POST
  `/api/v1/admin/ops/releases/multiband/select` 重钉（fingerprint+expected_revision+reason）。
- 验证结果：任务 SUCCEEDED、锚已生效（evidence `attenuation_anchor_verified:true`），但
  **PIA/eligible 仍全 0**。逐门归因：`_phase_support` 要求输入侧 `PHASE_VALID_MASK`+`LIQUID_MASK`
  逐门掩码——**代码库无任何生产者**、归一化数据不携带（canonical 仅矩量）。这是上游"相位质量
  分类"基础设施（从 PHIDP/RHOHV/SNR 纹理逐门判定相位可用性/液态路径）的真正缺口，需算法开发，
  不能用配置缺省替代（伪造全 1 = 对垃圾相位做 unwrap，危险且违规）。
- 次级缺口（已知未解）：融合准入还需 radome evidence（天线罩状态，运行输入，基数据不可推导）。
- 当前状态安全：zphi 已配置但因无相位支撑而零订正（诚实弃权），产品行为与 none 等价
  （winner 层仍 S 驱动，X 值未被错误订正）。待相位分类器就绪后无需再动配置即可点亮。
- 交叉定标重跑补齐：控制服务恢复后 zf101/zf102 补验尚未做（与 zf103-105 预期一致）。

## 2026-09-30 调研：wradlib/Py-ART 对 Z–Φ 与相位质量分类的支持度（结论：可直接用，无需自写求解器）

- **关键事实：两个库已在 worker 镜像内**（wradlib 2.9.5 + Py-ART 2.2.5，xarray 2026.7）——S 波段
  开源质控链本来就把它们带进了镜像，零新增依赖。许可证 MIT / BSD-3，兼容。
- Z–Φ 求解器：wradlib `atten.specific_attenuation_zphi(phidp, dbz, alpha, b, rng)` 裸数组接口、
  系数显式传入（可直接挂我们的登记出处）；Py-ART `correct.calculate_attenuation_zphi`（Gu 2011，
  a_coef/beta/c/d 显式 + gatefilter + fzl）需 Radar 对象。另有 wradlib `pia_from_kdp`、
  约束法 `correct_attenuation_constrained`。
- 相位质量分类：两库都没有"开箱即用分类器"，但要件齐全——wradlib `dp.phidp_kdp_vulpiani`
  （去斑+展开+迭代 ΦDP/KDP 全流程）、`dp.unfold_phi`(Wang2009)、`system_phidp_*`（系统相位）、
  `util.texture/despeckle`、`dp.rhohv_noise_correction(rho,snr)`；Py-ART `phase_proc_lp`
  （Giangrande-Ryzhkov LP）、`filters.GateFilter`、`retrieve.hydroclass_semisupervised`
  （9 类水凝物→LIQUID_MASK 可由 LR/RP/RN 类导出）。
- 附加发现：wradlib `atten.correct_radome_attenuation_empirical(gateset, frequency, hydrophobicity)`
  可作为天线罩**证据生产者**（雨期近场经验估计），是 radome 门的一个诚实数据来源选项。
- 建议架构（后续立项）：自写薄适配层产出逐门 PHASE_VALID_MASK+LIQUID_MASK（wradlib 原语拼装，
  阈值走配置+登记），喂给既有 `_zphi`（保留我们的契约：无 S 参考、出处、逐门保护）；
  wradlib/Py-ART 的 ZPHI 作交叉验证对照，不替换主求解器（避免 Radar 对象转换与契约缺口）。


## 2026-09-30 ZF702 径向预算与接收机模式修复已发布

- 本地主线提交a5ed2bb/3027252/d60fbb8/c2918a1已合并main并push：源枚举去重、独立几何预算、异构诊断无损压缩、上传前身份校验、REF/SNR接收机模式分位一致。239项相关当前用例通过，混合模式与上传漂移回归均实跑红绿；旧8项参考不适配保留记录。
- 正常r4 task `7f00e623-f996-4a0c-868a-6d0ef74c38fb` / attempt `3a519e3b-1106-4b12-9a29-fa043e0a5830` SUCCEEDED08:20Z，artifact SHA `c29dc68fc441284dea55ec2de312e8c6f36f3e9239d6abff01c7dd75d869b709`。ZF702 scan3b3bf9da全部9层源模型完整、raw不变；主要cut0/2/4/5远距≥15dBZ残留门77079→1397、182551→1655、110031→1149、81840→1409。r3漏检的cut2约198.345°强径向2481→0，浏览器复核0/2/4/5原始/质控地图通过主要径向清除检查。
- cut2/9仍ACTION_BUDGET_ABSTAINED，界面保留未完成警告，不宣称全层或全天验收通过。未提高动作预算，不启用可信融合/QPE/预报。
- 105默认4个MB Worker已更新 `rainpulse-cpu-worker:xqc-mode-c2918a1-merged`，4/4ready，发布指纹 `b9480e0d5085612bb9bb841e41a221e0bff6e7b6e6d6becbb2ec15a15f03e392`。保留同期d13f8dd定标/衰减适配，17份算法SHA核对提交c2918a1。配置仍74bfcf66，S QC镜像/控制Web未重启。证据 `.build/xqc-zf702-investigation/published-r4.jsonl`、verified-ray-r4.json；105 `.build/xqc-budget-c2918a1/acceptance.json`、promotion.json。
- 下一步沿主线抽查其他站/时次/仰角与动作保护，不从单体扫推断全网干净。X全天/小时定时仍暂停；实际MinIO数据盘约116.9GiB可用（不是/data的NFS空闲），低于120GiB批次保护线，不能盲启动大批量。


## 2026-09-30 X 径向跨站时次与动作保护复核完成

- 已核验ZF702 r4 cut2/9的ACTION_BUDGET原因：拟排除328436/446929（73.49%）、24190/32814（73.72%）超过70%，源模型完整；预算门QC图有效可见数0，hard-weather重叠0，不能用提高上限消除警告。
- 当前MB默认发布通道已由同期相位分类任务更新为94f82b9镜像/指纹92060946…；核验source_blocks/core/radial_source/source_summary/evidence_tables与main一致，保留c2918a1径向修复，未回滚并行改动。
- 两份代表任务均完成：ZF701 scan07bdd490、task832c7cbc/attempt2e3cbe4a；ZF702 scan5418017f、task0a71654e/attemptef55b788。18层全部EVALUATED且源模型完整，无动作预算弃权；DBZH_RAW逐层与归一化对象相等、原生方位距离一致、SHA与提交标记核验。
- 实图复核ZF701 08:03:32/3.36°、ZF702 08:33:05/0.54°强扇形条带消失，近场蓝色及独立西南绿色回波保留。不能据此声明独立真值误删率0或全网验收完成。
- 证据及后续入口：docs/XQC_SOURCE_BUDGET_FIX_20260930.md，`.build/xqc-zf702-investigation/*-verified.jsonl`，105 `.build/xqc-budget-c2918a1/representative/`。下一步增加高污染与真实降水对照样本，复核70%保护所涉及的大面积候选；不盲调上限，不恢复小时定时/全天大批次。

## 2026-09-30 Z–Φ 相位质量分类器链（wradlib 优先）落地回执

- 结论：XQC 相位基础设施四件套已合入 main 并全测试通过（`tests/sx_path_20260929/ tests/xqc_v2_20260928/` → 176 passed, 1 skipped）：
  - `94f82b9` phase_quality.py 分类器：PHASE_VALID_MASK / LIQUID_MASK / 雨段锚（PATH_ANCHOR_*）合成，hook 在 quality.py x_qc 逐层 correct_sweep 之前，仅 X 波段 zphi/phidp_linear 启用；证据方法说明真实 sha256 固化于 ANCHOR_EVIDENCE_SHA256。
  - `699ba43` 雨段锚：归一化体数据无回波区为全 NaN、逐径向数据从首个回波门开始，OBSERVED_MASK=isfinite(DBZH)；锚在雨段首个相位支撑门（e≤k≤e+tol，tol=max(3,500m/dr)），解决 INITIAL_LOSS_UNKNOWN 全线失效。
  - `024c04e` Vulpiani 升级：phidp_kdp_vulpiani 重建 PHIDP 桥接纹理/噪声孔洞，粗差下限 RhoHV(噪校)≥0.90、SNR≥4dB 取代严格纹理门；锚/液态判据/证据契约不变。本地 venv 与 worker 镜像均 wradlib 2.9.5。
- 真实数据测量（zf101 00:18–00:19 体 sweep_000，105 离线 docker-run，镜像 x-sxfusion-vulp-mb）：锚 46 行、证据 sha 就位、支撑门 10187；锚处有支撑 23 行；满足 ≥1km 连续段仅 2 行（row46 anchor@696 run=45门/1350m、row47 anchor@694 run=49门/1470m）。e2e x_qc 仍 eligible=0 / pia_finite=0。
- 判定：链路各环节已点亮，剩余是求解器级连续支撑稀缺——下一步是对 minimum_delta_phase_deg(3°)/endpoint_noise(3°)/minimum_segment_m(1000)/RhoHV/SNR 下限做系统性阈值标定研究（对照 wradlib specific_attenuation_zphi 交叉验证），而不是继续单点盲调；两个候选拒绝原因为 Δφdp<3°（1.35km 轻雨）或端点噪声>3°。
- 独立遗留：融合准入还差 radome 证据门（RADOME_UNVERIFIED）；候选诚实证据生产者为 wradlib correct_radome_attenuation_empirical，未实施。
- 105 网络注册态：x_qc.attenuation=zphi、α=0.32/β=0.85（coefficient_id xphi-9p4ghz-anchor-20260828-v1）已注册；回滚备份 `.build/sx-priority1/.before-xcal-20260930-115729.bak`、`.before-xphi-20260930-143911.bak`。

## 2026-09-30 X 径向质控同期 S 冲突筛查

- 已完成 ZF701 UTC00:03:32 / ZF702 UTC00:33:05 两体扫18仰角只读筛查，6份同期S输入经正常组合预检冻结，未提交组合重算、未切换worker、未恢复定时或全天任务。
- S可用反射率≥20dBZ、RHO≥.95、可信掩码；X原始≥15dBZ且≥15km。水平≤1km、逐径向时间差≤180秒共1859个已排除X门有S候选匹配。再要求“各自雷达以上高度差≤1km”剩63个，均ZF701；它不是共同MSL匹配、不是误删率，不能宣布误删验收通过。
- 优先复核ZF701仰角0/4/5/6/8的1/8/6/45/3门；既有RADIAL_SOURCE也有NOISE_FLOOR原因。代表仰角0 14.46°/68.889km X21.5dBZ、S24.5dBZ，可能降水与干扰共存。下一关口是共同MSL/几何、接收功率与极化逐门证据，不能盲提70%预算或按固定方位清除。
- 证据文档 docs/XQC_S_CONTROL_SCREEN_20260930.md；本地.build/xqc-zf702-investigation/scontrol-screen-final.jsonl共24记录，执行退出0。MinIO空闲122346885120字节/100641076inode，仍低于全天120GiB保护线。

## 2026-09-30 X/S 冲突逐门复核续验

- 原63门全部完成原生SNR/RHO/ZDR/PHI和邻域掩码复核：39门NOISE_FLOOR且SNR<3dB，24门RADIAL_SOURCE；独立保护掩码全0。源模型排序还原与压缩完整性核对，没有新运行故障证据，不盲恢复回波或改阈值。
- 现有头部候选天线高度ZF701429m/Z95981740m差1311m，基准仍未核验。使用候选高度、真实垂直波束和4/3地球地面弧长重新筛全部18层，得到47个候选重合门（29噪声/18源），不是原63的确定子集或误删真值。重点18源门是ZF701cut5 ray39五门/cut6 ray81十三门。
- ray81十三门对照下一体扫UTC00:07:21完成，原生同8.96°、方位偏差0.12°；原DBZH29–33/SNR13.5–16/ZDR−5.25至−2.75，下一时次10门有REF26–28/SNR9–12.5。持续接收功率和异常极化不能排除降水叠加，3门缺测不等于无雨。
- docs/XQC_S_CONTROL_SCREEN_20260930.md含续验；.build/xqc-zf702-investigation/scontrol-{gates,63-signature,source-models,candidate-height,next-raw}.jsonl全部最终执行退出0。无算法/部署/预算/定时修改。下一关口：18个源与降水共存候选的独立几何及可信降水证据，不能宣布全网漏检误删验收通过。

## 2026-09-30 Z–Φ 阈值标定研究回执（链路修复 + 全层量化）

- 主链修复三连（全部已提交、177 测试通过）：`4d2d653` 管线入口合成缺失掩膜（prepare_phase 此前把全零占位当"已声明"，baseline 分类器永不运行于增强站点）；`37a77d5` 分类器写出 `PHIDP_RECON`（Vulpiani 重建，证据 sha ccc7b292…）且 `_zphi` 仅凭证据消费它，原始 PHIDP 仍是测量记录；`0562e59` 把合成后的 cut 传入 prepare_phase（此前副本被 active 分支丢弃）。
- 量化结果（zf101 00:18–00:19 体，sweep 0/5/10/20，镜像 0562e59）：分类器 14334 支撑门/46 锚行；prepare_phase 网络阈值收紧至 3056、放宽（0.90/4/7.5）后 10187；求解器注册档（Δφ≥3°/噪声≤3°/段≥1km）0 行——锚段累积相位中位数仅 1.07°；放宽档（1.5°/5°/600m）1 行/层（sweep 20：PIA 1.13 dB，24 门）。交叉验证：与纯相位积分 MAPE 5e-8、与 KDP 链 4.1%；wradlib.atten.specific_attenuation_zphi 在唯一段上返回 NaN 待查（注意：该 API 在 wradlib.atten 不在 wradlib.dp）。
- 生产路径：链路已全开（xqc_phase 4881–15534/层）但 PIA 仍 0。最终归因：XQC 候选扣留 8582 门（回波 33%）经距离 cummax 扩张为 85543 个 ATTENUATION_UNRELIABLE 门，斩断全部锚段；同一放宽求解器配置绕过候选层即可出 PIA——这是"可疑测量后不发明无衰减重启"的诚实契约，不是缺陷。
- 决策建议（研究 JSON：`.build/sx-zphi-tuning-20260930/zphi-tuning-study-20260930.json`，含 4 份原始矩阵）：A 先降 zf101 候选覆盖率（候选核质量线），B 再注册放宽阈值（zphi Δφ≥1.5°/噪声≤5°/段≥600m + enhancement 0.90/4/7.5），C 放松污染语义不可取（违反不得伪造契约）。网络注册维持不动，直至 A 落地。RADOME_UNVERIFIED 仍独立阻断 CR 准入。
- 镜像：105 上 `rainpulse-cpu-worker:x-sxfusion-0562e59-mb`（离线验证用，未部署到编排）。注意镜像 ENTRYPOINT 是 worker，脚本验证须 `--entrypoint python`。

## 2026-09-30 X 上层天气上下文选层缺陷已修复部署

- 实测内存context_for_cut按绝对仰角差选下层占满2供体，evaluate_context只能接受正上层高度差，且与流式GroupContextProvider行为不同。共享upper_delta，只选实际仰角差>0.2°上层，不改天气阈值/动作预算。两回归实跑红后绿，相关XQC+context 131passed。
- 代码53beaba先push再105部署，镜像xqc-upper-53beaba-mb继承当前94f82b9，仅limited_context一模块改变；80模块SHA比较通过，容器文件SHA对提交一致。并行相位后续未部署提交未被覆盖。
- 冻结ZF701 RAW新镜像审计退出0：cut5供体6/7匹配668门、cut6供体7/8匹配71门，旧路径均0。18门目标天气初筛只有1门满足；全18无已验证上层天气支持，缺波束契约不能当无雨。
- 默认4MB Worker闲置核验后切换，4/4ready/healthy，fingerprint ca853767299e4a9f9d87dd0b024f807d9cb7e8fc52ff60c4aae5715a41a966c6，网络74bfcf66未变，context仍disabled。S Worker/阈值/预算/定时未改；无雷达产品/UI变化。
- docs/XQC_S_CONTROL_SCREEN_20260930.md续验；105.build/xqc-upper-context-20260930/promotion.json，本地upper-image-real.jsonl/upper-hashes.txt/upper-tests.txt。下一关口为可核验波束证据+冻结样本mixed_review验证，仍不能宣布18门无降水或全天全网完成。

## 2026-10-01 Z–Φ 事件级验证回执（原"强降水个例"计划的诚实替代）

- 归档盘点结论：实验重建归档只覆盖 2026-08-28 00:00–00:56 UTC 单一降雨事件（zf101×10、zf505×10、其余站×1；无 rebuilds 的 scan 只有 DBZH 无矩量，Z–Φ 不可用）。**不存在强降水个例**，改对全部 10 个 zf101 体（事件全程，回波 9.8k→157k 门）做全电池验证。
- 结果（sweeps 0/10/20 × 4 臂，镜像 0562e59）：solver 注册档（Δφ≥3°/噪声≤3°/段≥1km）在 8/30 个 case-sweep 出 PIA——产量随事件强度增长，最强时刻 00:54 sweep20 达 5 行/210 门（中位 2.0、最大 3.7 dB），00:36 sweep10 单段 PIA 4.9 dB；放宽档仅增加 ~1 dB 边际段。生产路径（含候选层）全臂 PIA=0，即使相位有效门高达 77k。交叉验证 n=33：与纯相位积分 MAPE 1.5e-8；wradlib specific_attenuation_zphi 全部 33 段返回 NaN（开放问题）。
- 决策更新：**B（注册放宽阈值）被证据否决**——注册阈值在本事件强单体已出 4.9 dB 订正，放宽只买边际段还松质量门；**A（候选覆盖率）确认为唯一生产阻断**（solver 与生产差异完全来自候选+污染累积层）；eligible=0 全程成立，radome 证据仍是 CR 准入独立硬门槛。
- 证据：`.build/sx-zphi-tuning-20260930/eventwide-addendum-20261001.json` + heavy-*/inventory-* 产物。下一步唯一优先：A（与对端协调候选核），radome 证据生产者次之。

## 2026-10-01 Z–Φ 生产阻断 2×2 反事实闭合（A 证据交付）

- 方法：三个体（00:18/00:36/00:54）× sweeps 0/10/20，注册档求解器选项不变，变换两个轴——增强层相位阈值（网络 0.95/10/5 vs 对齐求解器契约 0.90/4/7.5）× 污染（仅确认 vs 现行候选 cummax）。
- 矩阵结果：网络阈值+无污染=0；对齐阈值+无污染=**出产量**（34d8a880 s10 单段 4.94 dB、50e028fe s20 5 行/中位 2.03/最大 3.68 dB）；对齐阈值+现行污染=0。两个独立且各自充分的阻断确认：**增强层双重门**（比 zphi 系数自身有效期 0.90 还严的 0.95，支撑 10187→3056 碎片化）与**候选污染累积**。
- 污染构成：zf101 上唯一污染源是 v2 evaluate_cut 分类器的 proposals（8.6k–42.7k 门/层，回波 9–32%）；budget=0、prior_candidate=0（DBZH 非气象核零命中）、原始 cut 无 CONFIRMED_NONMET。proposals 落在弱回波（中位比未标记低 4–8 dB）。
- 注册决策更新：增强层阈值对齐（0.95→0.90、10→4、5→7.5）有注册依据（与系数有效期一致性，非任意放宽），但因 vB 证明其单独不解锁（污染仍斩段），**推迟到候选覆盖工作落地后一并注册**；求解器 zphi 阈值维持注册值（B 维持否决）。
- 证据：`.build/sx-zphi-tuning-20260930/blocker-matrix-20261001.json` + contam*.json。候选核证据已可交付对端（唯一污染源、逐层计数、DBZ 构成、解锁矩阵）。下一优先：radome 证据生产者（eligible=0 的独立硬门槛）。

## 2026-10-01 radome 证据生产者落地回执（X 准入链第二硬门槛打通）

- 实现（`0a3bba7`，183 测试全绿）：`radome_quality.py` — wradlib Merceret-2000 经验湿雷达罩双向损耗估计，输入=每径向近雷达（≤10km）最大有限 DBZH 门（保守统计），标准材料 0.165，站频段 8–12GHz 才评估；`verified_negligible` 仅当全部被测径向 ≤1.0 dB 且被测径向 ≥30 条；证据 sha 为方法声明（含 C/S 波段拟合外推至 9.4GHz 的明示）的真实 sha256。拒绝粘性 + 后层更湿则降级撤销先前 negligible 声明；无近雷达测量的径向保持 RADOME_UNVERIFIED；已有声明永不覆盖。接线：baseline x_qc 循环 + v2 管线入口（与相位分类器同位）。
- 真实数据验证（10 体 × sweeps 0/10/20，镜像 0a3bba7-mb，离线）：**7/10 体 verified_negligible**（worst 0.0–0.003 dB），RADOME_QUALIFIED_MASK 点亮最高 42.5 万门/层，RADOME_UNVERIFIED 从全回波降至 ~2k–50k（未测径向）；4/10 体诚实弃权（measured=0，全部径向近雷达无回波）。无一体被误判 admitted。
- 至此 X 准入链状态：定标✓（交叉定标注册）、radome✓（本生产者，按体自证）、路径有效性✓基础设施但生产产量被两阻断（增强层双重门 + 候选污染，见 blocker-matrix-20261001.json）。eligible>0 的最后一步仍系于那两阻断的解决。
- 证据：`.build/sx-zphi-tuning-20260930/radome-check-20261001.json`；镜像 `rainpulse-cpu-worker:x-sxfusion-0a3bba7-mb`（105，未部署编排）。

## 2026-10-01 X 原生波束证据、混合回波复核已部署验收

- 56796d4 修复普通 RSTM type-1 归一化丢失已核对的站点垂直波束宽度，契约绑定原始 SHA/config/version；缺失/无效或相控阵不补猜值。红测1失败后相关149测试通过，main先push后105部署，2解码Worker健康且模块SHA对提交一致。
- 5份代表体扫（ZF701 08:03/08:07/08:49；ZF702 08:26原问题/08:33）正常重建及质控均SUCCEEDED，45个REF层源模型完整；924个原始矩阵/几何数组与旧归一化对象逐数组相同。ZF702原问题2/9层仍按70%动作保护扣留328436/24190门、质控可见0，不能把预算弃权当确认清除或恢复组合。
- 5af913d 冻结通用 native-beam mixed_review 策略；4个闲置MB Worker正常重建后CAS选择，22实验增强站应用，网络bee28feb…，指纹79faf337…，镜像仍xqc-upper-53beaba-mb，保留当前已部署相位模块，不覆盖同期未部署研究。ZF703/ZF801/S/可信资格/预算/定时不变。
- 线上正常任务1569935c/asset a2033863…已SUCCEEDED且九层逐门验收PASSED：1092混合门action=3、非确认拒绝、不恢复质控显示或组合；RAW及整层CR准入与前版相等，流式/离线结果一致。先前18个S重合重点源门仍无验证上层支持，不等于无降水。
- 实浏览器ZF70108:49及ZF702原问题0.54°新图径向/扇形消失；未固定result入口已自动选新结果。历史result链接保留历史产品。证据docs/XQC_NATIVE_BEAM_CONTEXT_20261001.md，105.build/xqc-upper-context-20260930/beam及本地.build/xqc-zf702-investigation/beam-receipts。实际MinIO空闲122080387072字节，低于120GiB全天保护线，未恢复全天/小时任务。
- 下一主线：高污染层与独立降水对照、其他站/时次回归及完整全天验收；缺原生等价波束的相控阵保持未知。不宣称全天全网全部完成，不启用候选可信融合/QPE/预报。

## 2026-10-01 S 断续径向优化本地实现与只读回放

- 修复独立形态资格被全局 SNR≥20/完整偏振要求否决：接收机来源路径条件保留，形态动作单独存 RV2_GEOMETRY_ACTION_MASK，原因/弱状态与序列化校验完整。新增 opt-in discontinuous_tracks_enabled（≥4片、8km实测支撑、80km跨度、物理宽度及双侧约束），原始缺测不补值，天气/冲突/几何屏障有效，无递归扩张。
- 123 项相关用例通过。精确父配置子档生成器 scripts/make_discontinuous_radial_profile.py 只新增开关及版本后缀，保留背景资产与其他阈值，尚未启用生产或部署。
- 105 进程内只读候选回放：Z9591 10:24/1.51°西北抽查139可见门新增隔离提议33；Z9598 08:42/0.5°西南4795门新增4014。来自形态弱状态修复，新断续链这两例额外命中0；保护/缺测动作均0。不能声明全部短碎片已解决或独立天气误删率0。无Web产品更新，无后台重算。
- 入口 docs/S_RADIAL_DISCONTINUOUS_20261001.md；回执及模块SHA .build/s-discontinuous-20261001/replay-receipt.json。下一步部署需以105实际S父档生成子档并完整QC→出图验收，保持X/定标/衰减并行任务不变。

## 2026-10-01 S 原始射线范围追踪与严格无锚判别（本地 v2）

- 第3点：source_envelope_enabled 冻结原始独立种子及 RAW 对象范围/边界/父 ID，支持弱尾段及相邻原生射线单跳；尾段距原始种子≤120km，范围仅原对象±10km，不以新尾段递归扩张。可追溯字段和序列化校验已接入。
- 第4点：新断续无锚路径分离几何候选与动作，要求双侧实测≥6dB差、20/60km窗口双侧有效支持≥75%、边界稳定；缺测不能当零回波。接入既有邻站/多仰角天气可用状态及正证据保护；旧长射线判别未整体替换。
- 176项相关用例通过，git diff --check通过。105只读来源阶段回放：Z9591 10:24/1.51°抽查139→新增提议33，Z9598 08:42/0.5°4795→4014；v2原对象追踪这两例额外可见命中0，后者71个无锚几何候选因实测不足保留。保护/缺测动作0、RAW不变，无独立天气真值。
- 文档 docs/S_RADIAL_DISCONTINUOUS_20261001.md；最终回执 .build/s-discontinuous-20261001/envelope-v2-replay.json。未提交/部署/发布产品或后台重算，Web未更新。生产启用仍须基于实际S父配置并完整QC→出图验证。

## 2026-10-01 S 断续径向复核出图

- 保留 scripts/plot_s_radial_review.py：指定日期/站/时次/层/局部范围，只读105 Worker进程内回放，保存NPZ+SHA+实际体扫时刻+配置，render-only可离线重画。不改服务/生产产品。
- 出图 .build/s-discontinuous-20261001/visual-review-final：Z9591 10:24/1.51°全层新增提议45，西北小条改善有限；Z9598 08:42/0.5°全层9702，西南明显减少但北东南有残留。单独第3/4点额外可见命中0。校正：71是无锚几何候选，不是最终保留数；68被其他规则隔离，最终保留3个，双窗口均不足。只复核两例，未完整Worker/组合验证或Web发布。

## 2026-10-01 用户授权清理未使用模型，X 全天容量阻塞解除

- 用户明确授权 `/home/yons/hwapp/dis` 下未使用资源可删除；已核对模型进程、容器挂载、systemd/启动脚本引用、软链接和可读文件映射。当前 Qwen36 服务使用系统盘 `/home/yons/hwapp/Qwen3.6-35B-A3B-GGUF`；TURBO 文件仍有 serve.sh 引用，保留。
- 删除无发现引用的 `dis/Qwen3.6-27B-GGUF`、`Qwen3.6-27B-NVFP4-MTP-GGUF.gguf`、`Qwen3.6-35B-A3B-NVFP4-MTP-HQ.gguf`；精确文件清单、inode/大小和删除前后空间记录保留105部署目录 `.build/xqc-acceptance-20261001/storage-cleanup-unused-models.json`，状态COMPLETE。未删除MinIO/RainPulse/雷达资料；其他模型保留。
- 释放464131375104字节（432.25GiB），数据盘可用586090844160字节（545.84GiB）。仅解除容量阻塞，不表示全天质控/图件验收通过。下一步继续固定验收清单的通用修复、完整回放与正常生命周期发布。

## 2026-10-01 S径向残留进一步研究

- 8个只读诊断快照，7例目标扇区有残留；09:48最近前序体扫与截图对象位置不一致，不能记为解决。出图脚本新增diagnostics/精确scan-id，保留audit_s_radial_residuals.py离线原因复核。
- Z9591 10:24剩余106门均未进入候选，62有RAW窄走廊却无锚/提名；7例目标剩余均不落在合格原始范围对象。扩大所有RAW提名反证仍不命中目标残留。需短碎片家族原始对象+独立联合判别，不能单纯放宽删门阈值。
- 两站低层REF460km与Doppler230km分层、时间/方位不同；Z9591≥250km残留超出VR/SW覆盖。近处仅可作真实位置受限上下文，远距需其他证据。MIT ATC-454 S波段组合方案作为研究参考，未照搬阈值或实现。
- 方案docs/S_RADIAL_RESIDUAL_RESEARCH_20261001.md；证据.build/s-discontinuous-20261001/research-diagnostics及research-moment-inventory-v2。只改诊断工具，未改算法/部署/生产产品。无独立天气真值，不能保证全部清除且零误删。

## 2026-10-01 X 全量原生复核在途（八路）

- pilot-v3-bound实际结束247层机械通过、2层预算FAIL（ZF702原问题2/9）；raw-smoke-v2四份原生体扫160REF层全部机械通过。诊断budget-source-probe.jsonl证明源模型完整，fan为主，仍不能绕过70%大面积动作保护。
- main最新8381d1b，审计逻辑2c4c3f8已push后部署；4测试通过。精确父网络bee28feb不变，离线原生镜像xqc-native-acceptance-c5537fe只补已接受56796d4 writer，不切生产MB。所有decoder YAML字节SHA冻结。
- 全量raw-full-v1四路实际每路2GiB/一CPU、主机可用49GiB；为加速明确停止该自有回放，保留旧回执及superseded-for-parallelism.json，不因超时重启。raw-full-v2-eight driver PID1754097，105部署.build/xqc-acceptance-20261001/raw-full-v2-eight，8个rainpulse-xqc-raw-5174e300-0..7、各1CPU/4GiB；完整5466原始SHA分片互斥，每个文件全部REF层，只有审计小记录，不写产品。以最终v2唯一文件/层回执验收，不累加旧部分计数。
- 待复核8路实际句柄/失败矩阵/原生日期与6分钟桶、预算两层和天气保护；新网络候选SHA398ccfb1…只17站×3开关+release_id共52变化，本地source-activation-v2，未部署。完整新产品及UI仍未完成。容量已解除（约546GiB空闲），S/定时/可信融合/QPE/预报未改。

## 2026-10-01 S 原始短碎片家族 A 步本地落地

- 新增raw_families.py与raw_fragment_families_enabled，保留<1km/单门片段，原始首片固定边界单跳相邻径向，范围≤180km/缺口≤60km；长连续片分段并保留原始长度。实测双窗口/ID/物理宽度/弃权原因/波束代理字段可序列化。诊断提名不进入旧来源拟合或动作，独立证据未接入前不删除。
- 新增11测试，相关130passed；8个固定旧诊断快照最终回放全部非预算弃权、缺测/保护候选0、动作/补门0。Z9591 10:24剩余106门提名62，11:24 185提名169；Z9598 08:36 1406提名174，08:42 781提名64，宽扇区仍待B模型。09:48仍非截图精确输入。
- 保留scripts/replay_s_raw_families.py，出图快照入口支持--raw-families。最终证据.build/s-discontinuous-20261001/raw-family-a-final；方案文档A回执已追加。尚未提交/部署/产品发布；目标active，下一步B独立联合判别及冻结来源关联，随后C有条件清理和完整QC→图/Web验收，不得把A诊断覆盖当完成。

## 2026-10-01 S B 步窄家族联合判别初版

- 新增family_joint.py/开关，原来源拟合先于新资格且候选输入不变，step3身份在合并后生成。窄RAW家族原始种子≥10km/稳定边界/距原种子≤120km可隔离弱尾；无锚要求8km/80km/长宽比12/双20&60km实测窗口+既有可靠偏振票。相位圆统计/rho纹理只诊断，不造缺测票/新来源/递归扩张。新增8测试，相关138passed，父ID/原种子支撑距离/偏振票可序列化复验。
- 八快照7红框扇区新增隔离均0；10:18/10:42全层新增3/2（扇区外），其他全层0。105进程内完整来源阶段两例回放通过、RAW/保护不变，10:24/08:42全层仍45/9702，与前版一致。证据family-joint-b-final及family-joint-transport-probe（Z9591）/family-joint-transport-z9598-probe（Z9598），脚本--joint/--family-joint保留。
- B尚未全部完成：需完整原始来源/宽扇区模型（当前来源受窄RAW家族截断）、可用邻站/仰角/多普勒或过去确认来源上下文；随后C有条件清理、完整QC→图/Web验收。不部署无目标改善的初版，不把候选/通过合成测试当解决红框。目标继续active，无新生产发布。

## 2026-10-01 X 重复方位角正常发布入口修复

- main05db916已push后105仅替换实际53beaba父镜像的stream_managed一行：X单站显式native_polar_qc=True，严格S/融合校验保持。完整Executor回归先红后绿，原RAW/重复方位角/射线时间保留，33相关测试通过；旧reference兼容3失败不算通过。
- 镜像xqc-duplicate-05db916-mb，4Worker ready，网络bee28feb未变，新指纹10700d99e3c9f52bef35f7e1d77c6722a7679f68e8a7f6a46a78123068b6e768。正常旧FAILED022987e3…保留；相同源身份新task19f77464-731d-4544-941e-2e72c1bf027a/run5720fa32-2adc-4e48-a720-c638845cf1c3 SUCCEEDED。
- ZF70208:08:25第六体扫9REF层全部数值/证据SHA/4PNG核验，第5层1重复方位角保留，RAW/几何/原生时间逐值一致，withheld-visible/admitted/hard-weather-rejected=0。证据105 .build/xqc-acceptance-20261001/zf702-0808-duplicate-verified.jsonl。浏览器自动化两次超时，真实UI未验收；内部不变量非独立天气误删率。
- raw-summary-03：254有回执/246机械文件完成/5220待完成，当前已完成无FAIL。raw-full-v2-eight原driver1754097与8容器继续；不得因观察超时重启，仍用现有冻结SHA和唯一码汇总。两层预算问题、独立天气、全日产品/UI、17站源开关启用仍未完成；第一优先未闭合，不进入S+X量化。

- B末次传输故障已定位为大诊断数据同一流丢块现象；工具改为按体分别取流+3072字符包+字节/SHA校验/原子保存。单体两例完整992/2743包通过；失败family-joint-b-live-final*不算成功。完整源邻域只读probe发现10:24有17、11:24有75个窄残留在原始源120km内且直连无屏障，仍需完整父对象判别，不能直接删。

## 2026-10-01 S B 完整原始来源账本本地落地

- source_ledger.py/默认关闭开关保留全部原始独立来源，不受窄RAW家族180km截断；来源类型/支撑/边界/原RAW范围/父ID可复验，单跳120km，三源争用永久歧义，不产生动作。新增8测试、相关146passed；旧拟合/候选/动作逐字段一致，writer保存校验通过。
- 最终八快照complete-source-ledger-final七目标扇区新几何关联仍0，全层10:18/10:42仅5/1残留获诊断关联；RAW/保护/缺测不变、动作/填门0。105内实际两例完整source阶段complete-source-ledger-live-v1抓包+出图通过，仍45/9702，与前版一致，非Web发布；latest-validation补充保留原运行SHA。
- 10:24两个原始源实际支撑14.75/11km，拒绝关联是窄边界不足而非长度；11:24多数源缺窄边界或不稳。下一步需距离窗口可变宽边界和完整宽扇区父对象（当前只原始射线来源ID），不能整范围清空。--ledger/--source-ledger复核入口保留，未部署/重启/生产重算；目标继续active，完整B/C/全链验收仍未完成。

## 2026-10-01 X 固定验收继续收口

- main e14758d已push并105发布静态Web（旧assets保留、index原子切换，无服务重启）；统一QC目录跟随全部next_cursor，按scan_id/radar_id去重，坏身份/循环/后页失败不返回部分日。第二页精确scan链接先红后绿，17相关测试/TS/ESLint/Vite通过，105实际chunk含分页保护。当前ZF70170/ZF70268无下一页，此修复不是它们径向残留的解释。真实UI仍未验收（CUA三次通道超时）。
- 同指纹10700d99五批后台driver2217431，07:15Z四个新增必测体扫各9层数值/PNG审核通过，加第六重复方位角例共5体扫45层；原问题taskbc376635…仍RUNNING，正常旧失败保留。全量原生raw-full-v2-eight driver1754097/8容器持续：07:15:51Z493解码、486文件完成、19532层机械通过，未完成5466全量。不得重新启动现有句柄。
- 0fa932b修复稀疏父配置生成器补默认导致漂移，3红→绿及17相关测试通过；精确bee父复现候选398ccfb1共52差异，候选网络未启用。17站源开关门控、2层动作预算和气象误删/真实UI未完成，不能进入S+X量化或提高预算冒充通过。
- 独立SURF控制探查脚本已提交部署（6测试）；同日文件base64完整ZIP/CSV可核验，但00时60分钟2516站与RAIN_SUM第一列全部不一致（同单位/前60分钟仅未证实假设），福建坐标和时间/列语义缺失，不能当真值。已异步询问用户实测/测试、福建字典及字段说明，未答。receipt precip-control-hour00.json状态UNVERIFIED_CONTROL_CONTRACT；不推断伪造/单位、不用它调参。

## 2026-10-01 X 六体扫正常发布已全部结束

- 原taskbc376635…于07:29Z正常SUCCEEDED，observer2387501正常结束NORMAL_PUBLICATION_AUDITED。全部六必测54REF层当前指纹10700d99有新产品，52机械通过、2层(原ZF7022/9)CUT_ACTION_BUDGET_ABSTAINED；source_complete/RAW几何时间/SHA/4PNG均核验。两失败层withheld-visible/admitted/hard-weather-rejected均0，实际QC>=15dBZ门2680/28，但扣留不等于确认污染清除，不改预算或改失败状态。回执zf702-original-verified.jsonl SHA5e0397769a3fe4d01336bb6ffef6dfc593b72ea481351522f381f8b100dafa72。
- 本轮CUA短暂恢复，六例中的ZF70208:08第5/10层全页截图与9层DOM选择、ZF70108:07第0层默认最新版本+全页双图已复核；后续循环/再次getTab超时，未验证层不算UI成功。本地ui-observations-20261001.json保留观察记录，截图在本任务工具输出。完整界面与气象误删仍未完成。
- raw-summary-04冻结5466文件，545有回执/539机械完成/4927待完成，已完成FAIL0/身份错误0；8路driver1754097实际仍live。进一步完成全日、预算2层与独立天气/全部UI；不进入第二优先。

## 2026-10-01 X 预算层上下文复核

- 对新正常taskbc376635…读取不可变产品/输入完整SHA，新增只读product-audit-context.py。105 current-budget-context.jsonl保留9层；第2层上下文reader=verified_groupcuts/EVALUATED、21000测量、842已验证天气冲突，两上层4/6实际60,133,128字节；第9层上层10实际24,751,968字节、77测量/0天气冲突。线上与有界回放入口均绑定GroupContextProvider，排除“上下层上下文丢失”假设。
- 旧budget-source-probe输入asset df19897…并非新原生波束身份d014707…，旧context=0不能代表本轮产品；新物理几何上下文已验证。源门总数不变：第2层fan320882/union321183，额外内部关联6204；第9层fan23747/union23766，关联360。去掉关联也不足把第2层拟动作降到70%，未发现能解释整体73.49/73.72%的意外扩大范围；不凭内部拟合断言误删为0。
- 8路实际1CPU/约2GiB，主机available37GiB/load11.26；driver1754097继续live。07:32Z566文件完成/22815层机械通过，非全天最终值；未改变分片/重启或启用17站候选。CUA再次恢复后仰角三步循环仍30秒超时，不算新增UI通过。地面雨量字段语义/福建站点资料仍待用户信息，Go源码搜索无RAIN_ONEMINUTE/QC_RAIN_SUM来源实现，未猜单位/累积窗。

## 2026-10-01 X 体扫切换与历史结果身份修复

- main cce68e2已push后105发布静态Web：固定历史result显示版本提示并可显式切到最新；当前与保留产品同时核验radar_id/scan_id/result_id，避免同站切体扫时沿用上一图件/预算状态。两项集成回归未修代码实际红、修复绿，22相关测试/TS/限定ESLint/Vite通过。旧assets保留、index原子切换，无服务或Worker重启。
- 105实际HTTP index SHA4aa040cb827ca34ac1a21ea7bb345075f052ef8390da090ded3abfe9a8f04852，RadarQCWorkspace-gzNku5Bt.js SHA763c97e5927916bdd44500b5d6199a4c5a4be1ea10a462de9f5b5318eadeb03b，与本地构建一致；rainpulse.service active。CUA连接连续超时，视觉验收仍未完成，不能以静态字节/React测试代替浏览器验收。
- 原问题公开API新resultbc376635….24a157aa…第2/9层确实返回ACTION_BUDGET_ABSTAINED/quarantine，其余EVALUATED。两层预算/独立天气、全日产品/完整UI仍未闭合；17站候选网络未切换。8路driver1754097仍live。raw-summary-05：676机械完成/4790待，ZF101238与ZF102244全部冻结文件已处理，起报UTC桶237/234，缺口和北京时间跨日尾段单列，不填零或宣称全日对齐完成。

- 补充只读地面对照查找：Weather深度4/hwapp-ruiyun-bdp深度5未发现福建站点字典；SURF_ATMO_STAT/StationInfo_river_key_jy完整base64/ZIP CRC通过、39站、福建候选区域内0站，不能用来定位福建雨量。SURF_CHN_MUL_MIN_SOURCE仅5字节test文件，目标日PARQUET目录不存在。新证据surface-control-discovery-02.json本地build保存，RAIN_SUM字段/时间单位仍未证实，原用户资料问题仍待答，不拿它作真值。
- raw-summary-06逐SHA关联800有回执、792机械完成、4674待，已完成FAIL0/身份错误0；driver1754097于02:35elapsed仍实际live。新增UI版本已发布但CUA getState再次30秒超时，未算视觉通过，未重启现有计算或改变候选网络。


## 真实界面两体扫全仰角复核（2026-10-01）

cce68e2静态Web已在真实浏览器加载。ZF70108:07:21九个原生REF仰角0–8全部完成双地图截图，主要长径向与南侧扇形主体在QC图不再显示，近站弱回波保留。ZF702原问题08:26:55九个原生REF仰角0/2/4/5/6/7/8/9/10全部双图复核；第2/9层明确显示ACTION_BUDGET_ABSTAINED警告，不能因QC近空白判为通过，第0层西南小回波没有独立分类依据。仍未完成其余四体扫全部层的真实UI与独立降水验收。

用户旧result链接实际出现历史版本提示；点击查看最新结果后URL切到bc376635….24a157aa…，历史提示消失，预算弃权层警告保留。快速切层出现原始栅格尚未加载的暂时空白，等加载后重拍才计入复核；未把先前空白截图算通过。原始时间仍为08:26:55，六分钟分析轴08:24并非伪造原始时刻。

ui-observations-03.json保留本地详细身份与工具截图记录。raw-summary-07唯一SHA关联921有回执、913机械完成、4553待，已完成失败0；driver1754097和8容器实际仍运行。实际MinIO命名卷只读statvfs核验可用585746448384字节/100607185 inode，检查容器已正常移除；rainpulse.service active，未重启计算或启用候选17站网络。两层预算与气象保护、完整全天产品及其余UI仍是剩余主线，不进入S+X量化。


## 2026-10-01 S 仅基数据过去来源复核

- 用户确认没有SQI/IQ/脉冲统计，不再把其作为前提。新增只读scripts/audit_s_past_sources.py，冻结旧manifest URI/SHA后按当前105实际profile独立Stage A重算过去体扫；检查时间/健康/原生几何，重复形态非确认、预算降级不出票、无动作。
- 八例past-source-all-v1完成：Z9598 08:18/08:36/08:42过去径向＋偏振拒绝同门重合11/1018/314，08:42双体扫124；Z9591非空四ROI均0且过去测量仅0/1/0/3。此为当前污染线索非真值、当前profile多数不同旧回执明确记录；09:48仍未复现截图。
- 旧跨站/垂直支持实际被current_beam_missing/terrain_missing阻断，不把分数缺测当无天气。下一步冻结过去源对象范围并和当前父对象/偏振/屏障联合资格、补实际几何上下文；未部署/重算产品/Web，完整天气与全链验收仍未完成。


## 新发现弱径向残留：候选提名遗漏（2026-10-01）

ZF70208:08:25全部九个原生REF层已完成真实双图复核（补0/2/4/6/7/8/9，前次5/10保留）。第4层南向及第9层南向仍有弱长径向形态，记REVIEW_REQUIRED，不能把9层机械门通过写为9层全部清干净。ZF70108:03新增0/1层真实双图，其余层未算验收。至此54层中29层有实际全页截图（含前轮），独立天气和整体清除验收未闭合。

对第4层读取正常不可变产品及原生输入，校验native SHA570fb857…、输入3e3b090d…；主射线176.555°、820可见门、64.3–208.9km、跨度144.6km。RAW与QC逐门相等；所有残留门source/proposed/withheld/hard-weather/budget mask为0，原因3584仅定量资格限制。原生370射线没有重复/几何间断，SNR与REF支持完整，排除几何、保护、预算或图件版本导致这条残留的假设。诊断方位只用于定位证据，不进入算法特例。

只读原模块插桩对90/50两模式逐矩阵及整个原模块记录相等，拒绝原因主要是参考块比例、可用块不足/范围杠杆及部分斜率，不是资源退出。保留首个探针relative-import失败，v2修复诊断环境后成功，生产代码未改。两个只读替代实验分别把参考比例分母限定到有配对测量的块、把fan硬编码1dB成员窗口替换为既有spread，均未命中主射线820残留门；前者仅次射线有261门提名、后者整体提名反而下降，均不部署、不记为修复。

105及本地build保留residual-native/moment/model-v2/denominator/membership探针、完整SHA回执和ui-observations-04.json。当前需要进一步建立跨距离功率模式与原始源家族的联合判别，并完整回归；不能只改缺测比例或放宽窗口宣称解决。原问题两个预算层和独立降水对照仍未关闭。八路driver1754097于08:52Z实际live，984文件机械完成，继续原分片，不重启。


## 2026-10-01 S 固定过去来源联合资格回放

- 新增默认未接入引擎的past_sources.py及replay --past-sources；独立过去原始拒绝门冻结10km/60km/3块来源，单跳原始范围内且120km，当前RAW父对象/实测SNR与rho/20&60km窗口仍强制，无递归/新源/填门/动作。6新增、径向修订185测试通过。
- past-source-bounds-all-v3输入身份回执与past-source-joint-v2完成：Z9598 08:18/08:36/08:42候选11/1255/306，合资格0；当前缺可靠测量9/993/271、天气型偏振2/262/35，不能把历史重合当新增清理效果。详细past-source-joint-partition-v1，未生产发布/Web仍未变。
- 105 S Worker实机ENV三项几何资源全部缺失、load_geometry_resources返回radar_config_directory_unavailable；配置bind存在，实际Compose链未含V7资源覆盖层。后续补资源加载/地形和基准真实性审计，或同站相对几何正向天气支持；不冒称1985/文学转换为verified。全链与独立天气验收仍未完成，目标active。


## 2026-10-01 S 几何资源与相对天气支持复核

- 105一次性只读容器加载已有配置/DEM成功、manifest SHA9f4108b…，1985基准仍incompatible_with_epsg_3855；不能只补ENV宣称可信跨站支持恢复。新增独立Compose资源覆盖层仅S/只读、不换镜像profile，未线上启用/重启Worker。
- 新relative_vertical.py、audit_s_relative_vertical.py仅同站同体扫地面弧长/相对高度、实际时间/足迹、独立干净上层正向天气支持；不借绝对高程/DEM作跨站证明，缺测NaN不作负票。4新测试，相关189通过，默认未接入引擎。
- 08:36/08:42实际ROI正向支持仍0；08:42几何观测对97384、可靠连续上层0；current-and-past-source-all-v1确认八输入当前独立Stage A径向＋偏振拒绝残留0且无预算降级。不是改配置即可清除/仅Stage A更新即可修复。代码/回执保存，未部署产品/Web，天气真值与全链验收未闭合、目标active。


## 弱径向两条漏检路径已区分（2026-10-01）

只读读取同一正常产品19f77464…及原生输入3e3b090d…，未修改生产策略或图件。第4层主射线820可见门在5dBZ轮廓中被原生4连通拆成161个组件；最大组件538门、跨度10.95km、角宽6.842°、长宽比1.237；15dBZ最大组件长宽比2.812。两级没有一个残留门获得径向几何提名（要求长宽比至少3），虽有166门具实测侧翼对比。原始全长144.6km不能代表任一组件通过。候选遗漏的具体路径是`label_native`的逐门连续性与`extract_objects`对每个碎片单独作长宽比检查，不能通过降低宽度/比例阈值代替家族关联。

有界同射线诊断按既有1500m间隔且全部SNR有测量串联RAW片段，不填任何门。若把每个间隔低于噪声底都当作家族身份阻断，231个原始区间仅归成221组；区分“间歇源家族身份”和“逐门动作”后，200个片段可形成66.56–160.01km的诊断组，覆盖764/820残留门。此组本身不是污染分类：418门有rho测量，其中59门rho>=.97，只有144门达到既有偏振SNR门槛；超过1500m或缺测的间隔仍保留边界。不得把诊断组全删、把缺测补成零或把低SNR间隔视为动作许可。family-v3中的rejected_gaps仍按旧严格floor条件计数，仅代表原动作屏障，不代表新版身份分组拒绝计数。

第9层native SHA10041ed4…与第4层不同。主弱射线996门中307已获径向几何提名，却仍source/proposed/quarantine为0，local天气保护仅29门；694门有rho，rho中位.975，SNR中位7。该层存在下游实测偏振/SNR资格限制，不能把第4层的碎块提名修复外推为第9层清除。高rho本身也未构成独立降水真值。接下来需完整原始家族的距离窗口可变宽边界、源功率/跨仰角证据与逐门资格联合，先证明真实残留改善与天气保护，再正常重算。

对应object4/object9/family-v2/family-v3/moment9五份不可变只读回执及SHA账本`residual-topology-receipts.json`保留105验收目录和本地build。第一份family诊断因NumPy整数JSON序列化退出，原失败空文件保留；v2仅修诊断序列化后成功，生产代码无改动。一次SSH路径漏写bdp-dp导致读取失败，经实际路径与同一live PID核验后在正确目录读取，未重启计算。

ZF70108:03所有0–8层真实双地图截图已补齐，主体明显减少，3/7层仍有弱南向形态REVIEW_REQUIRED，2层南向孤立回波未分类；不能写成全部清干净。累计六例36/54层实际截图、剩余两体扫18层；ui-observations-05保留身份及观察。本轮浏览器选择后超时，但URL实际已切第2层，恢复同一tab后截图才计入；未把超时算成功。

raw-summary-08逐一关联冻结5466个SHA：1481有回执、1473文件机械完成、3993待完成、已完成FAIL0/身份错误0。机械检查未涵盖本轮发现的全部弱形态清除，不能据此撤销REVIEW_REQUIRED；原八路继续。

补充：ZF70108:49第0/1层真实双图已加载并截图，0层强广域径向主体不再显示，1层RAW/QC以近站弱回波为主；余2–8未验收。六例实际截图38/54，ui-observations-06追加保存，不能当作全部54层通过。


## 2026-10-01 S 原始来源轮廓首次目标增益

- 原始RAW父对象来源足迹审计发现当前原始源范围内的弱残留因功率拟合被排除。新增source_footprint.py/--source-footprint，冻结原始源角向轮廓/范围，目标+邻距块不训练、3外部块连续原始源射线、10km/60km支撑、边界漂移2原生间距、原始距离120km；天气/冲突屏障分段、可靠天气型偏振保守保留；无递归/新源/填门。仅离线资格，未接入引擎动作。
- 5新增/相关194测试通过，source-footprint-v2最终ROI增益：10:42=8、08:36=7、08:42=53，其余0；08:42全层428（含北向明显残留），08:36全层8、10:42全层9。不是确认污染门/误删率/线上结果。
- plot_s_source_projection.py保留，source-footprint-images-v1三幅SHA图件；实际打开08:42观察北向长带减少，南侧部分碎片改动，仍有大量残留。合同/研究文档已记输出研究边界，未部署/Web未变。下一步默认关闭引擎接入+writer原证据重算校验、105实际源回放/全链及无锚目标；独立天气与完整目标仍未闭合，active。

## 2026-10-01 S 来源轮廓引擎接入与105只读回放

- 默认关闭source_footprint_enabled已接引擎候选/geometry提议及writer验证；原始ledger先冻结，弱尾不变新源，audit无动作。序列化原生次序/坐标/good/gap及实测rho/SNR，writer从原始parent/seed重算而非信任合格标志或支撑计数。
- 新增3测试（相关197全通过）：伪造证明/来源/边界/屏障拒绝、原生恢复次序、实测偏振veto、非零引擎提议与source writer。8固定NPZ原证据回放通过，ROI增益仍8/7/53。
- 105真实08:42固定scan只读candidate进程live-source-footprint-v2成功：当前profile文件SHA63fd29b1…，该开关单独新增428可见门、南侧ROI53；RAW不变，序列化验证通过。总10130是多个研究路径提议不能当本次增益；v1误带window开关，v2修正并显式单开关baseline。保留plot_s_radial_review.py --source-footprint供复核。
- 尚未部署/线上重算/Web更新，仍需其他目标、独立天气保护和完整QC→Hybrid→组合→PNG验收；未彻底解决，goal active。


## 原始片段家族内源拟合（2026-10-01）

main410e21b新增RAW片段提名模块（未接生产），真实第4层主射线提名706/820门；套用原偏振与侧翼联合仅4门合格，说明只加几何提名不足。只读插桩把参考及目标限定在RAW家族范围，逐矩阵与旧全true路径模型记录相等；主射线距离留出源模型从0到q90 526/q50 665门。此为因果诊断，两个原家族不允许跨范围合并。

进一步实现source_blocks的可选布尔domain及独立fragment_source入口：逐RAW家族、90/50模式、窄实测角宽，保持原参考跨度/杠杆/比例/源功率及REF响应和资源上限；最终交集只保留原几何观测门，不覆盖保护或填门。54项相关回归通过，包括跨零与不同仰角、目标/邻块留出、短家族不能借远处参考、天气变化与增强、保护、缺测、超限弃权。没有新配置项、生产core接入或线上图件变化。

105同一19f77464正常不可变产品与3e3b090d输入SHA只读验证：第4层全层1359门有新源资格，主残留射线615/820合格；原北向高rho天气型182残留门新提名和资格均0。第9层全层105门合格，但主残留996门仍0。故本轮不能写成全部残留解决。第9层冻结RAW家族26.29–71.96km的q90接收功率从27dB降至约12dB，实际模式不能组成足够具有20km跨度/1.75范围杠杆的稳定参考块；不是资源、缺测或预算拦截。后段85.76–108.56km只5块且范围杠杆不足，保持拒绝，不能放宽阈值掩盖。完整插桩与原mask/record逐字段相等。

fragment-source-receipts-v1账本保留本地build与105验收目录，四份完整不可变JSON及SHA，不提交私有测量。八路原driver1754097于05:06elapsed和8容器实际live；未重启全日检查。下一步扩大同一新路径跨仰角/体扫验证，并用实际原生多层/时间证据辨别非稳定功率径向，保持原预算及独立天气门。第一优先尚未完成，S+X量化未开始。

## 2026-10-01 S 残留精确保留原因与实测侧翼线索

- source_footprint新增REJECTION_CODE完整决策轨迹，writer原证据重算验证；资格不变。可复用audit_s_source_footprint.py校验输入SHA/scan、逐例ROI完整分区及父对象原始来源/种类、RAW范围和实测外侧SNR。198相关测试通过、8例所有旧资格/证明数组逐元素相同。
- decision-audit-v2证据：08:18的362候选352无来源；08:36有486超原始角距范围、389参照不足、209无来源；08:42有292超范围、172参照不足、227未提名。不能简单放宽来源边界覆盖这些。
- 新线索08:18无锚parent59 257残留/32km/3.96°，target SNR中位9.5，双侧实测100%且全部<=3dB；08:36parent107 205/27.25km/2.95°/SNR8，双侧同。只作证据线索，短跨度/实测极化不可靠尚不足删除。下一步检查原生RAW家族分段+多窗口实测SNR对比，保持无锚严格规则；未部署/未新增处置，goal active。


### 六体扫54层新路径实际复核完成

105两路只读driver3666883正常结束，fragment-source-fixed-06c9763/state.json为DIAGNOSTIC_COMPLETE，54/54 DIAGNOSTIC_EVALUATED、失败0。逐receipt SHA、原task身份、模块SHA、原生输入/图件SHA和全部9层检查完成，汇总SHA3c31f2719e40b38eb55773126710c898433b52d20fc284131bfc5014f69b38b2；本地build保存fragment-source-fixed-summary-06c9763.json。实际增加源资格的当前可见残留为620门，仅4层：early第1/2层各1、ZF70208:33第2层3、ZF70208:08第4层615。不能用全层源资格数冒充残留改善，也不能把空间跨度超过20km的647个扫描射线记录当作647条确认污染；其中含正常/未知天气与间断回波，该计数仅诊断候选清单。

这说明新RAW家族范围修复确实解决第4层部分漏检，但尚不能覆盖第9层的非稳定接收功率或其他未知天气形态。生产core、镜像、目录和Web未切新算法；正常旧FAILED、两预算层和候选可信融合资格保持。CUA当前页面状态与恢复同一tab各一次30秒超时，未增加UI通过数，仍38/54实际截图。下一步集中核对非稳定家族的原始字段语义、跨层/相邻真实体扫同源证据与独立天气保护，再完成动作接入和正常生命周期重算；当前目标未完成。

## 2026-10-01 S 实测信号域跟踪试验未获目标增益

- 新signal_tracks.py与replay --signal-tracks仅诊断：真实REF/SNR选择，冻结角向首边界，60km/8°、3外部20km块/20km实测支撑、目标+邻块不训练，固定双侧90%实测/<=3dB，>=80%参照窗合格，稳态SNR差与可靠天气型偏振veto；不确认来源/不删/不填/不递归。小范围侧翼污染只从参照剔除，不放行污染目标。
- 5新测试通过；signal-tracks-v1/v2八例红框目标模型匹配全部0。全层394/566/3902不是目标改善或污染真值；v1有5440/7680天气型veto证明单凭信号形态不应删。08:18短片段仍无60km独立长对象，不能靠降门槛强行删除。
- 可复用脚本/模块/失败研究结果保留，未接生产/未部署；来源轮廓已有428真实08:42只读增益仍待完整发布验收。下一步继续真实可变宽度分段/可用体扫支持，goal active。
- 本轮最终相关radial_revision整套203测试通过，git diff --check通过；测试绿色不等于未匹配目标已解决。


## 2026-10-01 S 复核体扫身份纠正（优先于此前8例结论）

- 用户确认仅有基数据，没有SQI/IQ/脉冲功率统计；继续使用现有实测矩量，不再索要这些数据。
- 发现旧复核脚本按最近前序volume_start猜测体扫，8例中4例与Web实际raw/QC帧不一致：Z9591 09:48、11:24及Z9598 08:18、08:36。此前这些案例的ROI零增益、无来源、多层缺测及signal_tracks结论只能作独立研究样本，不能代表用户截图。08:42及10:42体扫一致，但最新QC数组与Web图件代次仍未闭合。
- 新s_web_identity.py按Web cycle精确valid_time/sweep/配对scan选择，plot_s_radial_review默认绑定Web，移除最近时间回退；单独研究必须显式offline-selection+scan-id。保存帧身份与latest_QC_not_Web_generation_verified标记。测试覆盖身份错配、缺帧、重复、目录分页循环。
- 实际08:18 Web scan9ee02e8c-c049-5d7d-8ff2-7c119f99d160，web-aligned-0818-v1最低层source_footprint单开关新增186可见门，第3层新增334（合格340，6与其他路径重叠）；总研究路径新增1592/6525不可当本路径增益。实际打开两层图件，东南射线减少但西南长线仍残留，未完成/未部署/Web未更新。
- audit_s_volume_sources读取原生ray_time/矩量有效性及冻结flag定义，同scan/normalizedSHA/URI、独立cut、300秒/半原生门匹配；未知位定义保留null、缺测不能当来源、禁止超距外推。新增3测试及Web身份4测试共7通过。仅射电坐标同源诊断，不能冒充真实地面位置天气验证。

## X 原生采样链与片段家族接入（2026-10-01）

本轮main07af471保存native_cut_sampling到writer/eager和流式adapter，绑定原始SHA/配置/原层，opaque波形保持semantic_verification=false。ZF70208:08全部9REF层原文件往返字段/码/几何/时间逐值不变，新逻辑SHAa661d478…；原输入3e3b090d…仍兼容，无Doppler动作升级。主第9层速度334对高SNR相邻门循环差中位9.5m/s仅诊断；PRF1200/800和波形8不能猜语义。

mainf4ec681把RAW片段家族独立来源接入既有block开关下的radial_source完整阶段，kind位8、同一资源账本、完整阶段才发布，硬保护/上下文/预算仍门控。2个接入测试先旧入口RED再GREEN，160相关测试通过，新增资源阶段故障测试后fragment_source10通过。未切生产Worker。a3a2d58文档记正常产品前置门控。

105只读fragment-core-fixed-v1 driver4012791已ps确认Ssl，最新12/54引擎回放EVALUATED、失败0；early1/2各新增1隔离门。回放context=None不能替代正常产品上下文或界面。原raw八路driver1754097同时ps确认Ssl elapsed06:12:41。正常后台不得仅因观察超时重启。已推main，准备仅覆盖source_blocks/fragment_geometry/fragment_source/radial_source的f4ec681镜像，Docker build SSH session90477尚待结果；旧生产镜像仍05db916，尚未发布。下一步检查54层阶段资源及预算，正常六例重算、补界面；第9层非稳态功率残留仍未解决，目标未验收完成。

- 校正后的09:48 source_footprint新增0、11:24新增1可见门。正确08:18跨层只读映射：剩余扇区2511门，第3层实际REF匹配86，其中原始ledger来源79、typed radial15；第5层仅几何覆盖1617但实际REF匹配0；两个typed来源层共同支持0。此为射电坐标来源线索不能推广成天气负票，原“高层完全没有来源”不适用于校正体扫。
- 正确08:18剩余：最低层766无源、1124原始相邻边界训练支持不足，原parent31有4842源门/7射线却仍1121残留；无源parent85有281门/32.75km/3.93°/SNR9.5。信号域新回放最低层目标0、第3层目标4（仅模型匹配不处置）。
- 四例批量读取前两份成功、08:36传输chunk不完整被严格拒绝，未用损坏输入；成功NPZ另行render-only出图，新增逐例transport回执失败前保存。正在独立重取08:36，不影响生产数据/Worker。

补充：f4ec681 X镜像构建成功，image config SHA65eb77b0225b6200071fde1a9f758150a7eae017b04586b194c274d89f1f218e；构建stdout已核验父镜像15ed6130…。完整引擎回放最新21/54 EVALUATED、错误0、新残留隔离5门，driver4012791 ps Ssl elapsed05:55，不能当全批完成。原生采样往返receipt已复制本地build，SHA b3b875351f24d64be196e2f6746bd52b1623029d4face2a006cd1fb25221216d。新旧镜像包逐py文件差异检查SSH session66157待结果，期望仅4个X文件改变。未生产切换。

SSH一次ConnectTimeout后同一状态读取恢复成功，driver4012791仍Ssl elapsed08:13；最新35/54 EVALUATED、失败0、19待完成。ZF70208:08第4/9层仍PENDING，原job未重启。镜像差异核验session66157仅连接层失败、未执行远端检查，后续重试该只读核验即可。

- 08:36独立重取再次缺尾23个传输包，manifest共2947、收到2924、SHA数据未发布；已有逐例失败回执。emit_packet已防短写/InterruptedError并有1测试，尚未证明能修复Docker尾包截断；不能称重取成功。相关算法及身份/来源7测试共210通过，新增传输测试与7项复跑8通过，diff-check通过。生产未变化。


## 2026-10-01 S 原始来源分束修复

- 正确08:18 parent31原source行162–165、167–168、170，旧“每窗整个source行集合必须连续”使两束有原始来源的稳定条带均被拒绝。source_footprint v2按冻结原始连续来源行岛分别追踪；独立维持2射线/10km/3外部参考窗60km/边界漂移/原范围/天气屏障与偏振保留，不能填166/169、借其它父对象、短束支撑或弱尾接力。接口及writer原证据重算保持、默认关闭。
- 实际08:18同两SHA快照相较v1最低层新增424可见门（扇区417、全来自parent31），第3层新增47（parent6/15、目标扇区0）；旧资格损失0，逐束writer重算通过。新增2反例测试、相关整套213通过，diff-check通过。
- 已打开SHA1f5cfe1e…分束对照图：东南残留一条带减少，西南长射线无改动，不能把宽扇区417计数冒充西南目标彻底解决。replay/validation/image可重复脚本保留；105同一Webscan只读引擎验证进行中，尚未部署/Web未更新，目标active。

- 105同一正确08:18最低层只读引擎live-web-aligned-components-v2完成：原始/基线数组相等、rawSHA6716efae…/QCSHA99a5a56c…/profileSHA63fd29b1…一致；v2来源轮廓单开关新增610可见门（v1为186，净增424），原提议损失0，总2016包含其他研究路径不能冒充本模块增益；完整revision writer校验在脚本内通过。未写产品或重启Worker，完整发布与天气验收仍待完成。

## X 全层引擎回放与超时恢复（2026-10-01）

v1 driver4012791已终止为DIAGNOSTIC_FAILED：54层中50完成、4观察TimeoutExpired（原ZF70208:26的0/2/4/5层），不是算法错误结论；240秒杀Docker CLI后旧容器一度仍live，先确认全部旧容器消失，保留原失败/empty pending后仅恢复4项。重点ZF70208:08第4层source/quarantine新增均615、第9层均0；receipt SHA分别a7662a49…及e7c742ff…，无跨层context仅引擎回放，不替代正常图件。

mainf6b6b39已推：domain只遍历有信号的good射线，空行原逻辑本来也不产生模型/资源计数；新测试旧版360次块索引遍历RED、优化后1次GREEN。完整xqc_v2 132项通过；冻结同一测试输入新旧mask/model/work receipt逐字段相等，单次39.7ms→24.8ms，不宣称真实大层加速已验证。没有阈值/动作改变。

恢复driver4165085、fragment-core-recovery-v2/state.json已ps确认Ssl elapsed06:12，4项PENDING、失败0，cut0/2两个容器实际Up6m。移除观察脚本240s退出，用后台长运行Docker CLI完整等待；源模块SHA仍冻结，父v1终态SHA、task身份绑定，不改原失败。该批次必须继续核验，不盲重启。

f6b6b39镜像已构建并真实比较：imageID a8ff28951c91c3df15ad976b6aeaa69eed7e1fa33f8262b1037ce6f121f90d61，仅四个X文件改变、790个py未改变。旧f4ec681实际imageID0b0a8c77…不是configSHA65eb77…，准备脚本已纠正；无线上切换。新deploy-fragment-fast.py先结合50旧成功+4恢复完整回执、严格normal/预算状态与source complete，再核验空闲worker/冻结network/旧fingerprint后切候选发布。replan-fragment-fast-six.py准备好重算六例，均未执行。第一优先仍未完成、第9层另一来源未解决、不启用可信融合/QPE/预报。SSH曾连接超时，随后同一作业状态读成功。

补充：恢复v2四层全部DIAGNOSTIC_EVALUATED，cut2保持ACTION_BUDGET_ABSTAINED；合计54层回放全部返回且部署脚本source_complete/回执身份校验通过。生产候选已正常切f6b6b39：4Worker ready，image a8ff2895…，network bee28feb…未变，新multiband fingerprint 0f31e8f767c384d18782289b5d9098cb7f49e91eaec47b9c0a2e8579d3e97cc3。后台delivery PID61263 ps Ss elapsed01:19，state RUNNING_AUDIT_SIX；verify/deploy/submit均exit0，尚未产品/UI完成。

新六正常task：early600f808f-fee1-46ed-9897-02e53c282c8b；nexta7951227-bc8e-4714-a56d-b8413759ffd0；zf7021f760dfd-6d86-4221-9bf9-18ec9a993b66；zf701-08492af26183-e4fc-4e68-9efe-a8779f084c55；zf702-original8346af7a-2418-40ed-966e-68d94a9b768c；zf702-0808fd6c60de-18ac-4e01-a111-58b30cf061f3。normal目录fragment-core-normal-f6b6b39，driver-log与fragment-delivery-state.json保留。finish已修复旧脚本FAILED也可能写NORMAL_PUBLICATION_AUDITED的观察分支，失败立即STOPPED_NORMAL_TASK_FAILED；机械失败仍显式留下review-required，不能当作全部验收。UI需打开新result_id，原URL固定旧result不能期待改写旧图件。

### X 新正常产品复核（2026-10-01 后续）

候选五正常任务完成/45层机械检查通过，原ZF70208:26 task8346af7a…仍RUNNING/COMPUTE，13:00Z实际新鲜heartbeat与CPU118%、RSS约1.1GiB；不重启。delivery61263/finish66144继续。实际浏览器已恢复，ZF70208:08新result fd6c60de-18ac-4e01-a111-58b30cf061f3.faf320de-61e3-4464-8652-2a839246a4aa全9层新图件截图观察完成（第7层首次loading未计，重访完成）；新增新结果UI9/54，其余旧38/54不能混计。

新旧正常产品源/RAW/几何/时间相等：cut4主ray364原820、新隔离615 kind8、仍205；cut9 ray208原996、新隔离0、仍996。compare-fragment-normal.py及-diagnostic.py/回执保留105验收dir/localbuild，未写产品。剩余cut4 205/nom91、cut9 996/nom456，无SNR缺测/低于3dB/硬天气保护；reason3584为phase/calibration/attenuation unknown，不是budget。下一步聚焦RAW提名边界与非稳态源模型，保持真实天气反例和跨体扫证据，不放宽阈值或猜waveform8。

raw八路driver1754097新鲜live7h、快照3033RAW_FILE_COMPLETE/112540机械层检查；不是新f6全天验收或天气接受。独立失败快照raw-failure-independent-review-v1.json SHA ad08dece1dd437abf402b16ae3d60318084b65979fd53589219cfe7fd24c1e11：38份冻结SHA源bzip2独立全读EOF，另5个不同源为预算/倒序，快照会继续增长，保留原FAILED。MinIO实际所在数据盘free585492312064字节/inode100593119。第一优先未完成，S+X量化未开始。

更新：六正常任务最终全部SUCCEEDED，normal state为NORMAL_PUBLICATION_AUDITED，52/54机械门通过，原ZF70208:26第2/9层保留CUT_ACTION_BUDGET_ABSTAINED；此终态优先于前文RUNNING。新产品对比诊断本地SHA13fb4a80170b4a998045c0c845107a167bcfadf8fd15ff49a7117c87a60e54e4。文档main7cb78ed已push，其间并发S提交5f639fc/4eee2e6在同main，未随X镜像部署；X实际image保持f6b6b39四模块。

## 2026-10-01 S 发布恢复与无锚残留推进

- 算法5f639fc已推送；v3正常Worker计算/数组验证通过但profile_version长度513触发摘要512上限，首job31080398终止。ee70342已改短版本+完整父配置摘要、profile parser预检查及后台按profileSHA/对象复用已成功/活跃任务；105两主S Worker现为s-bounded-radial-20261001-v4，配置SHA ebaa36fa24c147ebc4b4975062a9e4c39cbdd1623685e2d581e92ba3a660db68。首QC b9460c88已SUCCEEDED且grid完成；后台脚本PID469093运行，state当前08:18第三站QC，未确认组合/PNG发布。X未重启。
- 441ad7f复核脚本默认全原生视场，显式方位/距离支持跨北；实际9ee02e8c…08:18西南210–270°/100km外367剩余均无原来源，SNR中位8.5、RHOHV239门中位0.98；侧邻±2射线有281/287门为DBZH缺失但实测SNR≤3。下一步只读验证短片1km候选门槛及实测噪声侧邻，不能直接扩父边界/删高相关天气。证据web-aligned-0818-sw-v2.json与sw-moments-v2.json；方法泛化与完整PNG链仍未完成。

## 2026-10-01 S 本轮提交与105 Web交付核验

- ab5664f已推origin/main：微碎片/实测噪声侧翼只读四因素复核、三项测试与部署研究记录；相关radial_revision测试通过，两层正确08:18快照默认detect全部数组/报告与提交前相同。诊断代码已同步105现有目录，脚本SHA5580a8ac…与模块SHA116a0bca…本地远端一致；只读probe未接生产。并发X的2f506f2另有提交，未随本次擅自推送。
- 线上主S两Worker仍healthy/v4/profileSHAebaa36fa…，dff524c新orchestrator已部署。08:18正常QC→grid→mosaic→QPE→diagnostics完整发布，实际Web API选analysis cb2e4c83…及job79c0f459…，PNG下载/观察完成；离散径向仍有残留。后台732063实际live，08:36已进入mosaic，无error；其余八例顺序继续，未重启重复任务。
- 最新全量GitHub CI36879262437终态failure，包含旧参数hash、fusion对照及其他test/lint失败；不能宣称全量绿色。当前交付证据见S_BOUNDED_RADIAL_DEPLOY_20261001.md。目标未彻底解决，不把微碎片提名/独立源stage增益当完整天气泛化验收。

## 2026-10-01 X 常规 FMT 参数保留与54层速度谱宽诊断

用户假期无法核实厂家波形8/相位0；不等待厂家，不再次索要IQ/SQI，用已有Level2继续。官方QXT653 PDF表6核验常规FMT偏移16/64/68/72/76；2f506f2新增解模糊模式、两采样数、相位模式、大气损耗原样保留，decoder2.2.1，PA布局不猜。真实ZF702九REF层往返v3通过、原矩量/码/坐标/时间逐值相同，新参数semantic_verification=false；回执SHA2dbc85c8…，本地build已复制。元数据修复尚未切生产decoder/Worker，不能说已部署算法。

834ca88新增native_doppler_diagnostic与scripts/audit_x_native_doppler.py，只读原生有效相邻门统计；无动作资格，声明Nyquist仅敏感性检查，不代表双PRF解模糊验证。11新测试及解码/adapter/XQC整套186通过（现有NumPy兼容警告1）；首6新测试实际RED后GREEN。39f6ad1证据文档已pushmain；并发S无关dirty/untracked保留。

105 native-doppler-v1六task54层全完成exit0，六JSON/receipt SHA校验并复制本地。脚本SHAf9d56315…、模块c747dd13…、imagea8ff2895…绑定，未写公开产品。ZF70208:08剩余cut4ray364有80有效相邻对、声明周期差中位8.5m/s/相干0.091/SW中位4.5；cut9ray208有334对、9.5/0.047/7。部分ZF701保留回波SW10.5但相干.66-.73，不能用谱宽单阈值删除。跨体扫短时支持超时间窗、跨层偏振签名不同、合格相邻同源参考缺失，均保留只读反证，不能放宽源阈值强行匹配。

实际浏览器新原问题体扫8346af7a…/cut2地图与ACTION_BUDGET_ABSTAINED警示已观察；新结果UI观察10/54（含这项review-required），不混旧38/54。原六正常task仍52/54机械通过；cut4余205/cut9余996未解决。下一步验证双PRF别名敏感性和独立天气对照，发展可复验的非稳态源证据，再正常生命周期重算/UI；目标未完成。

raw八路旧冻结driver1754097新鲜ps Ssl elapsed9h03m，快照4197 RAW_FILE_COMPLETE、4138 DECODE_VERIFIED、148254机械门/88FAIL记录，1worker已COMPLETE；这不是f6新算法全日天气接受。实际MinIO卷经只读root容器statvfs可用581848674304字节/inode100525349，保护线之上；/data是NFS不得代替。最初df容器无工具、宿主卷目录权限不足均未作为盘量证据，不涉及清理/重启。最新并发S记全量CI36879262437失败，相关186绿色不代表全量CI通过。

## S native temporal audit, 2026-10-01 (5d42e71 pushed)

- New reusable audit/test: 9 passed. Native acquisition time, half-bin angular/range matching, elevation, gaps, distinct raw inputs and weather/conflict barriers. Diagnostic recurrence only; zero actions and no source promotion.
- Actual four 08:18 short parents occupy different angles, so association into one long source is disproven. New Web-paired 08:36 scan5172b858 and 08:42 scand12ac95a snapshots pass complete transport SHA checks. native-temporal-0836-v2: 1079 SW residuals, only 2 co-located past DBZH observations. native-temporal-0842-v2: 781 broad-sector residuals, two past DBZH votes36, two typed radial votes2, two stable-SNR votes0. Fixed recurrence is insufficient; details in S_RADIAL_RESIDUAL_RESEARCH_20261001.md.
- Last verified 105 driver732063 live at39:09; 08:18 and08:36 published, 08:42 grid. Later SSH timeout/API502, new script sync NOT completed (scp connection closed), no restart. CI36882213341 failure, not green. Goal remains incomplete.

### X 最新主线调整：用户要求形态驱动（优先于此前仅稳定源补强路线）

最新用户提出“有没办法跳出来，从形态上解决...肉眼明显径向或扇形”。已回应可行，按原生极坐标的细长径向/断续径向/宽扇形、多尺度边界/中心方位跟踪为主入口；矩量辅助、厂家扩展码未知不阻止形态检测。天气团/弯曲雨带/固定公里宽雨带反例及原始数据回放必须保留。新形态分类尚未实施/处置，不能把已有窄fragment_geometry提名当作完成；下一goal turn应集中做此方向，停止单纯稳定功率/相位阈值补丁研究。完整方案在docs/X_NATIVE_DOPPLER_DIAGNOSTIC_20261001.md末节。

速度谐波v2十二测试通过，原平滑风+分支跳变反例先RED后GREEN；正常六体扫54层v2全部成功，核对elevation/time等全部原生身份。JSON/receipt均复制本地并校验，summary SHA1b2c18e87277286eba4c94770945e65b6edae389c951c3ce6e546139e929dc40；cut9所有1–6谐波相干最大.2534，cut4最大.4838，仅有限分支敏感性结果，未动作升级。脚本SHA54fa81ae…、模块eb73e2aa…。代码47e6eaa/c35d0bf已push（c35一次GitHubSSL失败后原协议重试成功）。

6c19ca8同步Go RadarDecoderVersion到2.2.1，Pythonworker会严格拒绝版本不等请求，防止后续元数据发布启动即失败；原RP006硬钉2.1.0结构检查改控制/计算一致性，版本不等实际RED后GREEN；Go TestCreateRadarDecode通过。所有新解码代码仍未部署，105旧生产decode/controller不能只切其中一端；现有在途旧任务版本保持，不盲重启。候选X生产仍f6b6b39。目标active、未验收完成，下一步新形态算法实施与真实天气/用户案例验证。


## S whole-object morphology, 2026-10-02 (0ffbd38 pushed)

- New morphology_objects.py and reusable audit_s_morphology_objects.py: RAW angular templates at 5/10/20km and multiple DBZH levels, no stable-power or prior-source requirement; frozen boundaries, measured bilateral shoulders, per-target recheck, weather veto, no gap filling or recursive growth. Real gap-free PPI seam supported; sectors never wrap. Fourteen new tests plus related radial_revision regression passed. Evidence replay rejects forged masks; resource exhaustion returns no partial acceptance.
- All eight actual Web-paired native inputs replayed in native-morphology-objects-all-v3. Remaining evidence hits by case: Z9598 08:18=0, 08:36=832, 08:42=646; Z9591 09:48=0, 10:18=37, 10:24=0, 10:42=18, 11:24=0. SW210-270/>100km 08:36=800/1079, 08:18=0/367. Broad160-280/>100km 08:42=1/781, so most full-field gain is northern tails, not southern target improvement. Evidence is not confirmed pollution or full Worker/Web acceptance; independent weather holdout remains incomplete.
- Four committed files synchronized in place to105 and SHA matched: detector1ae372bf..., script3946002f.... No production action/image/config switch for this module. Deployment doc S_MORPHOLOGY_OBJECTS_20261001.md and repeatable PNGs retained. v4 driver732063 actually live at1:27:15, completed08:18/08:36/08:42/09:48/10:18, processing10:24; actual08:42 Web API now references diagnostics2ea995b4.... Not restarted. Goal still incomplete; next focus short/wide fragment substructure and independent weather counterexamples, not lowering all span thresholds.


## S whole-object engine integration, 2026-10-02 (1079765 pushed)

- Default-off FragmentLineConfig.whole_object_morphology_enabled now routes only strong whole-object evidence into experiment geometry quarantine after original source identities freeze. Audit keeps zero actions; weak nominees never gain source/action eligibility. Persist original measurement/availability, native coordinates/order, beam/good/gap and barriers; writer binds DBZH_RAW and recomputes masks after restoring native order. Two meaningful integration/writer tests added; 16 morphology tests and related 224-test radial_revision suite passed; diff-check passed.
- Actual engine replay with captured baseline equality succeeded for correct Web-paired Z9598 08:18 (live-web-aligned-components-v2 full snapshot), 08:36, 08:42. SW 08:36 strong AND extra visible proposals 800/1079, proof validator passed, no withdrawn old proposals or promoted original source identity. New reusable --engine-quarantine PNG shows experiment-after at native-morphology-engine-effect-v1; opened actual image. 08:18 remains zero new hits.
- Strict replay rejected 09:48 old snapshot SOURCE_FOOTPRINT_REJECTION_CODE drift and 10:18 SEGMENT_RESIDUAL_DB drift. Earlier components-v2 77-field diagnosis bundles are not complete raw snapshots (no sweep/Web/config). Do NOT skip baseline check, merge mismatched identities, or claim eight engine cases passed. Need capture updated full snapshots before remaining cases and weather holdout.
- Seven committed files synchronized in place to105; SHA checked (engine244a77b..., detectora567c7f..., validation68e2424..., scriptd84400f...). NO new production image/profile activation or Web publication for whole-object module. Fresh previous driver732063 still S at2:07:07; 7/8 published, processing11:24; v4 image and ebaa36 profile unchanged. Do not alter active file until driver terminal and queue idle. CI36893706818 in_progress at last read, not all-CI-green claim.
- Next: correct full baseline snapshots, frozen experimental child image/profile, normal QC-Hybrid-mosaic-PNG publication after current driver; independent weather negatives; short/wide subbranch morphology remains unresolved. Goal active. X WIP untouched.


### S morphology v5 experimental deployment, 2026-10-02 (defe096/c13561e pushed)

- Old v4 driver finished DONE, all eight slots published, PID732063 gone. New v5 child YAML SHA1f9803eab58c3eb8ee602a346c7304d2076073f5a95c5fe8e416850aa9397cd5 enables whole_object_morphology_enabled under existing experiment_quarantine only. Reusable refresh driver now accepts frozen profile/output/plan and priority UTC slot, checks distinct four-station groups and persisted plan SHA. Original v4 plan reused, 08:36 prioritized.
- 105 built rainpulse-cpu-worker:s-whole-morphology-20261002-v5 from v4, only config/engine/morphology_objects/validation copied. Image config SHA7832a8626a3f13e438acfc573a34e92016d15acb59b2819ddcd24fe1642a87c4. Initial compileall failed due inherited macOS sidecars and bytecode permissions; c13561e fixes read-only compilation of four copied files, rebuilt successfully. Actual image engine+writer test passed (100 synthetic geometry proposals, audit zero), child profile parsed.
- Queue idle and old image/profile/DONE preflight passed; parent active profile backed up .build/s-bounded-radial-20261001-release/morph-v5-parent.yaml, atomic child switch, both primary QC workers healthy on v5. X workers/decoder/controller unchanged.
- Background systemd unit rainpulse-s-whole-morphology-v5-refresh started, MainPID1694012 active/running. First root Docker/chroot launcher failed bus connection and created no unit; verified not-found then retried with host PID/network namespaces, succeeded; no duplicate driver. State/log .build/s-whole-morphology-20261002-v5-refresh. New Web publication remains pending until normal stages complete; do not claim images updated yet.
- c13561e CI36895280658 completed failure, previous1079765 CI has legacy parameter hashes/fusion/lint/general test failures; related224 tests/build/image smoke are green, not global CI acceptance. Independent weather holdout and short/wide morphology unresolved. Goal remains active.

- Final v5 launch receipt: RUNNING at08:36CST, first normal QC job115916e9-0e3f-55be-b1ae-ae5817858e8a submitted with childSHA1f9803ea; current Web pictures not yet regenerated. New image installed module SHA checks performed independently after activation; next turn inspect job completion/proof arrays and published PNG before claiming effect in Web.


## S morphology subbranch evidence, 2026-10-02

- Added reusable diagnostic audit_s_morphology_subbranches.py plus five geometry counterexample tests; volume-source audit now defaults to full native domain and explicit ROI rather than station rules (four tests). All nine targeted tests passed this turn. Changes remain uncommitted after concurrent main moved from e0a3d3f to724500b (X polar morphology); preserve that work and stage only own scope in a later serialized step.
- Correct08:42 d12ac95a snapshotSHAc5de3184: earlier211 PCA nominees included26 in a146-degree broad domain. New bounded diagnostic gives185/781, no actions. RAW10dBZ component81.9–460.1km/197.3–208.1deg; existing frozen boundary templates split related nominations into short windows and many fail edge contrast. Global PCA cannot authorize broad connected-weather removal. Receipts subbranches-0842-bounded-v2.json/subbranches-0842-rejection-v1.json. Next implement bounded variable-width whole-object tracking with centre/edge shape, constant-km-weather counterexamples and per-target observed shoulders; do not just lower all length thresholds.
- Exact08:18 SW367 donors sameRAW6716efae/cuts2,4 have geometric coverage367/295 but actualDBZH0, hence no independent weather-negative evidence. 105 current unit rainpulse-s-whole-morphology-v5-refresh MainPID1694012 active/running, completed08:36/08:18 and current08:42. New diagnostic corrections are not production algorithm changes; v5 remains experimental, browser/weather holdout acceptance pending.


### S complete variable-width prototype, 2026-10-02 (7965740 pushed)

- Eight own files committed/pushed on main after concurrent724500b stabilized: variable_morphology.py complete RAW overlap-history prototype, nine tests, native subbranch audit/five tests, explicit whole-domain volume audit/four tests, reusable --variable-width plot/proof switch and docs.34 relevant tests pass plus diff-check. Source8a26984c… and script8013b36b…; all eight source/test/doc files synced in place105 and SHA independently matches local archive. Worker/config not changed, prototype evidence-only; --variable-width plus --engine-quarantine rejects.
- Real correct08:18/08:36/08:42 full snapshots replayed/proof-redetected; broad160–280/>100km remaining2094/1374/781, strong target0/0/0. Artifacts variable-morphology-three-v4 with SHA receipts/PNG;08:42 image inspected. Whole-history curvature/fork/barriers/side contrast still reject; no claim of new improvement. Prototype preserves early failed envelopes and rejects constant-km rain ribbon, curved rain, unknown shoulders, hard protection, forks, forged proof and resource exhaustion. Limits2m gates/50k raw window nominations/50m work; no PPI seam connection yet. Next independent radial branches within frozen complete parent domain, not relaxed global weather protection.
- Latest actual systemd unit1694012 active/running, v5 completed08:36/08:18/08:42, now09:48. CI36902766632 in_progress at last read, not green. Main remote7965740 matches; project memory remains local modified. Goal not complete: normal browser acceptance, independent weather holdout and residual improvement remain.


## X native polar morphology pilot, 2026-10-02 (724500b pushed)

- User requests morphology as main route. Generic RAW-native centre/edge tracking at physical5/10km and levels5/15/25dBZ; radial/intermittent/fan objects, measured bilateral exterior shoulders, seam/gap/weather/resource barriers; no station/bearing/elevation exceptions. Shape-only action3 candidate, raw preserved, not confirmed-source promotion. Default local weather protects; joint_review explicit pilot. Budget preserves old qualified baseline. 49 scoped and191 XQC-v2 tests passed; code CI36900071920 failure (build passed), no global-green claim.
- Frozen v9 six tasks/54cuts all EVALUATED, hard weather overlap0. Actual oldZF70208:08 cut4ray364 201/205 residual hits; cut9ray208 996/996. ModuleSHA2b27c36f..., audit8be4c3ee..., receipts local/105.
- Candidate105 four ops multiband ready on xqc-polar-morphology-724500b-compat-mb, image0e8a8f84..., networkaa2356cc..., fingerprintb69c8995.... Only4 Python paths changed,791 unchanged. Generated pipeline applies committed724500b delta to oldimage pipeline; excludes main's undeployed radome/phase/decoder extensions. PipelineSHAa5fceda6.... Network changes onlyrelease_id andZF701/ZF702 morphology; r1 implicitdefaults draft rejected and r2 preserve generator used. S/decoder/controller unchanged, hourly automation not resumed.
- Six normal tasks submitted frozen sources; five SUCCEEDED/45cuts audited,44 mechanical gates pass, ZF70108:07 cut2 DEGRADED_MORPHOLOGY_ACTION_BUDGET staysreview. OriginalZF70208:26 task198c0180-ac2c-4edd-a377-8e517e8a8f13 stillRUNNING/COMPUTE withfreshheartbeat, CPU~100%/RSS1.34GiB; do not restart. Driver2022470/log/state .build/xqc-acceptance-20261001/polar-morphology-normal-724500b.
- New normalZF70208:08 comparisonSHA3b6e7216... cut4ray364 old205 now4 isolated gates, cut9ray208 old996 now0, RAW/geometry/time exact andnewlyvisible0. Five actualUI captures observed: ZF701early0/3,next0 andZF7020808cut4/9 showlongradial/fan gone. Local .build/xqc-polar-morphology/normal screenshots; next1 screenshot timedout andnotcounted. Independent weather holdout stillincomplete; no trustedfusion/QPE/forecast. Goalactive; nextcheck sixthnormalreceipt andbudgetlayers/UI, thennegativeweather/expandedstations, notanotherstabilitypatch.


## X morphology normal completion and nine-station replay, 2026-10-02

- Previous turn is progress: deployed pilot, submitted/published5 normalvolumes, actualUI5. This turn sixthnormal original198c0180 completed SUCCEEDED runtime1455779ms anddriver2022470 ended normally/NORMAL_PUBLICATION_AUDITED. All54 cuts RAW/geometry/time/PNG verified;48 fullyEVALUATED,6 budgetreview (ZF701nextcut2; ZF702original0/2/4/7/9), no falsegreen. Installedmodule2b27… replayall6 newnormalproducts:54 EVALUATED, selectedstillvisible0, hardweatheroverlap0; original replaySHAdd5a1f37….4 remainingcut4ray364 gates64.3125/69.7875/173.6625/208.9125km,6/8/17.5/20.5dBZ; notweathertruth/extrapolation.
- Frozen inventory20 nonpilot enabledX stations before openingvalues;9 withnative-v2 selectedfirstUTC00:00–01:00 publishedvolume = ZF101/102/103/104/105/401/402/505/605,268cuts.11 lacknative-v2 selectedoldproducts, do not mix counts. ZF703/ZF801 still excluded unknownformat. Firstexpandedv1 only505 succeeded;8 KeyErrors optionalhistorymasks, failedreceipts retained. a9d2e85 pushed2 RED/GREEN tests andauditfix: absentcontextmask null; incompletebaseline masks list/fractionsnull, no fabricatedzeros, requiredRAWidentityunchanged.193 fullXQC-v2 tests pass, relevant ruffpass.
- Expandedv2 all9 exit0/268 EVALUATED, resourceabstentions0/hardweatherhits0.8 stationsqualified0; ZF505738 RAWgatesselected and40 oldQCsurvivors, localweatherproxyoverlap0 (notindependenttruth). Script99e7fcbe…, moduleunchanged2b27….NineJSON/receipts SHAverified local.build/xqc-polar-morphology/expanded-v2;105frozenprotocol/replayfolders. Total11stations15volumes322readonlycuts; actualnewnormal2stations6volumes54cuts only. Networkimageunchanged, no expandedactivation.
- BrowsergetTab/getState both actualtimeouts afterkernelreset; no newUIobservations, previous5 remain. Goalactive. Next inspectZF505object40/residualPNG thenfrozenchildnormal lifecycle, legacy11 source-levelpath, independentweatherholdout/UI. No restartedhourlyautomation/no trustedfusion/QPE/forecast.

### S morphology measured branch exterior, 2026-10-02 (local prototype)

- Actual published 08:42 diagnostic f8b87a06 and input QC c786f3a1 match v5 parameter hash31bdfea9, RAW6798c862 unchanged. Selected old source-stage781 targets:728 renderer-eligible,53quarantined; true current product residual, not stale-profile explanation. Reusable audit_s_published_qc.py binds consumed diagnostic job/input, three tests pass; receipt published-variable-targets-0842-v2.
- Variable local actual footprints fix and optional bounded branch exterior search implemented locally: nearest measured contrast within2beam, every actionable path observed/unbarred, fixed original branch body, stable exterior; original qualified objects preserved.18 variable +3 product tests pass, diff-check. Real full RAW three-case v3 proof replay0/0/2 targets, no gain over footprint correction. First branch replay hit50M work cap/no partial output; optimized unique distance/path work plus geometry eligibility, no cap increase. Actual PNG reviewed; broad fan residual remains. Not engine/writer/production-enabled.
- Shared main moved during work a9abeef→524b4b9 (concurrent X work); own changes preserved unstaged/uncommitted, no push or worker modification this turn. Next full branch edge-history and repeated radial texture study plus independent weather counterexamples; do not lower global contrast to call success. Goal active.

### S frozen RAW radial backbone, 2026-10-02 (local evidence-only prototype)

- New radial_backbone.py identifies persistent original angular cores across full distance history, freezes one local actual-footprint fringe, checks lower-level parent narrowing even for high-intensity cores. Protected distance columns are excluded independently; full barriers split supported segments. Weather-core RHOHV/SNR also protects polarization-missing weak fringes. No source inheritance, recursive growth, filled gates or product actions. Reusable morphology audit --radial-backbone and published audit --evidence retained.
- 32 scoped tests pass (10 backbone/18 variable/4 stored-product binding); diff-check passes. Final three full RAW proofs/PNGs frozen-radial-backbone-v5 target0/0/72. Weather-core safeguard withdrew161 of previous233 nominations. Actual Web consumed v5 QC confirms68 of final72 remain renderer-eligible,4alreadyquarantined; published-backbone-targets-0842-v2 binds same v4/v5 final mask (equality checked). PNG inspected; not pollution truth, not deployed effect.
- Five Z9591 full RAW/Web snapshots frozen-radial-backbone-z9591-v2 have0new targetstrong;8case scope stillnotresolved. No independentweatherholdout or engine/writer integration yet. Main moved524b4b9→a7e780c byconcurrentXwork; ownprototype/diagnostics remainunstaged/uncommitted and no image/profile/product activation thisturn. Goalactive: validate actualweather, integrate onlyproof-bound qualified shape, address other7 residualcases rather than claimcomplete.


## X third-station normal completion and source inventory, 2026-10-02

- This goal turn is progress. ZF505 native sweep8 is9.89deg/sequence7; initial index8 wrongly selected unrelated19.45deg PNG and is excluded. Reviewed complete RAW51.36–104.69km object. Identical pilot policy/image0e8a8f84, no station/bearing/elevation tuning.6c0f877 freeze pushed before config. Childnetwork288690f1…/fingerprint59b7e186… changes only release_id+ZF505 morphology; four X ops recreated after ready/idle. S/decoder untouched; hourly automation not resumed.
- New normaltask2d7b0ad4-910a-4b5c-b476-bcd4449ceebb/attempt045dbec6-1104-4f69-aaa9-8067b653cca6 SUCCEEDED; driver2453270 terminal/NORMAL_PUBLICATION_AUDITED,9/9 mechanical gates. Oldray16340visible→0, cut153→113, wholevolume newlyvisible0; RAW/coords/time exact, comparisonSHA44d3f840….29 candidateaction3/11 sourcekind4reject. Oldsourcekindall0; independent installed-core morphologyOFF fullnewsourcekindmatches andconfirms11overlap, SHA7836b161….Firstdebugassert used allrejectedoverlap insteadoldvisible11; v2 boundoldnative fixesonlyscope, failedpendingretained. NewRAWPNG74a089cd unchanged; QC00eb9455… inspected, no UIclaim.
- a7e780c adds bounded --source-only audit;4 newtests actualRED/GREEN, full197XQC-v2 pass/ruff/diffcheckpass, existingNumPywarning1. Legacy11 frozen beforevalues:ZF201/202/203/501/502/503/504/602/603/604/900. v1 allfailed beforedetection because root sweep_start/end_ray_index parsedascuts; actualmetadata probe+regressionRED,57cda3f fixesrootcoordinates withoutrelaxinginvalidgroup/index/64bounds, pushedbefore sync. AuditSHAc95bbc462…/detectorunchanged2b27….
- source-v2 PID2588230 terminal SOURCE_REPLAY_COMPLETED,11exit0/405REFcuts EVALUATED/resource0/qualified0; actual360x1998,272–273x1472,826x1020 geometry. No products/actions; unavailableoldsurvivor/budget/weathercountsnull, notweathertruth. JSON/receipts/frozenprotocol/tasks copied .build/xqc-polar-morphology/legacy-source-v2 andlegacy-source-frozen-v1; alloutput/taskSHAverified. v1failurepreserved. Cumulative22stations26frozenvolumes727cuts =322pairednative+405source-only; normalnew3stations7volumes63cuts only.6budgetreviewlayers andindependentrealweather/cross-timeholdout incomplete.
- Inappbrowser3/tab4 briefly recovered/navigated newZF505URL butinitialloadingstate; logs/screenshot/getTabtimeout+kernelreset, no UIobservations beyondprior5. Inventory Maclocked error, backendworkavailable so no unlockrequest. Actual4173catalogAPI200/28stations/66ZF505scans/newresult verified0.12/0.01s, notUIproof. NewnormalJSON/PNG/sourcecheckSHA verified local. Docs8220376pushed after concurrentS01f964a alreadypushed(reflog/push01f964a→8220376); no Scode deployed byX. Memory stayslocaldirty, untrackedworkpreserved.
- Goalactive; nextfreeze cross-time/scan-family weather/pollution contrasts, inspect unqualifiedcompleteobjects andbudgetcuts, supplementtrueUI whenbrowserrecovers. Do not call727normalpublications or0nominees independentweatheracceptance; no businessfusion/QPE/forecastpromotion.


### X 跨时次形态验收续进，2026-10-02

- 本轮无生产算法/阈值/网络变动。22站预声明UTC02–03/05–06窗口，10站20冻源体扫/492层源级回放成功，缺样12站x2窗口保留；protocol e4d476a9…，原API按volume_end筛选、first按volume_start排序，跨窗起始说明另存不改协议。driver2749069已SOURCE_REPLAY_COMPLETED。累计22站46唯一体扫1219层只读，不是正常发布计数。
- 同8份冻源72层原生产品配对成功且JSON/task SHA本地核验；driver2764202/2778621终止。ZF402发现旧可见191/1793/514门新提名，当前未上线形态；ZF50505时次提名266870必须独立天气/完整图复核；ZF70205 cut7宽20.8deg/约120km扇形旧可见60509且预算弃权，cut5局部天气代理冲突1439，不能只按检出数量通过。
- 六正常预算层形态选中可见/CR0，告警保留，不升预算。新正常3站7体扫63层、真实UI5张和独立天气未完成边界不变；浏览器getTab仍超时。四X Worker健康，MinIO557842051072bytes/100052722inode。本轮仅只读审计，S并发脏文件保留、小时任务不恢复。
- 下一步完整对象原始/动作图和独立天气对照后，按冻源正常路径验收新跨时次样本；不固定站号/角度、不shape->confirmed。详细证据见docs/X_POLAR_MORPHOLOGY_20261001.md最新节，目标active。

### S full-RAW transverse fragment constellation, 2026-10-02

New fragment_constellation.py proposes whole original transverse-fragment groups using fixed original bearing neighborhoods, actual range support and multiwindow history. No ROI/residual/source input, recursive expansion, gap wrapping or engine/product actions. Five Web-paired Z9591 NW >300km replays raw-constellation-nw-v2: selected/candidate/strong 0948=97/77/0,1018=21/13/13,1024=103/59/0,1042=36/18/0,1124=0/0/0. Entire field strong objects only1 in1018; no independentweathertruth. Weather originalmembers and measured bilateral insufficiency still hold othergroups. 0948/1018 PNGs inspected. Measurement script audit_s_fragment_shapes.py retained; diagnosticgroups initially selectionbiased, newdetector independentallRAW. 271 relevanttests pass. Own6files commit09ae6a7, onmain afterconcurrentXdocs f904b24; no productionactivation. Goalactive: weathercounterexamples, unexplainedmembers/remainingtargets, widefanbranches and normalpublishedWebacceptance stillincomplete.

### S full-history constrained fragment segment evidence, 2026-10-02

Commit c0abe59 follows09ae6a7: optional research-only fragment_segments measures each originalmember and independentlyqualifies consecutive unprotected/bilateral-observed members; failedoriginal intervals also block overlappingmembers. No recursiveassociation/authority, fullparent angularnarrowing fixed-kmweathercounterexample holdsallsegments. Defaultwholegroupmode unchanged except fullparentweathercounterexample safeguard. EightfullRAW replays frozen raw-constellation-segments-nw-v2 and raw-constellation-segments-z9598-v1: NW>300km Z9591 strong72/13/24/0/0; Z9598 sector160–280>100km strong0/23/0. WholefieldZ9591 strong165/198/63/0/81; latter81notuserROI acceptance. 0948PNGinspected, remote shardsred/nearweatherorange; no productactions, enginebinding/independentweathertruth/Webpublicationnotdone. 273scopedtests pass. Goalactive, incomplete10:42/11:24 and broadfan/08:18/08:42cases remain.

### S constellation actual engine / writer integration, 2026-10-02

Defaultoff fragment_constellation_enabled wired after sourceledgerfreeze; onlystrong segmentproof participates candidate/geometry/actioncause, auditnoaction. Typed immutableRAW/nativeorder/moments/barrier proof uses SEGMENT_MODEuint8=1, writerindependent replay; mode/RAW/maskforgeryreject, orderrestorepass. 275relatedtests passed. Strictactualengine1024 target24additionalproposals/fullfield50 and0836 target23/fullfield83, originalsourceID/kindunchanged, serializedwriterpass. Receipts constellation-engine-1024-v1 and constellation-engine-0836-v1. 0948strictbaselinefailed; onlysourcefootprint rejectioncode4gatesold2→new4,243625–292375m; captured sourcefootprintSHA234400ec vs current8203ccad. Failedreceiptkept, no skip; currentbaseline mustrefreezebefore0948acceptance. Commitbaf1c56 afterindependentX9a90a78; no productionactivation/Webpublication. Goalactive; independentweathertruth, remainingcasecoverage and normalQC→Hybrid→images/Webacceptance incomplete.


### X完整对象漏检根因与v2扩宽分支，2026-10-02

- 本轮progress：4冻源8层完整RAW/oldQC/candidate/preview图渲染成功且SHA核验，实际观察4。render-v1 UID输出失败保留；v2按yons UID写目录成功。晚UTC05四S目录无已发布样本，不冒充独立天气真值。ZF505完整长条带残留因角宽随距离变化而固定边界超1.05足迹，源码/原生对象/图一致。
- 9a90a78已main push：默认关闭v2 expanding_fans_enabled、distinct version/schema、完整历史中心/扩宽/回缩判别，不改v1固定边界容差、天气/缺测/资源/来源/预算门。9新测试实际RED/GREEN，54形态/206整套通过。源SHA c2173ca5…。普通scp/mkdir源路径因root目录失败且无driver启动；通过现有Docker权限仅活动源目标写入，验证SHA后只读audit容器bind，不改四生产镜像。没有另开checkout。
- source2918816/paired2918817终态SOURCE_REPLAY_COMPLETED，20冻源492层+同8源配对72层全通过，资源0；旧v1 mask全保留，492层计数与原安装v1输出相同，额外10240（ZF50505 8452/70105 857/70205 931）。所有28输出/receipt/task SHA本地核对，摘要.build/xqc-polar-morphology/polar-morphology-expanding-*/verified-summary.json。新提名不是污染真值/正常发布，累计唯一1219层不变。
- 仍明显残留：ZF505cut4完整169.6km对象widthgrowth2.96/center1.48，另166.6km growth8/center2；cut5完整170km center0.49/narrowing3.07/growth0。不调高中心容差冒充通用解决；下一步完整边界锚定/持续核心和真实天气保护，补正常图件/UI。当前v2未生产启用、目标active、小时任务保持停止。
- 另新增可复验CLI --expanding-fans（显式v2，defaultv1）+3路由契约RED/GREEN，最新209套通过/ruff/diffcheck；不覆盖既有c95审计原证据。S并发dirty文件保留。

- 收尾：4a8a9f2 CLI push后活动scripts同步，脚本SHA01f397ff…，独立只读容器同ZF50505 source/paired两入口9+9层成功、逐层计数等于冻源wrapper输出，JSON/receipt本地SHA核验，不增唯一计数。四生产X Worker fresh healthy且容器真实模块2b27/v1，网络288690 unchanged。S并发baf1c56已先push，4a本次push显示baf→4a，未部署其代码。目标active，本轮无block；下一轮完整锚边/持续核心结构排查，不升全局容差。

### S current baseline refreeze and installed 105 candidate verification, 2026-10-02

09:48 oldfailure retained; refreeze_s_source_baseline.py only permits typed known rejectioncode drift, rejects any otherenginefield/source/action/dtype/newfield drift, verifiesalloriginalinputs unchanged. refrozen-current-source-0948-v1 SHA b70a6ce7555813ebe99f6dbd15b29569871d4e6cdc98d328cc43ba38d507825a; only4diagnostic changes. Strictcurrentengine constellation-engine-0948-refrozen-v1 passes72target/165fullfieldnewproposals, sourceidentityunchanged/writerpass. 277tests pass. 105 candidate s-fragment-constellation-baf1c56-candidate built immutable28modules SHAchecked, configSHAa267c8c4c44fb8f58ee38f72fef24ab80856bd5e963769bca821ce283deaeeb4; actualinstalledcandidateengine sameRAW72/165, strictfreshbaselinecompare,writerRAWreplay,defaultoff,auditnoaction,sourceIDsunchangedpass. Receipt constellation-image-test-receipt.txt. Twoinitialbuilderrors resolved by manifest-only compilation and syntaxfix; candidate is notactivated. Productionworkers1/2 stillv5running. Ownscript/tests/docs commit9fedfd8 pushed afterindependentXdocs21c3b8c. Goalactive: actualweatherholdouts, othermissedshapes, normalproduct/Webacceptance notfinished.

### S high-core lower-parent weather counterexample guard, 2026-10-02

New synthetic RED confirmed high45dBZ angularconstant core escaped low15dBZ fixed-km narrowing parent. Fix f579a7d freezescomplete10dBZparents for everyhighercontour, rejects broad/narrowing parentgeometry; parentweather/protectedmembers cannot authorize polarizationmissing cores or independentsegments. Two newcounterexamples GREEN;279relatedtests pass. EightoriginalRAW replay targetstrongZ9591=66/13/24/0/0,Z9598=0/23/0, withdrew6ofprior72in0948. Currentengine0948 strictbaseline/RAWwriter/sourceidentity pass66target/134allfieldadditionalproposals. 105 freshcandidate s-fragment-constellation-f579a7d-candidate configSHA05b6ccb0c9666545fc51f57cace2aa259fcb024c85ed28319e3ca1a7c79197f6: installed28modulehashes exact,realRAW66/134 same,writer/defaultoff/auditnoaction/sourceidentitypass; installedlowerparentweathercounterexamplealsoPASS. Receipt constellation-f579a7d-image-test-receipt.txt. Priorbafcandidateobsoleteforpromotion. No productionactivation/Webupdate; independentactualweatherholdouts,othermisses andnormalpublishedacceptance stillincomplete; goalactive.


### X v3完整原始锚边回放终态，2026-10-02

- 3059711/3059712终止SOURCE_REPLAY_COMPLETED，20源492层+同源8配对72层全部EVALUATED，v2资格mask保留且v2计数与实际前次输出逐层一致。28输出SHA核验；额外1452门仅ZF50505 cut4，不重复计数。v3渲染4层成功，实际观察505cut4仍有明显回缩条带残留；研究预览不是正常产品/天气真值。
- 219全XQC-v2测试通过，另正常QC集成测试通过action3/QC不显示/CR0/sourcekind0/RAW不变。生产仍v1、正常3站7体扫63层、真实UI5张，unique1219源层不增加。下一步完整原始持续核心与正常候选准备，不调大边界容差、不重启小时跟进、不启用可信融合。


### X原始稳定边界回缩扇形v4，2026-10-02，e2995f2已push

- 显式v4 pulsing_fans_enabled（须v2/v3父分支），保持初始边界1.05足迹/初始角宽下限两边量化容差/真正张开回缩和全部原始历史、双肩/缺测/天气/资源门，无固定站号方位。9形态+2CLI实际RED/GREEN，231整套通过，另正常v3/v4集成2项action3/sourcekind0/RAW不变/QC不显示/CR0通过。
- 模块3ca9d0df…，CLI83380395…；源3273741/配对3273742正式v2目录终止SOURCE_REPLAY_COMPLETED。492+同源72层全部EVALUATED/v3mask子集/逐层旧计数一致，冻源task/28输出SHA核验。新v4-only23222=ZF50505cut5 18960+ZF40205cut4 4262；其余0。不重复累计unique源1219层。402局部天气代理14→55冲突未获真值。
- 四层v4渲染成功，实际观察505cut4/5：cut5东南长扇形改善，西南短扇形/细径向及cut4仍有残留，未彻底解决。正常仍v1的3站7体扫63层/UI5。兼容v4镜像18c5ccff只换形态、794文件SHA不变，安装1080门烟测通过，尚未切换生产。下步剩余完整轨迹、跨层/时间支持与局部天气冲突复核，再冻源正常重算，不用预览代替网页验收。
- 同步非root权限失败后用Docker user0仅活动两源文件解决；初次启动引号/缺helper前置退出，未启动批次；正式完整helper上传后3273741/3273742各一次，均完成。实际MinIO557842075648字节/100052728 inode。小时任务不恢复、不启用业务可信融合/QPE/预报，goal active。

### S complete fan physical-window diagnosis, 2026-10-02

Read-only audit now routes constellation/whole-object/variable-width; same6frozen20260918volumes54REFcuts EVALUATED/resource0, RAW unchanged/weatherproxyoverlap0. Whole-object strong Z9591=100/3714/1965 Z9598=0/129436/0; variable=0/2702/2009 and0/28115/0; nominations not truth/publication. FullRAW/proposal whole-previews-v3 Z9598cut0/5 inspected: broadfansdetected but radialgapsremain. Dedicatedfan-diagnosis-v4 sameRAW/all9cutcounts unchanged exports support/windows/scale/aspect: cut5 59.71deg fan span140km/support100km/5of5confirmed/shoulder.9675 solelyheld sixwindows at20km scale; cut4 8.95deg span139.75km/support89.25km/5of5confirmed samecause. cut6 2.01deg span152.5km/support15.25km held20kmsupport notwindowcount. Next implement independentlymeasuredfixedphysicaldistanceevidence oncompleteRAWobject preservingweather/gaps/per-target checks, no six→five tuning. ThreeauditcontractsPASS. Own script/test/doc dirtyuncommitted; HEADf34c847 unchanged but concurrentXfileschanged duringpreflight, so no commit/push thisturn. Production untouched, goalactive.

### S physical-distance fan evidence implemented, 2026-10-02

Researchoptin morphology_objects.detect/validate physical_windows=True; explicitv2report/defaultv1 unchanged. CompleteRAWanchors inindependent10kmbins each>=500mactualsupport; fan100kmspan/20kmsupport/sixbins/bilateral80percentknownclear/confirmed80percent plusalllegacyedge/own-targetweather/barrierbounds. No engine/writerproofactivation. Tests3gate-resolutions/five20km→ten10kmpositive/unknown/weather/convergingfixed-kmweather/sparsealignedfan. Radialrevision suitepass beforelastsparsetest, final24morphology+audittestsPASS. Actual6vol54cutphysical-windows-v2 sourceauditcomplete/resources0/sameRAW/weatherproxyunchanged;5volunchanged, Z9598UTC02 129436→132982 (cut0+748/2+301/4+1694/5+803), weatherproxyoverlap0. Hashverified11PNGs, cut5inspected broadSouthfanimproved butrings/radialsremain. No truth/publicationclaim. HEADadvancedf34→e9ada37duringwork(concurrentX); own5filesremainuncommitted. Goalactive; nextversionedengine/writer/weatherholdout andnormalproductacceptance; donotclaimcomplete/deployed.

### S physical-window engine/writer and installed candidate, 2026-10-02

0b417e3b791ebb81433282535ccb364236ae26eb committed/pushed7ownfiles aftere9ada37; remoteSHAverified,289scopedtestsPASS, ghCIlistempty(noCIclaim). Defaultoff whole_object_physical_windows_enabled requireswholeparent, detection/evidence sameflag; typeduniformVERSION_CODE2 mapswriterRAW/nativeorder replayphysicalTrue,1legacy,unsupported/mixedversionreject. Engineaudit/action/sourceidentity and v1/v2writer tests pass. 105candidate s-physical-windows-0b417e3-candidate actual28committedmodulehasheschecked, imageconfigSHA bc07de77f4389323c19ad34cb4a795f4eb4db1d82492b03e63c264095fec28b5. Installedreal0948snapshotSHA b70a6ce7555813ebe99f6dbd15b29569871d4e6cdc98d328cc43ba38d507825a enginelegacy260/candidate260, v2writerreplay/raw/sourceunchanged/auditnoactionPASS. Receipt .build/s-bounded-radial-20261001-release/physical-windows-0b417e3-image-test-receipt.json. Initialimagepy_compilecachepermissionfailure resolvedinarchiveDockerfile bycompilewithoutwritingcache, finalbuildPASS. Candidate notactivated/Webnotupdated. Remaining independentweatherholdout/targetresiduals andnormalpipeline publication. Goalactive.


### X v5完整窄缝外轮廓与历史弃权根因，2026-10-02

e9ada37v5/6380c9f预算审计均main push后同步。20源492层+同8配对72层终态，v4mask保留，v5-only74350（50505 69983/70105 2193/70205 2174），unique1219不增。paired-v2审计a1bc1909全部8/72成功且SHA核验；50505 cut0/2/4/5实际历史detail X evidence record budget exceeded，旧available0、新RAW252147/233726/224579/187220，未来预算null。当前生产core已有无损压缩，需正常重算验证，不能冒称算法成功。4完整PNG已SHA核验实际观察，505cut5西南改善但细径向及cut4西/东南仍残留，702cut5长细径向被覆盖；都是研究预览不是UI验收。兼容v5镜像ec887aacc8只改形态，其余794文件不变，安装603观测门烟测通过RAW/action3/sourcekind0/缺测不可用/QCnan/CR0。四Xproduction仍v1healthy/network2886；v5尚未激活。installed core探针105grouped-core-v1针对50505cut4/5正在运行，本地exec66158可续读；无邻层context/无发布，不能替正常任务。goalactive/小时automation不恢复。下一步实际core证据后冻源正常重算及地图复核，再逐完整轨迹解释残留。S并发PROJECT_MEMORY本地dirty保留。

候选配置已冻结于grouped-release/child-network.json，SHA2e54f00c0e7c238c66f12762801f0771b1b04e1b2a93ee335789ca9f87ddebd4，只改release_id和ZF505/701/702形态v5开关/身份；保留其他噪声/源证据/动作预算/天气政策，ZF402仍未启用。未替换现场网络或发布通道。当前installed真实core探针约747.5MiB/4GiB、100%单核运行，尚未完成，不以启动称通过。105run-grouped-core.py/本地exec66158续读，结果grouped-core-v1/zf505-05/core-receipt.json待生成。相关形态+审计测试本轮104项通过，既有NumPy警告1；正常大体扫需等实际结果。

续证：installed真实core探针已exit0，auditSHA c186dc3ec34635b8258f8e26e03cbd4c9fa19bb969bad1884bd7a6807cabd4c9，本地receipt/SHA核验。50505 cut4 DEGRADED_MORPHOLOGY_ACTION_BUDGET，提名156506/形态102741，证据1650329字节；cut5 ACTION_BUDGET_ABSTAINED，提名16434/形态102912，证据1448026字节；均低于原4194304字节cap，RAW不变。证明当前core确实越过旧证据容量弃权，但动作预算仍阻止预览成为实际完整清除；未提供邻层context/未正常发布。exec66158已完成，不能再称运行中。下一步追踪原基线/新增形态候选比例及预算降级语义，保留原告警/cap，随后正常冻源重算和UI核验。

### S v6 activated and background refresh actually running, 2026-10-02

7c3e357b5ce7788241e94b10e82767c64620e72b pushedafterconcurrentX6380c9f,remoteSHAverified: reusable audit_s_physical_windows.py, v6profile/compose/docs. Actual8completeWebnativeengine comparisons: physical-onlyextra0/0/0/0/0/0/0/79; reviewedconstellation+physicalextra134/55/50/0/1/0/39/134=413. Suppliedweather/conflictprotected2141/overlap0, RAW/sourceIDs unchanged,writerPASS. This is protection-proxy replay,notindependentweatherttruth. 1042/0818 coverageunchanged. Installedcombined0948 legacy260→394 matchinglocal;28moduleSHA/v2writer/auditPASS. Newprofilevalidated91d7f00a5b26bba86f4f3af7cd068dafbe16757a9e2116cace9184d73d0b46c4 (distinctreviewchild retainsrecognizedpipeline7.3.9,7.3.10inventedversionrejectedbeforeactivation).

105twoSprimaryworkers now s-physical-windows-0b417e3-candidate healthy; activeparentprofileexactv6SHAconfirmed. Active28Sradialmodules and three scripts/profile/compose synced fromcommitted7c3e357 inexistingROOTonly. Guardoldv5profile/pendingQC0/DONEpreviousbatch; oldprofilebackup .build/s-bounded-radial-20261001-release/physical-v6-parent.yaml rollbackretained. FirsthelperargumentE2BIG beforemutationrolledbackv5healthy; switchedstdinhelper andsuccessfulactivation. Firstchrootlaunchbusfailure resolvedhostPID/network; firstprioritizewrongUTC00:48absentplan endedbeforejobs; corrected09:48CST→01:48UTC, samefrozen32scanplan.

Authoritativeunit rainpulse-s-physical-constellation-v6-refresh.service MainPID3730153 active/running; state .build/s-physical-constellation-20261002-v6-refresh/state.json RUNNING,current2026-08-28T01:48:00Z,completed[],v6SHAcorrect; firstQCjob ae9e3aea-9cfd-5424-b451-194dea33ccba actualDB RUNNING. Sequential8slots/32scans QC→grid→mosaic→QPE→diagnostics. Do notrestartonpolltimeout orclaimWebupdateduntilpublisheddiagnosticsverified. CurrentMainbranchonlyPROJECT_MEMORYdirty.

CI36933310335 for7c3e357 FAILED,notallgreen: logshowsoldparameteridentityassertionsseveralQCchecks andlintservices/control/internal/operations/basemap_config.go; exactrelationtoSflagnotyetdiagnosed. Do notclaimunrelatedwithoutbaselinecomparison. ScopedS289passpreviouscommit,actualnewprofileand8engine/writeproofpass. Goalactive: normalpublishedWebacceptance, residual1042/0818/broadfans,independentweathertruth andCIfollowup remain.

### S v6 batch progressing; legacy CI identity guards corrected, 2026-10-02

Actualsameunit3730153 staysactive/running, statev6SHAcorrect/noerror/current09:48CST. First3stations QC+gridfinished, fourthscan06d61256-95eb-507c-9b62-04166b964b9a QCsubmitted8c572d6a-c37a-5be5-8963-1261f898cadf; thirdgrid4a74fc7d-74df-5e9b-b116-c5f1294d0ee7. No09:48mosaic/imagepublicationclaimed yet. Do not restartbatch; inspectsameunit/DBjobs.

b7d3cd193ec4f68fcc02b93cb64084ec9e6e7fab pushedafterconcurrenta8e1638,remoteSHAverified. Onlytwolegacyparameteridentitytests exclude3assertedNonefieldsreview_extension_version/nonprecip_review/volume_review thenindependentlyreconstruct/pinoldV1/V3hashes; runtimeprofile.py identicalbeforeSbranch at6380c9f,so noSruntimehashchangeforthisfix. Two targetedtests2PASS. Broader66tests40PASS/26FAILallreportedmissingarm_pyart inlocalvenv,notgreen. Puregofmtspacingbasemap_config.go andownSdocsupdate included4files. Noalgorithm/imageupdateforthistest-onlyfix,105v6continues. RemainingCIperformance-CDsourcesmetadatadifferenceuninvestigated,notallgreen;newSHAghrunlistempty atcheck. Goalactive mainmorphology/publication/residuals notfinished.

### S actual v6 published residual diagnosis, 2026-10-02

Fresh105unit active; completed09:48/current08:18, no restart. Actual09:48diagnostic7bcc8316 SUCCEEDED/v6inputSHA matched; native published receipt published-v6-0948-native-v2.json97targets/66isolated/31renderable. FullRAW shape audit covers31wholecandidate/0strong:27shortfan33.5km/support15km/width1.99/shoulders.97297,4short13.25km/support6.25km/aspect.825/shoulders.84; insufficientgeometry. Constellation11candidate/0strong includes near-weather76–82km parents andfar355–459km, fullparentgeometry/weatherholds. Strictshort29.5km segment also bilateral/centerholds. Next originalparent/physicaldistance separation plusstrictshortqualification, notglobal lengthlowering orweather-vetoremoval. Read-only reusableaudit_s_published_residual_shapes.py and nativeper-gatereceiptexport added; actualPNGfetch helper boundexactstation/cut/asset/generation.15contracttestsPASS, actualRAW/QCPNGs inspected. Ownhelpers/docsuncommitted; preserve concurrentXdirty. Goalactive residuals/independentweathertruth/publication all8notcomplete.

S RAWdistancepartition research nowimplemented optinreportonly/defaultmasksunchanged: extendmember bycompletelow10dBZparentinterval before20kmgap grouping, never split sharedweak/weatherbridge; missinggapnotdryevidence. Actual0948partitionsv2separatesnearparent362/far354–385km, farparentgeometryholdgone butshortbilateral/center(.257–.329deg)stillfails, remote456–459kmsinglememberinsufficientsupport. Noextraactions/noactivation; nextnativeboundaryquantization+measuredwindowshortproof, nottolerancefit. Relatedsuite303PASS plus6receipt-negativePASS. OwnSfilescommitpreflightHEADb7d3cd1/emptyindex; concurrentXdirtypreserved. Goalactive.

Sshortedgecentercorrection:2REDregressionsthenGREEN, nowshortreportusescompleteoriginalleft/rightcellmidpoint insteadofpopulationbearing; validatescompletefinitewidthcoherenthistory, no tolerancechange. Actual0948far354–385km drift0 instead.257–.329; remainsbilateralheld. Explicitwindow/band fullRAWreplaysboth0additionalstrong, notpromoted.311suitePASS plusCLIdependencytestPASS.8completeRAWsnapshots arraysidentical vsGit-loaded e074module(no sourcecopy/checkout), RAWunchanged receipt edge-center-eight-array-invariance-v1.json.105unit3730153active:0948/0818DONE,current0836. Priorf3CI36937869862opensourcetestssuccess/overallFAILperfmetadataand4910lint, logretained. Goalactive; own5Sfilescommitpreflightcurrentc4fc70c, preserveconcurrentXdirty/memory.


### X v5正常终态与v6完整分支对象，2026-10-02

本轮progress非完成。v5兼容ec887aacc8已实际部署四X ops，网络2e54f00c/fp c23048cc，仅505/701/702形态v5，S不涉及。794其他模块含pipeline/decoder逐SHA不变，promotion.json保留。installed完整x_qc复核纠正前次推断：动作预算限制确认拒绝但budgetwithheld仍action3/QCnan/CR0；505cut4/5实际选中可见0/CR0，RAW不变，audit ad10b10a。不得继续称预算阻止显示清除。

六正常冻源全部SUCCEEDED、finisher3812107 NORMAL_PUBLICATION_AUDITED且结束。54层RAW/坐标/时间/4PNG及候选可见0/CR0全部核验，50机械通过，505cut2 SOURCE_INCOMPLETE/model-trial cap、cut4/5预算和702cut7预算告警保留。正常累计3站13唯一体扫117层，不等于天气完成。新70105网页实际截图1张（RAW12:57:45/cut0、新task7fa1aa0e/attempt ea21449b），累计6；702cut7截图/getTab恢复超时，无新UI证明。唯一冻源仍22站46体扫1219层。小时automation未恢复。

junction探针aeb3fc05确认505cut5父336–354在180km末端split336–337/340–353，多子关联导致130/160km历史被ambiguous全否决。e07493e默认关v6 branching_envelopes显式schema/CLI：全原始相邻窗口连通图，不继承资格，完整组件外轮廓/双肩/天气/gap/历史重新验收。实际RED/GREEN跨北缝/分辨率/split-join/固定公里宽天气/保护/正常action3等。首次source3993700/paired3993701终态20/8但505cut0/2 work资源弃权；v1失败保留。

c4fc70c修复重复全层signal：复用单节点不可变原始测量、多节点只量rows×cols。冻结9M限e074内存隔离旧fixture实际RED，当前GREEN，同成员16340/work10244704→7565313。262全XQC-v2PASS/NumPywarning1/ruff/diffcheck通过，main push后仅同步两活动源供RO，不改生产v5。模块7b49db31/审计63de4fe7。

source4040542/paired4040543均终止SOURCE_REPLAY_COMPLETED，492源/同72配对全EVALUATED/资源0/v5逐门子集保留，28输出/taskSHA本地核验。v6-only151238（配对101718是同源不加unique），跨103/401/402/505/701/702。40202cut4 localproxy253、50505cut6 40、70205cut7 49冲突保留，不独立真值。当前v5正常505cut4/5四栏预览PNG SHA核验实际观察，新增可见覆盖15375/13；cut4涉及团块附近回波，须天气反例/独立S对照，不直接启用v6。source模块505cut2真实SHA证据PARTIAL_RESOURCE_LIMIT/failed_module fan/reason X source model-trial budget exceeded/source_gates86932，不能提预算或冒称完整。下一步天气叠加反例+源trial复用根因及候选正常图件/UI；goalactive，所有上述driver终态不再重启。S并发脏文件保留，Memory仅local dirty。

Sbilateralrootcauseactual0948:3far targetgates opponentDBZHmissing/SNR4or9 measurednonquiet, notdry/unknown. Addedboundedpointdiagnostics+researchshortmeasuredsubset mask requiresoriginalpartition and1/2/5kmwindowproof, alloriginalweather/geometrykept, onlyownconfirmedgates contribute>=4members/5kmsupport/4windows; noengine/profile/writeractionauthority/defaultarraysunchanged. Actual0948shortsubsetv7=83researchgates/only2of31publishedtargets/protected0,29remain.8fullRAW replay83/0/0/0/0/0/86/38, weather/protectedoverlap0/rawandproductionarraysunchanged; nottruth/newisolation.315suitePASS plusnewCLItestPASS/34targetedPASS. Nextone-time frozenlowerparent weakfootprint validation, norecursivegrowth. Own5Sfiles pendingcommit; goalactive.

Sone-hopweakfootprint implementedresearchonly: low10dBZcompleteparentindicesfrozen beforehighcoregroup, onlyoriginalparentIDsfromqualifiedshortpartitions, independentlyweakown6dBcontrast/actualquietSNR+1/2/5kmwindow/weather/barriers, no newanchors/recursivegrowth. NewSHORT_PARENT_RESEARCH_MASK/defaultproductionandshortarraysunchanged. Actual0948published-boundv8:91researchgates/9of31residualtargets(vs2prior;7more)/22remain/protected0, notpublishedisolations. Reusableaudit_s_short_footprints.py8fullRAW combined91/0/0/0/0/0/86/38; parentonly8/0/0/0/0/0/0/0. Finalbarred-v2 addsRV2BARREDplateau/nativebarriers unchangedcounts/weatherprotected0/allRAWexistingarraysunchanged.320testsPASS, owntestsunknown/weakowncontrast/singlehopIDs/orphanweatherpassed.105unit3730153activecompleted0948/0818/0836/0842,current1018. Noactivation; nextremainingoriginalfootprintscope/proofvalidation/normalpublication; goalactive.

### X v7 compact shape ambiguity research, 2026-10-02

8f68012769807741f25f07bc9092b2a7e0a77891 own7files main committed/pushed/remoteSHA matched; S dirty files preserved. Actual v6 attached compact core synthetic selected587/589, RED/GREEN v7 diagnostics retain core while external clear radial forms remain selected. Explicitv7 compact_counterexamples defaultfalse requiresbranch graph; complete35/25/15dBZ native components/physicalXY/no filling/gapbarriers/measurednorthseam, descending highcore no lower-parent protection inheritance. Weak mixed components remain ambiguity, not weathertruth/source authority. Native uint8 XQC_MORPHOLOGY_COUNTEREXAMPLE_MASK defaultzero, normalcore test RAW/source/hardweather unchanged.277XQC testsPASS/1existingNumPyABIwarning,ruff/diffcheckPASS; noCIclaim.

Module b4f1d1d217b8bcc8fa46fb14985c3a9dacab313f2f61266c511027feb32238c7, audit1b1ad008f6f4b02586218b753e73c53c511a7c5fb225e4c42b8031f45871a1c8, compactwrapperdebf2813301b15a8597b261b8f989fb3eadddcc791d3514df4712a92f248c8da. Afterpush onlyactiveROmodule/script synced, productioninstalledv5ec887/network2e54 retained. 105ROsource driver87362/paired87363 launch actual compact-source-v1/compact-paired-v1; latest11source/2paired complete/resource0, stillRUNNING. Comparison assertsv7mask==v6mask minusoriginalcompactambiguitymask, no deletion outsideoldqualification. Rendering actualcurrentv5normal50505cut4/5 driver99537 compact-render-v1 started, notcompleted/accepted. Exacttask/hash receipts preserved; do notrestartonpolltimeout. goalactive; independentweather/sourcefantrialcap/normalpromotion/UI stillpending. Noautomationrestore/secrets. MinIOvolumehoststat permissionerrorneedsdockerdf; no diskcleanup performed.

Xv7续证：87362/87363/99537均已终态，不再重启。source20/492+paired8/72所有exit0/resource0，28输出SHA核验，source完整v6候选629073→v7610407，撤回原紧凑歧义18666；paired16983同源子集不加unique。精确v7==v6minuscompact invariant逐层通过，验证摘要保留compact-{source,paired}-v1/verified-summary.json。MinIO真实ROdockerstat可用548811935744bytes/99873884inode，高于保护线，hostsys盘inode仅576083不等于MinIO实际盘。

真实当前v5normal50505 cut4/5五栏PNG SHA核验并实际观察；cut4candidate122275/compact481，NE团块附近仍被选；cut5candidate114434/compact0。明确剩余真实反例，不能因synthetic通过启用v7/冒称天气验收。1a7b54d docs记录终态和限制（push待/随后核验），下一步完整原始contour连接/径向与团块重叠测量，不能固定站点ROI成为算法例外/填洞/猜weather真值。production仍v5，sourcefantrialcap/实际正常UI仍未完成，goalactive。


S complete-object geometry shadow (2026-10-02): independent repeated-transverse-shard hypothesis >=6 original members/>=4 frozen low parents/20km span/5km support, complete edge center<=.1beam/width drift<=.25beam; actual flank coverage>=.9, nonquiet never relabelled dry. All parent/weather/barriers retained, one-hop frozen footprint only, no engine/profile/writer authority. Exact09:48published31visible targets: geometry27 vs priorresearch9;4shortisolated held. Eight completeRAW geometry140/0/0/52/0/0/96/42, incremental49/0/0/52/0/0/10/4, priorarrays/RAWunchanged and protectionproxyoverlap0; notindependentweathertruth. Reusable scripts geometry flag/preview forbid engine routing; actualPNG0948shadow inspected.328scopedtestsPASS plus2CLIactionroutingchecksPASS. Newhypothesis still depends on original longgroup discovery, lacks standalone shortgroups/completewidefans/independentweathertruth, no productionactivation. Fresh105actualunit3730153active/running completed0948/0818/0836/0842/1018,current1024; do notrestart. Goalactive.


S standalone geometry/quantized-weather ambiguity: explicit geometry_evidence now discovers six-member20–60km groups without unrelated distantmembers, separateledger/default IDs/allarrays preserved; originalparent/weather/barrier/unknown retained andworkcharged. Standalonepositive actualREDthenGREEN; resolvedfixed3kmweathercounterexample reachesassessment/rejected; five standalone negatives.337scopePASS then55targetedafterbudget/defaultdetail adjustment.8finalRAW standalonegroups0/0/1/1/5/4/11/17 butexistinggeometrycounts140/0/0/52/0/0/96/42 unchanged; all21qualified hypotheses (inclduplicategroups) compatiblewithfixedphysicalwidth afternativebeamquantization, independentweather evidence required/notproductioneligible. SourceSHA-boundfinalreceipt standalone-geometry-eight-final-v4.json. Current1042samevolume positiveaudit36target/lowmeasured3/high0/two-typedradialsupport0, notweatherabsence. Fresh105unit3730153active completed7slots/current1124/noerror.11976CIoverallFAILED; opensourceQC/residual/papercomparison/crossradar/RFI/build jobsSUCCESS, lint/test/perfCD/verifyFAIL, reasons stillinspect. Noactivation; goalactive nextpositiveevidence/widefan coverage.

### X v8 direction proof and full frozen corpus, 2026-10-02

前轮progress；本轮继续progress非完成。Root real50505cut4 complete25dBZ contour2863 gates/29rays/19.575km/rangegrowth1.246/axisratio3.229, radialvariance23.719M vs transverse100.820M m²。v7轴长宽阈值漏保护横向展开体，低15dBZ parent含长RF/rangegrowth9.685不可继承保护。两regressions真实RED后v8GREEN，物理中心bearingresolved后横向variance>=径向variance允许非径向歧义，不提高原compactratio3/跨度/范围比/资源cap，不猜weather/source单位。v8defaultoff强依赖v7compact/branching，schema/CLI一致，northseam/粗细分辨率/高仰角/纯line-fan-broken/radiallongbody门控全285XQC测试PASS/1已有NumPyABI警告，ruff/diffPASS。a3581a4519270e38654ee4359baa74176738dae4 own6files mainpush/remoteSHA核验；S并发dirty保留（S随后11976b9）。模块f6a7558a97f5dad84f65fd73f2bb1a2c38eba251c44f4ba86b005291d44df02b，审计fa522efd3eaa821b35eb0db5b32697f03cebfa88213b4cca27ac9d3fb7638a27，wrappera4faf0f5ce56987ef165a919b5fff6127d9e5d60c1ef9d10d267d4b74f25bfd3。

20源492层/8paired72层v8终态46reportSHA/taskSHA核验（实际上20+8=28 reports，勿误记46），v6maskminuscounter精确保留/v7reintroduced0，新增withdraw只50505cut4=2863，source607544/paired559707同源不加unique。v8两个current-v5-normal-bound全幅5栏PNG SHA核验实际观察，cut4候选119412/compact3344/当前v5可见选择12512；cut5候选114434/compact0/可见选择13。lower mixed weather/RF仍非独立真值。

只两模块core/shape新候选image rainpulse-cpu-worker:xqc-transverse-a3581a4-candidate-mb，ID sha256:0957efe71f8801e8a7047e6ab44a16202eee1ce64d93543559344616516f5218。parentinstalledv5ec887不变，793otherPython逐SHA同，包括decoder/pipeline；core committed SHA46bd73b501f0eda2219b71ab8f63536c8a651b738dcaebb3f15b4da8d14e57ea。首verifyhelpermask定义顺序NameError在algo前失败，v1保留，v2修复烟测完整x_qc PASS：1373 transversebody保留，externalradialaction3/QCnan/CR0，RAW不变/sourcekind0/hardweather0/operationalfalse。receipt transverse-image-v2/image-verification.json本地核验。未替换生产四X image/network2e54，不更新UI/normalpublication。

installed fullactualx_qc driver302206 transverse-pipeline-v1已终态exit0，本地audit SHA核验。505cut4 DEGRADED_MORPHOLOGY_ACTION_BUDGET，shape119412/compact3344、compactQCfinite2863、selectedQCvisible0/CR0、证据1655679<4194304；cut5 ACTION_BUDGET_ABSTAINED shape114434/counter0/selectedQCvisible0/CR0/evidence1452549。原RAW未改；无邻层context/对象写入/正常发布，不称UI验收。driver308898 source-trials-v1同终态exit0/cut2 DEGRADED_SOURCE_RESOURCE_LIMIT：500001trial=605radial+85440block+413956fan，其中eligible82775/392844；廉价geometry失败仅数万，不能只搬trial或提高cap冒称完成。

进一步只读training-keys driver369635 source-training-keys-v1已终止（remoteprogress有完整1层）；同geometryeligible训练集合按原detector调用+rawray+membersSHA计重复：block26609/unique56166，fan158505/unique234339，共185114重复。未改任何模型动作；下一步精确per-ray/per-detector训练拟合复用的RED/GREEN，训练key不能跨target/guard变化泄漏，coverage between outside分母仍target-dependent须在缓存外复验；RAW/cfg/responsequantile/domain要冻结，缓存按原32MiBsummary headroom有界/LRU，源模型50k/试算500kcap保持，fitcache目标条件不得继承。尚未实现优化。

扩大全部原冻源22站46unique体扫/1219层，不新挑样：407569 driver transverse-full-corpus-v1实际4worker启动，选原cross20/expanded9/legacy11/normal6/zf505normal1元数据去重47→46，protocol先冻，不新增unique coverage。fresh105快照 SOURCE_REPLAY_COMPLETED/46tasks/1219cuts/driver不存在，所有exit0/noresource/no v7reintroduced，terminal不再重启；本地SCP全报告尚在取回exec60482已终态，当前full corpus下载exec新604? 最新handle工具返回需续读（本轮最后full fetch）。要逐46 outputSHA对protocol taskSHA/sourceURI/SHA核验后记录最终总数；启动不称验收。3ce7fba2a773fc662422b24abf7e35f357c7467d docs mainpush核验。goalactive，不恢复小时automation，不保存凭据。

X本轮最终续证：fullcorpus下载42771已终态，46报告SHA+proto taskSHA已本地逐项核验，22站46体扫1219层全部EVALUATED/资源0/v7reintroduced0。总v6提名2165502/v72015500/v82012637，只新增撤回50505cut4 2863；protoSHA c26cca2749709904396c519e63284937e7710d337e3874394a3d91ee2ca89629，verified-summary.json已保存，绝非独立天气/污染清除统计。所有X本轮driver407569/302206/308898/369635均终态，不再重启；training-keys-v1 auditSHA已核验，digest key按同detector-call+原ray+实际members计185114重复（targetcoverage分母不能继承）。d586bfb docs commit/push待本轮tool结尾确认，之前3ce7fba remoteSHA核验。下一步SourceStatistics/source_blocks每原ray同调用的有界训练fit缓存RED/GREEN，保留原target和guard排除、逐target coverage/acceptance，不能缓存整条target动作或跨fan/quantile/domain/CUT/cfg。source trials/models caps不变，summary32MiB应预留现有workspace并有界/LRU，消除重复polyfit/percentile计算后实际cut2高预算reference等价和原500k完整终态再正常候选重算/UI。生产仍v5ec887/network2e54；v8候选image0957只构建/实际烟测+source2cuts链路核验，未激活。

### S published wide-fan diagnosis, 2026-10-02

Read-only actual v6 QC audits completed: Z9598 0818 target2094/visible2094;0842 target781/visible728, both stored parameters match active v6 and RAW unchanged. Reusable published residual audit adds opt-in --variable-objects, full RAW detection before bound published selection, no engine/actions. Whole vs variable candidate/strong:0818 2082/0 vs2093/0;0842 579/0 vs727/2 (research only). Fixed objects short-history rejection; variable full connected graphs merge near clutter/forks with radial branches (0818 largest2.125–460.125km,75479gates,width.975–50.325deg,target946). Original barriers/weather must not be dropped. Next full original edge multi-hypothesis fork tracking, preserving low-parent/weather history; not yet implemented or deployed. Evidence published-v6-z9598-{0818,0842}-{fan,variable}-v1.json, private .build only. Own script/test/docs dirty, unrelated work preserved. Goal active.

### S frozen boundary hypotheses prototype, 2026-10-02

Implemented opt-in boundary_hypotheses in variable_morphology with new branch_boundaries helper. Seed originalrun fixededges, full split/merge window history, >=6 windows/100km/20km support/.8 fullhistorymatch; actual bilateral evidence, complete corridor weather/barriers and full10dBZ lowerparent weather/barrier/narrowing guard. Separate BOUNDARY_HYPOTHESIS_MASK/BOUNDARY_QUALIFIED_RESEARCH_MASK; no engine/writer/production activation, old arrays unchanged. Initial real0818/0842 resource failures fixed using original-boundary vector index/repeated-window prefilter/cache, budget not raised. Final actualtargetcandidate1502/546, qualified0/0, receipts published-v6-z9598-{0818,0842}-boundaries-v3.json bind module/helperSHAs. All8RAW invariance success, existing fields/raw identical; full-layer researchgates0/0/0/0/0/0/0/178 (0842 outside selected residual ROI), no barred/weatherproxy overlap; branch-boundaries-eight-invariance-v1.json.347 scopedtests exit0, helperruffPASS. Own code/test/script/docs uncommitted; no production delta. Goal active: fullvariableboundary/weather ambiguity and meaningful extra isolation/actualpublication pending.

### X reference-fit cache continuation, 2026-10-02

cac7a4a22d4892ef8e2570dc5a8849a94a9ea418 own5files main committed/pushed/ls-remote verified; concurrent S dirty and local-only MEMORY preserved. Per-call/per-RAW-ray bounded scalar ReferenceFits implemented. Actual post-target/guard member bytes key; target coverage/admission recomputed, domain/quantile/fan/config/cut cannot cross scope. Conservative768+key bytes/LRU inside originalsummary headroom; production trial500k/model50k unchanged. RED17budget old18th trial failure; GREENsame781gates/13records. Five tests compare frozen old Git-module in RAM across modes/quantiles/domains/protected/changedtargets/twoRAWrays/guard and test zeroheadroom23trials, eviction/failedfits/guard-beforefit.290XQC testsPASS/one existingNumPyABIwarning, scopedRuff/diffPASS. CI36948865619 completedfailure; not allCIgreen, independentfail details pending.

New candidate only image rainpulse-cpu-worker:xqc-reference-fit-cac7a4a-candidate-mb IDsha256:c2afb43ac626fa5a3e0486e8cb43b15e607c44320a3fbe1681d2cd89a718cb5d, parentv8 image0957. Immutable Git source tar in RAM; initial FROMbare-sha build failed externalregistry lookup, fixed to locally SHA-verified parenttag; no production mutation. Imageverification 3changedfiles source_blocks/source_summary/reference_fit,793 othermodules unchanged; sourceSHA ad4777fd25cbc78179fdec1d43e19b2d492e9a5cfdab19284ea05d3495861e9c / 5b4f188b3d0ec7f8b20104296acb4a514b3c4c2994a3cd07cd37b436a9634504 / c3c576994ca32c2b0a1576386ddddf1f93a8210df14900516ab40ba7a4e4b5e6. Receipt remote .build/xqc-acceptance-20261001/reference-fit-image-verification.json; local build-reference-fit-image.py + source receipt.

Two actual RO installed fullx_qc cut2 comparisons now RUNNING, driverPIDs622324candidate/622325reference. Output polar-morphology-reference-fit-{candidate,reference}-v1/zf505-05; launch reference-fit-launch-v1/launch.json. Candidate original500k budget; oldv8 reference2M onlyRO validation, never production cap. Both same frozen zf50505 source/RAW task; immutable images pinned;2cpu/4G each, MemAvailable29934260KiB launch; no secrets persisted/RAWwriting/normalpublication. Probe hashes all native arrays and retainsSourceStats receipts without frameinspection fitting overhead. driverSHA6abf384785852a9bb56ab03e3bb65a92a28383cf46f4ccd788aadc0b6c7e749f; probeSHA7dd065971d303e1618c3f63bdad32fc7a43087dc1d8dcb2bc2d710e357660bfb. Latest freshcheck bothPIDs present/progress absent/logempty, do not restart merelynooutput. Next wait terminal, receipt/outputSHA verify, compare arrays and model evidence ignoring expectedwork/budget parameterSHA differences; ensure cap restored/fullstatus noSOURCE_RESOURCE_LIMIT. If failed fix, ifequal expand relevant source cuts then normalcandidate plan+publication+actualUI. Production remainsv5ec887/network2e54, hourlyautomationpaused, goalactive notcomplete.

X cache actualcomparison bothPIDs622324/622325 nowterminal(exit0), do not restart. Both outputs downloaded/local receipt auditSHA/taskSHA/probeSHA checked, all74nativearraySHA exactlyequal candidate500k vs oldRO2Mreference. candidate347720trials/reference575242,227522cachehits/32068modelrecords both; statusEVALUATED (priorcut2SOURCE_RESOURCE_LIMIT resolved). peakcache1715600+workspace1333944<32MiB,evidence3963736<4194304;RAWunchanged,selectedQCvisible0/CR0,withheld140723. Summary .build/xqc-polar-morphology/reference-fit-actual-verification.json. Candidateimage verified3changed/793unchanged no productionactivation. No fullactualmodel-record-content equality claim (array hashes+recordcounts real; complete model-record equality coveredsynthetics). CIcac7a4a36948865619 failedtest/lint/performanceCDsourcesmetadata; build and independentQCpaper/opensource/residual/RFI/crossradar checks success. Next broadernative sourcecuts + normalcandidate lifecycle/publication/UI andindependentweather/mixedambiguity. Goalactive progress, no hourlyrestart.

### S frozen enclosed-branch research committed, 2026-10-02

5be9f6fe3342b3dca8afa38c8d95e395a56db58f committed/pushed main; exact remoteSHA verified after parent b1bedb6 X-doc commit (concurrent cac7a4a/d586bfb X-only history inspected). Six ownS files only, memory/private snapshots/unrelatedWIP excluded. Added fullRAW native run membership and post-detection actual-target diagnosis: all actual0818/0842 target hypotheses fail full-boundary stability. New enclosed_branch_hypotheses opt-in measures original child runs inside immutable seed exterior; crossing runs not clipped, measured internal paths required, actual nonquiet SNR coverage notdry and never DBZHfill. Parent narrowing corrected to original per-window whole outer envelope rather than individual child widths (positive split test RED before fix, now GREEN); complete lower10 weather/barrier/narrowing guards retained. Tests355 scoped exit0, helperruffPASS, CLIhelp and full2 real invocation inspected. Final actualcandidate1511/575, research-qualified0/0; receipts published-v6-z9598-{0818,0842}-enclosed-v3.json. All8 completeRAW oldarrays identical/RAW unchanged/no protected or weatherproxy overlap, fullresearch counts0/0/0/0/0/0/0/178 (lastoutside actualtargetROI); enclosed-boundaries-eight-invariance-v1.json. No new production action/deployment/Web change. Goalactive: real varying outer boundaries, independent weather discrimination, meaningful residual isolation and normalpublication remain. CI freshly queried, not claimed green.

### X v8+reference fit normal rollout, 2026-10-02

Previousturn progress; currentturn fullcomparison/rollout progress. Driver709030 terminal6jobs(3stations×oldreference/candidate),27unique nativecuts, remote and local verifier PASS.2044allnativearraySHAs match; fullmodule modelrecords canonical SHA match stripping ONLYwork counters. trialstotal1024737vs1562161/cachehits537424; source_resource_limit0, warnings505cuts4/5+702cut7retained,shapevisibleQC0/CR0,RAWsame. Protocoldriver3045ae67.../probe5c57e392..., folder .build/xqc-polar-morphology/polar-morphology-reference-fit-full-cuts-v1 mirrored/verified-summary.json. These27 notadditionaluniqueover46/1219shape corpus; actualfullx_qc percut only/noadjacentcontext thennormalbelow.

Remote candidate RELEASE NOW ACTIVE: ops4 rainpulse-cpu-worker:xqc-reference-fit-cac7a4a-candidate-mb IDc2afb43ac626fa5a3e0486e8cb43b15e607c44320a3fbe1681d2cd89a718cb5d; network0b88aba83f774288c1e524d9c431c8f446de26f5c35371803014a02bb128da47,fp453032980fd593912f2a7bf2e234a662ed8732e0c598e54a439a5a03c17d4e39, CASoriginalrevision21(success likelynew22 freshcheck).Onlyrelease_id/threepilotmorph3newflags+v8version(13diffpaths), otherthresholds/flags/otherstations/QPE unchanged. Release folder .build/xqc-acceptance-20261001/polar-morphology-reference-fit-release-v3, promotion.json mirrored. Installedimagevsoldproductionec887 verifies5changed/new modules(core/shape/blocks/summary/refcache),791other unchanged; smoke2613radialisolated/1373compactpreserved/RAWsame/sourcekind0/hardweather0/notoperational. Preparedv1oldimagegenerator expandedunrequesteddefaults caughtstrictguard, v2helperabsentscppermissionfailed/dockermadeemptydir; preservedfail dirs. Fixedactive remote release_profiles.py only usingGitcommittedbbf06f4... bytesandrootcontainerbindatomic write, removedonlyour-createdemptyfiledirectory. V3strictdiff13 andsuccessfulconfiggeneration. NoothercodeworkerimagesorSserviceschanged.

Normal13plans/runs via API completedsubmission (no SQL), folder polar-morphology-reference-fit-normal. Includes frozen sixcross02/05 +early/next/zf702/zf7010849/zf702original/zf7020808 +zf505original. Exactsource/inputs/fpchecked andidempotency saved. Finisher PID865475 NOW LIVE, launcher reference-fit-normal-audit-launch-v1, driverSHA9dd8b8e6bd20f33642948f0c89f877cc2c8c1d57814182a50f6d9344ab299db8/audit2fe2182d33fef16e5ed18059b7333199e6ff2c79434f52dd04b1912af325fcfb. Lateststable snapshot11audited/99nativecuts, RAW/geometry/time/sourcecomplete/withheldQC0/CR0/PNGs allhashchecked. TasksstillRUNNING50505 baad222b-1263-4769-8b71-0b09f8354e23 and702original0070fcec-9230-4e72-9a07-e8b5d01917f8; observeroneKeyErrorattempts onQUEUED nottaskfailure, finisher unaffected. Normal snapshotfetch initialrecursiveSCP caughtpendingrename race, helper fetch-reference-fit-normal-snapshot.py nowcreatesRAMtaroffrozenstate+immutableauditedfiles only/SHAverifies, useit nextinsteadrecursiveSCPwhilelive. DoNOT restartnormal tasks/finisher merelytimeout; pollAPIheartbeats/driverPID/states.

CUA getState currentlyMaclocked/nativeappinventoryfails/browserextensionfetchfailed, IABtab4getTab30stimeout+kernelreset; asyncuserunlockrequest sent, responsepending. NoactualnewUIobservations. NewtasksURLs neednewresult IDs, olduserbookmarks pinoldversions; don'talteroldresult payloads. UI/independentweatherambiguity notaccepted,normal13 notyetallfinished. Goalactive, hourlyautomationstillpaused, secretsnotpersisted. ConcurrentSmain now5be9f6f; preserveMEMORY/localartifacts;Xdoc ownappendawaitcommit/push. Nextsamefinisheractual117cutpublication/audit, realmaps/useroriginal residual morphology check and legitimateweather/mixedoverlap; onceaccepted expand22stations/alltime thenquantify. NofullCIgreenclaim(lastcac7failedtest/lint/perfCD).

### S bounded variable RAW boundaries, 2026-10-02

Previous goal turn onlyplan/no implementation; thisturn implemented opt-in variable_boundary_hypotheses. ImmutableRAWseed centre .5beam/exterior2beam/adjacentedgejump2beam (gaps no multiplier), originalrunmembers, no chaining/newanchor. Entire matched+seed envelope guards retainweather/barriers/lowparent, originalhistorynotcropped/defaultarraysunchanged/noengineaction. New six tests initiallyunsupportedflagRED thenGREEN, spacing.5/1 plusconstantkm/curvedweather and expandededgeweather/barrier.361scopedtestsPASS, helperruff/diffPASS. Reusable actualpublishedaudit --variable-boundaries finaltwo v2receipts candidate1725/640 (prior1511/575), qualifiedtarget0/0; someoriginalhistoriesstable butweather/barrierstillhold, notactualisolation.8completeRAW defaultarraysidentical/rawunchanged/protectedweatheroverlap0, researchcounts0/0/0/0/0/0/0/178 lastoutsideactualROI; variable-boundaries-eight-invariance-v1.json. Own5code/test/script/docs dirty pluslocalMemory, notcommitted/deployed yet; noWebdelta. Allfourunifiedsessions terminal. Goalactive nextcomplete-object pollution/weather segment proof retainingoriginalhistory/positiveprovenance; no droppingguards toclaimsolved.

X finalfreshsnapshot12normaltasks/108nativecuts productaudited+hashchecked; normal50505nowSUCCEEDED/audited too (sourcecomplete incloldcut2). Only702original0070fcec-9230-4e72-9a07-e8b5d01917f8 stillRUNNING/currentworkerops-multiband-f95274b4738d-cea0ff682610, heartbeat2026-10-02T01:59:25.055615Z/errorNone/workerCPU100.65%RSS1.103GiBof6; auditPID865475confirmedlive. Allthree05actualcomparisons6jobsalreadyterminal. 3fa34b4d4a7663244ecc9b0d8b8d711b27f89961 ownXdocs mainpush/lsremoteverified afterS5be9; latestghrunlistfor3fa[] (don'tcallCIgreen). FreshgitstatusSnewdirtybranch_boundaries/variable_morphology/test_branch_boundaries/Sdoc/auditScript+MEMORYpreserved. Needpollsame702original/finisher, no restart. NewearlyURL result0a84694a-8682-4ba2-abc2-30c8bdc4e754.7b69df43-a6da-4759-8972-949ee28c4b68; newnext92f227a5-cece-4f11-9fa9-0ba01eaee319.c62f2223-2cf7-4d84-9fa7-9009fe0269ce. RAWscan/timeunchanged; browserpendingunlock request,goalactive.

X afterhumanunlock20261002: finisher nowNORMAL_PUBLICATION_AUDITED,13normal tasks117nativecuts localSHAverified,sourcecompleteall. ActualIAB screenshotfullpagehung/reconnecttimeout; nativeEdge nowusable,4fresh realmap screenshots .build/xqc-polar-morphology/ui-v8-{zf701-0803-cut0,zf701-0803-cut3,zf701-0807-cut1,zf701-0849-cut0}.png andmanual-review.json. Actualcut0earlymajorradials/fansgone,0849majorradialsgone,0807cut1majorfanremoved. HOWEVER0803cut3southernnarrowsegmented yellow/greenradialresidualstillvisible;0807cut1weakwestwardtracevisible. ThereforeNOT_ACCEPTED,goalactive; next traceactualremaining nativeQCgates throughcounterexample/hardweather/qualification withoutROIaction orweakenedguards. No code/deploychange thisfollowup; preserveconcurrentSdirty. Normal taskcompletion doesnotmeanweather/pollutionacceptance.

### S source-segment diagnostic committed, 2026-10-02

d7e39544599051b4e5633592fcc2687b1c643ba1 committed/pushed main; exactlsremoteSHA matched, parent3fa34b4 Xdocs inspected. Six ownS code/test/docsfiles only, unrelatedWIP/privateRAW/Memoryexcluded. Includes precedingturn boundedvariableedges plus reusable publishedaudit --source-models. Recomputes fullRAW originalfan_joint andfan_states first, validate originalsourceIDs/coordinates/protection/modelheldouttarget±20km; pertargetfullsourcehistory/kindcounts retained, actualtrainingcountsrestrictedcurrentbarriersection. Tested barrierlateinsertion source48gateskept butactualrefs36, targetchanged15dBdoesnotretrain, selectionone/fulldoesnotcrop, currentweatherholds, sourcecoordinate/protectionrejects. Final366scopedtestsPASS, helperruff/diffcheckPASS, actualCLIhelpinspected. Actual0818published2094residuals power60/state59/sameoriginalsourcecombined56 (ray165,SNR3–8dB,RHOHVknownonly1ofpower60);0842published728 allthree0. Final source-segments-v3 receipts SHA-boundaudit2df20a992ba2569538353599cbb299a96b009004c883fb27a267adc8f3d76c84/nativeRAW/publishedQC/2models; actualmodelsvalidated, notpollutiontruth/noactionauthority/RAWunchanged. No productiondeployment/Webdelta. Goalactive stillneedpositive originalneighbor-ray/timefeatures and weather-safe segment qualification/actualpublish; can'tclaimcomplete or inferpollutionfrommissingpolar/weakSNR. Allprobe/audit/test/pushhandles terminal exceptfinalghquery2957 pendingatthisappend.

### S recent-work summary plots, 2026-10-02

Userrequestedsummary/effects/nextwithimages. New reusable scripts/render_s_qc_review.py (untrackedowned) validates fullRAW SHA/exactpublishedscan/nativecoords/targetcount and researchreport snapshot+publishedSHA+noauthority; renders2panelswhennooverlay,3withvariable/source overlays. GrayexplicitRAWcontextnotQC, colorsauditedtargetsONLY, orange shape/purpleoriginalsourcematchNOTremoval. Freshboundthreeplots .build/s-discontinuous-20261001/review-summary-20261002/:0948v2 97RAWtargets→31visible (66withheld currentpublished, notsolelatestresearchbenefit);0818 2094→2094/orange1725/purple56;0842 781→728/orange640/purple0. 0818/0842/v2-0948 imagesactuallyinspected; JSONreceipts attachimage/script/inputSHAs, no productwrites. SSHfresh105bothSworkersrunning image rainpulse-cpu-worker:s-physical-windows-0b417e3-candidate; latestd7e3954researchnotdeployed. No huge newrecompute/testscope; previous366tests reusedlabelonly. Goalnotcomplete, originalneighbor-ray/timeevidence andweathersafeaction/holdout/livepublicationremain.


### X v9 centered pulse normal acceptance, 2026-10-02

12ce5954fa310ba5249747f699576d9ceaf2c1f9 own4 code/test/schema/docs mainpush+SHAverified;301XQCscopePASS/ruff/diffPASS. Actual earlycut3 residual hadnohard/local/counter/budget mask, complete5dBZ10kmhistory7windows/67.425km fixedcenter1.03nativefootprints whileedgesmove2.34/1.76; genericdefaultoff centered_pulsing_fans_enabled v9 fixesmissingalternative with originalguardsunchanged. Candidate4Workers imageff72257c787badf8d6da58b6e9e6c134f4d4713097bdc0f6b7c20330c0a3fc34/tagxqc-centered-12ce595-candidate-mb;1changedmodule795unchangedPython;smoke2840isolated/RAWsame/QCnan/CR0/sourceunconfirmed. Active3pilotZF505/701/702 fp1455036582b14f59e7e04ad6eeb5b1feca9cf849ea24efdb0980bdff76d75a34/network2fbb387cbb931317c3fa85053659d7ab0d146cfda639df31fac38bfe2ca4510b; operational/QPEfalse. Strict7diffpaths/CASverified4READY. 13frozennormal RO117cutsoldcandidateskept/counterunchanged/RAWsame,3newcuts; thenfull22stations46unique1219sourcecuts v2COMPLETED all46localoutput/receipt/sourceSHAverified/noresource,4newcuts total35830 inclnewZF4014bc90...cut0(11829); stillonly3pilotactive,no401normalpublicationyet. Fullv1wrapperwrongproductentry stopped/excluded, preserved.

Normalcentered-pulse-normal13tasks117cutsNOWallSUCCEEDED/productaudited/localSHAverified/sourcecomplete/RAWgeometrytimeexact/withheldQC0/CR0/PNGchecked.13budgetwarningcuts retained, NOTfullyaccepted. ActualEdgeui-v9screenshots0803cut3removedprevioussouthernlongline;0807cut1majorfangonebutweakwestline;7020808cut0majorfangonecompactSWkept;7020824cut4majorradialsgonebutexplicitbudgetwarningvisible. Weakwestnextcut1newnormalresidual-v2 boundSHA1a21b699... native270.4/272.38deg41/46visible>=5dBZ/>=10km, masks hard/local/counter/budget/morph/proposed/withheldall0; nextcompleteRAW qualification trace, don'tlowerthresholdblindly. UIusessharedS106analysis timeline whileXmapactualsweeptime, recordedfollowup.

105rootordinaryuseravailable0 causedobserver1287835exit/damagedprobe upload; sourcecomputedtaskstillalive andultimatelysucceeded,no computetaskrestart. MigratedONLY .build/xqc-acceptance-20261001 to /home/yons/hwapp/dis/rainpulse-xqc-evidence-20261002,2243filesSHAchecked/originalsymlinkpreserved,rootrestored~742MiB/data~506GiB. Copyownershipfixedtooriginalyonsuid1000; observerresumed1411395sameSHA andnowterminal13all. Bothfullcorpusdriver1329296 and13normalfinisherterminal; nextweakprobe9739terminalsuccess. Oldfailedobserver/probe/v1fullproof retained. Don'trestartnormal orhourlyautomation. Rootstilltight; don'tdeleteothermodels/raw. Goalactive, nextweakstreakcompletehistory+13actionbudgetacceptance+new401normalpilotthenexpandallstations/alltimecandidate; noindependentweathertruth/fullacceptanceclaim. ConcurrentSdirtyresidual_profile/speckle_review/isolation_geometry+Memorypreserved.


### S physical isolated speckle candidate, 2026-10-02

099abb30257ed7f61fe9b3108b510b77b0c1745c own10files committed/pushed main; ls-remote exact SHA verified. Opt-in residual.speckle_physical_support uses complete lower RAW parents, physical1/2km rings, original weather/gaps/footprints, target own noise/polar evidence and >=60% original-object evidence. Actual SNR[-50,0] missing-DBZH support is noise limited, not no-rain; exact target lowSNR3 retained. RAW unchanged; candidate follows residual eligibility/usable/flags; full RAW+availability digest and serialized replay, resource-limit whole-sweep abstention. DefaultNone preserves existing configuration serialization/path; no production YAML/105 change. Initial tests realRED7failed8passed, final469 scopedPASS plus ownnewfilesruff/diffPASS. Eight exact fullRAW audits current-policy-v2 SHA-bound script/modules/snapshots/images, physicalcandidate0all, geometricisolated11/0/0/0/0/0/6/9 but current own evidence0all; legacypotential94/4/7/1/0/88/95/31 NOTactualWebdelta. Artificialtarget8dB preflight superseded; do not claim gains/weathertruth/generalization or deploy zero-effect moreconservative path. Reusable scripts/audit_s_speckles.py/doc S_PHYSICAL_SPECKLE_20261002.md committed; privateRAW/plots/.build/Memory and concurrentXdirty excluded. Next original radial/fan provenance+neighbor-time positive evidence for weakfragments, weather/unknown guards retained, no blanket small-object deletion. No new IQ/SQI requests.

### X near-floor RAW source reference research, 2026-10-02

9ef3921ba16242f4d5b77e215200b5e71caab7af own3files(main source_blocks optionaldiagnostic/test/doc) committed+pushed, be1a1c6cfbadec0ef649d135e61f7b6370a8b2d9 docsfollowup push/lsremoteSHAverified. Default production paths unchanged; NO normal action/config/schema/worker rollout. Ownnewnear_floor_references defaultFalse onlydetect research enables pairedRAWsn within existingfloor±spread as refs, targetsaboveflooronly, preserves originalbilateralwidth/heldoutguards/coverage/spread/response/resource.9newtestsRED/GREEN;310XQC-v2PASS/ruff/diffPASS/oldNumPyABIwarning. Broaderhardening9failures confirmedidentical withunchangedHEAD module loadedinmemory (staleharness/reference), notclaimCIgreen. ConcurrentS099abb3knownmainparent inspected, unrelateddirty preserved.

Full RAW weaknextcut1 270.4/272.38 provespairedz-20logr stable andSNRmain1–3floorstraddling; abovefloor-onlyreferences excluded continuousbelowfloorsignal. Newnormalboundsingleprobe near-floor-reference-v2 SHAea562593335e3aefe5b05817355a68a696995b5323e89fb6a99a02c7baeaded7 actual50/87visible matched(40/41+10/46), RAWsame/hard0. Remaining1atnative146 SNR4.5above independentfitupper4; native148 mixed blockeligibility:4/5/12 geometryfalse;7/9 heldoutcoverage2/3<.7;2/3cantrainotherblocks butnotownfit, don'tattributeallbilateral. Readonlyinstrumentrejections SHA c14a25ba6ae22293dd76eb487dfb83ec4a0519784bc344928b0e9368954bc890. No loweredthreshold.

Nearfloor normalcorpus drivers1613568(v1),1644083(v2),1687316(v3) allterminalCOMPLETED13tasks117nativecuts/localinput-outputSHAverified/private .build/xqc-polar-morphology/near-floor-normal-corpus-v{1,2,3}. v2all394069 candidates/currentvisible2592/hardlocal0 BUTcompact14376/currentvisible100 across5cuts: noactivationallowed. v3excludes FULL originalhard/local/compact objects fromTARGETS+REFERENCES beforefit, verified376778/currentvisible2447/allhardlocalcompactoverlap0/RAWsame, west50/87 retained andcurrentvisible53southern194.49unselected. Doesnotaddunique1219sourcecount andnotweathertruth. Protectingrefsremovedadditionaloutsidecandidates vsjustoutputmasking. Verifiedsummary localv3boundallreports. Remotebase /home/yons/hwapp/dis/rainpulse-xqc-evidence-20261002 stilloriginalsymlink; source_blocks overridefileSHAfbb1aa5337454ce62a6b0f6722aeb6e56109fbb3894af2284eed8f9a2a2417b9 exact9efcommit. AllROcontainerdriversdone,4productionXWorkersstillv9ff722 unchanged; hourlyoff. Freshdf ordinaryroot98GiB/data505GiB (no unrelatedcleanup bythisturn).

Goalactive next: measuredpergate bilateralvsblockmedians trace forremaining37, independentfullRAWreferenceeligibility without relaxations; retain wholeprotectedrefs; broader22station46/1219source evaluation thennormalpublication+actualmap beforeacceptance. 13budgetwarningsareclassificationconfirmationabstentions butALLcandidateswithheld/QCnan/CR0, don'tequatewarningcountwithvisiblepollution. No restartoldterminaltasks, noalgorithmactivation/crediblefusion/QPE/forecast/secretpersistence.


### S causal original-seed recurrence, 2026-10-02

600d50c own6files committed/pushed main; remote SHA verified. New diagnostic-only source_temporal: immutable same-site/original-lineage past seeds only, native UTC causal/20min and real angle/range/height alignment, no tail growth; latest measured DBZH conflict/unknownSNR wins over older matching, duplicate scan/cut dedup and changed-input rejection, weather/gaps/resource guards. No engine/profile/105 changes or action authority. New15module+6render tests; full490 scopedPASS/ownruff/diffPASS. Eight final source-temporal-20261002-v2 reports module/input/diagnosticSHA-bound; fullRAW matches0/0/0/0/0/0/0/243 last0842; all26isolatedmatches0/allboundarysupported0. Exact0842publishedreceipt781RAW/728visible unchanged,13visiblegates past08:36originalseedmatch, allpast SOURCE_HOLD2 NO_NARROW_BOUNDARY, sourcekind8/16/24 notconfirmedpollution. Genericaudit and reusable render --temporal-report committed; actualPNG inspected v1/v2 pixelSHA91280da2...same, currentRAWcontextgray and cyan13 diagnosticNOTremoval. No generalization/weathertruth claim, otherzerosincludeidentity/age/nohistory notnegativeevidence. Nextcompleteoriginalwidefan boundary proof retainingweather/history; can'trelaxsmallpoint/noise thresholds or promote recurrence alone. ConcurrentX/code/privateRAW/plots/Memory notstaged.


### X complete near-floor weak-family candidate continuation, 2026-10-02

39b008316da75c13faf7c7f7545b9e11e9aaad9b own8files main committed/pushed, ls-remote exactSHA verified; source_envelope/source_ledger concurrentS dirty and Memory/private artifacts preserved. Generic source_fans nearfloor RAW reference option retains45deg family andheldout/ownpower bounds; narrow7deg unchanged. Exactnextcut1 probe SHA0b312bfbcf2e68ff8ba2e53ca1c8d31ff5759118822651dd32c2e42bb0f329c6 selected40/41+46/46 westvisible, hard/local/compact0, south53unselected. Native shoulder probe7a55242cf41240feb33864ebf95522714f516d53e76d66646a164a0de5ce69fe disproves blockmedian masking: exactbilateral sum<=7 only6/41+14/46; wholefanmethod ratherthanwideningnarrowthreshold. Independentbounds do notadmitnative146onepointSNR4.5.

New strict defaultFalse near_floor_source_candidates_enabled requires explicitfloor+compactprotection inPython/JSON. Full originalhard/local/compact removed fromtargets+refs; Reason262144, nativeXQC_NEAR_FLOOR_SOURCE_MASK; samefinalcandidateowner action3/QCnan/CR0, notconfirmed. Original prior sourcework consumes same500k/50k allowance; newpasscannotreset. Newmodel/evidence/compact failures wholemoduleabstain, retainoldcompleted masks/evidence. TwoRED/GREEN regressions: pipelinewholecut resourceabstention noKeyError; newcombined losslessevidenceoverflow refinalizes same completedarrays withoutnewfit/budgetreset ratherthanemptywholecut. Audit preservesoldQC/action/CR. 326XQCcasesPASS/oldNumPyABIwarning, owncore/source/testruff+diffPASS. CI36966524199 failedtest/lint/performance-cd whilebuild and independentprelaunch/opensource/RFI/paper/crossradar/residual/AB success; notCIgreen. No unrelatedbroadCIrepair.

Actualstandalonepipelinev1 13FAILED due installedparent missing unrelatedlocalradome producer; failureskept. v2 usesinstalledparent+ownpatch, lastfresh12jobreceipts11EVALUATED/1FAILED(zf50505cut1 KeyError module_records aftercoreabstention; exactabstentionreason notcaptured). Driver1902046 lastalive elapsed31min, remaininghardcore_mclaren CPU100%/1.4GiBof3g notstalled; no restart. V2usesprecombinedbudgetcode andis superseded, outputsnotyetlocallySHAverified. Final installedpipeline+committedpatchSHA275aa37be73beb595109322e4e137c278e59803248917c2977fce838590385aa fromparenta5fceda6385b7036609a047135a7effd4e525d01cc11b792f981397c4764da6c (no unrelatedradome dependency). New image rainpulse-cpu-worker:xqc-near-floor-39b0083-candidate-mb built successfully on105; no worker/config/promotion changes. Source receipt .build/xqc-polar-morphology/near-floor-image-source.json ties config89e579..,coree42c5d..,blocksd353..,fansb654..,pipeline275aa..; parentff72257 unchanged.

105SSH intermittenttimeout/bannerexchange/connectionclosed after successfulbuild; Web4173HTTP200. Imagehashverification helpers failed onlySSH, no successfulverification claim ornewimageIDyet. Read prepare-installed-v3 unknown result laterconfirmedcompleted withSHA above. Finalhelpers verify-and-launch-near-floor-final.py, probe-near-floor-pipeline-final.py, run-near-floor-pipeline-final.py preparedprivate; uploads/launch outcome mustfreshcheck (pendinglatestSSH). Finaldriver chooses3threadsifold1902046alive else4, sharesboundimageSHA andall source/probeSHA, priority50505failure+next. It only ROpaired13jobs117cuts, no adjacentcontext/publication. Check `.build/xqc-acceptance-20261001/near-floor-pipeline-final{/-state.json,-launch.json,.log}` actualfolderstate.json. Before launch repeat checkstate/launchPID toavoid duplicateunknownexecution. Networkstillv9 threepilots; no trustedfusion/QPE/forecast/hourlyautomation. Dataordinaryfree505GiB/root566GiB freshpriorcheck, no deletions. Goalactive: finalrealpipeline+full22station46/1219candidateevaluation, normalpublicationandfreshmapstillpending.

Finalfreshoutcome: SCP final4helpers/receipt exited255 Connectionclosed; finalverify-and-launch SSH exited255 connecttimeout, no returnedverification/launchreceipt. Do NOT claim finaldriverstarted; freshcheck remote partialuploads/state/PID before completing upload/launch. 39b0083 remains exactoriginmain, ownindexempty; concurrentSsource_envelope/source_ledger dirty preserved. Webreachable whileSSH22banner unreliable is current externalexecution limitation; goalnotcomplete, no hourlyrestart or configswitch.

## 2026-10-02 S 原始射线侧翼取样修复

- 修正 0.99° 原生角间隔 / 1° 波束向外取整跳过最近侧翼导致单射线内部支撑不足的问题；补充最近实际角度侧翼仅在原保护检查范围内，要求实际 DBZH 对比或 [-50,3] dB 有效 quiet SNR；保持完整原始源、范围和禁止递归增长。旧向外证明行为保留。
- 新版本 source envelope v3 / source ledger v2 measured-nearest-flanks；禁止跨新旧台账混用同版本时间证据。保留 scripts/audit_s_flank_sampling.py 和 docs/S_SOURCE_FLANK_SAMPLING.md。
- 红/绿回归覆盖角间隔、角度跨零、缺测/非 quiet SNR、无效低 SNR、天气/保护和不扩大原保护范围。最终本轮相关测试 363 passed，新文件 Ruff 与 git diff --check 通过；安全/架构专项复核完成，发现的补充保护范围回归已修复并复现通过。
- 私有 8 体扫最终证据 .build/s-discontinuous-20261001/flank-sampling-20261002-v3/report.json，基线 600d50c。新增包络关联门 Z9591 10:18=30、Z9598 08:36=40、08:42=80，其他 5 体扫=0；这 150 门不是实际新增删除门数。08:42 实际 Web 已绑定 728 残留门与新增包络交集仍为 0，主红框问题未解决；未部署到 105，未修改/出版生产产品。


### X final image live paired replay, 2026-10-02 continuation

Previousgoalturn progress(commit39b0083/push/sourceintegration), notmerelywait. Fresh105SSHrecovered intermittently; candidateimage now verified SHA8a3ab6a43739206f14d592a10dca20f6b3c23b1fd53cf2de0d28f0a726ca6eca,5ownedmoduleschanged/791otherPythonunchanged/defaultFalse/importPASS. Verificationreceipt mirroredlocal .build/xqc-polar-morphology/near-floor-image-verification.json; exactsameconfig/core/blocks/fans/pipeline sourceSHAs as sourcecommit39b0083. Installedparentpipelinepatch avoidsunrelatedradome feature absentparent. Finalpairedpipeline driver2190068 live(pidfreshps17:55 pluscurrentpollpending), 3algorithmcontainers alongsideoldv2 thenoldv2terminalFAILED13jobs12success/one50505failure; no restart. Final9/13jobs81nativecuts EVALUATED/localinput-outputSHAverified; allRAW/nativecoords/old masks/withheldretained/newselectedQCnan/CR0/action3/protection0, combinedtrialexcess0. visible_selected1455 across81; nextcut1 targetwest40/41+46/46,full113visible, trialsold2497+new2250. Final50505 pendingpartial nativecut2 nowEVIDENCE_BUDGET_ABSTAINED/newvisible0/oldresultretained ratherthancrashing; cut4EVALUATEDnearvisible578,cut5ongoing. DoNOTclaim50505wholefinished orresourcecomplete. Earliercut1reference for505failure wasordinalsecondREF, actualnativecut2; publicdoccorrected.

Readonlywider-source queueddriver2265993 confirmedlive17:55(currentfreshpollpending) .build/xqc-acceptance-20261001/near-floor-source-full-corpus-v1 stateWAITING_PAIRED_FINAL, waits2190068/old1902046thenrequirespairedCOMPLETED before4parallel22stations46unique1219sources. Launchreceipt mirroredlocal; driverSHAe21a4f4e09f227c41c3a5c08fd5f432bfb0b15e9625c0ecf2928967e9f6ac8ff/probec3870bf7384e6984f0c666882447b0a0934c9683cbf2a0634ab8502c6a78fdda. Source-onlyprobeusescurrentverifiedcandidateimage andsamefrozen zf701policy but explicitlyreceiver_floor_transfer_verifiedFalse; FULL hard/local/compact exclusionbefore RAWrefs andtargets,oldshapeunchanged/RAWsame/nativeidentitiesSHA,losslessmodelproofcap retained. Itisdiagnostic NOTotherstationcalibration/normalrelease authority; doNOTswitchlive networkwhilepaired/widerROstillrunning (readsourceconfiglive). IfwaitingPIDmissinginspectlog/assertions,don'ttruststaleWAITINGstate orrestartsolelyobservationtimeout.

Preparednear-floor-release-v1 blockedstrictdiff4 becauseimagehasoldrelease_profiles implicitdefaultmaterialization; preservedfailedfolder/nochildpublication/no productionwrites. Verifiedinstalledroothelperbbf06f4...sameGit39helper, v2bindsexactcorrecthelper andstrict4pathsPASS. ChildnetworkSHA317806b5e732a46794b834e10632f50bf656270034aeaaebbbb80b36e771264e, parent2fbb387...; onlyreleaseID+threepilotnear_floor_source_candidates_enabledTrue. .build/.../near-floor-release-v2 network-receipt.json ready butNOTDEPLOYED,active4Workersstillv9ff722/networkfp14550365. No trustedfusion/QPE/forecast/hourlyautomation/otherstationactivation, no secrets persisted/deletion.

Private fetch-near-floor-pipeline-{v2,final}.py snapshotarchives onlycompletedimmutablefiles+inputJSON,localSHAverify; Python3.9 archiveextractionfixed tosafevalidatedregularfiles, neverfollowarchivepaths. Legacyv2 lastsnapshot11jobs99cuts1795visible, superseded notfinalacceptance. Finalsnapshot9jobs81cuts1455visible locallymirrored withverified-summary. SSHmaster .build/ssh105 canhangwhenVPNrouteflaps; directControlPath=none+ConnectTimeout15+ServerAlive5/2 works; don'trepeat unknownmutations. Userhourlystayspaused, goalactive. Docs6484324f3a96c1f19b74b77cc1909314130dacbb ownXdoccommitted(pushlatestpending); concurrentknownSb232ab9ancestor anddirtysource_footprint/render_s_qc_review/Memorypreserved. Next pollsamefinal2190068+queued2265993, completeall13/117SHA andcompletewider22/46/1219; address actualevidence-resourceabstentions withouttruncation orraisingcaps ifvisibleimpact, thennormalcandidate configCAS/APIplans/productaudit/freshmaps. Newnormalpublicationnotyetdone andno fullweathertruth/overallcompletionclaim.

Finalfreshthiscontinuation: pairedsnapshot12jobs108cuts locallySHAverified, sourcegatevisible3595 selectedQCvisible0/CR0,107nearEVALUATED+one50505nativecut2EVIDENCE_BUDGET_ABSTAINED. CurrentwholebatchstillRUNNING; notfull117/newnormalpublication. 6484324pushcompleted/lsremoteexactSHAconfirmed, concurrentSsource_footprint/render/deployqc-s-physical-constellationdirtypreserved. Latestdirectfreshps/statepollhandle83626 pendingatappend; do notrestart either2190068 or2265993 ontimeout. Newsourcefull corpus onlyQUEUED waiting, not1219done. Preparednear-floor-release-v2 strict4diffpaths+child317806... notselected/deployed. SSHmayflap butnewprogressverified, no blocked/complete goalstatuschange. Next samehandle/batch then queuedsourceproof and actualnormalcandidate lifecycle+map; retainoneevidencewarning andold13budgetwarnings, don'tclaimall modules complete.


## 2026-10-02 S complete original source references v7

8426ddb8d710c930be6b9bf492c16dbee0ffb12c committed/pushed main; exact ls-remote SHA checked. Only own 8 files staged; concurrent 6484324 X docs parent inspected, unrelated Memory/private WIP preserved. source_footprint v3 recovers full distance gates of frozen IDs touching local RAW tile while retaining its original angular islands/candidate bounds; held-out target±20km, barriers/gaps, weather and no-recursion guards intact. Seven regressions plus seven bound-render tests; final377 scoped PASS, own new-file Ruff/diff PASS; security/architecture review no findings. Whole-repo CI36970589513 completed failure (test/prelaunch/lint/performance-CD/verify), not green.

Private eight native cases audit .build/s-discontinuous-20261001/complete-source-20261002-v3/report.json baseline b232ab9: recovered0/9/0/220/156/3148/339/5375; 0842 withdrawn446=unstable304/outsideboundary142. Actual published targets0818 overlap122/2094 and0842 18/728, withdrawn visible0; qualification is not final engine removals/independent weather truth. Reusable audit_s_complete_source.py and render --footprint-report retained; actual0818 review-v2 inspected, red offline proofs not removals. Most peripheral unseeded rows/speckles unresolved.

105 now both S QC workers healthy image rainpulse-cpu-worker:s-complete-ledger-20261002-v7 SHA3505752503f27c14f2c4fae09199e8d70b222a32f0137efecd40560fba344f05. Only verified source_envelope/ledger/footprint modules patched atop actual0b417e3 parent; installed hashes/synthetic same-ID20targets/RAWunchanged/weatherretain smoke passed. Profile287064a46494ec61c74899c2500813cad2c88a7e1868bbb382717039702677c1 differs v6 settings only version, eligibilityFalse; active alias atomically updated with rollback copies. X4workers IDs unchanged. Initial private installer helper quoting failed BEFORE worker stop/config writes; raw-string repair then success, receipts .build/s-complete-ledger-20261002-v7-release retained.

Background systemd rainpulse-s-complete-ledger-v7-refresh active PID2475124: same frozen8time/32scan plan, prioritized0818, then QC→grid→mosaic→QPE→diagnostics. Fresh state RUNNING current2026-08-28T00:18:00Z/noerror/completed0/newprofileSHA; firstQC624add27-fb9a-5d3f-85b4-c286d6c1b24e. Output .build/s-complete-ledger-20261002-v7-refresh/state.json and run.log. Do not relaunch/duplicate; inspect these receipts next. Web updated image not yet claimed; background first-cycle publication still pending.


### X continuation 2026-10-02: shared source budget and empty-target fix
Fresh paired final still12/13 (108 cuts), original ZF702 nativecut0 EVALUATED58703 candidates/18 newlyvisible; cut2 RESOURCE_LIMIT_ABSTAINED old355972+new144029 trials=500001 (first rejected reservation); cut4 model-record limit old36358+new13643=50001, candidatezero. Baselinecut5 ongoing, CPU100% RAM1.441GiB/3GiB. This is actual old/new shared-resource exhaustion, not SSH failure. One50505cut2 evidence cap retained. Do not publish f39 candidate claiming all117EVALUATED. Diskordinary available541071683584bytes (504GiB),99.7millioninodes.
New generic empty-target optimization f49cedac0775eacac3a42c96215f1d4cce1c2e84 committedmain/pushed and lsremote exact. TestRED old16uselessfittrials with no actiontarget, GREEN new0; full XQC-v2 327PASS(oldABIwarning). source_blocks only omits targetblocks without any eligible abovefloor target; all belowfloor training references/guard membership remain. Realcut2/4 improvement stillUNVERIFIED; modelrecord exhaustion maypersist. No productionconfig/image switch. Preserved concurrentS8426ddb, Memory/privatefiles.
Private deploy-near-floor/replan-near-floor/verify-near-floor-source-full-corpus prepared syntaxPASS, notexecuted. Verifierfirstreject trial cap+1 isallowedonlyResourceabstention; latest correctedverifier local (initialremoteupload priorcorrection; reuploadneeded). Planned optimized replay original ZF702cuts2/4 waits existing2190068 and2265993, onecomputeCPU so no5threadoverlap; probe/driver/sourceblocks f49 exportedandSCPsuccess remoteevidencebase. LaunchSSH exited255 connecttimeout, UNKNOWN/MISSING launch untilfreshread14393 returns; do notclaim queuedstarted. Fetchfinal SSH session55844 pending read transfer (no mutation); nextwaitsamehandles. Optimized helper uses immutableimage8a3 + onlycommittedblocksROoverlay; remains diagnosticnotproduction. Generic runtime-record dedup/targetfit waste remainsnextfocus; do notraisebudgets/truncateevidence/guessvendorfields. Goalactive, hourlyautomationstillpaused.


### X first-proof resource recovery, 2026-10-02 continuation
Own main commits be7226e and 58f8633 pushed; full XQC340 tests/Ruff/diff passed. Generic raw-coverage-before-fit and first-complete-proof target dedup (upper/primary/narrow), retaining all RAW reference measurements and original thresholds/shared500k fits/50k models/4MiB evidence caps. Candidate58 image72067d2f7b1ab12ff34bfffc2560a31837c82e028adce7f48892c3de8a1b59d8 verified2own modules changed/794 other Python unchanged; production v9ff722 and network2fbb still unchanged. Config child e057e006f231ad1d531d820de441cd11bf5dc3921501581e683247c86a1ba224 prepared onlyrelease_id+near_floor enabled on ZF505/ZF701/ZF702, NOT activated.
Final13 native paired replay `.build/xqc-acceptance-20261001/near-floor-first-proof-pipeline-v2` PID2914005 aliveRUNNING1thread, lastfresh2/13 immutable receipts (50505,next). Whole50505 all9cut nearEVALUATED, cut2 previouslyevidenceabstained now595additionalvisible withheld/RAWunchanged, complete3553031bytes<4MiB. Original702 pendingcut4 nearEVALUATED268visible selected, notimmutablewholevolume untilcomplete; cut5 stillbusy100%CPU1.427GiB/3GiB. Failedinitialfirstproofmemoryprecheck PID2860928 missing/jobs0 receipt retained, notbatchrestart. FreshMemAvailable~5GiB due unrelatedS workers~85GiB +diagnostics10GiB; do notpause/deleteotherwork. Fourcompute maximum authorized but actual safeone thread selected bypreflight.
Prior be722 replay13/117 COMPLETED locallySHAverified,116EVALUATED/one50505cut2evidenceabstention,4840newvisible withheld; initial39 replay114/117EVALUATED with2sharedresource+oneevidenceabstention. Oldfullsource22stations46volumes1219cuts source-only verified/noresourceabstentions708176candidate; calibrationtransfer UNVERIFIED/no normalactionpublication.
New58 broadsource PID2946789 aliveWAITING_PAIRED_FINAL, foldernear-floor-first-proof-source-corpus-v1; exact-maskSHAcomparison toold39all1219 required by uploaded verifier. No promotionuntilbothcomplete/verified. RAMsafe deploy/replan/finisher latesthelpers SCPsuccess; prepared notexecuted. Finalfetch helper localdestination fixed tonear-floor-first-proof-pipeline-v2 (previous wrongnon-v2folder); freshfetchpendinglocalprocess seeactivehandles.
ActualPlaywrightheadless map baseline inspected: user originalZF702linkpins sx-quality-v2-z10historical result; actual 查看最新结果 loads v9result e17a43ae-50ab-47db-a24f-820262926fee.4ed48ecb-d8c0-4637-b015-0429b3ba5b9b. v9mapmostlyclear butclassification/actionbudgetwarning retained; NOTnew58UIacceptance. Baselineoutput/playwright/zf702-original-pinned-history.png andzf702-v9-latest-headless.png; headedbrowsertimeouts, headlessworks. New58normalpublication/nativePNG/UI stillpending. Hourlyautomationpaused/goalactive/no trustedfusionQPEforecast/no secrets. ConcurrentSdirtytracked/privatefiles preserved.

Fresh local final58 fetch completed/input-output SHA verified: 2 immutable jobs18cuts,2785 additional visible gates withheld,0combinedtrialexcess; replay RUNNING notall117acceptance. New read-only queued verifier helper uploaded; waits final13/117 immutableproof+source46/1219, validates exact outputSHA/receipts/RAW/withheld/newNaNCR0+sharedcaps then creates remoteverified-summary; no deployment authority. Launch pending session55098 until freshreceipt; inspect state/launch before retry.
Queued read-only verifier launch confirmed PID3169336 (session55098 exit0); no deployment performed. Next freshread pipeline2914005/source2946789/verifier3169336, locallyfetch completedproof then normalCASpromotion+boundednormal13publication/UI; do not rerun immutablejobs or initialmemoryprecheckfailure.

### S frozen original-object morphology, 2026-10-02 continuation
User explicitly accepts original contamination source + same radial object + residual radial morphology without constant-power fit. Research-only anchored_radial_shape.py v2 now associates adjacent actual native rays within two measured beams and same frozen RAW_FAN_ID; selects nearest ORIGINAL ancestry before fit, no farther fallback, permanent competing-ID ambiguity, source-to-target full-stencil gaps/barriers and original seed range retained. Actual target gains: eight SHA-bound replay anchored-radial-shape-20261002-v5/report.json, stored Web residual0842 144/728 qualification vs oldsame-row3,0818 still0/2094. Total proposals not production removals; no engine/deploy/worker/product changes this turn, RAWsame/protection0. Added17 focused tests; combined428PASS, ownRuff/diffPASS. Own research files remain uncommitted alongside prior owned extent/original-boundary audit; unrelated shared engine/Memory edits preserved. Frozen ancestry excludes far recursive child promotion; failed nearest original source regression passes. Main red-box problem unresolved, do not claim complete/generalized or deploy these proposals as validated weather cleanup. Next sparse full-object morphology + weather controls and lossless engine evidence integration, then actual publication/image verification. Existing v7 background refresh not freshly polled/relaunched here.

Current goal turn progressed: local SHAverified final58 5jobs45cuts/allnearEVALUATED/4726additionalvisible withheld/no combinedtrialexcess. OriginalZF702all9cuts complete:cut2 old142809+new45303 trials/543additionalvisible;cut4 old112698+new34919/268additionalvisible; originalclassification/actionbudgetwarnings retained. Publicdoc-only40dcc73cc5ae6831a285fee66acf85cc7a1e8c16 main pushed/lsremoteexact; image source58 remainsunchanged. Preparednormalreplan resourceadmission corrected toactual6GiBworker cgroup+6GiBreserve, conservative activeallowances (minimum12GiB); updatedremoteSCPsuccess. No tasksubmission/deployment. Latest live2914005/2946789/3169336 freshrevalidated; newest poll39329 handle needsread ifnotalreadyfinished.

Further fresh fetch succeeded: final58 10/13jobs90nativecuts, allnearEVALUATED/5095additionalvisible withheld/0combinedtrialexcess, input/outputSHAandRAW/oldwithheld/newNaNCR0 locallyverified. Remaining3jobs notyetacceptance. Resource/statusprobeSSH94517 connectiontimeout (observationfailure only); driver2914005 lastfresh alive elapsed49min, source2946789 andverifier3169336 alivewaiting. Do not restart because SSHtimeout.

### S original narrow-band morphology and bound images, 2026-10-02
Previousgoalturnprogress (v2code+real144/728). This continuation anchored_radial_shape v3 adds frozen multi-ray original-source bands/full ORIGINAL RAW morphology, complete ID refs from local parent tiles, every-row heldout refs and full band/outer-side barriers/gaps. Found/repaired false quiet shoulder from short target-only seed: unrelated source remains actual observed shoulder. 26module regressions+10newbound-overlay tests; scoped447PASS, ownRuffPASS. Final SHA-bound eight replay anchored-radial-shape-20261002-v8/report.json: exactold0842visible147/728 qualified,0818still0/2094; totalproposals0/508/6/62/28/378/103/632, RAWsame/protection0. New render --anchored-report binds actualreceipt/nativearrays/owner/ambiguity; 0842-review.png inspected, red147 offlineproposals NOTnewproductiondeletions, grayRAWcontextNOTQC. Mainunresolved0818:998/2094 rough attribution have sameparentnearbyoriginalrefs butmultiple-rowresidue,1008no sameparentnearbyseed; fullparent originalbands oftenoverlapotherfans andbroadsource. Do not relax allflanks/raiseextent to callweatherpollution. Ownresearchfilesstilluncommitted; sharedHEADmoved58f8633→40dcc73duringturn, no index/deploy modifications. Noengineintegration/productwrites/105relaunch. Continue full original angular-object separation and weather controls then evidenceintegration/publication; goalnotcomplete.

2026-10-02 08:11Z: actual resource recovery, only three idle ops-multiband worker2/3/4 recycled through pool CAS drain24->25/resume26, sameDockercontainerIDs and v9image preserved; worker1/ROreplay/allSservices untouched. Receipt idle-xqc-recycle-receipt.json statusRECYCLED/SSH64564exit0; MemAvailable3572310016->7969001472bytes (+~4.1GiB). Globalactive11 areALLstalledexpiredlegacy attempts (poolactive==stalled11/queued0), not current executingwork: all4currentservicecontainers READYidle; thesehistoricalstates/receipts preserved, noabandon/SQL. Initialdryrunzeroactiveassert failedno changes, then safelyallowedallhistoricalstalled with four-container/no-busy census and drain preventingclaims. Never repeatrecycleblindly: receiptguard exists. Latefreshworkerreadycounts temporarilyinclude oldheartbeatTTL, need currentboot/seen-at filterbeforepromotion; channel/net/image unchanged. Finalpipeline2914005 last11/13, source2946789 waiting, verifier3169336 waiting; goalactive.


## 2026-10-02 S complete-fan source calibration (local candidate; goal still active)

- Ray offsets + independent ray/range cross-validation repair paired DBZH/SNR population bias without widening power/phase bounds. Protected opening/cross-ray envelopes now require the target's own strict receiver evidence; independent weather/conflicts cannot train. Existing weather patch preserved.
- Final v6: source manifest 2d6111fb..., parameters b6829ee1...; 632 tests PASS, full 11-sweep/export and original RAW equality PASS. O1/O2/R1/R2/R3 isolated, O3 retained; red14->0, orange eligible2981->1001. Protection union530 retains517; only13 strict own-source/segment matches isolated. Three weather controls retained.
- Evidence .build/s-full-fan-source-20261002/ and docs/S_FULL_FAN_SOURCE_20261002.md. v2/v4 superseded; v5 stopped before compute after a kernel read stall. Six cached input tree hashes match originals. v6 deduplicated arrays3.63GB below8GiB cap; reclaimed only own stopped scratch57.2GiB, retaining original downloads/protection verification/compact evidence/logs.
- No deployment/push/online recompute. O3 and similar protected residuals still need independent near-range source evidence excluding target/protection windows; right fan boundary is acquisition-censored. Concurrent anchored_radial_shape/other changes preserved. Do not claim complete pollution resolution or production readiness.

- 2026-10-02 bounded follow-up: protected13-and-orange1001.json source-only audit matches frozen v6. All13 pass even excluding fixed530/control ±20km reference windows, but1237–1240/1240 original references are RAW weather-compatible; removing those refs yields0matches. Independent pollution labels absent;13 dispositions remain research candidates. Remaining1001 all fail own checks (rho707/phase599 overlapping), no independent weather available; all attribution unresolved. No new full-volume replay/core change/deploy.


### S anchored morphology integration correction and residual attribution, 2026-10-02

Earlier v8 147/728 08:42 proposal gain is withdrawn as weather-safe deployment evidence: actual uniform-weather counterexample showed source-labelled flanks could fabricate quiet air. Fixed outward measured flanks retained; new native-angle positive/negative stripe contrast uses independent held-out windows, variable magnitudes, frozen ancestry/range and no recursive sources. source_footprint v4 persists actual DBZH/policy/added evidence and replays before action; audit and disabled policy tested. Current focused47 tests PASS, prior full scoped457 PASS (shared dirty checkout, not installed runtime). Bound final8 integrationv2 report against8426ddb: actual old stored-visible gain0818=16/2094,0842=0/728. No new deployment or product publication. Current0818 image inspected, SHA5840e7c7..., bound16 proposals—not removals; do not show oldv8 image as current. ancestry-limits-v2 exact visible inventory:0818 new16/protected0/noParent231/noLinkedOriginal766/beyond2beams14/nearOriginalRange1067;0842 0/45/182/30/40/431=sum728. v1 inventory superseded (included53nonvisible0842). Widening angular search alone is not principal0818fix; inspect complete original sparse-band morphology for1067 and association for997 separately. Own code/docs remain uncommitted, other shared WIP preserved. Goalactive; installed image replay/deploy/live refreshed proof stillpending.

- 2026-10-02 final safety handoff: BroadSourceConfig.allow_raw_weather_receiver_override defaults false, no existing config opted in; normal/missing-moment BWS action boundary holds all RAW proxy targets even with strict receiver matches. Explicit true recovers only offline candidate exception; independent weather and own failed polar still veto. Pure ray-offset calibration + weather patch retained; unprotected pre-existing experimental branches remain. 645 regression tests PASS. Historical v6/11-sweep output is pre-gate candidate evidence, NOT current-default output/promotion. Local safety-only.patch/receipt under .build/s-full-fan-source-20261002/safety-handoff. No new full replay/remote read/deploy.

Final58 paired replay now terminalCOMPLETED13/13tasks117/117nativecuts, everynearEVALUATED,5435additionalvisiblewithheld,0combinedtrialexcess. Localfullinput/outputSHAverified plus exactall-native-array comparison tobe722:116cuts ALLfieldarraySHAidentical, solechange50505cut2 priorEVIDENCE_BUDGET_ABSTAINED nowEVALUATED fieldsDBZH_QC/flags/display/proposed/reason/newsource/withheld;RAW/oldmasks/action/CRunchanged. Localprior-native-array-comparison.json freezes exactdifferences. No normalpublicationyet. Source2946789 freshwaitingmemory at08:14Z but anchor1recycledryrun35951 failed WAITING_MEMORYprecondition before ANY mutation, indicatingstatechanged; poll currentstatus notassumeRUNNING. Worker1 NOT restarted; no secondreceipt. Originalsource/verifier stillusev1/PIDs2946789/3169336; noqueue cancellation orresourcepolicy relaxation performed.


### S complete original fan shape candidate, 2026-10-02

New own original_fan_shape.py diagnostic-only branch and11 regressions implement immutable fullRAW bands, measured outeredges, heldout5windows150km, >=3originalsource rays10km/3windows100km, parent/range ancestry, explicitmissing/weather/no recursive growth and bounded work. Local weather excludes entire measured angular columns from refs+targets rather than fabricating dry shoulders; broadweather and sparseprotectedcontrols retained. Existing audit driver --method original-fan and renderer --original-fan-report extended;20bindingcases narrow/fan PASS, currentshared scoped478PASS/Ruff/diffPASS. Frozen8replay original-fan-shape-20261002-v3/report.json: actualoldvisible0818=0/2094,0842=57/728, baseline-delta verifies57ALLnew over8426ddb(v7) andcurrentanchoredv4. RAWsame/externalprotection0; total230/3715RAWproposals notWebgain/weathertruth. v1all0;v2stoppedJSONint64serialization andsuperseded;v3terminalexit0. New0842image inspected/57red offline proposals, imageSHA07198dff..., notliveQCremovals. Main0818widefan stillfails scatteredweather/protectedcolumns andvaryingboundaries; next validweather-island separation in originalfullfan withoutdryflank fabrication/sourcegrowth. Newfan NOTenginewired/deployed/committed; no105worker/jobchanges. ExistingotherdirtyWIPpreserved; goalactive.

Cross-source new58 nowterminalCOMPLETED46volumes/22stations1219cuts, remoteverifierPID3169336 terminalREAD_ONLY_VERIFIED; old/newcandidate maskSHAexactall1219, candidate708176/noresourceabstentions. Sourcerunnerused4threads preflight48575623168bytes, freshMemAvailable~44GiB, both source/verifierPIDs gone normalterminal. Remotereceipts fetchedlocallyfirst-proof-remote-receipts, receiver-floortransfer remainsFalse/notweathergroundtruth/notnormalpublication. Idleanchor1dryrun failedWAITING_MEMORYprecondition BEFORE anymutation since sourcehadprogressedtoCOMPLETED; no anchorrestartneeded. Candidate58 deployment launched DETACHED PID3824911, launchreceiptnear-floor-first-proof-release-v1/deployment-launch.json SHAed5f84dc11d6229bab406989bb2bea4a8ec35435e70b7d8273a8f903ae7187d9 matcheslocalhelperafterpoolCASdrain addition. InitialunpinnedlaunchSSH23578 failedConnectTimeout; snapshot47526 similarlyfailed. Newpinnedlauncher92588 exit0confirmed3824911; upload79692exit0beforethissuccessfullaunch. Deploymentreceipt/pool/actualnewWorkerchannelmustfreshinspect beforeclaimsuccess ornormalplans. Pendingmonitor127? readcurrenttoolhandles (latestfreshmonitor session in tooloutput). No duplicate launcher/recycle.

105 candidate58 promotion CONFIRMED terminalsuccess; promotion.json newfingerprint932cff55d32afce878b642b4320021da45fde2461137503d71f7bb36f8df3b94, childnetworke057e006f231ad1d531d820de441cd11bf5dc3921501581e683247c86a1ba224, image72067... (tagxqc-near-floor-first-proof-58f8633-candidate-mb),4READY. Deployment3824911gone withsuccessfulpromotionreceipt+logs. PoolCAS26->DRAINING27->ACCEPTING28, historical11stalledattempts unchanged. Enableonly3pilotsZF505/ZF701/ZF702/defaultotherstationsFalse/no trustedfusionQPEforecast. Normalreplan+finisher+productaudit latestRAMsafehelpers uploaded (priorSCPs success); new supervise-first-proof-normal.py upload pendinghandle54006; MUSTwaituploadexit0, then launchpinnedhelperSHAguardedonce, no dependentrace. Supervisor replansubmits13normalAPI frozeninputs, max4basedresourcefull6GiBworkerreserve; finisherafter13submit waitsallterminal then3GiBaudits. Actualnormalproducts/UI notyetaccepted. Remainingmechanicalclassification/actionbudgetwarnings mustremainvisible, notcountedfullpassed.

- 2026-10-02 approved independent S evidence continuation: 105 readonly succeeds;25 authorized-window-overlap FMT volumes across Z9591/93/98/99 (6 reused/19 new SHAverified), normalized348MB under1GiB cap. Actual configs datum5737/DEM3855, calibration offsets null; DEM accepted62tiles/42ocean absent; no non-ballpark datum transform. QC worker lacks geometry resources; grid has DEM. 4017-gate native observations:13disputed mincrossheight2.07–2.94km, no compatible positive echo for6points/old1001; W1 crosssite37.5/W2W3 upper echoes. No independent near nonmet labels; goalunresolved. Current safe-default v7 manifest0f5969a0/params409240f9 full11/RAW/export PASS237.56s:all6KEEP/protection530/530/red14/14/orange3500;0newloss/6576restored vsweather-v3. Gate affects more than13; NOT cleanup/deployment. New report docs/S_INDEPENDENT_WEATHER_EVIDENCE_20261002.md, evidence .build/s-independent-weather-20261002; no core edits/newremote writes/deploy/push.

Normalreplan supervisoryworkflow bug found andFIXED: oldbusy=sum(allhistoricalREADYrows) counted11expiredworkers, required83GiB/busy11 andforeverwaiteddespite4currentidle. Current_worker_inventory nowusesexactrunning4containerprefixes+selectedfp932+awareheartbeat0..75s+READY; dedupscontainerbusy andtreats current_task asbusy. MeaningfulRED old11/GREEN live0; staleidentity/age/future/unready ignored,livebusy/currenttask count,duplicatesonce,missingworkerblocks. Regressionprivateadmission-worker-regression.json; liveproofselectedREADY4/busy0/oldsum11. StoppedONLYwait-child3870737 withSIGTERM afterproving0plans/0runs, priorhelperSHA00cbbackup+admission+supervisorSTOPPED_ERROR(-15) receiptsretained. Oldsupervisor3870736gone; nopublictaskstatechanges. Newhelper31aaa1fad7925b0d4dcff46cffae3e78efbe661b113624cb7e5f6b71b8f2491f uploadedconfirmed, guardedretry1 supervisor3970910/child3970911 live; max4normalcomputes. Normalactual10submitted/7SUCCEEDED aslatestfetch; localsnapshotsfirst-proof-normal-current/{label}-task.json andui-result-urls.json. early task7bacba12...attempt1fbf7540..., next6357e050...attempt64c336fd...,701084941aad2ea...attempt4b5545b6...,7020808adaa45ef...attempt26a287f5...SUCCEEDED. Original702b059871f.../40eec546...RUNNING, 50505cff00ede.../ec083c18...RUNNING. Freshresultid MUST task.id+current_attempt, NOT run.id+task.id; verifiedpriorv9 e17a43ae task/4ed48ecb attempt. URLtime useactualsourcevolume_start, oldnominal00:24 mismatchesoriginalactual00:26:55 butscanexplicit preserved. Playwrightoldheadlesssessionclosed, newopenonnext result pending. SSH newControlMasterauto/ControlPersist300 .build/ssh105-xqc-20261002 established/reused successfully (publickey, no secrets); oldControlPathnone intermitttenttimeouts notterminalbatchfail. Goalactive/fullnormalnativeaudit+actualnewmaps stillpending.


### S original object morphology v8 preparation, 2026-10-02
Own feature92dad83 and compact/profile c33a4d4 pushed main/remoteexact. Full original preclear fan + narrow source-footprint v5 now engine-integrated; weather islands never fabricate dry shoulders, nearest original bridge/source-parent/range fixed, no recursive accepted source. Compact native-ref count/SHA + mask replaces repeated JSON lists; final8replay complete-fan-integration-20261002-v3 binds currentbytes, RAW unchanged/protection overlap0. Exact oldstoredvisible gain0818=559/2094,0842=66/728, withdrawn0; offline proposals notnewWeb removals. Fresh0818review SHAe4571434... identical inspectedv2pixelSHA. Latest scoped456tests PASS/Ruff/diffPASS in sharedcheckout; installedv7parent engine/writer smoke pending. CI36992958542 in_progress, notgreenclaim. Remote freshv7refresh DONE all8, bothSworkershealthyv7; newv8package only3committedleafmodules+versionedprofile, installer launched session26612; mustreadexit/installedreceipt beforeclaimdeployment orstartingrecompute. OtherWIPnotstaged, no source checkoutcopies. Goalunresolved: leftedge/unknownparent/isolated residuals remain; no generalized weathertruthclaim.

S v8 deployment CONFIRMED: c33a4d4 image99906cfda947cfa31c3d1c8fbfb46fc45e820a472550d1014fda5e464eb285e0, twoShealthy/X4unchanged. Actual installedv7-parent smoke engineaction/fan2310/writerproof/auditnoaction/weather/RAW/moduleprofileSHA allPASS. Initial wholeparentcompileall failed AppleDouble/null and inheritednonrootpycache writes BEFOREworkersmutation; artifact compile now targets3exactleaf read-only, codebytesunchanged; secondinstaller exit0/installedreceipt. New8time32scan background systemd rainpulse-s-original-object-v8-refresh PID4054755 active/RUNNING00:18Z(CST08:18)/completed0/errornull; profilec506e8ee...; firstQC660fba4c-2943-5d3b-9e77-f83a28523171. No newWeb publicationclaim. CI36992958542 terminalfailure: lint/test/test-performance-cd/aggregateverify failed; build and dedicatedQCchecks passed. Do notclaimallCIgreen orgeneralization/goalcomplete. Remoteoutput .build/s-original-object-20261002-v8-refresh/state.json; nextreadfresh beforeanyrestart.

All13newnormal tasks reachedSUCCEEDED (finishercheckedallterminal beforeallocatingauditor); no newworker/normaltaskfailure. Actualheadlessnewnext6357...result inspectedcut1(1.41deg) screenshotoutput/playwright/zf701-next-first-proof-cut1.png: strongsouthfan+westspokesremoved,smallnear-siteprotectedechoretained/noalertoncut1. Cut3(3.36deg) output/playwright/zf701-next-first-proof-cut3.png inspected: grossfanremoved, sparsefarpoints+near-site/east echoremains, oldDEGRADED_MORPHOLOGY_ACTION_BUDGETalertretained; notfullacceptance. Finisher4001084 failedAUDIT_ENV atfirstearlytask dueunmounted hostpromotion.json path in auditcontainer; supervisor3970910 terminalSTOPPED_ERROR. Alltaskoutputsunchanged; auditfirstpendingempty/logFileNotFound retained. Fixlocalproduct-audit loads /tmp/first-proof-promotion.json withimageIDgate; finisherbindsexactpromotionfile:ro there. Preserved originalhelpersbyteexact SHA345e9... andd6ba... inprivate *before-receipt-mount-fix.py, noaudittestweakened/no13taskrecompute. Uploadattempt53881 SSHtimeout andretry27811 Connectionclosed BOTH unsuccessful; correctedremotefilesnotyetconfirmed. NeedfinishuploadSHAverify, preservepre-fix finisherstate/empty pending/log, launchONLYupdatedfinisher detached (all13runreceipts existing) withnewretryaudit-launch receipt; notsupervisorreplan/resubmit. NewSSHexplicitmaster24200 pending; Control socket previous5minexpired/missing, nowabsolute path .build/ssh105-xqc-20261002 with30minpersist attempted; await beforedependentSCP. Goalactive, nontrustedfusionQPEforecast remainsFalse and classificationwarnings truthful.


### S held-out sparse target continuation 2026-10-02
Prior goal turn progress(deployedv8/started8batch). Newown d0c1465b6470b36860e5e0279f4cbba4859fcc41 pushedmain/remoteexact: targetdistance no longer has to rediscover denseRAWfan; immutableheldoutbands nominate sparse targets, originalparents/range/sourceproof and actual measuredoutershoulders preserved. Leafdefault sparseFalse reproducesv5, sourcefootprint explicitpolicy3 v6; historicalpolicy2 exactreplay remains. RED sparse4gates missed old, GREEN new; unknown/wetflanksretain, 8negativecasesbothpolicies/historytamper; scoped468PASS/Ruff/diffPASS. Private heldout-sparse-target-20261002-v1 final8SHAinputs/c33baseline/output binds oldWeb0818+114/2094 and0842+73/728, withdrawnoldvisible0; acrossRAW0818new2095/withdraw129 duecompeting originalowners ambiguityabstention(notweathertruth),0836new34,0842new926, others0. ActualRAWsame/protection0. Inspected0818newtargetsimageSHA23019418... red114offlineproposals grayRAWnotliveQC. Reusableauditor/renderer retained. d0NOTdeployed: v8batch initialfreshread PID4054755 active/current0818/job660fba4c...; subsequenttwoSSHreadtimeouts notterminal/notrestartauthority. Re-poll sameunit/job; do notswitchSworkersmidbatch orlabelv8productpolicy3. Candidateinstalledruntime smoke andversionedv9profile/package pending. Goalactive,leftedge/unassociatedisolatedstillunresolved. OtherWIPhashpreserved/notstaged; docsS_HELDOUT_SPARSE_TARGET... tracks evidence.

### S unified object candidate, 2026-10-02

8051d7c957dd49bc0838382f3230220fa729ccdd committed only eight owned files. Added immutable object/window records, optional RAW record export from existing variable tracker (legacy defaults unchanged), joint object evidence with local disposition guards, strict short lines and nonrecursive disconnected-original projection. Genuine native sectors remain separate; complete fork/curved/narrowing histories cannot restart. No production configuration/engine/writer integration yet; quarantine API only emits eligible action masks, not new DBZH products. Tests for these paths and existing fixed/variable paths PASS; own new-file Ruff/diff PASS.

Reusable scripts/audit_s_unified_objects.py binds fixed/variable/unified code, exact eight inputs, masks and plots. Final private unified-objects-20261002-v4/report.json fully checked: RAW unchanged/protection overlap0/audit action0, nominal detector runtime0.77–2.14s (excludes validator/plot). Z9591 source-stage remaining overlap0948=90/1024=61; inspected0948 image highlights NW fragments. Exact old stored-Web0818 still0/2094 and0842 only2/728; main mixed/forked wide fan unresolved. v1/v2 superseded; v3 incomplete membership-budget failure; v4 removed redundant single-parent/too-short projection candidates without increasing caps. Next: genuine original-parent subband decomposition, worker proof/config/action integration, RADVOL reference comparison and independent event/site holdout before deployment/readiness. Goal active. No new105 mutations or status claims; previous4054755 batch needs fresh authoritative read before any worker changes. Preserve other shared dirty files.


X 2026-10-02 10:38Z actual continuation: SSH absolute master established22536/SCP50973 success, earlier memory upload uncertainty superseded. Audit-only retry7753 failed native-field KeyError DBZH_QC_DISPLAY (contract exports DBZH_QC); old bytes/state/log preserved. Fixed actual field + exact published native->RAW/QC polar PNG regeneration checks; helperSHA174abaa6b6853382571d8f7491449ef1461ce774dc70c7bb261d7d0b67c51974, read-only retry25916 terminalNORMAL_PUBLICATION_AUDITED. All13 normaltasks/117cuts audited asset/native/evidenceSHA/RAWgeomtime exact/PNGbyteexact/newcandidateQCNaNCR0; 103mechanicalpass/14retainedbudgetwarnings, no task resubmission/SQL/statefake. Local first-proof-publication-audit/state.json, all13verifiedjsonl/taskjson/verified-summary.json SHAchecked. Newreadonly residualdriver50303 COMPLETED13/117 (helperfa34ad.../driver32cdc...) fetchedandinput/outputSHAverified in residual-inventory-v1. Beyond10kmQC>=15 all407027 action3/originalCReligible0, counter104555/local14788 overlappossible; notpollutioncount/groundtruth. Reasons3072/3584/3136 reflect calibration/attenuation/path uncertainty, notnonmetproof. Real neworiginalZF702cut0/2 screenshots viewed output/playwright/zf702-original-first-proof-cut{0,2}.png: majorradial/fansremoved, sparsepatchesremain +truebudgetalert. Nextcontinue residual fullRAW object/reference attribution, including compactprotectedbody vs sparseunclassified; avoid treating raybounding span ascontinuousstripe orassuming gate spacing. No newalgorithmproductioncode thisturn, existing58candidate3pilots unchanged, no trustedfusionQPEforecast/hourlyautomation, goalactive. ConcurrentSHEADd0c1465/WIPpreserved.

### Z9591独立源证据阶段结论 2026-10-02
本任务本机-only：25体扫/六DEM瓦片SHA已验证，4017本机地形样点等于105、缺测0。冻结默认资源接线已修复；232.45s的11层wired回放与unwired所有数组相同，530保护/3500橙/14红全留、新增隔离0，未解决目标。文献+0.32m±0.10m状态研究-only，共同偏移不解释2km高度不重叠。37866上层正回波双侧SNR对照有28门≥20dB（20–100km内8门）；精确55.5/276.2事件签名避开脉冲反例但不是held-out通用源真值。25原始头各站声明模式稳定，不能排除逐射线增益/测试变化。完整右界采集截断、O3相位仍失败；不新增删除/不重复回放/不干预并行v8。最小需冻结目标/保护窗外独立源事件标签或同高度观测，若排模式需06:02:24.674–29.286逐射线状态语义。报告docs/S_INDEPENDENT_WEATHER_EVIDENCE_20261002.md和.build/s-independent-weather-20261002/joint-evidence-decision.json；保护补丁及并发改动保留，未部署/推送。


### S original subband research continuation, 2026-10-02
Optional native_subbands provider preserves frozen RAW boundaries and full matched/merged/forked/unknown window history, five held-out reference windows/150km, unique provider IDs and existing shared caps. Local union no longer cancels a stable original band; pure narrowing/curved weather and genuine forks retained, reliable weather/missing/external barriers cannot act. Providers never project accepted/new subband targets as source ancestry. Default remains v1; no production engine/profile/105 mutation. Scoped fixed/variable/unified/subband70tests PASS, ownRuff/diffPASS. Final private unified-subbands-20261002-v2 report8inputs/code/evidence/images SHA verified, RAWsame/protection0/auditaction0, allv1proposals retained. ExactoldWeb0818=0/2094 STILL unresolved;0842=19/728 vsoldv1 2, not newWebremovals. Source-stage0818overlap412 notsameasoldstoredWebsuccess. Inspected0842figure confirms only smallwestgain; wholeRAWnewcounts not main-targetaccuracy. Nominalruntime.77–2.35s excludesvalidation/plots. Diagnosedmain0818originalfan bilateral simultaneous coverage.61–.79 below.8source gate: need separate-side aggregate held-out joint object evidence, not pertime rules/thresholdlowering. DocsS_UNIFIED_OBJECTS updated with RADVOL research and remaining baseline/independentholdout/enginewriter/integration/Web/isolatednearclutter scope; goalactive. Re-poll existing4054755v8batch before anyworker mutation; no freshremoteclaim thisround. SharedotherWIP preserved.

S subband final correction: own41903be then56cefb9 committed/pushedmain exactremoteSHA verified. Added meaningful target-only-width RED/GREEN: new nominated boundary must occur in >=2 remote original profile windows excluding target±1; old approximate matches cannot authorize self-nominated width. Finalv3candidate version/8data report unified-subbands-20261002-v3 supersedesv1/v2: allcode/input/mask/imageSHAverified, alloldv1proposalsretain, RAWsame/protection0;71scopedtestsPASS/Ruff/diffPASS. Main0818oldWebstill0/2094, source-stage85 (earlier412WITHDRAWN asfinal count);0842oldWeb19/728 stillvsold2; nominalruntime.76–2.21s. Actualv3imageinspected. No productionintegration/deploy/105read orbatchmutation. Researchanswer: completeoriginal object/multiscale morphology + separate-sided heldout measurements + joint weather evidence, independentevent/sitevalidation; not point-by-point fixes or loweredunknownside threshold. Goalactive.


### X repeated-native-ray consensus, 2026-10-02 continuation
Own 12607428343e9b1246ff5a53e66cde9f2acf1e1b committed eight X-only files and pushed main/lsremote exact; concurrent S 8051/41903 and unrelated dirty work preserved. Exact root: original ZF702cut5 ray368(12.545deg) loses right shoulder because original rows0/369 at13.540/13.545deg, 31.67s apart, both become duplicate geometry barriers. Native exhaustive original acquisition choices, no moment averaging/fabricated quiet data; all choices must agree, repeated target rows excluded. Existing time/elevation/trial/model/evidence caps and original weather protections retained. Explicit StrictBool default-off config+JSONschema; normal core/single finalizer integrated action3/NaN/CR0, atomic incomplete view/resource/evidence fallback preserves parent masks. 9 helper +5 runtime regressions; full354X-v2 PASS (existingNumPy ABIwarning), own newfiles Ruff/diffPASS. Actual pre-integration normal test RED missingmask, integration GREEN; originaladaptzero source barrier asserted.
Readonly native-consensus-full-v2 complete13/117 fetched allinput/output/maskSHAverified:114NO_REPEAT/3EVALUATED, additional628visible solelyoriginal702cut5, protection/ambiguousoverlap0/RAWsame; fulloriginal+two viewproof2186720B<4MiB. Local first-proof-publication-audit/native-consensus-full-v2/verified-summary.json. Prior normal13/117=103mechanicalpass/14budgetwarnings retained, notfullweathertruth.
New candidate image e7a65b03a1b82c1abe0c25497486ae242ec5089315fc1a9364c52fa866dd39a0(tagxqc-native-alternative-1260742-candidate-mb) built oninstalled72067, imports/default-offPASS, onlyconfig/core/native_alternatives/pipeline changed,793Python unchanged. Pipeline uses installedparent275aa37be73beb595109322e4e137c278e59803248917c2977fce838590385aa +exact own8lines, avoiding unrelated localS/radome dependency. Local native-alternative-image-verification.json source hashes frozen. No workers/channel switch yet.
105 readonly fullruntime replay native-alternative-pipeline-v1 PID506029 launched with4computes/3GiB/CPU1 and28.35GiB MemAvailable. Latest12/13completed; original702 stillcomputing (pending completecuts0/2 as lastread; readfresh beforeclaim). Probe SHA490bb992... driverd798c514..., allfinishedjobs exit0, no failures. Readonly verifier localSSH session56734 stillwaiting; remote verify-native-alternative-pipeline.py validates all117exactmaskSHA vs prior nativeviews, unchangedRAW/parent masks/NaNCR0+complete4MiB. Do not resubmit or restart replay duewaiting. Source-rejections-v1 fetched but fullSHAverify notyetdone; source-boundaries-v2/neighbors-v3 actual remote proofs notlocallymirroredyet.
Prepared remote native-alternative-release-v1 childnetwork75fb929e0917088f323e17a8abb263f79e26487f1f295ff60e06c450910f52ae changesonlyreleaseid+nativeflag3pilots(parente057...), notactivated. SHAverified7helpers uploaded: verify/deploy/replan/finisher/auditor/normal-supervisor/advance. deploy-native-alternative.py gates complete verified117/628, liveidle4/poolCAS/actualMinIO120GiB100k protection, oldchannel932+720image; rollback preserves failedreceipts. Newnormal pipeline plans same frozen13inputs throughAPI, admissionliveworker fix retained, noSQLfake. Auditor adds newnative masks exact to frozenpipeline proof and existing RAW/time/native/PNGbyteexact checks. Initial generatedauditor syntax failed beforelaunch and preserved local*.prelaunch-invalid; corrected pycompile+uploadSHAexact, no business taskaffected. advance-native-alternative.py PREPARED NOTLAUNCHED; do not claimnormalpublication. Nextwaitexistingverifier terminal, inspect summary, CASdeploy then normal-supervisor, actualoriginal702cut5 and remaining usermaps/UI acceptance. Originalcandidate720 stilllive/3pilots only; no trustedfusionQPEforecast, hourlyautomationpaused, goalactive. ActualMinIOdisk bytes~516.88GiB avail, RAM~27GiB, no unrelatedcleanup thisturn.

X freshlaterread original fullruntime replay completecuts0/2/4, remainingstillCPU100%/1.197GiB/3GiB;12otherjobs complete. Advance guarded continuation nowLAUNCHED PID598209, helperSHA0e17a4cbd258b4a167fb25b4f57639c3693b6d1f44dad13f089413e01c489bdd, receipt native-alternative-release-v1/advance-launch.json. It reuses existing readonly verifier (no duplicate result writers), waits complete/parsed VERIFIED117 exacte7summary, then CASdeploy+normal13supervisor; anyfailure STOPPED_ERROR retainsreceipts. NEEDfreshadvance-state/promotion/normalstate, no blindrerunlaunch; oldPREPAREDNOTLAUNCHED note superseded foradvance ONLY. Initialauditor corrected beforethis launch, all7remotehelpersSHAverified; advancechangedafterverification then ownSHAguardedupload+launch confirmed. Actualnormal+UI notyetclaimed.

### Z9591通用形态候选交付 2026-10-02
新独立fan_morphology.py+契约/测试/本机auditor，不改worker/保护/安全默认。final-v4 35cut RAW相等；橙3500/红14全部held-out形态匹配、有界0/source未知/删除0。强子带149.49–201.10°右肩原生角无gap但相差31.766632s，不能当同时刻边界；不是完整混合父外界。已修复单射线径向支持、连续径向/横向来源桥、目标双肩同距6dB对比；27新回归+相关108PASS。真实上层正回波1873/39116也匹配（非已标注误伤率），自然径向天气合成7200形态FP，禁止据此动作。报告docs/S_GENERIC_FAN_MORPHOLOGY_20261002.md；.build/s-general-fan-morphology-20261002/final-v4及交接清单。连接已恢复、未重复启动worker、不干预并行批次、不push/部署；删除目标仍未解决。

X later continuation: full native-alternative-pipeline-v1 nowCOMPLETED13/117 and verifier56734 exited0VERIFIED, every117candidate maskSHA exact to prior exhaustive nativeviews, additional628visible/RAWsame/parentwithheld retained/weather0/QCNaNCR0/complete4MiB. Local state+verified-summary fetched; early/next JSONL SHAverified, zf505-02 JSONL PARTIAL UNVERIFIED (bulkcopy29553 SIGTERM solelytofreebandwidth; originalremoteproofs intact). Do NOT claimallfullJSONL locallyverified.
Advance598209 STOPPED_ERROR pre-mutation because I erroneously froze releasechannelrevision>=26 (pool revisionconfused withrelease). Actualfreshchannel932/revision24; originalbadhelper/state/log retained, no initialpool/worker changes. Corrected exactCASguardrevision24 +existingfp, deploymenthelperbefore-fix retainedremote; retryresume615595/helperc797e... launched SHAguard. ActualPROMOTED: imagee7a65b03..., network75fb..., fingerprint37c2e265a2cb513a1221525432cbeb75aee4f21af38e2c5dc9488f8ce3783136,4READY, pooldrain/resume CAS succeeded, only3pilots, no trustedoutputs. native-alternative-release-v1/promotion.json authoritative. Old failedadvance receipt intact; inspect advance-retry1-state.json, NOTblind restartoldadvance.
Normalsupervisor620277/replan620278 RUNNING;7submitted aslastfreshread, next/early/7010849/7020808/50502SUCCEEDED, original702task0f591f01-835e-4232-9f09-914eaf364871 attemptaa565b57-57a9-496b-99bb-309bd9501f98 and50505task4ce99c30... stillRUNNING. Admissionmax2 dueMemAvailable28.3GiB/full6GiBworkers+6GiBreserve,2busy/4READY; original7029min100%CPU/1.202GiBof6GiB/noerror, notstalledclaim/restart. Whole13normalproduct/117audit stillPENDING; finisherwaitsallterminal thenverifies newnative masks against immutable fullpipeline proof+existingRAW/time/PNG/nativeSHA.
Realnewnext7013.36deg (task4b78352e-b139-4d73-89db-a5aeef291cb9 /attempt121f35f8-a0ee-4fbb-b4f3-303b00eea426) screenshotoutput/playwright/zf701-next-native-consensus-cut3.png viewed: majorfans cleared, sparseprotectedpoints+near-site/eastweather remain, trueDEGRADED_MORPHOLOGY_ACTION_BUDGETalert retained. Oldbefore original702cut5 screenshotoutput/playwright/zf702-original-before-native-consensus-cut5.png viewed: longnorthstripe stillvisible oldb059...40e...; new0f591...notyetpublished, DO NOT labelnewmapfixedyet. Needfreshneworiginalcut5snapshot, andcrossangles/cases.
Macdirect105:4173newTCPtimedout, ambientproxycurl502; serverlocalhost4173HTTP2000.4ms confirmed, notWeboutage. Browserexistingconnection eventuallyloadedoldresult; freshrealUI via existingkeySSHmaster forward127.0.0.1:14173->105localhost4173 HTTP200153ms, no source/appchanges. Playwrightnewheadlesssessionrainpulse-xqc-headless/2000x1250 active currentlyOLDoriginal702cut5 throughtunnel. Useexplicitnewtask.current_attemptpin andnormalAPI sourceactualstart (original002655/nominal002400oldlink), no guessedtimestamp. Reuseexisting tunnel, donotduplicatelisten. Browserbulktransferstoppedforbandwidth only. Goalactive/remaining compact-protected sparse cases notgroundtruth/14oldbudgetwarnings persist; hourlyautomationpaused, concurrentSdirtyfiles preserved.


### X normal publication and native receiver-floor root cause, 2026-10-02
Fresh normal native-alternative v1 terminalNORMAL_PUBLICATION_AUDITED13/117. All13 task/receipt input+outputSHA locallyverified, original/native/time/PNGbyteexact proof;103mechanicalPASS/14budgetwarnings retained. RealNEW original702cut5 output/playwright/zf702-original-native-consensus-cut5.png viewed: oldnorthgreen longline~110–210km removed, sparseblue residual remains. SSH14173forward reused, noWeboutage. Readonly native-floor-diagnosis-v1 PID817931 terminalCOMPLETED13/117, localallSHAverified;3repeatcuts, 257additionalvisible>=5 belowfloor (256original702cut5 +1 0808cut5), nohard/local/compactprotectionoverlap. Root: adapt.available clearsallmoments onduplicategeometryrows; oldcore floor wronglyinheritsangularbarrier though independentnativeSNRvalid. Ownscope repair noise-censor core uses originalmoment_support +OBS/noecho/DBZH range/hardmixed, samefloor/coverage/fractioncaps; spatialgeometry unchanged/defaultNone. New2regressions actualRED beforefix, GREENafter;15floor testsPASS. FullXsuite running tool92191; no newimage/deployyet. ConcurrentS dirty/untracked preserved, goalactive; hourlyautomationpaused. Need immutableparent e7 minimalcore/config candidate image, actualpipeline delta against fp37 products thennormalCAS/publication/map. No blanketaction3delete ornewtrustedoutputs.


X native-floor repair b1a267a then test-format23c01714442cc110db505d0e949c96d2c460cd49 committed/pushedmain exactlsremote. FullX-v2 362PASS, 15floorPASS, new2 actualRED->GREEN; owntestRuff/diffPASS; existing10core/config lintitems retained (9config +1oldcore), no wholefilecleanup. Minimalcandidateimage e6a1f199d898b53c09f15cdc97e77b27ed52bc86d1a727bb7834f2ee8289d34a built oninstallede7, onlycore/config changed795Pythonunchanged/importdefaultoffPASS, sourceSHAfrozen. Native-floor-diagnosis13/117verified257visible5belowfloor. No new liveversionyet.
Readonlypipelinev1/v2 STOPPED/FAILED harness mismatch: singlecutinitialprobe omittednormalboundeduppercontext; GroupCutsv2still hadtinycontext arraydifferences vsstorednormal. ActualUNMODIFIEDe7 parentprobe onzf50502 reproducedsame2XQC_CONTEXT_DONOR mismatches, so notfloorregression; priorfailedreceipts retained, no business taskmutated. Newpairedv3 executes SHAverifiedINSTALLEDparentcore in-memory asreference(no sourcecheckout/copy) andnewcore onidentical GroupCuts-boundRAW/context. PID1006194 4CPU/3GiB each, latest9/13done117totalpending4. All9exit0, no errors; remainingoriginal702/0808/50505/702 CPUs~100%,~.9–1.52GiB<3GiB. Eachnonrepeatcut allparentfieldarrays exact, oldfloor masks exacttopublishedoldfloor, incrementaloriginalvalidbelowthreshold/RAW/spatial/weather0 andparentwithheld retained. No sourcefit/cap relaxation. Needfreshstatebeforeclaim13complete.
Networknative-floor-release-v1 childa9c78e6641044e11c89013bc0cb1c5fc35c4666aff483e8c2b7b467b8358b942 changesONLYrelease_id, parent75fb fingerprint37c2/channelrev25 frozen; noflags/stations ortrustedoutputs expanded. Seven helperSHAfixedanduploaded: verifier/deploy/replan/finisher/supervisor/productauditor/advance plushelperSHAmap. Guardedadvance launch sent tool55312; MUSTreadexit0+actuallaunchreceipt/advance-state beforeclaimRUNNING orrelaunch. It waitsCOMPLETEpaired117thenSHA/257diagverification, CASpooldrainidle4/fp37rev25/MinIO120GiB100kinode, deploye6 andnormal13+nativeflooradditionalexactmaskedagainstoldpublishedNPZ+PNGbyteexactaudit. Productauditor mountspriornormal readonly; do not useoldnative-source maskSHAforadditionalfloor—theverifierkeepsseparateoriginalnativesourceSHA. SourcehelperpycompilePASS; startisnotacceptance.
AdditionalREAL currente7 mapzf7010849cut0 viewed output/playwright/zf701-0849-native-consensus-cut0.png: formerbroadnorth/west/eastgreenradialfanbands gone, sparseblue/near-sitepoints remain. Currentheadlessbrowseronthismapvia14173existingtunnel. NewfloorstillnotUIproof. Goalactive, hourlypaused, sharedSdirtyuntouched.

X finalfresh confirmation: upload59026exit0, advance launch55312exit0 confirmedPID1050112; advance-launch.json andadvance-state.json actualWAITING_PAIRED_REPLAY withverifierchild1050113/helperSHA426d1fda0b6220697ff78da9935873f972d41864214d5345ba552b757f0821af. Pairedruntime1006194 stillRUNNING9/13, noerrorlog; remainingactive4, notclaimedcomplete. SourceprobeSHA8a7ed8deeb25d14c6e9ed95b6c8bff27d959d234695c58b0f1dff2c20aaadb31 andinstalledreferenceparentcoreSHA d5bade729cc2314a9425735d3f238b7efb937d940e6110fc3c19c1496a9ca63d. Freshinspect existingadvances/promotion/normalstate beforeanyduplicateprocess/tasksubmission; auto normalmustnotbeclaimedatWAITINGstatus. No newlivee6yet/realfloorUIpending.


### S unified candidate v5 engine/writer integration, 2026-10-02
Own default-off unified flags, independent v3/separated providers and full proof now bridge revision engine -> BroadSource/P2/finalizer -> actual Zarr writer. Reviewer found RAW moment/coordinate binding gap; eight RED counterexamples reproduced it, canonical RAW DBZH/SNR/RHOHV + range/azimuth/order/good/gap binding fixed it. Refreshed security/architecture reviews clear. Exact INDEX algorithms + tracked scoped tests:629PASS; real pinned Py-ART/wradlib apply_basic_qc->validatedZarr shuffled-ray test1PASS (dependency warnings retained). Eight v5 input/code/NPZ/PNG SHA rechecked; OLDWeb0818=742/2094,0842=19/728 offline proposals, not new deletion accuracy. 105 v8 batch freshDONE8/8, Resultsuccess/exit0, not v5. No flags/deployment/worker changes; upstream shared weather-forwarding excluded. Remaining object texture/classification, true RADVOL baseline, independent event/site holdout, installed runtime/Web acceptance; goalactive. Other sharedWIP preserved.

Final adversarial review reproduced invalid-SNR<-50 quiet-shoulder1080gate false proposals/actions; v6 uses valid quiet[-50,3] at reference and finaltarget, newRED/GREEN regression. Final631scoped/actualrunnerPASS (three dependencywarnings), refreshed assumption/composition/abuse reviews and bounded-cascade check clear. Final8data v6 report unified-valid-quiet-20261002-v2 replaces v5 after freshfull SHA binding; exactOLDWebcounts742/2094 and19/728 unchanged. No newdeployment; default-off candidate only.


### Z9591 acquisition source audit, 2026-10-02 (local only)
Independent acquisition_components v2 + contract/tests/auditor, production unwired. Final-v5 85 cached cuts RAW same; fixed SHA-bound declared processing hypothesis removes whole-cut coefficient leakage, odd/even explicitly conditional, whole-segment elevation bound, weather absent on84cuts explicitly unknown. Orange2702/3500/red14/14/protection19/530 signatures; all target reference-bracketed0, O3strictphasefails, actions0. 128 scoped tests/RuffF/diffPASS. Closed coherent weather control demonstrates joint-feature ambiguity, no override. Two localRAW TREF/KDP probes: next06:07cut2 coherent42455 gates TREF−REF P90=0; cannot prove target source cause. Reused HEAD4407321 unified-v6 on25lowcuts with proof replay: target3proposals, orange/red/sixpoints0, allactions0/RAWsame; no othermodule edits/deploy/push. docs/S_ACQUISITION_SOURCE_COMPONENTS_20261002.md + .build/s-acquisition-components-20261002/handoff-receipt.json bind evidence. Goal unresolved: source-event/processor semantics or same-event independent cause evidence needed; avoid extrapolation/threshold widening/broad parent deletion.


### X native receiver-floor publication complete, 2026-10-02
All13 normal tasks/117cuts terminal NORMAL_PUBLICATION_AUDITED on105 e6a1f199/fingerprint f3f7dcea836d438486e1b9e2c73e8f795be6f51cd23f6490de6882f656244e03. Complete normal receipts/task/native+polarPNG audit downloaded and allSHA locallyverified: native-floor-publication-audit/verified-summary.json;103mechanicalPASS/14existingbudgetwarnings retained, no weathertruth/QPE/fusion promotion. Three native repeatcuts additional373gates/257formerlyvisible beyond10km(340/256 original702cut5,26/1 0808cut5,7/0 70202cut5). Independent normal69-field delta v2 complete13/117 VERIFIED:114allarrays identical,3expectedfloorchanges plus exact existing downstream phase prefix/bits;RAW/coords/time unchanged. Local native-floor-normal-array-delta-v2 all13input/output receiptSHA verified. Initial v1 strict local whitelist flagged PATH_REASON/XQC_PHASE_BLOCKED/XQC_REASON, not product regression; v2 checks exact prefix+existing256/512/16384bits, onegood+3tamper controlsPASS. Failedv1 receipts retained, no compute task rerun. First v2 launcher preflightSHAfailed beforemutation due uploadedhelper timing; subsequent SHAguardconfirmedPID1360260 terminalVERIFIED, no duplicate active process.
Paired full40MiB transfer65971 exited255 connectionclosed; partial retained as *.tar.gz.partial, never full-local proof. Complete compact257127B SHA fed3c46c184f2c1c6c924d9b466c57753c4072e99ee88933b2eda4eeec5396bb fetched; all117maskSHA/inputstate/physicalguards localPASS; full evidence table remote intact. SSHmaster expired/lost, listener14173 absent proven; re-established existingnamedmaster forward keySSH/serveralive30/3600persist afterabsence, browser resumed. NoWeb/Worker restart.
Actual NEWmaps viewed: zf702-original-native-floor-cut5.png, zf702-0808-native-floor-cut5.png, zf701-next-native-floor-cut1.png. Strong originalfans/spokes/repeatednorthweakstripe gone, near-site/sparseprotected residuals retained; original702budgetalert remains. Originaluser b2fc pinnedURL accuratelyshows historicalversion, real click latest switched to47c53894-7f02-49af-ba82-fffc703cbcac.bd453fbf-3ff6-4551-9bfc-bd610a084445 same scan/native sweep. Browser now on thatlatest original702cut5. An earlier attempted701URL had incorrect guessedscan/time, discarded; actual screenshot uses frozen scan93d2d253... UTC00:07:21.302Z.
OwnWeb8f5c545 committedpushed/deployed15assetsSHA+HTTPindex exact/no servicereboot. SharedTimeline explicittimeBasis analysis keeps shared6minaxis, rawheadernative; newregression actualRED->GREEN;21UItests/buildPASS. CI37013778349 failure(test/lint/performance-cd); parent23c0171 CI37008733199 samejobsfailed and exactmissinglegacyRP029AdminWorkspace; wholeCI NOTgreen. ConcurrentS4407321 committedbyotherwork after8fpush, ownX/UIS unchanged; preserveotherdirtyfiles. Ownpublicdoc00684fa recordednormalproof committed; pushsession currentmustwaitactualexit/lsremote beforeclaimpublished. GoalACTIVE:14budgetwarnings/unlabelledprotectedresiduals still require object-level acceptance, cannot blankethideaction3 orraisebudget; hourlyautomationremains paused. Next focus only remaining14 and actual continuity/source/protection evidence, no vendorunit guesses or unrelated S changes.

X final doc push30576 exit0 confirmed origin/main 00684faee19f84861f5483ae04345121e1f7904e. OwnX/UI/doc clean; remaining dirty files belong to preserved S work/memory. New documentation CI queried separately; prior Web commit CI failed as documented, no all-green assertion. Goal still active for retained14 warnings/residual acceptance.


### X residual budget and mixed-contour diagnosis, 2026-10-02
Read-only installed e6/fp f3 diagnostic native-floor-residual-budget-v1/v2 terminal COMPLETE14warning cuts/5normal tasks, all local input/output SHA verified; reconstruct exact existing action-cap branches, ACTION_BUDGET-marked visible gates0 across14, not full acceptance or reason to clear warnings. Actual maps output/playwright/zf505-05-native-floor-cut4-residual.png and zf702-original-native-floor-cut2-residual.png inspected+SHA bound. ZF50505cut4 continuous visible5 max40.125km belongs completeRAW94.7deg/axis1.12 mixed blob; clear near/other gross spokes gone but NE mixed bands unresolved. ZF702originalcut2 120–132km compact4ray/axis2.02 protected strong patch retained, NOT weathertruth. ZF505cut5 visible5max33.3km, after >=15dbz+existingprotections no>=20kmcontinuous unprotected row; do NOT equate with no contamination.
Fresh original RAW source trace native-floor-merged-source-fits-v2 PID1651495 terminal COMPLETE2cuts/8mode traces+2selection rows, locally fullSHA verified. 2longest unprotected505cut4 rays64.25/68.23deg conventional50/90fits do pass reference models, but322 fit-cache-MISS gate-model evaluations all SNR outside sourcebounds/both boundsmatched0; not unique-gate count/rate, not evidence all cached targets tested or confirmed rain. Nearfloor references insufficient. Cannot inherit near source to far field by widening bounds. Previous tracev1 driver STOPPED_ERROR wrongly assumed2longrows eachcut; valid8output records preserved remotely; v2 explicitCUT_SELECTION handles actual0 selectedcut5 without fake success, no business tasks changed. 6existing weather/missingshoulder/fixed-km/history controls PASS. Local .build/xqc-polar-morphology/residual-budget-diagnosis-verified-summary.json binds all receipts+2screens. Doc d56b93d committed main, push current turn must wait exit0/lsremote exact. No algorithm/flags/newproduct/service changes; all diagnostic processes terminal, no duplicate active workers. Shared Sdirty preserved. GoalACTIVE/hourlypaused: next actual mixed-object boundary/source transition and weather-safe separation, not blanketaction3hide/stationangle/cap changes; no independent truth/promotion.

X residual doc push70024 exit0 confirmed origin/main d56b93d7115a7e3856aa48ce6d43385903a652c3. Fresh remote three diagnosis states COMPLETE, PIDs1519942/1561812/1651495 gone normally; NORMAL_PUBLICATION_AUDITED unchanged, MemAvailable24.9GB. No active new diagnostic or product recompute.


### S unified V8 bounded branches, 2026-10-02 (candidate only)
Own object_branches.py now retains immutable original fork/merge parent range, exact original members/negative history, independently held-out body occupancy + bilateral edges, and bounded weak-member inheritance; ambiguous intervals/wet shoulders/weather/barriers retained, no recursive ancestry. New strict default-off flag wired through engine + canonical RAW writer bit16 and all six policy combinations. Wide-fan aspect and variable-width template bugs reproduced RED then fixed; actual shuffled-ray apply_basic_qc->Zarr branch proof passes. Isolated owned+HEAD sources679PASS/3dependencywarnings, Ruff/diffPASS. Final8 audit-v3-variable-boundaries source/input/NPZ/PNG hashes verified: old v7 proposal arrays identical, new gains0/withdrawals0, 200 overlapping nominations/0confirmed; OLDWeb0818=742/2094,0842=19/728 still offline candidates not deletion accuracy. Final0818 image inspected/no claimed gain. V1/v2 superseded; no commit/push/deployment/production flag/worker changes. Next separate whole-window geometric/weather/unknown conflict attribution and complete joint along/across mixed-object/fragment classification, not another template-tolerance widening. True RADVOL baseline/independent holdout/near-site+isolated-point stages/105 consumedWeb acceptance pending; goal active. Concurrent shared WIP preserved; engine ownership only texture+branches argument lines.


### X available-day normal backfill and adjacent contour diagnosis, 2026-10-02
Latest user goal explicitly reiterates generic algorithm. Catalog/source freeze0c2c6f... actual204 NORMALIZED scans:50566/70170/70268, observedUTC00–07/BJ08–15 ONLY, not24hours. Existinglatestf3/e6 products13, stale191. Current candidate algorithm/image/config/caps unchanged. Started normal API per-minute/source-frozen/idempotent batch native-floor-pilot-day-v1, ownscontrollerPID1950275; resumed sameledger after startupurllib scopeerror RED/GREEN beforetasksubmit and shared diagnostic resource reservation guard RED/GREEN; alloldfailure/state/launch receipts preserved. Up to4normals byfull6GiB cap+host6GiB,120GiB/100kinodes+remaining productdiskreserve; selected4 liveprefix heartbeats/fp checked beforeadmission; readonly3GiB audit +diag slots reserved. NoSQL/service/Worker restart. Fresh15:40Z26 audited234cuts28qualitywarnings,2RUNNINGnativeheartbeat, controlleralive; launch is NOTcompletion. Complete local stage snapshot24/216/23 allfile/input/source/nativegeomtime/PNGbyteSHA verified in .build/xqc-polar-morphology/native-floor-pilot-day-snapshot-v1/verified-summary.json. No meteorological acceptance/fullUTCday claim. ActualMinIO509476839424 bytes/99034680 inodes, MemAvailable~21GB.
Readonlyadjacentcompletecontours native-floor-adjacent-contours-v1 PID1950276 terminal COMPLETE6volumes12cuts, all localinput/outputSHA verified; reservation removed. Six505neighborsUTC04:43–05:13 cuts4/5 independentlyoriginalfull15parents, cut4width80.8–90.8degrees, originalproblem05:01range18.7–180.7km/radialvariance>transverse vsfiveotherparents54.7–142.9km/transverse>radial. Longvisiblecut4center-gaplength42.375/45.225/44.775/40.125/56.1/48.675km; auditorfootprintlength adds75m. Source/parentmergechangesgeometry, no rawwholeparentdelete orweathertruth; centroidchangesnot objecttracked/velocity evidence. Newrealmap output/playwright/zf505-0507-pilot-day-cut4.png viewed (mainNW/Wspokes gone,broadNEbodyretained). Local native-floor-adjacent-contours-v1/verified-summary.json binds12native membership/input/outputandactualscreen SHA. Next normalbatch complete+catalog/latest/liveUI acceptance, then mixedoriginalsubbands with independent shoulder/source and rain/fork/missing controls; genericno station/angle/time overrides, no trustedfusion/QPE/forecast. Hourlyautomationpaused, goalACTIVE. New own publicdoc needs commitpush verification; allconcurrentSdirty preserved.

X final15:45Z: ownpublicdoc0ecb908bcf62eb0397e6113b318e8cd1ae2b46cb committed/push exit0 and lsremoteorigin/main exactverified. Freshdaycontroller1950275 alive WAITING_AUDIT_RESOURCES,32ledger/30audited270cuts/28warnings/oneactive,MemAvailable20909633536,MinIO509263613952bytes99020584inodes. Diag1950276 PIDgone/COMPLETE/reservationremoved. Stage-localproofstill24/216/23 (later30remoteonly nofalsewholelocalclaim). GoalACTIVEupdateduserrequiresgeneric; no completion/block/pause called.


### S morphology generalization reassessment, 2026-10-02
Read-only original-provider attribution completed8/8, exact v8 nomination counts and input/module SHA verified; evidence .build/s-object-branches-20261002/readonly-veto-attribution.json. Distinct attempted cache bodies overlap spatially and include non-nominated templates, not labelled object/gate counts. Z9598 0818 combinedveto24376/localproxyonly14658, median conflict-cell fraction.020;0842 40111/22617/median.025. Causes separate suppliedweather/conflicts/RV2barred/localRHO-SNRproxy/native-rowinvalid; no claim all masks independent truth. Z9591 conflicting shares often muchlarger. Next prioritize bounded mixed-object/fragment joint classification, distinct hardgeometry/positiveweather/localcompatibility/unknown, completeRAW retained; do not fabricatecleanedges/dropnegativehistory or blanketdeleteprotectedmembers. Strong morphology route requires weather-counterexample acceptance, actual RADVOL and untoucheddate/site/object labels. Research docs updated only; no algorithm/flag/worker/deploy/product changes, no new gains. Goalactive/sharedWIP preserved.


### X complete wide history and mixed-source diagnostics, 2026-10-03
Own8d5583999b2e435356d3046143b74f71dfa72c80 committed/pushedmain exactremoteverified: measure_entry must retain oversize original predecessor for finish qualification, never erase negative narrowing history. Contract +13 actualRED/GREEN regressions (2policies/2bearings/3native resolutions+elevations/independentline), scoped140/fullX-v2375PASS/RuffdiffPASS. Currentnormal105 e6/f3 unchanged; no livepatch/deployment. Read-only complete-history-shadow-v1 PID2172661 terminal13/117 allinput/output/deltaNPZ SHA locallyverified. Parentmask publishedexact/RAWsame/counterexamplesexact/protectedoverlap0/resourceabstentions0. Added0,withdrawn14761 (main505cuts2/6),sole-morph original5far10withdrawn4263; stage-only subtraction, NOTfullpipeline finalmap/weathertruth. Mixed505cuts4/5no newgain; do not deployguardalone claimingcleanliness.
SixneighborUTC0443–0513 cuts4/5: native-floor-mixed-bilateral-v1 complete6/12 allSHA local; selectedunprotected15longrows have observed wet immediate shoulders, no selected visiblewindows bilateral6dB80%contrast. Initialreadonlydriver outputkeyraw_exact mismatchSTOPPED_ERROR afterfirst2cutcalc; correctedraw_unchanged, failedstate/log/pendingretained, no business taskmutation. Retry2244921terminalgone/reservationremoved. Mixed-polar-v2 PID2294773terminal6/12 allSHA local; originalRHOHV+SNR+ZDR existingcfg only,no PHIDP/VR/SW inference. Problem4dynamicrays jointvisiblebad85/567,281/568,163/343,140/725 vsneighborlongrows2–17. These selecteddiagcounts NOTaccuracy/weathertruth; clear originalpolarcontrast vsadjacentrows suggests mixedsource but cannotblanketdelete.
Mixed-source-geometry-v3 PID2335694terminal6/12 fullinput/outputSHA local: immutablefields+completeoriginalobjects retained; temporarylowrho/SNR support geometryview explicitlyNOTobservation/missing/publicationsemantics. Existingpointconnectedextract_objects gives0newvisible/0newjointqualification, targets64.25/68.23still0. Rejectsimplisticlowrho-mask->oldobjects solution: labels remainpoint-connected despitewindow support, interspersednormal/unsupportedcells fragmentlong anomaly. Next completeoriginalphysical-window joint anomaly+bilateral measurement trajectories withnegativehistory/protection/source/fork/unknown controls, not thresholdwidening or fillingholes. No newformalcleaninggain thisturn.
ActualnormalNEW7010849cut0 map output/playwright/zf701-0849-native-floor-recheck-cut0.png viewed: majorrawbroadspokes/fanscleared,currentnormalf24984aa.c0aa3f6f pinned;near-site sparseblue retained. Notnew8dshadowproduct. Headlesssessionrainpulse-xqc-headless reused14173SSH tunnel.
Fresh2026-10-02T16:32:34Z daycontroller1950275 actualPIDlive RUNNING105/204audited945cuts29warnings/submitted106;rawavailablecoverageUTC00–07ONLYnot24h,UI/weatheracceptancefalse. Allfourread-onlydiagPIDs gone/reservationremoved, no poolworker/service restart. MinIO506134781952B/98808917inodes,MemAvailable19882905600,120GiBreserve unchanged. Localpilot snapshot still24/216/23; later105remotereceipts notyetall locallymirrored, don'tclaimalllocal. GoalACTIVE/hourlypaused/no trustedXfusionQPEforecast, concurrentS/fusion/Web/APIdirtypreserved. Ownpublicdocdd4e96e recordsabove; confirmpushsession27421 exit0+lsremote beforeclaimremoteexact. Nextreadfreshdaystate, keepfrozenpublicationbatchrunning, implementgenericwindow-object route withactualnegativecases beforecandidatepromotion.

X documentation push27421 exit0 and lsremote origin/main exact dd4e96e01df886bf87c85c1f8761cdd9303357c0 confirmed; own fourcode/contract/test files +doc clean. Goal remainsACTIVE, no completion/block/pause transition.

### S V10 mixed-branch candidate reassessment, 2026-10-03

Default-off V10 local compatibility localization + fullRAW directional axial/exterior measurements + unique disjoint parallel branches implemented; hard/weather/unknown/negative history and original ancestry preserved. Shared immutable member storage charged once without raising caps; expanded real nominations exposed and fixed prior double counting. Owned+HEAD suite683PASS/3existing warnings; final8 audit-v4 report55d3874e... input/module/NPZ/PNG SHA verified:1079overlapping nominations/0confirmed/new0/withdrawn0 on all8 vsV8. Local joint-positive windows0818/0836/0842=416/1157/575 overlap, not labels; no object-level gain. OLDWeb overlap742/2094 and19/728 unchanged/offlineonly. Cached8plots rerendered with separate renderer/producer SHA under .build/s-mixed-object-20261003/rendered-v1; reusable auditor handles singlemethod/title. No commit/push/105worker/product/deploy changes. Docs now prescribe complete narrow/fan/discontinuous/short/parallel families + explicit mixed-segment support/weather/opposition/unknown, actualRADVOL and independent holdouts before promotion; near-site+isolated stages/Web acceptance pending. FullgoalACTIVE; concurrentWIP preserved.


### Z9591 parent-history continuation, 2026-10-02 (local only)
HEADdd4e96e read; X8d558399 earlywidth fix not direct S targetcause: existing349fullparents retain210widewindows,20dBZ/20kmparent315 containsorange3500/red14/protect509. Isolatedwidehistory-only25lowcuts restores67bands/target3 butprofile/subband0. NEW measured S samplingbug: maxstep<=1.5minstep discardsnormal~1deg dueactual.1deg dense sample/no nativegap. One-line native_subbands median-spacing correction +4 meaningfulRED/GREEN;618scopedPASS/RuffF/diff. MultilevelRAWdiagnostic now30dBZgeometry3500/14 (also316protect), references0; actual25unified proofreplay final3nearbyproposals, orange/red/sixpoints0, RAWsame/actions0. No threshold loosening/worker/deploy/push/remote reads/backfill. docs/S_PARENT_HISTORY_SAMPLING_20261002.md + .build/s-parent-history-20261002/handoff-receipt.json; safe deletion stillunresolved, independent source qualification needed for frozen strong-level boundary, not local coherent moments alone.


### Z9591 acquisition boundary review completion, local 2026-10-02
Supersedes whole-stencil median sampler: sharedacquisition_geometry uses originalrayorder + continuousdense/sparse modes/localcadence, clockreverse/duplicate/pause/censoredsourceends, fullsegmentel. Adapteraddsacqgap/segment; reviewgeometry/subbands/variable/components use it; availablecanonicalclock boundsserializedproof. Targetnative204 orig365(201.10)→orig0(201.20) actual31.766632s nowbarrier, no fakeclosure. Optionalsubbanddiagnostic sink allpredicates/nonexclusivecounts+primarypartition; qualifiedprofiles0 notweatheronly (30dBZ80runs:23clockfail,54excluded,54occupancy overlapping). 25lowcut exactproof RAWsame/allproposals0/actions0;85acqnewsourceordertopology RAWsame/targetsignature2702orange14red/alltargetbracket0/O3fails.685PythonPASS plusfinalfocusedchecks/RuffF/diff; CnativecontrastblockedexistingDarwinld--gc-sections, unmodified. docs/S_ACQUISITION_BOUNDARIES_20261002.md + .build/s-acquisition-boundaries-20261002/handoff-receipt.json freeze. No newremote reads/deploy/push/backfill; dirtyparallel preserved. Safe deletion unresolved.


### S original trajectories and pre-confirmation RAW joint windows, 2026-10-03
V11 original sparse/short trajectories + exact RAW extent/statistic reuse and necessary geometry screens implemented default-off; no cap/threshold widening, complete negative/weather/gap history and nonrecursive ownership retained. Failed resource-v1-v4 and source-drift-v5 receipts preserved. Reusable audit --freeze-sources captures all nested Python bytes in memory with early producer/input and executed-module SHA, no source copy/extra checkout. Final audit-v6 reporte0999dd5... exact8input/NPZ/PNG verified; proposal additions/withdrawals0 vsV10, not effectiveness/readiness.
New object_window_evidence.py measures full RAW dBZ/linear-Z along/across + endpoint coverage/occupancy/contrasts before confirmation, actual DBZH without SNR; compatible/protected points remain measured, missing notzero, native gaps/invalid interior notbridged. Read-only window_evidence_enabled changes no masks. Serializing native integers + final-report failure handling + recycledtuple-id cache bugs reproduced RED/GREEN and fixed. Final frozen-current-working scoped747PASS/3existingwarnings incl actualrunner/Zarr; all14 executed audit sourceSHA match tests and finalfiles. Earlier128failed/618passed suite used wronglyresolvedvenv symlink/base interpreter missing skimage/PyART; failedreceipt retained, correctedlauncher records actualruntime/deps.
Final private window-evidence-v3 report480c221a... /verification30fa6d37... binds8snapshots/PNG/NPZ:27896distinctRAWrectangles,53532overlapping unconfirmed annotations (notlabels/recall), all11arrays/sample identical toV11 baseline, measurementwork23.06-48.58M under50M. Intermediatev1 JSONfailure/v2 retained. Actual0836plot inspected/residualsremain. docs/S_UNIFIED_OBJECTS_20261002.md records reusablecommand and scope. No new classifier/action gain/commit/push/105mutation/deploy; next competing continuous/intermittent joint object routes using pre-confirmation measurements, actualRADVOL + labelleddevelopment/independentholdouts thennear-site/isolated/Web acceptance. FullgoalACTIVE, allsharedWIPpreserved.

### X complete available replay and default-off window integration, 2026-10-03
Currentca5a2f6 main/pushed exact before own new integration. Normal105 f3/e6 remains:204/1836 normalcomplete,7344PNG comparisons,37warnings(23incrementalmorphbudget+14overallbudget) allnormalreceiptSHA/source/task/native bindings locallyverified under native-floor-pilot-day-complete-v1; onlyUTC00–07 available, not24h/weathertruth/UIacceptance. V2bd69 full204/1836 read-only COMPLETE and alltask/output/source/native/maskSHA locallyverified:59768proposed,1163visible5/927visible15;505149/149,7021014/778,7010/0, fournewvisiblecuts only;RAWsame/protected/contextweather overlap0/resources0. NOechohypothesis discarded6020object/12040referencewindows no validnoecho; oldref wrongly discarded knownfalse conjunctions when unrelatedoperandunknown. dc55 diagnostics402PASS masksoldexact; e898contrast417PASS, pushed; fullproof not production/weathertruth.
Owndefault-off strictpolar_window_candidates_enabled + derivedV2windowpolicy stage now integrated into solepipelinewriter, compactprotectionrequired inclmissingarrayreject, raw/hard/local/compact/contextprotected. Parent masks/confirmedquarantine retained; acceptednewproposal action3/CR0/QCviewsNaN in active, auditunchanged. Combinedaction/resource/evidence overflow withdrawnewwholepass, no newACTIONBUDGETimplicitwithholding; native state1..5 explicit if JSONparentfull. Olddefaultcfgdigest exact. ActualstubRED11→GREEN20integration inclschema/trueparentrun; complete437XsuitePASS/oneexistingABIwarning; ownednewfilesRuff/diffPASS. Needcommitpush/minimal e6 overlay5Xfiles only, actualpairedpipeline before normalCASdeploy andmap. 105controllers1950275normal/2957154diag bothterminal, noactivecompute; MinIOavail501649088512bytes. Neverpersistsecrets. Hourlypaused/fullgoalACTIVE/otherSWebAPIdirtiespreserved.


### S V12 complete-window joint classification, 2026-10-03 (local research)
Default-off strict unified_joint_classifier_enabled + canonical native policy bit32 implemented: continuous/intermittent/mixed original-window routes, physical occupancy nomination before confirmation, actual rainy-side source contrast, complete opposing/missing/fork/curve/narrowing history and local weather/hard retention. Actual engine→validated Zarr positive and censored-acquisition-boundary abstention covered; fork restart regression fixed. Final frozen-current-working scoped776PASS/3existingwarnings, not owned-only release proof. Final paired8 audit-v3: +477 Z9591 10:18/+193 Z9598 08:42, other6 unchanged, withdrawals0; IMPORTANT added source-stage residual overlap0, so no demonstrated improvement on target remnants. Both actual paired plots viewed. All16 executed sourceSHA match test/currentbytes;8input/NPZ/PNG SHA verified. Private .build/s-joint-window-20261003/audit-v3-paired report0d152ac7... verification41e5ac02...; actions0/no productwrites/currentWebreceipts/weathertruth. No commit/push/105mutation/deploy. Next full-original-window stable path decomposition/competing weather models, frozen labelled development + untouched dates/sites + actual RADVOL/weatherloss/runtime, then near-site/isolated/realworker/Web acceptance. Do not loosen whole-parent fork veto or claim670newproposals solved visible targets. Full goal active/shared WIP preserved.

### S V13 original-window paths, 2026-10-03 (local research)

Default-off strict unified_object_paths_enabled requiring joint + canonical policy bit64 implemented. Frozen original endpoint-connected families/path templates preserve full original ranges/negative history; persistent paths through episodic joins, no recursive membership or cap increase. Actual late-curved-prefix false nomination reproduced RED then fixed by retaining full endpoint family; true forks/gaps/missing/opposing/narrowing counterexamples remain. Held-out physical20km range-corrected DBZH profile competes with constantDBZH alternative; morphology hypothesis, NOT measured power/IQ/SQI/weather truth. Only supported path route overrides localrho/SNR compatibility; externalweather/barriers retained. Actualengine→validatedZarr positive trunk/power and RAW/policy/forgery proof tested. Finalfrozen scoped803PASS/3existingwarnings incl sharedWIP, notowned-onlyreleaseproof. Final8 audit-v3-final all17 executedmoduleSHA matchtests/currentbytes,8input/NPZ/PNG verified, V12baseline exact: +61162 proposals/withdrawals0, BUT only33 newsource-stage residual overlaps (Z9591 1018=23/1042=3; Z9598 0836=5/0842=2), other4 unchanged. Source-stage mask NOTcontaminationtruth; mostadded alreadyoutside residualtarget. Actualfinal0836plot inspected remainslargelyunchanged; weatherloss/date-sitegeneralization unmeasured. Private reportc25f10cf... verificationbc372bde... retained script --paths --only unified_joint --only unified_paths --freeze-sources --plot. No actions/productwrites/105deploy/Webupdate/commit/push. docs/S_UNIFIED_OBJECTS_20261002.md records fullresults and next physicallynormalized narrow/fan/intermittent/short object routes, actual RADVOL comparator + labelled holdouts, separate near-site/isolated stages. FullgoalACTIVE/sharedWIP preserved.
X integration9eb0ff341192eaa3b87b4a3e622ef211d31879d8 committed/push/lsremoteexact. Minimalcandidate105 e64cb8fc... changes5Xfiles/794otherssame; runtimepipeline parent45680219 pluscommittedintegrationonly, excludeshistoricalmainradomephase block. Buildv1 preflightpipelinehash mismatch/no mutation;v2 bareDockerIDFROM registryresolutionfailed;v3 localSHAcheckedtag build/importPASS. Newrelease child d9381008... preparedwithcurrentcommittedbbf06f4 generator viaRAM only;4changes releaseID+3pilotflagtrue, no liveconfigselect. Installedgenerator older r1 addedimplicitdefaults, strictscopeguard correctly rejected; failedattemptfilesretained.
Fullpipelinefirstdriver1869227 STOPPED_ERROR beforecompute Path→sha bytes misuse; corrected1888610 twojobs18cutsPASS,505firstcut26contextdiagnosticdifferences. Pairednew-vs-disabledparent0differences provesharnessscope mismatch: loadedALLfields increases64MiBdonorbudget, pickedupper8 vsstored4. Actualselected_source_keys(x_qc_only=True) restores exactstored26point diagnostics; diagnosiscut0exit0. Preservev2failedstate/stderr/partials. Freshv4driverPID1978203 RUNNING3×4GiB/CPU1 on105, Dpolar-window-full-pipeline-v4, helperscef4aea3(driver)/d0a13132(probe), e64candidate, f3normal staysunchanged; readfreshstate/ps/Docker beforeanyrestart. Priority13+new702gainvolume precedesremaining204. Normalcapacity~501.58GB/~98.5Minodes. Needfullpipelineverifiedlocalhash/mask then metadata-warningnewimage proof/normalCASpublish/maps; no automatichourlyresume.
Ownadditionalstatusbugfixedlocal after2actualRED: incrementalabstention withparentEVALUATED couldhideXQCStatus UIwarning. attach marksDEGRADED_POLAR_WINDOW_* (orretainexistingparentfailure), preservesallparentarrays; JSONfullfallbacknativecode causespipeline warning.21integration/438fullXsuitePASS existingABIwarning, ownnewfilesRuff/diffPASS. Currente64backgroundproof uses9ebscientificstage beforestatusfix; do nottreat asnewwarningimage ornormalpublication. Needcommit/pushonlyowned5files/docs(notsharedMEMORY) afterHEADrecheck; thenminimalstatusoverlayimage+realcomparisons. GoalACTIVE/firstprioritynotcompleted/sharedSWebAPIdirtiesintact/nosecretspersisted.

X continuation 2026-10-03: f9dfb0a491c15fabd3ccc7fd6327c8c7f091075b committed/pushed exactlsremote; status-only minimalimage6ee18ea4... over e64 changes2files/797otherssame, committedpipelineguard only/runtime historicalradome exclusions retained. Fresh status real2volumes3cuts added/refused/unchanged allnumeric/mask/source/native SHA locallyverified under polar-window-status-pipeline-v2/verified-summary.json. Initialv1 andinstrumenteddiagnosis failed onlyruntime tuple() vsJSON[] at native_alternative_source/groups; fullofficialJSONcanonicalization fixedharness, no algorithm/arrayguardchange. Transportinitial100MiB guard rejected127MiBproof beforetransfer; inspected sizes thenbounded1GiB gzip transport, scientificcaps unchanged, originalfailures retained. Childrelease-v3 network7a952932... onlyreleaseID+3pilottrueflags; prepared, NOTselected. Normalstillf3/e6.
FullpipelineV4 PID1978203 live3×4GiB/1CPU; localSHAverified57/513 snapshot (as03:05Z), additional1014visible5/778visible15, no RAW/oldaction/protection delta. Remote48/204 earlier freshstate nowadvanceing; startupnotcompletion. IndependentreadonlyfinisherPID2219091 waitsactualterminal thenall204source/spec/input/output/native/mask SHA and1836 checks, Dpolar-window-full-pipeline-finish-v1; verifierSHA3613549189de5619523e5d8fcb807d40a8016731e979be0e83f3fb1edb63d23c. Noautopromotion/normal API recompute started; those andrealnewmaps remain pending. Verify freshPID/state first, do notblind restart.
Readonlybudgetdiagnosis-v2 actual50505cut5 RAW/source/nativebound: observed187220, fraction.65=>121693; parentexistingproposed|ACTION_BUDGET minusindependentnoise126541 alreadyexceeds, new1484total/149novel=>126690. Therefore potentialshadow149NOTactualcleaning, preservebudget/oldfailedreceipts. Initialprobe-v1 failed because exportdoesnotpersistOBSERVED_MASK; correctedreadoriginalGroupCuts actualselected_source_keys, nofinite-RAW/enumguess. Next audit largeparentnomination ownership/counterexamples; no capincrease/blanketdelete. Fresh realnormal702originalcut5 map output/playwright/zf702-original-cut5-normal-20261003.png viewed: majorRAWspokes/fanremoved, nearblue/sparsepointsretain andDEGRADED_MORPHOLOGY_ACTION_BUDGETvisible. F3currentresult47c53894.bd453fbf pinned, not6ee publication. SSH14173oldmastergone/browserERR_CONNECTION_REFUSED; serverlocalhost4173HTTP200, recreatedkey-onlysameControlPathforward afterno listener confirmed, noapprestart.
CI f9run37090735476 completedFAIL sameparent9ebrun37088130464:4899lint, oldAdminWorkspace path missing,10fusionquality_receipt metadata comparisons. NotcleanCI/readinessclaim; X438PASSexistingABIwarning. OtherS/fusion/Web/API dirty preserved; MEMORY unstaged. get_goal currentlyreportedPAUSED; latesthuman continues manual repair, noresume-status API used, hourlyautomation remainsPAUSED. Nevermarkgoalcomplete.


X continuation 2026-10-03 03:56Z: local full-pipeline snapshot independently SHA/mask/source/native/old-array verified149/1341, gains still1014visible5/778visible15, RAW/old disposition/protection preserved; remote fresh156/204, producer1978203 live and finisher2219091 waiting. Full proof/publication NOT complete. Real source-membership diagnosis-v4 505db1ac179 cut5: source123296 across84rays, fan123054 overlapping, allsourceQCvisible0/fourprotectionintersection0, validSNR<-50 count0. Baseline124092 vs121693cap; baseline/noise overlap0. Both duplicate-noise and invalid-low-SNR hypotheses disproven for target; no speculative core fix/cap increase.
One-shot normal advance v2 PID2562222 live WAITING_COMPLETE_PAIRED_VERIFICATION under polar-window-normal-advance-v2. It waits complete204/1836 independent finisher/actual producer exit, then guarded no-mutation preflight, CAS parentf3/rev26/networka9/4idlefreshworkers/disk120GiB100kinode -> candidate6ee/child7a95, normalAPI204 replay with immutable source ledger and exact old-v-new native/PNG audit, firstsuccess audit before further tasks, final latestcatalog check. No normal version switched yet, no hourly resume. Four helper SHA frozen at remote polar-window-normal-helper-sha.json; local product guard nonemptygood+12tamper controls PASS, preflight before proof complete actually refuses with no deployment. Initialadvancev1 PID2511327 retired ONLY while waiting (STOPPED_FOR_AUDIT_GUARD_REPAIR): counterexample maskvalue2 was accepted by boolean coercion, now rawuint8/shape/binary window+state checks fixed, SHA refrozen/v2 launched. Preserve all receipts; do not relaunch blindly. Final real maps/remnants/weathertruth still pending after normal publication. Own docs update only, shared MEMORY remains unstaged; hourly paused/goal tool PAUSED not markedcomplete.

X final controller correction 2026-10-03 04:00Z: actual204 frozen-row fixture reproduced inherited child manifest reuse_count13/new_task_count191 despite reuse=None for all. V3 changes only metadata counts0/204, source/spec/prior identities all exact; testPASS. Waitingadvancev2 PID2562222 safely retired before any promotion, original receipts retained STOPPED_FOR_MANIFEST_COUNT_REPAIR. Authoritative advance now polar-window-normal-advance-v3 PID2607084 actualWAITING_COMPLETE_PAIRED_VERIFICATION166/204, freshlaunch/helperSHA verified; V1/V2 retired, NEVERrestart them. Scientificcontroller1978203/finisher2219091 continue, normalf3/e6 stillunchanged. Own doccommit6a0f44f585ffdca94af7b3b392405d15ac20a0a8 push87248exit0; verifyorigin/CI pending66754.


### S unified V14 complete azimuth-profile diagnostics, 2026-10-03
Default-off strict profiles flag/joint dependency/policy128 implemented with full native RAW20/60km occupancy nominations, exact PROFILE/local proxy-override masks and native engine/validatedZarr proof. Known quiet supports availability only, no boundary votes; unknown late native extent stays denominator. Censoredprefix432falseproposals RED->0GREEN; wide40deg fan1/.5deg RED->GREEN via separatefan geometry; continuous/missing/narrowingwide controls retained. Final frozen shared-working scoped829PASS/3existingwarnings,18executedreplaySHA match test/current;8input/NPZ/PNG SHAverified andV13baselinebitwiseexact. Final audit-v3-final report eea4b9a64dab8355481f3f1a8653d89149f948c033adef612688aa96c45d5946 / verification d8bd4c55c871e33d2fc2a9488efc2510842018a8065a2b6b01f49a45849befb7 under .build/s-profile-morphology-20261003:125profile nominations/1391overlappingwindows,0confirmed/0newproposals/0residualgain/0withdrawals. Source-stage0836pairedplot inspected: mostlyblue remains, notnewWeb. Reusable summarize_s_profile_morphology.py verifiedreport diagnosticJSON/PNG retained. No capincrease/commit/push/105mutation/deploy/actions/weathertruth. docs/S_UNIFIED_OBJECTS_20261002.md records morphology direction: bounded multi-scale angularridge/pairededge separation within FULL originalfamily, global persistentboundary paths including adverse/missinghistory, localdisposition/externalweather, actualRADVOL/labelleddevelopment/untoucheddate-site validation. Wholeangularrun pooling merges parallelstrips/fan/weather; merelymorecounts orlowerthresholds doesnotresolve. GoalACTIVE/fullscopeunfinished; preserveallsharedWIP/concurrentHEAD.

X 6a0f44f push exactorigin SHA confirmed; CI37094935330 completedFAIL (whole-project inherited failures still notgreen). Private productguard and manifestcount controls PASS; shared gitstatus preserved (ownpublicdoc clean, MEMORYunstaged). Fresh04:10Z full198/204; finisher/advanceV3 livewaiting, normalnotchanged.

X 04:17Z important milestone: server full204/1836 terminalFULL_PIPELINE_VERIFIED, independentfinisher terminalFULL_PIPELINE_SHA_VERIFIED after actualallSHA/raw/native/protection checks; fullmask47492/additional1014visible5/778visible15, states1821evaluated/15budgetrefused, parentstatuses1799evaluated/23morphbudget/14overallbudget. Local149complete plus remoteallverifiedsummary captured complete-pipeline-and-deployment-observation.txt; not allfull receipts mirroredlocal yet.
Firstdeploy failed ownhelper variablecollision fresh(list) shadowedfresh(function); rollback restored f3/e6 and original network/channel, submitted0. ActualRED reproduced, renamedis_fresh; stale/future/unready controlsPASS, preflight reverified. Failurelog preserved advance-v3/deployment.log, no scientific replay restart. Authoritative successful advance V4PID2711119 and normalrunner2715788 nowlive. 105 NEW normal candidate installed6ee18ea4.../fingerprintfd5db0c4194a77f7237da2fca057a912986a442ea77d032f5bfc164559e41185/network7a952932...;4READY/1busy, MinIO499414556672B/98530199inode, memavail32807981056, no other service reset. Promotion polar-window-release-v3/promotion.json; originalfailedreceipts retained. Newfrozenmanifest713bde271bf350f94aff819f481d5245dadfdb9d8231a459bd64fac40a6af556 all204no reuse, sourceidentities unchanged. Normalreplay first3868submitted1/audited0 WAITING_NORMAL_TASKS; must verifyactualproductaudit success before claims. Originalhistoricalb2fc URL realUIshows sx-quality-v2-z10-20260930 history and Viewlatestbutton; actualclickchangedresult47c53894.bd453fbf, oldf3majorartifacts cleared butbudgetwarningremains. Browser now original702oldf3latest, notnewfd5map.


### S V15 partial replay and generic morphology reassessment, 2026-10-03
Default-off V15 retains all original intensity nomination levels, independently selected disposition levels, shared RAW histogram/quantile measurement and immutable native membership views; no cap increase. Frozen shared-working suite838PASS/3existingwarnings/test-auditor drift0. Final paired-v7 session90737 terminalFAIL on eighth Z9598 0842 jointRAW50M cap; seven artifact diagnostics only, never completed eight-input acceptance. All8input and7NPZ/PNG SHA checked,46frozen radialmodule bytes match test/current; baselineV14bitwiseexact/actions0. Seven add8705candidate gates but0new frozen source-stage residual overlaps/0withdrawals, not truth or Web gain. Failureproducer/log preserved; private audit-v7-final/partial-diagnostic-verification.json. No new code edits/deploy/product writes this resumed research turn; code WIP from previous continuation retained. Research docs/S_UNIFIED_OBJECTS_20261002.md updated: bounded multiscale paired-edge/ridge graph over complete original objects, physical geometric scales, separate line/fan/parallel/short conditions, source-confirmation then bounded target disposition, no same-bearing blanketinheritance, sharedfeatures/performance, labelled development plus untoucheddate/site/weather holdout. Full variable-boundary decomposition/actualRADVOL execution/near-clutter+isolated/fullworker105acceptance stillpending. GoalACTIVE/fullscopeunfinished; allsharedWIP preserved.


### S V16 paired original boundaries, 2026-10-03
Default-off ProfileBoundaryPaths added/shared immutable census, full-original-family paired-edge graph/two-best complete DP paths, native gaps/no recursive ownership, actual boundary states and independent per-level disposition. Coverage-before-cost fixes skipped-width-history RED; distance occupancy fixes continuous-weather RED; touching fork/saturated-width narrowing histories preserved. Native rotation/resolution/weather/missing/external/resource controls PASS; explicit synthetic path ablation984vs336/1728vs600 only, not real acceptance. Joint unconditional originalgeometry rejection skips repeatedRAW measurement with explicit reasons/reference entries; cap unchanged. Final frozen working-scoped848PASS/3existingwarnings/test-auditor drift0; actual8/8 paired-v8 COMPLETE,19executedmodule SHA=test/current, allinput/NPZ/PNG SHAchecked,V14pathsbitwiseexact/action0. Maxjointwork48702117, per-candidate4–18.5s beforevalidate/report/plots.309pathnominations/15confirmed (overlap), +6216proposals/0withdrawal vsV14 but0newsource-stage residual gain. Actual0836pairedPNG inspected: blue remains, notnewWeb. Private audit-v8-final report d6028dc744fa33238c1170684183a9b39bef2cdbff4534b4938ae85e664f556a / verification14e4e0f649d3ba1649191d6308f4b6d2fda03bb799312fd75c54ca38bd87ede3. Reusedofficialaudit/diagnosticplots; privateboundedweakcounterfactual exactsourcelevelreferences+interior range gives95980818/0836/0842 only4/3/2residual,0actualbilateral newcandidates; notcompleteattribution or truth. Next completecross-intensity sourceidentity/range/availableboundaryproof and strictunanchoredfragments, notblanketextend samebearing; jointmixedmultiple-boundary paths stillpending. PinnedRADVOLheadersdownloaded reference-only, coreNOTexecuted. No commit/push/105/deployment/productionflag/product changes. GoalACTIVE(fullscope:weatherholdout/RADVOL/nearclutter+isolated/worker+Web pending). Shared WIP preserved.

### S V17 parallel original boundary decomposition, 2026-10-03
Default-off ordered multi-path assignment added inside complete RAW family: separated first/last identities, complete node ownership, unresolved merge context only/no votes or actions, full family inventory retained per child. Interior displaced-node RED fixed by original competing context within endpoint envelope+2 physical beams; no restart/recursive expansion/cap increase. Final frozen working-scoped859PASS/3existingwarnings incl actual shuffled native runner→validatedZarr RAW/coords/profileproof; native writer test's first optional-field assertion was incorrect, repaired to actual sweep.dbzh_raw and canonical Zarr DBZH_RAW; failure log retained. Synthetic provider ablation0→792 proposals/background0/merge0/RAWsame, not real gain. Plot renderer retained under private .build; initial wrapped-angle plotting bug preserved as v1, corrected final inspected. Actual8/8 paired-v9 COMPLETE,19executedmodule SHA=test/current, all8input/NPZ/PNG SHAverified; V14paths/V16allcandidate masks bitwise same, added0/withdrawn0/newresidual0/actions0. Parallel real nominations0. Full309 overlapping family diagnosis:223singlefirst/81terminalcount mismatch/4endpoint nonoverlap/1intermediate/context, not truth/object recall. Limitation identified: strict complete-family terminals cannot resolve sources issuing from near-field mixed context; next cross-intensity RAW sourceidentity/finite source onset with original ambiguity preserved, not dropping endpoint/fork protections. Maxjointwork48702179. Private audit-v9-final report be9d4ccac41b3fc88e97fa7f7b3772599277ab437e8dbf9c3eee6571884e5d94 / verification bb6b4ec73c06e6eb7ce257021a818f0f305e0b35c5a5195562e90b0297885e7c. All owned tests/replay/verifier processes terminal; no commit/push/deploy/105/product/productionflag changes. GoalACTIVE/fullscopeunfinished incl real gain/weatherholdout/RADVOL/nearclutter+isolated/workerWeb; all shared WIP preserved.

S research follow-up 2026-10-03: user asks generic radial/fan extension rather than screenshot patches. docs/S_UNIFIED_OBJECTS_20261002.md now specifies finite source onset/end inside complete original family, source/background/mixed/unknown assignment, shared geometry features and strict held-out target disposition; design NOTimplemented/promoted. Public RADVOL2022/NOAA dpQC/wradlib sources fetched locally and SHA indexed under private .build/s-generic-morphology-research-20261003. Checked existing response-v2 driver/report/eight input identities: distance-response counterfactual covers28 frozen residual gates only95911018, other7zero; NOTall8zero, NOTpollutiontruth/actualQCgain, lackslegacyancestry. Range-normalized census parallel nominations8zero. No business edits/replay/recompute/deploy this turn; original tests/candidate state remain asV17. Next externalbaseline + finite-onset decomposition/weatherholdout; preserve unknown and frozen source extent. SharedWIP unchanged except owned research docs/memory.


### X complete receiver-family root investigation, 2026-10-03
Latest user continues generic X radial/fan repair; hourly automation remainsPAUSED, no new checkout or sourcecopy. Main/origin6a0f44f unchanged; all S/Web/Go WIP retained. New normal702scan3868baa7 cuts7/8 actualmaps FAIL (19348/7290 visible35 beyond10km). Submissioncontroller2715788 verified exact then SIGSTOP20submitted/18audited, visual-failure-hold.json authority; scientificjobs notstopped. Do notresume oldbatch.
Proved fixed45deg boundary truncation, weather-stencil80dBZ exclusion vs baselinelegal100, and missing SNR shoulders counted as failed reference continuity. Own complete_source_families_enabled strictdefaultoff+separateabsolute/relative mode; originalcompletegeometry/normal solewriter integrated candidates only, parentconfirmed/source/quarantine retained. Added private-source-view legal[-50,100] support and strict >=3 remote same-shoulder reference prediction for one missing targetboundary; unknown neverquiet/RAW unchanged, targetguards/span20km/ratio1.75/3dB/caps unchanged. Wholebudgetexcess leavesparentmasks and explicit candidate ACTION_BUDGET withholding (different from priorwindow whole-refusal); hard/context protected, local/compact proxies recorded. State1..5 distinguishesunavailable/resource/budget/evidence. All changes UNCOMMITTED/defaultoff/NOTDEPLOYED as this entry; own3tests +2helpers +existing7Xfiles/schema/doc, no unrelatedstage. Actual RED3wide +RED3missing(emptyfixture red-v1 invalid; correctednonempty red-v2 actual0) thenGREEN; latest472X-v2 PASS4existingwarnings, scoped20PASS, ownRuffselect/diffPASS.
R/O complete-pipeline-real-v1 PID3373996 COMPLETE2/2 on105 installed6ee parent17a42d9697... plus ownhunks/moduleRAM only. Locallyallinput/output/mask SHAverified; parentnormalallnativeexact/RAWsame/action3/CR0/parent dispositions intact/combinedrecordbounded. Cut7 added50255mask/19331strong,17strongremain; cut8 added28243mask/5892strong,1398remain; bothstate4budgetreview. Actualread-only plotcut8 inspected: thin SW/far southern remnants remain, NOTaccepted. Subsequentprovenance-key rename/protection-unavailable state2 patch needs finalfrozenruntimeproof; no numericchange claimsolelytesting. Private .build/xqc-polar-morphology/complete-pipeline-real-v1.
R/O relative-family-real-v1 andshared-range-family-real-v1 COMPLETE2each locallySHAverified. Absolutefamilycut7+18709strong,relative+19331; cut8relative+2811strong. Sharedrange-normalizationalone+2805 cut8 DIDNOTIMPROVE, notimplemented. family-failure-real-v1 SHAverified4479remaining attributed2806geometry/1673fit; family-boundary-real-v1 shows repeated row185 receivercoverage<.5 vs otherrealrangeblocks measuredsamequiet shoulder163/185. family-heldout-real-v1 exactpairedgeometryprototype5728strong, notnormal. New family-residual-real-v2 PID3428937 COMPLETE1398 targetremaining, 861geometry/537fit-bounds; remoteinput/outputrefs6ace11d... (localmirrorstillpendingverification atentry). family-ablation-real-v1 PID3480351 RUNNING one3GiBCPU1 sequentialfactorial complete held-out templates andphysical-range affine response; currentconfig thresholds unchanged, read-only/notproduction, inspectfreshstate/stderr before anyrestart. Diagnosticreservation native-floor-adjacent-contours-v1/reservation.json scoped1, oldnormalheld. Need inspectablation evidence, generic weather/counterexamples before any production implementation, then finalfrozen tests/sourceproof, localmainpush before minimal105overlay, actualnormalproducts/maps andcrosssite/timeallangles. No guessed Doppler/waveform8/phase0, no trustedfusion/QPE/forecast/credentials/unrelatedcleanup.


### S V18 finite identities and generic morphology research, 2026-10-03
Default-off finite original identities implemented after contract/RED tests: source start/end, mixed endpoints context-only, full original family retained, native ambiguity/fork/displacement/resource guards; continuous source cannot be disguised as intermittent by a mixed prefix. Frozen final875PASS/3existingwarnings incl actual native runner→validatedZarr, ownedRuff/diffPASS. Actual8/8 audit-v10-final complete; independent verifier rerun terminalPASS,19executedmodule SHA=test/current plus all8input/NPZ/PNG; V14baseline and V17candidate masks bitwise identical, added0/withdrawn0/newresidual0/actions0. One realfinite nomination95911042,confirmed0/proposal0; continuous-route refusal is not weathertruth. Maxjointwork48717576/cap50Munchanged. Synthetic ablation864new/background0/mixed0/RAWsame only, no actualgain/Web claim. Reportda9db39b9ab1cacb6012ee282a21d5945b5b99cdb2b08d3b236aea1f5d1d687e; finaltest1a857a688993834d1138864d9a5d6938c6dfe0fde6d8037e7d76f12862f91d6b.
Latestresearch saved-native early-gate reconstruction309overlapping families:259insufficient repeatedexactboundaries/43originalnodewithoutsourceowner/6single-sourceedgechange/1delegatedparallel. Notfullruntime rejection/truth/recall. Private diagnose-finite-family-v10.py+SHA-boundJSON retained. docs/S_UNIFIED_OBJECTS_20261002.md now differentiatesimplementedV18fromproposed physical paired-edge/ridge tracking + source/background/mixed/unknown competing explanations. Stopincrementalexact-template accumulation withoutactualgain; next broadcategory development/untouchedsite-date-angle weatheracceptance, actualRADVOLreference execution pending. Primary RADVOL2022/NOAAdpQC/PyARTdespeckle docs checked; borrowedmethods notvalidatedforourSthresholds. No newcommit/push/deploy/105/product/business edits inlatestresearch; earlierV18WIP retained. GoalACTIVE/fullscopeunfinished: realgain/weatherholdout/RADVOL/nearclutter+isolated/normalworkerWeb. ConcurrentX/Web/GoWIP unchanged; allownedprocessesterminal.


### S V19 original source/background allocation, 2026-10-03
Owned default-off background_paths uses measured physical centre/paired-edge variation in FULL original family; all source/mixed/background assignments retained, nearby/disconnected competing boundaries refuse identities, source range/native denominator frozen. Background is relative membership, NOTweathertruth. Contract/RED first:3fail5pass. Original continuous/weather/fork/displaced/missing/barrier/caps preserved; actual shuffledrunner→validatedZarr newfixture passed. Fullregression RED caught same-centre nestedwidth hypotheses mislabelledmixed and bypassing narrowing: fixed mixed identity to physicallydisjoint corridors, preserves originalnegative. Firstactual8 attemptaudit-v11 terminalFAIL eighth50M;7artifacts retained, nofullacceptance. Sharedseed/node-mix work removes repeatednomination computation; unchanged thresholds/caps. Finalfrozen884PASS/3existingwarnings/Ruff/scopeddiffPASS. Actual8/8audit-v12-final COMPLETE; verifierterminalPASS19executedmodule SHA=test/current+8input/NPZ/PNG; V14baseline/V18candidate bitwise same, added0/withdrawn0/newresidual0/actions0. Maxjoint49165499<50M. Real93overlapping backgroundnominations/0confirmed, nonexclusiveholds curved37/narrowing9/continuous-source37/insufficient-profile17; some0readywindows with missing/bilateral limits. PrivateSHA-bound rejectiondiagnosis retained, notcontaminationtruth. Syntheticablation0→1416proposal/background0/mixed0/RAWsame, inspectedexplicitlysyntheticPNG; reusableplot/verifier/diag retained. Report4543b5c55539f798ae50fb285ce359a3b39f379b666383e525f458dee2b937d5 /verification2c3d15782a9b3a18556ffa315829947a6d310a008b68983c5416f7d4c6e9ea49 /test8e95baea8fdbc5c1a9a28a1103673018c6ebef3b01844eed47e1ff9c7c317483. No commit/push/deploy/105/productchanges, allownedprocessesterminal, concurrentWIPretained. docs/S_UNIFIED_OBJECTS_20261002.md states next own-vs-background curvature provenance, RAW angularridge/paired-edge nomination independentofabsoluteDBZH, and continuous-source/weather competingexplanations withrealcounterexamples. V19 is allocationonly, notfullgenericclassifier/realgain. GoalACTIVE/fullscopeinclweather/date-site-angleholdouts/actualRADVOL/nearclutterisolated/normalworkerWeb unfinished.


### X complete-family sparse support continuation, 2026-10-03
Main/origin exact4e46f3f20e4ce2d6247084272e9af7c81314e356; c7202c8 complete RAW receiver-family candidate fix and7c2b style were pushed earlier. New4e46 fixes sparse target-ray population veto ONLY explicit heldout complete-family additional candidates; legacy confirmed source/RAW/hard-context masks and caps unchanged. Corrected nonempty RED3positives/3controls -> GREEN6, full492PASS7existingwarnings reused/currentSHAchecked; own Ruff PASS, pipeline14lint diagnostics exactly unchanged from7c2b. CI37107611567 completedFAIL (old performance metadata10, wholelint4898, frontend baseline tests); own newtest/source notfound failures. Do notclaimwholeCIgreen. All S/Web/Go WIP preserved; only4own files committed, sharedMEMORY notstaged.
Final exact installed-parent plus committed ownhunks sparse read-only replay two real7023868cuts7/8: parentallnativeexact, RAWsame, action3/NaN/CR0, selected50394/42886, strong19348/7290 ->17/37, weak647/3688 ->611/3330; plots inspected weak remnants remain, NOTvisualacceptance/normalpublication. Minimal image574997be2ae000d9317712b90bb0d58cdfe88531f1f90e7c0e33e5ff641230a9 (tagxqc-complete-family-4e46f3f-candidate-mb),8Xfiles changed793othersunchanged/defaultoffimportsPASS, pipeline2d0d45c... exact frozenproof; no historicalmainradome/SWIP. Prepared sparse release child7b2e1a7ca9427af3bc134bb6334f7ff8db5a44500797ca3a034c9d179b6077ba ONLYreleaseid+2flags3pilots; parent7a95/fpfd5/channelrev27 remainlive; no switchyet.
Old c720cross18/162 now FULL_PIPELINE_CROSS_VERIFIED, independentfinisher3961292 terminalHASH_AND_NATIVE_MASK_RECEIPTS_VERIFIED; originalinput/output/native/proofSHA verifiedserver, notallfullreceiptsmirroredlocal. New sparsecross producer4167778, chain4134926 RUNNING3CPU4GiBslots, latest86/162 at08:19Z; normalparentreplay reusedfromoldSHAverified v1, originalnormalasset/raw re-read, newfullnormalpipeline executed and unchangedparentdispositions checked. ProbeSHAcb4efd43..., manifest99ba1032694fe1b807cac6bd88bdf847444cf7703a9203d0c78d1f483598edb6. Nativechecker positiveactualnonempty50394 +11tampercontrolsPASS; source/byte proof stillfullpending, don'trestart. 105 B=/home/yons/hwapp/dis/rainpulse-xqc-evidence-20261002. R reservation own3slots/memoryprotect8GiB. ActualMinIO497820712960B/98472564inode at08:14,MemAvailable~19.3GiB while3probes;120GiB/100kinode guard maintained,noextra cleanup/restarts.
Guarded delivery56343 RUNNING WAITING_COMPLETE_SPARSE_CROSS, helperSHA4a29574b4fbcb134dd4c0b2b78c1c3682f03fb8c34dffa43e70659635c070016; state complete-family-sparse-delivery-v1/state.json. It waitsfull162+independentcheck, separatelycomparesold/newnativecandidates and stopsifwithdrawn, thenCASidle4worker/pool/channelrev27+rollbackdeploy574/child7b2; freezes18sameinputs and normal API xqc tasks+raw/time/allparentarrays/PNGbyteexact auditor+latestcatalog18. Normalrunner/sourceauditor helpers SHA in localcomplete-family-sparse-delivery-helper-sha.json. No SQLfake/RAWoverwrite/weathertruth/UIacceptance claim; controllerterminal mustbe NORMAL_PUBLICATION_AUDITED_PENDING_UI_AND_RESIDUAL_ACCEPTANCE, nevercompletegoal. Firstnormalsort3868 thenuser3b3bf. Helperscompiled/uploadedSHAchecked, actualjobsnotyetstarted. Originalbadnormalrunner2715788 remainsT with20submitted/18audited andvisual-failure-hold.json; controller2711119 stillwaiting it. Do NOTresumeoldrunner/hourlyautomation; hourlyPAUSED, candidateonly3pilots/no trustedfusionQPEforecast. Needfreshallstates beforeanyrestart, inspectpartialcomparison iffail. Continue remaininggenericweakshape/reference/fit attribution and actualnewmaps afternormalpublication; no blindthreshold/SNRfloor cuts.

### S V20 native source geometry provenance, 2026-10-03
Owned default-off background path corrects row-only parent veto with exact native row/range source overlap; mixed/interpolated/unrelated-distance context retains IDs/history but no source vote. One actual overlapping source gate still retains original parent veto, own curve/narrowing/fork/weather protections unchanged. SHA-bound V19 attribution:93overlapping background nominations,35inherited-only,25with no parent/source native overlap. New contract/RED3->GREEN, exact20/60km native-intersection controls; finalfrozen889PASS/3existingwarnings/Ruff/scopeddiffPASS. Firstreal audit-v13 terminalFAIL old50M due repeatedparent grouping; failureproducer/log preserved. Onepass shared20/60km grouping fixes computation, no caps/thresholdchange. Finalactual8/8audit-v14 COMPLETE; independentverifierPASS19executedmodules=test/current+all8input/NPZ/PNG; V14baseline/V19candidate bitwise exact, added0/withdrawn0/newresidual0/action0. Maxjoint49860730. Report52d177ccb7936bdd177bc6d976f533ba41ec85ca4a46a73230ae487b0d2271fd /verificationd6dfa82e7f20264e7a560e157b6494a2abf6381f684e4a64793d2a1fc5c0a43e. 25wronggeometryholds removed;24thencontinuousfull/sourceholds,1insufficient;21haveactualreadywindows. Private reusable provenance/response diagnostics retained, original numpyJSONfailurelog too. SomeactualconstantSNR/range-growingDBZH profiles suggest nextreceiver-response alternative, NOTpower/pollutiontruth:1018id503SNR54 over110–450km/DBZH52.5→68.5;0836id1255SNR8.5–9 over91–441km/DBZH6→23.5. Observed-window heldout/local/interior counterfactual-v2 max10remaining1018,other7zero; NOTimplementedQC or acceptedweathergain. Need original angularedge nomination + continuous-source/weather competing explanations, no blanketcontinuousveto removal or moretemplateaccumulation. No commit/push/105/productchanges, allownedprocessesterminal (33370JSONfail;15684diagPASS;17121/17371focusedPASS;48158test887PASS;19464realcapFAIL;80476actualPASS;93174test889PASS;98576verifierPASS;41794responsePASS;76193prototypePASS;45063counterfactual localnameFAIL;20271fixedcounterfactualPASS), sharedX/Web/GoWIPpreserved. GoalACTIVE/fullscope includes realgain/untouchedweatherdate-site-angle/RADVOL/nearclutterisolated/normalworkerWeb acceptance.

### S V21 pending and generic research update, 2026-10-03
Working default-off V21 relative-native paired excursions, basis provenance, shared20/60km counts/window inventory and controls are UNCOMMITTED/NOTDEPLOYED. Latest monotonic native stack focused63PASS/3existingwarnings; earlier frozen905PASS was BEFORE final stack edits, not final current-byte proof. Real audit-v15/v16 failed budget; latest instrumented trace-relative-cost v3 reduced relative nomination to4,984,034+3,206 work but still hits unchanged50M during classification. No final8-case replay/gain/production result; all own jobs terminal. Next investigate shared measurement cost while preserving whole original histories/caps, then complete-source vs weather classification rather than another template. User explicitly asks generic solution/research: docs/S_UNIFIED_OBJECTS_20261002.md research supplement records object-level shape route, continuous hold replacement (not blanket removal), bounded RAW residual ownership, existing AFL reuse and independent site/date/sweep validation. Read primary Wen2020/RADVOL2022/SouthChina2025; source text+SHA under private .build/s-generic-morphology-research-20261003/object-decision-v2. Existing afl.py already reproduces paper features with explicit approximations; do not present it as new or count related DBZH methods as independent evidence. Latest turn edits research docs/memory only; no new business edits/105 changes. Shared S/X/Web/Go WIP retained; goalACTIVE/fullscope unfinished.

### S V21 final offline replay, 2026-10-03
Supersedes pending above: shared immutable parent row inventory/window cache, exact pooled row+column census, unchanged geometry preflight now fit original50M caps. Frozen final912PASS/3existingwarnings, owned5filesRuff/scopedtracked diffPASS. Actual8/8 audit-v19-geometry-preflight COMPLETE; independent verifierPASS19executedmodule SHA=test/current +8input/NPZ/PNG, RAWsame/protection0/action0. Originalpaths bitwiseexact, vsV20 added0/withdrawn0/newfrozenresidual0; maxjoint44413293, localmethod3.69–15.30s not105latency. Relative119nominated/0confirmed/0proposal; overlapping holds fork/merge115,curve97,envelope59,narrow5,insufficient2,continuous1; NOTtruth/recall. Failure-v17/v18 preserved, oldtracepathsFalse notfullproof. Docs latest section records source-vs-background/mixed/unknown competing model and labelledweather/site/date/angle +actualRADVOL stillpending; stoptemplategrowth withoutgain. Allownedjobs terminal(60695actualPASS,42863testsPASS,63368verifierPASS). Defaultoff/uncommitted/no105/product/deploy/push, sharedS/X/Web/GoWIP retained, fullgoalACTIVE.

### S V22 continuous-profile alternative, 2026-10-03
Owned default-off profile route now compares heldout normalized DBZH vs constantDBZH, actualpositive complete range>=.9, physicalrefs>=160km, actualgate prediction+bilateral/native barriers. Onlycontinuityholds replaced, fullgeometry/history retained. RED found old displacedprefix/thresholdcrossing calledintermittent; allboundarymodels now use actualbilateral source occupancy at0 independentofintensitycut/background. Contract first; focused65PASS/nativepositive+weather writer covered; frozenfinal921PASS/3existingwarnings, ownedRuffPASS. Actual8/8audit-v22-response COMPLETE,19executedmodule SHA=test/current +8input/NPZ/PNG/gatecaps verified; originalpaths bitwiseexact/RAWsame/protection0/actions0. Independentnative verifier reconstructs all35responseobjects/282target fits and nativebounds, allnewgates attributed, PASS. vsV21 +77484proposals/-845, BUT realfrozenresidual new30 only(95911018=29/1042=1;other6zero), withdrawalsremaining0, notcontamination/weathertruth. Maxjoint46420083/cap50M. 1018PNG viewed65/7395 supported, majorremnantsremain. Private finaltests/results/verifiers+plots retained; allown jobs terminal13937tests,82046actual,19033general,4489native. No105/product/Web/commit/push/deploy, sharedWIPpreserved/fullgoalACTIVE. Next preserve angularresponse distribution forwidefans/parallelrays in full RAW source-vs-background/mixed/unknown model, actualgain plus independentweather/site/date/angles/RADVOL/nearclutterisolated and normalworkerWeb stillrequired; no perstationthreshold patching or wholemedian candidatecount claims.


### X complete-family normal publication and expanded replay, 2026-10-03 10:48Z

Supersedes the X sparse-delivery WAITING entry. Main/origin 5f2101234ff1e9652cd16d9812389d4b9088be95; candidate image fc7e5d8d7f83a4ba11f6570f4900848057f2de0c0e28709dd26cbb8092cc3efb, network2247267d.../fp8e7a327a..., channel29/pool40. Only complete_family_integration + pipeline revision differ from parent574;799 other Python files unchanged. Real accepted-increment provenance bug promoted1847 inherited ACTION_BUDGET review-only gates into PROPOSED; RED regression then scoped493PASS, paired real cuts6(state1)/7(state4):1847 proposal corrections/all other arrays exact. Same pattern in polar-window writer already safe. Old failed c230 product/receipt retained. WholeCI37112079632 remains inherited failure (parent also4898lint/10performance_cd/frontend failures); scoped tests/Ruff/diff passed.

New normal batch complete-family-provenance-normal-v1 terminal NORMAL_PUBLICATION_AUDITED:18/18 newSUCCEEDED,162/162 cuts,648PNG checks,162 budget-mask tamper controls,18 latestcatalog bindings; coverageZF50510/ZF7013/ZF7025,27 quality warning cuts. Controller696037 exited. First audit-v1 omitted the already-specified state4 BUDGET_WITHHELD delta; exact-delta/reason parity + per-cut tamper controls added in audit-v2, original failure kept, SAME successful task re-audited, no SQL/state forgery. Local final independent receipt verification SHA16ab6e2d...; B=/home/yons/hwapp/dis/rainpulse-xqc-evidence-20261002, local .build/xqc-polar-morphology. Normal 162 detector proof reused explicitly because writer-only repair; all162 new normal products nevertheless regenerated/audited.

Real published maps viewed:ZF70107bdd490 cuts0/3;ZF702 user3b3bf9da cuts0/2;ZF7023868baa7 cuts7/8;ZF5057c07459c cut6/d41c8ce4 cut5. Major long radials/large fans greatly removed; weak scattered and compact/broad patches remain. Old result-pinned URLs still show immutable old products; new task IDs in private X_QC_DELIVERY_20261003.md/UI URL receipts. Native longest-ray length is not contamination truth: ZF50524km samples occur within broad patches; ZF702 remaining259>=35dBZ gates on cut2 largely compact proxy, not independent weather truth. Five SHA-bound published residual probes v2 explain all remaining gates by model qualification/bounds or default ZF505 local-policy protect; v1 omitted the default protect condition, preserved and superseded (v2SHA8e57fa9f...). No unexplained already-fitting-but-unselected gate after policy accounted. Three prior source-bound model traces likewise explained; detrended-response prototype discarded:0strong gain/57withdrawals in usercase. No threshold/shape-region cleanup adopted.

Expanded read-only paired installed pipelines now RUNNING:PID1058265, driver run-complete-family-expanded-v2.py, state B/complete-family-expanded-v1/state.json. Frozen204 volumes (ZF50566/ZF70170/ZF70268) existingUTC00:00–~07:01 sources, NOT full UTC day. ProbeSHAed7ea163..., manifestSHA101d39ad..., samefc7/net224 release.3 containers xqc-expanded-*,2CPU/4GiB each, reservation native-floor-adjacent-contours-v1/reservation.json owns3slots/12GiB; keeps8GiB headroom/120GiB actualMinIO/100kinode. At10:47Z first3 volumes active,0complete; startup is not acceptance. First preflight-v1 correctly failed invalid disabled-relative config; v2 restores absolute_noise mode for disabled baseline, unchanged enabled child. Actual installed parent/child paired cuts6/7 BOTH matched every current normal native array, status PAIRED_PUBLISHED_PREFLIGHT_VERIFIED, outputSHAc7115fc9...; failures/logs retained. Extended runner freezes all204 source identities from original manifest, no production writes/API submissions, stops on unexpected RAW/parent dispositions/proposals/budget/numeric delta/resource/source drift; terminal must remain PENDING_INDEPENDENT_AUDIT, never automatically publishes/claims QC completion. Inspect original failed outputs before retry; do not overwrite.

Fresh at10:47Z actualMinIO496495026176B/98418246inode,MemAvailable~24.1GiB, four normalcandidate workersREADY/idle6GiB/2CPU each. Pool11historicalstalled,queued0; new18 tasks allcompleted. Old visual-failed204 runner2715788 remainsT; do NOTresume it. Hourlyautomation remainsPAUSED; goal statuspaused but currentmanual continuation authorized. No trustedfusion/QPE/forecast, no S/control/Web restart or unrelated dirty/untracked edits, no credentials saved/deletions. ZF703/ZF801 formatunverified excluded;ZF401 invertedfile retainedfailed. Next inspect newexpanded state/source-native receipts, independently verify terminal hashes/cuts then target failing or still-obvious shapes across remainingtimes/elevations; preserve compact/weather-loss and full-day/all-site limits. Private report .build/xqc-polar-morphology/X_QC_DELIVERY_20261003.md. Whole goal NOTcomplete.

Expanded replay followup 10:51Z:3/204 volumes and27 cuts PAIRED_VOLUME_VERIFIED; next3 volumes active,3 workers,controller1058265 stillRUNNING. No full-batch/independent/weather/UI acceptance yet. Scoped pytest493 count independently bound to actual progress-log SHA in parent-budget-proposal-scoped-test-receipt.json.

### S V23 native angular response, 2026-10-03
Owned default-off per-original-ray heldout DBZH response; >=80% original rows, full-positive >=.9, physical refs/bilateral/gate bounds. Exact positive parent native-overlap may reinterpret inherited absolute-contour curve ONLY with stable full relative boundaries/no own adverse holds. RED disconnected short shifted tail hidden by60km pooling; original20km RAW census/nearby/unbounded context now binding. Firstaudit-v23-angular thirdcase50M FAIL retained. Shared eligible RAW capture avoids duplicate dense scan; stats stillcharged. Frozenfinal940PASS/3existingwarnings/Ruff/diffPASS. Actual8/8audit-v23-angular-shared COMPLETE; independentgeneral PASS20modules=test/current+8input/NPZ/PNG; pathsbitwiseexact/RAWsame/protection0/actions0. Nativev4 independently reconstructs157responseobjects/1201targets/perray/rawcontext/provenance/allnewgates PASS; nativev1/v2 window-range definition mismatches andv3Nonebeam harnessfailures retained, algorithms unchanged. vsV22 +119127proposals/withdrawn0, but only823newfrozen-residual proxy overlaps (0842=77/1018=593/1042=141/0818=2/0836=10;other3zero); not contamination/weathertruth. Maxjoint49861937<50M, local3.57-16.52s not105latency. Actual1018 comparison viewed: V22 65 ->V23 658 /73955 proxytargets (correct earlier doc7395typo); majorblue remnants remain. Publicaudit/private4-path comparisonplot retained; docs updated. No commit/push/105/product/Web/deploy, sharedX/Web/GoWIPpreserved; ownedjobs terminal. FullgoalACTIVE: sparse/short/isolated/nearclutter, independentweather/site/date/angle/RADVOL/normalworkerWeb acceptance pending. Do not claim generalization or raise caps.


### S generic-object diagnosis and next-step research, 2026-10-03
Read-only conditional-observation counterfactual on0948/1024/1124 retained full original geometry/negative history: added proposals and residual overlaps all0; not adopted. Private exact-member remote NW diagnosis:235/237,239/239,206/207 proxy gates already profile-nominated; region is inspection only, never algorithm rule. New contract + RED/GREEN scripts/audit_s_object_coverage.py and tests/test_s_object_coverage_audit.py implement reusable generic whole-native partition with original member count/SHA, input/report/evidence bindings, overlapping nominations de-duplicated, unknown/protection preserved and non-overwriting atomic output. Final21testsPASS/RuffPASS; actual8/8 coverage-audit-v2-final PASS, independent exact partition/current reporter SHA/prior diagnostic parity proof b84bed41 report retained. No new QC masks/classifier/promotion, no labels/weather recall/generalization claims; stage target is BEFORE&~ADDED proxy, no_profile_nomination is only profile family. docs/S_GENERIC_QC_NEXT_STEPS_20261003.md records common object/source-background-mixed model, physical scale reuse, labelled development and event/site/date holdouts, actual RADVOL pending, separate near-site/isolated routes and normal105/Web acceptance. Primary AFL/RADVOL/wradlib/PyART reconfirmed; new2026MDPI direct403/browser429, not fully fetched/adopted. No commit/push/105 mutation/deploy; shared WIP preserved. GoalACTIVE; prioritize replacement competing object decision over more same-type thresholds/templates.


### S actual original RADVOL A/B comparison, 2026-10-03
Contract + RED/GREEN offline C harness/adapter and reusable audit/figures added (uncommitted). Unchanged pinned77a0c436 radvolspike.c full A/B core actually executed;24official source/header/license SHA PASS; no RAVE object/ODIM runtime or complete2022five-family claim. Native finiteDBZH/observedquiet/unknown separated, protected input retained, separate admitted mask with geometry/time-seam barriers, child timeout/crash/partial/drift fail closed; no interpolation. Final31PASS/Ruff/diffPASS. Actual8/8 complete under .build/s-radvol-reference-20261003/actual-eight-v1; reportf8b87081..., independent-proof reloads all native inputs/reconstructs exact flags/admission/proxies/NPZ/PNG/current code/build SHA PASS. Proxy overlaps A/B vsV23:0842 733/232;0948 0/90;1018 1578/658;1024 0/61;1042 1268/145;1124 0/0;0818 1223/904;0836 477/101. These include weather, NOT recall/weather loss. Clarification: NW inspection237/239/207 is actual native observed gates, not proxy targets; V23 proposals155/11/189, A/B0/0/0; NW1124proxy0, cannotinfer no local detection from fullproxy0. Two actual plots viewed, original sparse synthetic miss retained. Docs records common source-vs-weather/mixed decision as next mainline; physical response supporting not universal prerequisite, complete histories/finite ownership maintained. No classifier gain/new action/commit/push/105/Web/deploy; shared WIP preserved, fullgoalACTIVE, independent site/date/weather/fullRADVOL/nearclutterisolated/normalworker acceptance still pending.

### X expanded residual followup, 2026-10-03 13:08Z
Currentmain/105candidate unchanged5f21012/fc7e/2247267d; no business edits/deploy/publication this turn. Producer1058265 actualRUNNING81/204volumes729cuts at13:07Z;3reservedprobes/12GiB, actualMinIO495599005696B/98417962inode,MemAvailable24222224384B. Separate independentcompleted snapshot59/531 PASS, frozenoriginal/task/sourceSHA +numericmask/subset/NaN/counts +16tampercontrols; raw_source_bitwise_independently_reloadedFalse explicit. Auditorv1 wronglyassumedstate4withholding0; originalfailurekept, v2accepted0/reviewwithholding actualcontract, noalgorithmthresholdchange. Local5NPZhashverified,5explicit-native-cell-edge diagnosticplots viewed; v1edgewarningretained/v2no warning. ActualnormalZF505309ccut0UI majorNWspokecleared/broadNEpreserved. Untouched407/3a1a majorNWspokes cleared, shortpatches remain. **Notwholeacceptance:** db1ac179cut2 northeast residual ray52.65deg166.8–270.2km;review1628visible1367noacceptedmodel/245responseabove/16otherfitbounds, localprotect0. 78556ed5cut5 southernreview1315visible880strong,982noacceptedmodel/324bothabove/8responseabove/1SNRbelow. Actualnormal-sourcev3probe bothcutsSUCCESS/rawexact, nofits-but-unselectedleak; mixed/localizedreturn qualification remainsalgorithmcoverage issue, cannotfixbywholeweather-veto removal/azimuthlist/relaxingbudget. Privateexpanded-published-residual-v3.jsonl andexpanded-independent-followup-receipt.json bindproofs; X_QC_DELIVERY updated. SSHnewconnection transienttimeouts, existingmuxrecovered; no service/replayrestart. One-shotfinisher1941043liveWAITING_EXISTING_PAIRED_RUN; terminalfull204thenindependent2GiB/1CPUaudit, max12hwait, cannotpublish/restart/claimweathertruth. B=/home/yons/hwapp/dis/rainpulse-xqc-evidence-20261002. Nextfreshproducer/finisherstates, modelrefusalbranch/fullmixedshort-object evidence +expandednormalproducts/maps; hourlyPAUSED, old2715788T, candidateonly/noQPEtrustedfusionforecast, sharedS/Web/GoWIPpreserved. GoalACTIVE/fullscopeNOTcomplete.

### S single-source crossing reassessment, 2026-10-03

Default-off V25 interior single-source/broad-background family explanation + relative bilateral excursion occupancy + exact parent-contour qualification implemented, original RAW/adverse histories/acquisition/weather/caps retained. Final radial directory817PASS/3existing warnings and RuffPASS. Ordinarypytest source/test observation21modules/54testfiles unchanged atcompletion, not frozenpytest. Actual8 audit-v25-single-crossing-v2 COMPLETE/no checkoutdrift/all21executedSHA current; delta-proof PASS input/NPZ/PNG/RAW/protection/actions/caps, oldpaths bitwiseunchanged. RelativeV24 all8added0/withdrawn0/newproxy0; maxjoint49997720 near50M. No actualbatchgain or independentweathertruth; no nativepipeline/V25 Zarr acceptance, no commit/push/105/Web/product/service changes. V1stale cache-source run retained; initialcompletion verifier used incorrecttestlogfilename afterdelta-proof write, correctedfilename/observationPASS withoutreplaying/mutatingdata. Docs S_GENERIC_QC_NEXT_STEPS_20261003 now lead with zero-gain decision: replace shared confirmation using finite original angularcarrier + source/weather/mixed/unknown explanations and separate memberdisposition, reusemeasurements/reduce repeated scans, frozenlabels/event-siteholdouts; don'tcontinue localtemplate layering orcallsyntheticPASS generic effectiveness. SharedWIP preserved/fullgoalACTIVE.

### S pooled shape reassessment, 2026-10-03
Default-off V24 narrow pooled angular shape candidate retains original history/source/gate barriers; no universal power response, target+adjacent windows held out. Prior opposing-background regression fixed by complete-source persistent opposition guard; contract clarified mean native-row occupancy vs union distance occupancy and all unknown intervals. Related tests PASS; full radial revision directory 801PASS/3existing warnings, RuffPASS after import sorting. Actual8 audit-v24-pooled-shape-v2 COMPLETE, delta-proof PASS input/array/PNG/RAW/protection/action/caps and paths identical vsV23; only1042 +30 proposals/0proxy gain, other7zero, withdrawn0, maxjoint49872480. Offline diagnosis only, not independent pollution/weather/native ownership acceptance. Executed source hashes verified before subsequent import-only reorder; retain that distinction, not current-code identical replay claim. Legacy singleton shifted-tail fixture still selects prefix; new pooled route abstains, not fixed overall. Docs updated: no promotion for negligible batch gain; next common source/weather/mixed confirmation with retained adverse histories, separately nearclutter/isolated points and untouched event/site/weather acceptance. No commit/push/105/Web/products/service changes; sharedWIP preserved, fullgoalACTIVE.


### X generic shape root and expanded normal continuation, 2026-10-03 14:47Z
Main/105 unchanged5f21012/fc7/224/fp8e7; no businessalgorithm edit/deploy this turn. Installed model trace actualmaskexact:505dbcut2ray322 bilateral geometry true,293REF slope refusals/1025continuity visits; RAW fieldpaircoupling consistent independentbearings, rawREF trial0gain, strictallmeasured+unknown-family donor exclusion onlynearrefs/0gain; rejected/no thresholds/caps changed. Actual original pinned RADVOL SPIKE A/B C executed6source-bound samples across3stations/cuts0,2,3,5, then independentdirectC/native/protection proofPASS; 505 residualray807/809 nominated. **Exact current compact-proxy ownership root:** installed line observer bothcases actualpublishedQC/maskexact; all809 505raypoints belong6separate15/25dBZcompactcontours,4native rows each,11–19km spans/axis1.03–1.61. Sparse fragments of >100km fullangularcarrier become falsecompactprotection; excludingcompact cancelsprimarygain0. This is not proofallcompact ispollution; comparatorconflicts4563/597/0/9/270/0. Needwholeoriginalcarrier+source/weather/mixed/unknown memberdecision, notnewtemplates/absoluteRFprerequisite/blanketcompact removal. Bexpanded-compact-ownership-v1 completedoutput291cb6e1...; localcopymatch. Actual6Ccomparison andindependentnativeproof+2nativeedgeplots viewed in .build/xqc-polar-morphology/expanded-original-radvol-comparison-v2; sourceexport25e6e5b5...; firstprematuretransportchecksum/missingstate refusalretained-v1, successfultransferSHAverified. No scientificpromotion/weathertruth.
Fresh14:45:55Z expandedproducer1058265RUNNING157/204,1413cuts,17reviewbudgets; MinIO494838435840B/98417666inodes,mem23925121024B; finisher1941043waiting. Newone-shot normalrunner2424559WAITING_COMPLETE_INDEPENDENT_VERIFICATION under Bcomplete-family-expanded-normal-v2, frozen204allsourceSHA5b3322ee...,reuse18successfulcurrenttasks+186newpending normalAPItasks; re-auditALL204native/source/RAW/PNG and finalcatalog. Full204 producer/independentreceipt hashes+actualninecutpreflightSHA64fa105d...required beforeanysubmit. Initialhelperv1wrongly assumednative0–8, guardfailedBEFORElaunch/submit, originalhelper/frozenmapv1retained; v2usesactualreceiptIDs0,2,4,5,6,7,8,9,10 and duplicate/missing/wrongcut/task controlsPASS. RunnerSHAa1a012f8...,auditor10cff321...,helpersmapexpanded-normal-helper-sha-v2.json. Samecurrentcandidateimageonly/no releasepromotion, stopsondrift/failure/resourceguard, no credentials persisted. Old2715788T/hourlyPAUSED/S-Web-GoWIP preserved. FullgoalACTIVE/NOTaccepted; next wholecarrier structural fix tests &weathercounterexamples, freshbackgroundstates/normalproducts/maps; don'tclaimwhole204/fullUTCday/allXsite/date/weatherloss.

X root reproduction 14:51Z: private fragmented-compact-carrier-repro-v1 independently constructed known sparse120km RAW carrier at53/178/359deg and0.5/3.36/8deg. Same installed morphology SHA frozen; baselinewithoutcompact detects1272/1275, currentcompact-enabled detects12/1275 and protects1260, allthreeidentical/RAWunchanged. No realweathertruthclaim, no deployed businesschange or capincrease. Confirms disconnectedfar-range patch PCA cannot byitself veto a complete qualifiedparentcarrier. Next contract/tests should retain parentgeometryqualification/fullnegativehistory and ORIGINALmemberidentity, add heldoutlocal receiver excess/missing/native-weather counterexamples, preserve compactdiagnostic+explicitoverrideownership, boundedincrement fallback keeping alloldmasks. Do not simplyremovecompact/varyfar-range thresholds; sharedS/Web/GoWIPuntouched/fullgoalactive.

### X fragmented original-carrier repair and installed replay, 2026-10-04
Main/origin4eda204 verified; own c336647 adds default-off exact qualified carrier export + offline held-out matched SNR-p90 review, 4eda204 adds 3 wide-fan rotation/elevation controls and source-binding docs. Only owned 5+2 file changes committed/pushed; all S/Web/Go WIP retained. Existing 505-X/128-scoped PASS logs reused and checked, new15 fragmented tests PASS and ownedRuff/diffPASS. Architecture review final no actionable findings. Nearest reference stencil handles drift; matched statistics fixes variability RED; native partition avoids repeated bucket scans, unchanged50M cap/atomic abstention. No production caller/normal algorithm promotion.
Six actual native cuts v5 complete, independently reconstructed membership/local refs/masks, RAW/compact/protection retained; +1053visible weak/0new>=35, primary505 longline809/809;4 controlcuts0gain. Native paired plots inspected; localized strong702 remains (whole merged-history boundary excursion, not simply compact veto). These are offline candidates, not weather truth. Private .build/xqc-polar-morphology/fragmented-carriers-real-v5.
105 diagnostic-only image569e65d4d577... inventories802Python files,800 unchanged vsfc7, only morphology/newreview bytes differ. Parentfc7 morphology e0ecd788... still truncates oversize original widths unlike main5f/c336; first installed preflight failed default-parent QC assertion, retained fragmented-expanded-v1. v2 helper failed local base64 binding before launch, retained. Corrected v3 uses immutable exact parent module in RAM solely for old baseline; candidate retains main complete original history. Installed preflight passes with all3 masks exactly independent v5, gain1011/0strong,809primary; receipt6bfecd8a... and outputc9198306... in fragmented-expanded-installed-v3-preflight. No alternate checkout/sourcecopy.
Background PID3289519: B=/home/yons/hwapp/dis/rainpulse-xqc-evidence-20261002, fragmented-expanded-v3 RUNNING,204frozenvolumes/1836cuts,2CPU2parallel4GiBworkers, reservation native-floor-adjacent-contours-v1/reservation.json slots2. Image569e65.../probe9ecf49b.../manifest343d6c35... frozen. At17:32Z completed21/189, gain1425visible/64>=35,1cutResourceLimit abstained with0increment. Independent prefix auditor f64b20ec... passed21/189 identity/native/accounting, **not** full original-source/protection/weather reload; local fragmented-expanded-prefix-independent-v1.json. Scope only3pilots existingUTC0–7h, NOTfullUTCday/allnetwork. Source-bound first3strong505cells and1strong702cell full RAW/published arrays retrieved SHAchecked;505 native strip figure inspected (same long radial remnants),702 RHO1/ZDRnegative field facts alone not truth. Strong newcases now13cuts/64cells need broader source/weather audit. Don't present early0strong six-cut result as expanded absence of strong increments.
Previous parent paired204/1836 and independent numeric ledger finished, hashes101d39.../8a3b5a...; normal204controller2424559 active, at17:37Z17submitted+18reused/34audited306cuts/1pendingaudit,4READYnormalfc7, fp8e7a32..., disk493.6GB free. Resource waits expected; do not blindrestart. Oldbadcontroller2715788 remainsT, neverresume; hourlyPAUSED. No service/normalrelease/operational candidate eligibility change. CI37136293792 and37138519085 FAIL test-alerting/multiband performance/lint gates; no wholeCIgreen claim.
Next read both fresh states/receipts/resources; finish full204 and independently review strong/localized gains/untouched weather, then normal sole-writer integration/product/map acceptance. Further mixed-source repair must allocate distinct original angular carriers with full competing/adverse histories; don't relax global widths/edge thresholds, discard negative prefixes or blanket-delete compact echoes. Candidate remains untrusted/noQPE/fusion/forecast; waveform8/phase0 unverified,703/801 excluded; no credentials stored. GoalACTIVE/fullscopeNOTcomplete.


### X fragmented carrier sole-writer integration, 2026-10-04 (19:07Z snapshot)
Manual user instruction remains continue generic X radial/fan repair; hourly remains PAUSED. Goal API actually returns PAUSED (not ACTIVE); manual work is authorized by the new user request, no API resume claimed. Sole checkout/main/origin now **8e6d355799ceb64c045f974040f5dc625f22d98c**; owned 10 files committed/pushed, unrelated S/Web/Go/untracked WIP preserved, staging empty. Default-off strict flag, Reason4194304, candidate-only sole writer + native diagnostics/export, no confirmedRF/QPE/trustedfusion. Local weather protection is authoritative if EITHER policy says protect; old budget reviews never promoted, incremental action/evidence/work failures preserve parent. Actual pipeline RED (3 modes missing field), protection counterfactual RED, final **75 PASS** with all nonowned imports frozen from4eda204 in RAM/81 executed modules; 520 full-directory PASS was earlier pre-final protection/export additions, do not claim523 final full rerun. New module/test Ruff and owned diff PASS. Architecture review final no actionable. CI37144249733 completed FAILURE (lint, performance-cd golden fusion metadata, general test missingRP029 gate); no wholeCIgreen claim, failures/logs retained privately.
All then-new strong increments independently source/normal-native/reconstructed heldout disposition checked: **20cuts/120>=35 gates/9 source volumes**, PASS_SOURCE_AND_HELDOUT_STRONG_PREFIX; geometry detector shared, **not independent weather truth**. `.build/xqc-polar-morphology/fragmented-strong-batch-v3/local-verification.json`. Generic merged-envelope issue remains: same702785cut5 original angular profile shows persistent angular power + localized excess, whole merged boundary excursions reject carrier; do not loosen global edge thresholds, discard adverse prefixes or erase all localized strong echoes. New source-bound profile plot viewed `fragmented-merged-carrier-profiles-v1.png`; whole-source fixed lanes/competing source-weather-mixed-unknown disposition still necessary/unimplemented.
New diagnostic-only installed image **0a1dff691d069b6a7c0bef575ac7462b71b19702b6aefc911fa39a1fa7f99c24** built from569 with exactly committed8e6 config/core/newintegration + committed pipeline delta on installed parent;799 other Python modules unchanged. Existing installed radome/phase omissions retained; no extra checkout/source copies; build tar in RAM. IMPORTANT canonical morphology on image differs oldfc7 width-truncation; do not assume normal parent default QC same. Native comparator validation uses exact fc7 morphology only in RAM for parent caller, candidate review canonical; actual canonical image validation separately queued below. No normal worker/service/network/release/Web change from this integration.
105 B=/home/yons/hwapp/dis/rainpulse-xqc-evidence-20261002. Native v1 validator4116495 stopped on incorrect oracle: native export deliberately omits DBZH_QC_DISPLAY; rendering array already checked correctly, no product-code defect. Failure retained. Corrected bounded v2 **PID4176637**, Bfragmented-integration-installed-v2 RUNNING **2/8 cuts**, source505db cuts2/0 actual caller/export and outside-parent/protection/RAW exact; +1029visible/+3strong, native SHA0090a663... forcut2. Offline source helper v4 generalized to all visible (not onlystrong), actual zero-strong preflight passed cut2 +1011/0strong, receiptSHA-bound output716e68c6.../helperad3b1841.... v2 helperSHA8bf3406e..., driverv29d9ada47.... New rule still unselected in normal products.
Actual canonical image caller/export follow-up **PID68126**, driver run-fragmented-integration-actual-v4.py / helper probe-fragmented-integration-actual-v3.py, Bfragmented-integration-actual-v4 **WAITING_INSTALLED_COMPARATOR_BATCH**. Three actual source cuts505db2/7027855/70107cut3; after v2 fully exits and reservation restored, separate legacy frozen source comparator, actual unpatched canonical disabled+enabled pipeline; record old-vs-canonical differences explicitly plus new member/actions/native exact, no publish. Waiting-only v3 PID39953 was explicitly stopped before compute to repair final-state-before-finally reservation race; retained STOPPED_BEFORE_COMPUTE_DEPENDENCY_GUARD_REPLACED. New v4 waits upstream process exit, not merely terminal status. No production batch restarted.
Finite full follow-through **PID39955**, finish-fragmented-expanded-acceptance-v1.py, Bfragmented-expanded-acceptance-final-v1 **WAITING_EXISTING_FULL_BATCHES**. Waits producer204/1836 terminal/exited AND normal204 PRODUCT_AUDITED/currentcatalog204, actual v4 source helper preflight passed. Then exclusive own reservation1/3GiB, accounting all204 and original source/native/protection/membership + independently reconstructed heldout dispositions on EVERY positive visible cut (including zero strong); does not publish/promote/restart or claim weather truth. Helpers SHA frozen in fragmented-expanded-acceptance-helper-sha-v1.json; max12h wait/1CPU3GiB; retain failures. This one-shot is distinct from paused hourly automation.
Fresh19:07Z: diagnostic3289519 **106/204 volumes954/1836cuts**, gain1742visible/120strong,2 atomic resource refusals; normal2424559 **51/204audited459cuts**,34submitted+18reused,1 pendingaudit, WAITING_AUDIT_RESOURCES because extra3 reserved validators/MemAvailable21.5GB; four normalworkers READY,MinIO492.9GB/98.3M inode,120GiB guard intact. Validation reservation restored on exit, don't relax caps or blindly restart. Oldbad2715788 stillT, neverresume. Scope still3pilots UTC0–7h, not fullUTCday/network.
Real CUA map reread user3b3bf9da station702 analysis00:24, actualscan00:26:55/currentresult7a59e5dd...8f1802b5...; low0.54 and3.39degree native layer both show major longspokes/fan cleared on current OLD normal algorithm, nearstationechoes preserved; explicit DEGRADED_MORPHOLOGY_ACTION_BUDGET warning still present. This is no new8e6 publication or full acceptance. CUA screenshots in current tool output; no screenshot file falsely claimed. Browser tab5 may be temporary/closed after turn.
Next fresh v2/v4/fullfinisher states/logs, verify completed SCP SHA, canonical baseline changes and remaining merged-source qualification, then verified normal release + actual new maps. Do not enable new algorithm blindly while old204 normal controller checks immutable network/image fingerprints. No passwords/token files persisted. Complete goal NOT achieved, no markcomplete. Candidate-only/no QPE/fusion/forecast; waveform8/phase0 unresolved,703/801 excluded.

X permalink root19:17Z independently confirmed: user resultb2fc710d.a2d51715 is historical sx-quality-v2-z10-20260930 completed2026-09-29T18:12:42Z; same scan latest7a59e5dd.8f1802b5 isprovenance5f21012 completed2026-10-03T09:49:41Z. Task fingerprint ae659c81→8e7a327a. **Normalized inputs also differ**: old volume.zarr0a750296... vs validated rebuildsf8ba35f8...d0147072...; don'tclaim array-identical source orattribute allgainonlyclassifier. Initial task-lineage check incorrectly compared second resultfragment(attempt UUID) as taskID→404; then asserted same inputSHA→failed, retained emptyv1; correctedv2 stores actual different URI/SHA. Scanidentity/times same, normalized lineage different. Actual source selector HEADRadarQCWorkspace.tsx151 explicitly pins queryresult;205 already has historical warning and查看最新结果button, refreshclears pinnedresult. No Webcode change. Current5f low/high map reread alreadyshows major cleared; oldpinned CUA navigation/read twice30stimeout(kernelreset), so oldpagevisualnotaccepted. API current/history and source code establish pinning, not screenshot of old result. Private permalink-version-check-20261004.json andpermalink-task-lineage-20261004-v2.json. New8e6fragmented integration remains defaultoff/notpublished.

19:20Z follow-up: v2 installed comparator caller/export **8/8 cuts COMPLETE**, local receipts/outputSHA independently checked (1078visible/4strong increments). Actual unpatched canonical image v4 **1/3 cuts complete**; primary505dbcut2 +1011increment/outsidecanonical-parent/RAW/protection/export exact, but canonical default parent vs oldfc7 differs **1676 QC gates**. Thus cannot equate old-installed-parent comparator with actual new normal runtime; assess restoration/removal directions and source/weather/unknown explanations before any activation. Image remains diagnostic only. Remaining v4 cuts running and fullfinisher39955waiting; fullproducer121/2041089cuts,1809visible/120strong gains,2resourceabstentions; normal51audited waitingtemporarythirdslot resources. No blinddeployment/guardrelaxation. Native source arrays for canonical differences not retained in tiny output, counts known but direction NOT yet measured; do not invent weather-loss/benefit.


### X whole-source continuation, 2026-10-04 02:35Z
User continues generic X repair; goal ACTIVE, hourly automation PAUSED. Sole main8e6d355 preserved; no X business code/worker/channel/network/Web change in this continuation, all unrelated S/Web/Go WIP retained. Diagnostic204/1836 COMPLETE, independent full mask/source-identity accounting SHA7e49d67391bf8b4c5b9a774ab20e204e49975427f758e60b7c6631f69f383eff:3461visible/580strong/67positivecuts/3atomic resource refusals. Largest late3 original-source/published-native/independent heldout checks PASS1453visible/420strong; local receipts/outputSHAverified. Bfragmented-late-largest-source-v1 PID2843674 exitedCOMPLETE, receiptSHA33d99125aeaf446666be60077d5c0f968292441608ff14e71566ae2ac2b24f1e. All540 old120+late420 strong verified notraintruth; full67positive proof stillfinisher39955.
Canonical actual3/3 finished, allreceipts/outputSHAverifiedlocal. New bounded directionsprobe2784107 exitedCOMPLETE:505dbcut2 actualcanonical1676differences ALL restored/0removed/0finitechanged,12strong; action3/reason3584 phase+calibration+attenuation uncertainty, explicit hard/context/local/compact masks0. 22original acquisitionrays mostly62–80deg145–274km plus4–9deg77–81km; no weathertruth or qualityimprovement claim. Bcanonical-parent-directions-v1 outputSHA c0657e2b4ddcb0eaad479baa7056b8fde3b5518af9b87aa9340c28d251f8d6d7; source28c57275...f4260c9. Native newimage0a1 remains diagnostic only. Fullcommitted8e6 X regression now523PASS/7existingwarnings/105executedmodule SHA, no sourceoverlay; v1 had522PASS and1 CUSTOM IMPORTLOADER palette orphanpath (notproductdefect), retained. FileReader+verifiedcommittedpalette fixes testharness, fullv2GreenSHAproof + XML/log .build/xqc-polar-morphology/fragmented-integration-committed-full-...-v2.
New explicit offline SNR whole-original geometry ablation6nativecuts+22syntheticcontrols PASS. Primary702785cut5 additional292visible/80strong, retains845stronglocalexcess; notbusinessalgorithm orweathertruth, no automaticdeleteall. LocalsourceNPZ67333a2...e86. First serverpreflight291/79 refused vslocal292/80; exactly1gate nativeRow25/gate2637 az174.895/r197812.5 raw83.5/QC83.5/SNR67: sharedweatheradapt[-32,80] excluded legalmeasurement. Nativev2usesEXISTING source_family_geometry.source_view[-50,100] explicitobserved/available, weatherstencilunchanged. Independentall4nativegeometry/nomination/excess/unknown masks bitwise match local; outputSHA359a22d47fba9afb3c61cc6e37a2a585a34d381fe4ae589d3bf72d179d6d2481,mask2a9e2c6aa6155bd28a7225c5889d40b0059a6d82b9fb7e58faca71145e260c75. Originalfailedsnr-original-expanded-v1/2919617 retained, neverresume.
Finite new SNR full204 driver PID2958358 Bsnr-original-expanded-v2 WAITING_CURRENT_FULL_PUBLICATION_AND_SOURCE_ACCEPTANCE afterrealpreflight292/80/all4masksPASS. Helpers probe-snr-original-expanded-v2.py/run-snr-original-expanded-v2.py underL/B, bytesSHA bound. It waits39955terminal+exited, then exclusiveownR1slot4GiB and max8hboundedwait; usesnormalSUCCEEDED+PRODUCT_AUDITED assets, frozenRAW/source+QC, no tasksubmission/promotion/workerchanges. Momentthresholdsnumericallyreuse frozenDBZtiers only explicitSNRexperiment; productionmomentpolicy/postgeometryworkcharge notyetdesigned, mustnotcall this a readyalgorithm. Resourcefallback parentmorphologyrefusals kept, geometry50Mcap and4GiB/30mintaskbounds. Fullnative comparisonPNG source-bound/full210.7km inspected; small improvement, twofar strongpatches remain (notvisualacceptance). Lsnr-original-carrier-v1/full-native-comparison-v1.png metadata says NOTdeployed. Next whole angular source/mixed/unknown profile and finite/time-persistent residual evidence, notglobaltolerance/source-range crop/weather-all-delete.
Fresh02:30Z normal2424559 stillLIVE185/204audited1665cuts,2active; finisher39955LIVEwaitingnormal204; Rnone afterpreflight,MinIO487044177920B/97945267inodes,MemAvailable22.6GB. Oldbad2715788 remainsheldT. Do notrestartnormalbatch/globalrelease until fullcatalog204. CUA globalinventorytimeout; browser selection/list works butoldtab5 readandfreshlatesttabcapture timeout30s, no newactualUIacceptance/no Maclockclaim. Oldpermaversionpinning alreadyknown; preservelatestlink, no fictitiousscreenshot.
Raw read-only filenameinventory found24stations/22420files across20260828,20260901/02/03 (5466/5731/5709/5514 respectively), fullhourfilenamesavailable. Sourceinventoryprivate x-source-directory-inventory-20261004.json; notheaderUTC/unit/decode acceptance oropenedweatherholdout. Current204scope only3pilots07h; laterUTCday/crossdate/crosssite needed. Oldsx-priority1 backfill/qc-backfill/composite files remainstaleSep28 fromuserpause, doNOTblindlyresume orinterpret RUNNING text aslive. ExistingS17-z9591 STOPPED_ERROR belongs separateSwork; do notchangeS. ZF703/801 remainexcluded/no waveform8/phase0guess/trustedfusionQPEforecast. No secrets persisted.

02:45Z owned acceptance doc48lines committed/pushed main6762a749feb5ba7c0814b3ebef8f3685a2b3834d (documentation only; algorithms still8e6 code tested523). origin exactverified; sharedmemory/otherWIP notstaged. snr-source-preflight-independent-v2 all4masks bitwisePASS; image569 geometry same frozenmain. NewhelperSHAs private continuation-helper-sha-20261004-v2.json. Lastfreshnormal191/204/1719cuts, healthyfiniteparents2424559/39955/2958358, old271T.


### X original SNR integration pending shipment, 2026-10-04 03:52Z
Main6762a749, sole checkout/shared S/Web/Go WIP retained. Optional strict SNRCarrierPolicy separates SNR dB from DBZH, exact existing geometry/weather prerequisites, joint geometry/profile caps, native candidate/excess/unknown/state and sole candidate writer; defaultNone retains olddigest/revision/actions. Final frozen full-v2 559PASS/7existing warnings/no skips, dependency6762 RAM+5owned overlays; source proof/JUnit private build. Firstfull-v1 invalidated on sourceupdates retained. Six real native cuts bounded primitive BITWISEmatches prior hypothesis masks, RAWsame/caps unchanged, notweathertruth. Normaloldbatch204/1836 NORMAL_PUBLICATION_AUDITED oldfp8e7; fragmented67positivecuts/28volumes3461visible580strong source/member/heldoutverified, NOTweathertruth/newnormalacceptance. Sourcehistory10vol/90cuts COMPLETE, temporalrepeat/permanentangle refuted; farther uppercuts unobserved notcleared. Angularcommon-gain diagnostic0accepted/notimplemented. Header88samples/22stations4dates type16PA64/type1regular24, SNRmissingZF900firstrows; prefixSHA notwholefileSHA, unparsedphaseNone notabsenceproof. SNRdriver2958358 RUNNING175/204 at03:50Z,25436visible2770strong/3resourceabstentions,R1slot4GiB; doNOTswitch fc7liveworker while itsidentitycheckruns. Old2715788 remainsT/neverresume; hourlyPAUSED. No newnormal/release/worker/map change. Finite/transientstrongresidual and broaderweather/date/site/format gates remain. GoalAPI PAUSED/manualcontinueauthorized, notcomplete.


### X interrupted-run closeout, 2026-10-05 Beijing time (17:13Z Oct4)
Supersedes 03:52Z pending shipment: owned c5c92b4 SNR integration, 7cebde6 AST-equal formatting, b2fbe97 exact percentile batching committed/pushed main. Documentation closeout 5e391a1 scoped ONLY acceptance doc; shared memory remains unstaged and unrelated S/Web/Go work preserved. SNR frozen559PASS and36 final focused, percentile frozen573PASS/0skip then final import-order-only current bytes19PASS; no whole CI green claim.
Finite source-percentile-real-v1 PID4066745 exited normally05:27Z, 4/4 ACTUAL_NATIVE_PERCENTILE_PARITY_VERIFIED_PENDING_NORMAL_PUBLICATION. Image cab6ff686f8025b4df1623760a6739b2bf34e48db5c5bec8d7697f043c8d56a4 diagnostic only (parent987,2changed/804unchanged Python files). Frozen source/helper/module/input/output/receipt membership checks PASS, native SHA equals old actual c5 export for7023bcut0/702785cut5/70107cut3/505dbcut2, RAW unchanged/export arrays exact. Call times262.416/143.371/16.046/229.333s under singleCPU cProfile. Same user7023bcut0 old380.195->262.416s (30.98%lower); percentile8555102->2500834 (70.77%fewer). Not normal throughput, no new classification gain. ReceiptSHA f973d9496b127de6a754120b45c7e61880011b85023d36d3c89b9f510a66acf7; local independent membership/SHA report dc880e2e6193406121cf2ef02402393972877646ee976d84a2e323344bdb5cf6 mirrored B. Independent native recomputation FALSE, hash-bound producer parity TRUE. First verifier assumed baseline timing output nativeSHA existed; KeyError kept, corrected to source/cut timing comparison plus separate frozen expected-native receipts; no product changes.
Earlier finite jobs complete: old normal204/1836 catalog audited (3pilots UTC0-7h, NOTallUTCday/network); complete fragment proof67positivecuts/28vols3461visible580strong SHA c86aba7ffa7c1e87744c8a293255370ed868a8226657af6cede059b7825a99b3. SNR prototype204/1836 complete25440visible/producer2770strong/3resourceabstentions, NOTbounded native integration/normalpublication. Actual c5caller9/9 complete628visible86strong; user3b cut9 remains state4 action-budget refusal, not admitted/cleared. Native-moments6/6 original normalized arrays checked, not unsigned source-code semantics. All diagnostic processes exited and reservation absent on fresh105 check; old stopped2715788 must neverresume. Four live normalworkers fc7/5f21012 candidate healthy unchanged; newSNR/release/preparedchildf1bb and cab diagnostic not activated, no new UI acceptance (browser read previously timed out). Actual disk486112100352B/97890799inode above120GiB/100k guard; no cleanup/restarts. Hourly rainpulse-s-x confirmed PAUSED, no automatic revival.
Open mainline: normal versioned publication/new maps for generic SNR+fragment candidates; finite/transient far strong strips still need complete-object source/background/weather/mixed/unknown attribution, no fixed-angle/station masks, no deletion by missing upper-range data. Whole-day/network/date/site/angle/weather gates remain. Waveform8/phase0 unknown,703/801 excluded; candidate trustedfusion/QPE/forecast disabled. Full goal not complete. Current phase is real performance/native-parity closeout, not all X morphology acceptance.

### X finite final application shipment, 2026-10-05 Beijing time
User explicitly narrowed goal to final deployment/closure, no endless QC research. Main/origin875c6c692ba610e51a5662e0a8d9afafcf04e64c pushed. Final specialist/adversarial review identified prior-ray live fit-cache workspace oversubscription and SNR carrier membership check after allocation; both reproduced RED and fixed, with bounded membership census charged to existing work cap and legacy unbounded collector unchanged. Frozen575PASS/7existing warnings before AST-equal line-wrap; final exact28PASS/RuffPASS. Private runner audit leftovers/retry defect fixed with UUID attempt files; timeout container ownership now exact-name validated, cleanup scoped, stale owned containers block admission. Actual AST retry/timeout/tamper guard checks PASS; no source thresholds or protection loosened.

105 final candidate image b2ba8f4f8f46d36518da7397ca0530719738c8b8f4e16eba61fdae6ddfb99794, tag xqc-snr-final-875c6c6-candidate-mb; only3resource modules changed/803Python files unchanged fromcab. Four source-bound actual cuts native bytes EXACT priorc5/b2f outputs, RAWsame, realdriver796781 terminal18:56:54Z, receiptSHAab8d448585b9246c6b2e3a41a493c606b361012b74a1c9682b1b428059a5d060; independent receipt/membership/input/helper hash verification PASS (notindependentnative recompute).

Final live network96da59107ab4c74aa6aacd1b23b7a0e2cdf2ef7fe377d2394c4cf046b72f7dab, fp d61b479e64a0a6384d82025c62371ad3ec61834836dab94530814cfd36c4e2cc, releasefujian-sx-full-20260828-x-snr-final-875c6c6;4workersREADY. Onlyzf505/zf701/zf702 SNR+fragment candidates, operationalfalse, noQPEtrustedfusionforecast. Guarded drain/CAS switch retained all other service containerIDs. Firstb2f normalbatch controller536714 held then retired onlyafter its SUCCEEDED task309455 and completed audit child; superseded receipt retained, no lifecyclemutation/SQL. Old2715788T mustneverresume.

Finite final normalbatchv2 PID864924 ACTIVE under B=/home/yons/hwapp/dis/rainpulse-xqc-evidence-20261002/snr-final-normal-v2; fixed22volumes/198nativecuts (zf50512/zf7014/zf7026), includingall6mandatory sources +2untouchedcontrols; notfullUTCday/network/weathertruth. ManifestSHAe7cdad6101ee2a951611b0c92f11274d232d5807906c2c24f34aecde842ac36f; runnerbbe1210d...,auditor802535b3... frozen. At19:06Z submitted1/audited0, firstuser3b active; startup NOTacceptance. Audit compares every native field/RAW/coordinate and polarPNG exact installed recomputation, mapPNG validity, finalcatalog latest task binding; warns separate fromstructuralPASS. Do notcallallqualityclean; SNRstate4 and inherited morphology/action refusals remain transparent. ActualMinIO486034673664B/97887918inode, mem31.5GB. HourlyautomationremainsPAUSED; goalACTIVE untilfinite delivery proof done.

CI875 run37225769083 FAILED; parent5e run37220010025 same failed tests/lint4898/TypeScript errors, comparison private reportPASS inherited sameness; build and specialised checks pass, no wholeCIgreenclaim. CUA sameIAB tabDOM/screenshot/fresh-tab reads continue timeout; Codex native fallback safety-denied, do notbypass. Publiccatalog/product HTTP works, actualnewUI acceptance notclaimed. SharedS/Web/Go uncommitted/untracked work preserved, memoryunstaged, only6owned files committed. Next inspect PID864924 freshstate, finish22normal audits/catalog +publicmap/quality evidence and finite closeout; no additional research/thresholds/full-daybatch expansion.

Final shipment checkpoint followup: runbook docs committed/pushed ff36e2bd498510bbae71d5f0e4cd0e6f3519cfee (code remains875c6c6; no later business edit). Finite independent finish verifier PID923348 WAITING_EXISTING_FINITE_NORMAL_BATCH under B/snr-final-finish-v2, cannot submit tasks; after22/198 checks receipt/hash/cuts/catalog/public assets and writes terminal finite-delivery report, UI/weathertruth remainsfalse. Firstfinal normaluser7023b taskc17aa0d4-95b5-48dc-8f2c-ec1a318229d3 SUCCEEDED19:14:53Z, resultc17aa0d4-95b5-48dc-8f2c-ec1a318229d3.25e0a556-afa2-40d4-82a0-02869a1c3a5b latestpubliccatalog/currentversionverified. PublicwholecutIDs0/2/4/5/6/7/8/9/10, source volume_start00:26:55.533865Z (old userquery00:24 wasselectiontime, notactualstart). PublicRAW/QCmapPNG6 assets cuts0/2/9 SHA checked/contenttypevalid and viewed:majorraysremoved, cut2 someedgeremnants; NOTweathertruth. PNGtransparentbackground RGBblue hasalpha0 confirmed; notbluefinite-fieldclaim. Metadata7quality-warningcuts onfirstsource, retained DEGRADE/ACTION_BUDGET notfullyclean. Nativeindependentauditongoing (firstcutPASS observed), notcountwholebatchaccepted. CUA sameIABfresh tabagain20sectimeout, noUIacceptance/bypass. Hourlyautomations localTOMLfreshstatusPAUSED, no schedulechanged. CIff36 run37227499723 completedFAIL; docs-onlydelta from875, no globalgreenclaim. Nextfreshreadfinitebatch864924/finisher923348, handleonlyconcreteworkflowerrors, collectfinal22/198report/maps and closeboundedgoal; doNOTredeploy/repeatfirsttask/expandalgorithmorwholedaynetwork.


X final finite closeout resumed 2026-10-04 19:36Z: normal864924 and independent finisher923348 verified LIVE/exactcmd; fixed22 manifest unchanged, submitted4/audited3/native27, fourth93d2 pending3cuts, owned audit100%CPU551MiB/3GiB. No task failure, MinIO485788737536B/97876620inode, mem30.56GB, guards unchanged. First user7023b finaltaskc17aa0d4 full9cut receipt independently mirrored/verified SHA32ef7df18fa7e57c88b62f6737ec2465558b4e1fb3134faa135cfcee63663a5b, native RAW/geometry/source/direct native arrays exact and36PNGchecks;7quality warnings retained. Local final-v2-first-audit/local-verification.json structuralPASS, independent native recomputefalse/producertrue. Earlier local read ran before SCP completed and failedFileNotFound; completed SCP then rerunPASS, no product defect. Current total warnings8/27, notweathertruth/allclean. CUA existing IAB2 tabread again20sectimeout after inventorysuccess; no UI acceptance or denied-native bypass. main/originff36 exactchecked, no code/docs/runtime/channel mutation this resumed turn. Hourlypaused, old2715788neverresume. Goal ACTIVE until22/198finalnormalcatalog+finisherterminal; do not treat background progress ascomplete.

X finite closeout 19:46Z: all six mandatory original reported sources completed normal audits54cuts/216PNGchecks, source-task-fingerprint/receiptSHA/RAWgeometrytime/allnative-direct-computation/polarPNG exact independently checked locally in final-mandatory-six-verification-v2.json. Qualitywarnings11 retained, candidate only, notweathertruth/all22/UIacceptance. Supplemented finisher selectedmaps with missing mandatoryd426 public currentresultab47e676.449b80bb assets nativecuts0/5/9 RAW+QC, sixSHA+alpha verified, actualassets viewed: majorS/Wlongspokes andfan absent, compactSWcut0 patch and distantcut9pixels retained; alpha0 RGBblue isnotfiniteecho. Private final-public-d426-v2/{report,visual-inspection}.json. No running frozenhelper/source/release altered. Background864924/923348 live; remaining16finitevolumes, goalactive, hourlypaused.


### X final candidate application delivery closed, 2026-10-05 Beijing time
User narrowed the goal to final deployment and closure, without endless QC research. Code875c6c6 remains installed on105; final owned documentation commit aa7845ec5722b8260ce81cf461ca9e136fc68999 committed/pushed main and origin exactchecked. Only two owned docs committed; shared S/Web/Go WIP and this memory remain unstaged. No further algorithm, thresholds, source interpretation, release, candidate eligibility or S changes during this closeout.
Fixed22 normal publication TERMINAL NORMAL_PUBLICATION_AUDITED at20:59:13Z Oct4, all198 nativecuts/792PNGchecks/currentcatalog22. Independent finite finisher TERMINAL FINITE_FINAL_APPLICATION_DELIVERY_VERIFIED at20:59:38Z. Both864924/923348 exited on fresh /proc check. Final report SHA9185ed9286dd654e5d7465c6e7c73711f9493524cb545d134e887bb5542e4183 locally mirrored and independently checked frozen22 membership/198 distinct cuts/22 publicproduct hashes/24publicPNGbytes. Producer independently recomputed allnative arrays/immutableRAW/coords/time and polarPNG; finisher/local consumer didnot recompute native again. Six mandatory54cuts/216PNG verified separately. d426 sixthcase supplemented with6mapassets cuts0/5/9 SHA+alpha; selectedQCassets of all6mandatory plus2untouchedcontrols viewed. Majorreported spokes/fans absent in selectedassets; some thinfragments/compactreturns/twofarstrongpatches remain; controlclusters retained, notweathertruth. Transparentalpha0RGBblue notfiniteecho.
Qualitywarnings27/198:18DEGRADED_MORPHOLOGY_ACTION_BUDGET,8ACTION_BUDGET_ABSTAINED,1DEGRADED_COMPLETE_FAMILY_ACTION_BUDGET_ABSTAINED; stations702/701/505=14/6/7. Candidateoperationalfalse/trustedfusionQPEforecastdisabled, notallXradialproblemssolved/no fullUTCday/network/weatheracceptance. Waveform8/phase0unknown/703801excluded unchanged. Browser actualUIacceptanceFALSE: IAB2getTabDOM timeout; documented directtabs.get+url worked but actualscreenshot again20stimeout/kernelreset, no deniednativebypass. HTTPindex+JS/CSSbundle availability independently verified, notactualbrowserrenderproof. Finaldocs state limitations; no furtherresearch/full-daybatch expansion in this shippedgoal.
21:04:51Z runtimehealth PASS4healthy/READY/idle workers imageb2ba8f4.../fpd61b479e..., actualMinIO484826603520B/97825755inode aboveguards; owned auditcontainers0. FinalruntimeSHA boundtofinalreport. First read-only capacity check failed due shellquoting of embeddedPython, corrected properremotecommand shellquote and rerunPASS; noappdefect/state mutation. Private snr-final-finish-v2/{final-report,state,runtime-closeout,local-final-verification,web-http-closeout,completion-audit}.json plusassets. Hourlyrainpulse-s-x freshlocalTOMLPAUSED; no recurrence enabled. Oldheld2715788mustneverresume; keep supersededfailureevidence.
Finaldocs CI37235068581 completeFAIL; build/specialised checksPASS, test/performanceCD/lint/verifyFAIL. Private failedsignature comparison against code875 CI37225769083: same10 failing tests/4898lint, source unchanged bydoccommit; notglobalCIgreen. Owned deliveryrunbook/acceptancedoc finalterminalcounts/receipt/warnings/recovery/currentURL pushed. Finite backend batches and candidate shipment closeout completed; broad weather/allX morphology readiness remains explicitly unclaimed. No new normal submissions/algorithmresearch required for this final shipment.


### X budget counter and increment-review shipment, 2026-10-06 Beijing time
User asked remaining X QC problems/difficulty; active bounded goal remains first two flow issues only, not far strong weather/mixed research. Main/origin fa8dac494f63983e7154ff8cd72659d9f1239a20 pushed: noise-censor overlap excluded from heuristic cap, exact native review positions retained in window/fragment/SNR cap refusals; accepted mask/state4/RAW/protection/old confirmed actions preserved, no thresholds/caps raised. Frozen X suite604PASS/0skip, final assertion-format ASTequal15PASS; one new test E501 identified from CI4899 vs inherited4898 and fixed locally, pending scoped test/doc commit.
Actual final11cuts source/native/PNG/array checked locally,1594unique newly withheld visible>=5/133>=35, two untouched controls+truecap QC bitwise equal; notweathertruth. v3 driver STOPPED_ERROR due invalid final-parent oracle, receipts preserved; successful7 percut records independently verified plus corrected v4 actual-stage parent4cuts terminal; both472236/784212EXITED. Private report increment-budget-real-independent-v1.json SHAd48f4e6ae398d1d0c1965231de2f689227cfcfca97b6cf153c812c4340b96362, producer recomputation true/consumerhash+arrays only. Actual7 prior/newPNG inspected, spoke removed/remaining compact and sparse returns retained; alpha0RGBblue notfiniteecho.
105 budget-review image a1086b6c35a05158d4db833f1775a23ac6234baf47935a8288b00f02dfda9bed (tag xqc-budget-review-fa8dac4-candidate-mb) deployed via drain/CAS with fourREADY;5owned modules changed/801unchanged. Runtime pipeline child76429d669... retains existing radome/phase exclusions with owned version suffix only; full local pipeline not copied into candidate. Newnetwork4c1addf27a2cd0cec0e27a95db3e6574a7f6dcbe5cb6a3f4809aebeb7476e946/fp36a181d59c1d5fdbfb2dc29587e280e5478040da07ea1d0505933feabbbba94e/release fujian-sx-full-20260828-x-budget-review-fa8dac4. Other service containerIDs unchanged, candidates505/701/702 only, operationalfalse/trustedfusionQPEforecastoff. Promotion B/budget-review-release-v1/promotion.json, image receipt budget-review-image-fa8dac4/image-receipt.json.
Finite newnormal driver PID987559 and independentfinisher987784 launched with frozen22/198, normal budget-review-normal-v1, finish budget-review-finish-v1; manifest3af9e4b879171127f358bfcfa85095e09fc5eef662830014bcc2e2e945d56f89 and helper SHA ledger. Atlaunch RUNNING, notpublicationacceptance. Normalaudit exact installed recomputation allnative/RAW/coords/polarPNG plus new review state/reasons/writer/CRoff checks; cataloglatest22 required. First audit before further submissions. Actualcapacity484677029888B/97825543inodes guard120GiB/100k; old held2715788 NEVERresume, hourlyPAUSED. CI fa8 run37345364040 COMPLETED failure: build/specialisedpass, inherited10fusiontests/TS/lint plus corrected1testE501; no globalgreenclaim. GoalACTIVE untilfinite normal publication+actualpublicmaps proved; no full-day/network/independentweather/UI render claim, CUA earlier reads timedout. Preserve all sharedS/Web/Go work and unstagedmemory; do not restart old batches or schedule.

Budget-review shipment doc/test followup committed and pushed d78e09ef4d445540f6bdbe4e039fe4ed0198e4e1, origin exact verified; installed algorithm remains fa8dac4/a108 image. Two owned files only; fresh inventory410 other dirty/untracked entries preserved, including10 newly observed common-domain NowcastNet training files from shared work. One new test E501 fixed by AST-equal wrap, final frozen15PASS/0skip andRuffPASS. 22/198normal PID987559 latest WAITING_NORMAL_TASKS submitted1/audited0, finisher987784waiting; startup notacceptance. GoalACTIVE and hourlyPAUSED. Later closeout must fresh inspect exact processes/state/newpublicmap/catalog; no source thresholds/additionalresearch/oldcontrollerresume.

Budget review goal continuation: prior turn PROGRESS (published exact5module candidate and launched normal22/198). Fresh105 confirms normal987559+finisher987784 LIVE, first task7ce5a466-86fd-4807-8aec-6cd25eb410fb SUCCEEDED17:45:32Z with attempt e4ce54cf-2721-4787-8b1f-595f5b60ded9; normal current AUDITING firstvolume, owned audit c03ec0632da43162aa9122665716209c57388d72287979a3bfb97748abc24669 LIVE100%CPU1.264GiB/3GiB, actualpartialcut0/2PASS. Reported7023bcut2 normalnative independently matches installed recomputation,38exactSNR BUDGET_REVIEW gates, state4/QCaction3/QCNaN/CRoff; true ACTION_BUDGET_ABSTAINED retained, notfailure/allclean. Publiccataloglatest=result7ce5a466.e4ce54cf exactnewversion. Sixpublicmapassets0/2/9+productSHA verified locally under budget-review-first-public-v1, localverification cf26b3324c61f6be5a6a8396b3cf22dc74b760f2c2f24288c2786031072bded5. ActualRAW/QCcut2 viewed: broadrays/fan cleared,compactnorth and tinyedge remnants remain, alpha0RGBblue notecho; notweathertruth/browserrender. CUA freshbrowser3emptytabs, newtab screenshot/DOM still30stimeoutkernelreset, no bypass/unlockassertion.
Additional readonly finite198 old-v-new native companion started PID1091982 under B/budget-review-delta-v1, LIVE WAITING_AUDITED_NORMAL_PRODUCTS. Frozen helper SHA budget-review-delta-helper-sha-v1.json; doesnot submit or mutateprod,2GiB/1CPU percompletedvolume, all198 source/RAW/geometry/protection/oldconfirmed/outside-reviewvalues+prior-withheld-not-restored andexactgaincounts. Sourcehelper audit-budget-review-delta-v1.py / driver run-budget-review-delta-v1.py mirrorL/B. Requires normal22/198 receiptcomplete plus own NORMAL_PUBLICATION_OLD_NEW_NATIVE_DELTA_VERIFIED beforecompletion; doNOTrestartnormal/finisher/deploy because observationtimeout. Latest d78CI37349804139 completedFAIL10samefusiontestnames/4898lint, extraownedE501gone; globalCIstillnotgreen. Docs/code notchanged thiscontinuation, onlyprivatehelpers/evidence andunstagedmemory; sharedWIP preserved. GoalACTIVE/hourlyPAUSED/old2715788neverresume.

Budget-review bounded closeout continuation 2026-10-05 18:46Z: prior user-status turn classified verified wait (three actual controllers987559/987784/1091982 LIVE/exactcmd); no restart or scope expansion. Fresh normal13/22,117/198 and delta13/117 LIVE; earlier completed12/108 mirrored under .build/xqc-polar-morphology/budget-review-closeout-partial-v1 and local source/receipt/task/nativehash/member/protection/delta consumer PASS432PNGchecks. Partial old/new108 status transitions12false morphologywarnings corrected,6truewarningsretained;55newvisiblewithheld/5strong, outside-reviewQCexact/no prior restoration. Notfinal22acceptance. Rechecked reused604full/15format XML0fail0skip plus5ownedsourceoverlay SHA/currentGit bytes; no tests rerun. New private mirror/consumer helpers frozen in budget-review-local-closeout-helper-sha-v1.json; --final correctly rejectspartial12snapshot. Final normal/finisher/delta reports and runtime/owneddocs closure stillrequired. Publicmandatory6/54/22assets proof alreadycomplete; final selectedhighcut/controlmaps pendingfinisher. main d78 unchanged,410sharedWIP preserved exceptappendmemory, nopublicdoc/algorithm/runtime/schedulewrite. HourlyPAUSED; old2715788 NEVERresume. ObserverSSHsession82754 is finite read-only600s monitor, notnormalrunner; its timeout/end mustnotrestartthree livecontrollers.

### X first-two budget flow fixes closed, 2026-10-06 Beijing time
Supersedes partial13/117: normal987559 terminal NORMAL_PUBLICATION_AUDITED19:25:53Z Oct5,22new/0reuse/198native/792PNG/currentcatalog22. Finisher987784 terminal19:26:06Z FINITE_FINAL_APPLICATION_DELIVERY_VERIFIED, reportSHA14070eda5e6bf978d1efbf9b32afe92feb4a619e895cad2027c778883b03bd3d,22publicproducts/32publicmapassets; mandatory6/54/22assets supplementalSHA9a3ba323... retained. Readonlydelta1091982 terminal19:26:12Z NORMAL_PUBLICATION_OLD_NEW_NATIVE_DELTA_VERIFIED, reportSHAfcfceae964c00b62b5f4b599f9080dbdaf6abb95df90c04e5d1bc6146ec2d6a4, all198 RAW/geometry/protection/priorconfirmed/outside-reviewQCexact, no priorwithheldrestored. Newreview1760union/1611newfinitevisible>=5/133>=35,2controls0gain. Oldwarnings27 ->11:16morph->EVAL,1morph->complete-family genuinecap (3868cut8 core190987<=214606 nowcorrect),8overall+1morph+2completefamilyremain. No new formerlyEVAL warning. Producer independently recomputesnative, localconsumer hashes/membership/arraysreceipts only. Finalmirror .build/xqc-polar-morphology/budget-review-closeout-final-v1; local --finalPASS with all32RGBA720maps/hash, partialfinal/tamperedSHAnegativechecksPASS; firstnegative used systemPythonmissingPIL setup error then real venvPASS, no product defect.
19:27Z finalruntimePASS fourhealthyREADYidle correcta108/fa image,fp36a181/network4c1add/channelrev32/exact5moduleSHAs, all3newcontrollersEXITED/owned auditcontainers0,MinIO483466223616B/97763290inodes,old2715788T NEVERresume. Hourlyrainpulse-s-x freshTOMLPAUSED. Actualselected3868cut7/8/10QC+cut8RAW and2controlQCviewed: majordensefieldremoved butcut8faredge straightstrips/cut7sparsefragments remain thirdissue; bothcontrolclustersretained. Backgroundalpha0 measuredRGB[65,155,241], notfiniteecho. No weathertruth/fullUTCday/network/browserrenderacceptance; CUA timeout remains recorded.
Only owned closeout doc committed/pushed main 5e2ded2c4e3d9d617931387f5fb5e174159ae3a3 (5e2ded2,72insertions/5deletions),origin exactreadback; sourcecode remainsfa8dac4 and ASTequaltestfollowupd78, no morealgorithm/threshold/runtime/schedulewrites. All410sharedstatusentries preserved, memoryunstaged. Doc105SHAb3f4d6f2241f4a04538f276a5a71b1f0e9506c7750164b846133229afdbf0420 synchronized exactGit bytes; directSCP to root-owneddocs and sudo-n lackedauthentication, handled onlyowned newdoc via existing authorisedDockeradministration/atomicwrite; no credentials or privileges/config changed. Finaldoc CI37364644025 lateststate pendingreadback; inheritedglobalCIstillnotgreen. Complete boundedgoal only after finalCIreadback+requirementledger, not allXquality acceptance or thirdissue research.

Bounded first-two completion audit PASS under budget-review-closeout-final-v1/completion-audit.json: all required flow/source/runtime/finite-publication/owned-doc/shared-WIP checks satisfied. Latest docs-onlyCI37364644025 readback queued (build/prelaunch/crossradar/residual jobsPASS, other jobsqueued); no algorithm changes sincefa8, no globalCIgreen or whole-project release-readiness claim. No remaining required work for these two deterministic flow fixes; actual browser/weather/fullUTCnetwork and thirdfar-strongmixed research remain distinct limits. Current docmain/origin5e2ded2 exact,105docSHA b3f4d6f2241f4a04538f276a5a71b1f0e9506c7750164b846133229afdbf0420; fourcandidateworkershealthy, allnewcontrollers/auditsgone, old2715788T/hourlyPAUSED,410sharedWIPunchanged. No further task submissions, research, schedule or code changes for this goal.

### X third-issue current native diagnosis, 2026-10-06
Goal remains ACTIVE: far strong strips and mixed returns. Prior status-only turn was no algorithm progress; this turn freshly read current105 worker a108/fa healthy and exported6 current normal/source-bound cuts (3868:7/8/10,7855:5,two priorcontrolcut0) under L/B far-mixed-native-v1. RAW/azimuth/range exact plus task/fp/network/native/evidence/NPZ SHA checked; private consumer verifies6 and committed frozen HEAD5e dependency execution bytes; no extra checkout/shared WIP changed. New readonly exporter controller completed/exited, no task submissions/restarts/deploy/schedule/thresholds. Current fullcut visible5/strong35:3868c7 1676/95,c8 3435/34,c10 810/52;7855c5 2330/1038;controls407 51916/792,3a1a49063/686. c7 strong mainlynear, notfarcontaminationcount. Actual7855 original/QC/SNR diagnosticplot viewed: two farstrong components389/378;845currentstrong gates overlapSNR excess. Frozen complete originalSNR geometry/memberdiagnosis:7855 strongexcess outsideSNRcompact190/outsidebothcompact88;3868c8 strongexcess19 allDBZHcompact; controls0currentvisiblecarriertargets, notweathertruth.
Decisive heldout angular-amplitude probe rules out simple whole-carrier common-gain explanation:7855 24target row/5km blocks globalpowerresidual.0032–.0725 despite targetrowenhancement~19–40dB and otherrowgain~1; totallinear-powerfit dominatedstrongsourcebody. 3868c8 six tests residual.48–.58; c10four separatetests. No classification/action fromthese fits; per-member explanation required, no jointMSE shortcut/veto removal/thresholdtuning. Nativegeometry completebeforecounterexamples/protection remainsneeded; current snr_carriers.review p90 distancelocal_excess/unknown veto intentionalcontract, missing2D mixeddecision notfailedtask/cache/budget. S publiccatalogfresh sameUTCearlyday fourstations present butgeometryunverified; possibleengineeringcounterevidence, notabsence/deletiontruth. Newowneduntracked docs/XQC_FAR_MIXED_DIAGNOSIS_20261006.md freezes findings, next originallocalcomponent source/weather/mixed/unknown competition and regression obligations; allhelpers/reportSHA underL/B. No businesscode/commit/push/publication changes; hourlyPAUSED/old2715788NEVERresume/candidateonly/703801excluded remain. Fullgoal NOTcomplete; next implement/test actualgeneric2Dmember explanation with weather/compact counterexamples before normalpublication, notmoreglobalfit-only statistics.


X third-issue continuation: previous status-only turn NO_PROGRESS; new offline falsification changes next action. Relative-innovation v2's402visible/150strong is NOT cleaning.12 constructed controls with physically range-declining weather SNR reject prototype: fixed-km weather wrongly nominated160/161 at1deg178/359 and480/483 at.5deg53; fixed-angle positives480/483 at1deg but0at.5/.25. No production adoption/threshold changes. Report SHA bec2067b3f5dc44c1573520d115135d0c25f110544f8101f14788c5329c70a55. Native measured boundary LP v2: level6 11full original components,10 both physical-strip/angular models feasible (116candidate overlap),589gate/18.975km component overlap256 neither simplemodel fits;30candidate below6 unexamined, levels not additive. Infeasible NOTRFtruth; positivefeasibility onlycountermodel. LPv1failed synthetic opposite-bearing construction; corrected positive longitudinal domainv2 controlPASS/reportSHA11021653ae09f3c363af2f762e2a7f38c934ec70cc6fdb06e31b2bcce0f52585. Successful probe/dependencySHA verified against current5e2main; private localverification + owneduntracked diagnosisdoc updated. Prepared unlaunched S contextv2 (manifestlogical/hostfrozenpubliccatalog/exact4/3beam inverse16cases error5.82e-11m/actualMinIOvolume/cidscopedcleanup),pycompilePASS, notrealSvalidation. SSH105 timedout;scutil onlyen0 192.168.1.9, asyncrequestednetworkrestore. No businesscode/commit/push/deploy/task/service/schedule writes;411sharedstatusentries preserved. Goal ACTIVE, candidateonly, hourlyPAUSED,old2715788NEVERresume. Next acquirepositiveco-spatial/time/height Scontext afterconnectivity, preserveunknown/mixed semantics; no blanketmother-carrier deletion or repeatedpersistencemask.


X third-issue continuation PROGRESS:105 keySSH recovered14:07Z;4normalworkershealthya108same. ReadonlyS contextV2PID396041 thenV3PID441404 terminal POSITIVE_S_ENGINEERING_CONTEXT_MEASURED/EXITED/noownedcontainers.10 S normalized+QC receipts/866 currentXfarstrong points, frozen catalogSHAe45aee32...;V3 producerasserted fullSRAW/coords/time exact, localconsumer verified all10NPZ+X866 source/RAW/QC/time/row/gate IDs, NOTindependentSrecomputation. V3reportSHAa983140b19acc89fa74c57ca47109e7c1bf81f2ca6985d9de034e62629916b9b; localverificationSHAeaf3db423d1aafaf3e7d3a41546f5dd7acd979608abf0aebf2281a9c03193f10. ActualMinIO483410530304B/97763248inodes,guardsunchanged. Atdx<=5km/dt<=300s,height1/3/5km uniqueSraw5=0/463/866,raw35=0/175/565; eligiblecandidate5=0/10/12,eligible35=0/0/0; Sradialreject0/453/864. Keycounterevidence: S862cc72d cut2's864 finite DBZH_QC values stillACTION2/QI0/FLAGS49160=RADIAL8|LOWQUALITY16384|NONMET32768. FiniteSRAW/QCisNOTweather support; Sreject isNOTXgroundtruth. AllMSL/operationalweathertruthunverified. TwoXtargetcomponentsA389/B378:1km0positive;5kmallSradialrejected, A10weakcandidate/B0;3kmB370RF/A10weak. Consumerfirstwrongv3catalogpath failure retained, correctedcheckingactualfrozenv2catalogSHAPASS; no producer rerun. New source-bound sharedpalette nativepointfull/zoom A/B figure viewed, pointspacingNOTunmeasuredweatherstructure. Requestedhuman/independentlabels for specificA/B (notpriorZF701southernlowcut); awaiting, no fabricatedlabels. Owneduntracked diagnosisdoc+memoryupdated, other411sharedstatusentriespreserved. No businesscode/commit/push/deploy/normaltasks/service/schedule change. Goal ACTIVE/thirdissue NOTfixed/hourlyPAUSED/old2715788NEVERresume. Next use real labels/independentpositiveweather and fullparent 2Dmember models; noSabsence deletion/blanketgain/compact-veto override.


X third-issue blocked audit passed3 consecutive goal turns: after genuine S-source investigation, two further fresh audits show no A/B class labels/independent same-volume truth and no safe tested discriminator. New morphology extension still fails3 weather controls;10 frozenSsource/QC references equal freshpubliccatalog;4Sgeometryunverified;accepted/trustedSstrongsupport0at1/3/5km. Scontext396041/441404 bothterminal PIDabsent/ownedcontainersnone,notwaitingjobs. main5e2/noXmodulechanges; no new product/deploy/schedule/threshold/job mutation. Private far-mixed-blocker-audit.json count3/thresholdmet; update_goal blocked follows this handoff (notcomplete/notuserpause). Existingpendingrequest asksA389/B378 labels withbasis; alternatively verified independent observations/source-format information needed forvalidweather-loss discrimination. Resume uponhuman/externalinput andstartfreshblocked audit,notcarryingcount3. No repeatedrerunsforunchangedevidence; hourlyPAUSED/old2715788NEVERresume/RAWandsharedWIPpreserved.
### 08:12 S marine completeness — proven ray-age repair, normal recomputation queued, 2026-10-07

User supplied08:18 marine reference; primary08:12 hole traced to Z9593 admitted low rays age628.614s exceeding600s despite volume age320s/QCFlags0/QI1/CReligible1. Previous QC-only recompute did not fix this main hole; unknown/withheld samples are separate. Actual network hadZ9591 age720 andother3S600, allcadence360. Added actual-runtime snapshot configs/multiband/fujian-full-s-age720-20261007.json SHAbaa31f...: threeS→720/releaseid only, allQC/X/grid/execution unchanged. Six new regressions actualoldRED4fail2pass/newGREEN6pass;10relatedPASS. Sameimmutable4S/image readonly compute50987→66628 cells/+15641 allZ9593 ages600–720/nooriginalfinite loss/noexistinglowering;8restored native checksPASS, SfieldSHA3dba4929fa296fee0aa939db1e32abd705183987b4c5f51fa43e930d9bd78344. Three-panel marine comparison viewed;08:18 isSreference withdifferent selection, nofuturecopy/weathertruth claim. QCunknown/hardrejection intact.

105control network path .build/sx-priority1/fujian-full-experimental-20260828.json atomically replaced; oldbytes retained privately andexistingcontainer mountoldinode preserved. managedNetwork reads perplan; noGo/othercontrollerrestart. Ownnew candidate b551323fc73f/image6f1caf.../fp2d3e7ae8... ready; hostalias omission infirstfailedcontainer recorded/stopped, thenfixed. Channelmultiband now2d3e7ae8.../revision34. Firstpreflight oldnetworkBLOCK/notqueued; afterconfigsync normalplanb9de6f73-5908-47d2-b2a7-1f273e522659/taska503c57d-222b-4610-94ba-bb73dcb5f16a/run18d11eb2-2699-4679-928b-ed36904cba86 accepted06:53:32Z, requested28/same4S assets/08:12only. Formalpublish/readback pending: do NOTclaimreadonly outputpublished. Evidence .build/s-composite-recompute-20261007/age720-v1/, docS_COMPOSITE_COMPLETENESS_20261007 currentconclusion supersedesoldhole diagnosis. Pointsource-table compatibility unresolved; noGochanges. FinishthisrepairthenpauseM1–M4; no commit/push/newcheckout/otherScontrollerchanges.
### 08:12 S marine completeness repair — published and verified, 2026-10-07 15:43 CST

Closed primary east/NE marine map hole via threeS600→720s config alignment withexistingZ9591; same4immutable S QC assets/flags/QI/admission, samecompute image6f1caf.../code509f5..., Xconfig unchanged. Normal28requested/25actual(4S+21X) taska503c57d-222b-4610-94ba-bb73dcb5f16a/run18d11eb2-2699-4679-928b-ed36904cba86/attemptf18df3b2-9ef6-4df5-8096-ffb4181f3f4f SUCCEEDED07:38:18Z/runtime1901010ms. Formalfull Sarray66628/SHA3dba4929fa296fee0aa939db1e32abd705183987b4c5f51fa43e930d9bd78344 exactlymatchesreadonly/+15641 allqualifiedZ9593 ages600–720/nooldfinite loss/nolowering. All4nativewinning samplespass; prior8restored admission checkspass. Xfullarrayunchanged/13217, joint70379. Nineactuallydeclaredmap object/publicHTTP hashchecksPASS; initialhelper requestedundeclaredx_minus_sPNG andfailed, correcteddeclaration-drivenv2keepsfullnumericchecks. PublicSimageSHA84c131b889af8b1d0eda49e4a4027d9180611ed14696a8a11ebd5f39842c8e75 exactlysameasreadonlyPNG. AssetSHA960fb16de5487bb784f7fe2a99d01db4b5dbb8c42391f0e0179510cc211146b1.

LatestWebseries73f5f6765da32e200be425049e793cf2c5a2f53fee5effc11f414502d22a8b04; publicdaydirectoryselectsnewseries, explicit0812linkin docs/S_COMPOSITE_COMPLETENESS_20261007.md. Oldpinnedlinksretainoldnumbers; nohistorical overwrite/future-scan borrowing/adjacent backfill. Comparison-v2PNG numericallyredrawn/viewed/reproscript privateage720-v1 evidence. Configbaa31f... activeincontrolatomicfile andownworker; noGo restart/binary change. Ownoldaff1anddiagnosticb551 stopped/preserved, DNSfailedcontainerretained; ownnewnormalworker d22c82a8ef25/imageunchanged/registeredops-multiband-d22c82a8ef25-56ef15dc3533 READYidle/restartunlessstopped. SharedNATS pending0/ackpending0 afterexactsame-refoperatorredelivery; noSQLtask-state edits/duplicate execution. Other4MBworkers andScontrollers2315154/3393155 preserved.

Sixnew testsactualoldRED4fail2pass/newGREEN6pass;10relatedPASS/diffcheck. Onlyfullhorizontal0812 accepted: sharednetworkalsohasqualityv2(ageweightnormalizationchanges), notaccepted/promoted undernewwindow. Mainmap repairclosed, broaderM1–M4 remainsPAUSED; no newjobs/commit/push/checkout. Knownpublicecho pointprobe remainsunavailable duevolume-source-table versusGoindexed-sweep binding; rawarray/PNGcorrect, noGo fixclaim. CUAgetState itself10stimeout; no actualnewpage/browserbutton acceptance claim. Source unknown/hardreject/QPE/training unchanged; futureS-QC ownership/weathertruth unresolved separately. Evidence .build/s-composite-recompute-20261007/age720-v1/{formal-publication-verification-v2.json,public-publication-verification-v2.json,published-task-v2.json}; avoid anotherrecompute forpointbinding issue.

### S Z9591 14:06 source calibration correction, 2026-10-07

Exact scan78d331fa-049b-5f90-a3b4-2b9051d84831/cut0. Original Web QC Sept22 params23d87; priority v8 normal4scans/1slot DONE with publication auditorVERIFIED. v8 south0–100km retained5238, none60–100km: BWS_REASON275/near target conflict, original fan tracking cannot act because sourcecandidate absent. Installed broad-source shared_range_term pooled ray offsets; near term unsupported and zero fallback, ~−2.7dB target mismatch. Existing local paired_range helper fits per-ray offsets/sharedslope with heldout50km blocks/strict everyrayP90≤.5. Actual installed old test slope.008933 vs.011 RED; new8PASS. Readonly actualv8 source replayexact thencalibration-only +5107sourcecandidates/+3792southnear/0withdrawn/0independentweather-conflictoverlap/RAWunchanged; SOURCESTAGEONLY, notnormalfinalQC/weathertruth.

Scoped v9 image prepared from exactacceptedv8 broad_sourceSHA12d840; replaces ONLY shared_range_term +paired_range helper, otherASTequal. Excludes allotherdirty broad_source weatherproxy-policy/NMR-memory/shape WIP. Image candidate983e82aa..., profile274adf0c... onlyprofileversion changed, flags/pipeline7.3.9 unchanged. Actual candidate8paired+15independent frozenHEAD broad testsPASS (fixture-dependentstage test notruninimage). Installer3382179 launched; stops only own frozenv8 controller2315154 andauditor3393155, preservescheckpoint andnaturallydrainsoldjobs beforeS-onlyatomicdeployment+rollback. New frozen684/106 batch planned14:06first with existingcontroller/auditor, OUTPUT s-all-paired-range-20261007. Atthisentry DRAINING_OLD_SUBMISSIONS; no deployment/newWebclaimyet. See docs/S_Z9591_1406_RANGE_CALIBRATION_20261007.md and private s-current-refresh-20261007/fan-1406 receipts. Preserve otherMBworkers/age720repair/sharedWIP; no commit/push done.

14:06 correction deployment/start confirmed08:39:18Z: v9image e1c6652690e4a916364180bc6f9d739ad253b73ba3ee9fe552d339a265d77450, twoShealthy/livebroad+pairedhashchecked, other4MBcontainerIDsunchanged/oldjobsdrained. Newcontroller3397223/auditor3397224 alive; frozenplan56611304f7b503744e9258f9e1480037d82842ae8f93b9bbea16301fadc7c98c(684scans/106slots)14:06first. Actualfirst QCjob1a882b77-4c58-5dc9-8a05-30d5b1817b94 RUNNING/rebuild1a8a98bb-fe6c-4628-8886-46350a41d125; no slotpublishedyet. Installer3382179 exitedsuccessafterlaunch. Localmirroredinstalled/transition/startup/livehashreceipts; onlystartupverified, finalWebQCeffectpending. Userauthorizedbackgroundcompute/exitonceconfirmed; leavecontroller/observerrunning, do notrestartv8controllers orpretendWebupdatedalready. No thresholdrelax/noindependenttruth/otherWIPcommit/push.

14:06 v9 actual normal QC succeeded08:42:34Z(~200s). Selectedcut snapshot partialverifiedartifactreader retainscompleteindexsha; normal result beforeWeb publication, notalgorithmprojection. SameRAW/az/range/nativepermutation/raytime/elev exact v8-v9; +4990withheld visible/0restored/0oldweather-conflict-barredoverlap. South160-200/r<100km5238->1559;0-15km690->547,15-30km1949->920,30-60km2599->92,60-100km0->0. Viewedactualnormal3panel/fullzoom PNG: main30-60kmfan largelyremoved, near0-30kmorange patch/thinredtail remains; NOTcompletefan removal/weathertruth. RemainingallBWS_REASON275/noBWSsourcecandidate,355NPRAWproxyprotected of1559. Reusable scripts/plot_s_qc_generation_comparison.py added; actualcomparison+3identity/tamperfailclosedchecks/RuffPASS. Evidence fan-1406/{v9-normal-input,normal-v8-v9-comparison.json,z9591-1406-normal-v8-v9.png,v9-retained-south-diagnosis.json}. Freshv9controller3397223/observer3397224live,3scans complete/4thQC5a601runningat08:50Z,0slotpublished. PreviousgoalturnPROGRESS(deployment/normalcalibrationcompletedverified), full684/106goalactive. No threshold/RAWweatheroverride/blanketsector/productiontruthclaim/commit/push.

14:06 v9 normal publication accepted: allfourS completed/QPE+diagnostics638c7851-ca17-5dd5-934b-573cd3696c65. Exactusercycle Web API now binds same completedv9 QC URI1a8a98bb/1a882b77, RAW/QC publicPNGCRC+decode/SHA verified640x640; QCac81b6ce..., RAWe44ffa95..., actualbrowserrenderNOTverified. Private fan-1406/v9-Web-publication-proof.json+frames. Normalphase allslotsstillincomplete, visualresidual0-30km/tail retained; noallmeteoacceptanceclaim.

Received peer Z9595 dependency coordination request. Authoritativereadonlymanifest420causal4S native scans,32notinlegacyWebslotset(allincluded684inventory). Added optionalcausal prerequisite scheduling to refresh_s_all_current.py, maximum720s/basedvolumeEND/notfutureorotherstationborrow/tiesfailclosed/frozenmembershiprederived. OriginallegacyWebframebindingsNOTchanged orclaimedcausal. Four new regressions actualRED4+old8PASS thenGREEN12PASS/RuffPASS. Newfrozenplan exactly684same nativeIDs/106samelegacygroups/420causal requirements matchedpeerpending417subset;08:06causalempty. Freshhandover preserves18jobhandles/7completedscans/currentinflight; nojobcancellation/service/image/profile/poolchange. Own3397223/3397224STOPPEDnotrestart; newcontroller3604219/observer3604220actualLIVE on '.build/s-all-paired-range-causal-20261007', plan1fd94217e499dcadd9eb15b9759b316334844f1d934d26d14da1c000851e50d2/source8c13cd1f..., currentQCcf5f9030(priority14causalpredecessor),7native/1slot bothauditpass. Existing14:06publicationreceipt retained. Profile274adf0c/params0dc9d89/imagee1c665 unchanged. Oldfrozenplan/statekept+superseded-by-causal-scheduling sidecar, newparent-checkpointvalid. Neverrestartoldv8controller2315154 or oldv9controller3397223. OtherZ9595controller3439979 notchanged;3439980observedexitedbeforehandover, noaction/restart/message sent. No peer messagingauthorization inferredfromreceivedrequest. No commit/push/extra sourcecheckout. Fullgoalactive684/106latestrefreshongoing; userpermitsbackground/exitonceconfirmed.


### Z9595 five-S existing-slot backfill, 2026-10-07 09:20Z
User confirmed ONLY existing Beijing2026-08-28 slots:106 six-minute08:06–18:36,110 completedZ9595volumes; UTCwhole251/minutelegacy excluded. New ownheader YAML configs/radars/fujian-20260828/z9595.yaml (118.4977798461914E/24.89555549621582N,antenna530m,2785MHz,11cuts;datum/calibrationUNKNOWN), network fujian-five-s-age720-20261007.json SHA d5f874bc... addsONLYZ9595 to priorage720snapshot;29requested=5S+24X. Draft/experimentalCRonly/noheight/QPEpromotion. Initial10decodejobs failedactualWorkerfull-radars mountmissingYAML; configmountedboth, normalrebuildreceipts retained;110normalized. Firstcompletevolumeend00:10:00.101929Z, neverusefor08:06.
ConcurrentQCv8->v9 imagee1c665 automatically stoppedownoldcontrollers withoutmixedjobs. Current scopescope-v3.json SHA c7524eb49abc91361c7ef2a2ffa876cdb3d3670383340bec611e2ca7ad9fb1b4,profile274adf0c...,params0dc9d89cefb6630a4f3533655bbb0c02e3789732139601cffee06d11f52beff2. FirstZ95v9nativeQCverifiedRAWnormalizedheadergeometry/cut0shape366x1840/12434CReligible/148666VALID/assetf49d9eb3...,notweathertruth. Latest10/110QC; ingestcontroller3439979LIVEoneownQCinflight/sharedtwoQCworkers. Fusioncontroller3601550LIVE (supersedesown3439980/3580699,handlespreserved) frozenfp254907c7.../MBimage6f1caf...; sixownrainpulse-ops-five-s-20261007-{1..6}2CPU/4GiBeach. NormalGoCLI/APIjobs/publication only/noSQLmutation/defaultQPE. CompositecurrentlyWAITING_CAPACITY/0of106v9: sharedQCworkers~43GiBeach/systemMemAvailable11.6GiB/noswap. Submitguardsreserve4GiBforeachqueued/active+8GiBheadroom, actualconcurrency<=6; capacityregressionREDthenGREEN/finalowned13PASS/Ruff/diffPASS. Do notrestartsharedQCworkersinflight.
Userexplicitlyauthorizedpeercoordination with“研究雷达质控算法优化”01a06fc2-43c8-7350-8d04-36584f40894a; sentdependency+memoryreports. Ownerkept14:06priority thenprioritized32postponedcausalrequirements. Independentold/newplancomparePASS sameQCidentity/684scans/106groups,420causalrequirements; new1fd94217...at .build/s-all-paired-range-causal-20261007 (controller3604219/observer3604220); old3397223/3397224stoppedbyowner. Ownscope retainsoriginalupstreamhashforlineage/runtimeimagesidentical; coordinationreceiptupstream-causal-coordination-v1.json. No duplicateoriginalfourS QC.
HistoricalfiveS08:12v8task8b3be1a4...attempt62e3a34d... normalSUCCEEDED09:08Z,~32min,asset2b261345...; independentarrays/nativepoints/nohardreject/nofuture/9internal+publicPNGhashPASS. Frozenorig4S/Xsame,addZ95: S66628->69731,+3103finite/+1015strengthened/0lost/0lowered;XarrayEXACT. Z95jointwinning3743(notweathertruth/rainarea). Legacyseries471c0ecf...fixedoneframe,NOTv9acceptance/NOT106completed. Comparehelperv1badmanifestkeygrid retainedtooltrace;v2usesactualgrid_id+bounds andPASS,noalgorithmchange. Allprivateevidence .build/z9595-20261007/ bothhosts.
Finiteone-shotreadonlycompletionauditor3656051LIVE WAITING_EXISTING_BATCH; waitsQC/compDONE then110QCheaders/params/assetSHA+106normaltask/verifiedreceipt audit, onlyfullPASSwritescompletion-v9.json;error/48hwaitfailclosed. Thisisnotfullcompletionyet. Docs/Z9595_接入与已有时次补算_20261007.md recordssteps/status/limits. ExistingSsingleUIanalysispanelsstillonlyoriginal4; Z95catalog+nativeQC+fusionentry added, DO NOTclaimZ95singleWebpanelsalreadyenabled/trustedQPE. No commit/push/branchswitch/newcheckout/allWIPbuild/sharedS/X/Webchanges.


S 14:06 follow-up 2026-10-07 09:25Z: current causal v9 batch3604219/auditor3604220 LIVE,11native/1slot completed; same e1c665 image. worker2 RestartCount1/Started09:18:39, causeUNPROVEN(eventsnohistoricalentry;OOMKilledfalseafterrestart). InterruptedZ95d95f19 job automaticallyredeliveredattempt2 andcompleted09:24:31; no duplicatehuman submission/SQLmutation/forcedrestart. FreshMemAvailable23709644KiB/noSwap; workersprivateanon~42.7/31.5GiB, assetcache0, leakvsallocatornotdetermined. Preservefrozenrecipe/inflightjobs/peercontrollers. Actualv9south1559remainingallBWSreason275/source-match0;BWSresidualmedian.05857/P9013.3882, center180.01deg representativedistancegatesalreadyinvisible, neighbouringnearreturns inconsistent. No newthreshold/sectorblanket override/imagechange/commit. 14:06 normalWebproof and70percent reduction remains; notfullfan/weathertruth acceptance. Background684/106 stillactive, do notresumeoldcontrollers.

Z9595 freshhandoff09:25Z: allthreeownhandles LIVE_MATCH/noERROR;QC11/110,composites0/106 WAITING_S_QC,MemAvailable23731972KiB(~22.6GiB),memoryfloorcurrentlycleared. completionauditorWAITING_EXISTING_BATCH. ReadinessSQLlatestpersistedspec is earlier v8reference, NOTauthoritativecurrentpreflight when mixed-identity API409 rejects; helper report onlyoldspec,donotcallallfivecurrentQCv8. Formalv9notpublishedyet.

S 14:06 strict-target audit continuation: previous turn PROGRESS(resource restart/recovery proof); new readonly predicate replay uses exact released broadSHA d5d603 plus actualinstalled target3be9cf/distance934781 capturedfunctions, pairedhelper a051 andprofile274adf matched; allnormaltargetmask/residual EXACT. LocalinitialhelperAST identity checkfailed; capturedactualfunctions andreran, no relianceonlocaldirtyfunctionidentity. 1559 remainingallreferenceavailable; overlappingfails range343/SNR616/phase1308/ZDR966/rho1279.40associateddistancepolar rows19inconsistent/3insufficient/18consistent. Evidence fan-1406/v9-target-failure-audit-with-donors.json bound actualNPZ/profile/release/helper/capturedfunctions/runner SHAs. DownstreamactionsNOTexecuted/productwritesfalse/notweathertruth. Remote3GiB diagnostic launchguard refusedlowMemAvailable before Popen; no container/controller was launched orrestarted; localRAM replay used instead. Finitev9 calibration improvement verified; furtherdistance-threshold widening hasno evidence tosolveprimarynearpolarconflicts. Prioritise completing684/106sameversionbackground refresh ratherthanaddingunvalidated endlessbranches. Latest observed14native/2slots RUNNING; no fullcompletion claim. No prodprofile/image/action/code changes this turn.

S paired-range source shipment: main/origin bc46610b2076d5a752daffd3543c37a96dd38f4c exact readback. Sevenownedpaths only; broad_source partialindex replaces ONLYshared_range_term, wholecommittedfileSHA d5d603 equalsinstalledrelease; pairedhelper a051 unchanged. v9profile differsfromtrackedv8onlyprofile_version. Contractstageonlyacceptedcalibration23lines; unacceptedRAWweatheroverride workingtext remainsunstaged. Plothelper/doc/8tests included, 547otherfilebytespreserved, noextra checkout/sourcecopies/reset/stash. Fresh8PASS/RuffPASS, old5e HEAD .00893297 vs known.011/newcommit.011 regressionproof; firstmanualfunctionprobe missedwarnings global(setupfailure), correctedandreread actualold/new. Gitcommit initiallycompletedafterthatseparateshellprobeerror; no pushuntilcorrectedregression/scopechecksPASS. CI37604773370 in_progress/notglobalgreen. 105actualbroad/pairedmodulehashesequalcommit; image/profile/currentbatchnotchanged orrestarted. Live3604219/3604220,21native/3Webslots auditRUNNING(latestincludes08:18),684/106notcomplete. Fullsource/audit receipts .build/s-paired-range-20261007-v9-release/committed-pushed.json.

S batch follow-up 2026-10-07 10:37Z: controller3604219/auditor3604220 exact live,27/684 native and4/106 Web slots verified including08:30. worker1 auto RestartCount1/Started10:31:40Z while job83492f5d first delivery10:29:14; same job automatically redelivered attempt2 at10:35:54, currently computing. Both S workers healthy/imagee1 unchanged, MemAvailable~51GiB/noSwap afterrestart. CauseUNPROVEN, current OOMKilledfalse is not historical cause proof. No human restart/duplicate submit/profile/image/queue/SQL mutation; do not restart batch or treat interrupted RUNNING job as terminal. Private worker-1-auto-redelivery.json; fullrefresh still incomplete. Previous goal turns were verified waits on live exact process handles.

S interrupted-job recovery confirmed10:39Z: same83492f5d attempt2 emittedjob.completed duration244128ms and controlleradvancedto grid:d12ac95a; actual traceparams0dc9d89/profile274adf consistent. Countstill27native/4slots untilgridpublicationaudit. No human resubmission/restart/versionchange. Private worker-1-auto-redelivery.json amended; previousgoalturnPROGRESS(newrestart andautomatic recoveryevidence), fullrefreshactive.

Z9595 single-site RAW/QC follow-up 2026-10-07 11:00Z: user explicitly requested station single-view images. Rootcause native QC registered/fusion available but original-four analysis diagnostic requests exclude Z9595; static radarSites missing own station. Added normal Go cmd/radar-diagnostics-supplement (BDP runtime/releaseguard/transaction/outbox; no fabricated READY/DB state mutation), separate internal SupplementalRadarInputs/SupplementalRevisionID, all original contributors preserved and verified. Store admits supplemental current native S QC only when ended<=analysis_time and age<=720s; normal event radar_inputs includes all, existing renderer restricts CR to RadarAnalysis.source_codes. No promotion of unknown height datum/calibration/QPE. CLI scoped build imports normal unchanged store/orchestration, no all-WIP Go service deployment or restart. Contracts/data/diagnostic-station-supplement.md; own Go tests, Python supplement regression, 3 finite-runner tests, UI22PASS, whole Go module tests/vet/buildPASS, Ruff newfiles+diffPASS. Earlier Ruff scan of preexisting test_diagnostics raised existing E501/E741; new regression moved to owned test_diagnostic_supplement.py, original file restored; do not claim baseline lint fixed.
Actual08:12 supplement job f23fb8f4-86f8-51a4-bbeb-4efd4329a5cc analysis eaf09998-f436-5a2d-9bb4-2212f8b8387b normalSUCCEEDED10:54Z. Native scan a4869f6f.../QCsha f49d9eb.../params0dc9d89...; 11 cuts contain9 finiteDBZH (0,2,4..10),36 published layers. Public API/workspace SAME scan/sweep/params/content SHA for every RAW/QC pair; first PNG hashes and alphaPASS RAW27799/QC21993pixels; actualPNGvisualreviewPASS, CUA IAB timedout, browser click-flowNOTverified. User opens ?preset=qc&band=S&mode=single&date=2026-08-28&time=2026-08-28T00%3A12%3A00.000Z&station=z9595 (omit old series/cycle if any). 08:06 legitimately has no completed station volume (firstends08:10); nofuturebackfill. static stationQuanzhou own118.4977798461914/24.89555549621582/ground444/antenna530/frequency2785/cadence344/range460. Web baseline .build/s-composite-recompute-20261007/web-build-v4 allruntime sourcehashes equal except ownedradarSites; atomic distrelease10:52Z previndex1a80af45 -> b54394d1..., retains30oldassets/PID1149670/oldsourcebytes, no fullunacceptedWIPdeploy. Receipt .build/z9595-20261007/web-release-single-v1.json on105.
Finite serial single supplement runner scripts/backfill_z9595_single.py installed105 .build/z9595-20261007/, PID175345 LIVE_MATCH, scriptSHA3007d6b33e37e51a298d3d35017d897716420ab3c12bda7a9acad09e565d2375. State single-state-v1.json WAITING_JOB; visible1/106(08:12), unavailable1(08:06); othersWAIT v9 native QC/original4sameversiondiagnostics/emptydiagqueue/MemAvailable6GiB. Reusesexisting20GiBdiagworker toaddsingleincrement; doesn'trelaxfusion8GiB+4GiBreservations. Ownjob idempotent revision includes current base diagnostics job/QC/analysis; if upstream4 republisheslater needsnew supplement; no activefour jobs blocked/modified. Completionrequires bothQC+upstream DONE and verified/unavailableall106; finite48hFAILclosed, not fullcompletion claim. Other ownQC/fusion/audit handles preserved; lastQC41/110 andfusion1/106 shouldfreshcheck before reporting. Disk633Gfree. Userauthorized crosschat coordination message sent to研究雷达质控算法优化 01a06fc2... aboutsupplement; no new threads/subagents/worktrees/clones/commits/push/branchswitch/reset/stash. Docs/Z9595_接入与已有时次补算_20261007.md updated19:00CST actualproof/currentlimits.
Z9595 supplemental finalhandoff: runner175345 autonomously submitted08:18 jobcaf658c1-4ece-5496-ab20-e003ab8a6bfc after recognizing08:12publicpair; noERROR/LIVE_MATCH. NativeQC41/110, v9fusion1/106+3active WAITING_CAPACITY atlatestcheck. Currentstatus keptseparatefrom18:54firstsiteproof. Doc sync attempt initially failed remote docs dir permissions/no existing same-namefile; canonicallocaldoc andprivate105z9595-document-current.md preserved; no partialserverdocclaims beforeinstallverify.
Z9595 document delivery limitation: sudo -n install denied because deployment docs directory requires a password; no shared docs written. Canonical document is updated in sole localcheckout and identical private105 .build/z9595-20261007/z9595-document-current.md. This does not affect published native single-site images, Go helper, Web release, or running finite backfill. Do not store SSH/sudo credentials in source or evidence scripts.
Z9595 finalfreshsingle snapshot: PID175345 LIVE_MATCH/noERROR; visible_frames2 (08:12 and08:18),unavailable1(08:06),completed_frames3 accounting legitimateabsence. 08:30 normaljob d0a24040-6e3c-5590-bba2-c8751688c1d8 inprogress. Updateddoc local and105privateevidence; no full106done claim. API-backed singleviewlink omitsfixedlegacyseries. Usercanrefreshoropenlink tosee RAW/QC and9DBZHangles now.

S supplement-aware publication observer 2026-10-07 11:16Z: previous3604220 EXITED terminal generation-mismatch after authorized Z9595 single-image supplement; original recompute3604219 stayed LIVE/no restart/duplicate jobs. New frozen readonly observer290947 LIVE, scriptSHA682c94c7760792700f77d2755318381cb60d831b128ae2a9c9d4856425f7fb10 at105 .build/s-supplement-aware-publication-20261007/audit_s_refresh_publication.py, outputpublication-audit.json; original failed report retained. Explicitz9595allowlist only, sameanalysis/render/QPE/4contributors, causalcurrentS QC<=720s/profile274adf, everyoriginalRAW/QC/CR/QPE PNG exactbyte-equal plus actualextraPNG structure/hash checks; no changedpixels/mixedgeneration bypass. Actualonceprobe+liveRUNNING34/684native7/106slots,3supplemented. Fresh33testsPASS/RuffPASS, newobserveronly/noQCrecipe/image/profile/controllerchange. Fullrefresh NOTcomplete/weathertruthNOTverified. Local helper+two tests stilluntracked; committed algorithmbc46610 alreadydeployed. No crosschatmessage sent fromthischat; async humanpermission pending, priorpeer-to-root authorizationnotassumedroot-to-peer.

S publication observer shipment: main/origin032e792ec6f828afae15e785568bd6a763b9939c exactremote readback; onlyfourowned auditor/2tests/contract files,558otherbytes preservedbefore memoryappend, no newcheckout/branchswitch/reset/stash. SourceSHA682c94 equalsdeployed290947, finitecontroller3604219sameLIVE/no versionchange. Latest36/684native7/106slotsRUNNING11:22:59Z; notcompletion. Scoped33tests+Ruffpass fromprevious immediateverification, contract supplement invariants retained. Private committed-pushed.json, CIinprogress/no globalgreenclaim.

S observer CI follow-through:032e792 CI37613665810 completedFAIL; parentbc46610 CI37604773370 completedFAIL. Freshfailedlog normalizedANSI comparison exactsame10 fusion performance-CD testfailure signatures and4899lint errors; ownauditor/2test paths absentfromfailedlogs. NotglobalCIgreen; scoped33PASS retained. Firstgh parentfailed-log download transientEOF, samehandle re-read succeeded, no restart/repush. Private .build/s-supplement-aware-publication-20261007/ci-failure-comparison.json plusfailedlogs. Source/deployedQC/image/profile unchanged, latestcontroller3604219 andobserver290947 LIVEverified; fullrefresh active, no blockedcondition.

S all-station closeout uncovered Z9595 single publisher stopped175345ERROR11:19Z, notwaiting. Rootcause qc_assets.get(uncomputedscan)==candidate.qc_uri comparedNone==None, so14:06 falselyeligible and normalGo correctlyrejected no causalQC. Otherpeerthread freshlyreadID01a0fd25 statusIDLE, rootserializedtargetedlocal untrackedscript changes onlyqc_ready(nonemptystr/exactasset), opt-inv2state/lock andfrozensourceSHA. Newowned6regressionsREDthen9combinedPASS/RuffnewtestPASS/pycompile; actual10514:06decodeduncomputedcandidate oldguardTRUE/newFALSE verified. No Go/QC/publisher profile/image/recipe changes. New frozenhelper .build/z9595-20261007/backfill_z9595_single_v2.py SHA9866ce797e852e72facd50c474235cd7bcfdb789f7931d699c807299f71085bc; newPID447626 resumed explicitv2 output. Originalv1sourceSHA3007d6/source/errorstate/log/handle preserved, old175345authoritativelyEXITED. New single-state-v2.json carriesall4submitted receipts/all5verifieddict entries andunavailable; no duplicatednormaljobs/statemutation/forcedworkerrestart. Source+newtests remainuncommitted(sharedpeerscriptdependencies notsweptintoowncommit). Nooutgoingpeerchatmessage; humanpermissionpending. Closuremustalsoverifynewstation110QC andall106single supplementalframes(orlegitimateabsence), notjustoriginal684/106; peerQC3439979, composite3601550, completion3656051 preservedLIVE.

S throughput maintenance 2026-10-07 11:48Z: actualtwoSworkersidleRSS42.74/42.8GiB,MemAvailable~7.3GiB, noSwap, peerZ95QC41/110 WAITING_QC_CAPACITY8GiBfloor (last0a4jobalreadySUCCEEDED10:46, notrunning/stuck). Firstreadonlyprobe sawnewQCactive1 so no restart; bounded one-shot532414 waited forglobalQCqueue0. It brieflyquiescedbothsubmitcontrollers3604219/3439979, recheckedQCpending/running0, recycledONLYidleworker2 samecontainer3b9f/imagee1 unchanged, waitedhealthy, resumedboth. TerminalDONE/exited; memavailable7886020608->53744185344B(~42.7GiBreleased), producersresumedtrue, frozenplan/profile/source/assetlineage unchanged/no activejobinterrupted. One-offv1helper/receipt frozen, do notrerunblindly; futureguardmustensuretimeout-path producerresume before anynewmaintenance. Private .build/s-idle-qc-memory-20261007/{maintenance-state,reclaim-result}.json mirroredchecked. All6existinghandlesfreshLIVE/notT; twoShealthy. PeerZ95nextnormalQC a831cabf RUNNING aftercapacityrecovery, composite4/106verified4active; singlev2visible7/unavailable1. Root41/684native9/106slots11:50Z. Notfullcompletion/no new algorithmversion/no CIgreenclaim.

S idle-recycle follow-up11:53Z: recoveredZ95normalQC a831cabf SUCCEEDED11:51:07, actualrequestprofile274adf matchescurrentprofilebytes; Z95QC42/110,next04041942 RUNNING, originals42/684native9/106normalWeb, singlevisible7, fiveSfusion4/106+4active. All6controller/observerhandlesfreshLIVE, Sworkershealthy samee1 image, controller/contractcommittedpaths clean main032e792. MemAvailable~17GiBafternewQC, filesystemfree367402233856B aboveexisting120GiBreserve; do notassumefuture fullbatchfits or delete referencedoldQC. Maintenanceone-shot532414terminal/exited andsourcefrozen, no repeatedrecycle/no image/recipe/profile/queue changes. Fullscope stillunfinished; thisturn verifiedrecovery+wait, notblock/complete.


### S+X frame selection fix, 2026-10-07 12:11Z
User authorized fix/optimization. Actual0818 df1b2e80 result:5S+20X,X15427finite/+4314S-missing/+4010stronger,joint exactfmax. Pinned one-frame series lacks selectedtime; S fallback falselylabelledXnotparticipating. Contract-first/4RED/fullWeb190PASS/final33PASS/build/newtestESLint/diffPASS. Automatic URL omitsseries:cacheddaywidest declarednetwork +exacttime/product/station-set latestseries lookup,no smaller or equal-count-different-scope substitutions,abort/invalidate staleframes. Fixed URLs remainimmutable; fixed-current action added/latest preserves time. Missing/error/loading separatefrom actual contribution; no oldframe undernewclock. Only3runtimeWebfiles differfromacceptedb54394 baseline;105staticrelease12:06Z index17fce2cc,33oldassets/PID1149670 preserved,no Go/Worker/QC/historicalassetchanges. Real Playwright oldURL misleadingtext reproduced thenSreference/auto0818S+X25inputs/auto0812S+X26inputs/pin0818coldnavigation sameID verified,screenshotviewed. Cold dropdown selection beforeReactloaded retried aftervisible;fullreload defaultsS. Historical produceridentity incomplete/run-isolated remains,not invented/merged;futureproducer rollout notdone. Current QC44/110,fusion4verified/106,single8accounted;all6knownhandleslive/noerrors,wholebatchunfinished. DocS_COMPOSITE_COMPLETENESS updated,evidence.build/sx-x-participation-20261007. No commit/push/restart/branchswitch/newcheckout/sharedWIPdeployment.

S finite resource guard 2026-10-07 12:13Z: originals45/684 natives10/106 Web slots, Z9595 44/110. Repeated lowmemory (~5GiB) with idleworker2 retained42.7GiB justified finite backgroundguard742153 at .build/s-idle-qc-memory-20261007/maintain_existing_s_refresh.py SHAa7301369852837fb6e834cc68ea7dafdd7907365cfef918a03cada47f177daa3. Seven decision/timeout-resume testsPASS in existing investigationvenv; system/miniconda lackedpytest, no appfailure. Actualreadonlyprobe activeQC1 so no recycle. Guard onlyqueue0/memory<8GiB/worker2RSS>24GiB/currentimagee1; frozen guardedhelper52ac5b, plan1fd942 unchanged. Childtimeout parentfinally resumesownedproducers; guard stops aftereitherQCproducerDONE or48h/500actions/error. Launch-only receipt initially observed; subsequent validation found guard ERROR (see correction below), no activejob interrupted/repeated submissions/profile/image/algorithm change. Finite maintenance support not full684/106 completion; othercontrollersremainLIVE. Currentgoalactive, no blockingcondition or fullweathertruth acceptance.

S finite guard correction12:17Z: initial742153 exitedERROR beforeworkerrestart becauseSIGSTOP asynchronous and immediateTassert raced; childreceipt provesworker_restartedfalse/producers_resumedtrue. Preservefailedsource/log/state/handle. New childreclaim_idle_qc_v2 SHA3ee42f814a6869144987314df3990f787e4bfc09d7cb89cd007c7e81fedc6de8 waits bounded5sforbothT thenrechecksQCqueue0; parenttimeoutfinallySIGCONTretained. New finiteguardv2 SHA7bf9612fac3132d3c08203c780dbbaf6739ebbde3c78197edc92be7a4b87a650 PID762840 actualLIVE/WAITING_EXISTING_BATCH verified;7testsPASS, actualreadonlyprobeactive1 skippedrecycle. Separatev2state/handle/log/actions preserveoldfailure. Bothsubmittersandpublication/singleobserversLIVE. No QC/image/profile/recipe change; fullbatchstillincomplete.

S finiteguard real action confirmed12:18Z: v2guard762840 LIVE; finite-v2-recycle-001.json actual emptyQCquiescence0, worker2samee1container recycledhealthy Started12:17:41, producers_resumedtrue. MemAvailable5544960000->51304497152B, fresh50024348KiB (~47.8GiB; actual output50124348KiB). Peerpreviouslycapacitywaiting now normalQC29534da8 RUNNING12:17:48, bothSworkershealthy/sameimage, allroot+peerproducer/auditor/singlehandlesLIVE. Recycledonlyidleworker/noactivejobinterrupt/newprofile; receiptmirroredlocally. Originals47/684/10of106 andpeer44/110stillunfinished. Prior goalturnPROGRESS(implementedfiniteguard); thisturnPROGRESS(actualcapacityrecovery+normaljobadvance), noblock/fullcompletionclaim.

S current-job delay12:35Z: prior goalturnverifiedwait on live original2ffd. Actualworker2 Started12:27:47/RestartCount1, samee1 healthy; firstattempt12:25:50 thenready12:27:49 andsamejobdelivery_attempt2 at12:32:30. Newfiniteguard onlyactionstarted12:17:41 withqueue0, not12:27restart. CauseUNPROVEN; OOMKilledfalse now/eventsringonlyrecenthealthchecks do notprovehistoricalcause. No humanresubmit/restart/imagechange. Actualsamejob RUNNING remains; private worker-2-auto-redelivery-1227.json preservestimestamps. Fullbatch49/684/11of106 unfinished; waitnormalrecovery ratherthanrestartlivecontrollers.

S same-job recovery proven12:37Z: worker2interrupted2ffd839b seconddelivery normalSUCCEEDED12:35:22, originalbatchadvanced50/684 and11/106, Z9595QC50/110. Allfiveexistingcontroller/observer/guardhandlesLIVE,noERROR. Actualrecoveryreceipt amendedworker-2-auto-redelivery-1227.json, no humanresubmit/restart/thresholds/versionchange. PreviousturnPROGRESS(new restart/redeliveryevidence), thisturnverifiedrecoveryandwait; fullrefreshunfinished/notblocked/notcomplete.


### Five-S finite handover, 2026-10-08 00:45 CST
Human explicitly requires five stations incl Z9595 and resource parallelism. Sole checkout; no new checkout/subagents/goal resume/automation. Confirmed old four native+downstream controller still active alongside existing five-S CR. Retired only submitter3604219, observer290947 and standalone single supplement447626 by SIGTERM; no DB cancellation, RAW deletion, worker algorithm/config/image change. Z9595 QC3439979 naturally DONE110/110. Old private controller/single states backed up and marked SUPERSEDED, receipts retained. Old memory guard762840 was already ERROR/exited, not restarted.
New local scripts/refresh_s_five_station.py + contract/test/docs own untracked paths, 22 scoped tests/RuffPASS. Installed SHA b570bfd1226b406c07eb6e525c322c699ea4d92bbcb7387f71f6a5a2d85e439e, frozen plan88415c09d73630b4b1188d00227b03d740bcabcfe020916405ad1914d634b333 at105 .build/s-five-station-pipeline-20261008. PID3714963 actualLIVE/RUNNING:794native=684+110/106slots,203currentQC inherited,92grids,0newverifiedframes at16:45Z. Normal QC f401e401(z9598) and09fc8336(z9599) concurrently RUNNING, bothworkers~100%CPU, samee1 image/profile274adf. Memory admission reserves growth to48GiB perworker +8GiBfloor; atmostQC2/grid4/product2/diagnostic1, queue0-only idle recycle sameimage (oneactual action); 72hfinite deadline/no activejobrestart. Source/config/plan frozen, batch job observations, UUIDpersist-before-submit, assetprefix/profile/QC->gridinput proof and public RAW/QC/cut/content/elevation/PNG representative checks.
Existing five-S CR controller3601550 preserved, sixexisting workers/max6,22/106 verified; public08:12 ACTUAL sources z9591,z9593,z9595,z9598,z9599 checked. No new X batch. New normal14:06 five-native diagnostic job RUNNING; publicold75b652 supplement alreadyfive, new generation NOTyetclaimed published. Original trusted QPE baseline keeps existing historical alignment/contributors (mayinclude futurecompletedvolume underprior +/-300s historical protocol), separate from causal five-S CR <=720s; doNOTcall it causalfiveQPE. Z9595 datum/calibrationUNKNOWN, native/CR only, notsilentlypromotedtrustedHybridScan/QPE. 08:06 no completedZ95 remainsmissing. Full794/106 NOTcomplete/weathertruthNOTverified. Further operator checks should use newstate +existingCRstate, not old fourobserver. Goal remains explicitlyPAUSED, one-shot automation s alreadyPAUSED; no scheduled followup recreated.

Five-S startup final16:47Z: actualRUNNING205/794native and1/106newverifiednormalframe;14:06 five-native diagnostic generation completed, all5RAW/QCsource/version/cut/content/elevation and10representative publicPNG downloads verified. ExistingCR22/106stillseparate. Receipt105startup-verified.json mirroredlocal; notall106done.

Hourly five-S monitoring explicitly authorized by human 2026-10-08: existing heartbeat automation s updated ACTIVE, renamed「S 五站重算与性能每小时巡检」, recurring hourly in the same root thread 01a06fc2-43c8-7350-8d04-36584f40894a. This supersedes prior one-shot monitoring only; continuous goal and the other-thread rainpulse-s-x automation remain PAUSED. Monitor 794 native volumes, 106 normal five-station diagnostic frames, and 106 causal five-S CR frames using actual processes, DB jobs, public generation and PNG checks. Safely repair orchestration, observation and resource admission while preserving active job handles, frozen QC version and RAW inputs; no QC rule changes or new X/S+X batches. Reuse readonly .build/s-five-station-pipeline-20261008/collect_performance.py and save UTC performance snapshots. First baseline snapshot-20261007T165326Z.json: RUNNING, native QC 209/794, diagnostics 2/106, five-S CR 22/106, collector errors empty. Recent own completed jobs in 1h: QC 21 samples execution p50/p95 169/191s; grid 10 samples 29/48s; diagnostics 2 samples 197/202s. These are not whole-hour utilization averages or throughput. Notify meaningful progress, repair, fault or completion; stay quiet on unchanged non-actionable state. After all actual verification completes, save final performance summary and pause this automation.
