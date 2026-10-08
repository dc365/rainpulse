import { cleanup, fireEvent, render, screen } from '@testing-library/react'
import { afterEach, expect, it } from 'vitest'
import { RadarStatusStrip, bandOf, groupByBand } from './RadarStatusStrip'
import type { DataflowRadarStatus } from './types'

afterEach(cleanup)

function statusFixture(radarID: string, health = 'HEALTHY'): DataflowRadarStatus {
  return {
    radar_id: radarID, display_name: radarID.toUpperCase(), health,
    participating_in_latest_analysis: false,
  }
}

it('classifies radar bands from the site table and naming convention', () => {
  expect(bandOf('z9591')).toBe('S')
  expect(bandOf('Z9593')).toBe('S')
  expect(bandOf('zf101')).toBe('X')
  expect(bandOf('ZF505')).toBe('X')
  expect(bandOf('synthetic_radar_a')).toBe('other')
})

it('groups statuses into S first, X candidates, then others', () => {
  const groups = groupByBand([
    statusFixture('zf102', 'UNAVAILABLE'),
    statusFixture('z9591'),
    statusFixture('synthetic_radar_a'),
    statusFixture('zf101', 'UNAVAILABLE'),
    statusFixture('z9593'),
  ])
  expect(groups.map(group => group.key)).toEqual(['S', 'X', 'other'])
  expect(groups[0].statuses.map(status => status.radar_id)).toEqual(['z9591', 'z9593'])
  expect(groups[1].statuses).toHaveLength(2)
  expect(groups[1].collapsedByDefault).toBe(true)
  expect(groups[0].collapsedByDefault).toBe(false)
})

it('renders band sections and collapses the X-band group by default', () => {
  render(<RadarStatusStrip
    statuses={[
      statusFixture('z9591'),
      statusFixture('zf101', 'UNAVAILABLE'),
      statusFixture('zf102', 'UNAVAILABLE'),
    ]}
    ingest={null}
  />)
  expect(screen.getByText('S 波段')).toBeTruthy()
  expect(screen.getByText('X 波段候选')).toBeTruthy()
  expect(screen.getAllByText('Z9591').length).toBeGreaterThanOrEqual(1)
  // X-band candidates stay hidden until the group is opened.
  expect(screen.queryByText('ZF101')).toBeNull()
  fireEvent.click(screen.getByRole('button', { name: /X 波段候选/ }))
  expect(screen.getAllByText('ZF101').length).toBeGreaterThanOrEqual(1)
  expect(screen.getByText('2 不可用')).toBeTruthy()
})
