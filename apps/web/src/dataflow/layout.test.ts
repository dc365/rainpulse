import { afterEach, describe, expect, it, vi } from 'vitest'
import {
  failureSummary,
  GAP_THRESHOLD_MS,
  HEADROOM_MINUTES,
  PX_PER_MIN,
  VIEW_MIN_MINUTES,
  axisTicks,
  delayTone,
  eventToBlockKey,
  formatDuration,
  gapRanges,
  laneGapThreshold,
  panView,
  scanStatusLabel,
  segmentPlan,
  stageLabel,
  stageSegments,
  windowSpec,
  xForTime,
  zoomView,
} from './layout'
import type { DataflowJobStage } from './types'

afterEach(() => vi.restoreAllMocks())

const NOW = Date.parse('2026-10-08T04:30:00Z')

function minutesAgo(minutes: number): string {
  return new Date(NOW - minutes * 60_000).toISOString()
}

describe('windowSpec', () => {
  it('adds headroom so the live head is not pinned to the edge', () => {
    const spec = windowSpec(60, NOW)
    expect(spec.start).toBe(NOW - 60 * 60_000)
    expect(spec.end).toBe(NOW + HEADROOM_MINUTES * 60_000)
    expect(spec.width).toBe((60 + HEADROOM_MINUTES) * PX_PER_MIN)
  })

  it('maps wall-clock time onto the pixel axis', () => {
    const spec = windowSpec(60, NOW)
    expect(xForTime(spec.start, spec)).toBe(0)
    expect(xForTime(NOW, spec)).toBe(60 * PX_PER_MIN)
  })
})

describe('stageSegments', () => {
  it('positions finished stages by their own timestamps and running stages up to now', () => {
    const stages: DataflowJobStage[] = [
      { stage: 'decode', job_id: 'a', status: 'SUCCEEDED', created_at: minutesAgo(8), started_at: minutesAgo(8), finished_at: minutesAgo(7) },
      { stage: 'qc', job_id: 'b', status: 'RUNNING', created_at: minutesAgo(7), started_at: minutesAgo(7) },
    ]
    const spec = windowSpec(60, NOW)
    const segments = stageSegments(stages, spec, NOW)
    expect(segments[0].state).toBe('done')
    expect(segments[0].left).toBe(52 * PX_PER_MIN) // 8 minutes before now on a 60-minute window
    expect(segments[0].width).toBeCloseTo(PX_PER_MIN, 5) // one minute of runtime
    expect(segments[1].state).toBe('running')
    expect(segments[1].left + segments[1].width).toBeCloseTo(60 * PX_PER_MIN, 5)
  })

  it('keeps queued stages visible at the activity cursor instead of collapsing', () => {
    const stages: DataflowJobStage[] = [
      { stage: 'qc', job_id: 'a', status: 'SUCCEEDED', created_at: minutesAgo(5), started_at: minutesAgo(5), finished_at: minutesAgo(4) },
      { stage: 'grid', job_id: 'b', status: 'PENDING', created_at: minutesAgo(4) },
    ]
    const segments = stageSegments(stages, windowSpec(60, NOW), NOW)
    expect(segments[1].state).toBe('queued')
    expect(segments[1].width).toBeGreaterThanOrEqual(2)
    expect(segments[1].left).toBeGreaterThan(segments[0].left)
  })
})

describe('segmentPlan timed vs slot', () => {
  it('keeps timestamp layout when jobs run near the volume time', () => {
    const volumeEnd = minutesAgo(7)
    const stages: DataflowJobStage[] = [
      { stage: 'decode', job_id: 'a', status: 'SUCCEEDED', created_at: minutesAgo(7), started_at: minutesAgo(6.9), finished_at: minutesAgo(6.8) },
    ]
    const plan = segmentPlan(stages, windowSpec(60, NOW), NOW, Date.parse(volumeEnd))
    expect(plan.mode).toBe('timed')
  })

  it('falls back to duration-share layout for backfilled stages', () => {
    const volumeEnd = '2026-08-28T00:08:00Z'
    const stages: DataflowJobStage[] = [
      { stage: 'decode', job_id: 'a', status: 'SUCCEEDED', created_at: '2026-09-18T09:00:00Z', started_at: '2026-09-18T09:00:00Z', finished_at: '2026-09-18T09:00:09Z', runtime_ms: 9000 },
      { stage: 'qc', job_id: 'b', status: 'FAILED', created_at: '2026-09-22T00:35:00Z', started_at: '2026-09-22T00:35:23Z', finished_at: '2026-09-22T00:42:48Z', runtime_ms: 445000 },
    ]
    const plan = segmentPlan(stages, windowSpec(60, Date.parse('2026-08-28T01:00:00Z')), Date.parse('2026-08-28T01:00:00Z'), Date.parse(volumeEnd))
    if (plan.mode !== 'slot') throw new Error(`expected slot plan, got ${plan.mode}`)
    expect(plan.segments[0].widthPct).toBeLessThan(plan.segments[1].widthPct)
    const total = plan.segments.reduce((sum, segment) => sum + segment.widthPct, 0)
    expect(total).toBeCloseTo(100, 3)
  })
})

describe('gapRanges', () => {
  it('marks silence longer than the volume-scan beat as 断流', () => {
    const start = NOW - 60 * 60_000
    const end = NOW + 2 * 60_000
    const regular = Array.from({ length: 10 }, (_, i) => NOW - (i + 1) * 6 * 60_000)
    expect(gapRanges(regular, start, end)).toHaveLength(0)
    const withHole = [...regular.filter((_, i) => i < 3), ...regular.filter((_, i) => i > 6)]
    const gaps = gapRanges(withHole, start, end)
    expect(gaps.length).toBeGreaterThanOrEqual(1)
    expect(gaps[0].minutes).toBeGreaterThanOrEqual(12)
  })

  it('counts the leading window as silence only when it is long', () => {
    const start = NOW - 60 * 60_000
    const end = NOW + 2 * 60_000
    const early = [NOW - 55 * 60_000, NOW - 6 * 60_000]
    const gaps = gapRanges(early, start, end)
    expect(gaps.some(gap => gap.minutes >= 40)).toBe(true)
    const late = [NOW - 8 * 60_000]
    expect(gapRanges(late, start, end)).toHaveLength(1)
  })

  it('adapts the silence threshold to the lane observed cadence', () => {
    const sparse = Array.from({ length: 8 }, (_, i) => NOW - (i + 1) * 30 * 60_000)
    // Sparse 30-minute cadence raises the threshold, clamped at 30 minutes.
    expect(laneGapThreshold(sparse)).toBe(30 * 60_000)
    // A dense six-minute lane keeps a tight threshold; too few scans keep default.
    expect(laneGapThreshold(Array.from({ length: 8 }, (_, i) => NOW - (i + 1) * 6 * 60_000))).toBe(15 * 60_000)
    expect(laneGapThreshold([NOW - 6 * 60_000])).toBe(GAP_THRESHOLD_MS)
  })
})

describe('axisTicks', () => {
  it('aligns to the fixed six-minute UTC beat and labels in CST', () => {
    const spec = windowSpec(60, NOW) // 04:30Z -> CST 12:30
    const ticks = axisTicks(spec)
    expect(ticks.length).toBeGreaterThan(8)
    // The window opens at 03:30Z, itself a six-minute boundary -> 11:30 CST.
    expect(ticks[0].label).toBe('11:30')
    expect(ticks[0].major).toBe(false)
    // Hour boundaries are the major ticks: 04:00Z -> 12:00 CST.
    const noon = ticks.find(tick => tick.label === '12:00')
    expect(noon?.major).toBe(true)
    for (let index = 1; index < ticks.length; index += 1) {
      expect(ticks[index].left).toBeGreaterThan(ticks[index - 1].left)
    }
  })
})

describe('formatters', () => {
  it('formats durations in human units', () => {
    expect(formatDuration(null)).toBe('—')
    expect(formatDuration(850)).toBe('850 ms')
    expect(formatDuration(41_000)).toBe('41 s')
    expect(formatDuration(150_000)).toBe('2.5 min')
  })

  it('tones data delay by volume-scan cadence', () => {
    expect(delayTone(60)).toBe('ok')
    expect(delayTone(500)).toBe('warn')
    expect(delayTone(900)).toBe('risk')
    expect(delayTone(undefined)).toBe('unknown')
  })

  it('keeps Chinese stage and scan status labels', () => {
    expect(stageLabel('qc')).toBe('质控')
    expect(stageLabel('pysteps_lk')).toBe('pySTEPS-LK')
    expect(scanStatusLabel('QC_RUNNING')).toBe('质控中')
    expect(scanStatusLabel('RADAR_GRID_READY')).toBe('链路完成')
  })
})

describe('timeline view math', () => {
  const bounds = { start: Date.parse('2026-08-28T10:00:00Z'), end: Date.parse('2026-08-28T11:00:00Z') }
  const full = { ...bounds }

  it('zooms around the cursor and clamps to bounds', () => {
    const view = zoomView(full, Date.parse('2026-08-28T10:30:00Z'), 0.5, bounds)
    expect(view.end - view.start).toBe(30 * 60_000)
    // The focus time stays under the cursor.
    const focusRatio = (Date.parse('2026-08-28T10:30:00Z') - view.start) / (view.end - view.start)
    expect(focusRatio).toBeCloseTo(0.5, 5)
  })

  it('never zooms below the minimum or above the fetched window', () => {
    let view = full
    for (let i = 0; i < 20; i += 1) view = zoomView(view, bounds.start, 0.5, bounds)
    expect(view.end - view.start).toBe(VIEW_MIN_MINUTES * 60_000)
    // A nearly-full view may only grow to the fetched window itself.
    expect(zoomView({ start: bounds.start + 1, end: bounds.end }, bounds.start, 2, bounds))
      .toEqual(bounds)
  })

  it('pans within the bounds without resizing', () => {
    const view = zoomView(full, Date.parse('2026-08-28T10:15:00Z'), 0.5, bounds)
    const panned = panView(view, 20 * 60_000, bounds)
    expect(panned.end - panned.start).toBe(view.end - view.start)
    expect(panned.end).toBeLessThanOrEqual(bounds.end)
    const clamped = panView(panned, 60 * 60_000, bounds)
    expect(clamped.end).toBe(bounds.end)
  })
})

describe('eventToBlockKey', () => {
  const payload = {
    radar_lanes: [{
      radar_id: 'z9591',
      blocks: [
        { scan_id: 's-1030', volume_start: '2026-08-28T10:25:00Z', volume_end: '2026-08-28T10:30:00Z' },
        { scan_id: 's-1036', volume_start: '2026-08-28T10:31:00Z', volume_end: '2026-08-28T10:36:00Z' },
      ],
    }],
    analysis_blocks: [{ analysis_id: 'a-1036', analysis_time: '2026-08-28T10:36:00Z' }],
    forecast_blocks: [{ run_id: 'f-1036', issue_time: '2026-08-28T10:36:00Z' }],
  }

  it('maps a radar job event to the volume containing the moment', () => {
    expect(eventToBlockKey(payload, 'z9591', Date.parse('2026-08-28T10:33:00Z'))).toBe('s-1036')
  })

  it('maps an analysis event to the nearest analysis anchor', () => {
    expect(eventToBlockKey(payload, undefined, Date.parse('2026-08-28T10:36:30Z'))).toBe('a-1036')
  })

  it('returns null beyond ten minutes from any anchor', () => {
    expect(eventToBlockKey(payload, 'z9593', Date.parse('2026-08-28T10:33:00Z'))).toBeNull()
  })
})

describe('failureSummary', () => {
  const payload = {
    radar_lanes: [{
      blocks: [
        { status: 'RADAR_GRID_READY', stages: [{ status: 'SUCCEEDED', error_code: '' }] },
        { status: 'FAILED', degraded_reason: 'qc_failed', stages: [
          { status: 'SUCCEEDED', error_code: '' },
          { status: 'FAILED', error_code: 'qc_timeout' },
        ] },
        { status: 'QC_RUNNING', stages: [{ status: 'FAILED', error_code: 'qc_timeout' }] },
        { status: 'RADAR_GRID_READY', stages: [{ status: 'FAILED', error_code: '' }] },
      ],
    }],
    analysis_blocks: [{ status: 'FAILED', degraded_reason: '', stages: [{ status: 'FAILED', error_code: 'mosaic_error' }] }],
    forecast_blocks: [],
  }

  it('groups failed blocks by error code, sorted by count', () => {
    const groups = failureSummary(payload)
    expect(groups[0]).toEqual({ code: 'qc_timeout', count: 2 })
    const counts = Object.fromEntries(groups.map(group => [group.code, group.count]))
    expect(counts).toEqual({
      qc_timeout: 2,
      未标注: 2,
      qc_failed: 1,
      mosaic_error: 1,
    })
  })

  it('returns empty when nothing failed', () => {
    expect(failureSummary({
      radar_lanes: [{ blocks: [{ status: 'RADAR_GRID_READY', stages: [{ status: 'SUCCEEDED', error_code: '' }] }] }],
      analysis_blocks: [],
      forecast_blocks: [],
    })).toEqual([])
  })
})
