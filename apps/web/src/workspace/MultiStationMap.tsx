import { useEffect, useMemo, useState } from 'react'
import View from 'ol/View.js'
import { useComposite } from './CompositeMap'
import { RasterGISMap, type GISImageLayer, type GISMapExtent, type GISRadarContext } from '../RasterGISMap'
import { radarDisplayExtent, radarSiteFor } from '../radarSites'
import { FUZHOU_GIS_CONTEXT } from '../GISMapContexts'
import { REFLECTIVITY_LEGEND } from '../reflectivityPalette'
import { panelByID, qcSweepOptions, type WorkspaceCycleDetail } from './model'

const DOMAIN: GISMapExtent = [117.995, 24.995, 123.005, 27.005]
type Station = { radar_id: string; display_name: string }
type Geometry = { bounds: GISMapExtent; longitude_deg: number; latitude_deg: number; maximum_range_km: number; coordinate_source: string; raw: string; qc: string }
type Sweep = { sweep_number: number; elevation_deg: number; map?: Geometry }
type Scan = { scan_id: string; volume_start: string; volume_end: string; results: { result_id: string }[] }
type Resolved = { id: string; scan?: Scan; sweeps: Sweep[]; error?: string }
type Choice = { id: string; visible: boolean; opacity: number; sweep?: number }
const clock = (time: string) => new Date(Date.parse(time) + 8 * 3600_000).toISOString().slice(11, 19)
async function read<T>(url: string, signal: AbortSignal): Promise<T> {
  const response = await fetch(url, { signal, cache: 'no-store' })
  if (!response.ok) throw new Error(`读取失败（${response.status}）`)
  return response.json() as Promise<T>
}
export function MultiStationMap({ sIDs, xStations, detail, time, day, revision, sharedView, layout, composite, onTimes }: {
  sIDs: string[]; xStations: Station[]; detail?: WorkspaceCycleDetail; time: string; day: string; revision: number;
  onTimes: (times: string[]) => void; sharedView: View; layout: 'single' | 'pair' | 'swipe'; composite: boolean;
}) {
  const stations = useMemo(() => [...sIDs.map(id => ({ id, band: 'S', name: radarSiteFor(id)?.displayName ?? id })), ...xStations.map(s => ({ id: s.radar_id, band: 'X', name: s.display_name }))], [sIDs, xStations])
  const [choices, setChoices] = useState<Choice[]>(() => {
    try { const saved: unknown = JSON.parse(sessionStorage.getItem('rainpulse.multi-station.layers') ?? '[]');
      return Array.isArray(saved) ? saved.filter((c): c is Choice => typeof c?.id === 'string' && typeof c.visible === 'boolean' && Number.isFinite(c.opacity) && c.opacity >= 0 && c.opacity <= 1 && (c.sweep === undefined || Number.isInteger(c.sweep))).slice(0,64) : []
    } catch { return [] }
  })
  const [filter, setFilter] = useState('all')
  const [search, setSearch] = useState('')
  const [focus, setFocus] = useState('')
  const [rings, setRings] = useState('focus')
  const [resolved, setResolved] = useState<{ key: string; items: Resolved[] }>()
  const [failed, setFailed] = useState(false)
  const [productMode, setProductMode] = useState('s')
  const compositeState = useComposite(composite ? time : '', revision)
  useEffect(()=>{sessionStorage.setItem('rainpulse.multi-station.layers',JSON.stringify(choices))},[choices])
  const xIDs = choices.filter(c => stations.some(s => s.id === c.id && s.band === 'X')).map(c => c.id).sort().join(',')
  const key = `${day}/${time}/${revision}/${xIDs}`
  useEffect(() => {
    const controller = new AbortController()
    const ids = xIDs.split(',').filter(Boolean)
    const items: Resolved[] = []
    const times: string[] = []
    const start = Date.parse(`${day}T00:00:00+08:00`)
    if (!Number.isFinite(start) || !time) return () => controller.abort()
    const window = `&start=${encodeURIComponent(new Date(start).toISOString())}&end=${encodeURIComponent(new Date(start + 86400000).toISOString())}`
    async function worker() {
      while (ids.length && !controller.signal.aborted) {
        const id = ids.shift()!
        try {
          const scans: Scan[] = []
          let cursor = ''
          do {
            const result = await read<{ items: Scan[]; next_cursor?: string }>(`/api/v1/workspace/radar-scans?radar_id=${encodeURIComponent(id)}${window}${cursor ? `&cursor=${encodeURIComponent(cursor)}` : ''}`, controller.signal)
            scans.push(...result.items)
            cursor = result.next_cursor ?? ''
          } while (cursor && !controller.signal.aborted)
          times.push(...scans.map(s=>new Date(Math.floor(Date.parse(s.volume_start)/360000)*360000).toISOString()))
          const t = Math.floor(Date.parse(time) / 360000) * 360000
          const scan = scans.filter(s => Date.parse(s.volume_start) >= t && Date.parse(s.volume_start) < t + 360000).sort((a,b) => Date.parse(a.volume_start)-Date.parse(b.volume_start))[0]
          if (!scan?.results.length) { items.push({ id, scan, sweeps: [], error: scan ? '尚无质控结果' : '该窗口无体扫' }); continue }
          const result = await read<{ radar_id: string; scan_id: string; sweeps: Sweep[] }>(`/api/v1/workspace/radar-products/${encodeURIComponent(scan.results[0].result_id)}`, controller.signal)
          if (result.radar_id !== id || result.scan_id !== scan.scan_id) throw new Error('结果身份不匹配')
          items.push({ id, scan, sweeps: result.sweeps })
        } catch (error) { if (!controller.signal.aborted) items.push({ id, sweeps: [], error: String(error) }) }
      }
    }
    void Promise.all(Array.from({ length: Math.min(4, ids.length) }, worker)).then(() => { if (!controller.signal.aborted) { setResolved({ key, items }); onTimes([...new Set(times)].sort()) } })
    return () => controller.abort()
  }, [key, xIDs, day, time, onTimes])
  const data = useMemo(() => choices.map(choice => {
    const station = stations.find(s => s.id === choice.id)
    if (station?.band === 'S') {
      const options = detail ? qcSweepOptions(detail, choice.id, time) : []
      const sweep = options.find(s => s.number === choice.sweep) ?? options[0]
      const raw = detail && panelByID(detail, `dbzh_raw:${choice.id}`)?.frames.find(f => Date.parse(f.valid_time) === Date.parse(time) && f.sweep_number === sweep?.number)
      const qc = detail && panelByID(detail, `dbzh_qc:${choice.id}`)?.frames.find(f => Date.parse(f.valid_time) === Date.parse(time) && f.scan_id === raw?.scan_id && f.sweep_number === sweep?.number)
      const radar = radarSiteFor(choice.id)
      return { choice, station, raw: raw?.image_url, qc: qc?.image_url, extent: radar ? radarDisplayExtent(radar, raw?.maximum_range_km ?? radar.maximumRangeKM) : DOMAIN, radar, options, selected: sweep?.number, status: raw && qc ? clock(raw.observation_time ?? raw.valid_time) : '该时次无图件' }
    }
    const result = resolved?.key === key ? resolved.items.find(s => s.id === choice.id) : undefined
    const sweep = result?.sweeps.find(s => s.sweep_number === choice.sweep) ?? result?.sweeps.reduce<Sweep | undefined>((a,b) => !a || b.elevation_deg < a.elevation_deg ? b : a, undefined)
    const geo = sweep?.map
    const radar: GISRadarContext | undefined = geo ? { radarID: choice.id, displayName: station?.name ?? choice.id, radarBand: 'X', longitude: geo.longitude_deg, latitude: geo.latitude_deg, maximumRangeKM: geo.maximum_range_km, displayRangeRadiiKM: geo.maximum_range_km > 100 ? [50,100,150,200,250] : [10,20,30,40,50], geometryStatus: 'unverified' } : undefined
    return { choice, station, raw: geo?.raw, qc: geo?.qc, extent: geo?.bounds ?? DOMAIN, radar, options: result?.sweeps.map(s => ({ number: s.sweep_number, elevation: s.elevation_deg })) ?? [], selected: sweep?.sweep_number, status: result?.error ?? (geo && result?.scan ? `${clock(result.scan.volume_start)}–${clock(result.scan.volume_end)} · 坐标待核验` : result ? '缺少地图图件' : '读取中') }
  }), [choices, stations, detail, time, resolved, key])
  const contexts = useMemo(() => data.filter(d => d.choice.visible && d.radar).map(d => ({ ...d.radar!, displayRangeRadiiKM: rings === 'all' || rings === 'focus' && d.choice.id === focus ? d.radar!.displayRangeRadiiKM : [] })), [data, rings, focus])
  const rawLayers = useMemo(() => data.filter(d => d.choice.visible && d.raw).map(d => ({ id: d.choice.id, url: d.raw!, extent: d.extent, opacity: d.choice.opacity })), [data])
  const qcLayers = useMemo(() => data.filter(d => d.choice.visible && d.qc).map(d => ({ id: d.choice.id, url: d.qc!, extent: d.extent, opacity: d.choice.opacity })), [data])
  function patch(id: string, value: Partial<Choice>) { setChoices(items => items.map(c => c.id === id ? { ...c, ...value } : c)) }
  function toggle(id: string) { setChoices(items => items.some(c => c.id === id) ? items.filter(c => c.id !== id) : [...items, { id, visible: true, opacity: 1 }]); setFocus(id) }
  function select(band: string) { const ids = stations.filter(s => band === 'all' || s.band === band).map(s => s.id); setChoices(items => [...items, ...ids.filter(id => !items.some(c => c.id === id)).map(id => ({ id, visible: true, opacity: 1 }))]); setFocus(ids[0] ?? '') }
  function fit() { const extents = data.filter(d => d.choice.visible && (d.raw || d.qc)).map(d => d.extent); if (extents.length) sharedView.fit([Math.min(...extents.map(e=>e[0])),Math.min(...extents.map(e=>e[1])),Math.max(...extents.map(e=>e[2])),Math.max(...extents.map(e=>e[3]))], { padding: [35,35,35,35], duration: 250 }) }
  function map(title: string, layers: GISImageLayer[], refs = contexts) { return <section className="radar-qc-map" aria-label={title}><header><strong>{title}</strong><span>{layers.length} 个图层</span></header><RasterGISMap imageLayers={layers} radarContexts={refs} imageDescription={title} imageExtent={DOMAIN} fitExtent={DOMAIN} validTimeLabel={time ? clock(time) : ''} contextLabel="多站资料窗口" productLabel={title} legend={REFLECTIVITY_LEGEND} legendUnit="dBZ" footerNote="" mapLabel={title} resetViewLabel="复位地图" loading={false} layerError={failed} onLayerError={setFailed} sharedView={sharedView} comparisonMode basemapVisible referenceContext={FUZHOU_GIS_CONTEXT} rasterStyle="grid" zoomControls="hidden" emptyStateHint="选择站点后显示该资料窗口的可用图件" /></section> }
  const mosaic = detail && panelByID(detail, 'analysis:dbzh_qc')?.frames.find(f => Date.parse(f.valid_time) === Date.parse(time))
  const products = compositeState?.data?.manifest.comparison.products ?? []
  const product = (id: string) => products.find(p=>p.product_id===id)
  const productLayers = (id: string): GISImageLayer[] => { const p=product(id); return p?.map ? [{id,url:p.map.object_path,extent:p.map.bounds,opacity:1}] : [] }
  const sx = product('sx_composite')
  return <div className="multi-station-workspace">
    <aside className="multi-station-sidebar"><details open><summary>站点与图层 · {choices.length}</summary><input aria-label="搜索站点" placeholder="站号或名称" value={search} onChange={e=>setSearch(e.target.value)}/><div className="multi-station-actions"><select aria-label="筛选波段" value={filter} onChange={e=>setFilter(e.target.value)}><option value="all">S + X</option><option>S</option><option>X</option></select>{['S','X','all'].map(b=><button key={b} onClick={()=>select(b)}>{b==='all'?'S+X':b} 全部</button>)}</div><div className="multi-station-directory">{stations.filter(s=>(filter==='all'||s.band===filter)&&`${s.id} ${s.name}`.toLowerCase().includes(search.toLowerCase())).map(s=><label key={s.id}><input type="checkbox" checked={choices.some(c=>c.id===s.id)} onChange={()=>toggle(s.id)}/><b>{s.band}</b> {s.id.toUpperCase()} · {s.name}</label>)}</div></details>
      <h3>显示图层 <small>列表末项在上方</small></h3>{data.map((d,index)=><section className="multi-station-layer" key={d.choice.id}><div><input aria-label={`显示 ${d.choice.id}`} type="checkbox" checked={d.choice.visible} onChange={e=>patch(d.choice.id,{visible:e.target.checked})}/><button aria-pressed={focus===d.choice.id} onClick={()=>setFocus(d.choice.id)}>{d.choice.id.toUpperCase()}</button><button aria-label={`上移 ${d.choice.id}`} disabled={index===data.length-1} onClick={()=>setChoices(items=>{const copy=[...items];[copy[index],copy[index+1]]=[copy[index+1],copy[index]];return copy})}>↑</button><button aria-label={`移除 ${d.choice.id}`} onClick={()=>toggle(d.choice.id)}>×</button></div><small>{d.status}</small><select aria-label={`${d.choice.id} 仰角`} value={d.selected??''} disabled={!d.options.length} onChange={e=>patch(d.choice.id,{sweep:Number(e.target.value)})}>{d.options.map(o=><option key={o.number} value={o.number}>{o.elevation.toFixed(2)}° · {o.number}</option>)}</select><label>透明度 {Math.round(d.choice.opacity*100)}%<input aria-label={`${d.choice.id} 透明度`} type="range" min="0" max="1" step="0.05" value={d.choice.opacity} onChange={e=>patch(d.choice.id,{opacity:Number(e.target.value)})}/></label></section>)}
      <div className="multi-station-actions"><label>距离圈<select aria-label="距离圈" value={rings} onChange={e=>setRings(e.target.value)}><option value="focus">当前站</option><option value="all">全部</option><option value="off">关闭</option></select></label><button onClick={fit}>定位全部</button></div>
    </aside><div className="multi-station-content">{composite ? <><div className="radar-qc-summary"><select aria-label="组合产品" value={productMode} onChange={e=>setProductMode(e.target.value)}><option value="s">S 组合反射率</option><option value="sx">S+X 组合反射率</option><option value="compare">S / S+X 对照</option></select><span>{compositeState?.data ? '同次计算 · 同网格' : '已发布 S 产品'} · 站点选择用于新建组合</span><a href={`/admin?${new URLSearchParams({view:"new",preset:"sx_composite",radar:choices.map(c=>c.id).join(","),start:time,end:time?new Date(Date.parse(time)+360000).toISOString():""})}`}>生成组合</a></div>{productMode==='s' ? map('S 组合反射率',productLayers('s_only').length ? productLayers('s_only') : mosaic?[{id:'s-composite',url:mosaic.image_url,extent:mosaic.bounds ?? detail?.grid.raster_bounds ?? DOMAIN,opacity:1}]:[],[]) : productMode==='compare' ? <div className="radar-qc-pair layout-pair">{map('S 组合反射率',productLayers('s_only'),[])}{map('S+X 组合反射率',productLayers('sx_composite'),[])}</div> : map(sx?.label ?? (mosaic ? 'S 组合反射率 · X 未参与' : 'S+X 组合反射率'),productLayers('sx_composite').length ? productLayers('sx_composite') : !sx && mosaic ? [{id:'s-fallback',url:mosaic.image_url,extent:mosaic.bounds ?? detail?.grid.raster_bounds ?? DOMAIN,opacity:1}] : [],[])}<p role="status">{compositeState?.error ?? (sx ? sx.reason ?? 'S 与 X 贡献见计算结果；候选产品' : productMode==='s' ? '现有 S 数值组合产品。选择 S / S+X 对照时仅使用同次计算产品。' : 'X 未参与：该分析时次尚无可定位的 S+X 数值组合。单图可参考已发布 S 产品；双图仅显示同次计算结果。')}</p></> : <><div className="radar-qc-summary"><strong>{qcLayers.length}/{choices.length} 站可显示</strong><span>{choices.some(c=>c.opacity<1)?'透明叠加':'站点图层叠加'} · 资料窗口 · 数值组合请切换组合反射率</span></div><div className={`radar-qc-pair layout-${layout}`}>{map('原始反射率',rawLayers)}{map('质控后反射率',qcLayers)}</div></>}</div>
  </div>
}
