import { useEffect, useRef, useState } from 'react'
import './x-qc-workspace.css'

type Page<T> = { items: T[]; next_cursor: string }
type Station = { radar_id: string; display_name: string; registered: number; normalized: number; qc_ready: number }
type Stations = Page<Station> & { start: string; end: string; available_range: { start: string | null; end: string | null } }
type Result = { result_id: string; version: string; finished_at: string }
type Scan = { scan_id: string; radar_id: string; volume_start: string; volume_end: string; state: string; qc_status: string; error_message?: string; results: Result[] }
type Sweep = { sweep_number: number; sequence: number; elevation_deg: number; raw: string; qc: string; flags: string }
type Comparison = { result_id: string; radar_id: string; scan_id: string; volume_start: string; volume_end: string; sweeps: Sweep[] }
const prefix = '/api/v1/workspace/'
const labels: Record<string, string> = { READY: '可对照', ASSET_UNAVAILABLE: '图件不可用', WAITING_QC: '待质控', WAITING_DECODE: '待解码', FAILED: '失败', BLOCKED: '受阻', CANCELLED: '已取消', RUNNING: '计算中', COMMITTING: '发布中', QUEUED: '排队中', WAITING: '等待中' }
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
 const [search, setSearch] = useState('')
 const [showStations, setShowStations] = useState(false)
 const [revision, setRevision] = useState(0)
 const [flags, setFlags] = useState(false)
 const [playing, setPlaying] = useState(false)
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
  const next = timeline.slice(index + 1).find(s => s.results.length)
  if (!next) return
  const timer = window.setTimeout(() => { setScanID(next.scan_id); setResultID(''); setSweepNumber(null) }, 2400)
  return () => window.clearTimeout(timer)
 }, [playing, pair, index, timeline])
 useEffect(() => {
  const params = new URLSearchParams({preset: 'qc', band: 'X'})
  if (date) params.set('date', date)
  if (selectedStation || stationID) params.set('station', selectedStation?.radar_id ?? stationID)
  if (selectedScan || scanID) params.set('scan', selectedScan?.scan_id ?? scanID)
  if (selectedResult || resultID) params.set('result', selectedResult?.result_id ?? resultID)
  if (sweep || sweepNumber !== null) params.set('sweep', String(sweep?.sweep_number ?? sweepNumber))
  window.history.replaceState({}, '', `/?${params}`)
 }, [date, selectedStation, selectedScan, selectedResult, sweep, stationID, scanID, resultID, sweepNumber])
 const last = index >= 0 && !timeline.slice(index + 1).some(s => s.results.length)
 const linkProblem = stations.data && stationID && !selectedStation && !stations.cursor ? '链接中的站点未登记，请从清单选择站点。' : scans.data && scanID && !selectedScan ? '链接中的体扫尚未找到；请加载更早体扫，或选择其他时次。' : selectedScan && resultID && !selectedResult ? '指定结果版本不可用，请选择其他体扫或重新打开站点。' : ''
 const problem = linkProblem || (stations.error ?? scans.error ?? result?.error ?? pair?.error ?? (detail && !validDetail ? '结果身份与所选体扫不一致' : ''))
 const recomputeStart = selectedScan?.volume_start ?? `${date}T00:00:00+08:00`
 const recomputeEnd = date ? new Date(selectedScan ? Date.parse(selectedScan.volume_end) + 1 : Date.parse(recomputeStart) + 360000).toISOString() : ''
 const shown = stations.items.filter(s => `${s.radar_id} ${s.display_name}`.toLowerCase().includes(search.toLowerCase()))
 return <main className="workspace-shell xqc-shell">
  <header className="workspace-topbar"><a className="workspace-brand" href="/"><strong>RainPulse</strong><small>短临降水工作台</small></a><span className="xqc-context">X 波段 · 原生体扫质控</span><a className="admin-link" href="/admin">后台</a></header>
  <section className="workspace-controls xqc-toolbar" aria-label="工作台控制">
   <nav aria-label="工作台预设"><a href="/?preset=forecast">预报对比</a><a href="/?preset=qc&band=X" aria-current="page">质控排查</a><a href="/?preset=verification">检验回放</a></nav>
   <nav className="xqc-band" aria-label="雷达波段"><a href="/?preset=qc">S 波段</a><a href="/?preset=qc&band=X" aria-current="page">X 波段</a></nav>
   <label>资料日期<input aria-label="资料日期" type="date" value={date} onChange={e => {setDay(e.target.value);setScanID('');setResultID('');setPlaying(false)}} /></label>
   <button type="button" onClick={() => setRevision(v => v + 1)}>刷新资料</button>
   <a href={`/admin?view=new&preset=x_qc&radar=${encodeURIComponent(selectedStation?.radar_id ?? '')}&start=${encodeURIComponent(recomputeStart)}&end=${encodeURIComponent(recomputeEnd)}`}>生成／重算</a>
  </section>
  <div className="xqc-notice">历史资料 · 北京时间 UTC+8 · 基础质控候选，尚未取得业务融合资格。站心 PPI 不依赖地图坐标核验。</div>
  {problem && <div className="xqc-error" role="alert">{problem} <button onClick={() => setRevision(v => v + 1)}>重试读取</button></div>}
  <button className="xqc-stations-toggle" aria-expanded={showStations} onClick={()=>setShowStations(v=>!v)}>站点清单 · {selectedStation?.radar_id.toUpperCase() ?? "选择 X 站"}</button>
  <div className="xqc-body">
   <aside className="xqc-stations" data-expanded={showStations} aria-label="X 波段站点清单">
    <h2>X 波段站点 <small>{stations.items.length}</small></h2>
    <input aria-label="搜索站点" placeholder="搜索站号或名称" value={search} onChange={e => setSearch(e.target.value)} />
    {!stations.data && !stations.error && <p role="status">正在读取站点…</p>}
    {shown.map(station => <button type="button" key={station.radar_id} aria-pressed={station.radar_id === selectedStation?.radar_id} onClick={() => {setShowStations(false);setStationID(station.radar_id);setScanID('');setResultID('');setSweepNumber(null);setPlaying(false);setPan({x:0,y:0});setZoom(1)}}>
     <span><strong>{station.radar_id.toUpperCase()}</strong><em data-ready={station.qc_ready > 0}>{station.qc_ready ? '可对照' : station.normalized ? '待质控' : station.registered ? '待解码' : '本日无资料'}</em></span>
     <small>{station.display_name}</small><small>{station.registered} 体扫 · {station.qc_ready} 可对照</small>
    </button>)}
    {stations.data && !shown.length && <p>没有匹配的已登记站点。</p>}
    {stations.cursor && <button disabled={stations.busy} onClick={() => void stations.more()}>更多站点</button>}
    <p className="xqc-catalog-note">清单范围：已登记 X 站点。磁盘中尚未登记的资料不计入数量。</p>
   </aside>
   <section className="xqc-content" aria-label="X 波段质控对照">
    <div className="xqc-selection"><div><h1>{selectedStation?.radar_id.toUpperCase() ?? 'X 波段'} <span>质控前后对照</span></h1><p>{selectedScan ? `${date} ${localTime(selectedScan.volume_start)} — ${localTime(selectedScan.volume_end)} · 原始状态 ${selectedScan.state}` : '选择站点和有资料的日期'}</p></div><span className="xqc-candidate">候选结果</span></div>
    {selectedResult && <div className="xqc-image-controls">
     <label>仰角层<select aria-label="仰角层" value={sweep?.sweep_number ?? ''} disabled={!validDetail} onChange={e => {setSweepNumber(Number(e.target.value));setPlaying(false)}}>{validDetail?.sweeps.map(s => <option key={s.sweep_number} value={s.sweep_number}>{s.elevation_deg.toFixed(2)}° · 第 {s.sequence} 层 · 编号 {s.sweep_number}</option>)}</select></label>
     <button aria-pressed={flags} onClick={() => setFlags(v => !v)}>{flags ? '返回前后对照' : '质控标记'}</button>
     <label>缩放<input aria-label="对照缩放" type="range" min="1" max="4" step="0.25" value={zoom} onChange={e => setZoom(Number(e.target.value))} /></label><button onClick={() => {setZoom(1);setPan({x:0,y:0})}}>复位</button>
     <label>结果版本<select aria-label="结果版本" value={selectedResult.result_id} onChange={e => {setResultID(e.target.value);setPlaying(false)}}>{selectedScan?.results.map(r => <option key={r.result_id} value={r.result_id}>{r.version} · {localDate(r.finished_at)} {localTime(r.finished_at)}</option>)}</select></label>
    </div>}
    {selectedResult ? <div className="xqc-compare">
     {[{name:'原始反射率',src:pair?.raw},{name:flags?'质控动作标记':'基础质控后',src:pair?.qc}].map((image,i) => <figure key={i}><figcaption>{image.name}<small>{i === 0 ? '原始观测 · dBZ' : flags ? '红：确认无效／污染 · 琥珀：待复核' : '确认剔除门隐藏 · 琥珀色回波待复核'}</small></figcaption>
      <div className="xqc-image-viewport" tabIndex={0} aria-label={`${image.name}联动视图`} onPointerDown={e=>{if(!image.src)return;e.currentTarget.setPointerCapture(e.pointerId);drag.current={x:e.clientX,y:e.clientY,originX:pan.x,originY:pan.y}}} onPointerMove={e=>{if(drag.current)setPan({x:drag.current.originX+e.clientX-drag.current.x,y:drag.current.originY+e.clientY-drag.current.y})}} onPointerUp={()=>{drag.current=null}} onPointerCancel={()=>{drag.current=null}} onKeyDown={e=>{const offsets:Record<string,[number,number]>={ArrowLeft:[-20,0],ArrowRight:[20,0],ArrowUp:[0,-20],ArrowDown:[0,20]};const offset=offsets[e.key];if(offset){e.preventDefault();setPan(p=>({x:p.x+offset[0],y:p.y+offset[1]}))}}}>
       {image.src ? <img draggable={false} src={image.src} alt={i===0?'原始反射率 PPI':flags?'质控动作 PPI':'基础质控后 PPI'} style={{transform:`translate(${pan.x}px, ${pan.y}px) scale(${zoom})`}} /> : <p role="status">{problem ? '图件不可用，请重试。' : '正在校验并读取同一扫层图对…'}</p>}
      </div></figure>)}
    </div> : <div className="xqc-empty"><strong>{selectedScan ? label(selectedScan.qc_status) : scans.data ? '本日没有已登记体扫' : '正在读取体扫目录…'}</strong><p>{selectedScan?.qc_status==='WAITING_QC' ? '该体扫已解码，等待基础质控。' : selectedScan?.qc_status==='WAITING_DECODE' ? '该体扫已登记，等待解码。' : selectedScan?.error_message || '请选择有资料日期，或刷新查看任务进度。'}</p>{stations.data?.available_range.end && <button onClick={()=>{setDay(localDate(stations.data!.available_range.end!));setScanID('');setResultID('')}}>转到最近有资料日期</button>}</div>}
    <p className="xqc-footnote">原生站心极坐标 · 前后图共用同一扫层和色标 · 缺测保持透明 · 可拖动任一图同步查看</p>
    {selectedScan && <details className="xqc-lineage"><summary>资料来源与结果身份</summary><dl><dt>体扫</dt><dd>{selectedScan.scan_id}</dd><dt>观测 UTC</dt><dd>{selectedScan.volume_start} — {selectedScan.volume_end}</dd><dt>结果</dt><dd>{selectedResult?.result_id ?? '尚无成功结果'}</dd><dt>空间与资格</dt><dd>坐标待核验；基础质控候选，不代表业务融合准入</dd></dl></details>}
   </section>
  </div>
  <section className="shared-timeline xqc-timeline" aria-label="X 波段真实体扫时间轴">
   <div className="xqc-time-controls"><strong>{selectedStation?.radar_id.toUpperCase() ?? 'X 波段'} · {date}（UTC+8）</strong><button disabled={index<=0} onClick={()=>chooseScan(timeline[index-1].scan_id)}>上一扫</button><button disabled={!pair?.raw || last} aria-pressed={playing && !last} onClick={()=>setPlaying(v=>!v)}>{playing && !last ? '暂停' : '播放可用图件'}</button><button disabled={index<0 || index>=timeline.length-1} onClick={()=>chooseScan(timeline[index+1].scan_id)}>下一扫</button><span>{timeline.length} 个体扫 · 真实观测时间，缺扫不补齐</span>{scans.cursor && <button disabled={scans.busy} onClick={()=>void scans.more()}>加载更早体扫</button>}</div>
   <div className="xqc-scan-list">{timeline.map((scan,i) => <div key={scan.scan_id} className="xqc-scan-slot">{i>0 && Date.parse(scan.volume_start)-Date.parse(timeline[i-1].volume_start)>420000 && <span className="xqc-gap">资料间断</span>}<button aria-pressed={scan.scan_id===selectedScan?.scan_id} data-ready={scan.results.length>0} onClick={()=>chooseScan(scan.scan_id)}><strong>{localTime(scan.volume_start)}</strong><small>至 {localTime(scan.volume_end)}</small><span>{label(scan.qc_status)}</span></button></div>)}</div>
  </section>
 </main>
}
