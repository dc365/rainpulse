import { describe, expect, it } from 'vitest'
import { refreshPlan, selectedCycleRevision, mergeRefreshReason } from './refreshPolicy'
import type { RefreshContext, RefreshReason } from './refreshPolicy'

const base: RefreshContext = { visible: true, connected: true, mode: 'follow', catalogAgeMS: 0, detailAgeMS: 0 }
describe('request policy', () => {
  it('keeps manual refresh explicit, even in a hidden page', () => {
    expect(refreshPlan('manual', { ...base, visible: false })).toEqual({ catalog: true, detail: true })
  })
  for (const reason of ['event', 'timer', 'reconnect', 'visible'] as RefreshReason[]) {
    it(`does not request ${reason} in a hidden page`, () => {
      expect(refreshPlan(reason, { ...base, visible: false, catalogAgeMS: 1e9, detailAgeMS: 1e9 })).toEqual({ catalog: false, detail: false })
    })
  }
  it('refreshes followed detail on an event, not an unrelated pinned detail', () => {
    expect(refreshPlan('event', base)).toEqual({ catalog: true, detail: true })
    expect(refreshPlan('event', { ...base, mode: 'history' })).toEqual({ catalog: true, detail: false })
  })
  it('uses a slower safety poll while connected', () => {
    expect(refreshPlan('timer', { ...base, catalogAgeMS: 30_000, detailAgeMS: 30_000 })).toEqual({ catalog: false, detail: false })
    expect(refreshPlan('timer', { ...base, catalogAgeMS: 120_000, detailAgeMS: 120_000 })).toEqual({ catalog: true, detail: true })
  })
  it('recovers quickly without SSE but still rechecks same-ID historical rebuilds', () => {
    expect(refreshPlan('timer', { ...base, connected: false, mode: 'history', catalogAgeMS: 30_000, detailAgeMS: 30_000 })).toEqual({ catalog: true, detail: false })
    expect(refreshPlan('timer', { ...base, connected: false, mode: 'history', catalogAgeMS: 30_000, detailAgeMS: 120_000 })).toEqual({ catalog: true, detail: true })
  })
  it('does not confuse catalog freshness with selected content identity', () => {
    const c = { cycle_id: 'a', analysis_id: 'x', capabilities: { radar: true, lk: false, steps: false, nowcastnet: false } }
    const key = selectedCycleRevision(c)
    expect(selectedCycleRevision({ ...c })).toBe(key)
    expect(selectedCycleRevision({ ...c, analysis_id: 'y' })).not.toBe(key)
    expect(selectedCycleRevision(undefined)).toBe('')
  })
  it('does not downgrade reconnect recovery during an event burst', () => {
    expect(mergeRefreshReason('reconnect', 'event')).toBe('reconnect')
    expect(mergeRefreshReason('event', 'visible')).toBe('visible')
    expect(mergeRefreshReason('manual', 'event')).toBe('manual')
    expect(mergeRefreshReason(undefined, 'event')).toBe('event')
  })
})
