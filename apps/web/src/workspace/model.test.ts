import { describe, expect, it } from 'vitest'

import {
	analysisCycleAt,
	frameAt,
	panelsForPreset,
	qcFlagLabel,
	timelineForPreset,
	radarIDs,
	type CycleSummary,
	type WorkspaceCycleDetail,
} from './model'

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
    { panel_id: 'analysis:qc_flags', algorithm_id: 'radar', display_name: '融合质控标志', role: 'diagnostic', lifecycle: 'analysis', data_kind: 'diagnostic', cadence_minutes: 5, status: 'ready', frames: [] },
  ],
}

describe('workspace model', () => {
  it('keeps a stable forecast panel order', () => {
    expect(panelsForPreset(detail, 'forecast', null).map((panel) => panel.panel_id))
      .toEqual(['qpe', 'lk', 'steps', 'nowcastnet'])
  })

  it('does not interpolate a ten-minute model onto a five-minute time', () => {
    const panel = detail.panels.find((item) => item.panel_id === 'nowcastnet')!
    expect(frameAt(panel, '2026-09-01T01:05:00Z')).toBeNull()
    expect(frameAt(panel, '2026-09-01T01:10:00Z')?.asset_id).toBe('nc10')
  })

  it('selects raw and QC evidence for one radar', () => {
    expect(radarIDs(detail)).toEqual(['z9591'])
    expect(panelsForPreset(detail, 'qc', 'z9591').map((panel) => panel.panel_id))
      .toEqual(['dbzh_raw:z9591', 'dbzh_qc:z9591', 'qc_flags:z9591', 'qpe'])
  })

  it('falls back to fused QC flags only when a radar flag layer is absent', () => {
    const withoutRadarFlags = {
      ...detail,
      panels: detail.panels.filter((panel) => panel.panel_id !== 'qc_flags:z9591'),
    }
    expect(panelsForPreset(withoutRadarFlags, 'qc', 'z9591').map((panel) => panel.panel_id))
      .toEqual(['dbzh_raw:z9591', 'dbzh_qc:z9591', 'analysis:qc_flags', 'qpe'])
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
    expect(timelineForPreset(detail, cycles, 'qc'))
      .toEqual(['2026-09-01T01:00:00Z', '2026-09-01T01:05:00Z'])
    expect(timelineForPreset(detail, cycles, 'forecast')).toEqual(detail.timeline)
  })
})
