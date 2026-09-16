import { cleanup, fireEvent, render, screen } from '@testing-library/react'
import { afterEach, expect, it, vi } from 'vitest'
import { HistoryPicker, caseSlot, localCaseDate } from './HistoryPicker'
import type { CycleSummary } from './model'

afterEach(cleanup)
const cycles: CycleSummary[] = ['2026-08-28T00:06:00Z', '2026-08-28T00:12:00Z', '2026-08-28T16:00:00Z'].map((issue_time, i) => ({ cycle_id: String(i), issue_time, grid_id: 'fuzhou', execution_mode: 'historical', freshness_seconds: 100000, capabilities: {radar:true, lk:false, steps:false, nowcastnet:false} }))
it('groups UTC observations into Beijing case dates across midnight', () => {
  expect(localCaseDate(cycles[2].issue_time)).toBe('2026-08-29')
})
it('files five-minute times into the six-minute clock without crossing midnight', () => {
  expect(caseSlot('08:10')).toBe('08:12')
  expect(caseSlot('08:12')).toBe('08:12')
  expect(caseSlot('08:25')).toBe('08:30')
  expect(caseSlot('00:00')).toBe('00:00')
  expect(caseSlot('23:59')).toBe('23:59')
})
it('selects an exact issue from the chosen date and closes the picker', () => {
  const onSelect = vi.fn()
  render(<HistoryPicker cycles={cycles} selectedID="0" onSelect={onSelect} />)
  fireEvent.click(screen.getByRole('button', {name: /历史案例 · 北京时间/}))
  fireEvent.click(screen.getByRole('button', {name:'2026-08-29'}))
  fireEvent.click(screen.getByRole('button', {name:'00:00'}))
  expect(onSelect).toHaveBeenCalledWith(cycles[2])
  expect(screen.queryByRole('region', {name:'历史案例选择'})).toBeNull()
})
it('moves to the next available slot, rather than inventing a time', () => {
  const onSelect = vi.fn()
  render(<HistoryPicker cycles={cycles} selectedID="0" onSelect={onSelect} />)
  fireEvent.click(screen.getByRole('button', {name:'下一起报时次'}))
  expect(onSelect).toHaveBeenCalledWith(cycles[1])
})
it('shows one row per six-minute slot and prefers the case closest to the slot', () => {
  const shared = [
    { ...cycles[0], cycle_id: 'a', issue_time: '2026-08-28T00:25:00Z' },
    { ...cycles[0], cycle_id: 'b', issue_time: '2026-08-28T00:30:00Z' },
  ]
  const onSelect = vi.fn()
  render(<HistoryPicker cycles={shared} selectedID="b" onSelect={onSelect} />)
  fireEvent.click(screen.getByRole('button', {name: /历史案例 · 北京时间/}))
  expect(screen.getAllByRole('button', {name: /^08:30/})).toHaveLength(1)
  fireEvent.click(screen.getByRole('button', {name:'08:30'}))
  expect(onSelect).toHaveBeenCalledWith(shared[1])
})
it('files a legacy five-minute case into its six-minute slot and keeps the real time visible', () => {
  // 00:10Z is 08:10 Beijing; the row is the 08:12 slot, the sub-label stays 08:10.
  const legacy = [{ ...cycles[0], cycle_id: 'legacy', issue_time: '2026-08-28T00:10:00Z' }]
  const onSelect = vi.fn()
  render(<HistoryPicker cycles={legacy} selectedID="legacy" onSelect={onSelect} />)
  expect(screen.getByText(/08:10 起报/)).toBeTruthy()
  fireEvent.click(screen.getByRole('button', {name: /历史案例 · 北京时间/}))
  const row = screen.getByRole('button', {name:'08:12（实际 08:10）'})
  expect(row.textContent).toContain('08:10')
  fireEvent.click(row)
  expect(onSelect).toHaveBeenCalledWith(legacy[0])
})
it('steps the range on six minutes and filters by the slot', () => {
  render(<HistoryPicker cycles={cycles} selectedID="0" onSelect={vi.fn()} />)
  fireEvent.click(screen.getByRole('button', {name: /历史案例 · 北京时间/}))
  const start = screen.getByLabelText('开始时间') as HTMLInputElement
  expect(start.step).toBe('360')
  expect(screen.getByRole('button', {name:'08:06'})).toBeTruthy()
  fireEvent.change(start, {target:{value:'08:10'}})
  expect(start.value).toBe('08:12')
  expect(screen.queryByRole('button', {name:'08:06'})).toBeNull()
  expect(screen.getByRole('button', {name:'08:12'})).toBeTruthy()
})
it('defaults to all day and filters inclusive Beijing time without selecting or recomputing', () => {
  const onSelect = vi.fn()
  render(<HistoryPicker cycles={cycles} selectedID="0" onSelect={onSelect} />)
  fireEvent.click(screen.getByRole('button', {name: /历史案例 · 北京时间/}))
  expect(screen.getByRole('button', {name:'08:06'})).toBeTruthy()
  fireEvent.change(screen.getByLabelText('开始时间'), {target:{value:'08:12'}})
  expect(screen.queryByRole('button', {name:'08:06'})).toBeNull()
  expect(onSelect).not.toHaveBeenCalled()
  fireEvent.click(screen.getByRole('button', {name:'全天'}))
  expect(screen.getByRole('button', {name:'08:06'})).toBeTruthy()
})
it('distinguishes an empty result range from unavailable source data and rejects reversed times', () => {
  render(<HistoryPicker cycles={cycles} selectedID="0" onSelect={vi.fn()} />)
  fireEvent.click(screen.getByRole('button', {name: /历史案例 · 北京时间/}))
  fireEvent.change(screen.getByLabelText('开始时间'), {target:{value:'09:00'}})
  expect(screen.getByText(/该时段暂无可用结果/)).toBeTruthy()
  fireEvent.change(screen.getByLabelText('结束时间'), {target:{value:'08:00'}})
  expect(screen.getByRole('alert').textContent).toContain('开始时间不能晚于结束时间')
  expect(screen.queryByRole('button', {name:'08:06'})).toBeNull()
})
