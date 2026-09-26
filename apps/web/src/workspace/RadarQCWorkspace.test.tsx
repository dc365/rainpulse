import { cleanup, fireEvent, render, screen, waitFor } from '@testing-library/react'
import { afterEach, expect, it, vi } from 'vitest'
import { RadarQCWorkspace } from './RadarQCWorkspace'

vi.mock('../RasterGISMap', () => ({ RasterGISMap: ({ imageUrl, imageLayers, radarContext, referenceOnly, imageExtent }: {
  imageLayers?: {id:string;url:string}[]; imageExtent?: number[]; imageUrl?: string; radarContext?: { longitude: number; latitude: number; geometryStatus?: string; displayRangeRadiiKM: readonly number[] }; referenceOnly?: boolean
}) => <div data-testid="geo-image" data-layers={imageLayers?.map(l=>l.url).join(",")} data-image={imageUrl ?? ''} data-extent={imageExtent?.join(',')} data-longitude={radarContext?.longitude} data-latitude={radarContext?.latitude}
  data-geometry-status={radarContext?.geometryStatus} data-radii={radarContext?.displayRangeRadiiKM.join(',')} data-reference-only={referenceOnly} /> }))

const morning = '2026-08-28T00:06:00Z'
const evening = '2026-08-28T10:36:00Z'
const cycles = [morning, evening].map((issue_time, index) => ({
  cycle_id: `cycle-${index}`, issue_time, grid_id: 'fuzhou', execution_mode: 'historical', freshness_seconds: 0,
  capabilities: { radar: true, lk: false, steps: false, nowcastnet: false },
}))
const panel = (id: string, time: string, radar = 'z9591') => ({
  panel_id: `${id}:${radar}`, algorithm_id: 'radar-analysis', display_name: id, role: 'qc', lifecycle: 'analysis',
  data_kind: 'reflectivity', cadence_minutes: 6, status: 'ready', radar_id: radar,
  frames: [{ asset_id: `${id}-${radar}`, valid_time: time, lead_time_minutes: 0, image_url: `/${id}-${radar}.png`,
    media_type: 'image/png', scan_id: `scan-${radar}`, sweep_number: 0, elevation_deg: 0.5 }],
})
const cycleDetail = (index: number) => ({
  schema_version: '1.0', ...cycles[index], grid: { grid_id: 'fuzhou', bounds: [118, 25, 123, 27], raster_bounds: [118, 25, 123, 27] },
  quality: { coverage_ratio: 0, mean_quality_index: 0, maximum_rate_mm_h: 0 }, radars: [{ radar_id: 'z9591', state: 'PARTICIPATING' }, { radar_id: 'z9598', state: 'PARTICIPATING' }],
  timeline: [cycles[index].issue_time], panels: index === 0
    ? [panel('dbzh_raw', morning), panel('dbzh_qc', morning)]
    : [panel('dbzh_raw', evening, 'z9598'), panel('dbzh_qc', evening, 'z9598')],
})
const stations = { items: [
  { radar_id: 'zf101', display_name: 'X 测试站', geometry_status: 'unverified', registered: 1, qc_ready: 1,
    candidate_site: { longitude_deg: 119.3306, latitude_deg: 26.1758, coordinate_source: 'draft_radar_config', config_version: 'v1' } },
  { radar_id: 'zf505', display_name: '缺坐标站', geometry_status: 'unverified', registered: 0, qc_ready: 0, candidate_site: null },
], next_cursor: '', start: morning, available_range: { end: morning } }
const scans = { items: [{ scan_id: 'scan-x', radar_id: 'zf101', volume_start: morning, volume_end: '2026-08-28T00:07:00Z', state: 'NORMALIZED', qc_status: 'READY', results: [{ result_id: 'result-x', version: 'candidate-v1', finished_at: morning }] }], next_cursor: '' }
const result = { result_id: 'result-x', radar_id: 'zf101', scan_id: 'scan-x', sweeps: [{ sweep_number: 3, sequence: 1, elevation_deg: 0.9, map: { crs: 'EPSG:4326', bounds: [118.8,25.7,119.8,26.7], longitude_deg: 119.3306, latitude_deg: 26.1758, maximum_range_km: 75, coordinate_source: 'normalized_volume_site', projection_version: 'wgs84-geodesic-4over3-v1', raw: '/map-raw-x.png', qc: '/map-qc-x.png', flags: '/map-flags-x.png' }, raw: '/raw-x.png', qc: '/qc-x.png', flags: '/flags-x.png' }] }

function setup() {
  vi.stubGlobal('ResizeObserver', class { observe() {} disconnect() {} })
  vi.stubGlobal('Image', class { src = ''; decode() { return Promise.resolve() } })
  URL.createObjectURL = vi.fn(() => 'blob:comparison')
  URL.revokeObjectURL = vi.fn()
  vi.stubGlobal('fetch', vi.fn(async (input: string) => {
    const url = String(input)
    const data = url.includes('radar-stations') ? stations : url.includes('radar-scans') ? scans
      : url.includes('radar-products') ? result : url.includes('cycles/cycle-') ? cycleDetail(Number(url.at(-1)))
        : { schema_version: '1.0', items: cycles, generated_at: morning, next_cursor: null }
    return { ok: true, json: async () => data, blob: async () => new Blob(['png'], { type: 'image/png' }) }
  }))
}
afterEach(() => { cleanup(); sessionStorage.clear(); vi.unstubAllGlobals(); window.history.replaceState({}, '', '/') })

it('keeps the six-minute target and native sweep identity across S→X→S', async () => {
  setup()
  window.history.replaceState({}, '', '/?preset=qc&band=S&date=2026-08-28&time=2026-08-28T00:06:00Z&station=z9591')
  render(<RadarQCWorkspace />)
  await waitFor(() => expect(document.querySelectorAll('[data-image="/dbzh_raw-z9591.png"]')).toHaveLength(1))
  fireEvent.click(screen.getByRole('button', { name: 'X' }))
  await waitFor(() => expect(document.querySelectorAll('[data-image^="/map-"]')).toHaveLength(2))
  expect((screen.getByRole('combobox', { name: '仰角' }) as HTMLSelectElement).value).toBe('3')
  expect(window.location.search).toContain('time=2026-08-28T00%3A06%3A00Z')
  fireEvent.click(screen.getByRole('button', { name: 'S' }))
  await waitFor(() => expect(document.querySelectorAll('[data-image="/dbzh_qc-z9591.png"]')).toHaveLength(1))
  expect((screen.getByRole('combobox', { name: '站点' }) as HTMLSelectElement).value).toBe('z9591')
})

it('keeps the selected S station when the next cycle has no matching frames', async () => {
  setup()
  window.history.replaceState({}, '', '/?preset=qc&band=S&date=2026-08-28&time=2026-08-28T00:06:00Z&station=z9591')
  render(<RadarQCWorkspace />)
  await screen.findByRole('button', { name: /08\/28 18:36 北京时间/ })
  fireEvent.click(screen.getByRole('button', { name: /08\/28 18:36 北京时间/ }))
  await waitFor(() => expect((screen.getByRole('combobox', { name: '站点' }) as HTMLSelectElement).value).toBe('z9591'))
  expect(document.querySelectorAll('[data-testid="geo-image"][data-image=""]')).toHaveLength(2)
  expect(window.location.search).toContain('station=z9591')
})

it('selects S and X together without requiring trusted fusion geometry', async () => {
  setup()
  window.history.replaceState({}, '', '/?preset=qc&band=S&date=2026-08-28&time=2026-08-28T00:06:00Z&station=z9591')
  render(<RadarQCWorkspace />)
  await waitFor(() => expect(document.querySelectorAll('[data-image="/dbzh_qc-z9591.png"]')).toHaveLength(1))
  fireEvent.click(screen.getByRole('button', { name: '多站叠加' }))
  fireEvent.click(screen.getByRole('button', { name: 'S 全部' }))
  fireEvent.click(screen.getByRole('checkbox', { name: /ZF101/ }))
  await waitFor(() => expect(document.querySelector('[data-layers="/dbzh_qc-z9591.png,/map-qc-x.png"]')).toBeTruthy())
  fireEvent.change(screen.getByRole('combobox', {name:'筛选波段'}), {target:{value:'S'}})
  expect(document.querySelector('[data-layers="/dbzh_qc-z9591.png,/map-qc-x.png"]')).toBeTruthy()
  fireEvent.click(screen.getByRole('button', { name: /08\/28 18:36 北京时间/ }))
  await waitFor(() => expect(document.querySelector('[data-layers*="map-qc-x"]')).toBeNull())
})

it('overlays X raw and QC rasters on exactly two maps using the result geometry', async () => {
  setup()
  window.history.replaceState({}, '', '/?preset=qc&band=X&date=2026-08-28&time=2026-08-28T00:06:00Z&station=zf101')
  render(<RadarQCWorkspace />)
  await waitFor(() => expect(document.querySelectorAll('[data-image^="/map-"]')).toHaveLength(2))
  const maps = screen.getAllByTestId('geo-image')
  expect(maps).toHaveLength(2)
  expect(maps.map(map => map.dataset.image)).toEqual(['/map-raw-x.png', '/map-qc-x.png'])
  for (const map of maps) expect(map.dataset).toMatchObject({ longitude: '119.3306', latitude: '26.1758',
    geometryStatus: 'unverified', radii: '10,20,30,40,50', extent: '118.8,25.7,119.8,26.7' })
  expect(fetch).toHaveBeenCalledWith('/map-raw-x.png', expect.anything())
  expect(fetch).toHaveBeenCalledWith('/map-qc-x.png', expect.anything())
  expect(screen.queryByRole('region', { name: 'X 波段站点参考地图' })).toBeNull()
  expect(screen.queryByLabelText('原生图缩放')).toBeNull()
  fireEvent.click(screen.getByRole('button', { name: '质控标记' }))
  await waitFor(() => expect(fetch).toHaveBeenCalledWith('/map-flags-x.png', expect.anything()))
  fireEvent.click(screen.getByRole('button', { name: '单图' }))
  expect(document.querySelector('.radar-qc-pair.layout-single')).toBeTruthy()
})
