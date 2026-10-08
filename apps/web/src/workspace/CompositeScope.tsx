import type { SourceScope } from './CompositeMap'
type Source = { radar_id: string; band?: string }
export function CompositeScope({ registered, requested, sources, scope: sourceScope, method }: {
  registered: string[]; requested?: string[]; sources: Source[]; scope?: SourceScope; method?: string;
}) {
  const catalog = new Set(registered)
  const scope = new Set(requested ?? [])
  const actual = [...new Map(sources.map(s => [s.radar_id,s])).values()]
  const partial = scope.size > 0 && [...catalog].some(id => !scope.has(id))
  const sameScope = scope.size === catalog.size && [...scope].every(id=>catalog.has(id))
  const scopeText = !scope.size ? '历史请求范围未记录'
    : partial ? `局部组合：请求 ${scope.size}/${catalog.size} 个登记站，覆盖范围有限`
    : sameScope ? `请求全部 ${scope.size} 个登记站，实际输入 ${actual.length} 站`
    : `系列请求 ${scope.size} 站，登记范围尚待核对`
  const counts=sourceScope?.counts
  const qualifications=counts ? `；空间合格 ${counts.qualified_station_count??'未知'} 站；最终回波 ${counts.winner_station_count} 站；切面 ${counts.source_cut_count??'未知'}` : ''
  return <p className="radar-qc-alert" role="status">{method ? `${method} · ` : ''}{scopeText}{qualifications}。实际输入 {actual.filter(s=>s.band==='S').length} S + {actual.filter(s=>s.band==='X').length} X；资料输入不代表每站均有合格回波贡献。</p>
}
