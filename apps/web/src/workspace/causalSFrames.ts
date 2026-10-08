import type { CompositeResult } from './CompositeMap'
import type { CycleSummary, WorkspacePanel } from './model'

export type NativeSource = { radar_id: string; scan_id: string; volume_start?: string; volume_end: string; asset_sha256: string }
type Layer = { scope: string; field: string; radar_id?: string; scan_id?: string; sweep_number?: number; elevation_deg?: number; maximum_range_km?: number; image_url: string; layer_id: string; qc_content_sha256?: string; bounds?: [number,number,number,number]; legend?: {value?: number; label?: string; color: string}[] }
type Diagnostics = { layers: Layer[] }
export type CausalFrames = { panels: WorkspacePanel[]; sources: NativeSource[] }
export function causalSources(result: CompositeResult, time: string): NativeSource[] {
  if (Date.parse(result.manifest.analysis_time) !== Date.parse(time)) throw new Error('组合分析时次不符')
  const sources = (result.manifest.sources ?? []) as NativeSource[]
  const byStation = new Map<string, NativeSource>()
  for (const source of sources.filter(s => ('band' in s ? s.band === 'S' : false))) {
    const age = Date.parse(time) - Date.parse(source.volume_end)
    if (!source.scan_id || !source.asset_sha256 || !Number.isFinite(age) || age < 0 || age > 720000) throw new Error('S 来源身份或因果时间不符')
    const prior = byStation.get(source.radar_id)
    if (prior && (prior.scan_id !== source.scan_id || prior.asset_sha256 !== source.asset_sha256)) throw new Error('同站存在冲突的 S 来源')
    byStation.set(source.radar_id, source)
  }
  return [...byStation.values()]
}
export async function resolveCausalSFrames(result: CompositeResult, time: string, cycles: CycleSummary[], read: (path:string)=>Promise<Diagnostics>): Promise<CausalFrames> {
  const sources = causalSources(result,time)
  const at = Date.parse(time)
  // Polar geometry is independent of the downstream mosaic grid. Identity
  // is established by exact scan and QC content, never by grid compatibility.
  const candidates = cycles.filter(c=>c.analysis_id && Math.abs(Date.parse(c.issue_time)-at)<=18*60000)
    .sort((a,b)=>Math.abs(Date.parse(a.issue_time)-at)-Math.abs(Date.parse(b.issue_time)-at)).slice(0,7)
  const panels: WorkspacePanel[] = []
  const remaining = new Set(sources.map(s=>s.radar_id))
  for (const cycle of candidates) {
    if (!remaining.size) break
    const diagnostics = await read(`/api/v1/analysis-cycles/${encodeURIComponent(cycle.analysis_id!)}/diagnostics`)
    for (const source of sources.filter(s=>remaining.has(s.radar_id))) {
      const layers = diagnostics.layers.filter(l=>l.scope==='polar' && l.radar_id===source.radar_id && l.scan_id===source.scan_id && l.qc_content_sha256===source.asset_sha256)
      const raw = layers.filter(l=>l.field==='DBZH_RAW')
      const paired = raw.filter(r=>layers.some(q=>q.field==='DBZH_QC' && q.sweep_number===r.sweep_number))
      if (!paired.length) continue
      for (const [field,prefix] of [['DBZH_RAW','dbzh_raw'],['DBZH_QC','dbzh_qc'],['QC_FLAGS','qc_flags']]) {
        const selected = layers.filter(l=>l.field===field && paired.some(r=>r.sweep_number===l.sweep_number))
        if (!selected.length) continue
        panels.push({panel_id:`${prefix}:${source.radar_id}`,radar_id:source.radar_id,algorithm_id:'causal-native-source',display_name:prefix,role:'qc',lifecycle:'reference',data_kind:'reflectivity',cadence_minutes:6,status:'ready',legend:selected[0].legend?.map(l=>({minimum:l.value,label:l.label,color:l.color})),frames:selected.map(l=>({asset_id:l.layer_id,valid_time:time,observation_time:source.volume_end,lead_time_minutes:0,image_url:l.image_url,media_type:'image/png',scan_id:source.scan_id,sweep_number:l.sweep_number,elevation_deg:l.elevation_deg,maximum_range_km:l.maximum_range_km,bounds:l.bounds,frame_kind:'native'}))})
      }
      remaining.delete(source.radar_id)
    }
  }
  return {panels,sources}
}
