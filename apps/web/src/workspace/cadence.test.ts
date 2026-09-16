import { describe, expect, it } from 'vitest'

import { isTimelineGridTime, TIMELINE_STEP_MINUTES } from './cadence'

describe('workbench cadence', () => {
  it('keeps the shared timeline step at six minutes', () => {
    expect(TIMELINE_STEP_MINUTES).toBe(6)
  })

  it('accepts only zero-second times on a six-minute boundary', () => {
    expect(isTimelineGridTime('2026-08-28T00:00:00Z')).toBe(true)
    expect(isTimelineGridTime('2026-08-28T00:06:00Z')).toBe(true)
    expect(isTimelineGridTime('2026-08-28T23:54:00Z')).toBe(true)
    expect(isTimelineGridTime('2026-08-28T00:05:00Z')).toBe(false)
    expect(isTimelineGridTime('2026-08-28T00:10:00Z')).toBe(false)
    expect(isTimelineGridTime('2026-08-28T00:06:30Z')).toBe(false)
    expect(isTimelineGridTime('not-a-time')).toBe(false)
  })
})
