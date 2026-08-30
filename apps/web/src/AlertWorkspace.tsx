import { useCallback, useEffect, useMemo, useState } from 'react'

import type { components } from './api/generated/schema'

type AlertSnapshot = components['schemas']['AlertSnapshot']
type AlertRecord = components['schemas']['AlertRecord']
type AlertState = components['schemas']['AlertState']
type OperationalIssue = components['schemas']['OperationalIssue']
type OperationalIssueSnapshot = components['schemas']['OperationalIssueSnapshot']
type Filter = 'all' | 'firing' | 'pending' | 'suppressed'

const stateLabels: Record<AlertState, string> = {
  pending: '待生效',
  firing: '告警中',
  silenced: '已静默',
  inhibited: '已抑制',
}

const severityLabels: Record<AlertRecord['severity'], string> = {
  critical: '严重',
  warning: '警告',
  info: '提示',
  unknown: '未知',
}

function formatUtc(value: string) {
  return `${new Intl.DateTimeFormat('zh-CN', {
    timeZone: 'UTC',
    month: '2-digit',
    day: '2-digit',
    hour: '2-digit',
    minute: '2-digit',
    second: '2-digit',
    hour12: false,
  }).format(new Date(value))} UTC`
}

function formatElapsed(activeAt: string, observedAt: string) {
  const seconds = Math.max(0, Math.floor((Date.parse(observedAt) - Date.parse(activeAt)) / 1000))
  if (seconds < 60) return `${seconds} 秒`
  if (seconds < 3600) return `${Math.floor(seconds / 60)} 分钟`
  if (seconds < 86400) return `${Math.floor(seconds / 3600)} 小时`
  return `${Math.floor(seconds / 86400)} 天`
}

function matchesFilter(alert: AlertRecord, filter: Filter) {
  if (filter === 'all') return true
  if (filter === 'suppressed') return alert.state === 'silenced' || alert.state === 'inhibited'
  return alert.state === filter
}

function formatAge(seconds: number) {
  if (seconds < 60) return `${Math.floor(seconds)} 秒`
  if (seconds < 3600) return `${Math.floor(seconds / 60)} 分钟`
  if (seconds < 86400) return `${Math.floor(seconds / 3600)} 小时`
  return `${Math.floor(seconds / 86400)} 天`
}

function matchingAlert(issue: OperationalIssue, alerts: AlertRecord[]) {
  return alerts.find((alert) => {
    if (issue.event_id && alert.labels.event_id === issue.event_id) return true
    if (issue.job_id && alert.labels.job_id === issue.job_id) return true
    if (issue.aggregate_id && alert.labels.aggregate_id === issue.aggregate_id) return true
    return Boolean(
      issue.run_id && issue.job_type &&
      alert.labels.run_id === issue.run_id && alert.labels.job_type === issue.job_type,
    )
  })
}

function SourceStatus({
  name,
  availability,
}: {
  name: 'Prometheus' | 'Alertmanager'
  availability: 'ready' | 'unavailable'
}) {
  return (
    <span className={`alert-source-status ${availability}`}>
      <i aria-hidden="true" />
      {name} {availability === 'ready' ? '正常' : '不可用'}
    </span>
  )
}

export function AlertWorkspace({ refreshToken }: { refreshToken: number }) {
  const [snapshot, setSnapshot] = useState<AlertSnapshot | null>(null)
  const [issues, setIssues] = useState<OperationalIssueSnapshot | null>(null)
  const [filter, setFilter] = useState<Filter>('all')
  const [loading, setLoading] = useState(true)
  const [error, setError] = useState<string | null>(null)
  const [issueError, setIssueError] = useState<string | null>(null)

  const load = useCallback(async (signal?: AbortSignal) => {
    try {
      const [alertResult, issueResult] = await Promise.allSettled([
        fetch('/api/v1/alerts', { signal }),
        fetch('/api/v1/operations/issues', { signal }),
      ])
      if (alertResult.status === 'rejected') throw alertResult.reason
      if (!alertResult.value.ok) throw new Error(`告警接口响应异常（${alertResult.value.status}）`)
      setSnapshot(await alertResult.value.json() as AlertSnapshot)
      setError(null)
      if (issueResult.status === 'fulfilled' && issueResult.value.ok) {
        setIssues(await issueResult.value.json() as OperationalIssueSnapshot)
        setIssueError(null)
      } else if (!(signal?.aborted)) {
        const detail = issueResult.status === 'fulfilled'
          ? `运行证据接口响应异常（${issueResult.value.status}）`
          : '运行证据读取失败'
        setIssueError(detail)
      }
    } catch (requestError: unknown) {
      if (!(requestError instanceof DOMException && requestError.name === 'AbortError')) {
        setError(requestError instanceof Error ? requestError.message : '告警状态读取失败')
      }
    } finally {
      if (!signal?.aborted) setLoading(false)
    }
  }, [])

  useEffect(() => {
    const controller = new AbortController()
    const kickoff = window.setTimeout(() => void load(controller.signal), 0)
    const timer = window.setInterval(() => void load(controller.signal), 30_000)
    return () => {
      controller.abort()
      window.clearTimeout(kickoff)
      window.clearInterval(timer)
    }
  }, [load, refreshToken])

  const filteredItems = useMemo(
    () => snapshot?.items.filter((alert) => matchesFilter(alert, filter)) ?? [],
    [filter, snapshot],
  )
  const suppressedCount = (snapshot?.counts.silenced ?? 0) + (snapshot?.counts.inhibited ?? 0)
  const filters: Array<{ key: Filter; label: string; count: number }> = [
    { key: 'all', label: '全部', count: snapshot?.counts.total ?? 0 },
    { key: 'firing', label: '告警中', count: snapshot?.counts.firing ?? 0 },
    { key: 'pending', label: '待生效', count: snapshot?.counts.pending ?? 0 },
    { key: 'suppressed', label: '已抑制', count: suppressedCount },
  ]

  return (
    <section className="alert-workspace" aria-labelledby="alert-workspace-title">
      <header className="alert-workspace-heading">
        <div>
          <p>Alert operations / RP-029 + RP-030</p>
          <h1 id="alert-workspace-title">告警中心</h1>
          <span>聚合规则、分发状态，以及可定位到任务和 Outbox 的只读运行证据。</span>
        </div>
        <div className="alert-source-cluster" aria-label="告警数据源状态">
          <SourceStatus name="Prometheus" availability={snapshot?.sources.prometheus ?? 'unavailable'} />
          <SourceStatus name="Alertmanager" availability={snapshot?.sources.alertmanager ?? 'unavailable'} />
          <small>{snapshot ? formatUtc(snapshot.observed_at) : '正在读取状态'}</small>
        </div>
      </header>

      {error ? (
        <div className="error-banner" role="alert">
          <strong>告警连接中断</strong>
          <span>{error}</span>
        </div>
      ) : null}

      <div className="alert-summary-rail" aria-label="告警状态摘要">
        <div><span>活动总数</span><strong>{snapshot?.counts.total ?? 0}</strong><small>当前规则实例</small></div>
        <div className="critical"><span>告警中</span><strong>{snapshot?.counts.firing ?? 0}</strong><small>已完成持续判定</small></div>
        <div className="pending"><span>待生效</span><strong>{snapshot?.counts.pending ?? 0}</strong><small>仍在持续计时</small></div>
        <div><span>已抑制</span><strong>{suppressedCount}</strong><small>静默或关联抑制</small></div>
      </div>

      {snapshot?.status === 'degraded' ? (
        <div className="alert-completeness-notice" role="status">
          <strong>告警状态不完整</strong>
          <span>至少一个上游不可用，当前列表不能作为“无告警”的依据。</span>
        </div>
      ) : null}

      <div className="alert-ledger">
        <header>
          <div>
            <p>Active alert ledger</p>
            <h2>活动告警</h2>
          </div>
          <div className="alert-filter" aria-label="告警状态筛选">
            {filters.map((item) => (
              <button
                type="button"
                className={filter === item.key ? 'active' : ''}
                aria-pressed={filter === item.key}
                key={item.key}
                onClick={() => setFilter(item.key)}
              >
                {item.label} <span>{item.count}</span>
              </button>
            ))}
          </div>
        </header>

        <div className="alert-ledger-columns" aria-hidden="true">
          <span>级别与状态</span>
          <span>告警证据</span>
          <span>作用域</span>
          <span>持续时间</span>
        </div>

        {loading ? <p className="alert-empty">正在聚合告警状态…</p> : null}
        {!loading && snapshot?.status === 'ready' && snapshot.items.length === 0 ? (
          <p className="alert-empty healthy">当前没有活动告警</p>
        ) : null}
        {!loading && snapshot?.status === 'degraded' && snapshot.items.length === 0 ? (
          <p className="alert-empty">等待告警数据源恢复后重新判断。</p>
        ) : null}
        {!loading && snapshot && snapshot.items.length > 0 && filteredItems.length === 0 ? (
          <p className="alert-empty">当前筛选条件下没有告警。</p>
        ) : null}

        <div className="alert-record-list">
          {snapshot ? filteredItems.map((alert) => {
            const scopeLabels = Object.entries(alert.labels)
              .filter(([key]) => !['alertname', 'severity', 'lifecycle'].includes(key))
            return (
              <article className={`alert-record ${alert.severity} ${alert.state}`} key={alert.alert_id}>
                <div className="alert-record-state">
                  <span className={`alert-severity ${alert.severity}`}>{severityLabels[alert.severity]}</span>
                  <strong>{stateLabels[alert.state]}</strong>
                </div>
                <div className="alert-record-evidence">
                  <strong>{alert.summary}</strong>
                  <code>{alert.name}</code>
                  {alert.value ? <small>当前值 {alert.value}</small> : null}
                </div>
                <div className="alert-record-labels">
                  {scopeLabels.length > 0
                    ? scopeLabels.map(([key, value]) => <span key={key}><small>{key}</small>{value}</span>)
                    : <span><small>scope</small>global</span>}
                </div>
                <div className="alert-record-time">
                  <strong>{formatElapsed(alert.active_at, snapshot.observed_at)}</strong>
                  <small>{formatUtc(alert.active_at)}</small>
                </div>
              </article>
            )
          }) : null}
        </div>
      </div>

      <div className="operational-ledger" aria-label="关联运行问题">
        <header>
          <div>
            <p>Correlated operational evidence</p>
            <h2>关联运行问题</h2>
          </div>
          <dl aria-label="运行问题计数">
            <div><dt>失败任务</dt><dd>{issues?.counts.failed_jobs ?? 0}</dd></div>
            <div><dt>阻塞任务</dt><dd>{issues?.counts.stuck_jobs ?? 0}</dd></div>
            <div><dt>Outbox</dt><dd>{issues?.counts.outbox_events ?? 0}</dd></div>
          </dl>
        </header>

        {issueError ? (
          <div className="operational-evidence-error" role="status">
            <strong>运行证据不完整</strong>
            <span>{issueError}</span>
          </div>
        ) : null}

        {!issueError && issues?.items.length === 0 ? (
          <p className="alert-empty healthy">当前没有失败、阻塞或滞留的运行记录</p>
        ) : null}

        <div className="operational-record-list">
          {issues?.items.map((issue) => {
            const alert = matchingAlert(issue, snapshot?.items ?? [])
            return (
              <article className={`operational-record ${issue.kind}`} key={issue.issue_id}>
                <div className="operational-record-state">
                  <span>{issue.kind === 'job' ? '任务' : 'Outbox'}</span>
                  <strong>{issue.status}</strong>
                </div>
                <div className="operational-record-evidence">
                  <strong>{issue.summary}</strong>
                  <code>{issue.job_type ?? issue.event_type ?? issue.issue_id}</code>
                  {issue.error_message ? <small>{issue.error_message}</small> : null}
                </div>
                <div className="operational-record-identity">
                  {issue.job_id ? <span><small>job</small>{issue.job_id.slice(0, 8)}</span> : null}
                  {issue.event_id ? <span><small>event</small>{issue.event_id.slice(0, 8)}</span> : null}
                  {issue.run_id ? <span><small>run</small>{issue.run_id.slice(0, 8)}</span> : null}
                  <span><small>attempt</small>{issue.attempt_count}</span>
                </div>
                <div className="operational-record-correlation">
                  {alert ? (
                    <span className={`linked ${alert.state}`}>已关联 {stateLabels[alert.state]}</span>
                  ) : (
                    <span>尚未进入活动告警</span>
                  )}
                  <small>{formatAge(issue.age_seconds)}</small>
                </div>
              </article>
            )
          })}
        </div>
      </div>

      <footer className="alert-workspace-boundary">
        <strong>当前边界</strong>
        <span>仅查看，不发送外部通知，不提供静默或确认操作。通知责任人和正式 SLA 确认后再启用外发。</span>
      </footer>
    </section>
  )
}
