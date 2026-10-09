import type { DataflowStageTrend, DataflowStageTrendSeries } from './types'
import { formatDuration } from './layout'

// StageTrend renders one mini chart per chain stage: median runtime over the
// lookback, as a hand-rolled SVG sparkline (project convention: no chart
// libraries). Null buckets break the line instead of pretending to be zero.
function sparklinePath(values: (number | null)[], width: number, height: number): string {
  const max = Math.max(...values.filter((value): value is number => value != null), 1)
  const step = values.length > 1 ? width / (values.length - 1) : width
  const segments: string[] = []
  let open = false
  values.forEach((value, index) => {
    if (value == null) {
      open = false
      return
    }
    const x = index * step
    const y = height - 2 - (value / max) * (height - 6)
    segments.push(`${open ? 'L' : 'M'}${x.toFixed(1)},${y.toFixed(1)}`)
    open = true
  })
  return segments.join(' ')
}

function lastValue(values: (number | null)[]): number | null {
  for (let index = values.length - 1; index >= 0; index -= 1) {
    if (values[index] != null) return values[index]
  }
  return null
}

export function StageTrend({ trend }: { trend: DataflowStageTrend | null }) {
  if (!trend || !Array.isArray(trend.series) || trend.series.length === 0) return null
  return <div className="df-trend" role="group" aria-label="阶段耗时趋势">
    <small className="df-trend-caption">近 {trend.hours} 小时 p50 · 每 {trend.bucket_minutes} 分钟一桶 · <span className="df-trend-fail-legend"/> 失败数</small>
    <div className="df-trend-row">
      {trend.series.map((series: DataflowStageTrendSeries) => {
        const last = lastValue(series.values)
        const failures = series.failures ?? []
        const maxFail = Math.max(...failures.map(value => value ?? 0), 1)
        return <div className="df-trend-cell" key={series.key}>
          <svg
            className="df-trend-svg"
            width={120}
            height={30}
            viewBox="0 0 120 30"
            preserveAspectRatio="none"
            aria-hidden="true"
          >
            <path className="df-trend-line" d={sparklinePath(series.values, 120, 24)}/>
            {failures.map((count, index) => count == null || count <= 0 ? null : <circle
              className="df-trend-fail"
              cx={valuesX(index, failures.length, 120)}
              cy={27}
              r={1.5 + (count / maxFail) * 2.5}
              key={index}
            />)}
          </svg>
          <b>{series.label}</b>
          <small>{last != null ? `p50 ${formatDuration(last)}` : '无样本'}</small>
        </div>
      })}
    </div>
  </div>
}

function valuesX(index: number, length: number, width: number): number {
  const step = length > 1 ? width / (length - 1) : width
  return index * step
}
