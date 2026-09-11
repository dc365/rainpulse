import { useEffect, useState } from 'react'
import type { MapCoordinate } from '../RasterGISMap'
import type { WorkspaceCycleDetail } from './model'
import { readExactSample } from './sample'
import { comparePoint, nativeFrameAt, verificationTimes } from './verification'
import { SpatialVerification } from './SpatialVerification'

type Comparison = ReturnType<typeof comparePoint> & { observed: number; predicted: number }
export function VerificationInspector({ detail, algorithm, validTime, point, onClear, threshold, onThresholdChange, windowKM, onWindowChange }: {
  detail: WorkspaceCycleDetail; algorithm: string; validTime: string | null
  point: MapCoordinate | null; onClear: () => void
  threshold:number; onThresholdChange:(value:number)=>void; windowKM:number; onWindowChange:(value:number)=>void
}) {
  const [result, setResult] = useState<{ key: string; value?: Comparison; error?: string } | null>(null)
  const truthPanel = detail.panels.find(p => p.panel_id === 'qpe')
  const forecastPanel = detail.panels.find(p => p.panel_id === algorithm)
  const truthFrame = nativeFrameAt(truthPanel, validTime)
  const forecastFrame = nativeFrameAt(forecastPanel, validTime)
  const truthURL = truthFrame?.image_url
  const forecastURL = forecastFrame?.image_url
  const key = JSON.stringify([truthURL, forecastURL, validTime, point, threshold])
  useEffect(() => {
    if (!point || !truthURL || !forecastURL || !validTime || validTime === detail.issue_time) return
    const controller = new AbortController()
    void Promise.all([
      readExactSample(truthURL, point.longitude, point.latitude, controller.signal),
      readExactSample(forecastURL, point.longitude, point.latitude, controller.signal),
    ]).then(([truth, forecast]) => {
      if (controller.signal.aborted) return
      const comparison = comparePoint(truth, forecast, validTime, threshold)
      setResult({ key, value: { ...comparison, observed: truth.value!, predicted: forecast.value! } })
    }).catch((error: unknown) => {
      if (!controller.signal.aborted) setResult({ key, error: error instanceof Error ? error.message : '读取对照数据失败' })
    })
    return () => controller.abort()
  }, [key, point, truthURL, forecastURL, validTime, threshold, detail.issue_time])
  const current = result?.key === key ? result : null
  const matched = verificationTimes(detail, algorithm).length
  const comparable = Boolean(truthFrame && forecastFrame && validTime !== detail.issue_time)
  return <section className="verification-inspector" aria-label="同格点检验对照">
    <div className="verification-context"><strong>原生时效对照</strong><span>{matched} 个实况匹配时效 · 固定起报</span>
      <label>事件：雨强 &gt; <select aria-label="检验雨强阈值" value={threshold} onChange={event => onThresholdChange(Number(event.target.value))}>
        {[1, 5, 10, 20, 50].map(value => <option key={value} value={value}>{value} mm/h</option>)}
      </select></label></div>
    <SpatialVerification cycleID={detail.cycle_id} algorithm={algorithm}
      lead={validTime ? Math.round((Date.parse(validTime)-Date.parse(detail.issue_time))/60000) : 0}
      sourceKey={JSON.stringify([truthURL,forecastURL,truthFrame?.sha256,forecastFrame?.sha256])}
      threshold={threshold} windowKM={windowKM} onWindowChange={onWindowChange} enabled={comparable} />
    {!comparable ? <p role="status">该时效暂无可配对的原生预报与实况；不插值、不使用其他周期替代。</p>
      : !point ? <p>点击地图固定一个格点，读取同位置实况、预报、差值与命中/漏报结果。</p>
      : current?.value ? <div className="verification-values">
        <span>实况 <strong>{current.value.observed.toFixed(2)} mm/h</strong></span>
        <span>预报 <strong>{current.value.predicted.toFixed(2)} mm/h</strong></span>
        <span>预报−实况 <strong>{current.value.error.toFixed(2)} mm/h</strong></span>
        <span>事件判断 <strong>{current.value.event}</strong></span>
        <span>有效配对 <strong>N = 1</strong></span><button type="button" onClick={onClear}>清除选点</button>
      </div> : <p role="status">{current?.error ?? '读取同格点数值…'}</p>}
    <small>雷达 QPE 对照，非独立雨量站真值。单格点结果不代表整场预报技巧；派生帧不计入本检验。</small>
  </section>
}
