import { cleanup, fireEvent, render, screen, waitFor } from '@testing-library/react'
import { afterEach, beforeEach, expect, it, vi } from 'vitest'
import { HistoricalQCPanel, historyScope } from './HistoricalQCPanel'
import type { CycleSummary } from './model'
const cycles = ['one', 'two', 'missing'].map((id, i) => ({ cycle_id: id, run_id: i < 2 ? id : undefined, issue_time: `2026-08-27T16:${String(i * 5).padStart(2, '0')}:00Z`, grid_id: 'grid', execution_mode: 'historical', freshness_seconds: 0, capabilities: { radar: true, lk: true, steps: false, nowcastnet: false } } as CycleSummary))
const json = (data: unknown, status = 200) => new Response(JSON.stringify(data), { status, headers: { 'Content-Type': 'application/json' } })
beforeEach(() => {
  const values = new Map<string, string>()
  vi.stubGlobal('localStorage', { getItem: (k: string) => values.get(k) ?? null, setItem: (k: string, v: string) => values.set(k, v), clear: () => values.clear() })
})
afterEach(() => { cleanup(); window.localStorage.clear(); vi.unstubAllGlobals() })
it('groups Beijing dates, deduplicates runs and exposes unavailable cycles', () => {
  const scope = historyScope([...cycles, cycles[0]], '2026-08-28')
  expect(scope.runs).toHaveLength(2)
  expect(scope.unavailable).toHaveLength(1)
})
it('submits all available cycles, counts publication and restores without reposting', async () => {
  const fetcher = vi.fn(async (url: string, options?: RequestInit) => {
    if (options?.method === 'POST') return json({ run_id: url.includes('/one/') ? 'new-one' : 'new-two' }, 202)
    return url.endsWith('/jobs') ? json([{ job_type: 'product', status: 'SUCCEEDED' }]) : json({ status: 'PUBLISHED' })
  })
  vi.stubGlobal('fetch', fetcher)
  const page = render(<HistoricalQCPanel cycles={cycles} />)
  fireEvent.click(screen.getByRole('button', { name: '重算本案例全部可用时次' }))
  await waitFor(() => expect(screen.getByText(/已发布 2 \/ 2/)).toBeTruthy())
  expect(fetcher.mock.calls.filter(c => c[1]?.method === 'POST')).toHaveLength(2)
  page.unmount(); render(<HistoricalQCPanel cycles={cycles} />)
  expect(screen.getByText(/已发布 2 \/ 2/)).toBeTruthy()
  expect(fetcher.mock.calls.filter(c => c[1]?.method === 'POST')).toHaveLength(2)
})
it('does not count rejection as completion and stops on uncertain submission', async () => {
  vi.stubGlobal('fetch', vi.fn(async () => { throw new Error('connection lost') }))
  render(<HistoricalQCPanel cycles={cycles} />)
  fireEvent.click(screen.getByRole('button', { name: '重算本案例全部可用时次' }))
  await waitFor(() => expect(screen.getByText(/受理结果不确定：connection lost/)).toBeTruthy())
  expect(screen.getByText(/已发布 0 \/ 2/)).toBeTruthy()
  expect(vi.mocked(fetch)).toHaveBeenCalledTimes(1)
})
