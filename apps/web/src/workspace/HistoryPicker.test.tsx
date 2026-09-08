import { cleanup, fireEvent, render, screen } from '@testing-library/react'
import { afterEach, expect, it, vi } from 'vitest'
import { HistoryPicker, localCaseDate } from './HistoryPicker'
import type { CycleSummary } from './model'

afterEach(cleanup)
const cycles: CycleSummary[] = ['2026-08-28T00:05:00Z', '2026-08-28T00:10:00Z', '2026-08-28T16:00:00Z'].map((issue_time, i) => ({ cycle_id: String(i), issue_time, grid_id: 'fuzhou', execution_mode: 'historical', freshness_seconds: 100000, capabilities: {radar:true, lk:false, steps:false, nowcastnet:false} }))
it('groups UTC observations into Beijing case dates across midnight', () => {
  expect(localCaseDate(cycles[2].issue_time)).toBe('2026-08-29')
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
it('moves to the next available issue, rather than inventing a time', () => {
  const onSelect = vi.fn()
  render(<HistoryPicker cycles={cycles} selectedID="0" onSelect={onSelect} />)
  fireEvent.click(screen.getByRole('button', {name:'下一起报时次'}))
  expect(onSelect).toHaveBeenCalledWith(cycles[1])
})
