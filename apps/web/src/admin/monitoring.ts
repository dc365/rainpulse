import { toUTC } from './model';
import type { Inventory } from './model';
export type Scan = Omit<Inventory, 'config_version'> & { config_version: string | null; run_id: string | null; received_at: string | null; updated_at: string | null };
export type StationSummary = { radar_id: string; registered: number; normalized: number; qc: number; grid: number; failed: number; latest_observation: string | null };
export type AssetCheck = { scan_id: string; stage: string; uri: string; state: 'marker_checked' | 'unverified'; checked_at: string; scope: string; message: string; asset?: { sha256: string; marker_sha256: string; size_bytes: number } };
export type LinkedTask = { id: string; run_id: string; kind: string; state: string; created_at: string; config_version?: string; fingerprint?: string; name?: string };
export type Downstream = { id: string; analysis_time: string; grid_id: string; state: string; contribution_state: string; config_version: string; mosaic_uri: string | null; analysis_uri: string | null };
export type Lineage = { scan: Scan; automatic_tasks: LinkedTask[]; candidate_tasks: LinkedTask[]; downstream_analyses: Downstream[]; checks: AssetCheck[]; truncated: Record<string, boolean>; sampled_at: string };
export type Pool = { kind: string; mode: 'ACCEPTING' | 'DRAINING'; revision: number; updated_at: string; actor: string; reason: string; registered: number; fresh: number; ready: number; active: number; stalled: number; queued: number; oldest_queued_at: string | null };
export type PoolEvent = { id: number; kind: string; action: string; revision: number; reason: string; actor: string; at: string };
export type Distribution = { samples: number; p50: number | null; p95: number | null; maximum: number | null };
export type PerformanceGroup = { kind: string; fingerprint: string; workers: string[]; attempts: number; states: Record<string, number>; metrics: Record<string, Distribution> };
export type PerformanceSample = { id: string; task_id: string; run_id: string; kind: string; fingerprint: string; worker_id: string; state: string; started_at: string; finished_at: string | null; metrics: Record<string, number> };
export type PerformanceReport = { start: string; end: string; sampled_at: string; scope: string; quantile_basis: string; attempts: number; groups: PerformanceGroup[]; slow: PerformanceSample[] };
export function kindLabel(kind: string) { return ({ multiband: 'S/X 质控与组合', qc: '单站 QC', render: '对照图', diagnostics: '区域诊断' } as Record<string, string>)[kind] ?? kind; }
export function poolState(pool: Pool) { return pool.mode === 'DRAINING' ? pool.active > 0 ? '排空中' : '已暂停接单' : pool.ready === 0 ? '没有可用 Worker' : '允许接单'; }
export function currentCheck(checks: AssetCheck[], stage: string, uri: string | null) { return checks.find(c => c.stage === stage && c.uri === uri) ?? null; }
export function scanWindow(scan: Scan) { const at = Date.parse(scan.observed_at); if (!Number.isFinite(at)) throw new Error('体扫时间非法'); const start = Math.floor(at / 60000) * 60000; return { start: new Date(start).toISOString(), end: new Date(start + 60000).toISOString() }; }
export function windowQuery(start: string, end: string, maximumHours: number) {
  if (!Number.isFinite(maximumHours) || maximumHours <= 0) throw new Error('查询时间上限非法');
  const from = toUTC(start), until = toUTC(end);
  if (Date.parse(until) <= Date.parse(from) || Date.parse(until) - Date.parse(from) > maximumHours * 3600000)
    throw new Error(`请选择不超过${maximumHours}小时的有效时间范围`);
  return { start: from, end: until };
}
