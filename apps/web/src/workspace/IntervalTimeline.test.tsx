import { act, cleanup, fireEvent, render, renderHook, screen, waitFor } from '@testing-library/react'
import { afterEach, expect, it, vi } from 'vitest'
import { useIntervalPanels } from './IntervalTimeline'
import { SharedTimeline } from './MainWorkspace'
import type { WorkspaceCycleDetail } from './model'

afterEach(() => { cleanup(); vi.unstubAllGlobals() })
const issueTime = '2026-08-28T08:30:00Z'
const props = { issueTime, values: Array.from({length:25},(_,i)=>new Date(Date.parse(issueTime)+i*300_000).toISOString()), panels:[], playing:false, selectedTime:issueTime, onTogglePlaying:vi.fn(), onSelect:vi.fn() }

it('uses the original time ticks and only the three shortcuts', () => {
  const commit = vi.fn()
  const select = vi.fn()
  render(<SharedTimeline {...props} onInterval={commit} onSelect={select} selectedInterval={{start:0,end:60}} />)
  expect(screen.queryByRole('button',{name:'区间累计'})).toBeNull()
  expect(screen.queryByRole('slider')).toBeNull()
  fireEvent.click(screen.getByRole('button', { name: '1–2 时' }))
  expect(commit).toHaveBeenLastCalledWith({ start: 60, end: 120 })
  fireEvent.click(screen.getByRole('button',{name:/^\+15 min，/}))
  expect(select).toHaveBeenLastCalledWith(props.values[3])
})

it.each([[15,60],[60,15]])('commits drag %s → %s only on release', (from,to) => {
  vi.stubGlobal('PointerEvent', MouseEvent)
  const commit = vi.fn(), select = vi.fn()
  const {container}=render(<SharedTimeline {...props} onInterval={commit} onSelect={select} />)
  const rail=container.querySelector('.workspace-timeline-rail')!
  container.querySelectorAll<HTMLElement>('[data-lead]').forEach(node => {
    node.getBoundingClientRect=()=>({left:Number(node.dataset.lead)*10,width:50} as DOMRect)
  })
  fireEvent.pointerDown(container.querySelector(`[data-lead="${from}"]`)!,{button:0,clientX:from*10+25})
  fireEvent.pointerMove(rail,{clientX:to*10+25})
  expect(commit).not.toHaveBeenCalled()
  expect(container.querySelector('[data-lead="30"]')?.getAttribute('data-selected')).toBe('true')
  fireEvent.pointerUp(rail,{clientX:to*10+25})
  expect(commit).toHaveBeenCalledExactlyOnceWith({start:15,end:60})
  expect(select).not.toHaveBeenCalled()
})

it('a press and release without dragging selects just one time', () => {
  vi.stubGlobal('PointerEvent', MouseEvent)
  const commit=vi.fn(), select=vi.fn()
  const {container}=render(<SharedTimeline {...props} onInterval={commit} onSelect={select} />)
  const node=container.querySelector('[data-lead="15"]')!
  fireEvent.pointerDown(node,{button:0,clientX:100})
  fireEvent.pointerUp(node,{clientX:100})
  fireEvent.click(node,{detail:1})
  expect(select).toHaveBeenCalledExactlyOnceWith(props.values[3])
  expect(commit).not.toHaveBeenCalled()
})

it('ignores late results from a previous interval and avoids polling duplicates', async () => {
  const pending: { input: { algorithm: string; start_minutes: number; end_minutes: number }; resolve: (value: unknown) => void }[] = []
  const fetcher = vi.fn((_url, options) => new Promise(resolve => pending.push({ input: JSON.parse(options.body), resolve })))
  vi.stubGlobal('fetch', fetcher)
  const detail = { cycle_id: 'test-cycle', panels: [] } as unknown as WorkspaceCycleDetail
  const { result, rerender } = renderHook(({ range, source }) => useIntervalPanels(source, true, range), {
    initialProps: { range: { start: 0, end: 60 }, source: detail },
  })
  expect(fetcher).toHaveBeenCalledTimes(4)
  rerender({ range: { start: 0, end: 60 }, source: { ...detail } })
  expect(fetcher).toHaveBeenCalledTimes(4)
  rerender({ range: { start: 15, end: 45 }, source: detail })
  expect(fetcher).toHaveBeenCalledTimes(8)
  const answer = (call: typeof pending[number]) => call.resolve({ ok: true, json: async () => ({
    cycle_id: 'test-cycle', start_minutes: call.input.start_minutes, end_minutes: call.input.end_minutes,
    panels: [{ panel_id: call.input.algorithm, frames: [{ lead_time_minutes: call.input.end_minutes }] }],
  }) })
  await act(async () => { pending.slice(4).forEach(answer) })
  await waitFor(() => expect(result.current.busy).toBe(false))
  await act(async () => { pending.slice(0,4).forEach(answer) })
  expect(result.current.panels).toHaveLength(4)
  expect(result.current.panels?.every(p => p.frames[0].lead_time_minutes === 45)).toBe(true)
})
