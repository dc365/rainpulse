import { act, cleanup, fireEvent, render, screen, waitFor } from '@testing-library/react'
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

function setup(stationCatalog = stations) {
  vi.stubGlobal('ResizeObserver', class { observe() {} disconnect() {} })
  vi.stubGlobal('Image', class { src = ''; decode() { return Promise.resolve() } })
  URL.createObjectURL = vi.fn(() => 'blob:comparison')
  URL.revokeObjectURL = vi.fn()
  vi.stubGlobal('fetch', vi.fn(async (input: string, options?: {body?:string}) => {
    const url = String(input)
    const data = url.includes('radar-composites/') ? {result_id:'late-composite',manifest:{analysis_time:'2026-08-28T15:54:00Z',comparison:{products:[]}}} : url.includes('radar-composites') ? {items:[{result_id:'late-composite',analysis_time:'2026-08-28T15:54:00Z'}]} : url.includes('radar-layer-resolutions') ? {items:JSON.parse(options?.body??'{}').time===evening?[{id:'zf101',sweeps:[],error:'该窗口无体扫'}]:[{id:'zf101',scan:scans.items[0],sweeps:result.sweeps}],times:[morning]} : url.includes('radar-stations') ? stationCatalog : url.includes('radar-scans') ? scans
      : url.includes('radar-products') ? result : url.includes('cycles/cycle-') ? cycleDetail(Number(url.at(-1)))
        : { schema_version: '1.0', items: cycles, generated_at: morning, next_cursor: null }
    return { ok: true, json: async () => data, blob: async () => new Blob(['png'], { type: 'image/png' }) }
  }))
}
afterEach(() => { cleanup(); sessionStorage.clear(); vi.unstubAllGlobals(); window.history.replaceState({}, '', '/') })

it('distinguishes reference analysis times and can leave a pinned old composite without changing its time', async () => {
  setup()
  const fallback = vi.mocked(fetch).getMockImplementation()!
  const old = 'a'.repeat(64), latest = 'b'.repeat(64)
  const series = [latest,old].map(series_id=>({series_id,product_id:'horizontal',requested_radars:['z9591','z9598'],legacy:true}))
  vi.mocked(fetch).mockImplementation(async (input,init)=>{
    const url=String(input)
    if(url.includes('radar-composites?')) {
      const chosen=new URL(url,'http://localhost').searchParams.get('series_id')||latest
      return new Response(JSON.stringify({series,selected_series_id:chosen,items:[{result_id:'result',analysis_time:morning,series_id:chosen}]}))
    }
    return fallback(input,init)
  })
  window.history.replaceState({},'',`/?preset=qc&mode=fusion&date=2026-08-28&time=${morning}&series=${old}`)
  render(<RadarQCWorkspace />)
  await screen.findByText(/本系列 1 个组合时次/)
  expect(screen.getByText(/2 分析周期/)).toBeTruthy()
  expect(screen.queryByText(/2 组合时次/)).toBeNull()
  fireEvent.click(screen.getByRole('button',{name:'查看最新组合'}))
  await waitFor(()=>expect(screen.getByRole('button',{name:'固定当前系列'})).toBeTruthy())
  expect(new URLSearchParams(window.location.search).has('series')).toBe(false)
  expect(Date.parse(new URLSearchParams(window.location.search).get('time')!)).toBe(Date.parse(morning))
})

it('keeps frame queries in the selected series and clears an old series while switching', async () => {
  setup()
  const fallback = vi.mocked(fetch).getMockImplementation()!
  const a = 'a'.repeat(64), b = 'b'.repeat(64)
  const series = [a,b].map((series_id,index)=>({series_id,product_id:index?'strict-v2':'horizontal',network_release:index?'quality':'experiment',fingerprint:series_id,legacy:false}))
  let releaseB: (()=>void) | undefined
  const pendingB = new Promise<void>(resolve=>{releaseB=resolve})
  const requested: string[] = []
  vi.mocked(fetch).mockImplementation(async (input,init)=>{
    const url=String(input)
    if (url.includes('radar-composites/')) {
      const chosen=url.endsWith('/result-b')?b:a
      return new Response(JSON.stringify({result_id:chosen===a?'result-a':'result-b',manifest:{analysis_time:morning,
        sources:[{radar_id:'z9591',band:'S',volume_end:morning}],comparison:{products:[
          {product_id:'s_only',label:'S 组合反射率',status:'ready',map:{bounds:[118,25,120,27],object_path:chosen===a?'/series-a.png':'/series-b.png'}},
          {product_id:'sx_composite',label:'S+X 组合反射率',status:'ready',echo_contributing_bands:['S']},
        ]}}}))
    }
    if (url.includes('radar-composites?')) {
      const q=new URLSearchParams(url.split('?')[1])
      const chosen=q.get('series_id')||a
      const start=q.get('start')??'',end=q.get('end')??''
      if (Date.parse(end)-Date.parse(start)<86400000) requested.push(chosen)
      if (chosen===b) await pendingB
      return new Response(JSON.stringify({series,selected_series_id:chosen,items:[{result_id:chosen===a?'result-a':'result-b',analysis_time:morning,series_id:chosen}]}))
    }
    return fallback(input,init)
  })
  window.history.replaceState({},'',`/?preset=qc&mode=fusion&date=2026-08-28&time=${morning}`)
  render(<RadarQCWorkspace />)
  await waitFor(()=>expect(document.querySelector('[data-layers="/series-a.png"]')).toBeTruthy())
  expect(requested).toContain(a)
  expect(screen.getByText(/本帧回波贡献：S。/)).toBeTruthy()
  fireEvent.change(screen.getByLabelText('组合产品系列'),{target:{value:b}})
  expect(document.querySelector('[data-layers="/series-a.png"]')).toBeNull()
  await act(async()=>{releaseB?.()})
  await waitFor(()=>expect(document.querySelector('[data-layers="/series-b.png"]')).toBeTruthy())
  expect(requested.at(-1)).toBe(b)
})

it('labels a missing fusion frame as an S reference and keeps a fixed historical series', async () => {
  setup()
  const fallback=vi.mocked(fetch).getMockImplementation()!
  const fixed='a'.repeat(64)
  vi.mocked(fetch).mockImplementation(async(input,init)=>{
    const url=String(input)
    if(url.includes('radar-composites?'))return new Response(JSON.stringify({items:[],series:[{series_id:fixed,product_id:'full',requested_radars:['z9591','zf101']}],selected_series_id:fixed}))
    if(url.includes('cycles/cycle-0')){
      const detail=cycleDetail(0)
      detail.panels.push({...panel('analysis',morning),panel_id:'analysis:dbzh_qc',frames:[{...panel('analysis',morning).frames[0],image_url:'/s-reference.png'}]})
      return new Response(JSON.stringify(detail))
    }
    return fallback(input,init)
  })
  window.history.replaceState({},'',`/?preset=qc&mode=fusion&date=2026-08-28&time=${morning}&series=${fixed}`)
  render(<RadarQCWorkspace />)
  await waitFor(()=>expect(document.querySelector('[data-layers="/s-reference.png"]')).toBeTruthy())
  fireEvent.change(screen.getByLabelText('组合产品'),{target:{value:'sx'}})
  await screen.findByText('S 参考图 · 本时次无融合结果')
  expect(screen.getByText(/缺少融合结果，无法判断 X 贡献/)).toBeTruthy()
  expect(screen.queryByText(/X 未参与/)).toBeNull()
  expect(new URLSearchParams(location.search).get('series')).toBe(fixed)
  fireEvent.change(screen.getByLabelText('组合产品'),{target:{value:'compare'}})
  expect(document.querySelector('[data-layers="/s-reference.png"]')).toBeNull()
})

it('opens the current time latest frame and preserves automatic selection across reloads', async () => {
  setup()
  const fallback=vi.mocked(fetch).getMockImplementation()!
  const newest='a'.repeat(64), current='b'.repeat(64)
  const series=[newest,current].map(series_id=>({series_id,product_id:'full',requested_radars:['z9591','zf101']}))
  vi.mocked(fetch).mockImplementation(async(input,init)=>{
    const url=String(input)
    if(url.includes('radar-composites?')){
      const q=new URL(url,'http://localhost').searchParams
      const narrow=Date.parse(q.get('end')!)-Date.parse(q.get('start')!)<86400000
      const selected=q.get('series_id') || (narrow?current:newest)
      return new Response(JSON.stringify({series:narrow?series.filter(s=>s.series_id===selected):series,selected_series_id:selected,items:[{result_id:selected,analysis_time:selected===current?morning:evening,series_id:selected}]}))
    }
    if(url.includes('radar-composites/'))return new Response(JSON.stringify({result_id:current,manifest:{analysis_time:morning,sources:[{radar_id:'z9591',band:'S'},{radar_id:'zf101',band:'X'}],comparison:{products:[{product_id:'sx_composite',label:'S+X 实际组合',status:'available',echo_contributing_bands:['S','X'],map:{bounds:[118,25,120,27],object_path:'/sx-current.png'}}]}}}))
    return fallback(input,init)
  })
  window.history.replaceState({},'',`/?preset=qc&mode=fusion&date=2026-08-28&time=${morning}&series=${newest}`)
  render(<RadarQCWorkspace />)
  await screen.findByText(/本系列 1 个组合时次/)
  fireEvent.click(screen.getByRole('button',{name:'查看最新组合'}))
  await waitFor(()=>expect(screen.getByText(/本帧回波贡献：S \+ X/)).toBeTruthy())
  expect(new URLSearchParams(location.search).has('series')).toBe(false)
  expect(screen.getByLabelText('组合产品系列')).toHaveProperty('value','')
  fireEvent.change(screen.getByLabelText('组合产品'),{target:{value:'sx'}})
  expect(document.querySelector('[data-layers="/sx-current.png"]')).toBeTruthy()
  fireEvent.click(screen.getByRole('button',{name:'固定当前系列'}))
  await waitFor(()=>expect(new URLSearchParams(location.search).get('series')).toBe(current))
})

it('identifies a pinned historical X result and refreshes to the latest version', async () => {
  setup()
  const fallback = vi.mocked(fetch).getMockImplementation()!
  vi.mocked(fetch).mockImplementation(async (input, init) => {
    const url = String(input)
    if (url.includes('radar-scans')) return { ok: true, json: async () => ({ ...scans, items: [{ ...scans.items[0], results: [...scans.items[0].results, { result_id: 'result-old', version: 'old', finished_at: morning }] }] }) } as Response
    if (url.includes('radar-products/result-old')) return { ok: true, json: async () => ({ ...result, result_id: 'result-old' }) } as Response
    return fallback(input, init)
  })
  window.history.replaceState({}, '', '/?preset=qc&band=X&date=2026-08-28&time=2026-08-28T00:06:00Z&station=zf101&scan=scan-x&result=result-old')
  render(<RadarQCWorkspace />)
  expect(await screen.findByText(/正在查看历史质控结果/)).toBeTruthy()
  expect(new URLSearchParams(window.location.search).get('result')).toBe('result-old')
  fireEvent.click(screen.getByRole('button', { name: '查看最新结果' }))
  await waitFor(() => expect(new URLSearchParams(window.location.search).get('result')).toBe('result-x'))
  expect(screen.queryByText(/正在查看历史质控结果/)).toBeNull()
})

it('does not retain a previous X volume or its completion status while the next volume loads', async () => {
  setup()
  const fallback = vi.mocked(fetch).getMockImplementation()!
  let finish: (value: Response) => void = () => {}
  const pending = new Promise<Response>(resolve => { finish = resolve })
  let requested = false
  vi.mocked(fetch).mockImplementation(async (input, init) => {
    const url = String(input)
    if (url.includes('radar-scans')) return { ok: true, json: async () => ({ ...scans, items: [scans.items[0], { ...scans.items[0], scan_id: 'scan-next', volume_start: evening, volume_end: evening, results: [{ result_id: 'result-next', version: 'v2', finished_at: evening }] }] }) } as Response
    if (url.includes('radar-products/result-next')) { requested = true; return pending }
    if (url.includes('radar-products/result-x')) return { ok: true, json: async () => ({ ...result, sweeps: result.sweeps.map(s => ({ ...s, xqc_v2: { status: 'ACTION_BUDGET_ABSTAINED', mode: 'quarantine' } })) }) } as Response
    return fallback(input, init)
  })
  window.history.replaceState({}, '', '/?preset=qc&band=X&date=2026-08-28&time=2026-08-28T00:06:00Z&station=zf101')
  render(<RadarQCWorkspace />)
  await screen.findByText(/ACTION_BUDGET_ABSTAINED/)
  fireEvent.click(await screen.findByRole('button', { name: /08\/28 18:36 北京时间/ }))
  await waitFor(() => expect(requested).toBe(true))
  expect(screen.queryByText(/ACTION_BUDGET_ABSTAINED/)).toBeNull()
  expect(document.querySelectorAll('[data-image="/map-qc-x.png"]')).toHaveLength(0)
  await act(async () => finish({ ok: true, json: async () => ({ ...result, result_id: 'result-next', scan_id: 'scan-next' }) } as Response))
  await waitFor(() => expect(document.querySelectorAll('[data-image="/map-qc-x.png"]')).toHaveLength(1))
})

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
  fireEvent.click(screen.getByRole('button', { name: 'S 站' }))
  fireEvent.click(screen.getByRole('button', { name: '全选' }))
  fireEvent.click(screen.getByRole('button', { name: '全部' }))
  fireEvent.click(screen.getByRole('checkbox', { name: /ZF101/ }))
  await waitFor(() => expect(document.querySelector('[data-layers="/dbzh_qc-z9591.png,/map-qc-x.png"]')).toBeTruthy())
  fireEvent.click(screen.getByRole('button', { name: 'S 站' }))
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
  expect(screen.queryByRole('region', { name: 'X 波段站点参考地图' })).toBeNull()
  expect(screen.queryByLabelText('原生图缩放')).toBeNull()
  fireEvent.click(screen.getByRole('button', { name: '质控标记' }))
  await waitFor(() => expect(maps[1].dataset.image).toBe('/map-flags-x.png'))
  fireEvent.click(screen.getByRole('button', { name: '单图' }))
  expect(document.querySelector('.radar-qc-pair.layout-single')).toBeTruthy()
})

it('shows composite times beyond the S catalog without selected station layers', async()=>{
 setup()
 window.history.replaceState({}, '', '/?preset=qc&band=S&mode=fusion&date=2026-08-28&time=2026-08-28T00:06:00Z')
 render(<RadarQCWorkspace />)
 expect(screen.queryByRole('checkbox', { name: /Z9591/ })).toBeNull()
 expect(screen.queryByRole('button', { name: '全选' })).toBeNull()
 const generate = await screen.findByRole('link', { name: '生成组合' })
 await waitFor(() => expect(generate.getAttribute('href')).toContain('radar=z9591'))
 expect(generate.getAttribute('href')).toContain('zf505')
 const late=await screen.findByRole('button',{name:/08\/28 23:54 北京时间/})
 fireEvent.click(late)
 await waitFor(()=>expect(new URLSearchParams(window.location.search).get('time')).toBe('2026-08-28T15:54:00.000Z'))
})

it('renders overlay mode directly from a URL without a time parameter', async () => {
  setup()
  window.history.replaceState({}, '', '/?preset=qc&band=S&mode=overlay&date=2026-08-28')
  render(<RadarQCWorkspace />)
  await screen.findByRole('checkbox', { name: /ZF101/ })
  await screen.findByRole('checkbox', { name: /Z9591/ })
  fireEvent.click(screen.getByRole('button', { name: '全选' }))
  await waitFor(() => expect(JSON.parse(sessionStorage.getItem('rainpulse.multi-station.layers') ?? '[]')).toHaveLength(4))
  expect(screen.getByRole('button', { name: '取消全选' })).toBeTruthy()
  fireEvent.click(screen.getByRole('button', { name: '取消全选' }))
  await waitFor(() => expect(JSON.parse(sessionStorage.getItem('rainpulse.multi-station.layers') ?? '[]')).toHaveLength(0))
  expect((screen.getByLabelText('资料日期') as HTMLInputElement).value).toBe('2026-08-28')
  expect(screen.queryByRole('combobox', { name: '字段' })).toBeNull()
})

it('renders the observation timeline as a fit-all rail with inline availability', async () => {
  setup()
  window.history.replaceState({}, '', '/?preset=qc&band=S&date=2026-08-28&time=2026-08-28T00:06:00Z&station=z9591')
  const { container } = render(<RadarQCWorkspace />)
  await waitFor(() => expect(document.querySelectorAll('[data-image="/dbzh_raw-z9591.png"]')).toHaveLength(1))
  const rail = container.querySelector('.workspace-timeline-rail')
  expect(rail?.classList.contains('observation-fit')).toBe(true)
  await waitFor(()=>expect(rail?.querySelectorAll('.workspace-timeline-frame')).toHaveLength(3))
  expect(container.querySelector('.workspace-timeline-availability')).toBeNull()
  expect(container.querySelector('.workspace-timeline-inline-availability')).toBeTruthy()
  expect(screen.getByText('S 分析周期')).toBeTruthy()
  expect(screen.getByText('第 1/3 帧')).toBeTruthy()
})

it('distinguishes reading from no-scan while the X scan list is loading', async () => {
  vi.stubGlobal('ResizeObserver', class { observe() {} disconnect() {} })
  vi.stubGlobal('Image', class { src = ''; decode() { return Promise.resolve() } })
  URL.createObjectURL = vi.fn(() => 'blob:comparison')
  URL.revokeObjectURL = vi.fn()
  let releaseScans: (value: unknown) => void = () => {}
  vi.stubGlobal('fetch', vi.fn(async (input: string) => {
    const url = String(input)
    if (url.includes('radar-scans')) return new Promise(resolve => { releaseScans = resolve })
    const data = url.includes('radar-stations') ? stations : url.includes('radar-products') ? result
      : { schema_version: '1.0', items: cycles, generated_at: morning, next_cursor: null }
    return { ok: true, json: async () => data, blob: async () => new Blob(['png'], { type: 'image/png' }) }
  }))
  window.history.replaceState({}, '', '/?preset=qc&band=X&date=2026-08-28&time=2026-08-28T00:06:00Z&station=zf101')
  render(<RadarQCWorkspace />)
  expect(await screen.findByText('正在读取体扫…')).toBeTruthy()
  expect(screen.queryByText('该时刻无体扫')).toBeNull()
  await act(async () => { releaseScans({ ok: true, json: async () => scans }) })
  await waitFor(() => expect(document.querySelectorAll('[data-testid="geo-image"]')).toHaveLength(2))
})

it('uses the S analysis grid for the X timeline and matches the nearest scan', async () => {
  vi.stubGlobal('ResizeObserver', class { observe() {} disconnect() {} })
  vi.stubGlobal('Image', class { src = ''; decode() { return Promise.resolve() } })
  URL.createObjectURL = vi.fn(() => 'blob:comparison')
  URL.revokeObjectURL = vi.fn()
  const offsetScans = { items: [{ ...scans.items[0], volume_start: '2026-08-28T00:07:30Z', volume_end: '2026-08-28T00:08:30Z' }], next_cursor: '' }
  vi.stubGlobal('fetch', vi.fn(async (input: string) => {
    const url = String(input)
    const data = url.includes('radar-stations') ? stations : url.includes('radar-scans') ? offsetScans : url.includes('radar-products') ? result
      : { schema_version: '1.0', items: cycles, generated_at: morning, next_cursor: null }
    return { ok: true, json: async () => data, blob: async () => new Blob(['png'], { type: 'image/png' }) }
  }))
  window.history.replaceState({}, '', '/?preset=qc&band=X&date=2026-08-28&time=2026-08-28T00:06:00Z&station=zf101')
  const { unmount } = render(<RadarQCWorkspace />)
  await waitFor(() => expect(document.querySelectorAll('[data-testid="geo-image"]')).toHaveLength(2))
  expect(document.querySelectorAll('.workspace-timeline-frame')).toHaveLength(2)
  expect(document.querySelector('.radar-qc-map header span')?.textContent).toBe('08:07:30')
  expect(document.querySelector('.workspace-timeline-state strong')?.textContent).toContain('08/28 08:06 北京时间')
  unmount()
  // 目标时刻无邻近体扫时回退最近带结果体扫，不落"无体扫"门控
  window.history.replaceState({}, '', '/?preset=qc&band=X&date=2026-08-28&time=2026-08-28T10:36:00Z&station=zf101')
  render(<RadarQCWorkspace />)
  await waitFor(() => expect(document.querySelectorAll('[data-testid="geo-image"]')).toHaveLength(2))
  expect(document.querySelector('.radar-qc-gate')).toBeNull()
  expect(document.querySelector('.radar-qc-map header span')?.textContent).toBe('08:07:30')
})

it('drops the retained composite once a time is confirmed to have no composite', async () => {
  vi.stubGlobal('ResizeObserver', class { observe() {} disconnect() {} })
  vi.stubGlobal('fetch', vi.fn(async (input: string) => {
    const url = String(input)
    let data: unknown
    if (url.includes('radar-composites/')) data = { result_id: 'rc-1', manifest: { analysis_time: '2026-08-28T00:06:00Z', comparison: { products: [
      { product_id: 's_only', label: 'S 组合反射率', status: 'ready', map: { bounds: [118, 25, 120, 27], object_path: '/full.png' } },
    ] } } }
    else if (url.includes('radar-composites?')) {
      const start = new URLSearchParams(url.split('?')[1]).get('start') ?? ''
      data = start.startsWith('2026-08-28T00') ? { items: [{ result_id: 'rc-1', analysis_time: '2026-08-28T00:06:00Z' }] } : { items: [] }
    }
    else if (url.includes('radar-stations')) data = stations
    else if (url.includes('radar-scans')) data = scans
    else if (url.includes('radar-products')) data = result
    else if (url.includes('cycles/cycle-')) data = cycleDetail(Number(url.at(-1)))
    else data = { schema_version: '1.0', items: cycles, generated_at: morning, next_cursor: null }
    return { ok: true, json: async () => data, blob: async () => new Blob(['png'], { type: 'image/png' }) }
  }))
  window.history.replaceState({}, '', '/?preset=qc&mode=fusion&date=2026-08-28&time=2026-08-28T00:06:00Z')
  render(<RadarQCWorkspace />)
  await waitFor(() => expect(document.querySelector('[data-layers="/full.png"]')).toBeTruthy())
  fireEvent.click(screen.getByRole('button', { name: /08\/28 18:36 北京时间/ }))
  await waitFor(() => expect(document.querySelector('[data-layers="/full.png"]')).toBeNull())
  expect(screen.getByText(/所选系列本时次无组合结果；该时次没有可显示的已发布 S 图件/)).toBeTruthy()
  expect(screen.getByText('所选系列无本帧结果')).toBeTruthy()
})

it('does not label the previous S reference as the next time while detail is loading', async () => {
  vi.stubGlobal('ResizeObserver', class { observe() {} disconnect() {} })
  const mosaicDetail = (index: number) => ({ ...cycleDetail(index), panels: [...cycleDetail(index).panels,
    { panel_id: 'analysis:dbzh_qc', algorithm_id: 'radar-analysis', display_name: 'analysis', role: 'qc', lifecycle: 'analysis',
      data_kind: 'reflectivity', cadence_minutes: 6, status: 'ready',
      frames: [{ asset_id: `m${index}`, valid_time: cycles[index].issue_time, lead_time_minutes: 0, image_url: `/mosaic-${index}.png`, media_type: 'image/png' }] }] })
  const pendingCycle1: ((value: unknown) => void)[] = []
  vi.stubGlobal('fetch', vi.fn(async (input: string) => {
    const url = String(input)
    let data: unknown
    if (url.includes('radar-composites')) data = { items: [] }
    else if (url.includes('radar-stations')) data = stations
    else if (url.includes('radar-scans')) data = scans
    else if (url.includes('radar-products')) data = result
    else if (url.includes('cycles/cycle-0')) data = mosaicDetail(0)
    else if (url.includes('cycles/cycle-1')) return new Promise(resolve => { pendingCycle1.push(resolve) })
    else data = { schema_version: '1.0', items: cycles, generated_at: morning, next_cursor: null }
    return { ok: true, json: async () => data, blob: async () => new Blob(['png'], { type: 'image/png' }) }
  }))
  window.history.replaceState({}, '', '/?preset=qc&mode=fusion&date=2026-08-28&time=2026-08-28T00:06:00Z')
  render(<RadarQCWorkspace />)
  await waitFor(() => expect(document.querySelector('[data-layers="/mosaic-0.png"]')).toBeTruthy())
  fireEvent.click(screen.getByRole('button', { name: /08\/28 18:36 北京时间/ }))
  await act(async () => { await new Promise(r => setTimeout(r, 120)) })
  expect(document.querySelector('[data-layers="/mosaic-0.png"]')).toBeNull()
  await act(async () => { pendingCycle1.forEach(resolve => resolve({ ok: true, json: async () => mosaicDetail(1) })) })
  await waitFor(() => expect(document.querySelector('[data-layers="/mosaic-1.png"]')).toBeTruthy())
  expect(document.querySelector('[data-layers="/mosaic-0.png"]')).toBeNull()
})

it('selects the whole 4 S + 24 X network without silently truncating stations',async()=>{
 const full={...stations,items:[...Array.from({length:24},(_,i)=>({...stations.items[0],band:'X',radar_id:`zf${100+i}`})),...['z9591','z9593','z9598','z9599'].map(radar_id=>({...stations.items[0],band:'S',radar_id}))]}
 setup(full)
 window.history.replaceState({},'', '/?preset=qc&band=S&mode=overlay&date=2026-08-28&time=2026-08-28T00:06:00Z')
 render(<RadarQCWorkspace />)
 await screen.findByRole('checkbox',{name:/ZF123/})
 fireEvent.click(screen.getByRole('button',{name:'全选'}))
 await waitFor(()=>expect(JSON.parse(sessionStorage.getItem('rainpulse.multi-station.layers')??'[]')).toHaveLength(28))
 const calls=vi.mocked(fetch).mock.calls.filter(([url])=>String(url).includes('radar-layer-resolutions'))
 expect(JSON.parse(String(calls.at(-1)?.[1]?.body)).radar_ids).toHaveLength(24)
})


it('loads a deep-linked native scan from the second catalog page', async () => {
  setup()
  const original = globalThis.fetch
  const fetcher = vi.fn(async (input: RequestInfo | URL, options?: RequestInit) => {
    const url = String(input)
    if (url.includes('radar-scans')) return new Response(JSON.stringify(url.includes('cursor=older') ? scans : {
      items: [{ ...scans.items[0], scan_id: 'newer-scan', volume_start: evening, results: [] }], next_cursor: 'older',
    }))
    return original(input, options)
  })
  vi.stubGlobal('fetch', fetcher)
  window.history.replaceState({}, '', '/?preset=qc&band=X&date=2026-08-28&time=2026-08-28T00:06:00Z&station=zf101&scan=scan-x')
  render(<RadarQCWorkspace />)
  await waitFor(() => expect(document.querySelectorAll('[data-image^="/map-"]')).toHaveLength(2))
  expect(fetcher.mock.calls.some(([url]) => String(url).includes('cursor=older'))).toBe(true)
  expect((screen.getByRole('combobox', { name: '仰角' }) as HTMLSelectElement).value).toBe('3')
})

it('labels the shared analysis grid separately from the native X acquisition time', async () => {
  setup()
  const nativeTime = '2026-08-28T00:08:25Z'
  let releaseScans: (() => void) | undefined
  const pendingScans = new Promise<void>(resolve => { releaseScans = resolve })
  const fallback = vi.mocked(fetch).getMockImplementation()!
  vi.mocked(fetch).mockImplementation(async (input, init) => {
    if (String(input).includes('radar-scans')) {
      await pendingScans
      return { ok: true, json: async () => ({
        ...scans, items: [{ ...scans.items[0], volume_start: nativeTime, volume_end: '2026-08-28T00:09:00Z' }],
      }) } as Response
    }
    return fallback(input, init)
  })
  window.history.replaceState({}, '', '/?preset=qc&band=X&date=2026-08-28&time=2026-08-28T00:08:25Z&station=zf101&scan=scan-x&result=result-x')
  render(<RadarQCWorkspace />)
  await waitFor(() => expect(vi.mocked(fetch).mock.calls.some(([url]) => String(url).includes('radar-scans'))).toBe(true))
  expect(new URLSearchParams(window.location.search).get('scan')).toBe('scan-x')
  expect(new URLSearchParams(window.location.search).get('result')).toBe('result-x')
  await act(async () => { releaseScans?.() })
  await screen.findAllByText('08:08:25')
  expect(screen.getByText('3 分析周期 · 分析时次')).toBeTruthy()
  expect(screen.getByText('08/28 08:06 北京时间')).toBeTruthy()
  expect(new URLSearchParams(window.location.search).get('time')).toBe(nativeTime)
  expect(new URLSearchParams(window.location.search).get('scan')).toBe('scan-x')
})

it('keeps one analysis clock and exact QC source across single, overlay and composite views', async () => {
  setup()
  const at='2026-08-28T02:48:00.000Z', before='2026-08-28T02:42:00Z'
  const catalog=[before,at].map((issue_time,i)=>({...cycles[0],cycle_id:`aligned-${i}`,analysis_id:`analysis-${i}`,issue_time}))
  const source={radar_id:'z9591',band:'S',scan_id:'ended-1043',asset_sha256:'current-qc',volume_start:'2026-08-28T02:37:49Z',volume_end:'2026-08-28T02:43:16Z'}
  const series=[{series_id:'aligned-series',product_id:'horizontal',requested_radars:['z9591'],legacy:true}]
  const exact=['DBZH_RAW','DBZH_QC'].map(field=>({scope:'polar',field,radar_id:'z9591',scan_id:source.scan_id,qc_content_sha256:source.asset_sha256,sweep_number:0,elevation_deg:.5,layer_id:field,image_url:`/exact-${field}.png`}))
  vi.mocked(fetch).mockImplementation(async input=>{
    const url=String(input)
    const data=url.includes('radar-composites/')?{result_id:'aligned-result',manifest:{analysis_time:at,grid_id:'fuzhou',sources:[source],comparison:{products:[{product_id:'s_only',label:'S 组合反射率',status:'ready',map:{bounds:[118,25,123,27],object_path:'/exact-composite.png'}}]}}}
      :url.includes('radar-composites?')?{selected_series_id:'aligned-series',series,items:[{result_id:'aligned-result',analysis_time:at,series_id:'aligned-series'}]}
      :url.includes('/analysis-cycles/')?{layers:url.includes('analysis-0')?exact:exact.map(l=>({...l,scan_id:'future-1048'}))}
      :url.includes('/cycles/aligned-')?{...cycleDetail(0),...catalog[1],panels:[panel('dbzh_raw',at),panel('dbzh_qc',at)]}
      :url.includes('radar-stations')?{...stations,items:[]}
      :{schema_version:'1.0',items:catalog,generated_at:at,next_cursor:null}
    return new Response(JSON.stringify(data))
  })
  window.history.replaceState({},'',`/?preset=qc&band=S&mode=single&date=2026-08-28&time=${at}&station=z9591`)
  render(<RadarQCWorkspace />)
  const displayed=()=>screen.getAllByTestId('geo-image').flatMap(e=>[e.getAttribute('data-image'),e.getAttribute('data-layers')]).join(' ')
  await waitFor(()=>expect(displayed()).toContain('/exact-DBZH_QC.png'))
  expect(screen.getByText(/完整体扫结束后才进入组合/).textContent).toContain('10:43:16')
  // Source explanation must not consume a column of the map stage grid.
  expect(screen.getByText(/完整体扫结束后才进入组合/).closest('.radar-qc-stage')).toBeNull()
  expect(screen.getByRole('region',{name:'质控图层'}).children).toHaveLength(1)
  fireEvent.click(screen.getByRole('button',{name:'多站叠加'}))
  fireEvent.click(await screen.findByRole('checkbox',{name:/Z9591/}))
  await waitFor(()=>expect(displayed()).toContain('/exact-DBZH_QC.png'))
  expect(displayed()).not.toContain('/dbzh_qc-z9591.png')
  fireEvent.click(screen.getByRole('button',{name:'组合反射率'}))
  await waitFor(()=>expect(displayed()).toContain('/exact-composite.png'))
  fireEvent.click(screen.getByRole('button',{name:'单站验证'}))
  await waitFor(()=>expect(displayed()).toContain('/exact-DBZH_QC.png'))
  expect(Date.parse(new URLSearchParams(location.search).get('time')!)).toBe(Date.parse(at))
})
