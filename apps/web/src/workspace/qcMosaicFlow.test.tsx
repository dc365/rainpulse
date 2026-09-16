import { cleanup, fireEvent, render, screen, within } from '@testing-library/react'
import { afterEach, expect, it, vi } from 'vitest'

import { MainWorkspace } from './MainWorkspace'
import type { CycleSummary, WorkspaceCycleDetail } from './model'

vi.mock('ol/Map.js', async () => {
  const { default: Observable } = await import('ol/Observable.js')
  return { default: class extends Observable {
    view: unknown
    viewport = document.createElement('div')
    constructor(options: { view: unknown }) { super(); this.view = options.view }
    getView() { return this.view }
    getViewport() { return this.viewport }
    setTarget() {}
    updateSize() {}
    addOverlay() {}
  } }
})

const issueTime = '2026-09-16T01:00:00Z'
const bounds: [number, number, number, number] = [117.995, 24.995, 123.005, 27.005]
const frame = (assetID: string, unit: string) => ({
  asset_id: assetID, valid_time: issueTime, lead_time_minutes: 0,
  image_url: `/${assetID}.png`, media_type: 'image/png', unit, bounds,
})

const detail: WorkspaceCycleDetail = {
  schema_version: '1.0',
  cycle_id: 'cycle-qc',
  issue_time: issueTime,
  grid_id: 'fuzhou_118_123_25_27_0p01deg_v1',
  execution_mode: 'historical',
  freshness_seconds: 0,
  capabilities: { radar: true, lk: true, steps: true, nowcastnet: false },
  grid: { grid_id: 'fuzhou_118_123_25_27_0p01deg_v1', bounds: [118, 25, 123, 27], raster_bounds: bounds },
  quality: { coverage_ratio: 0.76, mean_quality_index: 0.87, maximum_rate_mm_h: 42.5 },
  radars: [
    { radar_id: 'z9591', state: 'PARTICIPATING' },
    { radar_id: 'z9598', state: 'PARTICIPATING' },
    { radar_id: 'z9599', state: 'EXCLUDED' },
  ],
  timeline: [issueTime],
  panels: [
    { panel_id: 'qpe', algorithm_id: 'radar-analysis', display_name: '雷达 QPE', role: 'observation', lifecycle: 'analysis', data_kind: 'rain_rate', cadence_minutes: 6, status: 'ready', legend_unit: 'mm/h', frames: [frame('qpe', 'mm/h')] },
    { panel_id: 'dbzh_raw:z9591', algorithm_id: 'radar-analysis', display_name: 'Z9591 原始反射率', role: 'qc', lifecycle: 'analysis', data_kind: 'reflectivity', cadence_minutes: 6, status: 'ready', radar_id: 'z9591', legend_unit: 'dBZ', frames: [frame('raw-z9591', 'dBZ')] },
    { panel_id: 'dbzh_qc:z9591', algorithm_id: 'radar-analysis', display_name: 'Z9591 业务质控反射率', role: 'qc', lifecycle: 'analysis', data_kind: 'reflectivity', cadence_minutes: 6, status: 'ready', radar_id: 'z9591', legend_unit: 'dBZ', frames: [frame('qc-z9591', 'dBZ')] },
    { panel_id: 'qc_flags:z9591', algorithm_id: 'radar-analysis', display_name: 'Z9591 质控标志', role: 'qc', lifecycle: 'analysis', data_kind: 'reflectivity', cadence_minutes: 6, status: 'ready', radar_id: 'z9591', frames: [frame('flags-z9591', '')] },
    { panel_id: 'analysis:dbzh_qc', algorithm_id: 'radar-analysis', display_name: '质控反射率', role: 'diagnostic', lifecycle: 'analysis', data_kind: 'diagnostic', cadence_minutes: 6, status: 'ready', legend_unit: 'dBZ', frames: [frame('mosaic', 'dBZ')] },
    { panel_id: 'analysis:source_radar', algorithm_id: 'radar-analysis', display_name: '来源雷达', role: 'diagnostic', lifecycle: 'analysis', data_kind: 'diagnostic', cadence_minutes: 6, status: 'ready', frames: [frame('source-radar', '')] },
    { panel_id: 'analysis:quality_index', algorithm_id: 'radar-analysis', display_name: '综合质量指数', role: 'diagnostic', lifecycle: 'analysis', data_kind: 'diagnostic', cadence_minutes: 6, status: 'ready', legend_unit: '1', frames: [frame('quality-index', '1')] },
    { panel_id: 'analysis:qc_flags', algorithm_id: 'radar-analysis', display_name: '融合质控标志', role: 'diagnostic', lifecycle: 'analysis', data_kind: 'diagnostic', cadence_minutes: 6, status: 'ready', frames: [frame('grid-flags', '')] },
  ],
}

const cycle: CycleSummary = {
  cycle_id: 'cycle-qc', issue_time: issueTime, grid_id: detail.grid_id,
  execution_mode: 'historical', freshness_seconds: 0,
  capabilities: { radar: true, lk: true, steps: true, nowcastnet: false },
}

const { state } = vi.hoisted(() => ({
  state: {
    cycles: [] as CycleSummary[], detail: null as WorkspaceCycleDetail | null,
    selectedTime: null as string | null, loading: false, mode: 'pinned',
    catalogError: null as string | null, detailError: null as string | null, stale: false,
  },
}))
vi.mock('./useWorkspaceData', () => ({
  useWorkspaceData: () => ({
    state, now: 0, connection: 'connected', refresh: vi.fn(), requestCycle: vi.fn(),
    setTime: vi.fn(), pin: vi.fn(), follow: vi.fn(),
  }),
}))

afterEach(() => { cleanup(); vi.clearAllMocks(); vi.unstubAllGlobals() })

function captionTitles() {
  return Array.from(document.querySelectorAll('.workspace-map-caption strong')).map(node => node.textContent)
}

it('puts the radar mosaic in the lower-left QC slot and keeps the flag layer one click away', () => {
  vi.stubGlobal('ResizeObserver', class { observe() {} disconnect() {} })
  state.cycles = [cycle]
  state.detail = detail
  state.selectedTime = issueTime
  render(<MainWorkspace />)
  fireEvent.click(screen.getByRole('tab', { name: '质控排查' }))

  expect(document.querySelectorAll('.workspace-map-panel')).toHaveLength(4)
  expect(captionTitles()).toEqual(['Z9591 原始反射率', 'Z9591 质控后反射率', '雷达拼图', '雷达 QPE'])
  const mosaicCaption = document.querySelectorAll('.workspace-map-caption')[2]
  expect(within(mosaicCaption as HTMLElement).getByText(/2 站参与/)).toBeTruthy()
  expect(within(mosaicCaption as HTMLElement).getByText('多雷达拼图')).toBeTruthy()

  const evidence = screen.getByRole('group', { name: '质控证据图层' })
  expect(within(evidence).getAllByRole('button').map(button => button.textContent))
    .toEqual(['雷达拼图', '质控标志'])
  expect(within(evidence).getByRole('button', { name: '雷达拼图' }).getAttribute('aria-pressed')).toBe('true')

  // The single-map menu follows the preset, so the mosaic must be reachable there too.
  fireEvent.click(document.querySelector('.workspace-focus-trigger') as HTMLElement)
  expect(Array.from(document.querySelectorAll('.workspace-focus-menu button strong')).map(node => node.textContent))
    .toEqual(['Z9591 原始反射率', 'Z9591 质控后反射率', '雷达拼图', '雷达 QPE'])
  fireEvent.click(document.querySelector('.workspace-focus-trigger') as HTMLElement)

  fireEvent.click(within(evidence).getByRole('button', { name: '质控标志' }))
  expect(captionTitles()).toEqual(['Z9591 原始反射率', 'Z9591 质控后反射率', 'Z9591 质控标志', '雷达 QPE'])
  expect(within(evidence).getByRole('button', { name: '质控标志' }).getAttribute('aria-pressed')).toBe('true')
})

it('keeps the four-map layout when a cycle has no mosaic layer', () => {
  vi.stubGlobal('ResizeObserver', class { observe() {} disconnect() {} })
  state.cycles = [cycle]
  state.detail = { ...detail, panels: detail.panels.filter(panel => panel.panel_id !== 'analysis:dbzh_qc') }
  render(<MainWorkspace />)
  fireEvent.click(screen.getByRole('tab', { name: '质控排查' }))

  expect(document.querySelectorAll('.workspace-map-panel')).toHaveLength(4)
  expect(captionTitles()[2]).toBe('Z9591 质控标志')
  // Nothing left to switch between: the control hides instead of showing a dead button.
  expect(screen.queryByRole('group', { name: '质控证据图层' })).toBeNull()
})
