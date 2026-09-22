import { act, cleanup, renderHook } from '@testing-library/react'
import { afterEach, beforeEach, expect, it, vi } from 'vitest'
import { useWorkspaceData } from './useWorkspaceData'
import { readCycleCatalog } from './readCycleCatalog'
import type { CycleSummary } from './model'

vi.mock('./readCycleCatalog', () => ({ readCycleCatalog: vi.fn() }))
const at = '2026-09-22T13:00:00Z'
const cycle = (): CycleSummary => ({ cycle_id: 'A', grid_id: 'g', issue_time: at,
  execution_mode: 'realtime_shadow', freshness_seconds: 0,
  capabilities: { radar: true, lk: true, steps: false, nowcastnet: false } })
const catalog = (analysis = 'old') => ({ schema_version: '1.0' as const, generated_at: at,
  items: [{ ...cycle(), analysis_id: analysis }] })
const payload = () => ({ ...cycle(), schema_version: '1.0', grid: {}, quality: {},
  radars: [], panels: [], timeline: [at] })
class Source {
  static instances: Source[] = []
  onopen: (() => void) | null = null
  onerror: (() => void) | null = null
  listeners = new Map<string, (event: MessageEvent) => void>()
  close = vi.fn()
  constructor() { Source.instances.push(this) }
  addEventListener(name: string, callback: (event: MessageEvent) => void) { this.listeners.set(name, callback) }
  emit(revision: string) { this.listeners.get('workspace.changed')?.(new MessageEvent('workspace.changed', { data: JSON.stringify({ revision }) })) }
}
let hidden = false
const flush = async () => { await act(async () => { await Promise.resolve(); await Promise.resolve() }) }
const advance = async (ms: number) => { await act(async () => { await vi.advanceTimersByTimeAsync(ms) }) }
beforeEach(() => {
  vi.useFakeTimers(); vi.setSystemTime(new Date(at)); hidden = false; Source.instances = []
  vi.spyOn(document, 'visibilityState', 'get').mockImplementation(() => hidden ? 'hidden' : 'visible')
  vi.stubGlobal('EventSource', Source)
  vi.stubGlobal('fetch', vi.fn(async () => new Response(JSON.stringify(payload()))))
  vi.mocked(readCycleCatalog).mockReset().mockResolvedValue(catalog())
})
afterEach(() => { cleanup(); vi.useRealTimers(); vi.restoreAllMocks(); vi.unstubAllGlobals() })
async function ready() {
  const hook = renderHook(useWorkspaceData)
  await flush()
  act(() => Source.instances[0].onopen?.())
  await advance(100); await flush()
  expect(hook.result.current.state.detail?.cycle_id).toBe('A')
  vi.mocked(fetch).mockClear(); vi.mocked(readCycleCatalog).mockClear()
  return hook
}
it('does not refetch pinned detail for an unrelated catalog event', async () => {
  const hook = await ready()
  act(() => hook.result.current.pin())
  act(() => Source.instances[0].emit('other-cycle'))
  await advance(100); await flush()
  expect(readCycleCatalog).toHaveBeenCalledTimes(1)
  expect(fetch).not.toHaveBeenCalled()
})
it('fetches pinned detail when its asset identity changes', async () => {
  const hook = await ready(); act(() => hook.result.current.pin())
  vi.mocked(readCycleCatalog).mockResolvedValue(catalog('rebuilt'))
  act(() => Source.instances[0].emit('selected-cycle'))
  await advance(100); await flush()
  expect(fetch).toHaveBeenCalledTimes(1)
})
it('event after reconnect cannot suppress a historical detail refresh', async () => {
  const hook = await ready(); act(() => hook.result.current.pin())
  act(() => { Source.instances[0].onopen?.(); Source.instances[0].emit('burst') })
  await advance(100); await flush()
  expect(fetch).toHaveBeenCalledTimes(1)
})
it('rechecks same-ID historical content at the safety interval', async () => {
  const hook = await ready(); act(() => hook.result.current.pin())
  await advance(119_000)
  expect(fetch).not.toHaveBeenCalled()
  // The 5-second ticker rounds the 120-second age up to the next tick.
  await advance(6_000); await flush()
  expect(fetch).toHaveBeenCalledTimes(1)
})
it('suppresses hidden polling and refreshes both snapshots on visibility recovery', async () => {
  await ready(); hidden = true
  act(() => { document.dispatchEvent(new Event('visibilitychange')); Source.instances[0].emit('hidden') })
  await advance(180_000)
  expect(fetch).not.toHaveBeenCalled(); expect(readCycleCatalog).not.toHaveBeenCalled()
  hidden = false
  act(() => document.dispatchEvent(new Event('visibilitychange')))
  await advance(100); await flush()
  expect(fetch).toHaveBeenCalledTimes(1); expect(readCycleCatalog).toHaveBeenCalledTimes(1)
})
it('uses short fallback polling after SSE failure and cleans up all subscriptions', async () => {
  const hook = await ready()
  act(() => Source.instances[0].onerror?.())
  await advance(35_000); await flush()
  expect(hook.result.current.connection).toBe('polling')
  expect(fetch).toHaveBeenCalledTimes(1)
  hook.unmount()
  expect(Source.instances[0].close).toHaveBeenCalledTimes(1)
  expect(vi.getTimerCount()).toBe(0)
})
