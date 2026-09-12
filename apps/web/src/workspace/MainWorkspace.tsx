import { useCallback, useEffect, useMemo, useRef, useState } from 'react'

import { getCenter } from 'ol/extent.js'
import View from 'ol/View.js'

import { FUZHOU_GIS_CONTEXT } from '../GISMapContexts'
import {
  RasterGISMap,
  type GISLegendEntry,
  type GISMapExtent,
  type GISRasterStyle,
} from '../RasterGISMap'
import { radarDisplayExtent, radarSiteFor } from '../radarSites'
import { focusedPanelFromSearch, workspaceLayoutSearch } from './layoutState'
import { HistoryPicker } from './HistoryPicker'
import { useWorkspaceData } from './useWorkspaceData'
import { cycleAgeSeconds, isLiveCycle } from './workspaceState'
import { WorkspaceCrosshairInspector } from './WorkspaceCrosshairInspector'
import type { MapProbeDetail } from './mapProbe'
import type { MapCoordinate } from '../RasterGISMap'
import { VerificationInspector } from './VerificationInspector'
import { VerificationAnalysis } from './VerificationAnalysis'
import { verificationValidTime } from './verificationAnalysisModel'
import { accumulationLabel, type ProductMode } from './accumulation'
import { intervalLabel, useIntervalPanels, type Interval } from './IntervalTimeline'
import {
  analysisCycleAt,
  availabilityAt,
  displayFrameAt,
  formatCycleTime,
  formatValidTime,
  frameAt,
  leadLabel,
  panelsForPreset,
  qcFlagLabel,
  radarIDs,
  reasonLabel,
  timelineForPreset,
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

export function visibleWorkspaceWarnings(warnings: string[] = []) {
  // Optional ensemble diagnostics remain in the API; missing layers explain themselves.
  return warnings.filter(warning => warning !== 'ensemble-product')
}

export function MainWorkspace() {
  const { state, now, connection, refresh, requestCycle, setTime: setSelectedTime, follow, pin } = useWorkspaceData()
  const { cycles, detail, selectedTime: snapshotTime, loading } = state
  const selectedCycleID = detail?.cycle_id ?? ''
  const followLatest = state.mode === 'follow'
  const error = [state.catalogError, state.detailError ? `更新失败${detail ? `，保留 ${formatLocalCycleTime(detail.issue_time)} 起报结果` : ''}：${state.detailError}` : null,
    state.stale ? '数据服务降级，当前显示缓存结果' : null, ...visibleWorkspaceWarnings(detail?.warnings)].filter(Boolean).join('；') || null
  const [probe, setProbe] = useState<MapProbeDetail | null>(null)
  const [preset, setPreset] = useState<WorkspacePreset>('forecast')
  const [storedRadarID, setSelectedRadarID] = useState<string | null>(null)
  const selectedRadarID = detail && storedRadarID && radarIDs(detail).includes(storedRadarID) ? storedRadarID : detail ? radarIDs(detail)[0] ?? null : null
  const [verificationAlgorithm, setVerificationAlgorithm] = useState('lk')
  const [verificationThreshold, setVerificationThreshold] = useState(20)
  const [verificationWindow, setVerificationWindow] = useState(10)
  const [verificationPoint, setVerificationPoint] = useState<MapCoordinate | null>(null)
  const [mobilePanelID, setMobilePanelID] = useState<string>('qpe')
  const [focusedPanelID, setFocusedPanelID] = useState<string | null>(() => (
    focusedPanelFromSearch(typeof window === 'undefined' ? '' : window.location.search)
  ))
  const [focusMenuOpen, setFocusMenuOpen] = useState(false)
  const [playing, setPlaying] = useState(false)
  const [productMode, setProductMode] = useState<ProductMode>('rain_rate')
  const [interval, setInterval] = useState<Interval>({ start: 0, end: 60 })
  const activeProductMode = preset === 'forecast' ? productMode : 'rain_rate'
  const accumulation = useIntervalPanels(detail, activeProductMode !== 'rain_rate', interval)
  const [basemapVisible, setBasemapVisible] = useState(true)
  const [rasterStyle, setRasterStyle] = useState<GISRasterStyle>('grid')
  const [showRasterValues, setShowRasterValues] = useState(true)
  const [rasterOpacity, setRasterOpacity] = useState(1)
  const [layerErrors, setLayerErrors] = useState<Record<string, boolean>>({})
  const layoutPickerRef = useRef<HTMLDivElement>(null)

  const updateLayerError = useCallback((panelID: string, failed: boolean) => {
    setLayerErrors((current) => updateLayerErrorState(current, panelID, failed))
  }, [])

  const panels = useMemo(
    () => detail ? activeProductMode === 'rain_rate' ? panelsForPreset(detail, preset, selectedRadarID, verificationAlgorithm)
      : panelsForPreset(detail, 'forecast', selectedRadarID, verificationAlgorithm).map(panel => accumulation.panels?.find(result => result.panel_id === panel.panel_id) ?? ({
        ...panel, data_kind: 'accumulation_interval', frames: [], legend_unit: 'mm', legend: [], status: 'unavailable' as const,
        unavailable_reason: accumulation.error || '正在累计所选区间…',
      })) : [],
    [detail, preset, selectedRadarID, verificationAlgorithm, activeProductMode, accumulation.panels, accumulation.error],
  )

  const timelineValues = useMemo(
    () => detail ? timelineForPreset(detail, cycles, preset, verificationAlgorithm) : [],
    [cycles, detail, preset, verificationAlgorithm],
  )

  const selectedTime = activeProductMode !== 'rain_rate'
    ? detail ? new Date(Date.parse(detail.issue_time) + interval.end * 60_000).toISOString() : null
    : preset === 'verification' && !timelineValues.includes(snapshotTime ?? '') ? timelineValues[0] ?? detail?.issue_time ?? null : snapshotTime

  const focusedPanel = focusedPanelID
    ? panels.find((panel) => panel.panel_id === focusedPanelID) ?? null
    : null

  useEffect(() => {
    if (!detail && focusedPanelID) return
    const query = workspaceLayoutSearch(window.location.search, focusedPanel?.panel_id ?? null)
    const next = `${window.location.pathname}${query ? `?${query}` : ''}${window.location.hash}`
    const current = `${window.location.pathname}${window.location.search}${window.location.hash}`
    if (next !== current) window.history.replaceState(window.history.state, '', next)
  }, [detail, focusedPanel, focusedPanelID])

  useEffect(() => {
    if (!focusMenuOpen) return
    const closeMenu = (event: PointerEvent) => {
      if (!layoutPickerRef.current?.contains(event.target as Node)) setFocusMenuOpen(false)
    }
    document.addEventListener('pointerdown', closeMenu)
    return () => document.removeEventListener('pointerdown', closeMenu)
  }, [focusMenuOpen])

  useEffect(() => {
    const handleEscape = (event: KeyboardEvent) => {
      if (event.key !== 'Escape') return
      if (focusMenuOpen) setFocusMenuOpen(false)
      else if (focusedPanelID) setFocusedPanelID(null)
    }
    document.addEventListener('keydown', handleEscape)
    return () => document.removeEventListener('keydown', handleEscape)
  }, [focusMenuOpen, focusedPanelID])

  const applyTimeSelection = useCallback((
    value: string,
    stopPlayback: boolean,
    targetPreset: WorkspacePreset = preset,
  ) => {
    if (targetPreset === 'forecast' && activeProductMode !== 'rain_rate' && detail) {
      setProbe(null)
      setLayerErrors({})
      if (stopPlayback) setPlaying(false)
      return
    }
    let nextValue = value
    if (targetPreset === 'qc' && detail && Date.parse(value) !== Date.parse(detail.issue_time)) {
      const matchingCycle = analysisCycleAt(cycles, detail.grid_id, value)
      if (matchingCycle) {
        if (matchingCycle.cycle_id !== selectedCycleID) {
          requestCycle(matchingCycle, value)
          if (stopPlayback) setPlaying(false)
          return
        }
      } else {
        nextValue = detail.issue_time
      }
    }
    if (stopPlayback) setPlaying(false)
    setSelectedTime(nextValue)
    setLayerErrors({})
  }, [cycles, detail, preset, selectedCycleID, requestCycle, setSelectedTime, activeProductMode])

  useEffect(() => {
    if (!playing || loading || !detail || timelineValues.length < 2) return
    const timer = window.setInterval(() => {
      const index = timelineValues.indexOf(selectedTime ?? detail.issue_time)
      const next = timelineValues[(index + 1 + timelineValues.length) % timelineValues.length]
      if (next) applyTimeSelection(next, false)
    }, 1200)
    return () => window.clearInterval(timer)
  }, [applyTimeSelection, detail, loading, playing, selectedTime, timelineValues])

  const activeMobilePanelID = focusedPanel?.panel_id
    ?? (panels.some((panel) => panel.panel_id === mobilePanelID)
      ? mobilePanelID
      : panels[0]?.panel_id ?? '')

  const latestCycle = cycles.find(isLiveCycle) ?? null
  const realtimeAvailable = isLiveCycle(latestCycle) && cycleAgeSeconds(latestCycle, now) <= 900
  const selectedCycle = detail
  const isRealtimeView = Boolean(
    followLatest
    && realtimeAvailable && !state.stale && !state.catalogError && !state.detailError
    && selectedCycle?.cycle_id === latestCycle?.cycle_id,
  )
  const historicalCycles = cycles

  const selectedRadarSite = preset === 'qc' ? radarSiteFor(selectedRadarID) : undefined
  const mapFitExtent: GISMapExtent = selectedRadarSite
    ? radarDisplayExtent(selectedRadarSite)
    : validExtent(detail?.grid.bounds) ?? [118, 25, 123, 27]
  const boundsKey = mapFitExtent.join(',')
  const mapView = useMemo(() => {
    const extent = mapFitExtent
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
    applyTimeSelection(value, true)
  }, [applyTimeSelection])

  const focusPanel = useCallback((panelID: string) => {
    setFocusedPanelID(panelID)
    setMobilePanelID(panelID)
    setFocusMenuOpen(false)
  }, [])

  const showComparison = useCallback(() => {
    setFocusedPanelID(null)
    setFocusMenuOpen(false)
  }, [])

  return (
    <main className="workspace-shell">
      <header className="workspace-topbar">
        <a className="workspace-brand" href="/" aria-label="RainPulse 主工作台">
          <span aria-hidden="true"><i /><i /><i /></span>
          <strong>RainPulse</strong>
          <small>短临降水工作台</small>
        </a>
        <div className="workspace-data-mode" role="group" aria-label="数据模式">
          <button
            type="button"
            className={followLatest ? 'active' : ''}
            aria-pressed={followLatest}
            title={realtimeAvailable ? '跟随最新实时周期' : '当前没有新鲜的实时周期'}
            onClick={() => {
              setPlaying(false)
              if (preset === 'verification') setPreset('forecast')
              follow()
            }}
          >
            <i aria-hidden="true" />
            实时监测
          </button>
          <button type="button" className={!followLatest ? 'active' : ''} aria-pressed={!followLatest} onClick={() => { pin(); setPlaying(false) }}>历史案例</button>
          <span className="workspace-data-mode-note">
            {followLatest ? realtimeAvailable ? '跟随最新' : '等待新资料 · 自动恢复' : '固定历史起报'}
          </span>
        </div>
        {!followLatest ? <HistoryPicker cycles={historicalCycles} selectedID={selectedCycleID}
            onSelect={(cycle) => {
              setPlaying(false)
              requestCycle(cycle)
            }}
          /> : <section className={`workspace-cycle-summary${isRealtimeView ? ' live' : ''}`} aria-label="当前周期">
          <span>{isRealtimeView ? '实时周期' : '保留结果'}</span>
          <strong>{selectedCycle ? formatLocalCycleTime(selectedCycle.issue_time) : '读取周期中'}</strong>
          <small>{selectedCycle ? formatUTCCycleTime(selectedCycle.issue_time) : '—'}</small>
        </section>}
        <div className="workspace-freshness" aria-label="数据时效">
          <i className={isRealtimeView ? 'fresh' : ''} />
          <span>{followLatest ? connection === 'connected' ? '自动跟随 · 已连接' : '自动跟随 · 轮询恢复' : '历史回放 · 固定起报'}</span>
          <strong>{followLatest && detail ? ageLabel(cycleAgeSeconds(detail, now)) : selectedCycle ? capabilityText(selectedCycle) : '读取中'}</strong>
        </div>
        <a className="admin-link" href="/admin">后台</a>
      </header>

      {error ? <div className="workspace-warning" role="status">{error} <button type="button" onClick={refresh}>重试</button></div> : null}
      {loading && detail ? <div className="workspace-pending" role="status">正在读取所选周期；当前仍显示 {formatLocalCycleTime(detail.issue_time)} 起报结果。</div> : null}

      <section className="workspace-controls" aria-label="工作台控制">
        <div className="preset-tabs" role="tablist" aria-label="工作台预设">
          {(Object.keys(presetLabels) as WorkspacePreset[]).map((key) => (
            <button
              type="button"
              role="tab"
              aria-selected={preset === key}
              className={preset === key ? 'active' : ''}
              key={key}
              onClick={() => {
                if (key === 'verification') pin()
                setPreset(key)
                if (selectedTime) applyTimeSelection(selectedTime, true, key)
              }}
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
        {preset === 'verification' && <label className="verification-algorithm">对照预报
          <select aria-label="检验算法" value={verificationAlgorithm} onChange={event => { setVerificationAlgorithm(event.target.value); setPlaying(false) }}>
            <option value="lk">LK 确定性</option><option value="steps">STEPS P50</option><option value="nowcastnet">NowcastNet</option>
          </select></label>}
        <div className="map-tools" role="group" aria-label="地图显示">
          {preset === 'qc' ? <a href="/qc-review">QC 对照</a> : null}
          <div className="workspace-layout-picker" ref={layoutPickerRef}>
            <button
              type="button"
              className={focusedPanel ? '' : 'active'}
              aria-pressed={!focusedPanel}
              onClick={showComparison}
            >{preset === 'verification' ? '双图' : '四图'}</button>
            <button
              type="button"
              className={focusedPanel ? 'active workspace-focus-trigger' : 'workspace-focus-trigger'}
              aria-haspopup="menu"
              aria-expanded={focusMenuOpen}
              aria-pressed={Boolean(focusedPanel)}
              onClick={() => setFocusMenuOpen((value) => !value)}
            >
              <span>{focusedPanel ? `单图 · ${panelShortLabel(focusedPanel)}` : '单图'}</span>
              <i aria-hidden="true">⌄</i>
            </button>
            {focusMenuOpen ? (
              <div className="workspace-focus-menu" role="menu" aria-label="选择单图算法">
                {panels.map((panel) => (
                  <button
                    type="button"
                    role="menuitemradio"
                    aria-checked={focusedPanelID === panel.panel_id}
                    className={focusedPanelID === panel.panel_id ? 'selected' : ''}
                    key={panel.panel_id}
                    onClick={() => focusPanel(panel.panel_id)}
                  >
                    <span><strong>{panelDisplayName(panel)}</strong><small>{roleLabel(panel)}</small></span>
                    <i aria-hidden="true">{focusedPanelID === panel.panel_id ? '✓' : ''}</i>
                  </button>
                ))}
              </div>
            ) : null}
          </div>
          <button type="button" className={basemapVisible ? 'active' : ''} onClick={() => setBasemapVisible((value) => !value)}>底图</button>
          <div className="map-style-switch" role="group" aria-label="降水图层样式">
            {(['grid', 'smooth'] as const).map((style) => (
              <button
                type="button"
                className={rasterStyle === style ? 'active' : ''}
                aria-pressed={rasterStyle === style}
                key={style}
                onClick={() => setRasterStyle(style)}
              >{{ grid: '格点', smooth: '平滑' }[style]}</button>
            ))}
          </div>
          <button
            type="button"
            className={showRasterValues ? 'active' : ''}
            aria-pressed={showRasterValues}
            title="读取数值产品中的真实格点，不从图片颜色反推雨量"
            onClick={() => setShowRasterValues((value) => !value)}
          >点值</button>
          <label><span>雨层 {Math.round(rasterOpacity * 100)}%</span><input aria-label="雨层透明度" type="range" min="0.55" max="1" step="0.05" value={rasterOpacity} onChange={(event) => setRasterOpacity(Number(event.target.value))} /></label>
        </div>
        {detail ? <QualityStrip detail={detail} /> : null}
      </section>

      {preset === 'verification' && detail && <VerificationInspector detail={detail} algorithm={verificationAlgorithm}
        threshold={verificationThreshold} onThresholdChange={setVerificationThreshold} windowKM={verificationWindow} onWindowChange={setVerificationWindow}
        validTime={selectedTime} point={verificationPoint} onClear={() => setVerificationPoint(null)} />}
      <section className="mobile-panel-tabs" role="tablist" aria-label="移动端地图面板">
        {panels.map((panel) => (
          <button
            type="button"
            role="tab"
            aria-selected={activeMobilePanelID === panel.panel_id}
            className={activeMobilePanelID === panel.panel_id ? 'active' : ''}
            key={panel.panel_id}
            onClick={() => {
              setMobilePanelID(panel.panel_id)
              if (focusedPanelID) setFocusedPanelID(panel.panel_id)
            }}
          >{panelDisplayName(panel)}</button>
        ))}
      </section>

      <section
        className={`workspace-map-grid panels-${Math.min(4, Math.max(1, panels.length))}${focusedPanel ? ' layout-focus' : ''}`}
        aria-label={focusedPanel ? `${panelDisplayName(focusedPanel)}单图` : '同步地图对比'}
      >
        {panels.map((panel) => (
          <MapPanel
            key={panel.panel_id}
            panel={panel}
            detail={detail}
            selectedTime={selectedTime}
            loading={loading}
            sharedView={mapView}
            fitExtent={mapFitExtent}
            basemapVisible={basemapVisible}
            rasterStyle={rasterStyle}
            showRasterValues={false}
            onProbe={showRasterValues ? setProbe : undefined}
            onSelectPoint={preset === 'verification' ? setVerificationPoint : undefined}
            point={preset === 'verification' ? verificationPoint ?? undefined : undefined}
            rasterOpacity={rasterOpacity}
            layerError={layerErrors[panel.panel_id] === true}
            onLayerError={updateLayerError}
            mobileActive={activeMobilePanelID === panel.panel_id}
            focusMode={Boolean(focusedPanel)}
            focused={focusedPanelID === panel.panel_id}
            onFocus={() => focusPanel(panel.panel_id)}
            onShowComparison={showComparison}
          />
        ))}
        {panels.length === 0 ? <div className="workspace-empty" role="status">{loading ? '正在读取工作台…' : '暂无可显示的数据周期。'}{!loading && <button type="button" onClick={refresh}>重新读取</button>}</div> : null}
      </section>

      {preset === 'verification' && detail && <VerificationAnalysis detail={detail} cycles={cycles} validTime={selectedTime}
        threshold={verificationThreshold} windowKM={verificationWindow} onThresholdChange={setVerificationThreshold} onWindowChange={setVerificationWindow}
        onNavigate={(id,lead,algorithm)=>{
          const cycle=cycles.find(c=>c.cycle_id===id);if(!cycle)return
          const time=verificationValidTime(cycle.issue_time,lead,id===detail.cycle_id?detail.timeline:undefined)
          setVerificationAlgorithm(algorithm);setPlaying(false);setVerificationPoint(null);setProbe(null)
          if(id===detail.cycle_id)setSelectedTime(time);else requestCycle(cycle,time)
        }} />}
      {detail ? (
        <SharedTimeline
          selectedInterval={activeProductMode !== 'rain_rate' ? interval : null}
          intervalBusy={accumulation.busy}
          onInterval={preset === 'forecast' ? (value) => {
            setInterval(value); setProductMode('hourly'); setPlaying(false); setProbe(null); setLayerErrors({})
          } : undefined}
          onRetryInterval={accumulation.retry}
          detail={detail}
          issueTime={detail.issue_time}
          values={timelineValues}
          panels={panelsForPreset(detail, preset, selectedRadarID, verificationAlgorithm)}
          selectedTime={selectedTime}
          playing={playing}
          onTogglePlaying={() => { setProductMode('rain_rate'); setPlaying((value) => !value) }}
          onSelect={value => {
            setProductMode('rain_rate'); setPlaying(false); setProbe(null); setLayerErrors({})
            if (preset === 'forecast') setSelectedTime(value)
            else selectTime(value)
          }}
        />
      ) : null}
      <WorkspaceCrosshairInspector probe={showRasterValues && probe && detail && panels.some(panel =>
        displayFrameAt(detail, panel, selectedTime).frame?.image_url === probe.assetUrl) ? probe : null} />
    </main>
  )
}

function MapPanel({
  panel,
  detail,
  selectedTime,
  loading,
  sharedView,
  fitExtent,
  basemapVisible,
  rasterStyle,
  showRasterValues,
  rasterOpacity,
  layerError,
  onLayerError,
  mobileActive,
  focusMode,
  focused,
  onFocus,
  onShowComparison,
  onProbe,
  onSelectPoint,
  point,
}: {
  panel: WorkspacePanel
  detail: WorkspaceCycleDetail | null
  selectedTime: string | null
  loading: boolean
  sharedView: View
  fitExtent: GISMapExtent
  basemapVisible: boolean
  rasterStyle: GISRasterStyle
  showRasterValues: boolean
  rasterOpacity: number
  layerError: boolean
  onLayerError: (panelID: string, failed: boolean) => void
  mobileActive: boolean
  focusMode: boolean
  focused: boolean
  onFocus: () => void
  onShowComparison: () => void
  onProbe?: (probe: MapProbeDetail | null) => void
  onSelectPoint?: (point: MapCoordinate) => void
  point?: MapCoordinate
}) {
  const { frame, usesAnalysisBaseline } = detail
    ? displayFrameAt(detail, panel, selectedTime)
    : { frame: frameAt(panel, selectedTime), usesAnalysisBaseline: false }
  const displayName = panelDisplayName(panel)
  const radarSite = panel.data_kind === 'reflectivity' ? radarSiteFor(panel.radar_id) : undefined
  const analysisRadar = radarSite
    ? detail?.radars.find((radar) => radar.radar_id.toLowerCase() === radarSite.radarID)
    : undefined
  const radarContext = radarSite
    ? {
        ...radarSite,
        timeOffsetSeconds: analysisRadar?.time_offset_seconds,
        meanQualityIndex: analysisRadar?.mean_quality_index,
      }
    : undefined
  const isQCFlagsPanel = panel.panel_id === 'analysis:qc_flags'
    || panel.panel_id.startsWith('qc_flags:')
  const imageExtent: GISMapExtent = radarSite
    ? radarDisplayExtent(radarSite, radarSite.maximumRangeKM)
    : validExtent(frame?.bounds)
      ?? validExtent(detail?.grid.raster_bounds)
      ?? [117.995, 24.995, 123.005, 27.005]
  const legend: GISLegendEntry[] = (panel.legend ?? []).map((entry) => ({
    label: compactLegendLabel(
      isQCFlagsPanel
        ? qcFlagLabel(entry.label ?? '')
        : entry.label || (entry.minimum == null ? '' : String(entry.minimum)),
      panel.legend_unit,
    ),
    color: entry.color,
    minimum: entry.minimum,
    sourceLabel: isQCFlagsPanel ? entry.label ?? undefined : undefined,
  }))
  const unavailable = !usesAnalysisBaseline && panel.status !== 'ready'
    ? panel.unavailable_reason === 'observation_accumulation_unavailable' ? '观测累计产品暂不可用，不使用预报或零值填补。'
      : panel.unavailable_reason === 'accumulation_not_generated' ? '本起报累计产品尚未生成，需重新生成数据。' : reasonLabel(panel.unavailable_reason)
    : panel.data_kind.startsWith('accumulation_') && frame == null ? '该区间累计数据不完整或尚未生成。'
    : frame == null ? '当前算法无原生该有效时刻，未进行插值。' : undefined
  const lifecycle = panel.lifecycle === 'shadow'
    ? '影子'
    : panel.lifecycle === 'offline'
      ? '离线'
      : panel.lifecycle === 'reference'
        ? '参考'
      : panel.lifecycle === 'analysis'
        ? '分析'
        : '业务'
  const frameContext = panel.data_kind.startsWith('accumulation_') && detail && selectedTime
    ? panel.data_kind === 'accumulation_interval' && frame?.source_leads?.length
      ? intervalLabel(detail.issue_time, { start: frame.source_leads[0]-5, end: frame.lead_time_minutes })
      : accumulationLabel(detail.issue_time, selectedTime, panel.data_kind === 'accumulation_60' ? 'hourly' : 'total_2h')
    : usesAnalysisBaseline
    ? 'T0 分析场'
    : frame?.reference_observation && frame.observation_time
    ? `参考体扫 ${formatValidTime(frame.observation_time)}（${formatObservationOffset(frame.observation_offset_seconds)}，未参与本时次拼图）`
    : frame
    ? leadLabel(detail?.issue_time ?? frame.valid_time, frame.valid_time)
    : `每 ${panel.cadence_minutes} 分钟`
  const handleLayerError = useCallback(
    (failed: boolean) => onLayerError(panel.panel_id, failed),
    [onLayerError, panel.panel_id],
  )
  const handleProbe = useCallback((point: MapCoordinate | null) => {
    onProbe?.(point && frame ? { ...point, assetUrl: frame.image_url, panelID: panel.panel_id,
      panelLabel: displayName, validTime: frame.valid_time, frameKind: frame.frame_kind } : null)
  }, [onProbe, frame, panel.panel_id, displayName])
  return (
    <article className={`workspace-map-panel${mobileActive ? ' mobile-active' : ''}${focused ? ' focus-selected' : ''}${focusMode && !focused ? ' focus-suppressed' : ''}`}>
      <div
        className="workspace-map-caption"
        aria-label={`${displayName}，${roleLabel(panel)}，${lifecycle}，${frameContext}`}
      >
        <strong>{displayName}</strong>
        <span>{roleLabel(panel)}</span>
        <b>{lifecycle}</b>
        <small>{frameContext}{frame?.frame_kind === 'derived' ? ' · 派生帧' : ''}{panel.panel_id === 'steps' ? ' · 未校准集合' : ''}</small>
      </div>
      <button
        type="button"
        className="workspace-map-focus"
        aria-label={focused ? '返回四图' : `单图查看 ${displayName}`}
        title={focused ? '返回四图（Esc）' : `单图查看 ${displayName}`}
        onClick={focused ? onShowComparison : onFocus}
      >
        <span aria-hidden="true">{focused ? '▦' : '□'}</span>
        {focused ? '四图' : '单图'}
      </button>
      <RasterGISMap
        className="workspace-comparison-map"
        imageUrl={frame?.image_url}
        imageDescription={`${displayName} ${usesAnalysisBaseline ? 'T0 共用雷达 QPE 分析场' : formatValidTime(frame?.valid_time ?? selectedTime)}`}
        imageExtent={imageExtent}
        fitExtent={fitExtent}
        validTimeLabel={formatValidTime(frame?.valid_time ?? selectedTime)}
        contextLabel={frameContext}
        productLabel={displayName}
        legend={legend}
        legendMode={panel.legend_unit ? 'scale' : 'categorical'}
        legendUnit={panel.legend_unit ?? frame?.unit ?? ''}
        footerNote={panel.data_kind === 'probability_exceedance' ? '原始集合频率，未校准；空白不代表零风险，请查询有效覆盖' : '空白不代表无雨；点值区分有效零雨量与缺测'}
        mapLabel={`${displayName}同步地图，EPSG:4326`}
        resetViewLabel="复位同步地图范围"
        emptyStateHint={unavailable}
        loading={loading || panel.unavailable_reason === '正在累计所选区间…'}
        loadingLabel={panel.unavailable_reason === '正在累计所选区间…' ? '正在计算累积…' : undefined}
        layerError={layerError}
        onLayerError={handleLayerError}
        onProbe={onProbe ? handleProbe : undefined}
        onSelectPoint={onSelectPoint}
        point={point}
        sharedView={sharedView}
        comparisonMode
        basemapVisible={basemapVisible}
        rasterStyle={rasterStyle}
        showRasterValues={showRasterValues}
        rasterOpacity={rasterOpacity}
        motionVectors={[]}
        motionVisible={false}
        referenceContext={FUZHOU_GIS_CONTEXT}
        radarContext={radarContext}
      />
    </article>
  )
}

function formatObservationOffset(value?: number) {
  if (value == null) return '真实观测'
  const sign = value > 0 ? '+' : ''
  return `${sign}${value} 秒`
}

// Exported for the state-identity regression guard next to the workspace tests.
// eslint-disable-next-line react-refresh/only-export-components
export function updateLayerErrorState(
  current: Record<string, boolean>,
  panelID: string,
  failed: boolean,
) {
  if (current[panelID] === failed) return current
  return { ...current, [panelID]: failed }
}

export function SharedTimeline({
  productMode = 'rain_rate',
  onProductMode,
  selectedInterval = null,
  onInterval,
  intervalBusy = false,
  onRetryInterval,
  detail,
  issueTime,
  values,
  panels,
  selectedTime,
  playing,
  onTogglePlaying,
  onSelect,
}: {
  productMode?: ProductMode
  onProductMode?: (mode: ProductMode) => void
  selectedInterval?: Interval | null
  onInterval?: (range: Interval) => void
  intervalBusy?: boolean
  onRetryInterval?: () => void
  detail?: WorkspaceCycleDetail
  issueTime: string
  values: string[]
  panels: WorkspacePanel[]
  selectedTime: string | null
  playing: boolean
  onTogglePlaying: () => void
  onSelect: (value: string) => void
}) {
  const railRef = useRef<HTMLDivElement>(null)
  const gesture = useRef<{ lead: number; x: number; moved: boolean; end: number } | null>(null)
  const [draftInterval, setDraftInterval] = useState<Interval | null>(null)
  const highlighted = draftInterval ?? selectedInterval
  const leadValue = (lead: number) => values.find(value => Math.round((Date.parse(value)-Date.parse(issueTime))/60_000) === lead)
  const pointerLead = (x: number) => {
    const nodes = Array.from(railRef.current?.querySelectorAll<HTMLElement>('[data-lead]') ?? [])
    return nodes.reduce<HTMLElement | null>((best, node) => {
      const distance = (item: HTMLElement) => { const box = item.getBoundingClientRect(); return Math.abs(x-box.left-box.width/2) }
      return !best || distance(node) < distance(best) ? node : best
    }, null)?.dataset.lead
  }
  const cancelGesture = () => { gesture.current = null; setDraftInterval(null) }
  const selectedIndex = values.indexOf(selectedTime ?? issueTime)
  const activeIndex = selectedIndex >= 0 ? selectedIndex : 0
  const activeValue = values[activeIndex] ?? selectedTime ?? issueTime
  const intervalMinutes = values.length > 1
    ? Math.max(1, Math.round((Date.parse(values[1]) - Date.parse(values[0])) / 60_000))
    : 0
  const isDisplayAvailable = (panel: WorkspacePanel, value: string) => (
    detail ? displayFrameAt(detail, panel, value).frame != null : availabilityAt(panel, value)
  )

  useEffect(() => {
    const rail = railRef.current
    const active = rail?.querySelector<HTMLElement>('[aria-current="step"]')
      ?? rail?.querySelector<HTMLElement>(`[data-lead="${selectedInterval?.end}"]`)
    if (!rail || !active) return
    const left = active.offsetLeft - (rail.clientWidth - active.clientWidth) / 2
    if (typeof rail.scrollTo === 'function') rail.scrollTo({ left, behavior: window.matchMedia?.('(prefers-reduced-motion: reduce)').matches ? 'auto' : 'smooth' })
    else rail.scrollLeft = left
  }, [activeIndex, selectedInterval?.end])

  const move = (step: number) => {
    const next = values[Math.min(values.length - 1, Math.max(0, activeIndex + step))]
    if (next) onSelect(next)
  }

  const handleKeyDown = (event: React.KeyboardEvent<HTMLElement>) => {
    if (event.key === 'ArrowLeft') {
      event.preventDefault()
      move(-1)
    } else if (event.key === 'ArrowRight') {
      event.preventDefault()
      move(1)
    } else if (event.key === 'Home' && values[0]) {
      event.preventDefault()
      onSelect(values[0])
    } else if (event.key === 'End' && values.length) {
      event.preventDefault()
      onSelect(values[values.length - 1])
    }
  }

  return (
    <section
      className="shared-timeline"
      data-product-mode={productMode}
      data-playing={playing}
      aria-label="统一有效时间轴"
      tabIndex={0}
      onKeyDown={handleKeyDown}
    >
      <div className="workspace-timeline-context">
        {onInterval && <div className="interval-shortcuts" role="group" aria-label="累计快捷区间">
          {[[0,60],[60,120],[0,120]].map(([start,end]) => <button type="button" key={`${start}-${end}`}
            aria-pressed={selectedInterval?.start === start && selectedInterval.end === end}
            onClick={() => onInterval({start,end})}>{start/60}–{end/60} 时</button>)}
        </div>}
        {!onInterval && onProductMode && <div className="workspace-product-modes" role="group" aria-label="降水产品">
          {(['rain_rate', 'hourly'] as ProductMode[]).map(mode => <button type="button" key={mode}
            className={productMode === mode ? 'active' : ''} aria-pressed={productMode === mode}
            onClick={() => onProductMode(mode)}>{mode === 'rain_rate' ? '5分钟雨强' : '区间累计'}</button>)}
        </div>}
        {productMode !== 'total_2h' && <div className="workspace-timeline-playback">
          <button
            type="button"
            onClick={() => move(-1)}
            disabled={!values.length || activeIndex === 0}
            aria-label="前一时刻"
          >
            <span aria-hidden="true">◀</span>
          </button>
          <button
            type="button"
            className="workspace-timeline-play"
            aria-pressed={playing}
            onClick={onTogglePlaying}
            disabled={values.length < 2}
          >
            <span aria-hidden="true">{playing ? 'Ⅱ' : '▶'}</span>
            {playing ? '暂停' : '播放'}
          </button>
          <button
            type="button"
            onClick={() => move(1)}
            disabled={!values.length || activeIndex >= values.length - 1}
            aria-label="后一时刻"
          >
            <span aria-hidden="true">▶</span>
          </button>
        </div>}
        {productMode === 'rain_rate' && !onProductMode && !onInterval && <div className="workspace-timeline-periods" aria-hidden="true">
          <span>未来 0–1 小时</span>
          <span>未来 1–2 小时</span>
        </div>}
        <div className="workspace-timeline-state">
          <span>{playing ? <i aria-hidden="true" /> : null}{playing
            ? `播放中 · ${activeIndex + 1}/${values.length} 帧`
            : productMode !== 'rain_rate' ? `${values.length} 个累计区间`
            : `${values.length} 帧${intervalMinutes ? ` · ${intervalMinutes} 分钟间隔` : ''}`}</span>
          <span className="workspace-timeline-issue"><small>起报</small>{formatCycleTime(issueTime)}</span>
          <strong>{highlighted ? `${intervalLabel(issueTime, highlighted)}${intervalBusy && !draftInterval ? ' · 计算中…' : ''}` : productMode === 'rain_rate' ? `${leadLabel(issueTime, activeValue)} · ${formatValidTime(activeValue)}`
            : accumulationLabel(issueTime, activeValue, productMode)}</strong>
        </div>
      </div>

      <div className={`workspace-timeline-rail${onInterval ? ' selectable-timeline' : ''}${productMode !== 'rain_rate' ? ' accumulation-rail' : ''}`} ref={railRef}
        onPointerDown={event => {
          if (!onInterval || event.button !== 0 || event.isPrimary === false) return
          const node = (event.target as HTMLElement).closest<HTMLElement>('[data-lead]')
          const lead = Number(node?.dataset.lead ?? pointerLead(event.clientX))
          if (!Number.isFinite(lead) || lead < 0 || lead > 120) return
          gesture.current = {lead, end:lead, x:event.clientX, moved:false}
          event.currentTarget.setPointerCapture?.(event.pointerId)
        }}
        onPointerMove={event => {
          const current = gesture.current
          if (!current) return
          current.moved ||= Math.abs(event.clientX-current.x) >= 5
          if (!current.moved) return
          const lead = Number(pointerLead(event.clientX))
          if (!Number.isFinite(lead) || lead < 0 || lead > 120) return
          current.end = lead
          setDraftInterval(lead === current.lead ? null : {start:Math.min(lead,current.lead),end:Math.max(lead,current.lead)})
        }}
        onPointerUp={event => {
          const current = gesture.current
          if (!current) return
          cancelGesture()
          if (event.currentTarget.hasPointerCapture?.(event.pointerId)) event.currentTarget.releasePointerCapture(event.pointerId)
          if (current.moved && current.end !== current.lead) onInterval?.({start:Math.min(current.lead,current.end),end:Math.max(current.lead,current.end)})
          else { const value = leadValue(current.lead); if (value) onSelect(value) }
        }}
        onPointerCancel={cancelGesture}
        onLostPointerCapture={cancelGesture}>
        {values.map((value, index) => {
          const leadMinutes = Math.round((Date.parse(value) - Date.parse(issueTime)) / 60_000)
          const active = !highlighted && index === activeIndex
          const major = leadMinutes === 0 || leadMinutes === 60 || leadMinutes === 120
          return (
            <button
              type="button"
              className={active ? 'active' : ''}
              key={value}
              onClick={event => { if (!onInterval || event.detail === 0) onSelect(value) }}
              data-lead={leadMinutes}
              data-selected={Boolean(highlighted && leadMinutes >= highlighted.start && leadMinutes <= highlighted.end)}
              aria-current={active ? 'step' : undefined}
              aria-label={productMode === 'rain_rate' ? `${leadLabel(issueTime, value)}，${formatValidTime(value)}` : accumulationLabel(issueTime, value, productMode)}
              title={`${formatValidTime(value)} · ${panels.filter((panel) => isDisplayAvailable(panel, value)).length}/${panels.length} 面板可用`}
              data-major={major}
            >
              <i className="workspace-timeline-node" aria-hidden="true" />
              <span className="workspace-timeline-lead">{productMode === 'rain_rate'
                ? leadMinutes === 0 ? 'T0' : `${leadMinutes > 0 ? '+' : ''}${leadMinutes}`
                : `${productMode === 'total_2h' ? 0 : leadMinutes / 60 - 1}–${leadMinutes / 60} 小时`}</span>
              <span className="workspace-timeline-lanes" aria-hidden="true">
                {panels.map((panel) => (
                  <i key={panel.panel_id} data-ready={isDisplayAvailable(panel, value)} />
                ))}
              </span>
            </button>
          )
        })}
      </div>

      <div className="workspace-timeline-availability" aria-label="图层可用性">
        <span className="workspace-timeline-current"><i />{highlighted ? '累计区间' : '当前时效'}</span>
        {panels.map((panel) => (
          <span key={panel.panel_id}><i data-ready={isDisplayAvailable(panel, selectedTime ?? issueTime)} />{panelDisplayName(panel)}</span>
        ))}
        {selectedInterval && onRetryInterval && <button type="button" onClick={onRetryInterval} disabled={intervalBusy}>重新计算</button>}
        <small>{onInterval ? '点击看单时效 · 按住拖动看累计' : '← → 键逐帧查看'}</small>
      </div>
    </section>
  )
}

function QualityStrip({ detail }: { detail: WorkspaceCycleDetail }) {
  const participating = detail.radars.filter((radar) => radar.state === 'PARTICIPATING').length
  return (
    <div className="quality-strip" aria-label="T0 输入质量">
      <span><small>T0雷达</small><strong>{participating}/{detail.radars.length || 0}</strong></span>
      <span><small>T0覆盖</small><strong>{percent(detail.quality.coverage_ratio)}</strong></span>
      <span><small>T0 QI</small><strong>{number(detail.quality.mean_quality_index)}</strong></span>
      <span><small>T0最大</small><strong>{rate(detail.quality.maximum_rate_mm_h)}</strong></span>
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

function formatLocalCycleTime(value: string) {
  return new Intl.DateTimeFormat('zh-CN', {
    timeZone: 'Asia/Taipei',
    month: '2-digit',
    day: '2-digit',
    hour: '2-digit',
    minute: '2-digit',
    hour12: false,
  }).format(new Date(value)) + ' CST'
}

function formatUTCCycleTime(value: string) {
  return new Intl.DateTimeFormat('zh-CN', {
    timeZone: 'UTC',
    month: '2-digit',
    day: '2-digit',
    hour: '2-digit',
    minute: '2-digit',
    hour12: false,
  }).format(new Date(value)) + ' UTC'
}

function roleLabel(panel: WorkspacePanel) {
  if (panel.role === 'observation') return '实况分析'
  if (panel.role === 'qc') return panel.radar_id ? `${panel.radar_id.toUpperCase()} 质控` : '质控证据'
  if (panel.role === 'diagnostic') return '分析诊断'
  return '短临预报'
}

function panelDisplayName(panel: WorkspacePanel) {
  if (panel.panel_id === 'steps') {
    return panel.data_kind === 'probability_exceedance' ? 'STEPS 概率' : 'STEPS P50'
  }
  if (panel.panel_id.startsWith('dbzh_qc:')) {
    return `${panel.radar_id?.toUpperCase() ?? ''} 质控后反射率`.trim()
  }
  return panel.display_name
}

function panelShortLabel(panel: WorkspacePanel) {
  if (panel.algorithm_id === 'radar') return 'QPE'
  if (panel.algorithm_id === 'pysteps-lk') return 'LK'
  if (panel.algorithm_id === 'pysteps-steps') return 'STEPS'
  if (panel.algorithm_id === 'nowcastnet') return 'NowcastNet'
  return panelDisplayName(panel)
}

function compactLegendLabel(label: string, unit?: string | null) {
  let value = label.trim().replace(/^≥\s*/, '')
  if (unit && value.endsWith(` ${unit}`)) value = value.slice(0, -(unit.length + 1)).trim()
  return value
}

function ageLabel(seconds: number) {
  if (!Number.isFinite(seconds)) return '时效异常'
  if (seconds < 60) return `${Math.floor(seconds)} 秒`
  if (seconds < 3600) return `${Math.floor(seconds / 60)} 分钟`
  return `${Math.floor(seconds / 3600)} 小时`
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
