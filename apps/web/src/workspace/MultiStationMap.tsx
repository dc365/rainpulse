import { useEffect, useMemo, useState } from 'react'
import View from 'ol/View.js'
import { RadarProbePanel, type ProbeLayer } from './RadarProbePanel'
import { ReflectivityLegend } from '../ReflectivityLegend'
import type { MapCoordinate, GISLegendEntry } from '../RasterGISMap'
import { useComposite, type CompositeResult } from './CompositeMap'
import { CompositeCoverage } from './CompositeCoverage'
import { CompositeScope } from './CompositeScope'
import { RasterGISMap, type GISImageLayer, type GISMapExtent, type GISRadarContext } from '../RasterGISMap'
import { radarDisplayExtent, radarSiteFor } from '../radarSites'
import { FUZHOU_GIS_CONTEXT } from '../GISMapContexts'
import { REFLECTIVITY_LEGEND } from '../reflectivityPalette'
import { panelByID, stationDisplayName, qcSweepOptions, type WorkspaceCycleDetail } from './model'

const DOMAIN: GISMapExtent = [117.995, 24.995, 123.005, 27.005]
type Station = { radar_id: string; display_name: string }
type Geometry = { bounds: GISMapExtent; longitude_deg: number; latitude_deg: number; maximum_range_km: number; coordinate_source: string; raw: string; qc: string }
type Sweep = { sweep_number: number; elevation_deg: number; map?: Geometry }
type Scan = { scan_id: string; volume_start: string; volume_end: string; results: { result_id: string }[] }
type Resolved = { id: string; scan?: Scan; sweeps: Sweep[]; error?: string }
type Choice = { id: string; visible: boolean; opacity: number; sweep?: number }
const clock = (time: string) => new Date(Date.parse(time) + 8 * 3600_000).toISOString().slice(11, 19)
export function MultiStationMap({ sIDs, xStations, detail, time, day, revision, sharedView, layout, composite, compositeSeriesID, compositeRequestedRadars, onTimes }: {
  sIDs: string[]; xStations: Station[]; detail?: WorkspaceCycleDetail; time: string; day: string; revision: number;
  onTimes: (times: string[]) => void; sharedView: View; layout: 'single' | 'pair'; composite: boolean;
  compositeSeriesID?: string;
  compositeRequestedRadars?: string[];
}) {
  const stations = useMemo(() => [...sIDs.map(id => ({ id, band: 'S', name: radarSiteFor(id)?.displayName ?? id })), ...xStations.map(s => ({ id: s.radar_id, band: 'X', name: stationDisplayName(s.radar_id, s.display_name) }))], [sIDs, xStations])
  const [choices, setChoices] = useState<Choice[]>(() => {
    try { const saved: unknown = JSON.parse(sessionStorage.getItem('rainpulse.multi-station.layers') ?? '[]');
      return Array.isArray(saved) ? saved.filter((c): c is Choice => typeof c?.id === 'string' && Number.isFinite(c?.opacity) && c.opacity >= 0 && c.opacity <= 1 && (c.sweep === undefined || Number.isInteger(c.sweep))).map(c => ({ ...c, visible: true })).slice(0,32) : []
    } catch { return [] }
  })
  const [filter, setFilter] = useState('all')
  const [search, setSearch] = useState('')
  const [focus, setFocus] = useState('')
  const [rings, setRings] = useState('focus')
  const [resolved, setResolved] = useState<{ key: string; items: Resolved[] }>()
  const [point,setPoint] = useState<MapCoordinate|null>(null)
  const [productMode, setProductMode] = useState('s')
  const compositeState = useComposite(composite ? time : '', revision, compositeSeriesID)
  // 同时次、同系列刷新时才保留已确认结果；切时次立即移除旧图，避免贴上新时间。
  const [compositeMemory, setCompositeMemory] = useState<{ token: string; seriesID?: string; time: string; data?: CompositeResult }>()
  if (compositeState) {
    const token = compositeState.data ? `have:${compositeState.data.result_id}` : 'absent'
    if (compositeMemory?.token !== token || compositeMemory.seriesID !== compositeSeriesID || compositeMemory.time !== time) setCompositeMemory({ token, seriesID: compositeSeriesID, time, data: compositeState.data })
  }
  const compositeData = compositeState?.data
    ?? (composite && compositeSeriesID !== undefined && compositeMemory?.seriesID === compositeSeriesID && compositeMemory.time === time && compositeState === undefined && compositeMemory?.token.startsWith('have:') ? compositeMemory.data : undefined)
  useEffect(()=>{sessionStorage.setItem('rainpulse.multi-station.layers',JSON.stringify(choices))},[choices])
  const xIDs = choices.filter(c => c.visible && stations.some(s => s.id === c.id && s.band === 'X')).map(c => c.id).sort().join(',')
  const key = `${day}/${time}/${revision}/${xIDs}`
  useEffect(() => {
    const controller = new AbortController()
    const ids=xIDs.split(',').filter(Boolean)
    if(composite||!day||!time||!ids.length)return()=>controller.abort()
    void fetch('/api/v1/workspace/radar-layer-resolutions',{method:'POST',headers:{'Content-Type':'application/json'},signal:controller.signal,
      body:JSON.stringify({time,day,radar_ids:ids})}).then(async response=>{
      if(!response.ok)throw new Error(`图层解析失败（${response.status}）`)
      return response.json() as Promise<{items:Resolved[];times:string[]}>
    }).then(result=>{if(!controller.signal.aborted){setResolved({key,items:result.items});onTimes(result.times.sort())}})
      .catch(error=>{if(!controller.signal.aborted)setResolved({key,items:ids.map(id=>({id,sweeps:[],error:String(error)}))})})
    return () => controller.abort()
  }, [key, xIDs, day, time, onTimes, composite])
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
    return { choice, station, raw: geo?.raw, qc: geo?.qc, resultID: result?.scan?.results[0]?.result_id, extent: geo?.bounds ?? DOMAIN, radar, options: result?.sweeps.map(s => ({ number: s.sweep_number, elevation: s.elevation_deg })) ?? [], selected: sweep?.sweep_number, status: result?.error ?? (geo && result?.scan ? `${clock(result.scan.volume_start)}–${clock(result.scan.volume_end)} · 坐标待核验` : result ? '缺少地图图件' : '读取中') }
  }), [choices, stations, detail, time, resolved, key])
  const [seenStations, setSeenStations] = useState(stations)
  if (seenStations !== stations) {
    setSeenStations(stations)
    setChoices(items => { const kept = items.filter(c => stations.some(s => s.id === c.id)); return kept.length === items.length ? items : kept })
  }
  const byID = useMemo(() => new Map(data.map(d => [d.choice.id, d])), [data])
  const shownStations = stations.filter(s => (filter === 'all' || s.band === filter) && `${s.id} ${s.name}`.toLowerCase().includes(search.toLowerCase()))
  const pickedIDs = useMemo(() => new Set(choices.map(c => c.id)), [choices])
  const allShownPicked = shownStations.length > 0 && shownStations.every(s => pickedIDs.has(s.id))
  function pickAllShown() {
    if (allShownPicked) { const remove = new Set(shownStations.map(s => s.id)); setChoices(items => items.filter(c => !remove.has(c.id))); setFocus('') }
    else setChoices(items => [...items, ...shownStations.filter(s => !items.some(c => c.id === s.id)).map(s => ({ id: s.id, visible: true, opacity: 1 }))].slice(0, 32))
  }
  const contexts = useMemo(() => data.filter(d => d.choice.visible && d.radar).map(d => ({ ...d.radar!, displayRangeRadiiKM: rings === 'all' || rings === 'focus' && d.choice.id === focus ? d.radar!.displayRangeRadiiKM : [] })), [data, rings, focus])
  const rawLayers = useMemo(() => data.filter(d => d.choice.visible && d.raw).map(d => ({ id: d.choice.id, url: d.raw!, extent: d.extent, opacity: d.choice.opacity })), [data])
  const qcLayers = useMemo(() => data.filter(d => d.choice.visible && d.qc).map(d => ({ id: d.choice.id, url: d.qc!, extent: d.extent, opacity: d.choice.opacity })), [data])
  // 切时次时 S 详情与 X 图层解析在途会让两列图层瞬时清空（闪空态文案+残白）：在途保留上一帧。
  const resolving = Boolean(xIDs) && resolved?.key !== key
  const layersPending = detail === undefined || resolving
  const [keptRaw, setKeptRaw] = useState<GISImageLayer[] | undefined>()
  const [keptQc, setKeptQc] = useState<GISImageLayer[] | undefined>()
  if (rawLayers.length && keptRaw?.[0]?.url !== rawLayers[0].url) setKeptRaw(rawLayers)
  if (qcLayers.length && keptQc?.[0]?.url !== qcLayers[0].url) setKeptQc(qcLayers)
  const rawShown = rawLayers.length ? rawLayers : layersPending ? keptRaw ?? [] : []
  const qcShown = qcLayers.length ? qcLayers : layersPending ? keptQc ?? [] : []
  function patch(id: string, value: Partial<Choice>) { setChoices(items => items.map(c => c.id === id ? { ...c, ...value } : c)) }
  function toggle(id: string) { setChoices(items => items.some(c => c.id === id) ? items.filter(c => c.id !== id) : items.length>=32?items:[...items, { id, visible: true, opacity: 1 }]); setFocus(id) }
  function moveUp(id: string) { setChoices(items => { const index = items.findIndex(c => c.id === id); if (index < 0 || index === items.length - 1) return items; const copy = [...items]; [copy[index], copy[index + 1]] = [copy[index + 1], copy[index]]; return copy }) }
  function fit() { const extents = composite ? products.flatMap(p => p.map ? [p.map.bounds] : []) : data.filter(d => d.choice.visible && (d.raw || d.qc)).map(d => d.extent); if (extents.length) sharedView.fit([Math.min(...extents.map(e=>e[0])),Math.min(...extents.map(e=>e[1])),Math.max(...extents.map(e=>e[2])),Math.max(...extents.map(e=>e[3]))], { padding: [35,35,35,35], duration: 250 }) }
    function map(title: string, layers: GISImageLayer[], refs = contexts, mapLegend: readonly GISLegendEntry[] = REFLECTIVITY_LEGEND, unit='dBZ') {
    const computed: GISMapExtent = composite && layers.length ? [Math.min(...layers.map(l=>l.extent[0])),Math.min(...layers.map(l=>l.extent[1])),Math.max(...layers.map(l=>l.extent[2])),Math.max(...layers.map(l=>l.extent[3]))] : DOMAIN
    const fitExtent = fittedExtent ?? computed
    const shownTitle = layers === keptFusion?.layers && fusionPending ? `${title} · 上一帧` : title
    return <section className="radar-qc-map" aria-label={shownTitle}><header><strong>{shownTitle}</strong><span>{layers.length} 个图层</span></header><RasterGISMap imageLayers={layers} radarContexts={refs} imageDescription={title} imageExtent={DOMAIN} fitExtent={fitExtent} validTimeLabel={time ? clock(time) : ''} contextLabel="多站资料窗口" productLabel={title} legend={mapLegend} legendUnit={unit} point={point??undefined} onSelectPoint={setPoint} footerNote="" mapLabel={title} resetViewLabel="复位地图" loading={composite && !compositeState} sharedView={sharedView} comparisonMode basemapVisible referenceContext={FUZHOU_GIS_CONTEXT} rasterStyle="grid" zoomControls="hidden" emptyStateHint={composite ? !compositeState ? '正在读取本时次融合结果' : '本时次无可显示的融合图件；缺测不表示无回波' : '选择站点后显示该资料窗口的可用图件'} /></section> }
  const mosaic = detail && panelByID(detail, 'analysis:dbzh_qc')?.frames.find(f => Date.parse(f.valid_time) === Date.parse(time))
  const products = compositeData?.manifest.comparison.products ?? []
  const product = (id: string) => products.find(p=>p.product_id===id)
  const productLayers = (id: string): GISImageLayer[] => { const p=product(id); return p?.map ? [{id,url:p.map.object_path,extent:p.map.bounds,opacity:1}] : [] }
  const sx = product('sx_composite')
  // S 参考图仅在同一时次刷新期间保留，不跨时次回放。
  const fallbackLayers = useMemo<GISImageLayer[]>(() => mosaic ? [{ id: 's-fallback', url: mosaic.image_url, extent: mosaic.bounds ?? detail?.grid.raster_bounds ?? DOMAIN, opacity: 1 }] : [], [mosaic, detail])
  const [keptFallback, setKeptFallback] = useState<{time:string; layers:GISImageLayer[]}>()
  if (fallbackLayers.length && (keptFallback?.layers[0]?.url !== fallbackLayers[0].url || keptFallback.time !== time)) setKeptFallback({time,layers:fallbackLayers})
  const fallbackShown = fallbackLayers.length ? fallbackLayers : detail === undefined && keptFallback?.time === time ? keptFallback.layers : []
  // 产品逐帧范围随参差站覆盖微变；冻结首次取景，切帧不再重定位视图（「定位产品」可手动重定位）。
  const [fittedExtent, setFittedExtent] = useState<GISMapExtent | null>(null)
  const freezeSource: GISMapExtent | undefined = mosaic ? mosaic.bounds ?? detail?.grid?.raster_bounds : products.find(p => p.map)?.map?.bounds
  if (composite && freezeSource && !fittedExtent) setFittedExtent([...freezeSource] as GISMapExtent)
  const compositePending = composite && !compositeState
  const referenceTitle = compositeState?.error ? 'S 参考图 · 融合结果读取失败' : compositePending ? 'S 参考图 · 融合结果读取中' : 'S 参考图 · 本时次无融合结果'
  const selectedProduct = product(productMode)
  // Fusion primary layers with stale-while-revalidate: while the composite
  // list or the cycle detail is still in flight, keep the previous frame
  // instead of flashing the empty-state overlay; a confirmed-empty slot
  // (both sources arrived, nothing to show) clears to the real empty state.
  const fusionPrimaryLayers: GISImageLayer[] = selectedProduct
    ? productLayers(productMode)
    : productMode === 's'
      ? productLayers('s_only').length ? productLayers('s_only') : fallbackShown
      : productMode === 'sx'
        ? productLayers('sx_composite').length ? productLayers('sx_composite') : !sx ? fallbackShown : []
        : []
  const fusionPending = composite && !fusionPrimaryLayers.length && (compositeState === undefined || detail === undefined)
  const [keptFusion, setKeptFusion] = useState<{ key: string; layers: GISImageLayer[] }>()
  if (!fusionPending && fusionPrimaryLayers.length && keptFusion?.key !== `${compositeSeriesID ?? ''}|${time}|${productMode}`) setKeptFusion({ key: `${compositeSeriesID ?? ''}|${time}|${productMode}`, layers: fusionPrimaryLayers })
  const fusionShown = fusionPrimaryLayers.length ? fusionPrimaryLayers : fusionPending && compositeState !== undefined ? keptFusion?.layers ?? [] : []
  const probeLayers: ProbeLayer[] = point ? composite ? (()=>{
    const ids=productMode==='compare'?['s_only','sx_composite']:productMode==='s'?['s_only']:productMode==='sx'?['sx_composite']:[productMode]
    return ids.flatMap(id=>{const p=product(id);if(!p?.map||!compositeData)return [];const e=p.map.bounds;return [{id,result_id:compositeData.result_id,product_id:id,x:(point.longitude-e[0])/(e[2]-e[0]),y:(e[3]-point.latitude)/(e[3]-e[1])}]})
  })() : data.filter(d=>d.choice.visible&&d.qc).map(d=>({id:d.choice.id,asset_url:d.station?.band==='S'?d.qc:undefined,result_id:d.resultID,sweep_number:d.selected,x:(point.longitude-d.extent[0])/(d.extent[2]-d.extent[0]),y:(d.extent[3]-point.latitude)/(d.extent[3]-d.extent[1])})) : []
  return <div className="multi-station-workspace">
    <aside className="multi-station-sidebar">
      {composite ? <>
        <h3>组合反射率 <small>{compositeData ? `本帧输入 ${new Set(compositeData.manifest.sources?.map(s => s.radar_id) ?? []).size} 站` : fallbackShown.length ? '已发布 S 产品' : '所选系列无本帧结果'}</small></h3>
        <p className="multi-station-hint">本帧回波贡献：{sx?.echo_contributing_bands?.length ? sx.echo_contributing_bands.join(' + ') : compositeData ? sx?.echo_contributing_bands ? '无合格回波' : '旧结果未记录，贡献未知' : compositePending ? '读取中' : '未取得融合结果，贡献未知'}。点击地图可查看产品源数值。</p>
      </> : <>
        <h3>站点与图层 <small>已选 {choices.length}/32</small></h3>
        <input aria-label="搜索站点" placeholder="站号或名称" value={search} onChange={e=>setSearch(e.target.value)}/>
        <div className="multi-station-actions station-filter">
          <div className="station-band-filter" role="group" aria-label="波段筛选">
            {(['all','S','X'] as const).map(b=><button key={b} aria-pressed={filter===b} onClick={()=>setFilter(b)}>{b==='all'?'全部':`${b} 站`}</button>)}
          </div>
          <button aria-pressed={allShownPicked} onClick={pickAllShown} disabled={!shownStations.length}>{allShownPicked?'取消全选':'全选'}</button>
          <button onClick={()=>{setChoices([]);setFocus('')}} disabled={!choices.length}>清空</button>
        </div>
        <div className="multi-station-directory">
          {shownStations.map(s=>{
            const picked = pickedIDs.has(s.id)
            const d = picked ? byID.get(s.id) : undefined
            const stackTop = picked && choices.at(-1)?.id === s.id
            return <div key={s.id} className={`station-item${picked?' picked':''}`} data-band={s.band}>
              <label className="station-item-row">
                <input type="checkbox" disabled={!picked&&choices.length>=32} checked={picked} onChange={()=>toggle(s.id)}/>
                <b>{s.band}</b><span className="station-item-name">{s.id.toUpperCase()} · {s.name}</span>
                {picked && <small className="station-item-status">{d?.status}</small>}
              </label>
              {picked && d && <div className="station-item-controls">
                <select aria-label={`${s.id} 仰角`} value={d.selected??''} disabled={!d.options.length} onChange={e=>patch(s.id,{sweep:Number(e.target.value)})}>{d.options.map(o=><option key={o.number} value={o.number}>{o.elevation.toFixed(2)}° · {o.number}</option>)}</select>
                <label>透明度 {Math.round(d.choice.opacity*100)}%<input aria-label={`${s.id} 透明度`} type="range" min="0" max="1" step="0.05" value={d.choice.opacity} onChange={e=>patch(s.id,{opacity:Number(e.target.value)})}/></label>
                <button aria-label={`聚焦距离圈 ${s.id}`} aria-pressed={focus===s.id} onClick={()=>setFocus(s.id)} title="只显示该站的距离圈">◎</button>
                <button aria-label={`上移 ${s.id}`} disabled={stackTop} onClick={()=>moveUp(s.id)} title="向上移一层（列表上方 = 图上层）">↑</button>
              </div>}
            </div>
          })}
          {!shownStations.length && <p className="multi-station-hint">没有匹配的站点。</p>}
        </div>
      </>}
      <div className="multi-station-actions">
        {!composite && <label>距离圈<select aria-label="距离圈" value={rings} onChange={e=>setRings(e.target.value)}><option value="focus">当前站</option><option value="all">全部</option><option value="off">关闭</option></select></label>}
        <button onClick={fit}>{composite ? '定位产品' : '定位全部'}</button>
      </div>
    {composite && compositeData && <CompositeCoverage manifest={compositeData.manifest}/>}<RadarProbePanel point={point} layers={probeLayers}/></aside><div className="multi-station-content">{composite ? <><div className="multi-composite-meta">{compositeData && <CompositeScope registered={stations.map(s=>s.id)} requested={compositeRequestedRadars} sources={compositeData.manifest.sources??[]}/>}{compositeData?.manifest.display_warning && <p role="status" title={compositeData.manifest.display_warning}>{compositeData.manifest.display_warning}</p>}</div><div className="radar-qc-summary"><select aria-label="组合产品" value={productMode} onChange={e=>setProductMode(e.target.value)}><option value="s">S 组合反射率</option><option value="sx">S+X 组合反射率</option><option value="compare">S / S+X 对照</option>{products.filter(p=>!["s_only","sx_composite","x_minus_s"].includes(p.product_id)).map(p=><option key={p.product_id} value={p.product_id}>{p.label}</option>)}</select><span>{compositeData ? '同次计算 · 同网格' : fallbackShown.length ? '已发布 S 产品' : '所选系列无本帧结果'} · 实际参与以本帧资料为准</span><a href={`/admin?${new URLSearchParams({view:"new",preset:"sx_composite",radar:stations.map(s=>s.id).join(","),start:time,end:time?new Date(Date.parse(time)+360000).toISOString():""})}`}>生成组合</a></div>{selectedProduct ? map(selectedProduct.label,fusionShown,[],selectedProduct.legend??REFLECTIVITY_LEGEND,selectedProduct.unit??'dBZ') : productMode==='s' ? map(productLayers('s_only').length ? 'S 组合反射率' : fallbackShown.length ? referenceTitle : 'S 组合反射率 · 本时次无结果',fusionShown,[]) : productMode==='compare' ? <div className="radar-qc-pair layout-pair">{map('S 组合反射率',productLayers('s_only'),[])}{map('S+X 组合反射率',productLayers('sx_composite'),[])}</div> : map(sx?.label ?? (fallbackShown.length ? referenceTitle : 'S+X 组合反射率 · 本时次无结果'),fusionShown,[])}<div className="multi-composite-legend">{selectedProduct?.legend ? <><strong>{selectedProduct.unit}</strong>{selectedProduct.legend.map(e=><span key={e.label}><i style={{background:e.color}}/>{e.label}</span>)}</> : <><strong>dBZ</strong><ReflectivityLegend/></>}</div><p className="multi-composite-footnote" role="status">{compositeState?.error ?? (sx ? sx.reason ?? 'S 与 X 贡献见计算结果；候选产品' : productMode==='s' ? fallbackShown.length ? '所选系列本时次无组合结果；现有 S 数值组合产品供单图参考。选择 S / S+X 对照时仅使用同次计算产品。' : '所选系列本时次无组合结果；该时次没有可显示的已发布 S 图件。缺测不表示无回波。' : compositePending ? '正在读取本时次融合结果；X 贡献待核对。' : '所选系列本时次缺少融合结果，无法判断 X 贡献。单图仅显示已发布 S 参考图；双图仅显示同次计算结果。')}</p></> : <><div className="radar-qc-summary"><strong>{qcLayers.length}/{choices.length} 站可显示</strong><span>{choices.some(c=>c.opacity<1)?'透明叠加':'站点图层叠加'} · 上方的站在图上层 · 数值组合请切换组合反射率</span></div><div className={`radar-qc-pair layout-${layout}`}>{layout==='single'?<section hidden/>:map('原始反射率',rawShown)}{map('质控后反射率',qcShown)}</div></>}</div>
  </div>
}
