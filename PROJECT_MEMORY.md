# RainPulse Project Memory

Updated: 2026-09-17 (Asia/Taipei)

This file is the concise handoff for a new Codex session. Stable engineering
rules remain in `AGENTS.md`; implementation details remain in the referenced RP
documents. Do not add passwords, tokens, private data-source details, or raw
operational data here.

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
  Source remains local uncommitted; deployment used an isolated tested source bundle.
  Details: docs/near-background-clutter-20260919.md.
