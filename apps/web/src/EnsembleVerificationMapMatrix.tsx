import { useCallback, useMemo, useState } from 'react'

import { getCenter } from 'ol/extent.js'
import View from 'ol/View.js'

import type { components } from './api/generated/schema'
import { RasterGISMap, type GISMapExtent } from './RasterGISMap'

type MapFrame = components['schemas']['AlgorithmVerificationMapFrame']
type MapLayer = components['schemas']['AlgorithmVerificationMapLayer']
type PanelID = 'truth' | 'nowcastnet' | 'steps'

interface EnsembleVerificationMapMatrixProps {
  frame: MapFrame | null
  loading: boolean
  error: string | null
  mapsAvailable: boolean
}

export function EnsembleVerificationMapMatrix({
  frame,
  loading,
  error,
  mapsAvailable,
}: EnsembleVerificationMapMatrixProps) {
  const [activePanel, setActivePanel] = useState<PanelID>('truth')
  const [basemapVisible, setBasemapVisible] = useState(false)
  const [smoothRaster, setSmoothRaster] = useState(false)
  const [rasterOpacity, setRasterOpacity] = useState(.88)
  const frameKey = `${frame?.case_id ?? ''}/${frame?.issue_time ?? ''}/${frame?.lead_minutes ?? ''}`
  const [layerErrorState, setLayerErrorState] = useState<{
    frameKey: string
    errors: Record<PanelID, boolean>
  }>({
    frameKey: '', errors: { truth: false, nowcastnet: false, steps: false },
  })
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
    { id: 'truth', role: '实况', title: 'MRMS 实况', layer: frame?.layers.find((layer) => layer.role === 'truth') },
    { id: 'nowcastnet', role: '离线候选', title: 'NowcastNet 集合均值', layer: frame?.layers.find((layer) => layer.model === 'nowcastnet') },
    { id: 'steps', role: '主概率基线', title: 'STEPS 集合均值', layer: frame?.layers.find((layer) => layer.model === 'steps') },
  ]
  const legend = frame?.legend.map((item) => ({
    label: String(item.minimum_mm_h), color: item.color,
  })) ?? []

  return (
    <section className="verification-map-evidence verification-map-evidence-rp017 ensemble-verification-map" aria-labelledby="ensemble-map-title">
      <header className="verification-map-heading verification-map-heading-rp017">
        <div>
          <span>空间证据 · 同一有效域</span>
          <h2 id="ensemble-map-title">集合均值空间对比</h2>
          <small>{frame ? `${formatUTC(frame.issue_time)} 起报 · +${frame.lead_minutes} 分钟 · ${formatUTC(frame.valid_time)} 有效` : '实况与两套集合均值共用经纬度范围和雨强色标'}</small>
        </div>
        <div className="verification-map-tools verification-map-tools-rp017" role="group" aria-label="集合验证地图控制">
          <button type="button" className={basemapVisible ? 'active' : ''} aria-pressed={basemapVisible} onClick={() => setBasemapVisible((value) => !value)}>参考底图</button>
          <button type="button" className={!smoothRaster ? 'active' : ''} aria-pressed={!smoothRaster} onClick={() => setSmoothRaster((value) => !value)}>{smoothRaster ? '平滑显示' : '格点显示'}</button>
          <label><span>雨层 {Math.round(rasterOpacity * 100)}%</span><input aria-label="集合验证雨层透明度" type="range" min="0.55" max="1" step="0.05" value={rasterOpacity} onChange={(event) => setRasterOpacity(Number(event.target.value))} /></label>
        </div>
      </header>

      <div className="verification-map-mobile-tabs" role="tablist" aria-label="移动端集合地图图层">
        {panels.map((panel) => <button type="button" role="tab" aria-selected={activePanel === panel.id} className={activePanel === panel.id ? 'active' : ''} key={panel.id} onClick={() => setActivePanel(panel.id)}>{panel.title}</button>)}
      </div>

      {!mapsAvailable ? (
        <div className="verification-map-empty"><strong>该概率运行没有空间图层</strong><span>冻结指标仍可查看；只有重新回算产生不可变地图包后才能展示空间分布。</span></div>
      ) : loading || !frame ? (
        <div className="verification-map-empty"><strong>{error ? '集合地图读取失败' : '正在读取集合地图'}</strong><span>{error ?? '只读取当前案例、起报和时效的三幅同步图层。'}</span></div>
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
                imageDescription={`${panel.title} +${frame.lead_minutes} 分钟雨强空间证据`}
                imageExtent={imageExtent}
                fitExtent={fitExtent}
                validTimeLabel={formatUTC(frame.valid_time)}
                contextLabel={`+${frame.lead_minutes} min`}
                productLabel="雨强"
                legend={legend}
                legendUnit="mm/h"
                footerNote="同步集合验证视野"
                mapLabel={`${panel.title}验证地图，共享视野，EPSG:4326`}
                resetViewLabel="复位集合验证案例范围"
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
        <footer className="verification-map-legend verification-map-legend-rp017">
          <div><span className="no-rain" style={{ backgroundColor: frame.valid_no_rain_color }} /><small>有效无雨 &lt; {frame.rain_threshold_mm_h} mm/h</small></div>
          {frame.legend.map((item) => <div key={item.minimum_mm_h}><span style={{ backgroundColor: item.color }} /><small>≥ {item.minimum_mm_h}</small></div>)}
          <div><span className="missing" /><small>透明：缺测 / 无覆盖</small></div>
          <em>{frame.palette_version} · EPSG:4326 · {frame.width}×{frame.height}</em>
        </footer>
      ) : null}
      <p className="verification-motion-note">这里显示集合成员的算术平均雨强，用于检查雨区位置与形态；它不是阈值概率图，也不代表概率已经校准。</p>
    </section>
  )
}

function LayerCoverage({ layer }: { layer?: MapLayer }) {
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
