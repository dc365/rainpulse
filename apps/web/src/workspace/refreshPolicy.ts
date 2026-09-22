/** Request policy only; timestamps/HTTP/state snapshots belong to the hook. */
export type RefreshReason = 'manual' | 'event' | 'reconnect' | 'visible' | 'timer'
export type RefreshContext = {
  visible: boolean
  connected: boolean
  mode: 'follow' | 'history'
  catalogAgeMS: number
  detailAgeMS: number
}
export const FALLBACK_POLL_MS = 30_000
export const HEALTHY_POLL_MS = 120_000
export const HISTORY_DETAIL_POLL_MS = 120_000

export function refreshPlan(reason: RefreshReason, context: RefreshContext) {
  if (reason === 'manual') return { catalog: true, detail: true }
  if (!context.visible) return { catalog: false, detail: false }
  if (reason === 'visible' || reason === 'reconnect') return { catalog: true, detail: true }
  if (reason === 'event') return { catalog: true, detail: context.mode === 'follow' }
  const catalogInterval = context.connected ? HEALTHY_POLL_MS : FALLBACK_POLL_MS
  const detailInterval = context.mode === 'history'
    ? HISTORY_DETAIL_POLL_MS : catalogInterval
  return {
    catalog: context.catalogAgeMS >= catalogInterval,
    detail: context.detailAgeMS >= detailInterval,
  }
}

export type CycleReadIdentity = {
  cycle_id: string
  analysis_id?: string
  run_id?: string
  ensemble_bundle_id?: string
  nowcastnet_bundle_id?: string
  capabilities: { radar: boolean; lk: boolean; steps: boolean; nowcastnet: boolean }
}

/** Ignore freshness and generated_at. Same-ID QC reconstruction is covered by
 * the bounded detail poll: a catalog identity is NOT a content checksum. */
export function selectedCycleRevision(cycle: CycleReadIdentity | undefined): string {
  if (!cycle) return ''
  return JSON.stringify([
    cycle.cycle_id, cycle.analysis_id ?? '', cycle.run_id ?? '',
    cycle.ensemble_bundle_id ?? '', cycle.nowcastnet_bundle_id ?? '',
    cycle.capabilities.radar, cycle.capabilities.lk,
    cycle.capabilities.steps, cycle.capabilities.nowcastnet,
  ])
}

/** A burst of ordinary events must not downgrade a reconnect/visibility refresh. */
export function mergeRefreshReason(pending: RefreshReason | undefined, next: RefreshReason): RefreshReason {
  const priority: Record<RefreshReason, number> = { timer: 0, event: 1, visible: 2, reconnect: 3, manual: 4 }
  return pending && priority[pending] > priority[next] ? pending : next
}
