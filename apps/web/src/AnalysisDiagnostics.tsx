import { useCallback, useEffect, useMemo, useState } from 'react'

import type { components } from './api/generated/schema'

type AnalysisCycle = components['schemas']['AnalysisCycle']
type AnalysisCyclePage = components['schemas']['AnalysisCyclePage']
type MosaicMetrics = components['schemas']['AnalysisMosaicMetrics']
type QPEMetrics = components['schemas']['AnalysisQPEMetrics']
type DiagnosticBundle = components['schemas']['DiagnosticBundle']
type DiagnosticLayer = components['schemas']['DiagnosticLayer']

const analysisStatusLabels: Record<string, string> = {
  OPEN: '已打开',
  COLLECTING_RADARS: '收集雷达',
  ALIGNING: '时间对齐',
  MOSAIC_RUNNING: '拼图中',
  QPE_RUNNING: 'QPE 中',
  ANALYSIS_READY: '分析就绪',
  DEGRADED: '分析降级',
  FAILED: '分析失败',
  SKIPPED: '已跳过',
}

const reasonLabels: Record<string, string> = {
  insufficient_operational_contributors: '业务可用贡献雷达不足',
  'input_not_operational:z9598': 'Z9598 当前仍是工程配置',
}

const fieldNotes: Record<string, string> = {
  DBZH_RAW: '原始极坐标反射率，仅用于单雷达质控对照。',
  DBZH_QC: '质控后反射率；缺测保持透明，不等同于无雨。',
  RATE_QPE: '基础 Z–R 瞬时雨强，尚未进行雨量站订正。',
  QUALITY_INDEX: '综合质量指数，颜色越接近雷达青表示证据越可靠。',
  SOURCE_RADAR: '记录每个有效网格的实际来源雷达或融合来源。',
  BEAM_HEIGHT: '雷达波束中心相对海平面的工程高度估计。',
  QC_FLAGS: '显示每个像素最高优先级的原因标志；底层 uint32 位集仍是权威值。',
  'VALID_MASK+LOW_QUALITY_MASK': '青色为有效，橙色为低质量；透明区域为缺测。',
}

function formatUtc(value?: string | null, seconds = false) {
  if (!value) return '—'
  return `${new Intl.DateTimeFormat('zh-CN', {
    timeZone: 'UTC',
    month: '2-digit',
    day: '2-digit',
    hour: '2-digit',
    minute: '2-digit',
    second: seconds ? '2-digit' : undefined,
    hour12: false,
  }).format(new Date(value))} UTC`
}

function percent(value?: number | null, digits = 1) {
  return value == null ? '—' : `${(value * 100).toFixed(digits)}%`
}

function rate(value?: number | null) {
  return value == null ? '—' : `${value.toFixed(2)} mm/h`
}

export function AnalysisDiagnostics({ refreshToken }: { refreshToken: number }) {
  const [cycles, setCycles] = useState<AnalysisCycle[]>([])
  const [selectedID, setSelectedID] = useState<string | null>(null)
  const [selected, setSelected] = useState<AnalysisCycle | null>(null)
  const [mosaic, setMosaic] = useState<MosaicMetrics | null>(null)
  const [qpe, setQPE] = useState<QPEMetrics | null>(null)
  const [diagnostics, setDiagnostics] = useState<DiagnosticBundle | null>(null)
  const [selectedLayerID, setSelectedLayerID] = useState<string | null>(null)
  const [scope, setScope] = useState<'grid' | 'polar'>('grid')
  const [loading, setLoading] = useState(true)
  const [error, setError] = useState<string | null>(null)
  const [updatedAt, setUpdatedAt] = useState<Date | null>(null)

  const loadCycles = useCallback(async (signal: AbortSignal) => {
    try {
      const response = await fetch('/api/v1/analysis-cycles?limit=12', { signal })
      if (!response.ok) throw new Error(`分析时次接口响应 ${response.status}`)
      const page = await response.json() as AnalysisCyclePage
      setCycles(page.items)
      setSelectedID((current) => {
        if (current && page.items.some((item) => item.analysis_id === current)) return current
        return page.items.find((item) => item.status === 'ANALYSIS_READY')?.analysis_id
          ?? page.items[0]?.analysis_id
          ?? null
      })
      setError(null)
    } catch (requestError: unknown) {
      if (!(requestError instanceof DOMException && requestError.name === 'AbortError')) {
        setError(requestError instanceof Error ? requestError.message : '读取分析时次失败')
      }
    } finally {
      if (!signal.aborted) setLoading(false)
    }
  }, [])

  useEffect(() => {
    const controller = new AbortController()
    const kickoff = window.setTimeout(() => void loadCycles(controller.signal), 0)
    return () => {
      controller.abort()
      window.clearTimeout(kickoff)
    }
  }, [loadCycles, refreshToken])

  useEffect(() => {
    if (!selectedID) return
    const controller = new AbortController()
    const loadDetail = async () => {
      setLoading(true)
      try {
        const base = `/api/v1/analysis-cycles/${selectedID}`
        const responses = await Promise.all([
          fetch(base, { signal: controller.signal }),
          fetch(`${base}/mosaic-summary`, { signal: controller.signal }),
          fetch(`${base}/qpe-summary`, { signal: controller.signal }),
          fetch(`${base}/diagnostics`, { signal: controller.signal }),
        ])
        if (!responses[0].ok) throw new Error(`分析详情接口响应 ${responses[0].status}`)
        const [cycle, mosaicValue, qpeValue, diagnosticValue] = await Promise.all([
          responses[0].json() as Promise<AnalysisCycle>,
          responses[1].ok ? responses[1].json() as Promise<MosaicMetrics> : null,
          responses[2].ok ? responses[2].json() as Promise<QPEMetrics> : null,
          responses[3].ok ? responses[3].json() as Promise<DiagnosticBundle> : null,
        ])
        setSelected(cycle)
        setMosaic(mosaicValue)
        setQPE(qpeValue)
        setDiagnostics(diagnosticValue)
        setSelectedLayerID((current) => {
          const gridLayers = diagnosticValue?.layers.filter((item) => item.scope === 'grid') ?? []
          if (current && diagnosticValue?.layers.some((item) => item.layer_id === current)) {
            return current
          }
          return gridLayers.find((item) => item.field === 'RATE_QPE')?.layer_id
            ?? gridLayers[0]?.layer_id
            ?? null
        })
        setUpdatedAt(new Date())
        setError(null)
      } catch (requestError: unknown) {
        if (!(requestError instanceof DOMException && requestError.name === 'AbortError')) {
          setError(requestError instanceof Error ? requestError.message : '读取分析诊断失败')
        }
      } finally {
        if (!controller.signal.aborted) setLoading(false)
      }
    }
    void loadDetail()
    return () => controller.abort()
  }, [selectedID, refreshToken])

  const scopedLayers = useMemo(
    () => diagnostics?.layers.filter((layer) => layer.scope === scope) ?? [],
    [diagnostics, scope],
  )
  const activeLayer = diagnostics?.layers.find((item) => item.layer_id === selectedLayerID)
    ?? scopedLayers[0]
    ?? null
  const rawCompare = activeLayer?.scope === 'polar' && activeLayer.field === 'DBZH_QC'
    ? diagnostics?.layers.find((item) => item.scope === 'polar'
      && item.radar_id === activeLayer.radar_id && item.field === 'DBZH_RAW') ?? null
    : null

  const switchScope = (nextScope: 'grid' | 'polar') => {
    setScope(nextScope)
    const nextLayers = diagnostics?.layers.filter((item) => item.scope === nextScope) ?? []
    const preferred = nextScope === 'grid'
      ? nextLayers.find((item) => item.field === 'RATE_QPE')
      : nextLayers.find((item) => item.field === 'DBZH_QC')
    setSelectedLayerID(preferred?.layer_id ?? nextLayers[0]?.layer_id ?? null)
  }

  return (
    <section className="diagnostic-page" aria-labelledby="diagnostic-title">
      <header className="page-heading diagnostic-heading">
        <div>
          <p className="section-kicker">Analysis evidence / RP-012</p>
          <h1 id="diagnostic-title">分析诊断</h1>
          <p>沿着真实产物证据链检查质控、拼图、QPE、来源雷达与三态掩膜。</p>
        </div>
        <div className="update-time">
          <span>诊断更新时间</span>
          <strong>{updatedAt ? updatedAt.toLocaleTimeString('zh-CN', { hour12: false }) : '—'}</strong>
          <small>图层为不可变产物</small>
        </div>
      </header>

      {error ? <div className="error-banner" role="alert"><strong>诊断读取异常</strong><span>{error}</span></div> : null}

      <div className="analysis-layout">
        <aside className="cycle-panel" aria-label="分析时次列表">
          <div className="panel-heading">
            <div><p className="panel-label">UTC analysis cycles</p><h2>分析时次</h2></div>
            <span className="count-badge">{cycles.length}</span>
          </div>
          <div className="cycle-list">
            {loading && cycles.length === 0 ? <p className="empty-state">正在读取分析时次…</p> : null}
            {!loading && cycles.length === 0 ? <p className="empty-state">尚无分析时次</p> : null}
            {cycles.map((cycle) => (
              <button
                className={`cycle-row ${selectedID === cycle.analysis_id ? 'selected' : ''}`}
                key={cycle.analysis_id}
                type="button"
                aria-pressed={selectedID === cycle.analysis_id}
                onClick={() => setSelectedID(cycle.analysis_id)}
              >
                <span className={`cycle-state ${cycle.status.toLowerCase()}`} />
                <span><strong>{formatUtc(cycle.analysis_time)}</strong><small>{cycle.grid_id}</small></span>
                <span className="cycle-row-meta"><strong>{percent(cycle.valid_coverage_ratio)}</strong><small>{analysisStatusLabels[cycle.status]}</small></span>
              </button>
            ))}
          </div>
          <div className="cycle-note"><span>时间步长</span><strong>5 min · UTC</strong></div>
        </aside>

        <div className="analysis-workspace">
          {selected ? (
            <>
              <AnalysisEvidenceRail cycle={selected} diagnosticsReady={Boolean(diagnostics)} />

              {selected.degraded_reason ? (
                <div className="degradation-notice">
                  <div><span>Engineering only</span><strong>当前分析不可用于业务发布</strong></div>
                  <ul>{selected.degraded_reason.split(',').map((reason) => <li key={reason}>{reasonLabels[reason] ?? reason}</li>)}</ul>
                </div>
              ) : null}

              <div className="analysis-metrics" aria-label="分析质量摘要">
                <EvidenceMetric label="有效覆盖" value={percent(qpe?.valid_coverage_ratio ?? mosaic?.valid_coverage_ratio, 2)} note={`${qpe?.valid_cell_count?.toLocaleString('zh-CN') ?? '—'} 有效格点`} />
                <EvidenceMetric label="平均 QI" value={qpe?.mean_quality_index?.toFixed(3) ?? '—'} note={`${qpe?.low_quality_cell_count?.toLocaleString('zh-CN') ?? '—'} 低质量`} />
                <EvidenceMetric label="有雨格点" value={qpe?.rain_cell_count?.toLocaleString('zh-CN') ?? '—'} note={`${qpe?.no_rain_cell_count?.toLocaleString('zh-CN') ?? '—'} 有效无雨`} />
                <EvidenceMetric label="最大雨强" value={rate(qpe?.maximum_observed_rate_mm_h)} note={`P95 ${rate(qpe?.p95_rate_mm_h)}`} />
              </div>

              <div className="layer-workbench">
                <header className="layer-toolbar">
                  <div className="scope-switch" aria-label="诊断几何">
                    <button type="button" className={scope === 'grid' ? 'active' : ''} onClick={() => switchScope('grid')}>分析网格</button>
                    <button type="button" className={scope === 'polar' ? 'active' : ''} onClick={() => switchScope('polar')}>单雷达极坐标</button>
                  </div>
                  <div className="layer-tabs" aria-label="诊断图层">
                    {scopedLayers.map((layer) => (
                      <button
                        type="button"
                        key={layer.layer_id}
                        className={activeLayer?.layer_id === layer.layer_id ? 'active' : ''}
                        aria-pressed={activeLayer?.layer_id === layer.layer_id}
                        onClick={() => setSelectedLayerID(layer.layer_id)}
                      >{shortLayerTitle(layer)}</button>
                    ))}
                  </div>
                </header>

                <div className="visualization-layout">
                  <div className={`layer-stage ${scope}`}>
                    {diagnostics && activeLayer ? (
                      rawCompare ? (
                        <div className="compare-stage">
                          <LayerFigure layer={rawCompare} label="原始" />
                          <LayerFigure layer={activeLayer} label="质控后" />
                        </div>
                      ) : <LayerFigure layer={activeLayer} />
                    ) : (
                      <div className="layer-empty">
                        <strong>{loading ? '正在读取诊断图层' : '诊断图层尚未生成'}</strong>
                        <span>分析完成后由 Python Worker 原子发布透明 PNG。</span>
                      </div>
                    )}
                  </div>

                  <aside className="layer-ledger">
                    <div className="ledger-heading"><span>Active evidence</span><strong>{activeLayer?.title ?? '无可用图层'}</strong></div>
                    <p>{activeLayer ? fieldNotes[activeLayer.field] ?? '版本化诊断图层。' : '等待 DiagnosticBundle。'}</p>
                    {activeLayer ? (
                      <>
                        <dl className="layer-metadata">
                          <div><dt>字段</dt><dd>{activeLayer.field}</dd></div>
                          <div><dt>几何</dt><dd>{activeLayer.scope === 'grid' ? 'EPSG:4326' : `${activeLayer.radar_id?.toUpperCase()} PPI`}</dd></div>
                          <div><dt>尺寸</dt><dd>{activeLayer.width} × {activeLayer.height}</dd></div>
                          {activeLayer.elevation_deg != null ? <div><dt>仰角</dt><dd>{activeLayer.elevation_deg.toFixed(2)}°</dd></div> : null}
                          {activeLayer.maximum_range_km != null ? <div><dt>最大距离</dt><dd>{activeLayer.maximum_range_km.toFixed(1)} km</dd></div> : null}
                        </dl>
                        <div className="layer-legend">
                          {activeLayer.legend.map((item) => <span key={`${item.label}-${item.code ?? item.value ?? ''}`}><i style={{ backgroundColor: item.color }} />{item.label}</span>)}
                        </div>
                      </>
                    ) : null}
                  </aside>
                </div>
                <footer className="three-state-bar">
                  <span><i className="state-swatch valid" />有效</span>
                  <span><i className="state-swatch low" />低质量</span>
                  <span><i className="state-swatch missing" />透明棋盘 = 缺测</span>
                  <small>有效无雨仍是有值的 0 mm/h，不显示为透明</small>
                </footer>
              </div>

              <ContributorLedger cycle={selected} mosaic={mosaic} qpe={qpe} />
            </>
          ) : <p className="empty-state">请选择一个分析时次。</p>}
        </div>
      </div>
    </section>
  )
}

function AnalysisEvidenceRail({ cycle, diagnosticsReady }: { cycle: AnalysisCycle, diagnosticsReady: boolean }) {
  const stages = [
    ['RADARS', '雷达参与', cycle.radar_count > 0],
    ['ALIGN', '时间对齐', Boolean(cycle.mosaic_uri)],
    ['MOSAIC', '质量拼图', Boolean(cycle.mosaic_uri)],
    ['QPE', '基础 QPE', Boolean(cycle.analysis_uri)],
    ['VIEW', '诊断图层', diagnosticsReady],
  ] as const
  return (
    <div className="evidence-rail" aria-label="分析证据链">
      <div className="evidence-identity"><span>分析时次</span><strong>{formatUtc(cycle.analysis_time, true)}</strong><small>{cycle.analysis_id}</small></div>
      <ol>{stages.map(([code, label, complete], index) => <li className={complete ? 'complete' : 'pending'} key={code}><span>{String(index + 1).padStart(2, '0')}</span><div><small>{code}</small><strong>{label}</strong></div></li>)}</ol>
      <span className={`analysis-status ${cycle.status.toLowerCase()}`}>{analysisStatusLabels[cycle.status]}</span>
    </div>
  )
}

function LayerFigure({ layer, label }: { layer: DiagnosticLayer, label?: string }) {
  return (
    <figure className="layer-figure">
      {label ? <figcaption>{label}<small>{layer.field}</small></figcaption> : null}
      <div className="image-frame">
        <img src={layer.image_url} alt={`${layer.title}诊断图层`} />
        {layer.scope === 'polar' ? <div className="ppi-guides" aria-hidden="true"><i /><i /><span className="north">N</span><span className="east">E</span><span className="south">S</span><span className="west">W</span></div> : null}
      </div>
      {layer.scope === 'grid' && layer.bounds ? <div className="geo-bounds"><span>{layer.bounds[1].toFixed(3)}°N · {layer.bounds[0].toFixed(3)}°E</span><span>{layer.bounds[3].toFixed(3)}°N · {layer.bounds[2].toFixed(3)}°E</span></div> : null}
    </figure>
  )
}

function EvidenceMetric({ label, value, note }: { label: string, value: string, note: string }) {
  return <div><span>{label}</span><strong>{value}</strong><small>{note}</small></div>
}

function ContributorLedger({ cycle, mosaic, qpe }: { cycle: AnalysisCycle, mosaic: MosaicMetrics | null, qpe: QPEMetrics | null }) {
  return (
    <section className="contributor-ledger">
      <header><div><p className="panel-label">Provenance ledger</p><h2>来源与版本证据</h2></div><span>{cycle.radars.length} 部登记雷达</span></header>
      <div className="contributor-table" role="table" aria-label="分析贡献雷达">
        <div className="contributor-header" role="row"><span>雷达 / scan</span><span>状态</span><span>时差</span><span>平均 QI</span></div>
        {cycle.radars.map((radar) => <div role="row" key={radar.radar_id}><span><strong>{radar.radar_id.toUpperCase()}</strong><small>{radar.scan_id ?? '无匹配体扫'}</small></span><span className={`contributor-state ${radar.state.toLowerCase()}`}>{radar.state}</span><span>{radar.time_offset_seconds == null ? '—' : `${radar.time_offset_seconds} s`}</span><span>{radar.mean_quality_index?.toFixed(3) ?? '—'}</span></div>)}
      </div>
      <dl className="version-ledger">
        <div><dt>拼图</dt><dd>{mosaic ? `${mosaic.profile_version} · ${mosaic.algorithm_version}` : '—'}</dd></div>
        <div><dt>QPE</dt><dd>{qpe ? `${qpe.qpe_config_version} · ${qpe.qpe_algorithm_version}` : '—'}</dd></div>
        <div><dt>雨量站订正</dt><dd>{qpe?.gauge_adjustment_enabled ? '已启用' : '关闭（等待实况与 QC）'}</dd></div>
      </dl>
    </section>
  )
}

function shortLayerTitle(layer: DiagnosticLayer) {
  const parts = layer.title.split('·')
  return parts[parts.length - 1]?.trim() || layer.field
}
