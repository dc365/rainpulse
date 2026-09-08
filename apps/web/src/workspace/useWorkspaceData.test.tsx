import { act, cleanup, renderHook, waitFor } from '@testing-library/react'
import { afterEach, expect, it, vi } from 'vitest'
import { useWorkspaceData } from './useWorkspaceData'
import type { CycleSummary } from './model'

const cycle = (id: string): CycleSummary => ({ cycle_id: id, grid_id: 'g',
  issue_time: `2026-09-08T01:${id === 'A' ? '00' : id === 'B' ? '05' : '10'}:00Z`,
  execution_mode: 'realtime_shadow', freshness_seconds: 0,
  capabilities: { radar: true, lk: true, steps: false, nowcastnet: false } })
const detail = (id: string) => ({ ...cycle(id), schema_version: '1.0', grid: {}, quality: {},
  radars: [], panels: [], timeline: [cycle(id).issue_time] })
const catalog = (ids: string[]) => ({ schema_version: '1.0', items: ids.map(cycle) })
function requests() {
  const pending: { url: string; signal: AbortSignal; resolve: (response: Response) => void }[] = []
  vi.stubGlobal('fetch', vi.fn((url: string, options: RequestInit) => new Promise<Response>(resolve => {
    // Deliberately ignore abort to model a transport already delivering a response.
    pending.push({ url, signal: options.signal as AbortSignal, resolve })
  })))
  const reply = async (request: typeof pending[number], payload: unknown, status = 200) => {
    await act(async () => request.resolve(new Response(JSON.stringify(payload), { status })))
  }
  const next = async (suffix: string, from = 0) => {
    await waitFor(() => expect(pending.slice(from).some(item => item.url.endsWith(suffix))).toBe(true))
    return pending.slice(from).find(item => item.url.endsWith(suffix))!
  }
  return { pending, reply, next }
}
afterEach(() => { cleanup(); vi.unstubAllGlobals() })

it('keeps A on a failed switch and ignores B arriving after C has committed', async () => {
  const { reply, next } = requests()
  const hook = renderHook(useWorkspaceData)
  await reply(await next('limit=200'), catalog(['A']))
  await reply(await next('/A'), detail('A'))
  expect(hook.result.current.state.detail?.cycle_id).toBe('A')
  act(() => hook.result.current.requestCycle(cycle('B')))
  const failedB = await next('/B')
  await reply(failedB, {}, 503)
  expect(hook.result.current.state.detail?.cycle_id).toBe('A')
  expect(hook.result.current.state.selectedTime).toBe(cycle('A').issue_time)
  expect(hook.result.current.state.detailError).toContain('503')

  act(() => hook.result.current.requestCycle(cycle('A')))
  act(() => hook.result.current.requestCycle(cycle('B')))
  // Pick A then B creates a new effect even when the previous B failed.
  const bRequests = await waitFor(() => {
    const calls = vi.mocked(fetch).mock.calls.filter(([url]) => String(url).endsWith('/B'))
    expect(calls.length).toBe(2)
    return calls
  })
  expect(bRequests).toHaveLength(2)
  const secondB = await next('/B', 3)
  act(() => hook.result.current.requestCycle(cycle('C')))
  const c = await next('/C')
  expect(secondB.signal.aborted).toBe(true)
  await reply(c, detail('C'))
  await reply(secondB, detail('B'))
  expect(hook.result.current.state.detail?.cycle_id).toBe('C')
  expect(hook.result.current.state.selectedTime).toBe(cycle('C').issue_time)
  expect(hook.result.current.state.detailError).toBeNull()
})

it('preserves follow intent through a catalog outage and resumes with the next live cycle', async () => {
  const { pending, reply, next } = requests()
  const hook = renderHook(useWorkspaceData)
  await reply(await next('limit=200'), catalog(['A']))
  await reply(await next('/A'), detail('A'))
  const outageStart = pending.length
  act(() => hook.result.current.refresh())
  await reply(await next('limit=200', outageStart), {}, 503)
  await reply(await next('/A', outageStart), {}, 503)
  expect(hook.result.current.state.mode).toBe('follow')
  expect(hook.result.current.state.detail?.cycle_id).toBe('A')
  const recoveredStart = pending.length
  act(() => hook.result.current.refresh())
  await reply(await next('limit=200', recoveredStart), catalog(['C', 'A']))
  const lateA = await next('/A', recoveredStart)
  expect(lateA.signal.aborted).toBe(true)
  await reply(await next('/C', recoveredStart), detail('C'))
  await reply(lateA, detail('A'))
  expect(hook.result.current.state.mode).toBe('follow')
  expect(hook.result.current.state.detail?.cycle_id).toBe('C')
  expect(hook.result.current.state.catalogError).toBeNull()
  expect(hook.result.current.state.detailError).toBeNull()
})
