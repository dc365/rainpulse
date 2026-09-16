import { act, cleanup, fireEvent, render, renderHook, screen, waitFor } from '@testing-library/react'
import { afterEach, expect, it, vi } from 'vitest'
import { useIntervalPanels } from './IntervalTimeline'
import { SharedTimeline } from './MainWorkspace'
import type { WorkspaceCycleDetail } from './model'

afterEach(() => { cleanup(); vi.unstubAllGlobals() })
const issueTime = '2026-08-28T08:30:00Z'
const props = { issueTime, values: Array.from({length:41},(_,i)=>new Date(Date.parse(issueTime)+(i-10)*360_000).toISOString()), panels:[], playing:false, selectedTime:issueTime, onTogglePlaying:vi.fn(), onSelect:vi.fn() }

it('uses the original time ticks and the six shortcuts', () => {
  const commit = vi.fn()
  const select = vi.fn()
  render(<SharedTimeline {...props} onInterval={commit} onSelect={select} selectedInterval={{start:0,end:60}} />)
  expect(screen.queryByRole('button',{name:'区间累计'})).toBeNull()
  expect(screen.queryByRole('slider')).toBeNull()
  fireEvent.click(screen.getByRole('button', { name: '1–2 时' }))
  expect(commit).toHaveBeenLastCalledWith({ start: 60, end: 120 })
  fireEvent.click(screen.getByRole('button',{name:/^\+18 min，/}))
  expect(select).toHaveBeenLastCalledWith(props.values[13])
})

it('separates observed history from forecast and supports cross-origin ranges', () => {
  const commit = vi.fn()
  const { container } = render(<SharedTimeline {...props} onInterval={commit} />)
  expect(container.querySelectorAll('[data-lead]')).toHaveLength(41)
  expect(container.querySelectorAll('[data-period="past"]')).toHaveLength(10)
  expect(container.querySelectorAll('[data-period="future"]')).toHaveLength(30)
  expect(screen.getByText('起报时刻')).toBeTruthy()
  fireEvent.click(screen.getByRole('button', { name: '过去1时' }))
  expect(commit).toHaveBeenLastCalledWith({ start: -60, end: 0 })
  fireEvent.click(screen.getByRole('button', { name: '0–3 时' }))
  expect(commit).toHaveBeenLastCalledWith({ start: 0, end: 180 })
})

it.each([[18,60],[60,18],[-30,30],[30,-30]])('commits drag %s → %s only on release', (from,to) => {
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
  expect(commit).toHaveBeenCalledExactlyOnceWith({start:Math.min(from,to),end:Math.max(from,to)})
  expect(select).not.toHaveBeenCalled()
})

it('a press and release without dragging selects just one time', () => {
  vi.stubGlobal('PointerEvent', MouseEvent)
  const commit=vi.fn(), select=vi.fn()
  const {container}=render(<SharedTimeline {...props} onInterval={commit} onSelect={select} />)
  const node=container.querySelector('[data-lead="18"]')!
  fireEvent.pointerDown(node,{button:0,clientX:100})
  fireEvent.pointerUp(node,{clientX:100})
  fireEvent.click(node,{detail:1})
  expect(select).toHaveBeenCalledExactlyOnceWith(props.values[13])
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
  rerender({ range: { start: 18, end: 48 }, source: detail })
  expect(fetcher).toHaveBeenCalledTimes(8)
  const answer = (call: typeof pending[number]) => call.resolve({ ok: true, json: async () => ({
    cycle_id: 'test-cycle', start_minutes: call.input.start_minutes, end_minutes: call.input.end_minutes,
    panels: [{ panel_id: call.input.algorithm, frames: [{ lead_time_minutes: call.input.end_minutes }] }],
  }) })
  await act(async () => { pending.slice(4).forEach(answer) })
  await waitFor(() => expect(result.current.busy).toBe(false))
  await act(async () => { pending.slice(0,4).forEach(answer) })
  expect(result.current.panels).toHaveLength(4)
  expect(result.current.panels?.every(p => p.frames[0].lead_time_minutes === 48)).toBe(true)
})
