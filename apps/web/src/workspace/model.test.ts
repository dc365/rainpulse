import { describe, expect, it } from 'vitest'

import {
	analysisCycleAt,
	availableQCEvidenceLayers,
	displayFrameAt,
	frameAt,
	panelsForPreset,
	qcFlagLabel,
	timelineForPreset,
	radarIDs,
	type CycleSummary,
	type WorkspaceCycleDetail,
} from './model'
import { isTimelineGridTime } from './cadence'

const detail: WorkspaceCycleDetail = {
  schema_version: '1.0',
  cycle_id: 'cycle',
  issue_time: '2026-09-01T01:00:00Z',
  grid_id: 'fuzhou_118_123_25_27_0p01deg_v1',
  execution_mode: 'realtime_shadow',
  freshness_seconds: 30,
  capabilities: { radar: true, lk: true, steps: true, nowcastnet: false },
  grid: {
    grid_id: 'fuzhou_118_123_25_27_0p01deg_v1',
    bounds: [118, 25, 123, 27],
    raster_bounds: [117.995, 24.995, 123.005, 27.005],
  },
  quality: {},
  radars: [{ radar_id: 'z9591', state: 'PARTICIPATING' }],
  timeline: ['2026-09-01T01:00:00Z', '2026-09-01T01:05:00Z', '2026-09-01T01:10:00Z'],
  panels: [
    { panel_id: 'qpe', algorithm_id: 'radar', display_name: 'QPE', role: 'observation', lifecycle: 'analysis', data_kind: 'rain_rate', cadence_minutes: 5, status: 'ready', frames: [{ asset_id: 'qpe', valid_time: '2026-09-01T01:00:00Z', lead_time_minutes: 0, image_url: '/qpe', media_type: 'image/png' }] },
    { panel_id: 'lk', algorithm_id: 'lk', display_name: 'LK', role: 'forecast', lifecycle: 'shadow', data_kind: 'rain_rate', cadence_minutes: 5, status: 'ready', frames: [{ asset_id: 'lk5', valid_time: '2026-09-01T01:05:00Z', lead_time_minutes: 5, image_url: '/lk5', media_type: 'image/png' }] },
    { panel_id: 'steps', algorithm_id: 'steps', display_name: 'STEPS', role: 'forecast', lifecycle: 'offline', data_kind: 'quantile', cadence_minutes: 5, status: 'unavailable', frames: [] },
    { panel_id: 'nowcastnet', algorithm_id: 'nowcastnet', display_name: 'NowcastNet', role: 'forecast', lifecycle: 'shadow', data_kind: 'rain_rate', cadence_minutes: 10, status: 'ready', frames: [{ asset_id: 'nc10', valid_time: '2026-09-01T01:10:00Z', lead_time_minutes: 10, image_url: '/nc10', media_type: 'image/png' }] },
    { panel_id: 'dbzh_raw:z9591', algorithm_id: 'radar', display_name: 'Raw', role: 'qc', lifecycle: 'analysis', data_kind: 'reflectivity', cadence_minutes: 5, status: 'ready', radar_id: 'z9591', frames: [] },
    { panel_id: 'dbzh_qc:z9591', algorithm_id: 'radar', display_name: 'QC', role: 'qc', lifecycle: 'analysis', data_kind: 'reflectivity', cadence_minutes: 5, status: 'ready', radar_id: 'z9591', frames: [] },
    { panel_id: 'qc_flags:z9591', algorithm_id: 'radar', display_name: 'Z9591 · 质控标志', role: 'qc', lifecycle: 'analysis', data_kind: 'reflectivity', cadence_minutes: 5, status: 'ready', radar_id: 'z9591', frames: [] },
    { panel_id: 'analysis:dbzh_qc', algorithm_id: 'radar-analysis', display_name: '质控反射率', role: 'diagnostic', lifecycle: 'analysis', data_kind: 'diagnostic', cadence_minutes: 6, status: 'ready', frames: [] },
    { panel_id: 'analysis:source_radar', algorithm_id: 'radar-analysis', display_name: '来源雷达', role: 'diagnostic', lifecycle: 'analysis', data_kind: 'diagnostic', cadence_minutes: 6, status: 'ready', frames: [] },
    { panel_id: 'analysis:quality_index', algorithm_id: 'radar-analysis', display_name: '综合质量指数', role: 'diagnostic', lifecycle: 'analysis', data_kind: 'diagnostic', cadence_minutes: 6, status: 'ready', frames: [] },
    { panel_id: 'analysis:qc_flags', algorithm_id: 'radar', display_name: '融合质控标志', role: 'diagnostic', lifecycle: 'analysis', data_kind: 'diagnostic', cadence_minutes: 5, status: 'ready', frames: [] },
  ],
}

describe('workspace model', () => {
  it('shows observed history in forecast panels without carrying T0 backwards', () => {
    const at = '2026-09-01T00:54:00Z'
    const qpe = detail.panels[0]
    const observation = { ...qpe.frames[0], asset_id: 'past-qpe', valid_time: at, lead_time_minutes: -6 }
    const history = { ...detail, panels: [{ ...qpe, frames: [...qpe.frames, observation] }, ...detail.panels.slice(1)] }
    for (const panel of history.panels.filter(p => p.role === 'forecast')) {
      expect(displayFrameAt(history, panel, at)).toEqual({ frame: observation, usesAnalysisBaseline: true })
      expect(displayFrameAt(history, panel, '2026-09-01T00:48:00Z').frame).toBeNull()
    }
  })

  it('keeps a stable forecast panel order', () => {
    expect(panelsForPreset(detail, 'forecast', null).map((panel) => panel.panel_id))
      .toEqual(['qpe', 'lk', 'steps', 'nowcastnet'])
  })

  it('does not interpolate a ten-minute model onto a five-minute time', () => {
    const panel = detail.panels.find((item) => item.panel_id === 'nowcastnet')!
    expect(frameAt(panel, '2026-09-01T01:05:00Z')).toBeNull()
    expect(frameAt(panel, '2026-09-01T01:10:00Z')?.asset_id).toBe('nc10')
  })

  it('uses the QPE analysis field as the T0 baseline for every forecast panel', () => {
    const qpe = detail.panels.find((item) => item.panel_id === 'qpe')!
    for (const panelID of ['lk', 'steps', 'nowcastnet']) {
      const panel = detail.panels.find((item) => item.panel_id === panelID)!
      const selection = displayFrameAt(detail, panel, detail.issue_time)

      expect(selection.frame?.asset_id).toBe(qpe.frames[0].asset_id)
      expect(selection.usesAnalysisBaseline).toBe(true)
    }

    const lk = detail.panels.find((item) => item.panel_id === 'lk')!
    expect(displayFrameAt(detail, lk, detail.timeline[1])).toEqual({
      frame: lk.frames[0],
      usesAnalysisBaseline: false,
    })
  })

  it('puts the RP-010 mosaic in the QC evidence slot by default', () => {
    expect(radarIDs(detail)).toEqual(['z9591'])
    expect(panelsForPreset(detail, 'qc', 'z9591').map((panel) => panel.panel_id))
      .toEqual(['dbzh_raw:z9591', 'dbzh_qc:z9591', 'analysis:dbzh_qc', 'qpe'])
  })

  it('switches only the evidence slot to the flag layer on demand', () => {
    expect(panelsForPreset(detail, 'qc', 'z9591', 'lk', 'flags').map((panel) => panel.panel_id))
      .toEqual(['dbzh_raw:z9591', 'dbzh_qc:z9591', 'qc_flags:z9591', 'qpe'])
  })

  it('degrades the mosaic slot to the flag layer when the mosaic is absent', () => {
    const withoutMosaic = {
      ...detail,
      panels: detail.panels.filter((panel) => panel.panel_id !== 'analysis:dbzh_qc'),
    }
    expect(panelsForPreset(withoutMosaic, 'qc', 'z9591').map((panel) => panel.panel_id))
      .toEqual(['dbzh_raw:z9591', 'dbzh_qc:z9591', 'qc_flags:z9591', 'qpe'])
    const withoutRadarFlags = {
      ...withoutMosaic,
      panels: withoutMosaic.panels.filter((panel) => panel.panel_id !== 'qc_flags:z9591'),
    }
    expect(panelsForPreset(withoutRadarFlags, 'qc', 'z9591').map((panel) => panel.panel_id))
      .toEqual(['dbzh_raw:z9591', 'dbzh_qc:z9591', 'analysis:qc_flags', 'qpe'])
  })

  it('offers only evidence layers the cycle can actually draw', () => {
    expect(availableQCEvidenceLayers(detail, 'z9591')).toEqual(['mosaic', 'flags'])
    const flagsOnly = {
      ...detail,
      panels: detail.panels.filter((panel) => ![
        'analysis:dbzh_qc', 'analysis:source_radar', 'analysis:quality_index', 'qc_flags:z9591',
      ].includes(panel.panel_id)),
    }
    expect(availableQCEvidenceLayers(flagsOnly, 'z9591')).toEqual(['flags'])
  })

  it('localizes QC flag codes without changing unknown values', () => {
    expect(qcFlagLabel('GROUND_CLUTTER')).toBe('地物杂波')
    expect(qcFlagLabel('BRIGHT_BAND')).toBe('零度层亮带')
    expect(qcFlagLabel('UNKNOWN_FLAG')).toBe('UNKNOWN_FLAG')
  })

  it('maps a QC effective time to its complete radar analysis cycle', () => {
    const cycles: CycleSummary[] = [
      {
        cycle_id: 'cycle-current',
        issue_time: detail.issue_time,
        grid_id: detail.grid_id,
        execution_mode: 'realtime_shadow',
        freshness_seconds: 0,
        capabilities: { radar: true, lk: false, steps: false, nowcastnet: false },
      },
      {
        cycle_id: 'cycle-next',
        issue_time: '2026-09-01T01:05:00Z',
        grid_id: detail.grid_id,
        execution_mode: 'realtime_shadow',
        freshness_seconds: 0,
        capabilities: { radar: true, lk: false, steps: false, nowcastnet: false },
      },
    ]

    expect(analysisCycleAt(cycles, detail.grid_id, '2026-09-01T01:05:00Z')?.cycle_id)
      .toBe('cycle-next')
    const qcTimeline = timelineForPreset(detail, cycles, 'qc')
    expect(qcTimeline).toEqual(['2026-09-01T01:00:00Z'])
    expect(timelineForPreset(detail, cycles, 'forecast')).toEqual(detail.timeline)
  })

  it('offers only six-minute QC slots that already have a complete analysis cycle', () => {
    const qc = (issue_time: string, cycle_id: string): CycleSummary => ({
      cycle_id, issue_time, grid_id: detail.grid_id, execution_mode: 'historical',
      freshness_seconds: 0, capabilities: { radar: true, lk: false, steps: false, nowcastnet: false },
    })
    const cycles = [
      qc('2026-09-01T01:00:00Z', 'qc-now'),
      qc('2026-09-01T01:10:00Z', 'qc-later'),
    ]

    const qcTimeline = timelineForPreset(detail, cycles, 'qc')
    expect(qcTimeline).toEqual(['2026-09-01T01:00:00Z'])
    expect(qcTimeline.every(value => isTimelineGridTime(value))).toBe(true)
    expect(qcTimeline).not.toContain('2026-09-01T01:05:00Z')
  })

  it('drops QC slots that are not aligned to the six-minute timeline', () => {
    const issue = '2026-08-28T09:00:00Z'
    const at = (minutes: number) => new Date(Date.parse(issue) + minutes * 60_000).toISOString().replace('.000Z', 'Z')
    const qc = (minutes: number, cycle_id: string): CycleSummary => ({
      cycle_id, issue_time: at(minutes), grid_id: detail.grid_id, execution_mode: 'historical',
      freshness_seconds: 0, capabilities: { radar: true, lk: false, steps: false, nowcastnet: false },
    })
    // 09:05 is a legacy five-minute cycle, 09:06 is its six-minute slot and
    // 09:15 is a legacy cycle without any six-minute analysis cycle.
    const cycles = [qc(5, 'qc-legacy'), qc(6, 'qc-on-grid'), qc(15, 'qc-off-grid')]
    const irregular = {
      ...detail,
      issue_time: issue,
      // The QC rail shares the detail timeline, including off-grid legacy slots.
      timeline: [at(-54), at(-48), at(0), at(5), at(6), at(15)],
    }

    const qcTimeline = timelineForPreset(irregular, cycles, 'qc')
    expect(qcTimeline).toEqual([at(0), at(6)])
    const legacyCycle = { ...detail, issue_time: at(15), timeline: [at(15), at(20), at(21)] }
    expect(timelineForPreset(legacyCycle, cycles, 'qc')).toEqual([at(15)])
    expect(qcTimeline.every(value => isTimelineGridTime(value))).toBe(true)
    expect(qcTimeline).not.toContain(at(5))
    expect(timelineForPreset(irregular, cycles, 'forecast')).toEqual(irregular.timeline)
  })
})

it('pairs only matching radar/time/sweep frames and never substitutes another elevation', async () => {
  const { qcSweepOptions, withQCSweep } = await import('./model')
  const time = '2026-08-28T00:12:00Z'
  const frame = (sweep: number) => ({asset_id: `${sweep}`, valid_time: time, lead_time_minutes: 0,
    image_url: `/${sweep}`, media_type: 'image/png', sweep_number: sweep, elevation_deg: 0.5, scan_id: 'a'})
  const panel = (id: string, sweeps: number[]) => ({panel_id: id, radar_id: 'r', role: 'qc' as const,
    algorithm_id: 'radar', display_name: id, lifecycle: 'analysis' as const, data_kind: 'reflectivity',
    cadence_minutes: 6, status: 'ready' as const, frames: sweeps.map(frame)})
  const panels = [panel('dbzh_raw:r', [0, 2, 4]), panel('dbzh_qc:r', [0, 2])]
  const d = {panels} as WorkspaceCycleDetail
  expect(qcSweepOptions(d, 'r', time).map(sweep => sweep.number)).toEqual([0, 2])
  expect(qcSweepOptions(d, 'r', '2026-08-28T00:18:00Z')).toEqual([])
  expect(withQCSweep(panels, 'r', 2).map(p => p.frames[0].image_url)).toEqual(['/2', '/2'])
  expect(withQCSweep(panels, 'r', 4)[1].frames).toEqual([])
})
