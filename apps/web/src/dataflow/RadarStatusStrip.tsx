import { useState } from 'react'
import type { DataflowRadarStatus, IngestSource, IngestStatus } from './types'
import { delayTone, formatClock, formatDelay, formatDuration, formatRatio } from './layout'
import { radarSiteFor } from '../radarSites'

const HEALTH_LABELS: Record<string, string> = {
  HEALTHY: '健康',
  DEGRADED: '降级',
  UNAVAILABLE: '不可用',
}

export type RadarBandGroup = {
  key: string
  label: string
  statuses: DataflowRadarStatus[]
  collapsedByDefault: boolean
}

// bandOf mirrors configs/radars: registered sites carry their band, the zf*
// naming convention covers X-band candidates not in the site table, anything
// else (synthetic) lands in 其它.
export function bandOf(radarID: string): 'S' | 'X' | 'other' {
  const known = radarSiteFor(radarID)?.radarBand
  if (known === 'S' || known === 'X') return known
  if (/^zf/i.test(radarID)) return 'X'
  if (/^z9/i.test(radarID)) return 'S'
  return 'other'
}

export function groupByBand(statuses: DataflowRadarStatus[]): RadarBandGroup[] {
  const groups: Record<string, DataflowRadarStatus[]> = { S: [], X: [], other: [] }
  for (const status of statuses) groups[bandOf(status.radar_id)].push(status)
  return [
    { key: 'S', label: 'S 波段', statuses: groups.S, collapsedByDefault: false },
    { key: 'X', label: 'X 波段候选', statuses: groups.X, collapsedByDefault: true },
    { key: 'other', label: '其它', statuses: groups.other, collapsedByDefault: false },
  ].filter(group => group.statuses.length > 0)
}

// RadarStatusStrip is the per-radar "质控进程" summary grouped by band: S-band
// first, X-band candidates collapsed by default, then everything else.
export function RadarStatusStrip({ statuses, ingest }: {
  statuses: DataflowRadarStatus[]
  ingest: IngestStatus | null
}) {
  const ingestByRadar = new Map<string, IngestSource>()
  if (ingest && 'sources' in ingest && Array.isArray(ingest.sources)) {
    for (const source of ingest.sources) {
      if (source.radar_id) ingestByRadar.set(source.radar_id.toLowerCase(), source)
    }
  }
  if (statuses.length === 0) {
    return <p className="df-empty">尚未登记任何雷达；第一批体扫到达后这里会出现状态卡。</p>
  }
  return <div className="df-radar-groups">
    {groupByBand(statuses).map(group => <BandSection
      key={group.key}
      group={group}
      ingestByRadar={ingestByRadar}
    />)}
  </div>
}

function BandSection({ group, ingestByRadar }: {
  group: RadarBandGroup
  ingestByRadar: Map<string, IngestSource>
}) {
  const [open, setOpen] = useState(!group.collapsedByDefault)
  const unavailable = group.statuses.filter(status => status.health === 'UNAVAILABLE').length
  return <section className="df-radar-group">
    <button
      type="button"
      className={open ? 'df-radar-group-head' : 'df-radar-group-head df-radar-group-closed'}
      onClick={() => setOpen(value => !value)}
      aria-expanded={open}
    >
      <span className="df-radar-group-arrow" aria-hidden="true"/>
      <b>{group.label}</b>
      <small>{group.statuses.length} 部</small>
      {unavailable > 0 && <small className="df-count-fail">{unavailable} 不可用</small>}
    </button>
    {open && <div className="df-radar-grid">
      {group.statuses.map(status => <RadarCard
        key={status.radar_id}
        status={status}
        ingest={ingestByRadar.get(status.radar_id.toLowerCase())}
      />)}
    </div>}
  </section>
}

function RadarCard({ status, ingest }: { status: DataflowRadarStatus; ingest?: IngestSource }) {
  const delay = delayTone(status.data_delay_seconds)
  const healthLabel = HEALTH_LABELS[status.health] ?? status.health
  return <div className="df-radar-card" data-health={status.health}>
    <div className="df-radar-head">
      <span className={`df-health-dot df-health-${status.health.toLowerCase()}`} aria-hidden="true"/>
      <b>{status.display_name || status.radar_id.toUpperCase()}</b>
      <small>{status.radar_id.toUpperCase()}</small>
      <span className={`df-tag ${status.participating_in_latest_analysis ? 'df-tag-ok' : 'df-tag-warn'}`}>
        {status.participating_in_latest_analysis ? '参与拼图' : '未参与'}
      </span>
    </div>
    <dl className="df-radar-facts">
      <div><dt>健康</dt><dd>{healthLabel}</dd></div>
      <div><dt>最新体扫</dt><dd>{status.latest_scan_time ? formatClock(status.latest_scan_time) : '—'}</dd></div>
      <div data-tone={delay}><dt>数据延迟</dt><dd>{formatDelay(status.data_delay_seconds)}</dd></div>
      <div><dt>扫描完整率</dt><dd>{formatRatio(status.scan_completeness)}</dd></div>
      <div><dt>平均 QI</dt><dd>{status.mean_quality_index != null ? status.mean_quality_index.toFixed(2) : '—'}</dd></div>
      <div><dt>质控耗时</dt><dd>{formatDuration(status.qc_duration_ms)}</dd></div>
    </dl>
    {ingest?.last_error && <p className="df-radar-error" title={ingest.last_error}>
      采集：{ingest.last_error}
    </p>}
  </div>
}
