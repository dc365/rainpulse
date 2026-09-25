import { useEffect, useRef, useState } from 'react'
import './x-qc-workspace.css'
import { SharedTimeline } from './MainWorkspace'
import { ReflectivityLegend } from '../ReflectivityLegend'
import { REFLECTIVITY_STOPS } from '../reflectivityPalette'
import type { WorkspacePanel } from './model'

type Page<T> = { items: T[]; next_cursor: string }
type Station = { radar_id: string; display_name: string; registered: number; normalized: number; qc_ready: number }
type Stations = Page<Station> & { start: string; end: string; available_range: { start: string | null; end: string | null } }
type Result = { result_id: string; version: string; finished_at: string }
type Scan = { scan_id: string; radar_id: string; volume_start: string; volume_end: string; state: string; qc_status: string; error_message?: string; results: Result[] }
type Sweep = { sweep_number: number; sequence: number; elevation_deg: number; raw: string; qc: string; flags: string }
type Comparison = { result_id: string; radar_id: string; scan_id: string; volume_start: string; volume_end: string; sweeps: Sweep[]; legend?: {minimum_dbzh: number; rgb: [number, number, number]}[] }
const prefix = '/api/v1/workspace/'
const labels: Record<string, string> = { NORMALIZED: '已解码', REGISTERED: '已登记', RAW_RECEIVED: '已接收', DECODING: '解码中', READY: '可对照', ASSET_UNAVAILABLE: '图件不可用', WAITING_QC: '待质控', WAITING_DECODE: '待解码', FAILED: '失败', BLOCKED: '受阻', CANCELLED: '已取消', RUNNING: '计算中', COMMITTING: '发布中', QUEUED: '排队中', WAITING: '等待中' }
function label(state: string) { return labels[state] ?? state }
function localDate(value: string) { return new Date(Date.parse(value) + 8 * 3600000).toISOString().slice(0, 10) }
function localTime(value: string) { return new Date(Date.parse(value) + 8 * 3600000).toISOString().slice(11, 19) }
function dayQuery(day: string) {
 if (!day) return ''
 const start = new Date(`${day}T00:00:00+08:00`)
 if (!Number.isFinite(start.getTime())) return ''
 return `&start=${encodeURIComponent(start.toISOString())}&end=${encodeURIComponent(new Date(start.getTime() + 86400000).toISOString())}`
}
async function read<T>(url: string, signal: AbortSignal): Promise<T> {
 const response = await fetch(url, { signal, cache: 'no-store' })
 if (!response.ok) { const error = await response.json().catch(() => ({})); throw new Error(error.message ?? `读取失败（${response.status}）`) }
 return response.json()
}
// Keep the response attached to its query, so a late response can never relabel a new selection.
function useRead<T>(url: string, revision: number) {
 const [result, setResult] = useState<{url: string; revision: number; data?: T; error?: string}>()
 useEffect(() => {
  if (!url) return
  const controller = new AbortController()
  void read<T>(url, controller.signal).then(data => { if (!controller.signal.aborted) setResult({url, revision, data}) })
   .catch(error => { if (!controller.signal.aborted) setResult({url, revision, error: String(error.message ?? error)}) })
  return () => controller.abort()
 }, [url, revision])
 return result?.url === url && result.revision === revision ? result : undefined
}
function usePage<T, P extends Page<T>>(url: string, revision: number) {
 const first = useRead<P>(url, revision)
 const [extra, setExtra] = useState<{key: string; items: T[]; cursor: string; error?: string}>()
 const [busy, setBusy] = useState(false)
 const active = useRef<AbortController | null>(null)
 const queryKey = `${url}|${revision}`
 useEffect(() => () => active.current?.abort(), [queryKey])
 const added = extra?.key === queryKey ? extra : undefined
 const page = first?.data
 const cursor = added?.cursor ?? page?.next_cursor ?? ''
 async function more() {
  if (!cursor || busy) return
  const controller = new AbortController(); active.current = controller; setBusy(true)
  try {
   const next = await read<P>(`${url}&cursor=${encodeURIComponent(cursor)}`, controller.signal)
   if (!controller.signal.aborted) setExtra({key: queryKey, items: [...(added?.items ?? []), ...next.items], cursor: next.next_cursor})
  } catch (error) { if (!controller.signal.aborted) setExtra({key: queryKey, items: added?.items ?? [], cursor, error: String(error)}) }
  finally { setBusy(false) }
 }
 return { data: page, items: page ? [...page.items, ...(added?.items ?? [])] : [], error: first?.error ?? added?.error, cursor, more, busy }
}

export function XQCWorkspace() {
 const [initial] = useState(() => new URLSearchParams(window.location.search))
 const [day, setDay] = useState(() => { const value = initial.get('date') ?? ''; return /^\d{4}-\d{2}-\d{2}$/.test(value) && Number.isFinite(Date.parse(value)) ? value : '' })
 const [stationID, setStationID] = useState(initial.get('station') ?? '')
 const [scanID, setScanID] = useState(initial.get('scan') ?? '')
 const [resultID, setResultID] = useState(initial.get('result') ?? '')
 const [sweepNumber, setSweepNumber] = useState<number | null>(initial.has('sweep') ? Number(initial.get('sweep')) : null)
 const [revision, setRevision] = useState(0)
 const [flags, setFlags] = useState(false)
 const [playing, setPlaying] = useState(false)
 const [playSpeedMS, setPlaySpeedMS] = useState(1200)
 const [zoom, setZoom] = useState(1)
 const [pan, setPan] = useState({x: 0, y: 0})
 const drag = useRef<{x: number; y: number; originX: number; originY: number} | null>(null)
 const stations = usePage<Station, Stations>(`${prefix}radar-stations?band=X${dayQuery(day)}`, revision)
 const selectedStation = stationID ? stations.items.find(s => s.radar_id === stationID) : stations.items.find(s => s.qc_ready > 0) ?? stations.items[0]
 const date = day || (stations.data ? localDate(stations.data.start) : '')
 const scans = usePage<Scan, Page<Scan>>(selectedStation && date ? `${prefix}radar-scans?radar_id=${encodeURIComponent(selectedStation.radar_id)}${dayQuery(date)}` : '', revision)
 const selectedScan = scanID ? scans.items.find(s => s.scan_id === scanID) : scans.items.find(s => s.results.length > 0) ?? scans.items[0]
 useEffect(() => { if (stationID && stations.data && !selectedStation && stations.cursor && !stations.busy) void stations.more() }, [stationID, stations, selectedStation])
 useEffect(() => { if (scanID && scans.data && !selectedScan && scans.cursor && !scans.busy) void scans.more() }, [scanID, scans, selectedScan])
 const selectedResult = resultID ? selectedScan?.results.find(r => r.result_id === resultID) : selectedScan?.results[0]
 const result = useRead<Comparison>(selectedResult ? `${prefix}radar-products/${encodeURIComponent(selectedResult.result_id)}` : '', revision)
 const detail = result?.data
 const validDetail = detail && detail.scan_id === selectedScan?.scan_id && detail.radar_id === selectedStation?.radar_id ? detail : undefined
 const sweep = validDetail?.sweeps.find(s => s.sweep_number === sweepNumber) ?? validDetail?.sweeps.reduce<Sweep | undefined>((lowest, s) => !lowest || s.elevation_deg < lowest.elevation_deg ? s : lowest, undefined)
 const unifiedPalette = validDetail?.legend?.length === REFLECTIVITY_STOPS.length && validDetail.legend.every((entry,i)=>entry.minimum_dbzh===REFLECTIVITY_STOPS[i][0] && `#${entry.rgb.map(c=>c.toString(16).padStart(2,'0')).join('')}`===REFLECTIVITY_STOPS[i][1])
 const pairKey = sweep && selectedResult ? `${selectedResult.result_id}/${sweep.sweep_number}/${flags}/${revision}` : ''
 const rawURL = sweep?.raw ?? '', qcURL = (flags ? sweep?.flags : sweep?.qc) ?? ''
 const [images, setImages] = useState<{key: string; raw?: string; qc?: string; error?: string}>()
 useEffect(() => {
  if (!pairKey) return
  const controller = new AbortController(); const urls: string[] = []
  async function load(url: string) {
   const response = await fetch(url, {signal: controller.signal})
   if (!response.ok) throw new Error(`图件读取失败（${response.status}）`)
   const blob = await response.blob()
   if (blob.type !== 'image/png') throw new Error('图件格式不是 PNG')
   if (controller.signal.aborted) return ''
   const objectURL = URL.createObjectURL(blob); urls.push(objectURL); return objectURL
  }
  void Promise.all([load(rawURL), load(qcURL)]).then(([raw, qc]) => { if (!controller.signal.aborted) setImages({key: pairKey, raw, qc}) })
   .catch(error => { if (!controller.signal.aborted) setImages({key: pairKey, error: String(error.message ?? error)}) })
  return () => { controller.abort(); urls.forEach(URL.revokeObjectURL) }
 }, [pairKey, rawURL, qcURL])
 const pair = images?.key === pairKey ? images : undefined
 const timeline = [...scans.items].sort((a, b) => Date.parse(a.volume_start) - Date.parse(b.volume_start))
 const index = timeline.findIndex(s => s.scan_id === selectedScan?.scan_id)
 function chooseScan(id: string) { setPlaying(false); setScanID(id); setResultID(''); setSweepNumber(null); setPan({x: 0, y: 0}); setZoom(1) }
 useEffect(() => {
  if (!playing || !pair?.raw || !pair.qc) return
  const next = timeline.slice(index + 1).find(s => s.results.length) ?? timeline.find(s => s.results.length)
  if (!next) return
  const timer = window.setTimeout(() => { setScanID(next.scan_id); setResultID(''); setSweepNumber(null) }, playSpeedMS)
  return () => window.clearTimeout(timer)
 }, [playing, pair, index, timeline, playSpeedMS])
 useEffect(() => {
  const params = new URLSearchParams({preset: 'qc', band: 'X'})
  if (date) params.set('date', date)
  if (selectedStation || stationID) params.set('station', selectedStation?.radar_id ?? stationID)
  if (selectedScan || scanID) params.set('scan', selectedScan?.scan_id ?? scanID)
  if (selectedResult || resultID) params.set('result', selectedResult?.result_id ?? resultID)
  if (sweep || sweepNumber !== null) params.set('sweep', String(sweep?.sweep_number ?? sweepNumber))
  window.history.replaceState({}, '', `/?${params}`)
 }, [date, selectedStation, selectedScan, selectedResult, sweep, stationID, scanID, resultID, sweepNumber])
 const linkProblem = stations.data && stationID && !selectedStation && !stations.cursor ? '链接中的站点未登记，请从清单选择站点。' : scans.data && scanID && !selectedScan ? '链接中的体扫尚未找到；请加载更早体扫，或选择其他时次。' : selectedScan && resultID && !selectedResult ? '指定结果版本不可用，请选择其他体扫或重新打开站点。' : ''
 const problem = linkProblem || (stations.error ?? scans.error ?? result?.error ?? pair?.error ?? (detail && !validDetail ? '结果身份与所选体扫不一致' : ''))
 const recomputeStart = selectedScan?.volume_start ?? `${date}T00:00:00+08:00`
 const recomputeEnd = date ? new Date(selectedScan ? Date.parse(selectedScan.volume_end) + 1 : Date.parse(recomputeStart) + 360000).toISOString() : ''
 const timelinePanels: WorkspacePanel[] = [{panel_id:'x-qc', algorithm_id:'x-qc', display_name:'X 波段质控对照', role:'qc', lifecycle:'shadow', data_kind:'reflectivity', cadence_minutes:6, status:'ready', frames:timeline.filter(s=>s.results.length>0).map(s=>({asset_id:s.scan_id, valid_time:s.volume_start, lead_time_minutes:0, image_url:s.results[0].result_id, media_type:'image/png'}))}]
 return <main className="workspace-shell xqc-shell">
  <header className="workspace-topbar"><a className="workspace-brand" href="/"><span aria-hidden="true"><i /><i /><i /></span><strong>RainPulse</strong><small>短临降水工作台</small></a><span className="xqc-context">X 波段 · 原生体扫质控</span><a className="admin-link" href="/admin">后台</a></header>
  <section className="workspace-controls qc-controls" aria-label="工作台控制">
   <div className="preset-tabs" role="tablist" aria-label="工作台预设">{[{id:'forecast',label:'预报对比'},{id:'qc',label:'质控排查'},{id:'verification',label:'检验回放'}].map(p=><button key={p.id} role="tab" aria-selected={p.id==='qc'} className={p.id==='qc'?'active':''} onClick={()=>{if(p.id!=='qc')window.location.href=`/?preset=${p.id}`}}>{p.label}</button>)}</div>
   <label className="radar-selector"><span>雷达</span><select aria-label="雷达" value={selectedStation?.radar_id ?? ''} onChange={e=>{setStationID(e.target.value);setScanID('');setResultID('');setSweepNumber(null);setPlaying(false);setPan({x:0,y:0});setZoom(1)}}>{stations.items.map(s=><option key={s.radar_id} value={s.radar_id}>{s.radar_id.toUpperCase()} · X</option>)}</select></label>
   <label className="history-selector"><span>资料日期</span><input aria-label="资料日期" type="date" value={date} onChange={e=>{setDay(e.target.value);setScanID('');setResultID('');setPlaying(false)}} /></label>
   <div className="map-tools" aria-label="雷达波段"><a className="admin-link" href="/?preset=qc">S 波段</a><button className="active" aria-pressed="true">X 波段</button><button onClick={()=>setRevision(v=>v+1)}>刷新资料</button><a className="admin-link" href={`/admin?view=new&preset=x_qc&radar=${encodeURIComponent(selectedStation?.radar_id ?? '')}&start=${encodeURIComponent(recomputeStart)}&end=${encodeURIComponent(recomputeEnd)}`}>生成／重算</a></div>
  </section>
  <div className="xqc-notice">历史资料 · 北京时间 UTC+8 · 基础质控候选，尚未取得业务融合资格。站心 PPI 不依赖地图坐标核验。</div>
  {problem && <div className="xqc-error" role="alert">{problem} <button onClick={() => setRevision(v => v + 1)}>重试读取</button></div>}
  <div className="xqc-body">
   <section className="xqc-content" aria-label="X 波段质控对照">
    <div className="xqc-selection"><div><h1>{selectedStation?.radar_id.toUpperCase() ?? 'X 波段'} <span>质控前后对照</span></h1><p>{selectedScan ? `${date} ${localTime(selectedScan.volume_start)} — ${localTime(selectedScan.volume_end)} · 资料状态 ${label(selectedScan.state)}` : '选择站点和有资料的日期'}</p></div><span className="xqc-candidate">候选结果 · {selectedStation?.qc_ready ?? 0}/{selectedStation?.registered ?? 0} 体扫可对照</span></div>
    {selectedResult && <div className="xqc-image-controls">
     <label>仰角层<select aria-label="仰角层" value={sweep?.sweep_number ?? ''} disabled={!validDetail} onChange={e => {setSweepNumber(Number(e.target.value));setPlaying(false)}}>{validDetail?.sweeps.map(s => <option key={s.sweep_number} value={s.sweep_number}>{s.elevation_deg.toFixed(2)}° · 第 {s.sequence} 层 · 编号 {s.sweep_number}</option>)}</select></label>
     <button aria-pressed={flags} onClick={() => setFlags(v => !v)}>{flags ? '返回前后对照' : '质控标记'}</button>
     <label>缩放<input aria-label="对照缩放" type="range" min="1" max="4" step="0.25" value={zoom} onChange={e => setZoom(Number(e.target.value))} /></label><button onClick={() => {setZoom(1);setPan({x:0,y:0})}}>复位</button>
     <label>结果版本<select aria-label="结果版本" value={selectedResult.result_id} onChange={e => {setResultID(e.target.value);setPlaying(false)}}>{selectedScan?.results.map(r => <option key={r.result_id} value={r.result_id}>{r.version} · {localDate(r.finished_at)} {localTime(r.finished_at)}</option>)}</select></label>
    </div>}
    {selectedResult ? <div className="xqc-compare">
     {[{name:'原始反射率',src:pair?.raw},{name:flags?'质控动作标记':'基础质控后',src:pair?.qc}].map((image,i) => <figure key={i}><figcaption>{image.name}<small>{i === 0 ? '原始观测 · dBZ' : flags ? '红：确认无效／污染 · 琥珀：待复核' : unifiedPalette ? '确认剔除门隐藏 · 待复核门保留反射率' : '历史色谱 · 琥珀色为待复核标记'}</small></figcaption>
      <div className="xqc-image-viewport" tabIndex={0} aria-label={`${image.name}联动视图`} onPointerDown={e=>{if(!image.src)return;e.currentTarget.setPointerCapture(e.pointerId);drag.current={x:e.clientX,y:e.clientY,originX:pan.x,originY:pan.y}}} onPointerMove={e=>{if(drag.current)setPan({x:drag.current.originX+e.clientX-drag.current.x,y:drag.current.originY+e.clientY-drag.current.y})}} onPointerUp={()=>{drag.current=null}} onPointerCancel={()=>{drag.current=null}} onKeyDown={e=>{const offsets:Record<string,[number,number]>={ArrowLeft:[-20,0],ArrowRight:[20,0],ArrowUp:[0,-20],ArrowDown:[0,20]};const offset=offsets[e.key];if(offset){e.preventDefault();setPan(p=>({x:p.x+offset[0],y:p.y+offset[1]}))}}}>
       {image.src ? <img draggable={false} src={image.src} alt={i===0?'原始反射率 PPI':flags?'质控动作 PPI':'基础质控后 PPI'} style={{transform:`translate(${pan.x}px, ${pan.y}px) scale(${zoom})`}} /> : <p role="status">{problem ? '图件不可用，请重试。' : '正在校验并读取同一扫层图对…'}</p>}
      </div></figure>)}
    </div> : <div className="xqc-empty"><strong>{selectedScan ? label(selectedScan.qc_status) : scans.data ? '本日没有已登记体扫' : '正在读取体扫目录…'}</strong><p>{selectedScan?.qc_status==='WAITING_QC' ? '该体扫已解码，等待基础质控。' : selectedScan?.qc_status==='WAITING_DECODE' ? '该体扫已登记，等待解码。' : selectedScan?.error_message || '请选择有资料日期，或刷新查看任务进度。'}</p>{stations.data?.available_range.end && <button onClick={()=>{setDay(localDate(stations.data!.available_range.end!));setScanID('');setResultID('')}}>转到最近有资料日期</button>}</div>}
    {validDetail?.legend?.length ? <div className="xqc-legend" aria-label="反射率色标"><strong>dBZ</strong><ReflectivityLegend entries={validDetail.legend.map(e=>({minimum:e.minimum_dbzh,label:String(e.minimum_dbzh),color:`rgb(${e.rgb.join(',')})`}))} /></div> : null}
    <p className="xqc-footnote">原生站心极坐标 · {unifiedPalette ? 'S/X 共用反射率色谱' : '历史结果保留原色谱，可生成新版对照'} · 缺测透明 · 待复核门请查看质控标记</p>
    {selectedScan && <details className="xqc-lineage"><summary>资料来源与结果身份</summary><dl><dt>体扫</dt><dd>{selectedScan.scan_id}</dd><dt>观测 UTC</dt><dd>{selectedScan.volume_start} — {selectedScan.volume_end}</dd><dt>结果</dt><dd>{selectedResult?.result_id ?? '尚无成功结果'}</dd><dt>空间与资格</dt><dd>坐标待核验；基础质控候选，不代表业务融合准入</dd></dl></details>}
   </section>
  </div>
  <SharedTimeline observationOnly issueTime={timeline[0]?.volume_start ?? `${date || '2026-08-28'}T00:00:00+08:00`} values={timeline.map(s=>s.volume_start)} panels={timelinePanels} selectedTime={selectedScan?.volume_start ?? null} playing={playing} playSpeedMS={playSpeedMS} onPlaySpeedChange={setPlaySpeedMS} onTogglePlaying={()=>{if(playing){setPlaying(false);return}const first=timeline.find(s=>s.results.length);if(!first)return;if(!selectedResult)chooseScan(first.scan_id);setPlaying(true)}} onSelect={time=>{const scan=timeline.find(s=>s.volume_start===time);if(scan)chooseScan(scan.scan_id)}} cycleControls={<div className="workspace-timeline-cycle"><strong>{selectedStation?.radar_id.toUpperCase() ?? 'X 波段'} · 历史体扫</strong><span>{stations.items.length} 个已登记 X 站 · 缺扫不补齐</span>{scans.cursor && <button onClick={()=>void scans.more()} disabled={scans.busy}>加载更早体扫</button>}{stations.cursor && <button onClick={()=>void stations.more()} disabled={stations.busy}>更多站点</button>}</div>} />
 </main>
}
