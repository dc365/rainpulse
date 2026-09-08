import type { WorkspaceCycleDetail, WorkspacePanel } from './model'
import type { ExactSample } from './sample'

export function nativeFrameAt(panel: WorkspacePanel | undefined, time: string | null) {
  if (!time) return undefined
  return panel?.frames.find(frame => Date.parse(frame.valid_time) === Date.parse(time)
    && (frame.frame_kind === 'native' || (panel?.role === 'observation' && frame.frame_kind === 'analysis')) && !frame.reference_observation)
}

export function verificationTimes(detail: WorkspaceCycleDetail, algorithm = 'lk') {
  const truth = detail.panels.find(p => p.panel_id === 'qpe')
  const forecast = detail.panels.find(p => p.panel_id === algorithm)
  return detail.timeline.filter(time => Date.parse(time) > Date.parse(detail.issue_time)
    && nativeFrameAt(truth, time) && nativeFrameAt(forecast, time))
}

export function comparePoint(truth: ExactSample, forecast: ExactSample, validTime: string, threshold: number) {
  if (!truth.valid || !forecast.valid) throw new Error('实况或预报在该格点缺测，不能评分')
  if (!Number.isFinite(truth.value) || !Number.isFinite(forecast.value)) throw new Error('数值无效，不能评分')
  if (!Number.isFinite(threshold) || threshold < 0) throw new Error('雨强阈值无效')
  const rateUnit = (unit: string) => ['mm/h', 'mm h-1', 'mm h⁻¹'].includes(unit.trim())
  if (!rateUnit(truth.unit) || !rateUnit(forecast.unit)) throw new Error('当前产品不是可比的雨强，不能混用累计量或概率')
  if (Date.parse(truth.valid_time ?? '') !== Date.parse(validTime)
    || Date.parse(forecast.valid_time ?? '') !== Date.parse(validTime)) throw new Error('数值产品有效时间不匹配')
  if (!['native', 'analysis'].includes(truth.frame_kind) || forecast.frame_kind !== 'native') throw new Error('当前检验仅使用原生时效，不计入派生帧')
  if (Math.abs(truth.grid_longitude - forecast.grid_longitude) > 1e-6
    || Math.abs(truth.grid_latitude - forecast.grid_latitude) > 1e-6) throw new Error('实况与预报不在同一格点，不能直接比较')
  const observed = truth.value! > threshold
  const predicted = forecast.value! > threshold
  const error = forecast.value! - truth.value!
  return { error, absoluteError: Math.abs(error), sampleCount: 1,
    event: observed ? predicted ? '命中' : '漏报' : predicted ? '空报' : '正确无事件' }
}
