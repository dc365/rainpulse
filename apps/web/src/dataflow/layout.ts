import type {
  DataflowAnalysisBlock,
  DataflowForecastBlock,
  DataflowJobStage,
  DataflowScanBlock,
} from './types'

// Layout constants of the swimlane. Pixel math lives here so tests can pin it.
export const LANE_LABEL_WIDTH = 132
export const LANE_ROW_HEIGHT = 44
export const BLOCK_HEIGHT = 22
export const PX_PER_MIN = 10
export const HEADROOM_MINUTES = 2
export const GAP_THRESHOLD_MS = 12 * 60_000
export const MIN_SEGMENT_PX = 2

export type WindowSpec = {
  start: number
  end: number
  minutes: number
  pxPerMin: number
  width: number
}

export function windowSpec(windowMinutes: number, now: number, pxPerMin: number = PX_PER_MIN): WindowSpec {
  const start = now - windowMinutes * 60_000
  const end = now + HEADROOM_MINUTES * 60_000
  const minutes = windowMinutes + HEADROOM_MINUTES
  const density = Math.max(4, pxPerMin)
  return { start, end, minutes, pxPerMin: density, width: minutes * density }
}

// fitPxPerMin keeps the wall-clock axis filling the available panel width so
// lanes never leave a dead right margin on wide screens.
export function fitPxPerMin(containerWidth: number, labelWidth: number, minutes: number): number {
  if (containerWidth <= 0) return PX_PER_MIN
  return Math.max(4, (containerWidth - labelWidth - 2) / Math.max(1, minutes))
}

export function xForTime(time: number, spec: WindowSpec): number {
  return ((time - spec.start) / 60_000) * spec.pxPerMin
}

export type StageSegment = {
  stage: string
  label: string
  state: 'queued' | 'running' | 'done' | 'failed' | 'skipped'
  left: number
  width: number
  runtimeMS: number | null
  queueMS: number | null
  startAt: number | null
  endAt: number | null
}

// SlotSegment is the backfill layout: job timestamps live on another wall
// clock than the volume data, so stages are drawn by duration share inside the
// scan's beat slot instead of by absolute time.
export type SlotSegment = {
  stage: string
  label: string
  state: StageSegment['state']
  leftPct: number
  widthPct: number
  runtimeMS: number | null
  queueMS: number | null
}

export type SegmentPlan =
  | { mode: 'timed'; segments: StageSegment[] }
  | { mode: 'slot'; segments: SlotSegment[] }

const STAGE_LABELS: Record<string, string> = {
  decode: '解码',
  qc: '质控',
  grid: '格点化',
  mosaic: '拼图',
  qpe: 'QPE',
  diagnostics: '诊断',
  nowcast_input: '模型输入',
  pysteps_lk: 'pySTEPS-LK',
  pysteps_steps: 'pySTEPS-STEPS',
  nowcastnet: 'NowcastNet',
  products: '应用产品',
  verification: '检验',
}

export function stageLabel(stage: string): string {
  return STAGE_LABELS[stage] ?? stage
}

const SCAN_STATUS_LABELS: Record<string, string> = {
  RAW_RECEIVED: '已到达',
  RAW_VALIDATING: '校验中',
  DECODING: '解码中',
  NORMALIZED: '已解码',
  QC_RUNNING: '质控中',
  QC_READY: '质控完成',
  GRID_RUNNING: '格点化中',
  RADAR_GRID_READY: '链路完成',
  DEGRADED: '降级',
  FAILED: '失败',
  SKIPPED: '跳过',
}

export function scanStatusLabel(status: string): string {
  return SCAN_STATUS_LABELS[status] ?? status
}

export function stageState(stage: DataflowJobStage): StageSegment['state'] {
  switch (stage.status) {
    case 'RUNNING': return 'running'
    case 'SUCCEEDED': return 'done'
    case 'FAILED': return 'failed'
    case 'SKIPPED': return 'skipped'
    default: return 'queued'
  }
}

function stageRuntimeMS(stage: DataflowJobStage): number | null {
  if (stage.runtime_ms != null) return stage.runtime_ms
  if (stage.finished_at && stage.started_at) {
    const value = Date.parse(stage.finished_at) - Date.parse(stage.started_at)
    if (value >= 0) return value
  }
  return null
}

function stageQueueMS(stage: DataflowJobStage): number | null {
  if (!stage.started_at) return null
  const queue = Date.parse(stage.started_at) - Date.parse(stage.created_at)
  return queue >= 0 ? queue : null
}

// stageTimed decides whether job timestamps belong to the block's data time
// (live processing) or another wall clock (backfilled case). Backfilled stages
// keep their states, durations and errors but lose absolute positioning.
export function stageTimed(stages: DataflowJobStage[], dataTime: number): boolean {
  let earliest = Number.POSITIVE_INFINITY
  let latest = 0
  let seen = false
  for (const stage of stages) {
    for (const raw of [stage.started_at, stage.finished_at]) {
      if (!raw) continue
      const value = Date.parse(raw)
      if (Number.isNaN(value)) continue
      seen = true
      earliest = Math.min(earliest, value)
      latest = Math.max(latest, value)
    }
  }
  const tolerance = 30 * 60_000
  return seen
    && latest - dataTime < tolerance
    && dataTime - earliest < tolerance
}

// stageSegments positions every job of a live-timed block on the wall-clock
// axis. A queued segment pins to the previous activity boundary so it stays
// visible instead of collapsing onto one point.
export function stageSegments(stages: DataflowJobStage[], spec: WindowSpec, now: number): StageSegment[] {
  const segments: StageSegment[] = []
  let cursor: number | null = null
  for (const stage of stages) {
    const startAt = stage.started_at ? Date.parse(stage.started_at) : null
    const endAt = stage.finished_at ? Date.parse(stage.finished_at) : null
    const effectiveStart: number = startAt
      ?? cursor
      ?? Math.min(now, Math.max(spec.start, Date.parse(stage.created_at)))
    const effectiveEnd: number = endAt ?? (startAt != null ? now : effectiveStart)
    const left = xForTime(effectiveStart, spec)
    const width = Math.max(MIN_SEGMENT_PX, xForTime(effectiveEnd, spec) - left)
    segments.push({
      stage: stage.stage,
      label: stageLabel(stage.stage),
      state: stageState(stage),
      left,
      width,
      runtimeMS: stageRuntimeMS(stage),
      queueMS: stageQueueMS(stage),
      startAt,
      endAt,
    })
    if (effectiveEnd > (cursor ?? 0)) cursor = effectiveEnd
  }
  return segments
}

// slotSegments lays stages by duration share: runtime first, queue time added
// when known, retries of the same stage keep their own shares. The reprocessing
// effort of a backfilled case stays visible instead of collapsing.
export function slotSegments(stages: DataflowJobStage[]): SlotSegment[] {
  const weights = stages.map(stage => {
    const runtime = stageRuntimeMS(stage) ?? 0
    const queue = stageQueueMS(stage) ?? 0
    return Math.max(2_000, runtime + queue)
  })
  const total = weights.reduce((sum, value) => sum + value, 0) || 1
  let cursorPct = 0
  return stages.map((stage, index) => {
    const widthPct = (weights[index] / total) * 100
    const segment: SlotSegment = {
      stage: stage.stage,
      label: stageLabel(stage.stage),
      state: stageState(stage),
      leftPct: cursorPct,
      widthPct,
      runtimeMS: stageRuntimeMS(stage),
      queueMS: stageQueueMS(stage),
    }
    cursorPct += widthPct
    return segment
  })
}

export function segmentPlan(stages: DataflowJobStage[], spec: WindowSpec, now: number, dataTime: number): SegmentPlan {
  if (stageTimed(stages, dataTime)) {
    return { mode: 'timed', segments: stageSegments(stages, spec, now) }
  }
  return { mode: 'slot', segments: slotSegments(stages) }
}

export type BlockLayout = {
  key: string
  left: number
  width: number
  kind: 'scan' | 'analysis' | 'forecast'
  startAt: number
  end: number
  mode: 'timed' | 'slot'
}

// Every lane block anchors at the entity's own data time so a backfilled case
// replays on its meteorological clock. Live blocks extend to their last job
// activity (or now); backfilled blocks occupy one six-minute beat slot.
export function scanBlockLayout(block: DataflowScanBlock, spec: WindowSpec, now: number): BlockLayout {
  const dataTime = Date.parse(block.volume_end)
  return blockLayout(block.scan_id, dataTime, block.stages, spec, now, 'scan')
}

export function analysisBlockLayout(block: DataflowAnalysisBlock, spec: WindowSpec, now: number): BlockLayout {
  return blockLayout(block.analysis_id, Date.parse(block.analysis_time), block.stages, spec, now, 'analysis')
}

export function forecastBlockLayout(block: DataflowForecastBlock, spec: WindowSpec, now: number): BlockLayout {
  return blockLayout(block.run_id, Date.parse(block.issue_time), block.stages, spec, now, 'forecast')
}

function blockLayout(
  key: string,
  dataTime: number,
  stages: DataflowJobStage[],
  spec: WindowSpec,
  now: number,
  kind: BlockLayout['kind'],
): BlockLayout {
  const left = xForTime(dataTime, spec)
  if (stageTimed(stages, dataTime)) {
    let end = dataTime
    for (const stage of stages) {
      if (stage.finished_at) end = Math.max(end, Date.parse(stage.finished_at))
      else if (stage.started_at) end = Math.max(end, now)
    }
    const width = Math.max(MIN_SEGMENT_PX * 3, xForTime(Math.max(end, dataTime), spec) - left)
    return { key, left, width, kind, startAt: dataTime, end: Math.max(end, dataTime), mode: 'timed' }
  }
  const slotEnd = Math.min(spec.end, dataTime + 6 * 60_000)
  const width = Math.max(MIN_SEGMENT_PX * 3, xForTime(slotEnd, spec) - left)
  return { key, left, width, kind, startAt: dataTime, end: slotEnd, mode: 'slot' }
}

export type GapRange = { left: number; width: number; minutes: number }

// gapRanges marks wall-clock stretches without any volume scan for one radar.
// The window before the first block counts as silence only when it is long.
export function gapRanges(starts: number[], windowStart: number, windowEnd: number): GapRange[] {
  const sorted = [...starts].sort((a, b) => a - b)
  const gaps: GapRange[] = []
  let previous = windowStart
  for (const value of sorted) {
    pushGap(gaps, previous, value, windowStart)
    previous = Math.max(previous, value)
  }
  pushGap(gaps, previous, windowEnd, windowStart)
  return gaps
}

function pushGap(gaps: GapRange[], from: number, to: number, windowStart: number) {
  if (to - from < GAP_THRESHOLD_MS) return
  const scale = PX_PER_MIN / 60_000
  gaps.push({
    left: (from - windowStart) * scale,
    width: (to - from) * scale,
    minutes: Math.round((to - from) / 60_000),
  })
}

export type AxisTick = { left: number; label: string; major: boolean }

// axisTicks aligns to the fixed six-minute volume-scan beat (UTC wall clock),
// labelling in CST so operators read local time while UTC stays the truth.
export function axisTicks(spec: WindowSpec): AxisTick[] {
  const step = 6 * 60_000
  const first = Math.ceil(spec.start / step) * step
  const ticks: AxisTick[] = []
  for (let time = first; time <= spec.end; time += step) {
    ticks.push({
      left: xForTime(time, spec),
      label: formatClock(time),
      major: (time / step) % 10 === 0,
    })
  }
  return ticks
}

export function formatClock(time: number | string): string {
  return new Intl.DateTimeFormat('zh-CN', {
    timeZone: 'Asia/Taipei', hour: '2-digit', minute: '2-digit', hour12: false,
  }).format(typeof time === 'number' ? new Date(time) : new Date(time))
}

export function formatClockSeconds(time: number | string): string {
  return new Intl.DateTimeFormat('zh-CN', {
    timeZone: 'Asia/Taipei', hour: '2-digit', minute: '2-digit', second: '2-digit', hour12: false,
  }).format(typeof time === 'number' ? new Date(time) : new Date(time))
}

export function formatDuration(ms: number | null | undefined): string {
  if (ms == null) return '—'
  if (ms < 1000) return `${ms} ms`
  const seconds = ms / 1000
  if (seconds < 90) return `${seconds.toFixed(seconds < 10 ? 1 : 0)} s`
  return `${(seconds / 60).toFixed(1)} min`
}

export function formatDelay(seconds: number | null | undefined): string {
  if (seconds == null) return '—'
  if (seconds < 90) return `${Math.round(seconds)} s`
  return `${(seconds / 60).toFixed(1)} min`
}

export function formatRatio(value: number | null | undefined): string {
  if (value == null) return '—'
  return `${(value * 100).toFixed(value >= 0.995 ? 0 : 1)}%`
}

export function delayTone(seconds: number | null | undefined): 'ok' | 'warn' | 'risk' | 'unknown' {
  if (seconds == null) return 'unknown'
  if (seconds > 720) return 'risk'
  if (seconds > 420) return 'warn'
  return 'ok'
}
