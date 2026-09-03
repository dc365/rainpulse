# RainPulse Project Memory

Updated: 2026-09-03 (Asia/Taipei)

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

## Test deployment

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
- The Go control services follow the Ruiyun BDP runtime integration. Their
  program/configuration code is `bdp-dp-rada-rainpulse`; `RADA_L2_FMT` input
  roots come from BDP metadata when the platform is available, with the
  deployment manifest retained as the compatibility fallback.
- Existing SSH authorization is configured outside the repository. Credentials
  are intentionally omitted from project memory and must never be committed.
- For routine UI deployments, the user prefers an in-place update with only
  proportionate checks, then direct visual validation in the browser.

## Current product and UI behavior

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

- Phase-1 critical path remains radar QC, Hybrid Scan/grid, mosaic, QPE,
  NowcastInput, pySTEPS-LK and application products. pySTEPS-STEPS and
  NowcastNet remain comparison/controlled paths rather than blockers for the
  baseline product.
- Radar QC has been expanded for the visible 08/28 artifacts and history was
  replayed. Preserve raw and normalized radar inputs; QC is performed in polar
  space and cause flags/QI/provenance must remain traceable.
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
- Last end-to-end server proof used 08/28 14:25 CST: regeneration request
  `4bfb3099-1f34-4e9c-a0bc-459623d22efb`, target run
  `5c13c4bb-4d87-5ebb-914a-84a2afcc0544`; QC, grid, mosaic, QPE, diagnostics,
  NowcastInput, pySTEPS-LK and product publication all completed successfully.

## Data-version and retention intent

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
