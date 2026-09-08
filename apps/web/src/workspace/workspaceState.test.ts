import { describe, expect, it } from 'vitest'
import type { CycleList, CycleSummary, WorkspaceCycleDetail } from './model'
import { initialWorkspaceState, workspaceReducer, assertCycleDetail, cycleAgeSeconds } from './workspaceState'

const cycle = (id: string, mode = 'realtime_shadow'): CycleSummary => ({
  cycle_id: id, grid_id: 'grid', issue_time: id === 'old' ? '2026-09-08T01:00:00Z' : '2026-09-08T01:05:00Z',
  execution_mode: mode, freshness_seconds: 30,
  capabilities: { radar: true, lk: true, steps: false, nowcastnet: false },
})
const catalog = (items: CycleSummary[]): CycleList => ({ schema_version: '1.0', items, generated_at: '2026-09-08T01:05:00Z' })
const detail = (id: string): WorkspaceCycleDetail => ({ ...cycle(id), schema_version: '1.0', grid: { grid_id: 'grid', bounds: [118,25,123,27], raster_bounds: [118,25,123,27] }, quality: {}, radars: [], panels: [], timeline: [cycle(id).issue_time] })
function displayed() {
  const state = workspaceReducer(initialWorkspaceState, { type: 'catalog', payload: catalog([cycle('old')]) })
  return workspaceReducer(state, { type: 'loaded', id: 'old', detail: detail('old'), stale: false })
}
describe('atomic workspace snapshots', () => {
  it('retains the old title, time and detail together when selecting a new cycle fails', () => {
    const old = displayed()
    const picked = workspaceReducer(old, { type: 'pick', cycle: cycle('new') })
    expect(picked.detail).toBe(old.detail)
    expect(picked.selectedTime).toBe(old.selectedTime)
    const failed = workspaceReducer(picked, { type: 'failed', id: 'new', message: 'offline' })
    expect(failed.detail?.cycle_id).toBe('old')
    expect(failed.selectedTime).toBe(old.selectedTime)
    expect(failed.loading).toBe(false)
  })
  it('commits a new cycle atomically and rejects a late old response', () => {
    const picked = workspaceReducer(displayed(), { type: 'pick', cycle: cycle('new') })
    expect(workspaceReducer(picked, { type: 'loaded', id: 'old', detail: detail('old'), stale: false })).toBe(picked)
    const loaded = workspaceReducer(picked, { type: 'loaded', id: 'new', detail: detail('new'), stale: false })
    expect(loaded.detail?.cycle_id).toBe('new')
    expect(loaded.selectedTime).toBe(cycle('new').issue_time)
  })
  it('does not disable follow during an outage and follows recovery automatically', () => {
    const stale = { ...cycle('old'), freshness_seconds: 3600 }
    const state = workspaceReducer(displayed(), { type: 'catalog', payload: catalog([stale]) })
    expect(state.mode).toBe('follow')
    expect(workspaceReducer(state, { type: 'catalog', payload: catalog([cycle('new'), stale]) }).requestedID).toBe('new')
  })
  it('does not jump a pinned historical view after an event', () => {
    const pinned = workspaceReducer(displayed(), { type: 'pin' })
    expect(workspaceReducer(pinned, { type: 'catalog', payload: catalog([cycle('new'), cycle('old')]) }).requestedID).toBe('old')
  })
  it('ends loading on an empty catalog and on first-load failure', () => {
    expect(workspaceReducer(initialWorkspaceState, { type: 'catalog', payload: catalog([]) }).loading).toBe(false)
    expect(workspaceReducer(initialWorkspaceState, { type: 'catalog-error', message: 'offline' }).loading).toBe(false)
  })
  it('distinguishes archive-only startup from a stale live cycle', () => {
    expect(workspaceReducer(initialWorkspaceState, { type: 'catalog', payload: catalog([cycle('old','historical_replay')]) }).mode).toBe('history')
    expect(workspaceReducer(initialWorkspaceState, { type: 'catalog', payload: catalog([{...cycle('old'), freshness_seconds:9000}]) }).mode).toBe('follow')
  })
  it('rejects mismatched detail identities and future/faulty age', () => {
    expect(() => assertCycleDetail(detail('old'), 'new')).toThrow()
    expect(cycleAgeSeconds(cycle('old'), 0)).toBe(Infinity)
    expect(cycleAgeSeconds(cycle('old'), Date.parse(cycle('old').issue_time) + 120_000)).toBe(120)
  })
})
