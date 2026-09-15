import { useEffect, useRef, useState } from 'react'
import type { CycleSummary } from './model'

const storageKey = 'rainpulse-history-qc-batch-v1'
type Row = { source: string, time: string, run?: string, state: string, error?: string, detail?: string }
type Batch = { date: string, rows: Row[], created: string }
type Job = { job_type: string, status: string, error_message?: string }
export const beijingDate = (time: string) => new Date(new Date(time).getTime() + 8 * 3600_000).toISOString().slice(0, 10)
export function historyScope(cycles: CycleSummary[], date: string) {
  const selected = cycles.filter(c => beijingDate(c.issue_time) === date)
  const runs = [...new Map(selected.filter(c => c.run_id).map(c => [c.run_id!, c])).values()]
  return { runs: runs.sort((a, b) => a.issue_time.localeCompare(b.issue_time)), unavailable: selected.filter(c => !c.run_id) }
}
function restore(): Batch | null {
  try {
    const b = JSON.parse(window.localStorage.getItem(storageKey) ?? 'null') as Batch | null
    if (!b || typeof b.date !== 'string' || !Array.isArray(b.rows) || b.rows.length > 1000) return null
    if (!b.rows.every(r => typeof r.source === 'string' && typeof r.time === 'string' && typeof r.state === 'string')) return null
    return { ...b, rows: b.rows.map(r => r.state === 'SUBMITTING' ? { ...r, state: 'UNKNOWN', error: '提交结果未确认，请先查询运行记录，避免重复重算' } : r) }
  } catch { return null }
}
const labels: Record<string, string> = { WAITING: '待提交', SUBMITTING: '提交中', UNKNOWN: '受理结果待核实', ACCEPTED: '已受理', RUNNING: '后台处理中', SUCCEEDED: '已发布', FAILED: '失败', REJECTED: '未受理', CANCELLED: '已取消' }
const terminal = (s: string) => ['SUCCEEDED', 'FAILED', 'REJECTED', 'CANCELLED'].includes(s)
async function jsonRequest(url: string, options?: RequestInit) {
  const response = await fetch(url, options)
  const data = await response.json()
  if (!response.ok) throw new Error(data.message ?? `请求失败（${response.status}）`)
  return data
}

export function HistoricalQCPanel({ cycles }: { cycles: CycleSummary[] }) {
  const dates = [...new Set(cycles.map(c => beijingDate(c.issue_time)))].sort().reverse()
  const [date, setDate] = useState('')
  const chosen = dates.includes(date) ? date : dates[0] ?? ''
  const scope = historyScope(cycles, chosen)
  const [batch, setBatch] = useState<Batch | null>(restore)
  const current = useRef(batch)
  const [submitting, setSubmitting] = useState(false)
  const [error, setError] = useState('')
  function save(next: Batch) {
    current.current = next
    setBatch(next)
    try { window.localStorage.setItem(storageKey, JSON.stringify(next)) } catch { setError('浏览器无法保存进度；已受理任务仍在后台运行，请勿刷新后重复提交。') }
  }
  function update(source: string, patch: Partial<Row>) {
    if (current.current) save({ ...current.current, rows: current.current.rows.map(r => r.source === source ? { ...r, ...patch } : r) })
  }
  const tracked = batch?.rows.filter(r => r.run && !terminal(r.state)).map(r => `${r.source}:${r.run}`).join(',') ?? ''
  useEffect(() => {
    if (!tracked) return
    const controller = new AbortController()
    let timer: ReturnType<typeof setTimeout>
    async function poll() {
      const rows = current.current?.rows.filter(r => r.run && !terminal(r.state)) ?? []
      // Bound HTTP concurrency; progress is completed cycles, not dynamically growing jobs.
      for (let i = 0; i < rows.length && !controller.signal.aborted; i += 4) {
        await Promise.all(rows.slice(i, i + 4).map(async row => {
          try {
            const [run, jobs] = await Promise.all([
              jsonRequest(`/api/v1/runs/${encodeURIComponent(row.run!)}`, { signal: controller.signal }),
              jsonRequest(`/api/v1/runs/${encodeURIComponent(row.run!)}/jobs`, { signal: controller.signal }),
            ])
            if (controller.signal.aborted) return
            const tasks = jobs as Job[]
            const failed = tasks.find(j => j.status === 'FAILED')
            const state = run.status === 'PUBLISHED' ? 'SUCCEEDED' : run.status === 'FAILED' || failed ? 'FAILED' : run.status === 'CANCELLED' || run.status === 'SKIPPED' ? 'CANCELLED' : 'RUNNING'
            update(row.source, { state, error: failed?.error_message ?? (state === 'FAILED' ? '流水线失败，请查看该运行的任务详情' : undefined), detail: tasks.filter(j => j.status === 'RUNNING').map(j => j.job_type).join('、') || '等待后台阶段更新' })
          } catch (cause) {
            if (!controller.signal.aborted) update(row.source, { detail: `进度读取失败，将自动重试：${cause instanceof Error ? cause.message : '网络错误'}` })
          }
        }))
      }
      if (!controller.signal.aborted) timer = setTimeout(() => void poll(), 5000)
    }
    void poll()
    return () => { controller.abort(); clearTimeout(timer) }
    // The receipt set controls polling; individual progress updates must not restart it.
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [tracked])

  const active = Boolean(batch?.rows.some(r => r.run && !terminal(r.state)))
  async function submit() {
    if (submitting || active || !scope.runs.length) return
    setError(''); setSubmitting(true)
    const next: Batch = { date: chosen, created: new Date().toISOString(), rows: scope.runs.map(c => ({ source: c.run_id!, time: c.issue_time, state: 'WAITING' })) }
    save(next)
    for (const row of next.rows) {
      update(row.source, { state: 'SUBMITTING' })
      try {
        const response = await fetch(`/api/v1/admin/runs/${encodeURIComponent(row.source)}/rerun`, { method: 'POST', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify({ preset: 'forecast_all', reason: `历史案例 ${chosen} 雷达质控及关联产品重算` }) })
        const result = await response.json()
        if (!response.ok) { update(row.source, { state: 'REJECTED', error: result.message ?? `未受理（${response.status}）` }); continue }
        if (typeof result.run_id !== 'string') throw new Error('后台未返回运行编号')
        update(row.source, { state: 'ACCEPTED', run: result.run_id })
      } catch (cause) {
        update(row.source, { state: 'UNKNOWN', error: `受理结果不确定：${cause instanceof Error ? cause.message : '网络错误'}。停止后续提交，请先核实。` })
        break
      }
    }
    setSubmitting(false)
  }
  const completed = batch?.rows.filter(r => r.state === 'SUCCEEDED').length ?? 0
  const failures = batch?.rows.filter(r => ['FAILED', 'REJECTED', 'UNKNOWN', 'CANCELLED'].includes(r.state)).length ?? 0
  const blocked = Boolean(batch?.rows.some(r => r.state === 'UNKNOWN'))
  return <section className="admin-panel history-qc-panel">
    <header><div><span>Historical radar QC</span><h1>历史案例 · 雷达质控重算</h1></div><small>全部站点 · 当前生效算法 · 成功后替换当前产品</small></header>
    <div className="history-qc-controls">
      <label>历史案例（北京时间）<select aria-label="质控历史案例日期" value={chosen} onChange={e => setDate(e.target.value)} disabled={submitting}>{dates.map(d => <option key={d}>{d}</option>)}</select></label>
      <p>可提交 {scope.runs.length} 个起报时次。每个任务重算其输入帧内全部雷达站，并更新 QPE、LK 和网页产品。</p>
      <button type="button" disabled={!scope.runs.length || submitting || active || blocked} onClick={() => void submit()}>{submitting ? '正在提交…' : active ? '本批任务计算中' : '重算本案例全部可用时次'}</button>
    </div>
    {scope.unavailable.length > 0 && <p role="status">另有 {scope.unavailable.length} 个时次没有可用起报运行，无法直接提交；可能由相邻任务的输入帧覆盖，不能视为已完成。</p>}
    <p className="admin-regeneration-note">请等全部提交受理后再关闭页面；已受理计算不依赖浏览器。刷新后可恢复本浏览器的进度记录。原始观测保留。</p>
    {error && <p role="alert">{error}</p>}
    {batch && <div aria-live="polite">
      <strong>{batch.date}：已发布 {completed} / {batch.rows.length} 个时次{failures ? `，失败或待核实 ${failures}` : ''}</strong>
      <progress aria-label="历史质控重算完成进度" value={completed} max={batch.rows.length || 1} />
      <div className="history-qc-table"><table><thead><tr><th>北京时间</th><th>状态</th><th>当前任务 / 原因</th><th>运行编号</th></tr></thead><tbody>{batch.rows.map(r => <tr key={r.source}><td>{new Date(r.time).toLocaleString('zh-CN', { timeZone: 'Asia/Shanghai', hour12: false })}</td><td>{labels[r.state] ?? r.state}</td><td>{r.error ?? (terminal(r.state) ? '—' : r.detail ?? '等待提交')}</td><td>{r.run ?? '—'}</td></tr>)}</tbody></table></div>
    </div>}
  </section>
}
