import { useCallback, useEffect, useMemo, useState } from 'react'

import { getCenter } from 'ol/extent.js'
import View from 'ol/View.js'

import type { components } from './api/generated/schema'
import {
  RasterGISMap,
  type GISMapExtent,
  type GISMotionVector,
} from './RasterGISMap'

type MapFrame = components['schemas']['AlgorithmVerificationMapFrame']
type MapLayer = components['schemas']['AlgorithmVerificationMapLayer']
type VerificationMetric = components['schemas']['AlgorithmVerificationMetric']

const modelLabels: Record<string, string> = {
  lk: 'pySTEPS-LK',
  persistence: '持续性',
  translation: '基于 LK 的整场平移',
  phase_correlation: '独立相位相关平移',
}

interface VerificationMapMatrixProps {
  frame: MapFrame | null
  baseline: string
  lkMetric?: VerificationMetric
  baselineMetric?: VerificationMetric
  loading: boolean
  error: string | null
  mapsAvailable: boolean
}

type PanelID = 'truth' | 'lk' | 'baseline'

export function VerificationMapMatrix({
  frame,
  baseline,
  lkMetric,
  baselineMetric,
  loading,
  error,
  mapsAvailable,
}: VerificationMapMatrixProps) {
  const [activePanel, setActivePanel] = useState<PanelID>('truth')
  const [basemapVisible, setBasemapVisible] = useState(false)
  const [smoothRaster, setSmoothRaster] = useState(false)
  const [motionVisible, setMotionVisible] = useState(true)
  const [rasterOpacity, setRasterOpacity] = useState(.88)
  const [layerErrors, setLayerErrors] = useState<Record<PanelID, boolean>>({
    truth: false, lk: false, baseline: false,
  })
  const updateLayerError = useCallback((panelID: PanelID, failed: boolean) => {
    setLayerErrors((current) => current[panelID] === failed
      ? current
      : { ...current, [panelID]: failed })
  }, [])
  const layerErrorHandlers = useMemo<Record<PanelID, (failed: boolean) => void>>(() => ({
    truth: (failed) => updateLayerError('truth', failed),
    lk: (failed) => updateLayerError('lk', failed),
    baseline: (failed) => updateLayerError('baseline', failed),
  }), [updateLayerError])

  useEffect(() => {
    setLayerErrors({ truth: false, lk: false, baseline: false })
  }, [frame?.case_id, frame?.issue_time, frame?.lead_minutes, baseline])

  const fitExtent = toExtent(frame?.fit_bounds)
  const imageExtent = toExtent(frame?.pixel_edge_bounds)
  const extentKey = fitExtent.join(',')
  const sharedView = useMemo(() => new View({
    projection: 'EPSG:4326',
    center: getCenter(fitExtent),
    extent: expandExtent(fitExtent),
    constrainOnlyCenter: true,
    smoothExtentConstraint: true,
    minZoom: 4,
    maxZoom: 14,
  // eslint-disable-next-line react-hooks/exhaustive-deps
  }), [extentKey])

  const truthLayer = frame?.layers.find((layer) => layer.role === 'truth')
  const lkLayer = frame?.layers.find((layer) => layer.model === 'lk')
  const baselineLayer = frame?.layers.find((layer) => layer.model === baseline)
  const panels: readonly {
    id: PanelID
    role: string
    title: string
    layer?: MapLayer
    metric?: VerificationMetric
  }[] = [
    { id: 'truth', role: '实况', title: 'MRMS 实况', layer: truthLayer },
    { id: 'lk', role: '候选算法', title: 'pySTEPS-LK', layer: lkLayer, metric: lkMetric },
    { id: 'baseline', role: '比较基线', title: modelLabels[baseline] ?? baseline, layer: baselineLayer, metric: baselineMetric },
  ]
  const vectors = (frame?.motion.vectors ?? []) as GISMotionVector[]
  const delta = lkMetric?.fss != null && baselineMetric?.fss != null
    ? lkMetric.fss - baselineMetric.fss
    : null
  const legend = frame?.legend.map((item) => ({ label: String(item.minimum_mm_h), color: item.color })) ?? []

  return (
    <section className="verification-map-evidence verification-map-evidence-rp017" aria-labelledby="verification-map-title">
      <header className="verification-map-heading verification-map-heading-rp017">
        <div>
          <span>空间对比</span>
          <h2 id="verification-map-title">同一时效空间对比</h2>
          <small>{frame ? `${formatUTC(frame.issue_time)} 起报 · +${frame.lead_minutes} 分钟 · ${formatUTC(frame.valid_time)} 有效` : '实况、候选算法和比较基线共用地理范围与色标'}</small>
        </div>
        <div className="verification-map-tools verification-map-tools-rp017" role="group" aria-label="验证地图控制">
          <button type="button" className={basemapVisible ? 'active' : ''} aria-pressed={basemapVisible} onClick={() => setBasemapVisible((value) => !value)}>参考底图</button>
          <button type="button" className={!smoothRaster ? 'active' : ''} aria-pressed={!smoothRaster} onClick={() => setSmoothRaster((value) => !value)}>{smoothRaster ? '平滑显示' : '格点显示'}</button>
          <button type="button" className={motionVisible ? 'active' : ''} aria-pressed={motionVisible} disabled={!vectors.length} onClick={() => setMotionVisible((value) => !value)}>回波运动</button>
          <label><span>雨层 {Math.round(rasterOpacity * 100)}%</span><input aria-label="验证雨层透明度" type="range" min="0.55" max="1" step="0.05" value={rasterOpacity} onChange={(event) => setRasterOpacity(Number(event.target.value))} /></label>
        </div>
      </header>

      <div className="verification-map-mobile-tabs" role="tablist" aria-label="移动端地图图层">
        {panels.map((panel) => <button type="button" role="tab" aria-selected={activePanel === panel.id} className={activePanel === panel.id ? 'active' : ''} key={panel.id} onClick={() => setActivePanel(panel.id)}>{panel.title}</button>)}
      </div>

      {!mapsAvailable ? (
        <div className="verification-map-empty"><strong>该运行没有空间图层</strong><span>旧验证报告仍可查看数值指标；重新回算后才会产生不可变地图证据。</span></div>
      ) : loading || !frame ? (
        <div className="verification-map-empty"><strong>{error ? '地图证据读取失败' : '正在读取同步地图'}</strong><span>{error ?? '当前只读取所选案例、起报和时效的空间证据。'}</span></div>
      ) : (
        <div className="verification-map-grid verification-map-grid-rp017">
          {panels.map((panel) => (
            <article className={`verification-map-panel ${panel.id} ${activePanel === panel.id ? 'mobile-active' : ''}`} key={panel.id}>
              <header>
                <div><span>{panel.role}</span><strong>{panel.title}</strong></div>
                {panel.id === 'truth'
                  ? <MapCoverage layer={panel.layer} />
                  : <MapMetric metric={panel.metric} delta={panel.id === 'lk' ? delta : null} />}
              </header>
              <RasterGISMap
                className="verification-comparison-map verification-comparison-map-rp017"
                imageUrl={panel.layer?.image_url}
                imageDescription={`${panel.title} +${frame.lead_minutes} 分钟雨强空间证据`}
                imageExtent={imageExtent}
                fitExtent={fitExtent}
                validTimeLabel={formatUTC(frame.valid_time)}
                contextLabel={`+${frame.lead_minutes} min`}
                productLabel="雨强"
                legend={legend}
                legendUnit="mm/h"
                footerNote="同步验证视野"
                mapLabel={`${panel.title}验证地图，共享视野，EPSG:4326`}
                resetViewLabel="复位验证案例范围"
                loading={loading}
                layerError={layerErrors[panel.id]}
                onLayerError={layerErrorHandlers[panel.id]}
                sharedView={sharedView}
                comparisonMode
                basemapVisible={basemapVisible}
                smoothRaster={smoothRaster}
                rasterOpacity={rasterOpacity}
                motionVectors={panel.id === 'lk' ? vectors : []}
                motionVisible={panel.id === 'lk' && motionVisible}
              />
            </article>
          ))}
        </div>
      )}

      {frame ? (
        <footer className="verification-map-legend verification-map-legend-rp017">
          <div><span className="no-rain" style={{ backgroundColor: frame.valid_no_rain_color }} /><small>有效无雨 &lt; {frame.rain_threshold_mm_h} mm/h</small></div>
          {frame.legend.map((item) => <div key={item.minimum_mm_h}><span style={{ backgroundColor: item.color }} /><small>≥ {item.minimum_mm_h}</small></div>)}
          <div><span className="missing" /><small>透明：缺测 / 无覆盖</small></div>
          <em>{frame.palette_version} · EPSG:4326 · {frame.width}×{frame.height}</em>
        </footer>
      ) : null}

      {frame?.motion.fallback_used ? <p className="verification-motion-note caution">该起报使用运动回退：{frame.motion.fallback_reason ?? '未记录原因'}。地图保留预报结果，但不作为有效 LK 运动证据。</p> : null}
      {frame && !frame.motion.fallback_used ? <p className="verification-motion-note">回波运动矢量 {frame.motion.vectors.length} 个 · 特征 {frame.motion.feature_count} 个 · 可追踪雨像素 {frame.motion.trackable_rain_pixel_count.toLocaleString('zh-CN')}；矢量单位为格点 / 5 分钟，不是实测风。</p> : null}
    </section>
  )
}

function MapMetric({ metric, delta }: { metric?: VerificationMetric, delta: number | null }) {
  return <div className="verification-map-metric"><span>FSS <strong>{formatMetric(metric?.fss)}</strong></span><span>CSI <strong>{formatMetric(metric?.csi)}</strong></span>{delta == null ? null : <span className={delta >= 0 ? 'positive' : 'negative'}>Δ <strong>{formatSigned(delta)}</strong></span>}</div>
}

function MapCoverage({ layer }: { layer?: MapLayer }) {
  const total = layer ? layer.valid_cell_count + layer.missing_cell_count : 0
  const coverage = layer && total > 0 ? layer.valid_cell_count / total : null
  return <div className="verification-map-metric"><span>覆盖 <strong>{coverage == null ? '—' : `${(coverage * 100).toFixed(1)}%`}</strong></span><span>有雨 <strong>{layer?.rain_cell_count.toLocaleString('zh-CN') ?? '—'}</strong></span></div>
}

function toExtent(values?: number[]): GISMapExtent {
  return values?.length === 4
    ? [values[0], values[1], values[2], values[3]]
    : [117.995, 24.995, 123.005, 27.005]
}

function expandExtent(extent: GISMapExtent): GISMapExtent {
  const x = (extent[2] - extent[0]) * .12
  const y = (extent[3] - extent[1]) * .12
  return [extent[0] - x, extent[1] - y, extent[2] + x, extent[3] + y]
}

function formatUTC(value: string) {
  return `${new Intl.DateTimeFormat('zh-CN', {
    timeZone: 'UTC', month: '2-digit', day: '2-digit', hour: '2-digit', minute: '2-digit', hour12: false,
  }).format(new Date(value))} UTC`
}

function formatMetric(value?: number | null) {
  return value == null || !Number.isFinite(value) ? '—' : value.toFixed(3)
}

function formatSigned(value: number) {
  return `${value >= 0 ? '+' : ''}${value.toFixed(4)}`
}
