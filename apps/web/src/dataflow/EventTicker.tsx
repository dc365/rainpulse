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
// same window rows the lanes already show.
export function EventTicker({ events }: { events: DataflowEvent[] }) {
  if (events.length === 0) {
    return <p className="df-empty">窗口内还没有终结事件。</p>
  }
  return <ul className="df-events" role="list">
    {events.map((event, index) => <li
      className={`df-event df-event-${event.kind.replace('.', '-')}`}
      key={`${event.time}-${event.label}-${index}`}
      role="listitem"
    >
      <time>{formatClockSeconds(event.time)}</time>
      <span className="df-event-dot" aria-hidden="true"/>
      <span className="df-event-kind">{KIND_LABELS[event.kind] ?? event.kind}</span>
      <span className="df-event-label" title={event.detail || undefined}>{event.label}</span>
      {event.detail && <small>{event.detail}</small>}
    </li>)}
  </ul>
}
