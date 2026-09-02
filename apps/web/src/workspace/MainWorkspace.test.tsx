import { cleanup, fireEvent, render, screen } from '@testing-library/react'
import { afterEach, describe, expect, it, vi } from 'vitest'

import { focusedPanelFromSearch, workspaceLayoutSearch } from './layoutState'
import { SharedTimeline, updateLayerErrorState } from './MainWorkspace'
import type { WorkspacePanel } from './model'

const issueTime = '2026-09-01T01:00:00Z'
const values = [
  issueTime,
  '2026-09-01T01:05:00Z',
  '2026-09-01T01:10:00Z',
]

const panels: WorkspacePanel[] = [
  {
    panel_id: 'qpe',
    algorithm_id: 'radar',
    display_name: '雷达 QPE',
    role: 'observation',
    lifecycle: 'analysis',
    data_kind: 'rain_rate',
    cadence_minutes: 5,
    status: 'ready',
    frames: [{
      asset_id: 'qpe-0',
      valid_time: issueTime,
      lead_time_minutes: 0,
      image_url: '/qpe-0.png',
      media_type: 'image/png',
    }],
  },
  {
    panel_id: 'lk',
    algorithm_id: 'pysteps-lk',
    display_name: 'pySTEPS-LK',
    role: 'forecast',
    lifecycle: 'shadow',
    data_kind: 'rain_rate',
    cadence_minutes: 5,
    status: 'ready',
    frames: [{
      asset_id: 'lk-5',
      valid_time: values[1],
      lead_time_minutes: 5,
      image_url: '/lk-5.png',
      media_type: 'image/png',
    }],
  },
]

afterEach(cleanup)

describe('SharedTimeline', () => {
  it('renders a continuous rail and moves by controls or keyboard', () => {
    const onSelect = vi.fn()
    const { container } = render(
      <SharedTimeline
        issueTime={issueTime}
        values={values}
        panels={panels}
        selectedTime={issueTime}
        playing={false}
        onTogglePlaying={vi.fn()}
        onSelect={onSelect}
      />,
    )

    expect((screen.getByRole('button', { name: '前一时刻' }) as HTMLButtonElement).disabled).toBe(true)
    expect(screen.getByRole('button', { name: /\+5 min/ }).getAttribute('aria-current')).toBeNull()
    expect(container.querySelectorAll('.workspace-timeline-lanes i')).toHaveLength(values.length * panels.length)
    expect(container.querySelector('.workspace-timeline-toolbar')).toBeNull()
    expect(container.querySelector('.workspace-timeline-context .workspace-timeline-playback')).not.toBeNull()
    expect(container.querySelector('.workspace-timeline-context .workspace-timeline-state')).not.toBeNull()

    fireEvent.click(screen.getByRole('button', { name: '后一时刻' }))
    expect(onSelect).toHaveBeenLastCalledWith(values[1])

    fireEvent.keyDown(screen.getByLabelText('统一有效时间轴'), { key: 'End' })
    expect(onSelect).toHaveBeenLastCalledWith(values[2])
  })
})

describe('updateLayerErrorState', () => {
  it('does not create a render-driving state update when the layer state is unchanged', () => {
    const current = { qpe: false }

    expect(updateLayerErrorState(current, 'qpe', false)).toBe(current)
    expect(updateLayerErrorState(current, 'qpe', true)).toEqual({ qpe: true })
  })
})

describe('workspace focus URL state', () => {
  it('defaults to comparison and restores only an explicit focused panel', () => {
    expect(focusedPanelFromSearch('')).toBeNull()
    expect(focusedPanelFromSearch('?layout=single')).toBeNull()
    expect(focusedPanelFromSearch('?layout=single&panel=lk')).toBe('lk')
    expect(focusedPanelFromSearch('?layout=compare&panel=lk')).toBeNull()
  })

  it('preserves unrelated URL filters when focus is entered or cleared', () => {
    expect(workspaceLayoutSearch('?cycle=rain-1', 'steps')).toBe('cycle=rain-1&layout=single&panel=steps')
    expect(workspaceLayoutSearch('?cycle=rain-1&layout=single&panel=steps', null)).toBe('cycle=rain-1')
  })
})
