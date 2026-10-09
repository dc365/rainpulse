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
  LANE_LABEL_WIDTH,
  type BlockLayout,
  type SegmentPlan,
  type TimeView,
  type WindowSpec,
  analysisBlockLayout,
  axisTicks,
  fitPxPerMin,
  forecastBlockLayout,
  formatClock,
  gapRanges,
  laneGapThreshold,
  panView,
  scanBlockLayout,
  scanStatusLabel,
  segmentPlan,
  viewMinutes,
  xForTime,
  zoomView,
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
  onPress: (index: number) => void
}

type LaneGroup = {
  key: string
  label: string
  rows: SwimlaneRow[]
  scanCount: number
  collapsedByDefault: boolean
}

// blockIsFailed drives the 只看失败 filter: any failed stage or a failed lane
// status counts; degraded stays visible in the normal view only.
export function blockIsFailed(block: {
  status: string
  stages: { status: string }[]
}): boolean {
  return block.status === 'FAILED' || block.stages.some(stage => stage.status === 'FAILED')
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

// Swimlanes renders the wall-clock lanes grouped by band plus the shared
// analysis and forecast rows. Wheel zooms and drag pans inside the fetched
// window (double-click resets); a stage focus spotlights one stage; failedOnly
// keeps failed blocks only; flashKey scrolls one block into view.
export function Swimlanes({
  snapshot,
  now,
  onSelect,
  selected,
  nowLabel = '现在',
  stageFocus = null,
  failedOnly = false,
  flashKey = null,
  viewResetKey = '',
}: {
  snapshot: DataflowSnapshot
  now: number
  onSelect: (block: SelectedBlock) => void
  selected: SelectedBlock | null
  nowLabel?: string
  stageFocus?: string | null
  failedOnly?: boolean
  flashKey?: string | null
  viewResetKey?: string
}) {
  const lanesRef = useRef<HTMLDivElement>(null)
  const [availableWidth, setAvailableWidth] = useState(0)
  const [view, setView] = useState<TimeView | null>(null)
  const [panning, setPanning] = useState(false)
  const drag = useRef({ startX: 0, startView: null as TimeView | null, moved: false })

  useEffect(() => {
    const element = lanesRef.current
    if (!element || typeof ResizeObserver === 'undefined') return
    const observer = new ResizeObserver(entries => {
      for (const entry of entries) setAvailableWidth(entry.contentRect.width)
    })
    observer.observe(element)
    return () => observer.disconnect()
  }, [])

  // A new window or anchor invalidates any zoom/pan the operator had.
  useEffect(() => { setView(null) }, [viewResetKey])

  const bounds: TimeView = { start: Date.parse(snapshot.window_start), end: now }
  const active: TimeView = view ?? bounds

  // Wheel zoom needs a non-passive native listener; React registers wheel
  // passively and would swallow preventDefault.
  useEffect(() => {
    const element = lanesRef.current
    if (!element) return
    const onWheel = (event: WheelEvent) => {
      event.preventDefault()
      setView(current => {
        const base = current ?? bounds
        const rect = element.getBoundingClientRect()
        const pxPerMin = fitPxPerMin(availableWidth, LANE_LABEL_WIDTH, viewMinutes(base))
        const focus = base.start + ((event.clientX - rect.left - LANE_LABEL_WIDTH) / pxPerMin) * 60_000
        return zoomView(base, focus, Math.pow(1.0025, event.deltaY), bounds)
      })
    }
    element.addEventListener('wheel', onWheel, { passive: false })
    return () => element.removeEventListener('wheel', onWheel)
  }, [availableWidth, bounds.start, bounds.end])

  const minutes = viewMinutes(active)
  const pxPerMin = availableWidth > 0
    ? fitPxPerMin(availableWidth, LANE_LABEL_WIDTH, minutes)
    : 10
  const spec: WindowSpec = {
    start: active.start, end: active.end, minutes,
    pxPerMin, width: minutes * pxPerMin,
  }
  const ticks = axisTicks(spec)
  const statusByRadar = new Map(snapshot.radar_statuses.map(status => [status.radar_id, status]))
  const selectedKey = selectedBlockKey(selected)
  const zoomed = view != null
    && (view.start > bounds.start + 1000 || view.end < bounds.end - 1000)

  // makeRow keeps the failedOnly filter and the layout mapping aligned by
  // filtering the typed blocks first, then deriving positions from survivors.
  function makeRow<T extends { status: string; stages: { status: string }[] }>(source: {
    key: string
    band: 'S' | 'X' | 'other'
    label: string
    caption?: string
    tone?: 'ok' | 'warn' | 'risk'
    blocks: T[]
    layoutOf: (block: T) => BlockLayout
    planOf: (block: T) => SegmentPlan
    tooltipOf: (block: T) => string
    onPressOf: (block: T) => void
  }): SwimlaneRow | null {
    const kept = source.blocks.filter(block => !failedOnly || blockIsFailed(block))
    if (failedOnly && kept.length === 0) return null
    return {
      key: source.key,
      band: source.band,
      label: source.label,
      caption: source.caption,
      tone: source.tone,
      blocks: kept.map(block => source.layoutOf(block)),
      plansByBlock: kept.map(block => source.planOf(block)),
      tooltip: index => source.tooltipOf(kept[index]),
      onPress: index => source.onPressOf(kept[index]),
    }
  }

  const radarRows = snapshot.radar_lanes.map(lane => {
    const status = statusByRadar.get(lane.radar_id)
    return makeRow({
      key: lane.radar_id,
      band: bandOf(lane.radar_id),
      label: status?.display_name || lane.radar_id.toUpperCase(),
      caption: radarCaption(lane, status),
      tone: rowTone(lane, status),
      blocks: lane.blocks,
      layoutOf: block => scanBlockLayout(block, spec, now),
      planOf: block => segmentPlan(block.stages, spec, now, Date.parse(block.volume_end)),
      tooltipOf: block => `${status?.display_name || lane.radar_id.toUpperCase()} · 体扫 ${formatClock(block.volume_end)} · ${scanStatusLabel(block.status)}（点击查看证据）`,
      onPressOf: block => onSelect(scanSelection(lane, block, onSelect)),
    })
  })
  const analysisRow = makeRow({
    key: 'analysis',
    band: 'other',
    label: '分析周期',
    caption: snapshot.analysis_blocks.length > 0
      ? `拼图 · QPE · ${snapshot.analysis_blocks[snapshot.analysis_blocks.length - 1].radar_count} 雷达`
      : '拼图 · QPE',
    blocks: snapshot.analysis_blocks,
    layoutOf: block => analysisBlockLayout(block, spec, now),
    planOf: block => segmentPlan(block.stages, spec, now, Date.parse(block.analysis_time)),
    tooltipOf: block => `分析周期 ${formatClock(block.analysis_time)} · ${block.status}（点击查看证据）`,
    onPressOf: block => onSelect(analysisSelection(snapshot, block, onSelect)),
  })
  const forecastRow = makeRow({
    key: 'forecast',
    band: 'other',
    label: '预报与发布',
    caption: snapshot.forecast_blocks.length > 0
      ? formatClock(snapshot.forecast_blocks[snapshot.forecast_blocks.length - 1].issue_time)
      : undefined,
    blocks: snapshot.forecast_blocks,
    layoutOf: block => forecastBlockLayout(block, spec, now),
    planOf: block => segmentPlan(block.stages, spec, now, Date.parse(block.issue_time)),
    tooltipOf: block => `预报 ${formatClock(block.issue_time)} · ${block.status}（点击查看证据）`,
    onPressOf: block => onSelect(forecastSelection(snapshot, block, onSelect)),
  })

  useEffect(() => {
    if (!flashKey) return
    const element = lanesRef.current?.querySelector(`[data-block-key="${CSS.escape(flashKey)}"]`)
    element?.scrollIntoView({ behavior: 'smooth', inline: 'center', block: 'nearest' })
  }, [flashKey])

  const nowX = xForTime(now, spec)
  return <div
    className={panning ? 'df-lanes df-lanes-panning' : 'df-lanes'}
    data-window={snapshot.window_minutes}
    ref={lanesRef}
    onPointerDown={event => {
      if (event.button !== 0) return
      drag.current = { startX: event.clientX, startView: active, moved: false }
    }}
    onPointerMove={event => {
      const state = drag.current
      if (!state.startView || event.buttons !== 1) return
      const dx = event.clientX - state.startX
      if (!state.moved && Math.abs(dx) < 5) return
      state.moved = true
      setPanning(true)
      setView(panView(state.startView, -(dx / pxPerMin) * 60_000, bounds))
    }}
    onPointerUp={() => {
      drag.current = { startX: 0, startView: null, moved: false }
      setPanning(false)
    }}
    onPointerLeave={() => {
      drag.current = { startX: 0, startView: null, moved: false }
      setPanning(false)
    }}
    onClickCapture={event => {
      if (drag.current.moved) {
        event.preventDefault()
        event.stopPropagation()
        drag.current.moved = false
      }
    }}
    onDoubleClick={() => setView(null)}
    title="滚轮缩放 · 拖拽平移 · 双击复位"
  >
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
      {groupLanes(radarRows.filter((row): row is SwimlaneRow => row != null)).map(group => <LaneGroupSection
        key={group.key}
        group={group}
        selectedKey={selectedKey}
        specStart={spec.start}
        specEnd={spec.end}
        stageFocus={stageFocus}
        flashKey={flashKey}
      />)}
      {analysisRow && <LaneRow
        row={analysisRow}
        selectedKey={selectedKey}
        specStart={spec.start}
        specEnd={spec.end}
        stageFocus={stageFocus}
        flashKey={flashKey}
      />}
      {forecastRow && <LaneRow
        row={forecastRow}
        selectedKey={selectedKey}
        specStart={spec.start}
        specEnd={spec.end}
        stageFocus={stageFocus}
        flashKey={flashKey}
      />}
      <div className="df-lane-grid" aria-hidden="true">
        {ticks.map(tick => tick.major
          ? <span className="df-gridline" style={{ left: LANE_LABEL_WIDTH + tick.left }} key={`grid-${tick.left}`}/>
          : null)}
        <div className="df-nowline" style={{ left: LANE_LABEL_WIDTH + nowX }}>
          <span className="df-nowline-label">{nowLabel}</span>
        </div>
      </div>
    </div>
    {zoomed && <span className="df-zoom-hint">
      {formatClock(active.start)} – {formatClock(active.end)}
      <button type="button" onClick={() => setView(null)}>复位</button>
    </span>}
  </div>
}

function scanSelection(
  lane: DataflowRadarLane,
  block: DataflowSnapshot['radar_lanes'][number]['blocks'][number],
  onSelect: (block: SelectedBlock) => void,
): SelectedBlock {
  const index = lane.blocks.indexOf(block)
  return {
    kind: 'scan', radarID: lane.radar_id, block,
    position: {
      index, total: lane.blocks.length,
      move: delta => {
        const next = Math.min(lane.blocks.length - 1, Math.max(0, index + delta))
        if (next !== index) onSelect(scanSelection(lane, lane.blocks[next], onSelect))
      },
    },
  }
}

function analysisSelection(
  snapshot: DataflowSnapshot,
  block: DataflowAnalysisBlock,
  onSelect: (block: SelectedBlock) => void,
): SelectedBlock {
  const index = snapshot.analysis_blocks.indexOf(block)
  return {
    kind: 'analysis', block,
    position: {
      index, total: snapshot.analysis_blocks.length,
      move: delta => {
        const next = Math.min(snapshot.analysis_blocks.length - 1, Math.max(0, index + delta))
        if (next !== index) onSelect(analysisSelection(snapshot, snapshot.analysis_blocks[next], onSelect))
      },
    },
  }
}

function forecastSelection(
  snapshot: DataflowSnapshot,
  block: DataflowForecastBlock,
  onSelect: (block: SelectedBlock) => void,
): SelectedBlock {
  const index = snapshot.forecast_blocks.indexOf(block)
  return {
    kind: 'forecast', block,
    position: {
      index, total: snapshot.forecast_blocks.length,
      move: delta => {
        const next = Math.min(snapshot.forecast_blocks.length - 1, Math.max(0, index + delta))
        if (next !== index) onSelect(forecastSelection(snapshot, snapshot.forecast_blocks[next], onSelect))
      },
    },
  }
}

function LaneGroupSection({ group, selectedKey, specStart, specEnd, stageFocus, flashKey }: {
  group: LaneGroup
  selectedKey: string | null
  specStart: number
  specEnd: number
  stageFocus: string | null
  flashKey: string | null
}) {
  const [open, setOpen] = useState(!group.collapsedByDefault)
  const visible = open || group.rows.some(row =>
    row.blocks.some(block => block.key === selectedKey || block.key === flashKey))
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
      flashKey={flashKey}
    />)}
  </div>
}

function LaneRow({
  row,
  selectedKey,
  specStart,
  specEnd,
  stageFocus,
  flashKey,
}: {
  row: SwimlaneRow
  selectedKey: string | null
  specStart: number
  specEnd: number
  stageFocus: string | null
  flashKey: string | null
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
        const classes = ['df-block']
        if (active) classes.push('df-block-live')
        if (selectedKey === block.key) classes.push('df-block-selected')
        if (flashKey === block.key) classes.push('df-block-flash')
        return <button
          type="button"
          className={classes.join(' ')}
          style={{ left: block.left, width: block.width }}
          title={row.tooltip(index)}
          onClick={() => row.onPress(index)}
          data-block-key={block.key}
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
  const failed = lane.blocks.some(block => blockIsFailed(block))
  if (failed) return 'warn'
  return 'ok'
}
