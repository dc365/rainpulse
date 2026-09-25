import { useEffect, useMemo, useState } from 'react'
import View from 'ol/View.js'
import { RasterGISMap, type GISLegendEntry, type GISMapExtent, type GISRadarContext } from '../RasterGISMap'
import { FUZHOU_GIS_CONTEXT } from '../GISMapContexts'
import { radarDisplayExtent, radarSiteFor } from '../radarSites'
import { ReflectivityLegend } from '../ReflectivityLegend'
import { REFLECTIVITY_LEGEND, REFLECTIVITY_STOPS } from '../reflectivityPalette'
import { panelByID, qcFlagLabel, qcSweepOptions, radarIDs, type CycleList, type WorkspaceCycleDetail, type WorkspaceFrame, type WorkspacePanel } from './model'
import { readCycleCatalog } from './readCycleCatalog'
import { SharedTimeline } from './MainWorkspace'
import './radar-qc-workspace.css'

type Band = 'S' | 'X'
type Mode = 'single' | 'overlay' | 'fusion'
type Layout = 'pair' | 'swipe' | 'single'
type CandidateSite = { longitude_deg: number; latitude_deg: number; coordinate_source: 'draft_radar_config'; config_version: string }
type Station = { radar_id: string; display_name: string; geometry_status: string; registered: number; qc_ready: number; candidate_site?: CandidateSite | null }
type XStations = { items: Station[]; next_cursor: string; start: string; available_range: { end: string | null } }
type XScan = { scan_id: string; radar_id: string; volume_start: string; volume_end: string; state: string; qc_status: string; results: { result_id: string; version: string; finished_at: string }[] }
type XScans = { items: XScan[]; next_cursor: string }
type XMapGeometry = { crs: 'EPSG:4326'; bounds: GISMapExtent; longitude_deg: number; latitude_deg: number; maximum_range_km: number; coordinate_source: string; projection_version: string; raw: string; qc: string; flags: string }
type XSweep = { map?: XMapGeometry; sweep_number: number; sequence: number; elevation_deg: number; raw: string; qc: string; flags: string }
type XResult = { result_id: string; radar_id: string; scan_id: string; legend?: { minimum_dbzh: number; rgb: [number, number, number] }[]; sweeps: XSweep[] }
const prefix = '/api/v1/workspace/'
const X_FLAG_LEGEND: GISLegendEntry[] = [{ label: '确认无效／污染', color: '#bf3930' }, { label: '未决，待复核', color: '#eea028' }]
const X_REFERENCE_RADII_KM = [10, 20, 30, 40, 50] as const
const localDay = (value: string) => new Date(Date.parse(value) + 8 * 3600_000).toISOString().slice(0, 10)
const localClock = (value: string) => new Date(Date.parse(value) + 8 * 3600_000).toISOString().slice(11, 19)
const dayWindow = (day: string) => {
  if (!/^\d{4}-\d{2}-\d{2}$/.test(day)) return ''
  const start = Date.parse(`${day}T00:00:00+08:00`)
  return `&start=${encodeURIComponent(new Date(start).toISOString())}&end=${encodeURIComponent(new Date(start + 86_400_000).toISOString())}`
}
const slot = (time: string) => Math.floor(Date.parse(time) / 360_000)
function useJSON<T>(url: string, revision: number) {
  const [state, setState] = useState<{ url: string; revision: number; data?: T; error?: string }>()
  useEffect(() => {
    if (!url) return
    const controller = new AbortController()
    void fetch(url, { signal: controller.signal, cache: 'no-store' }).then(async response => {
      if (!response.ok) throw new Error(`读取失败（${response.status}）`)
      return response.json() as Promise<T>
    }).then(data => { if (!controller.signal.aborted) setState({ url, revision, data }) })
      .catch(error => { if (!controller.signal.aborted) setState({ url, revision, error: String(error) }) })
    return () => controller.abort()
  }, [url, revision])
  return state?.url === url && state.revision === revision ? state : undefined
}
function useSCatalog(revision: number) {
  const [state, setState] = useState<{ revision: number; data?: CycleList; error?: string }>()
  useEffect(() => {
    const controller = new AbortController()
    void readCycleCatalog(controller.signal).then(data => { if (!controller.signal.aborted) setState({ revision, data }) })
      .catch(error => { if (!controller.signal.aborted) setState({ revision, error: String(error) }) })
    return () => controller.abort()
  }, [revision])
  return state?.revision === revision ? state : undefined
}
function useXPair(sweep: XSweep | undefined, resultID: string | undefined, flags: boolean) {
  const key = sweep && resultID ? `${resultID}/${sweep.sweep_number}/${flags}` : ''
  const [state, setState] = useState<{ key: string; raw?: string; qc?: string; error?: string }>()
  useEffect(() => {
    if (!key || !sweep) return
    const controller = new AbortController()
    const urls: string[] = []
    const load = async (path: string) => {
      const response = await fetch(path, { signal: controller.signal })
      if (!response.ok) throw new Error(`图件读取失败（${response.status}）`)
      const blob = await response.blob()
      if (blob.type !== 'image/png') throw new Error('图件格式不是 PNG')
      if (controller.signal.aborted) return ''
      const url = URL.createObjectURL(blob)
      urls.push(url)
      const image = new Image()
      image.src = url
      await image.decode()
      return url
    }
    void Promise.all([load(sweep.map?.raw ?? sweep.raw), load(flags ? sweep.map?.flags ?? sweep.flags : sweep.map?.qc ?? sweep.qc)]).then(([raw, qc]) => {
      if (!controller.signal.aborted) setState({ key, raw, qc })
    }).catch(error => { if (!controller.signal.aborted) setState({ key, error: String(error) }) })
    return () => { controller.abort(); urls.forEach(URL.revokeObjectURL) }
  }, [key, sweep, flags])
  return state?.key === key ? state : undefined
}
function SMap({ title, frame, siteID, sharedView, active, note, flagLegend }: { title: string; frame?: WorkspaceFrame; siteID: string; sharedView: View; active: boolean; note?: string; flagLegend?: GISLegendEntry[] }) {
  const site = radarSiteFor(siteID)
  const extent: GISMapExtent = site ? radarDisplayExtent(site, frame?.maximum_range_km ?? site.maximumRangeKM) : [117.995, 24.995, 123.005, 27.005]
  const [failed, setFailed] = useState(false)
  return <section className={`radar-qc-map${active ? ' active' : ''}`} aria-label={title}>
    <header><strong>{title}</strong><span>{frame?.observation_time ? localClock(frame.observation_time) : frame ? localClock(frame.valid_time) : '该时刻无图件'}</span></header>
    <RasterGISMap imageUrl={frame?.image_url} imageDescription={`${siteID.toUpperCase()} ${title}`} imageExtent={extent} fitExtent={extent}
      validTimeLabel={frame ? localClock(frame.valid_time) : ''} contextLabel="S 波段 · 已定位" productLabel={title}
      legend={flagLegend ?? REFLECTIVITY_LEGEND} legendUnit={flagLegend ? '' : 'dBZ'} legendMode={flagLegend ? 'categorical' : 'scale'} footerNote={note ?? '原始与质控结果共用地图范围'} mapLabel={`${title}同步地图，EPSG:4326`}
      resetViewLabel="复位地图" emptyStateHint="此时刻没有匹配的原始/质控图件" loading={false} layerError={failed} onLayerError={setFailed}
      sharedView={sharedView} comparisonMode basemapVisible referenceContext={FUZHOU_GIS_CONTEXT} radarContext={site}
      rasterOpacity={1} rasterStyle="grid" zoomControls="hidden" />
  </section>
}
function XMap({ title, station, geometry, src, sharedView, time }: { title: string; station?: Station; geometry?: XMapGeometry; src?: string; sharedView: View; time: string }) {
  const [failed, setFailed] = useState(false)
  const radar: GISRadarContext | undefined = geometry && station ? {
    radarID: station.radar_id, displayName: station.display_name, radarBand: 'X',
    longitude: geometry.longitude_deg, latitude: geometry.latitude_deg,
    maximumRangeKM: geometry.maximum_range_km, displayRangeRadiiKM: geometry.maximum_range_km > 100 ? [50, 100, 150, 200, 250] : X_REFERENCE_RADII_KM,
    geometryStatus: 'unverified', coordinateSource: geometry.coordinate_source,
  } : undefined
  return <section className="radar-qc-map" aria-label={title}>
    <header><strong>{title}</strong><span>{time ? localClock(time) : ''}</span></header>
    {geometry && radar ? <RasterGISMap imageUrl={src} imageDescription={`${station?.radar_id.toUpperCase()} ${title}`} imageExtent={geometry.bounds} fitExtent={geometry.bounds}
      validTimeLabel={time ? localClock(time) : ''} contextLabel="X 波段" productLabel={title} legend={[]} footerNote=""
      mapLabel={`${title}同步地图，EPSG:4326`} resetViewLabel="复位地图" loading={!src}
      layerError={failed} onLayerError={setFailed} sharedView={sharedView} comparisonMode basemapVisible
      referenceContext={FUZHOU_GIS_CONTEXT} radarContext={radar} rasterOpacity={1} rasterStyle="grid" zoomControls="hidden" />
      : <div className="radar-qc-location-empty" role="status">该结果缺少地图图件，请刷新资料或生成新版结果。</div>}
  </section>
}

export function RadarQCWorkspace() {
  const [initial] = useState(() => new URLSearchParams(location.search))
  const [band, setBand] = useState<Band>(initial.get('band') === 'X' ? 'X' : 'S')
  const [mode, setMode] = useState<Mode>(initial.get('mode') === 'overlay' || initial.get('mode') === 'fusion' ? initial.get('mode') as Mode : 'single')
  const [layout, setLayout] = useState<Layout>('pair')
  const [day, setDay] = useState(initial.get('date') ?? '')
  const [target, setTarget] = useState(initial.get('time') ?? '')
  const [sStationID, setSStationID] = useState(initial.get('band') === 'X' ? '' : initial.get('station') ?? '')
  const [xStationID, setXStationID] = useState(initial.get('band') === 'X' ? initial.get('station') ?? '' : '')
  const [xScanID, setXScanID] = useState(initial.get('scan') ?? '')
  const [xResultID, setXResultID] = useState(initial.get('result') ?? '')
  const [sSweep, setSSweep] = useState<number | null>(null)
  const [xSweep, setXSweep] = useState<number | null>(initial.has('sweep') ? Number(initial.get('sweep')) : null)
  const [flags, setFlags] = useState(false)
  const [playing, setPlaying] = useState(false)
  const [speed, setSpeed] = useState(1200)
  const [revision, setRevision] = useState(0)
  const [overlayS, setOverlayS] = useState(true)
  const [overlayX, setOverlayX] = useState(true)
  const sharedView = useMemo(() => new View({ center: [119.1, 26.1], zoom: 7, projection: 'EPSG:4326' }), [])
  const catalog = useSCatalog(revision)
  const xStations = useJSON<XStations>(`${prefix}radar-stations?band=X${dayWindow(day)}`, revision)
  const linkedCycle = initial.get('cycle') ? catalog?.data?.items.find(c=>c.cycle_id===initial.get('cycle')) : undefined
  const date = day || (band === 'X' ? xStations?.data ? localDay(xStations.data.start) : '' : linkedCycle ? localDay(linkedCycle.issue_time) : catalog?.data?.items[0] ? localDay(catalog.data.items[0].issue_time) : '')
  const sTimes = (catalog?.data?.items ?? []).filter(c=>localDay(c.issue_time)===date && c.capabilities.radar).sort((a,b)=>Date.parse(a.issue_time)-Date.parse(b.issue_time))
  const sCycle = target ? sTimes.find(c=>slot(c.issue_time)===slot(target)) : linkedCycle ?? sTimes.at(-1)
  const sDetailState = useJSON<WorkspaceCycleDetail>(sCycle ? `/api/v1/workspace/cycles/${encodeURIComponent(sCycle.cycle_id)}` : '', revision)
  const sDetail = sDetailState?.data && sDetailState.data.cycle_id === sCycle?.cycle_id ? sDetailState.data : undefined
  const sIDs = [...new Set([sStationID, ...(sDetail ? radarIDs(sDetail) : [])].filter(Boolean))]
  const selectedSID = sStationID || sIDs.find(id=>sDetail && panelByID(sDetail, `dbzh_raw:${id}`)?.frames.length && panelByID(sDetail, `dbzh_qc:${id}`)?.frames.length) || sIDs[0] || ''
  const selectedX = xStationID ? xStations?.data?.items.find(s=>s.radar_id===xStationID) : xStations?.data?.items.find(s=>s.qc_ready>0) ?? xStations?.data?.items[0]
  const xScans = useJSON<XScans>(selectedX && date ? `${prefix}radar-scans?radar_id=${encodeURIComponent(selectedX.radar_id)}${dayWindow(date)}` : '', revision)
  const scans = useMemo(() => [...(xScans?.data?.items ?? [])].sort((a,b)=>Date.parse(a.volume_start)-Date.parse(b.volume_start)), [xScans?.data])
  const scan = xScanID ? scans.find(s=>s.scan_id===xScanID) : target ? scans.find(s=>slot(s.volume_start)===slot(target)) : [...scans].reverse().find(s=>s.results.length>0) ?? scans.at(-1)
  const xSelectedResult = xResultID ? scan?.results.find(r=>r.result_id===xResultID) : scan?.results[0]
  const xResultState = useJSON<XResult>(xSelectedResult ? `${prefix}radar-products/${encodeURIComponent(xSelectedResult.result_id)}` : '', revision)
  const xResult = xResultState?.data && xResultState.data.scan_id===scan?.scan_id && xResultState.data.radar_id===selectedX?.radar_id ? xResultState.data : undefined
  const xCut = xResult?.sweeps.find(s=>s.sweep_number===xSweep) ?? xResult?.sweeps.reduce<XSweep|undefined>((a,b)=>!a||b.elevation_deg<a.elevation_deg?b:a,undefined)
  const xPair = useXPair(xCut,xSelectedResult?.result_id,flags)
  const sOptions = sDetail ? qcSweepOptions(sDetail,selectedSID,sCycle?.issue_time ?? null) : []
  const selectedSSweep = sOptions.some(s=>s.number===sSweep) ? sSweep : sOptions[0]?.number
  const sRawPanel = sDetail ? panelByID(sDetail,`dbzh_raw:${selectedSID}`) : null
  const sQCPanel = sDetail ? panelByID(sDetail,`dbzh_qc:${selectedSID}`) : null
  const sFlagPanel = sDetail ? panelByID(sDetail,`qc_flags:${selectedSID}`) : null
  const sFlagLegend = sFlagPanel?.legend?.map(entry=>({label:qcFlagLabel(entry.label??''),color:entry.color})) ?? []
  const sRaw = sRawPanel?.frames.find(f=>f.sweep_number===selectedSSweep && f.valid_time===sCycle?.issue_time)
  const sQC = (flags ? sFlagPanel : sQCPanel)?.frames.find(f=>f.sweep_number===selectedSSweep && f.scan_id===sRaw?.scan_id && f.valid_time===sRaw?.valid_time)
  const xUnifiedPalette = xResult?.legend?.length===REFLECTIVITY_STOPS.length && xResult.legend.every((entry,i)=>entry.minimum_dbzh===REFLECTIVITY_STOPS[i][0] && `#${entry.rgb.map(c=>c.toString(16).padStart(2,'0')).join('')}`===REFLECTIVITY_STOPS[i][1])
  const activeTime = target || (band==='S' ? sCycle?.issue_time : scan?.volume_start) || ''
  const timeline = band==='S' ? sTimes.map(c=>c.issue_time) : scans.map(s=>s.volume_start)
  const activeTimelineTime = band==='S' ? sCycle?.issue_time : scan?.volume_start
  const timelinePanels:WorkspacePanel[] = band==='S' ? [{panel_id:'s-analysis',algorithm_id:'s-qc',display_name:'S 分析周期',role:'qc',lifecycle:'analysis',data_kind:'reflectivity',cadence_minutes:6,status:'ready',frames:sTimes.map(c=>({asset_id:c.cycle_id,valid_time:c.issue_time,lead_time_minutes:0,image_url:c.cycle_id,media_type:'application/json'}))}] : [{panel_id:'x-qc',algorithm_id:'x-qc',display_name:'X 质控结果',role:'qc',lifecycle:'shadow',data_kind:'reflectivity',cadence_minutes:6,status:'ready',frames:scans.filter(s=>s.results.length).map(s=>({asset_id:s.scan_id,valid_time:s.volume_start,lead_time_minutes:0,image_url:s.results[0].result_id,media_type:'image/png'}))}]
  const issue = timeline[0] ?? `${date || '2026-08-28'}T00:00:00+08:00`
  const selectable = band==='S' ? Boolean(sRaw&&sQC) : Boolean(xPair?.raw&&xPair.qc)
  useEffect(()=>{
    const params=new URLSearchParams({preset:'qc',band,mode})
    if(date)params.set('date',date)
    if(activeTime)params.set('time',activeTime)
    const station=band==='S'?selectedSID:selectedX?.radar_id
    if(station)params.set('station',station)
    if(band==='X'&&scan){params.set('scan',scan.scan_id);if(xSelectedResult)params.set('result',xSelectedResult.result_id);if(xCut)params.set('sweep',String(xCut.sweep_number))}
    if(band==='S'&&sCycle){params.set('cycle',sCycle.cycle_id);if(selectedSSweep!=null)params.set('sweep',String(selectedSSweep))}
    history.replaceState({},'',`/?${params}`)
  },[band,mode,date,activeTime,selectedSID,selectedX,scan,xSelectedResult,xCut,sCycle,selectedSSweep])
  useEffect(()=>{
    if(!playing||!selectable||timeline.length<2)return
    const at=timeline.findIndex(t=>t===activeTimelineTime)
    const timer=window.setTimeout(()=>{
      const next=timeline[(at+1)%timeline.length]
      setTarget(next);setXScanID('');setXResultID('');setXSweep(null)
    },speed)
    return()=>window.clearTimeout(timer)
  },[playing,selectable,activeTimelineTime,timeline,speed])
  function changeBand(next:Band){if(next===band)return;setDay(date);setTarget(activeTime);setXScanID('');setXResultID('');setPlaying(false);setBand(next)}
  function chooseTime(time:string){setTarget(time);setXScanID('');setXResultID('');setXSweep(null);setPlaying(false)}
  const xError=xStations?.error||xScans?.error||xResultState?.error||xPair?.error
  const sError=catalog?.error||sDetailState?.error
  const issueText=band==='X' ? selectedX ? `${selectedX.radar_id.toUpperCase()} · ${scan?`${localClock(scan.volume_start)}–${localClock(scan.volume_end)}`:'该时刻无体扫'}`:'正在读取站点' : `${selectedSID.toUpperCase() || 'S 波段'} · ${sCycle?localClock(sCycle.issue_time):'该时刻无分析周期'}`
  const xGeometryReady=false // Cross-station overlay still requires verified station geometry.
  return <main className="workspace-shell radar-qc-shell">
    <header className="workspace-topbar"><a className="workspace-brand" href="/"><span aria-hidden="true"><i/><i/><i/></span><strong>RainPulse</strong><small>短临降水工作台</small></a><span className="radar-qc-header-status">质控排查 · 历史资料</span><a className="admin-link" href="/admin">后台</a></header>
    <section className="radar-qc-navigation" aria-label="质控导航"><nav className="preset-tabs" aria-label="工作台预设"><a href="/?preset=forecast">预报对比</a><a className="active" href="/?preset=qc" aria-current="page">质控排查</a><a href="/?preset=verification">检验回放</a></nav><div className="radar-qc-modes" role="group" aria-label="验证方式">{([{key:'single',text:'单站验证'},{key:'overlay',text:'同图叠加'},{key:'fusion',text:'融合验证'}] as const).map(item=><button key={item.key} aria-pressed={mode===item.key} onClick={()=>{setMode(item.key);setPlaying(false)}}>{item.text}</button>)}</div></section>
    <section className="radar-qc-controls" aria-label="资料选择"><div className="radar-qc-band" role="group" aria-label="雷达波段"><span>波段</span>{(['S','X'] as const).map(value=><button key={value} aria-pressed={band===value} onClick={()=>changeBand(value)}>{value}</button>)}</div><label>站点<select aria-label="站点" value={band==='S'?selectedSID:selectedX?.radar_id??''} onChange={event=>{if(band==='S'){setSStationID(event.target.value);setSSweep(null)}else{setXStationID(event.target.value);setXScanID('');setXResultID('');setXSweep(null)}setPlaying(false)}}>{band==='S'?sIDs.map(id=><option key={id} value={id}>{id.toUpperCase()} · {radarSiteFor(id)?.displayName??'S 波段'}</option>):xStations?.data?.items.map(s=><option key={s.radar_id} value={s.radar_id}>{s.radar_id.toUpperCase()} · {s.display_name}</option>)}</select></label><label>仰角<select aria-label="仰角" value={band==='S'?selectedSSweep??'':xCut?.sweep_number??''} disabled={band==='S'?!sOptions.length:!xResult} onChange={event=>{if(band==='S')setSSweep(Number(event.target.value));else setXSweep(Number(event.target.value));setPlaying(false)}}>{band==='S'?sOptions.map(s=><option key={s.number} value={s.number}>{s.elevation.toFixed(2)}° · 编号 {s.number}</option>):xResult?.sweeps.map(s=><option key={s.sweep_number} value={s.sweep_number}>{s.elevation_deg.toFixed(2)}° · 编号 {s.sweep_number}</option>)}</select></label><label>字段<select aria-label="字段" value="dbzh" onChange={()=>{}}><option value="dbzh">反射率 · dBZ</option></select></label><div className="radar-qc-more"><button aria-label="刷新资料" onClick={()=>{setXResultID('');setRevision(n=>n+1)}}>刷新</button><details><summary>更多</summary><div><a href="/qc-review">证据复核</a><a href={band==='X'?`/admin?view=new&preset=x_qc&radar=${encodeURIComponent(selectedX?.radar_id??'')}`:'/admin?view=new'}>生成／重算</a></div></details></div></section>
    {(band==='S'?sError:xError)&&<div className="radar-qc-alert" role="alert">{band==='S'?sError:xError} <button onClick={()=>setRevision(n=>n+1)}>重试</button></div>}
    <div className="radar-qc-summary"><strong>{issueText}</strong><span>{band==='X'?'X 原始与质控结果':'S 已定位图层 · 原始与质控结果'}</span><button onClick={()=>{setFlags(v=>!v);setPlaying(false)}} aria-pressed={flags}>{flags?'返回反射率':'质控标记'}</button></div>
    <section className="radar-qc-stage" aria-label="质控图层">
      {mode==='fusion'?<div className="radar-qc-gate"><strong>融合候选尚不能在地图验证</strong><p>X 站点的空间定位与融合资格仍待核验。已生成的候选任务可在后台查看；符合同网格、同时间及来源身份的产品进入此处后，将显示 S 贡献、X 贡献和融合结果。</p><a href="/admin?view=new&preset=sx_composite">查看候选任务</a></div>:mode==='overlay'?<div className="radar-qc-overlay"><div className="radar-qc-layer-list"><h2>同图图层</h2><label><input type="checkbox" checked={overlayS} onChange={e=>setOverlayS(e.target.checked)}/>S · {selectedSID.toUpperCase()||'选择站点'} 质控后</label><label><input type="checkbox" checked={overlayX&&xGeometryReady} disabled={!xGeometryReady} onChange={e=>setOverlayX(e.target.checked)}/>X · {selectedX?.radar_id.toUpperCase()??'选择站点'} 质控后</label><p>{overlayX&&!xGeometryReady?'X 单站地图可查看；跨站叠加资格待核验。':''}</p></div>{overlayS&&sQC&&selectedSID?<SMap title="S 波段质控后" frame={sQC} siteID={selectedSID} sharedView={sharedView} active note="X 尚未取得地图显示资格"/>:<div className="radar-qc-gate"><strong>该时刻没有可定位的图层</strong><p>选择 S 分析时次或启用 S 图层。</p></div>}</div>:<div className={`radar-qc-pair layout-${layout}`}>
        {band==='S'&&selectedSID?<><SMap title="原始反射率" frame={sRaw} siteID={selectedSID} sharedView={sharedView} active={layout!=='single'}/><SMap title={flags?'质控标记':'质控后反射率'} frame={sQC} siteID={selectedSID} sharedView={sharedView} active note={flags?'标记颜色表示质控动作，不代表反射率强度':undefined} flagLegend={flags ? sFlagLegend : undefined}/></>:band==='X'&&scan?.results.length?<><XMap title="原始反射率" src={xPair?.raw} station={selectedX} geometry={xCut?.map} sharedView={sharedView} time={scan.volume_start}/><XMap title={flags?'质控标记':'质控后反射率'} src={xPair?.qc} station={selectedX} geometry={xCut?.map} sharedView={sharedView} time={scan.volume_start}/></>:<div className="radar-qc-gate"><strong>{band==='X'?scan?scan.qc_status:'该时刻无体扫':'该时刻无分析周期'}</strong><p>目标时刻保持不变。可在底部时间轴选择有资料的时次。</p></div>}
      </div>}
    </section>
    <section className="radar-qc-map-tools" aria-label="地图工具"><div role="group" aria-label="对照布局">{(['pair','swipe','single'] as const).map(value=><button key={value} aria-pressed={layout===value} onClick={()=>setLayout(value)} disabled={mode!=='single'}>{({pair:'双图',swipe:'卷帘',single:'单图'} as const)[value]}</button>)}</div>{flags&&mode==='single'?<div className="radar-qc-flag-legend" aria-label="质控标记图例">{(band==='X'?X_FLAG_LEGEND:sFlagLegend).map(item=><span key={item.label}><i style={{background:item.color}}/>{item.label}</span>)}</div>:band==='X'&&xResult?.legend?.length&&mode==='single'?<div className="radar-qc-palette"><strong>dBZ</strong><ReflectivityLegend entries={xResult.legend.map(e=>({minimum:e.minimum_dbzh,label:String(e.minimum_dbzh),color:`rgb(${e.rgb.join(',')})`}))}/></div>:band==='S'&&mode==='single'?<div className="radar-qc-palette"><strong>dBZ</strong><ReflectivityLegend/></div>:null}</section>
    {band==='X'&&xResult&&!xUnifiedPalette&&<p className="radar-qc-legacy">所选历史结果使用旧色谱，图件与色标均保持原版本。</p>}
    <details className="radar-qc-details"><summary>资料详情</summary><dl><dt>目标时间</dt><dd>{activeTime||'未选择'}</dd><dt>结果</dt><dd>{band==='X'?xSelectedResult?.result_id??'无':sCycle?.cycle_id??'无'}</dd><dt>站点资格</dt><dd>{band==='X'?'按体扫站点坐标、逐射线方位/仰角和距离门定位；站点坐标及业务融合资格待核验':'S 站点已定位'}</dd></dl></details>
    <SharedTimeline observationOnly observationLabel={band==='S'?'分析周期':'体扫'} issueTime={issue} values={timeline} panels={timelinePanels} selectedTime={activeTimelineTime??null} playing={playing} playSpeedMS={speed} onPlaySpeedChange={setSpeed} onTogglePlaying={()=>{if(!playing&&!selectable){const first=timeline[0];if(first)chooseTime(first)}setPlaying(v=>!v)}} onSelect={chooseTime} cycleControls={<div className="radar-qc-time-context"><strong>{mode==='single'?'单站验证':mode==='overlay'?'同图叠加':'融合验证'}</strong><label>历史资料日期<input aria-label="资料日期" type="date" value={date} onChange={e=>{setDay(e.target.value);setTarget('');setXScanID('');setXResultID('');setPlaying(false)}}/></label><span>{band==='S'?'S 分析周期':'X 真实体扫'} · 北京时间 UTC+8</span></div>}/>
  </main>
}
