import { useEffect, useRef, useState } from 'react'
import type {
  DataflowAnalysisBlock,
  DataflowForecastBlock,
  DataflowRadarLane,
  DataflowRadarStatus,
  DataflowSnapshot,
} from './types'
import { selectedBlockKey, type SelectedBlock } from './BlockDrawer'
import { bandOf } from './RadarStatusStrip'
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
  laneGapThreshold,
  scanBlockLayout,
  scanStatusLabel,
  segmentPlan,
  windowSpec,
  xForTime,
} from './layout'

export type { SelectedBlock } from './BlockDrawer'

type SwimlaneRow = {
  key: string
  band: 'S' | 'X' | 'other'
  label: string
  caption?: string
  tone?: 'ok' | 'warn' | 'risk'
  blocks: BlockLayout[]
  plansByBlock: SegmentPlan[]
  tooltip: (index: number) => string
  onPress?: (index: number) => void
}

type LaneGroup = {
  key: string
  label: string
  rows: SwimlaneRow[]
  scanCount: number
  collapsedByDefault: boolean
}

// groupLanes mirrors the status strip's band grouping so both halves of the
// screen read the same way; X-band candidates collapse by default because a
// backfilled case floods 24 mostly-silent rows.
function groupLanes(rows: SwimlaneRow[]): LaneGroup[] {
  const groups: Record<string, SwimlaneRow[]> = { S: [], X: [], other: [] }
  for (const row of rows) groups[row.band].push(row)
  return [
    { key: 'S', label: 'S 波段', rows: groups.S },
    { key: 'X', label: 'X 波段候选', rows: groups.X },
    { key: 'other', label: '其它', rows: groups.other },
  ]
    .map(group => ({
      ...group,
      scanCount: group.rows.reduce((sum, row) => sum + row.blocks.length, 0),
      collapsedByDefault: group.key === 'X',
    }))
    .filter(group => group.rows.length > 0)
}

// Swimlanes renders the wall-clock lanes grouped by band, plus the shared
// analysis and forecast rows. Left of the live head is history ("已处理");
// the head itself is "正在处理". A stage focus (from the chain strip) dims
// every other segment so the requested stage pops out across all lanes.
export function Swimlanes({
  snapshot,
  now,
  onSelect,
  selected,
  nowLabel = '现在',
  stageFocus = null,
}: {
  snapshot: DataflowSnapshot
  now: number
  onSelect: (block: SelectedBlock) => void
  selected: SelectedBlock | null
  nowLabel?: string
  stageFocus?: string | null
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

  const radarRows: SwimlaneRow[] = snapshot.radar_lanes.map(lane => {
    const status = statusByRadar.get(lane.radar_id)
    return {
      key: lane.radar_id,
      band: bandOf(lane.radar_id),
      label: status?.display_name || lane.radar_id.toUpperCase(),
      caption: radarCaption(lane, status),
      tone: rowTone(lane, status),
      blocks: lane.blocks.map(block => scanBlockLayout(block, spec, now)),
      plansByBlock: lane.blocks.map(block =>
        segmentPlan(block.stages, spec, now, Date.parse(block.volume_end))),
      tooltip: index => {
        const block = lane.blocks[index]
        return `${status?.display_name || lane.radar_id.toUpperCase()} · 体扫 ${formatClock(block.volume_end)} · ${scanStatusLabel(block.status)}（点击查看证据）`
      },
      onPress: index => onSelect(scanSelection(lane, index, onSelect)),
    }
  })
  const analysisRow: SwimlaneRow = {
    key: 'analysis',
    band: 'other',
    label: '分析周期',
    caption: snapshot.analysis_blocks.length > 0
      ? `拼图 · QPE · ${snapshot.analysis_blocks[snapshot.analysis_blocks.length - 1].radar_count} 雷达`
      : '拼图 · QPE',
    blocks: snapshot.analysis_blocks.map(block => analysisBlockLayout(block, spec, now)),
    plansByBlock: snapshot.analysis_blocks.map(block =>
      segmentPlan(block.stages, spec, now, Date.parse(block.analysis_time))),
    tooltip: index => {
      const block = snapshot.analysis_blocks[index]
      return `分析周期 ${formatClock(block.analysis_time)} · ${block.status}（点击查看证据）`
    },
    onPress: index => onSelect({
      kind: 'analysis', block: snapshot.analysis_blocks[index],
      position: {
        index, total: snapshot.analysis_blocks.length,
        move: delta => {
          const next = Math.min(snapshot.analysis_blocks.length - 1, Math.max(0, index + delta))
          if (next !== index) onSelect(analysisSelection(next))
        },
      },
    }),
  }
  function analysisSelection(index: number): SelectedBlock {
    return {
      kind: 'analysis', block: snapshot.analysis_blocks[index],
      position: {
        index, total: snapshot.analysis_blocks.length,
        move: delta => {
          const next = Math.min(snapshot.analysis_blocks.length - 1, Math.max(0, index + delta))
          if (next !== index) onSelect(analysisSelection(next))
        },
      },
    }
  }
  const forecastRow: SwimlaneRow = {
    key: 'forecast',
    band: 'other',
    label: '预报与发布',
    caption: snapshot.forecast_blocks.length > 0
      ? formatClock(snapshot.forecast_blocks[snapshot.forecast_blocks.length - 1].issue_time)
      : undefined,
    blocks: snapshot.forecast_blocks.map(block => forecastBlockLayout(block, spec, now)),
    plansByBlock: snapshot.forecast_blocks.map(block =>
      segmentPlan(block.stages, spec, now, Date.parse(block.issue_time))),
    tooltip: index => {
      const block = snapshot.forecast_blocks[index]
      return `预报 ${formatClock(block.issue_time)} · ${block.status}（点击查看证据）`
    },
    onPress: index => onSelect(forecastSelection(index)),
  }
  function forecastSelection(index: number): SelectedBlock {
    return {
      kind: 'forecast', block: snapshot.forecast_blocks[index],
      position: {
        index, total: snapshot.forecast_blocks.length,
        move: delta => {
          const next = Math.min(snapshot.forecast_blocks.length - 1, Math.max(0, index + delta))
          if (next !== index) onSelect(forecastSelection(next))
        },
      },
    }
  }

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
      {groupLanes(radarRows).map(group => <LaneGroupSection
        key={group.key}
        group={group}
        selectedKey={selectedKey}
        specStart={spec.start}
        specEnd={spec.end}
        stageFocus={stageFocus}
      />)}
      <LaneRow row={analysisRow} selectedKey={selectedKey} specStart={spec.start} specEnd={spec.end} stageFocus={stageFocus}/>
      <LaneRow row={forecastRow} selectedKey={selectedKey} specStart={spec.start} specEnd={spec.end} stageFocus={stageFocus}/>
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

function LaneGroupSection({ group, selectedKey, specStart, specEnd, stageFocus }: {
  group: LaneGroup
  selectedKey: string | null
  specStart: number
  specEnd: number
  stageFocus: string | null
}) {
  const [open, setOpen] = useState(!group.collapsedByDefault)
  const visible = open || group.rows.some(row =>
    row.blocks.some(block => block.key === selectedKey))
  return <div className="df-lane-group-block">
    <button
      type="button"
      className={open ? 'df-lane-group-head' : 'df-lane-group-head df-lane-group-closed'}
      onClick={() => setOpen(value => !value)}
      aria-expanded={open}
    >
      <span className="df-radar-group-arrow" aria-hidden="true"/>
      <b>{group.label}</b>
      <small>{group.rows.length} 部 · 窗口内 {group.scanCount} 个体扫</small>
    </button>
    {visible && group.rows.map(row => <LaneRow
      key={row.key}
      row={row}
      selectedKey={selectedKey}
      specStart={specStart}
      specEnd={specEnd}
      stageFocus={stageFocus}
    />)}
  </div>
}

function LaneRow({
  row,
  selectedKey,
  specStart,
  specEnd,
  stageFocus,
}: {
  row: SwimlaneRow
  selectedKey: string | null
  specStart: number
  specEnd: number
  stageFocus: string | null
}) {
  const starts = row.blocks.map(block => block.startAt)
  const gaps = gapRanges(starts, specStart, specEnd, laneGapThreshold(starts))
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
          title={row.tooltip(index)}
          onClick={() => row.onPress?.(index)}
          key={block.key}
        >
          {plan.mode === 'timed'
            ? plan.segments.map((segment, segmentIndex) => <span
                className={segmentClass(segment, stageFocus)}
                style={{ left: segment.left - block.left, width: segment.width }}
                data-stage={segment.stage}
                key={`${segment.stage}-${segmentIndex}`}
              />)
            : plan.segments.map((segment, segmentIndex) => <span
                className={segmentClass(segment, stageFocus)}
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

// segmentClass appends the dimmed modifier for stages outside the active
// spotlight; the strip group mapping stays in one place.
const STRIP_GROUP_OF_STAGE: Record<string, string> = {
  decode: 'decode', qc: 'qc', grid: 'grid',
  mosaic: 'mosaic_qpe', qpe: 'mosaic_qpe', diagnostics: 'mosaic_qpe',
  nowcast_input: 'nowcast', pysteps_lk: 'nowcast',
  pysteps_steps: 'nowcast', nowcastnet: 'nowcast',
  products: 'products', verification: 'products',
}

function segmentClass(segment: { stage: string; state: string }, focus: string | null): string {
  const base = `df-seg df-seg-${segment.state}`
  if (!focus || focus === 'ingest') return base
  return STRIP_GROUP_OF_STAGE[segment.stage] === focus
    ? `${base} df-seg-focus`
    : `${base} df-seg-dim`
}

function scanSelection(
  lane: DataflowRadarLane,
  index: number,
  onSelect: (block: SelectedBlock) => void,
): SelectedBlock {
  return {
    kind: 'scan', radarID: lane.radar_id, block: lane.blocks[index],
    position: {
      index, total: lane.blocks.length,
      move: delta => {
        const next = Math.min(lane.blocks.length - 1, Math.max(0, index + delta))
        if (next !== index) onSelect(scanSelection(lane, next, onSelect))
      },
    },
  }
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
