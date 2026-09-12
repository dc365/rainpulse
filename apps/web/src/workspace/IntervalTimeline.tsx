import { useEffect, useState } from 'react'
import type { WorkspaceCycleDetail, WorkspacePanel } from './model'

export type Interval = { start: number; end: number }
export function intervalLabel(issue: string, range: Interval) {
  const clock = (lead: number) => new Intl.DateTimeFormat('zh-CN', {
    timeZone: 'Asia/Taipei', hour: '2-digit', minute: '2-digit', hourCycle: 'h23',
  }).format(new Date(Date.parse(issue) + lead * 60_000))
  return `+${range.start} 至 +${range.end} 分钟 · ${clock(range.start)}–${clock(range.end)} · 累计 ${range.end-range.start} 分钟 · mm`
}

export function useIntervalPanels(detail: WorkspaceCycleDetail | null, enabled: boolean, range: Interval) {
  const [revision, setRevision] = useState(0)
  const [result, setResult] = useState<{ key: string; panels: WorkspacePanel[]; completed: number; error?: string } | null>(null)
  // Source identity changes after regeneration even when the cycle stays selected.
  const identity = detail ? JSON.stringify([detail.cycle_id, detail.panels.filter(p => ['qpe','lk','steps','nowcastnet'].includes(p.panel_id)).map(p => p.frames.map(f => [f.image_url,f.sha256]))]) : ''
  const key = `${identity}:${range.start}:${range.end}:${revision}`
  useEffect(() => {
    if (!enabled || !detail) return
    const controller = new AbortController()
    // A new request key hides old results without an effect-driven state reset.
    // Keep this accumulator local: StrictMode/re-enabling must not count old completions.
    let received: { key: string; panels: WorkspacePanel[]; completed: number; error?: string } = {
      key, panels: [], completed: 0,
    }
    const receive = (panels: WorkspacePanel[], error?: string) => {
      if (controller.signal.aborted) return
      received = {
        key,
        panels: [...received.panels.filter(p => !panels.some(next => next.panel_id === p.panel_id)), ...panels],
        completed: received.completed + 1,
        error: error || received.error,
      }
      setResult(received)
    }
    for (const algorithm of ['qpe','lk','steps','nowcastnet']) void fetch('/api/v1/workspace/accumulations', {
      method: 'POST', headers: { 'Content-Type': 'application/json' }, signal: controller.signal,
      body: JSON.stringify({ cycle_id: detail.cycle_id, algorithm, start_minutes: range.start, end_minutes: range.end }),
    }).then(async response => {
      if (!response.ok) throw new Error('累计服务暂不可用，请重试')
      const data = await response.json()
      if (data.cycle_id !== detail.cycle_id || data.start_minutes !== range.start || data.end_minutes !== range.end || !Array.isArray(data.panels)) throw new Error('累计响应与所选区间不一致')
      if (data.panels.length !== 1 || data.panels[0].panel_id !== algorithm) throw new Error('累计算法响应不一致')
      receive(data.panels)
    }).catch(error => { receive([], String(error.message)) })
    // Refresh ephemeral images before the 10-minute worker cache expires.
    const timer = window.setTimeout(() => setRevision(n => n+1), 8*60_000)
    return () => { controller.abort(); window.clearTimeout(timer) }
    // key includes all relevant cycle/source/range values, not polling object identity.
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [enabled, key])
  const current = result?.key === key ? result : null
  return { panels: enabled ? current?.panels : undefined, busy: enabled && (current?.completed ?? 0) < 4,
    error: current?.error, retry: () => setRevision(n => n+1) }
}
