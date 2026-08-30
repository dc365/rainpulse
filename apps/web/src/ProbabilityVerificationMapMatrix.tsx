import { useCallback, useMemo, useState } from 'react'

import { getCenter } from 'ol/extent.js'
import View from 'ol/View.js'

import type { components } from './api/generated/schema'
import { RasterGISMap, type GISMapExtent } from './RasterGISMap'

type MapFrame = components['schemas']['AlgorithmVerificationProbabilityMapFrame']
type MapLayer = components['schemas']['AlgorithmVerificationProbabilityMapLayer']
type PanelID = 'truth' | 'nowcastnet' | 'steps'

interface ProbabilityVerificationMapMatrixProps {
  frame: MapFrame | null
  loading: boolean
  error: string | null
  mapsAvailable: boolean
}

export function ProbabilityVerificationMapMatrix({
  frame,
  loading,
  error,
  mapsAvailable,
}: ProbabilityVerificationMapMatrixProps) {
  const [activePanel, setActivePanel] = useState<PanelID>('truth')
  const [basemapVisible, setBasemapVisible] = useState(false)
  const [smoothRaster, setSmoothRaster] = useState(false)
  const [rasterOpacity, setRasterOpacity] = useState(.88)
  const frameKey = `${frame?.case_id ?? ''}/${frame?.issue_time ?? ''}/${frame?.lead_minutes ?? ''}/${frame?.threshold_mm_h ?? ''}`
  const [layerErrorState, setLayerErrorState] = useState<{
    frameKey: string
    errors: Record<PanelID, boolean>
  }>({ frameKey: '', errors: { truth: false, nowcastnet: false, steps: false } })
  const updateLayerError = useCallback((panelID: PanelID, failed: boolean) => {
    setLayerErrorState((current) => {
      const errors = current.frameKey === frameKey
        ? current.errors
        : { truth: false, nowcastnet: false, steps: false }
      return errors[panelID] === failed && current.frameKey === frameKey
        ? current
        : { frameKey, errors: { ...errors, [panelID]: failed } }
    })
  }, [frameKey])
  const errorHandlers = useMemo<Record<PanelID, (failed: boolean) => void>>(() => ({
    truth: (failed) => updateLayerError('truth', failed),
    nowcastnet: (failed) => updateLayerError('nowcastnet', failed),
    steps: (failed) => updateLayerError('steps', failed),
  }), [updateLayerError])
  const layerErrors = layerErrorState.frameKey === frameKey
    ? layerErrorState.errors
    : { truth: false, nowcastnet: false, steps: false }

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

  const panels: readonly { id: PanelID; role: string; title: string; layer?: MapLayer }[] = [
    { id: 'truth', role: '0 / 100%', title: 'MRMS 超阈实况', layer: frame?.layers.find((layer) => layer.role === 'truth') },
    { id: 'nowcastnet', role: '离线候选 · 原始频率', title: 'NowcastNet 超阈概率', layer: frame?.layers.find((layer) => layer.model === 'nowcastnet') },
    { id: 'steps', role: '主概率基线 · 原始频率', title: 'STEPS 超阈概率', layer: frame?.layers.find((layer) => layer.model === 'steps') },
  ]
  const legend = frame?.legend.map((item) => ({
    label: String(item.minimum_probability_percent), color: item.color,
  })) ?? []

  return (
    <section className="verification-map-evidence verification-map-evidence-rp017 probability-verification-map" aria-labelledby="probability-map-title">
      <header className="verification-map-heading verification-map-heading-rp017">
        <div>
          <span>空间概率证据 · 同阈值同有效域</span>
          <h2 id="probability-map-title">超阈值概率空间对比</h2>
          <small>{frame ? `${formatUTC(frame.issue_time)} 起报 · +${frame.lead_minutes} 分钟 · ≥ ${frame.threshold_mm_h} mm/h` : '实况超阈指示与两套原始集合相对频率共用经纬度范围'}</small>
        </div>
        <div className="verification-map-tools verification-map-tools-rp017" role="group" aria-label="概率验证地图控制">
          <button type="button" className={basemapVisible ? 'active' : ''} aria-pressed={basemapVisible} onClick={() => setBasemapVisible((value) => !value)}>参考底图</button>
          <button type="button" className={!smoothRaster ? 'active' : ''} aria-pressed={!smoothRaster} onClick={() => setSmoothRaster((value) => !value)}>{smoothRaster ? '平滑显示' : '格点显示'}</button>
          <label><span>概率层 {Math.round(rasterOpacity * 100)}%</span><input aria-label="概率验证图层透明度" type="range" min="0.55" max="1" step="0.05" value={rasterOpacity} onChange={(event) => setRasterOpacity(Number(event.target.value))} /></label>
        </div>
      </header>

      <div className="verification-map-mobile-tabs" role="tablist" aria-label="移动端概率地图图层">
        {panels.map((panel) => <button type="button" role="tab" aria-selected={activePanel === panel.id} className={activePanel === panel.id ? 'active' : ''} key={panel.id} onClick={() => setActivePanel(panel.id)}>{panel.title}</button>)}
      </div>

      {!mapsAvailable ? (
        <div className="verification-map-empty"><strong>该运行没有超阈概率图层</strong><span>概率评分仍可查看；需用冻结集合成员重新回算后才能展示空间概率。</span></div>
      ) : loading || !frame ? (
        <div className="verification-map-empty"><strong>{error ? '概率地图读取失败' : '正在读取超阈概率地图'}</strong><span>{error ?? '只读取当前案例、起报、时效和阈值的三幅同步图层。'}</span></div>
      ) : (
        <div className="verification-map-grid verification-map-grid-rp017">
          {panels.map((panel) => (
            <article className={`verification-map-panel ${panel.id} ${activePanel === panel.id ? 'mobile-active' : ''}`} key={panel.id}>
              <header>
                <div><span>{panel.role}</span><strong>{panel.title}</strong></div>
                <LayerCoverage layer={panel.layer} />
              </header>
              <RasterGISMap
                className="verification-comparison-map verification-comparison-map-rp017"
                imageUrl={panel.layer?.image_url}
                imageDescription={`${panel.title} +${frame.lead_minutes} 分钟 ≥ ${frame.threshold_mm_h} mm/h`}
                imageExtent={imageExtent}
                fitExtent={fitExtent}
                validTimeLabel={formatUTC(frame.valid_time)}
                contextLabel={`+${frame.lead_minutes} min`}
                productLabel={`≥ ${frame.threshold_mm_h} mm/h`}
                legend={legend}
                legendUnit="%"
                footerNote="同步概率验证视野"
                mapLabel={`${panel.title}验证地图，共享视野，EPSG:4326`}
                resetViewLabel="复位概率验证案例范围"
                loading={loading}
                layerError={layerErrors[panel.id]}
                onLayerError={errorHandlers[panel.id]}
                sharedView={sharedView}
                comparisonMode
                basemapVisible={basemapVisible}
                smoothRaster={smoothRaster}
                rasterOpacity={rasterOpacity}
                motionVectors={[]}
                motionVisible={false}
              />
            </article>
          ))}
        </div>
      )}

      {frame ? (
        <footer className="verification-map-legend verification-map-legend-rp017 probability-map-legend">
          <div><span className="no-rain" style={{ backgroundColor: frame.valid_no_event_color }} /><small>0% · 有效未超阈</small></div>
          {frame.legend.map((item) => <div key={item.minimum_probability_percent}><span style={{ backgroundColor: item.color }} /><small>≥ {item.minimum_probability_percent}%</small></div>)}
          <div><span className="missing" /><small>透明：缺测 / 成员非共同有效</small></div>
          <em>{frame.palette_version} · EPSG:4326 · {frame.width}×{frame.height}</em>
        </footer>
      ) : null}
      <p className="verification-motion-note probability-map-note">概率为冻结集合成员超过所选雨强阈值的原始相对频率，未经校准、不可发布；实况图仅表示 0% / 100% 是否超阈。</p>
    </section>
  )
}

function LayerCoverage({ layer }: { layer?: MapLayer }) {
  const total = layer ? layer.valid_cell_count + layer.missing_cell_count : 0
  const coverage = layer && total > 0 ? layer.valid_cell_count / total : null
  return <div className="verification-map-metric"><span>覆盖 <strong>{coverage == null ? '—' : `${(coverage * 100).toFixed(1)}%`}</strong></span><span>非零 <strong>{layer?.event_cell_count.toLocaleString('zh-CN') ?? '—'}</strong></span></div>
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
