# RainPulse Project Memory

Updated: 2026-09-13 (Asia/Taipei)

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


## 2026-09-13 RFI V3 候选实现

V3 基于 main@1251ea8，新增联合相位/ZDR证据、粗糙结构、有界核心/外围复核、精确时间票数、固定原始域审计和独立离线候选任务准备。配置 fujian-qc-rfi-multivariate-v3，pipeline qc-opensource-3.0.0；旧配置与 V1/V2 参数身份保留。见 docs/radar-qc-opensource/RFI_MULTIVARIATE_V3.md。软件回归与真实效果验收分开；本轮未部署、未获得105原始资料、未训练模型或运行原生SPIKE/bRopo，不把此前V2的真实部署记录当作V3效果证据。
