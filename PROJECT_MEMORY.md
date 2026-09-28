# RainPulse Project Memory

Updated: 2026-09-26 (Asia/Taipei)

This file is the concise handoff for a new Codex session. Stable engineering
rules remain in `AGENTS.md`; implementation details remain in the referenced RP
documents. Do not add passwords, tokens, private data-source details, or raw
operational data here.

## Current S/X mainline: Priority 1 (2026-09-27)

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
  New worktree rainpulse-sx-priority1-followup beside main (old/tmp removed).
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
