import { useCallback, useEffect, useMemo, useState } from 'react'

import { getCenter } from 'ol/extent.js'
import View from 'ol/View.js'

import { FUZHOU_GIS_CONTEXT } from '../GISMapContexts'
import { RasterGISMap, type GISLegendEntry, type GISMapExtent } from '../RasterGISMap'
import {
  availabilityAt,
  formatCycleTime,
  formatValidTime,
  frameAt,
  leadLabel,
  panelsForPreset,
  radarIDs,
  reasonLabel,
  type CycleList,
  type CycleSummary,
  type WorkspaceCycleDetail,
  type WorkspacePanel,
  type WorkspacePreset,
} from './model'

const presetLabels: Record<WorkspacePreset, string> = {
  forecast: '预报对比',
  qc: '质控排查',
  verification: '检验回放',
}

export function MainWorkspace() {
  const [cycles, setCycles] = useState<CycleSummary[]>([])
  const [selectedCycleID, setSelectedCycleID] = useState<string>('')
  const [followLatest, setFollowLatest] = useState(true)
  const [catalogRevision, setCatalogRevision] = useState(0)
  const [detail, setDetail] = useState<WorkspaceCycleDetail | null>(null)
  const [preset, setPreset] = useState<WorkspacePreset>('forecast')
  const [selectedRadarID, setSelectedRadarID] = useState<string | null>(null)
  const [selectedTime, setSelectedTime] = useState<string | null>(null)
  const [mobilePanelID, setMobilePanelID] = useState<string>('qpe')
  const [playing, setPlaying] = useState(false)
  const [loading, setLoading] = useState(true)
  const [error, setError] = useState<string | null>(null)
  const [basemapVisible, setBasemapVisible] = useState(false)
  const [smoothRaster, setSmoothRaster] = useState(false)
  const [rasterOpacity, setRasterOpacity] = useState(.88)
  const [layerErrors, setLayerErrors] = useState<Record<string, boolean>>({})

  useEffect(() => {
    const controller = new AbortController()
    const loadCatalog = () => {
      void fetchJSON<CycleList>('/api/v1/workspace/cycles?limit=200', controller.signal)
        .then((payload) => {
          setCycles(payload.items)
          setSelectedCycleID((current) => {
            const latest = payload.items[0]?.cycle_id ?? ''
            if (followLatest || !payload.items.some((item) => item.cycle_id === current)) return latest
            return current
          })
          setCatalogRevision((value) => value + 1)
          setError(payload.degraded_sources?.length
            ? `部分目录降级：${payload.degraded_sources.join('、')}`
            : null)
        })
        .catch((requestError: unknown) => {
          if (!isAbortError(requestError)) {
            setError(requestError instanceof Error ? requestError.message : '读取周期目录失败')
          }
        })
    }
    loadCatalog()
    const timer = window.setInterval(loadCatalog, 30_000)
    return () => {
      controller.abort()
      window.clearInterval(timer)
    }
  }, [followLatest])

  useEffect(() => {
    if (!selectedCycleID) return
    const controller = new AbortController()
    void fetchJSON<WorkspaceCycleDetail>(
      `/api/v1/workspace/cycles/${encodeURIComponent(selectedCycleID)}`,
      controller.signal,
    )
      .then((payload) => {
        setDetail(payload)
        setSelectedTime((current) => current && payload.timeline.includes(current)
          ? current
          : payload.issue_time)
        const radars = radarIDs(payload)
        setSelectedRadarID((current) => current && radars.includes(current) ? current : radars[0] ?? null)
        setLayerErrors({})
        setError(payload.warnings?.length ? `证据不完整：${payload.warnings.join('、')}` : null)
      })
      .catch((requestError: unknown) => {
        if (!isAbortError(requestError)) {
          setDetail(null)
          setError(requestError instanceof Error ? requestError.message : '读取工作台失败')
        }
      })
      .finally(() => {
        if (!controller.signal.aborted) setLoading(false)
      })
    return () => controller.abort()
  }, [catalogRevision, selectedCycleID])

  const panels = useMemo(
    () => detail ? panelsForPreset(detail, preset, selectedRadarID) : [],
    [detail, preset, selectedRadarID],
  )

  useEffect(() => {
    if (!playing || !detail || detail.timeline.length < 2) return
    const timer = window.setInterval(() => {
      setSelectedTime((current) => {
        const index = detail.timeline.indexOf(current ?? detail.issue_time)
        return detail.timeline[(index + 1 + detail.timeline.length) % detail.timeline.length]
      })
    }, 1200)
    return () => window.clearInterval(timer)
  }, [detail, playing])

  const activeMobilePanelID = panels.some((panel) => panel.panel_id === mobilePanelID)
    ? mobilePanelID
    : panels[0]?.panel_id ?? ''

  const boundsKey = detail?.grid.bounds.join(',') ?? ''
  const mapView = useMemo(() => {
    const extent: GISMapExtent = validExtent(detail?.grid.bounds) ?? [118, 25, 123, 27]
    return new View({
      projection: 'EPSG:4326',
      center: getCenter(extent),
      extent: expandExtent(extent),
      constrainOnlyCenter: true,
      smoothExtentConstraint: true,
      minZoom: 5,
      maxZoom: 14,
    })
  // A serialized key avoids rebuilding the shared OpenLayers view on unrelated detail updates.
  // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [boundsKey])

  const selectTime = useCallback((value: string) => {
    setPlaying(false)
    setSelectedTime(value)
    setLayerErrors({})
  }, [])

  return (
    <main className="workspace-shell">
      <header className="workspace-topbar">
        <a className="workspace-brand" href="/" aria-label="RainPulse 主工作台">
          <span aria-hidden="true"><i /><i /><i /></span>
          <strong>RainPulse</strong>
          <small>短临降水工作台</small>
        </a>
        <label className="cycle-selector">
          <span>周期</span>
          <select
            aria-label="选择分析周期"
            value={selectedCycleID}
            onChange={(event) => {
              const value = event.target.value
              setLoading(true)
              setSelectedCycleID(value)
              setFollowLatest(value === cycles[0]?.cycle_id)
            }}
          >
            {cycles.map((cycle) => (
              <option key={cycle.cycle_id} value={cycle.cycle_id}>
                {formatCycleTime(cycle.issue_time)} · {capabilityText(cycle)}
              </option>
            ))}
          </select>
        </label>
        <button
          type="button"
          className={`workspace-status${followLatest ? ' following' : ''}`}
          aria-pressed={followLatest}
          aria-label={followLatest ? '停止实时跟随' : '恢复实时跟随'}
          onClick={() => {
            setFollowLatest((value) => !value)
            if (!followLatest && cycles[0]) {
              setLoading(true)
              setSelectedCycleID(cycles[0].cycle_id)
            }
          }}
        >
          <i className={detail && detail.freshness_seconds <= 900 ? 'fresh' : ''} />
          <span>{followLatest ? '实时跟随' : '历史固定'} · {detail?.execution_mode === 'realtime_shadow' ? '影子' : detail?.execution_mode ?? '读取中'}</span>
          <strong>{detail ? ageLabel(detail.freshness_seconds) : '—'}</strong>
        </button>
        <a className="admin-link" href="/admin">后台</a>
      </header>

      {error ? <div className="workspace-warning" role="status">{error}</div> : null}

      <section className="workspace-controls" aria-label="工作台控制">
        <div className="preset-tabs" role="tablist" aria-label="工作台预设">
          {(Object.keys(presetLabels) as WorkspacePreset[]).map((key) => (
            <button
              type="button"
              role="tab"
              aria-selected={preset === key}
              className={preset === key ? 'active' : ''}
              key={key}
              onClick={() => setPreset(key)}
            >{presetLabels[key]}</button>
          ))}
        </div>
        {preset === 'qc' && detail ? (
          <label className="radar-selector">
            <span>雷达</span>
            <select
              value={selectedRadarID ?? ''}
              onChange={(event) => setSelectedRadarID(event.target.value || null)}
            >
              {radarIDs(detail).map((radarID) => <option key={radarID} value={radarID}>{radarID.toUpperCase()}</option>)}
            </select>
          </label>
        ) : null}
        <div className="map-tools" role="group" aria-label="地图显示">
          <button type="button" className={basemapVisible ? 'active' : ''} onClick={() => setBasemapVisible((value) => !value)}>底图</button>
          <button type="button" className={!smoothRaster ? 'active' : ''} onClick={() => setSmoothRaster((value) => !value)}>{smoothRaster ? '平滑' : '格点'}</button>
          <label><span>雨层 {Math.round(rasterOpacity * 100)}%</span><input aria-label="雨层透明度" type="range" min="0.55" max="1" step="0.05" value={rasterOpacity} onChange={(event) => setRasterOpacity(Number(event.target.value))} /></label>
        </div>
        {detail ? <QualityStrip detail={detail} /> : null}
      </section>

      <section className="mobile-panel-tabs" role="tablist" aria-label="移动端地图面板">
        {panels.map((panel) => (
          <button
            type="button"
            role="tab"
            aria-selected={activeMobilePanelID === panel.panel_id}
            className={activeMobilePanelID === panel.panel_id ? 'active' : ''}
            key={panel.panel_id}
            onClick={() => setMobilePanelID(panel.panel_id)}
          >{panel.display_name}</button>
        ))}
      </section>

      <section className={`workspace-map-grid panels-${Math.min(4, Math.max(1, panels.length))}`} aria-label="同步地图对比">
        {panels.map((panel) => (
          <MapPanel
            key={panel.panel_id}
            panel={panel}
            detail={detail}
            selectedTime={selectedTime}
            loading={loading}
            sharedView={mapView}
            basemapVisible={basemapVisible}
            smoothRaster={smoothRaster}
            rasterOpacity={rasterOpacity}
            layerError={layerErrors[panel.panel_id] === true}
            onLayerError={(failed) => setLayerErrors((current) => ({ ...current, [panel.panel_id]: failed }))}
            mobileActive={activeMobilePanelID === panel.panel_id}
          />
        ))}
        {!loading && panels.length === 0 ? <div className="workspace-empty">当前周期没有可显示图层。</div> : null}
      </section>

      {detail ? (
        <SharedTimeline
          issueTime={detail.issue_time}
          values={detail.timeline}
          panels={panels}
          selectedTime={selectedTime}
          playing={playing}
          onTogglePlaying={() => setPlaying((value) => !value)}
          onSelect={selectTime}
        />
      ) : null}
    </main>
  )
}

function MapPanel({
  panel,
  detail,
  selectedTime,
  loading,
  sharedView,
  basemapVisible,
  smoothRaster,
  rasterOpacity,
  layerError,
  onLayerError,
  mobileActive,
}: {
  panel: WorkspacePanel
  detail: WorkspaceCycleDetail | null
  selectedTime: string | null
  loading: boolean
  sharedView: View
  basemapVisible: boolean
  smoothRaster: boolean
  rasterOpacity: number
  layerError: boolean
  onLayerError: (failed: boolean) => void
  mobileActive: boolean
}) {
  const frame = frameAt(panel, selectedTime)
  const imageExtent: GISMapExtent = validExtent(frame?.bounds)
    ?? validExtent(detail?.grid.raster_bounds)
    ?? [117.995, 24.995, 123.005, 27.005]
  const fitExtent: GISMapExtent = validExtent(detail?.grid.bounds) ?? imageExtent
  const legend: GISLegendEntry[] = (panel.legend ?? []).map((entry) => ({
    label: entry.label || (entry.minimum == null ? '' : String(entry.minimum)),
    color: entry.color,
  }))
  const unavailable = panel.status !== 'ready'
    ? reasonLabel(panel.unavailable_reason)
    : frame == null ? '当前算法无原生该有效时刻，未进行插值。' : undefined
  return (
    <article className={`workspace-map-panel${mobileActive ? ' mobile-active' : ''}`}>
      <header>
        <div><span>{roleLabel(panel)}</span><strong>{panel.display_name}</strong></div>
        <div><b>{panel.lifecycle === 'shadow' ? '影子' : panel.lifecycle === 'offline' ? '离线' : panel.lifecycle === 'analysis' ? '分析' : '业务'}</b><small>{frame ? leadLabel(detail?.issue_time ?? frame.valid_time, frame.valid_time) : `每 ${panel.cadence_minutes} 分钟`}</small></div>
      </header>
      <RasterGISMap
        className="workspace-comparison-map"
        imageUrl={frame?.image_url}
        imageDescription={`${panel.display_name} ${formatValidTime(frame?.valid_time ?? selectedTime)}`}
        imageExtent={imageExtent}
        fitExtent={fitExtent}
        validTimeLabel={formatValidTime(frame?.valid_time ?? selectedTime)}
        contextLabel={frame ? leadLabel(detail?.issue_time ?? frame.valid_time, frame.valid_time) : '无原生帧'}
        productLabel={panel.display_name}
        legend={legend}
        legendUnit={panel.legend_unit ?? frame?.unit ?? ''}
        footerNote={panel.data_kind === 'probability_exceedance' ? '透明：缺测 / 低于 1%' : '透明：缺测 / 无覆盖'}
        mapLabel={`${panel.display_name}同步地图，EPSG:4326`}
        resetViewLabel="复位同步地图范围"
        emptyStateHint={unavailable}
        loading={loading}
        layerError={layerError}
        onLayerError={onLayerError}
        sharedView={sharedView}
        comparisonMode
        basemapVisible={basemapVisible}
        smoothRaster={smoothRaster}
        rasterOpacity={rasterOpacity}
        motionVectors={[]}
        motionVisible={false}
        referenceContext={FUZHOU_GIS_CONTEXT}
      />
    </article>
  )
}

function SharedTimeline({
  issueTime,
  values,
  panels,
  selectedTime,
  playing,
  onTogglePlaying,
  onSelect,
}: {
  issueTime: string
  values: string[]
  panels: WorkspacePanel[]
  selectedTime: string | null
  playing: boolean
  onTogglePlaying: () => void
  onSelect: (value: string) => void
}) {
  const move = (step: number) => {
    const index = values.indexOf(selectedTime ?? issueTime)
    const next = values[Math.min(values.length - 1, Math.max(0, index + step))]
    if (next) onSelect(next)
  }
  return (
    <section className="shared-timeline" aria-label="统一有效时间轴">
      <div className="timeline-controls">
        <button type="button" onClick={() => move(-1)} aria-label="前一时刻">◀</button>
        <button type="button" className={playing ? 'active' : ''} onClick={onTogglePlaying}>{playing ? '暂停' : '播放'}</button>
        <button type="button" onClick={() => move(1)} aria-label="后一时刻">▶</button>
        <div><span>起报</span><strong>{formatCycleTime(issueTime)}</strong></div>
        <div><span>有效</span><strong>{formatValidTime(selectedTime)}</strong></div>
      </div>
      <div className="timeline-track">
        {values.map((value) => {
          const available = panels.filter((panel) => availabilityAt(panel, value)).length
          return (
            <button
              type="button"
              className={selectedTime === value ? 'active' : ''}
              key={value}
              onClick={() => onSelect(value)}
              title={`${formatValidTime(value)} · ${available}/${panels.length} 面板可用`}
            >
              <span>{leadLabel(issueTime, value)}</span>
              <i data-available={available}>{available}</i>
            </button>
          )
        })}
      </div>
      <div className="timeline-availability" aria-label="算法帧可用性">
        {panels.map((panel) => (
          <span key={panel.panel_id}><i data-ready={availabilityAt(panel, selectedTime ?? issueTime)} />{panel.display_name}</span>
        ))}
      </div>
    </section>
  )
}

function QualityStrip({ detail }: { detail: WorkspaceCycleDetail }) {
  const participating = detail.radars.filter((radar) => radar.state === 'PARTICIPATING').length
  return (
    <div className="quality-strip" aria-label="当前周期质量">
      <span><small>雷达</small><strong>{participating}/{detail.radars.length || 0}</strong></span>
      <span><small>覆盖</small><strong>{percent(detail.quality.coverage_ratio)}</strong></span>
      <span><small>平均 QI</small><strong>{number(detail.quality.mean_quality_index)}</strong></span>
      <span><small>最大雨强</small><strong>{rate(detail.quality.maximum_rate_mm_h)}</strong></span>
    </div>
  )
}

function capabilityText(cycle: CycleSummary) {
  return [
    cycle.capabilities.radar ? 'QPE' : null,
    cycle.capabilities.lk ? 'LK' : null,
    cycle.capabilities.steps ? 'STEPS' : null,
    cycle.capabilities.nowcastnet ? 'NowcastNet' : null,
  ].filter(Boolean).join('/') || '分析中'
}

function roleLabel(panel: WorkspacePanel) {
  if (panel.role === 'observation') return '实况分析'
  if (panel.role === 'qc') return panel.radar_id ? `${panel.radar_id.toUpperCase()} 质控` : '质控证据'
  if (panel.role === 'diagnostic') return '分析诊断'
  return '短临预报'
}

function ageLabel(seconds: number) {
  if (seconds < 60) return `${seconds}s`
  if (seconds < 3600) return `${Math.floor(seconds / 60)}m`
  return `${Math.floor(seconds / 3600)}h`
}

function percent(value?: number) {
  return value == null ? '—' : `${(value * 100).toFixed(1)}%`
}

function number(value?: number) {
  return value == null ? '—' : value.toFixed(3)
}

function rate(value?: number) {
  return value == null ? '—' : `${value.toFixed(1)} mm/h`
}

function validExtent(value?: readonly number[] | null): GISMapExtent | null {
  if (!value || value.length !== 4 || !value.every(Number.isFinite)) return null
  if (value[0] >= value[2] || value[1] >= value[3]) return null
  return [value[0], value[1], value[2], value[3]]
}

function expandExtent(extent: GISMapExtent): GISMapExtent {
  const x = (extent[2] - extent[0]) * .08
  const y = (extent[3] - extent[1]) * .08
  return [extent[0] - x, extent[1] - y, extent[2] + x, extent[3] + y]
}

async function fetchJSON<T>(path: string, signal: AbortSignal): Promise<T> {
  const response = await fetch(path, { signal })
  if (!response.ok) throw new Error(`接口 ${path} 响应 ${response.status}`)
  return await response.json() as T
}

function isAbortError(error: unknown) {
  return error instanceof DOMException && error.name === 'AbortError'
}
