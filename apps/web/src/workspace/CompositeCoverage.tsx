type QCIdentity = {
  parameters_sha256?: string | null
  implementation_revision?: string | null
  base_parameters_sha256?: string | null
}
export type CompositeCoverageManifest = {
  analysis_time: string
  sources?: { scan_id?: string; asset_sha256?: string; volume_start?: string; radar_id: string; band?: string; volume_end: string; qc_version?: string | null; qc_identity?: QCIdentity }[]
  skipped?: { radar_id: string; reason: string }[]
}
const reasons: Record<string,string> = {
  no_usable_causal_input: '无可用输入（缺测、未就绪或超龄）',
  future_or_expired: '时间不符或超龄',
}
export function CompositeCoverage({ manifest }: { manifest: CompositeCoverageManifest }) {
  const records = manifest.sources ?? []
  const sources = [...new Map(records.map(source=>[source.radar_id,source])).values()]
  const skipped = manifest.skipped ?? []
  return <details className="multi-station-layer composite-coverage"><summary>本帧资料 · 参与 {sources.length} · 未参与 {skipped.length}</summary>
    <p>计算输入，与地图勾选图层独立。资料年龄以体扫结束时间计算。</p>
    {sources.map(source => {
      const end = Date.parse(source.volume_end)
      const age = (Date.parse(manifest.analysis_time) - end) / 60_000
      const versions = [...new Set(records.filter(s=>s.radar_id===source.radar_id).map(s=>s.qc_version).filter(Boolean))]
      const identities = [...new Map(records.filter(s=>s.radar_id===source.radar_id && s.qc_identity).map(s=>[JSON.stringify(s.qc_identity),s.qc_identity!])).values()]
      return <div key={source.radar_id}><strong>{source.radar_id.toUpperCase()} · {source.band ?? '—'}</strong><small>结束 {Number.isFinite(end) ? new Date(end + 8 * 3600_000).toISOString().slice(5,19).replace('T',' ') : '未知'} 北京时 · 年龄 {Number.isFinite(age) ? `${age.toFixed(1)} 分钟` : '未知'}</small><small>QC {versions.join(' / ')||'旧结果未记录'}</small>
        {identities.map(identity=><span key={JSON.stringify(identity)}>
          <small title={identity.parameters_sha256??undefined}>参数 {identity.parameters_sha256?.slice(0,12)??'未记录'} · {identity.implementation_revision?`实现 ${identity.implementation_revision}`:'实现未记录'}</small>
          {identity.base_parameters_sha256 && identity.base_parameters_sha256!==identity.parameters_sha256 && <small title={identity.base_parameters_sha256}>基础参数 {identity.base_parameters_sha256.slice(0,12)}</small>}
        </span>)}
      </div>
    })}
    {skipped.map(source => <div key={source.radar_id}><strong>{source.radar_id.toUpperCase()}</strong><small>{reasons[source.reason] ?? source.reason}</small></div>)}
    {!manifest.sources && <p>旧结果未记录逐站信息</p>}
  </details>
}
