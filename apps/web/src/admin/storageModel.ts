import type { Identity } from './model';
export type Channel = { kind: string; current_fingerprint: string; previous_fingerprint: string; revision: number; updated_at: string };
export type Release = { kind: string; fingerprint: string; identity: Identity; last_used_at: string; fresh_workers: number; pending_tasks: number };
export type ReleaseCatalog = { channels: Channel[]; items: Release[] };
export type StorageReport = { id: string; host: string; label: string; path: string; sampled_at: string; total_bytes: number; available_bytes: number; inodes_total: number; inodes_free: number; inode_used_percent: number | null; device: string };
export type StorageControl = { session_id: string | null; pressure_report: string; inode_stop_percent: number; minimum_free_bytes: number; report_max_age_seconds: number; revision: number };
export type CleanupAttempt = { id: string; task_id: string; kind: string; prefix: string; marker_sha256?: string };
export type CleanupPlan = { id: string; digest: string; targets: { run_id: string; name: string; logical_bytes: number; updated_at: string; attempts: CleanupAttempt[] }[]; created_at: string; expires_at: string; scope: 'managed_candidates_only'; object_store_endpoint: string; policy: { keep_latest: number; minimum_age_hours: number; limit: number } };
export type StorageStatus = { control: StorageControl; reports: { report: StorageReport; received_at: string }[]; plans: { id: string; digest: string; state: string; targets: number; created_at: string; finished_at?: string }[] };
export type RetentionPin = { run_id: string; name: string; reason: string; at: string };
export function storageBytes(value: number | null | undefined): string {
  if (value == null || !Number.isFinite(value) || value < 0) return '未知';
  const units = ['B', 'KiB', 'MiB', 'GiB', 'TiB']; let n = value, i = 0;
  while (n >= 1024 && i < units.length - 1) { n /= 1024; i++; }
  return `${n.toFixed(i ? 1 : 0)} ${units[i]}`;
}
export function reportState(r: StorageReport, now: number, maxAgeSeconds = 180): string {
  const at = Date.parse(r.sampled_at);
  if (!Number.isFinite(at) || now - at > maxAgeSeconds * 1000 || at > now + 5000) return '采样过期';
  if (r.inode_used_percent == null || !Number.isFinite(r.inode_used_percent)) return 'inode 未提供';
  return r.inode_used_percent >= 95 ? 'inode 告急' : r.inode_used_percent >= 85 ? 'inode 偏高' : '采样有效';
}
export function releaseLabel(r: Release, channels: Channel[]): string {
  const c = channels.find(c => c.kind === r.kind);
  return c?.current_fingerprint === r.fingerprint ? '当前默认' : c?.previous_fingerprint === r.fingerprint ? '上一版' : '历史／未选定';
}
export function releaseTitle(r: Release): string {
  return r.identity.versions.qc_pipeline_version ?? r.identity.versions.renderer_version ?? r.identity.versions.preview_version ?? r.fingerprint.slice(0, 12);
}
export function downloadCleanupPlan(plan: CleanupPlan): void {
  const blob = new Blob([JSON.stringify(plan, null, 2)], { type: 'application/json' });
  const url = URL.createObjectURL(blob); const a = document.createElement('a');
  a.href = url; a.download = `cleanup-${plan.id}.json`; a.click(); setTimeout(() => URL.revokeObjectURL(url), 1000);
}

export function cleanupState(value: string): string {
  const labels: Record<string, string> = { PREVIEW: '预览（未删除）', DELETING: '已退役／清理中', ERROR: '部分清理，待恢复', COMPLETE: 'S3目录已核对为空' };
  return labels[value] ?? value;
}
