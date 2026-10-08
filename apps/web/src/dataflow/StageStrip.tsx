import type { DataflowStageSummary } from './types'
import { formatDuration } from './layout'

// StageStrip is the chain beat rail: seven downstream nodes with per-window
// counts, a p50 runtime and a live connector between busy neighbours.
export function StageStrip({ stages, nowActive }: { stages: DataflowStageSummary[]; nowActive: boolean }) {
  return <div className="df-strip" role="list">
    {stages.map((stage, index) => {
      const tone = stage.failed > 0 ? 'risk'
        : stage.running > 0 ? 'busy'
        : stage.completed > 0 || stage.queued > 0 ? 'ok' : 'idle'
      const flowing = nowActive && (stage.running > 0 || stage.queued > 0)
      return <div className="df-strip-cell" role="listitem" key={stage.key}>
        <div className={`df-node df-node-${tone}`} data-flowing={flowing || undefined}>
          <span className="df-node-dot" aria-hidden="true"/>
          <b>{stage.label}</b>
        </div>
        <div className="df-node-counts">
          <span className="df-count-done">{stage.completed} 完成</span>
          {stage.running > 0 && <span className="df-count-run">{stage.running} 执行</span>}
          {stage.queued > 0 && <span className="df-count-queue">{stage.queued} 排队</span>}
          {stage.failed > 0 && <span className="df-count-fail">{stage.failed} 失败</span>}
          {stage.key !== 'ingest' && <small>p50 {formatDuration(stage.p50_ms || null)}</small>}
        </div>
        {index < stages.length - 1 && <Connector active={nowActive && stages[index + 1].running > 0} />}
      </div>
    })}
  </div>
}

function Connector({ active }: { active: boolean }) {
  return <span className="df-connector" data-active={active || undefined} aria-hidden="true">
    <span className="df-connector-line"/>
  </span>
}
