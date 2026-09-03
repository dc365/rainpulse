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
import {
  analysisCycleAt,
  availabilityAt,
  formatCycleTime,
  formatValidTime,
  frameAt,
  leadLabel,
  panelsForPreset,
  qcFlagLabel,
  radarIDs,
  reasonLabel,
  timelineForPreset,
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
  const [catalogRevision, setCatalogRevision] = useState('')
  const [detail, setDetail] = useState<WorkspaceCycleDetail | null>(null)
  const [preset, setPreset] = useState<WorkspacePreset>('forecast')
  const [selectedRadarID, setSelectedRadarID] = useState<string | null>(null)
  const [selectedTime, setSelectedTime] = useState<string | null>(null)
  const [mobilePanelID, setMobilePanelID] = useState<string>('qpe')
  const [focusedPanelID, setFocusedPanelID] = useState<string | null>(() => (
    focusedPanelFromSearch(typeof window === 'undefined' ? '' : window.location.search)
  ))
  const [focusMenuOpen, setFocusMenuOpen] = useState(false)
  const [playing, setPlaying] = useState(false)
  const [loading, setLoading] = useState(true)
  const [error, setError] = useState<string | null>(null)
  const [basemapVisible, setBasemapVisible] = useState(true)
  const [rasterStyle, setRasterStyle] = useState<GISRasterStyle>('grid')
  const [showRasterValues, setShowRasterValues] = useState(false)
  const [rasterOpacity, setRasterOpacity] = useState(1)
  const [layerErrors, setLayerErrors] = useState<Record<string, boolean>>({})
  const layoutPickerRef = useRef<HTMLDivElement>(null)

  const updateLayerError = useCallback((panelID: string, failed: boolean) => {
    setLayerErrors((current) => updateLayerErrorState(current, panelID, failed))
  }, [])

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
          setCatalogRevision(catalogIdentity(payload.items))
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
    const timer = followLatest ? window.setInterval(loadCatalog, 30_000) : null
    return () => {
      controller.abort()
      if (timer != null) window.clearInterval(timer)
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
          const message = requestError instanceof Error ? requestError.message : '读取工作台失败'
          setError(`更新失败，保留当前结果：${message}`)
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

  const timelineValues = useMemo(
    () => detail ? timelineForPreset(detail, cycles, preset) : [],
    [cycles, detail, preset],
  )

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
    let nextValue = value
    if (targetPreset === 'qc' && detail && Date.parse(value) !== Date.parse(detail.issue_time)) {
      const matchingCycle = analysisCycleAt(cycles, detail.grid_id, value)
      if (matchingCycle) {
        if (matchingCycle.cycle_id !== selectedCycleID) {
          setLoading(true)
          setSelectedCycleID(matchingCycle.cycle_id)
          setFollowLatest(matchingCycle.cycle_id === cycles[0]?.cycle_id)
        }
      } else {
        nextValue = detail.issue_time
      }
    }
    if (stopPlayback) setPlaying(false)
    setSelectedTime(nextValue)
    setLayerErrors({})
  }, [cycles, detail, preset, selectedCycleID])

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
        <label className="cycle-selector">
          <span>周期</span>
          <select
            aria-label="选择分析周期"
            value={selectedCycleID}
            onChange={(event) => {
              const value = event.target.value
              const cycle = cycles.find((item) => item.cycle_id === value)
              setLoading(true)
              setPlaying(false)
              setSelectedCycleID(value)
              setSelectedTime(cycle?.issue_time ?? null)
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
              setSelectedTime(cycles[0].issue_time)
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
              onClick={() => {
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
        <div className="map-tools" role="group" aria-label="地图显示">
          <div className="workspace-layout-picker" ref={layoutPickerRef}>
            <button
              type="button"
              className={focusedPanel ? '' : 'active'}
              aria-pressed={!focusedPanel}
              onClick={showComparison}
            >四图</button>
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
            title="当前读取渲染色阶；真实格点值接口将在下一阶段接入"
            onClick={() => setShowRasterValues((value) => !value)}
          >色阶值</button>
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
            showRasterValues={showRasterValues}
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
        {!loading && panels.length === 0 ? <div className="workspace-empty">当前周期没有可显示图层。</div> : null}
      </section>

      {detail ? (
        <SharedTimeline
          issueTime={detail.issue_time}
          values={timelineValues}
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
}) {
  const frame = frameAt(panel, selectedTime)
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
  const unavailable = panel.status !== 'ready'
    ? reasonLabel(panel.unavailable_reason)
    : frame == null ? '当前算法无原生该有效时刻，未进行插值。' : undefined
  const lifecycle = panel.lifecycle === 'shadow'
    ? '影子'
    : panel.lifecycle === 'offline'
      ? '离线'
      : panel.lifecycle === 'analysis'
        ? '分析'
        : '业务'
  const frameContext = frame
    ? leadLabel(detail?.issue_time ?? frame.valid_time, frame.valid_time)
    : `每 ${panel.cadence_minutes} 分钟`
  const handleLayerError = useCallback(
    (failed: boolean) => onLayerError(panel.panel_id, failed),
    [onLayerError, panel.panel_id],
  )
  return (
    <article className={`workspace-map-panel${mobileActive ? ' mobile-active' : ''}${focused ? ' focus-selected' : ''}${focusMode && !focused ? ' focus-suppressed' : ''}`}>
      <div
        className="workspace-map-caption"
        aria-label={`${displayName}，${roleLabel(panel)}，${lifecycle}，${frameContext}`}
      >
        <strong>{displayName}</strong>
        <span>{roleLabel(panel)}</span>
        <b>{lifecycle}</b>
        <small>{frameContext}</small>
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
        imageDescription={`${displayName} ${formatValidTime(frame?.valid_time ?? selectedTime)}`}
        imageExtent={imageExtent}
        fitExtent={fitExtent}
        validTimeLabel={formatValidTime(frame?.valid_time ?? selectedTime)}
        contextLabel={frame ? leadLabel(detail?.issue_time ?? frame.valid_time, frame.valid_time) : '无原生帧'}
        productLabel={displayName}
        legend={legend}
        legendMode={panel.legend_unit ? 'scale' : 'categorical'}
        legendUnit={panel.legend_unit ?? frame?.unit ?? ''}
        footerNote={panel.data_kind === 'probability_exceedance' ? '透明：缺测 / 低于 1%' : '透明：缺测 / 无覆盖'}
        mapLabel={`${displayName}同步地图，EPSG:4326`}
        resetViewLabel="复位同步地图范围"
        emptyStateHint={unavailable}
        loading={loading}
        layerError={layerError}
        onLayerError={handleLayerError}
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
  const railRef = useRef<HTMLDivElement>(null)
  const selectedIndex = values.indexOf(selectedTime ?? issueTime)
  const activeIndex = selectedIndex >= 0 ? selectedIndex : 0
  const activeValue = values[activeIndex] ?? selectedTime ?? issueTime
  const intervalMinutes = values.length > 1
    ? Math.max(1, Math.round((Date.parse(values[1]) - Date.parse(values[0])) / 60_000))
    : 0

  useEffect(() => {
    const rail = railRef.current
    const active = rail?.querySelector<HTMLElement>('[aria-current="step"]')
    if (!rail || !active) return
    const left = active.offsetLeft - (rail.clientWidth - active.clientWidth) / 2
    if (typeof rail.scrollTo === 'function') rail.scrollTo({ left, behavior: 'smooth' })
    else rail.scrollLeft = left
  }, [activeIndex])

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
      data-playing={playing}
      aria-label="统一有效时间轴"
      tabIndex={0}
      onKeyDown={handleKeyDown}
    >
      <div className="workspace-timeline-context">
        <div className="workspace-timeline-playback">
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
        </div>
        <div className="workspace-timeline-periods" aria-hidden="true">
          <span>未来 0–1 小时</span>
          <span>未来 1–2 小时</span>
        </div>
        <div className="workspace-timeline-state">
          <span>{playing ? <i aria-hidden="true" /> : null}{playing
            ? `播放中 · ${activeIndex + 1}/${values.length} 帧`
            : `${values.length} 帧${intervalMinutes ? ` · ${intervalMinutes} 分钟间隔` : ''}`}</span>
          <span className="workspace-timeline-issue"><small>起报</small>{formatCycleTime(issueTime)}</span>
          <strong>{leadLabel(issueTime, activeValue)} · {formatValidTime(activeValue)}</strong>
        </div>
      </div>

      <div className="workspace-timeline-rail" ref={railRef}>
        {values.map((value, index) => {
          const leadMinutes = Math.round((Date.parse(value) - Date.parse(issueTime)) / 60_000)
          const active = index === activeIndex
          const major = leadMinutes === 0 || leadMinutes === 60 || leadMinutes === 120
          return (
            <button
              type="button"
              className={active ? 'active' : ''}
              key={value}
              onClick={() => onSelect(value)}
              aria-current={active ? 'step' : undefined}
              aria-label={`${leadLabel(issueTime, value)}，${formatValidTime(value)}`}
              title={`${formatValidTime(value)} · ${panels.filter((panel) => availabilityAt(panel, value)).length}/${panels.length} 面板可用`}
              data-major={major}
            >
              <i className="workspace-timeline-node" aria-hidden="true" />
              <span className="workspace-timeline-lead">{leadMinutes === 0 ? 'T0' : `${leadMinutes > 0 ? '+' : ''}${leadMinutes}`}</span>
              <span className="workspace-timeline-lanes" aria-hidden="true">
                {panels.map((panel) => (
                  <i key={panel.panel_id} data-ready={availabilityAt(panel, value)} />
                ))}
              </span>
            </button>
          )
        })}
      </div>

      <div className="workspace-timeline-availability" aria-label="算法帧可用性">
        <span className="workspace-timeline-current"><i />当前时效</span>
        {panels.map((panel) => (
          <span key={panel.panel_id}><i data-ready={availabilityAt(panel, selectedTime ?? issueTime)} />{panelDisplayName(panel)}</span>
        ))}
        <small>← → 键逐帧查看</small>
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

function catalogIdentity(items: CycleSummary[]) {
  return items.map((cycle) => [
    cycle.cycle_id,
    cycle.analysis_id ?? '',
    cycle.run_id ?? '',
    cycle.ensemble_bundle_id ?? '',
    cycle.nowcastnet_bundle_id ?? '',
  ].join(':')).join('|')
}

function compactLegendLabel(label: string, unit?: string | null) {
  let value = label.trim().replace(/^≥\s*/, '')
  if (unit && value.endsWith(` ${unit}`)) value = value.slice(0, -(unit.length + 1)).trim()
  return value
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
