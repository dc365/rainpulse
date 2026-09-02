import { useEffect, useState } from 'react'

type SystemStatus = { status?: string, version?: string }
type RadarStatus = {
  radar_id: string
  display_name?: string
  lifecycle?: string
  health?: string
  latest_scan_time?: string
  data_delay_seconds?: number
  scan_status?: string
  mean_quality_index?: number
}

type IngestSource = {
  source_id: string
  radar_id: string
  arrival_root: string
  latest_scan_at?: string
  latest_success_at?: string
  latest_volume_at?: string
  registered_count?: number
  failure_count?: number
  last_error?: string
}

type IngestStatus = {
  status?: string
  profile_version?: string
  execution_mode?: string
  sources?: IngestSource[]
  reason?: string
}


type NowcastNetShadowStatus = {
  status?: string
  reason?: string
  checked_at?: string
  profile_version?: string
  issue_time?: string
  frame_count?: number
  common_valid_ratio?: number
  inference_enabled?: boolean
  spatial_shape_validated?: boolean
  roi?: { y_start?: number, x_start?: number, height?: number, width?: number }
}

type AdminSnapshot = {
  system: SystemStatus | null
  radars: RadarStatus[]
  ingest: IngestStatus | null
  nowcastnet: NowcastNetShadowStatus | null
  issues: unknown
  alerts: unknown
}

export function AdminWorkspace() {
  const [snapshot, setSnapshot] = useState<AdminSnapshot>({
    system: null, radars: [], ingest: null, nowcastnet: null, issues: null, alerts: null,
  })
  const [error, setError] = useState<string | null>(null)

  useEffect(() => {
    const controller = new AbortController()
    void Promise.all([
      read<SystemStatus>('/api/v1/system/status', controller.signal),
      read<RadarStatus[]>('/api/v1/radars/status', controller.signal),
      readOptional('/api/v1/workspace/ingest-status', controller.signal),
      readOptional('/api/v1/workspace/nowcastnet-shadow-status', controller.signal),
      readOptional('/api/v1/operations/issues', controller.signal),
      readOptional('/api/v1/alerts', controller.signal),
    ]).then(([system, radars, ingest, nowcastnet, issues, alerts]) => {
      setSnapshot({
        system,
        radars,
        ingest: ingest as IngestStatus,
        nowcastnet: nowcastnet as NowcastNetShadowStatus,
        issues,
        alerts,
      })
      setError(null)
    }).catch((requestError: unknown) => {
      if (!(requestError instanceof DOMException && requestError.name === 'AbortError')) {
        setError(requestError instanceof Error ? requestError.message : '后台状态读取失败')
      }
    })
    return () => controller.abort()
  }, [])

  return (
    <main className="admin-shell">
      <header className="admin-topbar">
        <div><strong>RainPulse 后台</strong><span>数据源、流水线与排障</span></div>
        <a href="/">返回主工作台</a>
      </header>
      {error ? <div className="workspace-warning" role="alert">{error}</div> : null}
      <section className="admin-summary">
        <article><span>控制面</span><strong>{snapshot.system?.status ?? '读取中'}</strong><small>{snapshot.system?.version ?? '—'}</small></article>
        <article><span>接入数据源</span><strong>{snapshot.ingest?.sources?.length ?? snapshot.radars.length}</strong><small>{snapshot.ingest?.execution_mode ?? '状态读取中'}</small></article>
        <article><span>NowcastNet</span><strong>{shadowStatusLabel(snapshot.nowcastnet)}</strong><small>{snapshot.nowcastnet?.profile_version ?? '影子探测'}</small></article>
        <article><span>运行问题</span><strong>{evidenceCount(snapshot.issues)}</strong><small>只读证据</small></article>
        <article><span>活动告警</span><strong>{evidenceCount(snapshot.alerts)}</strong><small>Prometheus / Alertmanager</small></article>
      </section>

      <section className="admin-panel">
        <header><div><span>Ingest sources</span><h1>实时文件接入</h1></div><small>{snapshot.ingest?.profile_version ?? snapshot.ingest?.reason ?? '读取中'}</small></header>
        <div className="admin-ingest-grid">
          {(snapshot.ingest?.sources ?? []).map((source) => (
            <article key={source.source_id}>
              <header><strong>{source.radar_id.toUpperCase()}</strong><span>{source.source_id}</span></header>
              <dl>
                <div><dt>最新体扫</dt><dd>{formatTime(source.latest_volume_at)}</dd></div>
                <div><dt>最近成功</dt><dd>{formatTime(source.latest_success_at)}</dd></div>
                <div><dt>已登记</dt><dd>{source.registered_count ?? 0}</dd></div>
                <div><dt>失败</dt><dd>{source.failure_count ?? 0}</dd></div>
              </dl>
              {source.last_error ? <p>{source.last_error}</p> : <small>{source.arrival_root}</small>}
            </article>
          ))}
          {snapshot.ingest?.sources?.length ? null : <p className="admin-empty">接入进程状态暂不可用。</p>}
        </div>
      </section>
      <section className="admin-panel">
        <header>
          <div><span>NowcastNet shadow gate</span><h1>福建影子输入探测</h1></div>
          <small>{snapshot.nowcastnet?.checked_at ? `检查于 ${formatTime(snapshot.nowcastnet.checked_at)}` : '状态读取中'}</small>
        </header>
        <div className="admin-shadow-status">
          <article><span>状态</span><strong>{shadowStatusLabel(snapshot.nowcastnet)}</strong></article>
          <article><span>周期</span><strong>{formatTime(snapshot.nowcastnet?.issue_time)}</strong></article>
          <article><span>输入帧</span><strong>{snapshot.nowcastnet?.frame_count ?? 0}/9</strong></article>
          <article><span>ROI 共同有效</span><strong>{percent(snapshot.nowcastnet?.common_valid_ratio)}</strong></article>
          <article><span>固定尺寸</span><strong>{shadowROI(snapshot.nowcastnet)}</strong></article>
          <article><span>GPU 形状</span><strong>{snapshot.nowcastnet?.spatial_shape_validated ? '已验证' : '待验证'}</strong></article>
          <p>{snapshot.nowcastnet?.reason ? `当前门禁：${snapshot.nowcastnet.reason}` : '等待连续福建 QPE 输入。'}</p>
        </div>
      </section>

      <section className="admin-panel">
        <header><div><span>Radar workflows</span><h1>福建雷达处理状态</h1></div><small>解码、质控与格点</small></header>
        <div className="admin-radar-table" role="table" aria-label="雷达实时状态">
          <div role="row"><span>雷达</span><span>配置</span><span>健康</span><span>最近体扫</span><span>延迟</span><span>阶段</span></div>
          {snapshot.radars.map((radar) => (
            <div role="row" key={radar.radar_id}>
              <span><strong>{radar.display_name || radar.radar_id.toUpperCase()}</strong><small>{radar.radar_id.toUpperCase()}</small></span>
              <span>{radar.lifecycle ?? '—'}</span>
              <span data-health={radar.health?.toLowerCase()}>{radar.health ?? 'UNKNOWN'}</span>
              <span>{formatTime(radar.latest_scan_time)}</span>
              <span>{delay(radar.data_delay_seconds)}</span>
              <span>{radar.scan_status ?? '—'}</span>
            </div>
          ))}
        </div>
      </section>
      <section className="admin-evidence-grid">
        <details open><summary>运行问题原始证据</summary><pre>{pretty(snapshot.issues)}</pre></details>
        <details><summary>告警原始证据</summary><pre>{pretty(snapshot.alerts)}</pre></details>
      </section>
    </main>
  )
}

async function read<T>(path: string, signal: AbortSignal): Promise<T> {
  const response = await fetch(path, { signal })
  if (!response.ok) throw new Error(`${path} 响应 ${response.status}`)
  return await response.json() as T
}

async function readOptional(path: string, signal: AbortSignal) {
  const response = await fetch(path, { signal })
  if (!response.ok) return { unavailable: true, status: response.status }
  return await response.json() as unknown
}

function evidenceCount(value: unknown) {
  if (Array.isArray(value)) return value.length
  if (!value || typeof value !== 'object') return '—'
  const record = value as Record<string, unknown>
  for (const key of ['items', 'issues', 'alerts', 'groups']) {
    if (Array.isArray(record[key])) return (record[key] as unknown[]).length
  }
  return 0
}

function pretty(value: unknown) {
  return value == null ? '暂无证据' : JSON.stringify(value, null, 2)
}

function formatTime(value?: string) {
  if (!value) return '—'
  return new Intl.DateTimeFormat('zh-CN', {
    timeZone: 'Asia/Taipei', month: '2-digit', day: '2-digit',
    hour: '2-digit', minute: '2-digit', second: '2-digit', hour12: false,
  }).format(new Date(value))
}

function delay(seconds?: number) {
  if (seconds == null) return '—'
  if (seconds < 60) return `${seconds}s`
  if (seconds < 3600) return `${Math.floor(seconds / 60)}m`
  return `${Math.floor(seconds / 3600)}h`
}

function shadowStatusLabel(status: NowcastNetShadowStatus | null) {
  if (!status?.status) return '读取中'
  const labels: Record<string, string> = {
    starting: '启动中',
    waiting: '等待输入',
    input_ineligible: '输入未通过',
    input_eligible: '输入通过',
    running: '推理中',
    failed: '探测失败',
    unavailable: '不可用',
  }
  return labels[status.status] ?? status.status
}

function shadowROI(status: NowcastNetShadowStatus | null) {
  const roi = status?.roi
  if (roi?.width == null || roi.height == null) return '—'
  return `${roi.width}×${roi.height}`
}

function percent(value?: number) {
  return value == null ? '—' : `${(value * 100).toFixed(1)}%`
}
