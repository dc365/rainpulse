import type { DataflowAnalysisBlock, DataflowForecastBlock, DataflowScanBlock } from './types'
import { formatClock, formatDuration, formatRatio, scanStatusLabel, stageLabel } from './layout'

export type SelectedBlock =
  | { kind: 'scan'; radarID: string; block: DataflowScanBlock }
  | { kind: 'analysis'; block: DataflowAnalysisBlock }
  | { kind: 'forecast'; block: DataflowForecastBlock }

export function selectedBlockKey(selected: SelectedBlock | null): string | null {
  if (!selected) return null
  if (selected.kind === 'scan') return selected.block.scan_id
  if (selected.kind === 'analysis') return selected.block.analysis_id
  return selected.block.run_id
}

// BlockDrawer is the evidence side panel for one lane block: stage-by-stage
// queue/compute timing, failures with codes, and scan quality metrics.
export function BlockDrawer({ selected, onClose }: {
  selected: SelectedBlock | null
  onClose: () => void
}) {
  if (!selected) return null
  const body = drawerBody(selected)
  return <>
    <button type="button" className="df-drawer-backdrop" aria-label="关闭详情" onClick={onClose}/>
    <aside className="df-drawer" role="dialog" aria-label="数据块详情">
      <header>
        <div>
          <h3>{body.title}</h3>
          <p>{body.subtitle}</p>
        </div>
        <button type="button" onClick={onClose} aria-label="关闭详情">✕</button>
      </header>
      <p className={`df-drawer-badge df-drawer-badge-${body.tone}`}>{body.badge}</p>
      <table className="df-stage-table">
        <thead><tr>
          <th>阶段</th><th>状态</th>
          <th className="df-col-number">排队</th>
          <th className="df-col-number">耗时</th>
          <th>完成于</th>
        </tr></thead>
        <tbody>
          {body.stages.map(stage => <tr key={stage.jobID} className={stage.failed ? 'df-stage-row-failed' : undefined}>
            <td>{stage.label}{stage.model ? <small> · {stage.model}</small> : null}</td>
            <td><span className={`df-tag df-stage-${stage.status.toLowerCase()}`}>{stage.statusLabel}</span></td>
            <td className="df-col-number">{formatDuration(stage.queueMS)}</td>
            <td className="df-col-number">{formatDuration(stage.runtimeMS)}</td>
            <td>{stage.finishedAt ? formatClock(stage.finishedAt) : '—'}</td>
          </tr>)}
          {body.stages.length === 0 && <tr><td colSpan={5} className="df-empty">尚未派发任何阶段作业</td></tr>}
        </tbody>
      </table>
      {body.errors.length > 0 && <div className="df-drawer-errors">
        {body.errors.map(error => <p key={error}><code>{error}</code></p>)}
      </div>}
      {body.facts.length > 0 && <dl className="df-drawer-facts">
        {body.facts.map(([term, value]) => <div key={term}><dt>{term}</dt><dd>{value}</dd></div>)}
      </dl>}
      <footer><small>{body.identity}</small></footer>
    </aside>
  </>
}

type StageRow = {
  jobID: string
  label: string
  model: string
  status: string
  statusLabel: string
  queueMS: number | null
  runtimeMS: number | null
  finishedAt: string | null
  failed: boolean
}

type DrawerBody = {
  title: string
  subtitle: string
  badge: string
  tone: 'ok' | 'warn' | 'risk'
  stages: StageRow[]
  errors: string[]
  facts: [string, string][]
  identity: string
}

function stageRows(stages: DataflowScanBlock['stages']): StageRow[] {
  return stages.map(stage => ({
    jobID: stage.job_id,
    label: stageLabel(stage.stage),
    model: stage.model_id ?? '',
    status: stage.status,
    statusLabel: stageStatusName(stage.status),
    queueMS: stage.started_at
      ? Math.max(0, Date.parse(stage.started_at) - Date.parse(stage.created_at))
      : null,
    runtimeMS: stage.runtime_ms ?? null,
    finishedAt: stage.finished_at ?? null,
    failed: stage.status === 'FAILED',
  }))
}

function errorRows(stages: DataflowScanBlock['stages']): string[] {
  return stages
    .filter(stage => stage.status === 'FAILED' && (stage.error_code || stage.error_message))
    .map(stage => `${stage.error_code || 'error'}: ${stage.error_message ?? ''}`)
}

function stageStatusName(status: string): string {
  const names: Record<string, string> = {
    PENDING: '排队中', RUNNING: '执行中', SUCCEEDED: '已完成', FAILED: '失败', SKIPPED: '已跳过',
  }
  return names[status] ?? status
}

function drawerBody(selected: SelectedBlock): DrawerBody {
  if (selected.kind === 'scan') {
    const block = selected.block
    return {
      title: `${selected.radarID.toUpperCase()} 体扫链路`,
      subtitle: `体扫时段 ${formatClock(block.volume_start)} – ${formatClock(block.volume_end)} · 到达 ${formatClock(block.received_at)}`,
      badge: `${scanStatusLabel(block.status)}${block.degraded_reason ? ` · ${block.degraded_reason}` : ''}`,
      tone: block.status === 'FAILED' ? 'risk' : block.status === 'DEGRADED' ? 'warn' : 'ok',
      stages: stageRows(block.stages),
      errors: errorRows(block.stages),
      facts: [
        ['扫描完整率', formatRatio(block.scan_completeness)],
        ['平均 QI', block.mean_quality_index != null ? block.mean_quality_index.toFixed(2) : '—'],
      ],
      identity: `scan ${block.scan_id} · run ${block.run_id}`,
    }
  }
  if (selected.kind === 'analysis') {
    const block = selected.block
    return {
      title: '分析周期链路',
      subtitle: `分析时刻 ${formatClock(block.analysis_time)} · 网格 ${block.grid_id}`,
      badge: `${block.status}${block.degraded_reason ? ` · ${block.degraded_reason}` : ''}`,
      tone: block.status === 'FAILED' ? 'risk' : block.status === 'DEGRADED' ? 'warn' : 'ok',
      stages: stageRows(block.stages),
      errors: errorRows(block.stages),
      facts: [
        ['参与雷达', `${block.radar_count} 部`],
        ['有效覆盖', formatRatio(block.coverage_ratio)],
      ],
      identity: `analysis ${block.analysis_id} · run ${block.run_id}`,
    }
  }
  const block = selected.block
  return {
    title: '预报运行链路',
    subtitle: `起报 ${formatClock(block.issue_time)} · 网格 ${block.grid_id}`,
    badge: block.status,
    tone: block.status === 'FAILED' ? 'risk' : 'ok',
    stages: stageRows(block.stages),
    errors: errorRows(block.stages),
    facts: [],
    identity: `run ${block.run_id}`,
  }
}
