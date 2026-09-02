import { useEffect, useState, type FormEvent } from 'react'

import type { CycleList, CycleSummary } from './model'

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
  cycles: CycleSummary[]
  issues: unknown
  alerts: unknown
}

export function AdminWorkspace() {
  const [snapshot, setSnapshot] = useState<AdminSnapshot>({
    system: null, radars: [], ingest: null, nowcastnet: null, cycles: [], issues: null, alerts: null,
  })
  const [error, setError] = useState<string | null>(null)

  useEffect(() => {
    const controller = new AbortController()
    void Promise.all([
      read<SystemStatus>('/api/v1/system/status', controller.signal),
      read<RadarStatus[]>('/api/v1/radars/status', controller.signal),
      readOptional('/api/v1/workspace/ingest-status', controller.signal),
      readOptional('/api/v1/workspace/nowcastnet-shadow-status', controller.signal),
      read<CycleList>('/api/v1/workspace/cycles?limit=200', controller.signal),
      readOptional('/api/v1/operations/issues', controller.signal),
      readOptional('/api/v1/alerts', controller.signal),
    ]).then(([system, radars, ingest, nowcastnet, cycles, issues, alerts]) => {
      setSnapshot({
        system,
        radars,
        ingest: ingest as IngestStatus,
        nowcastnet: nowcastnet as NowcastNetShadowStatus,
        cycles: cycles.items,
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

      <RegenerationPanel cycles={snapshot.cycles} />

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

type RegenerationPreset = 'forecast_all' | 'pysteps_lk' | 'products'

const regenerationPresets: Array<{
  value: RegenerationPreset
  label: string
  route: string
}> = [
  { value: 'forecast_all', label: '主链路全部', route: '输入 → LK → 产品' },
  { value: 'pysteps_lk', label: 'pySTEPS-LK', route: '输入 → 光流外推 → 产品' },
  { value: 'products', label: '应用产品', route: '安全重走依赖 → 产品' },
]

type RegenerationResult = {
  run_id?: string
  rerun_of?: string
  status?: string
  code?: string
  message?: string
}

function RegenerationPanel({ cycles }: { cycles: CycleSummary[] }) {
  const runnableCycles = cycles.filter((cycle) => cycle.run_id)
  const [cycleID, setCycleID] = useState('')
  const [preset, setPreset] = useState<RegenerationPreset>('forecast_all')
  const [reason, setReason] = useState('验证更新后的算法配置')
  const [token, setToken] = useState('')
  const [confirming, setConfirming] = useState(false)
  const [submitting, setSubmitting] = useState(false)
  const [error, setError] = useState<string | null>(null)
  const [result, setResult] = useState<RegenerationResult | null>(null)

  const effectiveCycleID = runnableCycles.some((cycle) => cycle.cycle_id === cycleID)
    ? cycleID
    : runnableCycles[0]?.cycle_id ?? ''
  const selectedCycle = runnableCycles.find((cycle) => cycle.cycle_id === effectiveCycleID)
  const selectedPreset = regenerationPresets.find((option) => option.value === preset) ?? regenerationPresets[0]
  const reasonLength = Array.from(reason.trim()).length
  const ready = Boolean(selectedCycle?.run_id && token && reasonLength >= 3 && reasonLength <= 240)

  const resetDecision = () => {
    setConfirming(false)
    setError(null)
    setResult(null)
  }

  const submit = async (event: FormEvent) => {
    event.preventDefault()
    if (!ready || !selectedCycle?.run_id) {
      setError('请选择可重算周期，并填写管理令牌和 3～240 字的原因。')
      setConfirming(false)
      return
    }
    if (!confirming) {
      setConfirming(true)
      return
    }
    setSubmitting(true)
    setError(null)
    setResult(null)
    try {
      const response = await fetch(`/api/v1/admin/runs/${encodeURIComponent(selectedCycle.run_id)}/rerun`, {
        method: 'POST',
        headers: {
          Authorization: `Bearer ${token}`,
          'Content-Type': 'application/json',
        },
        body: JSON.stringify({ preset, reason: reason.trim() }),
      })
      const payload = await response.json() as RegenerationResult
      if (!response.ok) {
        throw new Error(regenerationError(payload, response.status))
      }
      setResult(payload)
      setToken('')
      setConfirming(false)
    } catch (requestError) {
      setError(requestError instanceof Error ? requestError.message : '重算请求提交失败')
      setConfirming(false)
    } finally {
      setSubmitting(false)
    }
  }

  return (
    <section className="admin-panel admin-regeneration-panel">
      <header>
        <div><span>Manual regeneration</span><h1>数据重算</h1></div>
        <small>固定预设 · 新运行发布 · 旧结果成功前保留</small>
      </header>
      <form className="admin-regeneration" onSubmit={(event) => void submit(event)}>
        <div className="admin-regeneration-fields">
          <label>
            <span>起报周期</span>
            <select
              aria-label="重算起报周期"
              value={effectiveCycleID}
              onChange={(event) => { setCycleID(event.target.value); resetDecision() }}
              disabled={!runnableCycles.length || submitting}
            >
              {runnableCycles.length ? null : <option value="">暂无可重算周期</option>}
              {runnableCycles.map((cycle) => (
                <option key={cycle.cycle_id} value={cycle.cycle_id}>
                  {formatTime(cycle.issue_time)} · {capabilityLabel(cycle)}
                </option>
              ))}
            </select>
          </label>
          <fieldset className="admin-regeneration-presets">
            <legend>重算范围</legend>
            {regenerationPresets.map((option) => (
              <button
                key={option.value}
                type="button"
                className={preset === option.value ? 'active' : ''}
                aria-pressed={preset === option.value}
                onClick={() => { setPreset(option.value); resetDecision() }}
                disabled={submitting}
              >
                <strong>{option.label}</strong><small>{option.route}</small>
              </button>
            ))}
          </fieldset>
          <label>
            <span>重算原因 <small>{reasonLength}/240</small></span>
            <input
              aria-label="重算原因"
              value={reason}
              onChange={(event) => { setReason(event.target.value); resetDecision() }}
              maxLength={240}
              disabled={submitting}
            />
          </label>
          <label>
            <span>管理令牌 <small>仅本次请求使用，不保存</small></span>
            <input
              aria-label="管理令牌"
              type="password"
              value={token}
              onChange={(event) => { setToken(event.target.value); resetDecision() }}
              autoComplete="off"
              spellCheck={false}
              disabled={submitting}
            />
          </label>
        </div>

        <aside className="admin-regeneration-trace" aria-live="polite">
          <span>本次谱系</span>
          <strong>{selectedPreset.label}</strong>
          <dl>
            <div><dt>起报</dt><dd>{formatTime(selectedCycle?.issue_time)}</dd></div>
            <div><dt>源运行</dt><dd title={selectedCycle?.run_id}>{shortID(selectedCycle?.run_id)}</dd></div>
            <div><dt>格点</dt><dd title={selectedCycle?.grid_id}>{selectedCycle?.grid_id ?? '—'}</dd></div>
          </dl>
          <p>创建新的可追溯运行；新结果成功发布后，工作台自动选择最新版本。</p>
          {confirming ? (
            <div className="admin-regeneration-confirm">
              <strong>确认提交这次重算？</strong>
              <span>同一周期、同一预设正在运行时会被拒绝。</span>
              <div>
                <button type="button" onClick={() => setConfirming(false)}>取消</button>
                <button type="submit" disabled={submitting}>{submitting ? '提交中…' : '确认执行'}</button>
              </div>
            </div>
          ) : (
            <button className="admin-regeneration-submit" type="submit" disabled={!ready || submitting}>
              {submitting ? '提交中…' : '准备重算'}
            </button>
          )}
          {result ? <p className="admin-regeneration-success">已受理：{shortID(result.run_id)} · {result.status ?? 'QUEUED'}</p> : null}
          {error ? <p className="admin-regeneration-error" role="alert">{error}</p> : null}
        </aside>
      </form>
      <footer className="admin-regeneration-note">
        当前界面覆盖控制面主链路；pySTEPS-STEPS 与 NowcastNet 离线产物仍沿用服务器受控入口。
      </footer>
    </section>
  )
}

function regenerationError(payload: RegenerationResult, status: number) {
  const labels: Record<string, string> = {
    invalid_token: '管理令牌无效。',
    regeneration_active: '该周期与预设已有重算任务正在执行。',
    unsupported_rerun: '所选周期缺少可复用的已提交输入谱系。',
    invalid_regeneration_reason: '重算原因必须为 3～240 个字符。',
  }
  return labels[payload.code ?? ''] ?? payload.message ?? `重算请求响应 ${status}`
}

function capabilityLabel(cycle: CycleSummary) {
  const values = ['QPE']
  if (cycle.capabilities.lk) values.push('LK')
  if (cycle.capabilities.steps) values.push('STEPS')
  if (cycle.capabilities.nowcastnet) values.push('NowcastNet')
  return values.join('/')
}

function shortID(value?: string) {
  if (!value) return '—'
  return value.length > 12 ? `${value.slice(0, 8)}…${value.slice(-4)}` : value
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
