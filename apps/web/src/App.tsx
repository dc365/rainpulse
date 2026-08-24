import { useCallback, useEffect, useMemo, useState } from 'react'

import type { components } from './api/generated/schema'
import './styles.css'

type SystemStatus = components['schemas']['SystemStatus']
type RadarStatus = components['schemas']['RadarStatusSummary']
type HealthState = components['schemas']['RadarHealthState']

const healthLabels: Record<HealthState, string> = {
  UNKNOWN: '状态未知',
  HEALTHY: '运行健康',
  DEGRADED: '降级运行',
  UNAVAILABLE: '当前不可用',
}

const reasonLabels: Record<string, string> = {
  ANOMALOUS_VALUES: '发现格式范围异常值',
  AZIMUTH_GAP: '方位覆盖存在缺口',
  CONFIG_NOT_READY: '雷达配置尚未转为 ready',
  DBZH_UNAVAILABLE: '基本反射率不可用',
  FIELD_UNAVAILABLE: '部分观测字段不可用',
  NOISE_OUT_OF_RANGE: '通道噪声超出合理范围',
  NOISE_TELEMETRY_MISSING: '基数据未提供有效噪声遥测',
  SCAN_INCOMPLETE: '体扫完整率低于门槛',
  SOURCE_TIME_MISMATCH: '文件名时间与基数据内部时间不一致',
}

const qcModuleLabels: Record<string, string> = {
  attenuation_and_calibration: '衰减与标定',
  dem_blockage: 'DEM 遮挡',
  health_gate: '雷达健康门控',
  missing_and_echo_state: '缺测与回波状态',
  quality_index: '基础质量指数',
  radial_interference: '径向干扰',
  sea_ap: '海杂波 / AP',
  static_ground_clutter: '静态地物杂波',
}

const qcStatusLabels: Record<string, string> = {
  applied: '已执行',
  failed: '执行失败',
  skipped: '已跳过',
}

function formatUtc(value?: string | null) {
  if (!value) return '尚无体扫'
  return `${new Intl.DateTimeFormat('zh-CN', {
    timeZone: 'UTC',
    year: 'numeric',
    month: '2-digit',
    day: '2-digit',
    hour: '2-digit',
    minute: '2-digit',
    second: '2-digit',
    hour12: false,
  }).format(new Date(value))} UTC`
}

function formatDelay(seconds?: number | null) {
  if (seconds == null) return '暂无数据'
  if (seconds < 60) return `${seconds} 秒`
  if (seconds < 3600) return `${Math.floor(seconds / 60)} 分钟`
  if (seconds < 86400) return `${Math.floor(seconds / 3600)} 小时`
  return `${Math.floor(seconds / 86400)} 天`
}

function percent(value?: number | null) {
  return value == null ? '—' : `${(value * 100).toFixed(value >= 0.9995 ? 0 : 1)}%`
}

export default function App() {
  const [systemStatus, setSystemStatus] = useState<SystemStatus | null>(null)
  const [radars, setRadars] = useState<RadarStatus[]>([])
  const [selectedRadarId, setSelectedRadarId] = useState<string | null>(null)
  const [error, setError] = useState<string | null>(null)
  const [loading, setLoading] = useState(true)
  const [refreshing, setRefreshing] = useState(false)
  const [updatedAt, setUpdatedAt] = useState<Date | null>(null)

  const load = useCallback(async (signal?: AbortSignal, manual = false) => {
    if (manual) setRefreshing(true)
    try {
      const [systemResponse, radarResponse] = await Promise.all([
        fetch('/api/v1/system/status', { signal }),
        fetch('/api/v1/radars/status', { signal }),
      ])
      if (!systemResponse.ok || !radarResponse.ok) {
        throw new Error(`控制面响应异常（${systemResponse.status}/${radarResponse.status}）`)
      }
      const [system, radarStatuses] = await Promise.all([
        systemResponse.json() as Promise<SystemStatus>,
        radarResponse.json() as Promise<RadarStatus[]>,
      ])
      setSystemStatus(system)
      setRadars(radarStatuses)
      setSelectedRadarId((current) => {
        if (current && radarStatuses.some((radar) => radar.radar_id === current)) return current
        return (
          radarStatuses.find((radar) => radar.health_metrics)?.radar_id ??
          radarStatuses[0]?.radar_id ??
          null
        )
      })
      setUpdatedAt(new Date())
      setError(null)
    } catch (requestError: unknown) {
      if (!(requestError instanceof DOMException && requestError.name === 'AbortError')) {
        setError(requestError instanceof Error ? requestError.message : '控制面请求失败')
      }
    } finally {
      if (!signal?.aborted) {
        setLoading(false)
        setRefreshing(false)
      }
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
  }, [load])

  const selected = useMemo(
    () => radars.find((radar) => radar.radar_id === selectedRadarId) ?? null,
    [radars, selectedRadarId],
  )
  const healthyCount = radars.filter((radar) => radar.health === 'HEALTHY').length
  const riskCount = radars.filter((radar) =>
    ['DEGRADED', 'UNAVAILABLE'].includes(radar.health),
  ).length
  const readyCount = radars.filter((radar) => radar.lifecycle === 'ready').length

  return (
    <main className="app-shell">
      <header className="topbar">
        <div className="brand-lockup">
          <span className="brand-mark" aria-hidden="true">
            <i />
            <i />
            <i />
          </span>
          <div>
            <p className="brand">RainPulse</p>
            <p className="product-name">短临降水预报系统</p>
          </div>
        </div>
        <div className="system-meta" aria-live="polite">
          <span className={`system-dot ${systemStatus?.status ?? 'loading'}`} />
          <span>{systemStatus?.status === 'ready' ? '控制面正常' : '控制面检查中'}</span>
          <span className="meta-divider" />
          <span className="version">{systemStatus?.version ?? '—'}</span>
          <button
            className="refresh-button"
            type="button"
            aria-label="刷新雷达状态"
            disabled={refreshing}
            onClick={() => void load(undefined, true)}
          >
            <svg viewBox="0 0 24 24" aria-hidden="true">
              <path d="M20 7v5h-5M4 17v-5h5" />
              <path d="M18.5 9A7 7 0 0 0 6.7 6.7L4 9m16 6-2.7 2.3A7 7 0 0 1 5.5 15" />
            </svg>
          </button>
        </div>
      </header>

      <section className="page-heading" aria-labelledby="page-title">
        <div>
          <p className="section-kicker">Radar operations / RP-007 + RP-008</p>
          <h1 id="page-title">雷达运行总览</h1>
          <p>监控基数据解码、体扫完整性、设备通道状态与基础极坐标质控。</p>
        </div>
        <div className="update-time">
          <span>状态更新时间</span>
          <strong>{updatedAt ? updatedAt.toLocaleTimeString('zh-CN', { hour12: false }) : '—'}</strong>
          <small>每 30 秒自动刷新</small>
        </div>
      </section>

      {error ? (
        <div className="error-banner" role="alert">
          <strong>数据连接中断</strong>
          <span>{error}</span>
        </div>
      ) : null}

      <section className="summary-strip" aria-label="雷达状态摘要">
        <SummaryStat label="登记雷达" value={radars.length} note="配置库存" />
        <SummaryStat label="健康运行" value={healthyCount} note="最近体扫" tone="healthy" />
        <SummaryStat label="风险雷达" value={riskCount} note="降级或不可用" tone="risk" />
        <SummaryStat label="可运行配置" value={`${readyCount}/${radars.length}`} note="lifecycle ready" />
      </section>

      <section className="operations-grid">
        <aside className="radar-panel" aria-label="雷达站列表">
          <div className="panel-heading">
            <div>
              <p className="panel-label">Radar roster</p>
              <h2>雷达站</h2>
            </div>
            <span className="count-badge">{radars.length}</span>
          </div>
          <div className="radar-list">
            {loading ? <p className="empty-state">正在读取雷达状态…</p> : null}
            {!loading && radars.length === 0 ? <p className="empty-state">尚未登记雷达</p> : null}
            {radars.map((radar) => (
              <button
                className={`radar-row ${selectedRadarId === radar.radar_id ? 'selected' : ''}`}
                key={radar.radar_id}
                type="button"
                aria-pressed={selectedRadarId === radar.radar_id}
                onClick={() => setSelectedRadarId(radar.radar_id)}
              >
                <span className={`health-dot ${radar.health.toLowerCase()}`} />
                <span className="radar-identity">
                  <strong>{radar.display_name || radar.radar_id.toUpperCase()}</strong>
                  <small>{radar.radar_id.toUpperCase()} · {radar.lifecycle}</small>
                </span>
                <span className="radar-row-metric">
                  <strong>{percent(radar.scan_completeness)}</strong>
                  <small>{healthLabels[radar.health]}</small>
                </span>
              </button>
            ))}
          </div>
          <div className="legend">
            <span><i className="health-dot healthy" />健康</span>
            <span><i className="health-dot degraded" />降级</span>
            <span><i className="health-dot unavailable" />不可用</span>
          </div>
        </aside>

        <section className="detail-panel" aria-live="polite">
          {selected ? <RadarDetail radar={selected} /> : <p className="empty-state">请选择一个雷达站</p>}
        </section>
      </section>
    </main>
  )
}

function SummaryStat({
  label,
  value,
  note,
  tone = 'neutral',
}: {
  label: string
  value: string | number
  note: string
  tone?: 'neutral' | 'healthy' | 'risk'
}) {
  return (
    <div className={`summary-stat ${tone}`}>
      <p>{label}</p>
      <strong>{value}</strong>
      <span>{note}</span>
    </div>
  )
}

function RadarDetail({ radar }: { radar: RadarStatus }) {
  const health = radar.health_metrics
  const qc = radar.qc_metrics
  return (
    <>
      <div className="detail-heading">
        <div>
          <div className="radar-title-line">
            <h2>{radar.display_name || radar.radar_id.toUpperCase()}</h2>
            <span className={`status-chip ${radar.health.toLowerCase()}`}>
              <i className={`health-dot ${radar.health.toLowerCase()}`} />
              {healthLabels[radar.health]}
            </span>
          </div>
          <p>{radar.radar_id.toUpperCase()} · {radar.config_version}</p>
        </div>
        <span className={`lifecycle-chip ${radar.lifecycle}`}>配置 {radar.lifecycle}</span>
      </div>

      <div className="scan-strip">
        <Metric label="最近体扫" value={formatUtc(radar.latest_scan_time)} />
        <Metric label="数据延迟" value={formatDelay(radar.data_delay_seconds)} />
        <Metric label="处理状态" value={radar.scan_status ?? '尚无任务'} />
        <Metric
          label="拼图参与"
          value={radar.participating_in_latest_analysis ? '已参与' : '尚未参与'}
        />
      </div>

      <div className="detail-grid">
        <article className="module completeness-module">
          <ModuleHeading eyebrow="Scan integrity" title="体扫完整性" />
          <div className="completeness-value">
            <strong>{percent(health?.scan_completeness ?? radar.scan_completeness)}</strong>
            <span>门槛 97%</span>
          </div>
          <div className="progress-track" aria-label="体扫完整率">
            <span style={{ width: `${Math.min(100, (health?.scan_completeness ?? 0) * 100)}%` }} />
          </div>
          <dl className="metric-table">
            <div><dt>仰角层</dt><dd>{health ? `${health.actual_sweep_count} / ${health.expected_sweep_count}` : '—'}</dd></div>
            <div><dt>实际径向</dt><dd>{health?.actual_radial_count.toLocaleString('zh-CN') ?? '—'}</dd></div>
            <div><dt>缺失径向</dt><dd>{health?.missing_radial_count.toLocaleString('zh-CN') ?? '—'}</dd></div>
            <div><dt>最大方位缺口</dt><dd>{health ? `${health.maximum_azimuth_gap_deg.toFixed(2)}°` : '—'}</dd></div>
          </dl>
        </article>

        <article className="module health-module">
          <ModuleHeading eyebrow="Health decision" title="健康判定" />
          {health?.health_reasons.length ? (
            <ul className="reason-list">
              {health.health_reasons.map((reason) => (
                <li key={reason}>
                  <span>{reasonLabels[reason] ?? reason}</span>
                  <code>{reason}</code>
                </li>
              ))}
            </ul>
          ) : (
            <p className="module-empty">当前没有健康降级原因。</p>
          )}
        </article>

        <article className="module fields-module">
          <ModuleHeading
            eyebrow="Field coverage"
            title="观测字段"
            trailing={health ? `${health.field_availability.length} 项` : undefined}
          />
          <div className="field-list">
            {health?.field_availability.map((field) => (
              <div className="field-row" key={field.field}>
                <span className={`field-state ${field.available ? 'available' : 'missing'}`} />
                <strong>{field.field}</strong>
                <span>{field.present_sweep_count} 层</span>
                <span>{percent(field.finite_gate_ratio)}</span>
                <small>{field.unit}</small>
              </div>
            )) ?? <p className="module-empty">等待完整性诊断。</p>}
          </div>
        </article>

        <article className="module channel-module">
          <ModuleHeading eyebrow="Equipment telemetry" title="通道与异常" />
          <div className="channel-status">
            <span className={`channel-code ${(health?.channel_status ?? 'UNKNOWN').toLowerCase()}`}>
              {health?.channel_status ?? 'UNKNOWN'}
            </span>
            <p>{health?.noise_level.sample_count ? '噪声遥测有效' : '当前基数据以缺测码记录噪声值'}</p>
          </div>
          <dl className="metric-table compact">
            <div><dt>水平通道噪声</dt><dd>{health?.noise_level.horizontal_dbm == null ? '缺测' : `${health.noise_level.horizontal_dbm} dBm`}</dd></div>
            <div><dt>垂直通道噪声</dt><dd>{health?.noise_level.vertical_dbm == null ? '缺测' : `${health.noise_level.vertical_dbm} dBm`}</dd></div>
            <div><dt>越界门数</dt><dd>{health?.out_of_range_gate_count.toLocaleString('zh-CN') ?? '—'}</dd></div>
            <div><dt>异常总数</dt><dd>{health?.anomaly_count.toLocaleString('zh-CN') ?? '—'}</dd></div>
          </dl>
          <div className="qc-note">
            <span>QI / 回波质控</span>
            <strong>{qc ? `${percent(qc.mean_quality_index)} · ${qc.qc_profile}` : '等待 RP-008'}</strong>
          </div>
        </article>

        <article className="module qc-module">
          <ModuleHeading
            eyebrow="Polar quality control"
            title="基础极坐标质控"
            trailing={qc?.qc_pipeline_version}
          />
          {qc ? (
            <div className="qc-layout">
              <div className="quality-block">
                <span>平均质量指数</span>
                <strong>{percent(qc.mean_quality_index)}</strong>
                <div className="progress-track quality-track" aria-label="平均质量指数">
                  <span style={{ width: `${Math.min(100, qc.mean_quality_index * 100)}%` }} />
                </div>
                <small>{qc.flag_definition_version}</small>
              </div>
              <dl className="qc-counts">
                <div><dt>有效距离库</dt><dd>{qc.valid_gate_count.toLocaleString('zh-CN')}</dd></div>
                <div><dt>缺测距离库</dt><dd>{qc.missing_gate_count.toLocaleString('zh-CN')}</dd></div>
                <div><dt>低质量距离库</dt><dd>{qc.low_quality_gate_count.toLocaleString('zh-CN')}</dd></div>
                <div><dt>有效无雨</dt><dd>{qc.no_rain_gate_count.toLocaleString('zh-CN')}</dd></div>
                <div><dt>径向干扰</dt><dd>{qc.radial_interference_ray_count.toLocaleString('zh-CN')} 条</dd></div>
                <div><dt>杂波 / AP 标记</dt><dd>{(qc.ground_clutter_gate_count + qc.sea_clutter_gate_count + qc.ap_gate_count).toLocaleString('zh-CN')}</dd></div>
              </dl>
              <div className="qc-module-list" aria-label="质控模块状态">
                {Object.entries(qc.module_statuses).map(([name, state]) => (
                  <div key={name}>
                    <span>{qcModuleLabels[name] ?? name}</span>
                    <strong className={`module-state ${state}`}>{qcStatusLabels[state] ?? state}</strong>
                  </div>
                ))}
              </div>
            </div>
          ) : (
            <p className="module-empty">当前体扫尚未生成 QCRadarVolume，等待 RP-008 Worker。</p>
          )}
        </article>
      </div>
    </>
  )
}

function ModuleHeading({
  eyebrow,
  title,
  trailing,
}: {
  eyebrow: string
  title: string
  trailing?: string
}) {
  return (
    <header className="module-heading">
      <div><p>{eyebrow}</p><h3>{title}</h3></div>
      {trailing ? <span>{trailing}</span> : null}
    </header>
  )
}

function Metric({ label, value }: { label: string; value: string }) {
  return <div className="scan-metric"><span>{label}</span><strong>{value}</strong></div>
}
