import type { WorkspaceCycleDetail, WorkspacePanel } from './model'

export type ProductMode = 'rain_rate' | 'hourly' | 'total_2h'
export const productModes: Record<ProductMode, string> = {
  rain_rate: '5分钟雨强', hourly: '逐小时累计', total_2h: '0–2小时累计',
}

export function accumulationTimes(issueTime: string, mode: ProductMode) {
  return (mode === 'hourly' ? [60, 120] : [120])
    .map(lead => new Date(Date.parse(issueTime) + lead * 60_000).toISOString())
}

export function productPanels(detail: WorkspaceCycleDetail, panels: WorkspacePanel[], mode: ProductMode) {
  if (mode === 'rain_rate') return panels
  const kind = mode === 'hourly' ? 'accumulation_60' : 'accumulation_120'
  return panels.map(panel => {
    const source = detail.panels.find(item => item.panel_id === `${panel.panel_id}:${kind}`)
    return source ? { ...source, panel_id: panel.panel_id, frames: source.frames.filter(frame => frame.valid_cell_count !== 0) } : {
      ...panel, data_kind: kind, legend_unit: 'mm', legend: [], frames: [],
      status: 'unavailable' as const, unavailable_reason: panel.role === 'observation' ? 'observation_accumulation_unavailable' : 'accumulation_not_generated',
    }
  })
}

export function accumulationLabel(issueTime: string, validTime: string, mode: ProductMode) {
  const end = Math.round((Date.parse(validTime) - Date.parse(issueTime)) / 60_000)
  const start = mode === 'total_2h' ? 0 : end - 60
  const clock = (minutes: number) => new Intl.DateTimeFormat('zh-CN', {
    timeZone: 'Asia/Taipei', month: '2-digit', day: '2-digit',
    hour: '2-digit', minute: '2-digit', hourCycle: 'h23',
  }).format(new Date(Date.parse(issueTime) + minutes * 60_000))
  return `${start / 60}–${end / 60} 小时累计 · ${clock(start)} 至 ${clock(end)} · mm`
}
