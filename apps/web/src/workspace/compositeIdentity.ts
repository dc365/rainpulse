/** Runtime checks supplement TypeScript; a failed identity check never falls back to another series. */
export type CompositeItem = { result_id: string; analysis_time: string; series_id?: string }
export function exactComposite(items: CompositeItem[], time: string, series?: string): CompositeItem | undefined {
  const target = Date.parse(time)
  if (!Number.isFinite(target)) throw new Error('组合时次无效')
  return items.find(i => Date.parse(i.analysis_time) === target && (!series || i.series_id === series))
}
export function assertCompositeIdentity(result: { result_id: string; series_id?: string; manifest: { analysis_time: string } }, item: CompositeItem, series?: string): void {
  if (result.result_id !== item.result_id || Date.parse(result.manifest.analysis_time) !== Date.parse(item.analysis_time)) throw new Error('组合结果与请求时次不一致')
  const expected = series || item.series_id
  if (expected && result.series_id !== expected) throw new Error('组合结果与冻结产品系列不一致')
}
