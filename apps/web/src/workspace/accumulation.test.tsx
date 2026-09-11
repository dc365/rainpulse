import { cleanup, fireEvent, render, screen } from '@testing-library/react'
import { afterEach, expect, it, vi } from 'vitest'
import { accumulationLabel, accumulationTimes, productPanels } from './accumulation'
import { SharedTimeline } from './MainWorkspace'
import type { WorkspaceCycleDetail, WorkspacePanel } from './model'

afterEach(cleanup)
const issue = '2026-08-28T08:30:00Z'
const base: WorkspacePanel = { panel_id: 'lk', algorithm_id: 'lk', display_name: 'LK',
  role: 'forecast', lifecycle: 'shadow', data_kind: 'rain_rate', cadence_minutes: 5,
  status: 'ready', legend_unit: 'mm/h', frames: [] }

it('uses issue-relative intervals, not natural hours', () => {
  expect(accumulationTimes(issue, 'hourly')).toEqual(['2026-08-28T09:30:00.000Z', '2026-08-28T10:30:00.000Z'])
  expect(accumulationLabel(issue, '2026-08-28T10:30:00Z', 'hourly')).toContain('17:30 至 08/28 18:30')
  expect(accumulationLabel(issue, '2026-08-28T10:30:00Z', 'total_2h')).toContain('16:30 至 08/28 18:30')
})

it('never substitutes rate frames for missing accumulation products', () => {
  const detail = { panels: [base] } as WorkspaceCycleDetail
  const projected = productPanels(detail, [base], 'hourly')[0]
  expect(projected.status).toBe('unavailable')
  expect(projected.frames).toEqual([])
  expect(projected.legend_unit).toBe('mm')
  expect(productPanels(detail, [base], 'rain_rate')[0]).toBe(base)
})

it('preserves map identity while selecting backend cumulative frames', () => {
  const cumulative = { ...base, panel_id: 'lk:accumulation_60', data_kind: 'accumulation_60', legend_unit: 'mm' }
  const detail = { panels: [base, cumulative] } as WorkspaceCycleDetail
  expect(productPanels(detail, [base], 'hourly')[0]).toEqual({ ...cumulative, panel_id: 'lk' })
})

it('hourly timeline selects two intervals, total timeline has no playback', () => {
  const onSelect = vi.fn(), onMode = vi.fn()
  const props = { issueTime: issue, panels: [], playing: false, onTogglePlaying: vi.fn(), onSelect, onProductMode: onMode }
  const { rerender } = render(<SharedTimeline {...props} productMode="hourly"
    values={accumulationTimes(issue, 'hourly')} selectedTime={accumulationTimes(issue, 'hourly')[0]} />)
  fireEvent.click(screen.getByRole('button', { name: '后一时刻' }))
  expect(onSelect).toHaveBeenCalledWith('2026-08-28T10:30:00.000Z')
  fireEvent.click(screen.getByRole('button', { name: '5分钟雨强' }))
  expect(onMode).toHaveBeenCalledWith('rain_rate')
  rerender(<SharedTimeline {...props} productMode="total_2h" values={accumulationTimes(issue, 'total_2h')} selectedTime={null} />)
  expect(screen.queryByRole('button', { name: '播放' })).toBeNull()
  expect(screen.queryByRole('button', { name: '后一时刻' })).toBeNull()
  expect(screen.getByText('0–2 小时')).toBeTruthy()
})
