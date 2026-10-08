import { useEffect, useRef, useState } from 'react'
import type {
  DataflowAnalysisBlock,
  DataflowForecastBlock,
  DataflowRadarLane,
  DataflowRadarStatus,
  DataflowSnapshot,
} from './types'
import { selectedBlockKey, type SelectedBlock } from './BlockDrawer'
import {
  HEADROOM_MINUTES,
  LANE_LABEL_WIDTH,
  type BlockLayout,
  type SegmentPlan,
  analysisBlockLayout,
  axisTicks,
  fitPxPerMin,
  forecastBlockLayout,
  formatClock,
  gapRanges,
  scanBlockLayout,
  segmentPlan,
  windowSpec,
  xForTime,
} from './layout'

export type { SelectedBlock } from './BlockDrawer'

type SwimlaneRow = {
  key: string
  label: string
  caption?: string
  tone?: 'ok' | 'warn' | 'risk'
  blocks: BlockLayout[]
  plansByBlock: SegmentPlan[]
  onPress?: (index: number) => void
}

// Swimlanes renders the wall-clock lanes: one row per radar plus the shared
// analysis and forecast rows. Left of the live head is history ("已处理");
// the head itself is "正在处理".
export function Swimlanes({
  snapshot,
  now,
  onSelect,
  selected,
  nowLabel = '现在',
}: {
  snapshot: DataflowSnapshot
  now: number
  onSelect: (block: SelectedBlock) => void
  selected: SelectedBlock | null
  nowLabel?: string
}) {
  const scrollRef = useRef<HTMLDivElement>(null)
  const [availableWidth, setAvailableWidth] = useState(0)
  useEffect(() => {
    const element = scrollRef.current
    if (!element || typeof ResizeObserver === 'undefined') return
    const observer = new ResizeObserver(entries => {
      for (const entry of entries) setAvailableWidth(entry.contentRect.width)
    })
    observer.observe(element)
    return () => observer.disconnect()
  }, [])
  const minutes = snapshot.window_minutes + HEADROOM_MINUTES
  const pxPerMin = availableWidth > 0
    ? fitPxPerMin(availableWidth, LANE_LABEL_WIDTH, minutes)
    : 10
  const spec = windowSpec(snapshot.window_minutes, now, pxPerMin)
  const ticks = axisTicks(spec)
  const statusByRadar = new Map(snapshot.radar_statuses.map(status => [status.radar_id, status]))
  const selectedKey = selectedBlockKey(selected)

  const rows: SwimlaneRow[] = snapshot.radar_lanes.map(lane => {
    const status = statusByRadar.get(lane.radar_id)
    return {
      key: lane.radar_id,
      label: status?.display_name || lane.radar_id.toUpperCase(),
      caption: radarCaption(lane, status),
      tone: rowTone(lane, status),
      blocks: lane.blocks.map(block => scanBlockLayout(block, spec, now)),
      plansByBlock: lane.blocks.map(block =>
        segmentPlan(block.stages, spec, now, Date.parse(block.volume_end))),
      onPress: index => onSelect({ kind: 'scan', radarID: lane.radar_id, block: lane.blocks[index] }),
    }
  })
  rows.push({
    key: 'analysis',
    label: '分析周期',
    caption: snapshot.analysis_blocks.length > 0
      ? `拼图 · QPE · ${snapshot.analysis_blocks[snapshot.analysis_blocks.length - 1].radar_count} 雷达`
      : '拼图 · QPE',
    blocks: snapshot.analysis_blocks.map(block => analysisBlockLayout(block, spec, now)),
    plansByBlock: snapshot.analysis_blocks.map(block =>
      segmentPlan(block.stages, spec, now, Date.parse(block.analysis_time))),
    onPress: index => onSelect({ kind: 'analysis', block: snapshot.analysis_blocks[index] }),
  })
  rows.push({
    key: 'forecast',
    label: '预报与发布',
    caption: snapshot.forecast_blocks.length > 0
      ? formatClock(snapshot.forecast_blocks[snapshot.forecast_blocks.length - 1].issue_time)
      : undefined,
    blocks: snapshot.forecast_blocks.map(block => forecastBlockLayout(block, spec, now)),
    plansByBlock: snapshot.forecast_blocks.map(block =>
      segmentPlan(block.stages, spec, now, Date.parse(block.issue_time))),
    onPress: index => onSelect({ kind: 'forecast', block: snapshot.forecast_blocks[index] }),
  })

  const nowX = xForTime(now, spec)
  return <div className="df-lanes" data-window={snapshot.window_minutes} ref={scrollRef}>
    <div className="df-lanes-inner" style={{ width: LANE_LABEL_WIDTH + spec.width }}>
      <div className="df-lane df-lane-axis">
        <div className="df-lane-label"/>
        <div className="df-lane-track" style={{ width: spec.width }}>
          {ticks.map(tick => <span
            className={tick.major ? 'df-tick df-tick-major' : 'df-tick'}
            style={{ left: tick.left }}
            key={tick.label + tick.left}
          >{tick.label}</span>)}
        </div>
      </div>
      {rows.map(row => <LaneRow
        key={row.key}
        row={row}
        selectedKey={selectedKey}
        specStart={spec.start}
        specEnd={spec.end}
      />)}
      <div className="df-lane-grid" aria-hidden="true">
        {ticks.map(tick => tick.major
          ? <span className="df-gridline" style={{ left: LANE_LABEL_WIDTH + tick.left }} key={`grid-${tick.left}`}/>
          : null)}
        <div className="df-nowline" style={{ left: LANE_LABEL_WIDTH + nowX }}>
          <span className="df-nowline-label">{nowLabel}</span>
        </div>
      </div>
    </div>
  </div>
}

function LaneRow({
  row,
  selectedKey,
  specStart,
  specEnd,
}: {
  row: SwimlaneRow
  selectedKey: string | null
  specStart: number
  specEnd: number
}) {
  const gaps = gapRanges(row.blocks.map(block => block.startAt), specStart, specEnd)
  return <div className="df-lane" data-tone={row.tone}>
    <div className="df-lane-label">
      <b>{row.label}</b>
      {row.caption && <small>{row.caption}</small>}
    </div>
    <div className="df-lane-track">
      {gaps.map(gap => <span
        className="df-gap"
        style={{ left: gap.left, width: gap.width }}
        title={`断流约 ${gap.minutes} 分钟`}
        key={`gap-${gap.left}`}
      />)}
      {row.blocks.map((block, index) => {
        const plan = row.plansByBlock[index]
        const active = plan.segments.some(segment => segment.state === 'running')
        return <button
          type="button"
          className={active || selectedKey === block.key
            ? `df-block ${active ? 'df-block-live' : ''} ${selectedKey === block.key ? 'df-block-selected' : ''}`
            : 'df-block'}
          style={{ left: block.left, width: block.width }}
          onClick={() => row.onPress?.(index)}
          key={block.key}
        >
          {plan.mode === 'timed'
            ? plan.segments.map((segment, segmentIndex) => <span
                className={`df-seg df-seg-${segment.state}`}
                style={{ left: segment.left - block.left, width: segment.width }}
                data-stage={segment.stage}
                key={`${segment.stage}-${segmentIndex}`}
              />)
            : plan.segments.map((segment, segmentIndex) => <span
                className={`df-seg df-seg-${segment.state}`}
                style={{ left: `${segment.leftPct}%`, width: `${segment.widthPct}%` }}
                data-stage={segment.stage}
                key={`${segment.stage}-${segmentIndex}`}
              />)}
        </button>
      })}
      {row.blocks.length === 0 && <span className="df-lane-empty">窗口内暂无数据</span>}
    </div>
  </div>
}

function radarCaption(lane: DataflowRadarLane, status: DataflowRadarStatus | undefined): string {
  if (status?.data_delay_seconds != null) {
    if (status.data_delay_seconds > 720) return `断流 ${Math.round(status.data_delay_seconds / 60)} 分钟`
    return `${Math.round(status.data_delay_seconds)} s 前`
  }
  return lane.radar_id.toUpperCase()
}

function rowTone(lane: DataflowRadarLane, status: DataflowRadarStatus | undefined): 'ok' | 'warn' | 'risk' {
  if (status?.health === 'UNAVAILABLE') return 'risk'
  if (status?.health === 'DEGRADED') return 'warn'
  const failed = lane.blocks.some(block =>
    block.status === 'FAILED' || block.stages.some(stage => stage.status === 'FAILED'))
  if (failed) return 'warn'
  return 'ok'
}
