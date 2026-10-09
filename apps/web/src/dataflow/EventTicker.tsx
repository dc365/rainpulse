import type { DataflowEvent } from './types'
import { formatClockSeconds } from './layout'

const KIND_LABELS: Record<string, string> = {
  'scan.received': '到达',
  'job.succeeded': '完成',
  'job.failed': '失败',
  'analysis.created': '分析',
  'run.published': '发布',
}

// EventTicker is the thin Dagster-style terminal event feed derived from the
// same window rows the lanes already show. Clicking an entry locates and
// flashes the matching swimlane block.
export function EventTicker({ events, onSelectEvent }: {
  events: DataflowEvent[]
  onSelectEvent?: (event: DataflowEvent) => void
}) {
  if (events.length === 0) {
    return <p className="df-empty">窗口内还没有终结事件。</p>
  }
  return <ul className="df-events" role="list">
    {events.map((event, index) => <li key={`${event.time}-${event.label}-${index}`} role="listitem">
      <button
        type="button"
        className={`df-event df-event-${event.kind.replace('.', '-')}`}
        onClick={() => onSelectEvent?.(event)}
        title="点击在泳道中定位对应数据块"
        disabled={!onSelectEvent}
      >
        <time>{formatClockSeconds(event.time)}</time>
        <span className="df-event-dot" aria-hidden="true"/>
        <span className="df-event-kind">{KIND_LABELS[event.kind] ?? event.kind}</span>
        <span className="df-event-label">{event.label}</span>
        {event.detail && <small>{event.detail}</small>}
      </button>
    </li>)}
  </ul>
}
