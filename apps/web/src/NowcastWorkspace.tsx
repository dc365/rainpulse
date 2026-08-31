import {
  useCallback,
  useEffect,
  useMemo,
  useRef,
  useState,
  type FormEvent,
} from 'react'

import type { components } from './api/generated/schema'
import { NowcastMap } from './NowcastMap'
import { NowcastTimeline, type TimelineAsset } from './NowcastTimeline'

type ForecastRun = components['schemas']['ForecastRun']
type ForecastRunPage = components['schemas']['ForecastRunPage']
type AnalysisCycle = components['schemas']['AnalysisCycle']
type AnalysisCyclePage = components['schemas']['AnalysisCyclePage']
type AnalysisQPEMetrics = components['schemas']['AnalysisQPEMetrics']
type DiagnosticBundle = components['schemas']['DiagnosticBundle']
type Product = components['schemas']['Product']
type ProductAsset = components['schemas']['ProductAsset']
type ProductPage = components['schemas']['ProductPage']
type ProductType = components['schemas']['ProductType']
type PointForecast = components['schemas']['PointForecast']
type PointForecastValue = components['schemas']['PointForecastValue']
type AreaStatistics = components['schemas']['AreaStatistics']
type EnsembleProductBundle = components['schemas']['EnsembleProductBundle']
type EnsembleProductCycle = components['schemas']['EnsembleProductCycle']
type EnsembleProductLayer = components['schemas']['EnsembleProductLayer']
type EnsembleProductAsset = components['schemas']['EnsembleProductAsset']
type VerificationSummary = components['schemas']['VerificationSummary']

type SupportedProductType = 'rain_rate' | 'accumulation_60' | 'accumulation_120'
type DisplayMode = 'deterministic' | 'probability' | 'quantile'
type ProductSource = 'radar' | 'lk' | 'steps'
type DataMode = 'realtime' | 'historical'
type DisplayAsset = TimelineAsset & {
  asset_type: string
  content_url: string
  media_type: string
  sha256?: string
  size_bytes?: number
  unit?: string | null
  coverage_ratio?: number | null
  valid_cell_count?: number | null
  missing_cell_count?: number | null
}
type CycleItem = {
  id: string
  time: string
  gridID: string
  run?: ForecastRun
  analysis?: AnalysisCycle
  ensemble?: EnsembleProductCycle
}
type Coordinate = { longitude: number, latitude: number }
type GridBounds = { west: number, south: number, east: number, north: number }

const FUZHOU_GRID: GridBounds = { west: 118, south: 25, east: 123, north: 27 }
const FUZHOU_GRID_ID = 'fuzhou_118_123_25_27_0p01deg_v1'
const DEFAULT_POINT: Coordinate = { longitude: 119.3, latitude: 26.08 }
const DEFAULT_AREA = [119, 25.9, 119.6, 26.3] as const

const productLabels: Record<SupportedProductType, string> = {
  rain_rate: '瞬时雨强',
  accumulation_60: '1 小时累计',
  accumulation_120: '2 小时累计',
}

const productNotes: Record<SupportedProductType, string> = {
  rain_rate: '每 5 分钟一帧，单位 mm/h',
  accumulation_60: '起报后 0–1 小时累计，单位 mm',
  accumulation_120: '起报后 0–2 小时累计，单位 mm',
}

const rainRateLegend = [
  [0.1, '#9dd9ff'], [1, '#4ba3f2'], [2.5, '#2a79c7'],
  [5, '#3ca85b'], [10, '#9acb3c'], [25, '#efd23a'],
  [50, '#ee8a2d'], [100, '#cf453b'], [200, '#862f82'],
] as const

const rainfallAmountLegend = [
  [0.1, '#9dd9ff'], [0.5, '#4ba3f2'], [1, '#2a79c7'],
  [2.5, '#3ca85b'], [5, '#9acb3c'], [10, '#efd23a'],
  [25, '#ee8a2d'], [50, '#cf453b'], [100, '#862f82'],
] as const

const probabilityThresholds = [1, 5, 10, 20, 50] as const
const quantileValues = [0.1, 0.5, 0.9] as const

const areaPresets = [
  { label: '福州城区', bbox: [119, 25.9, 119.6, 26.3] },
  { label: '闽江口', bbox: [119.45, 25.8, 120.05, 26.25] },
  { label: '闽东沿海', bbox: [119.25, 26.2, 120.25, 26.85] },
] as const

function isSupportedProduct(type: ProductType): type is SupportedProductType {
  return type === 'rain_rate' || type === 'accumulation_60' || type === 'accumulation_120'
}

function formatUtc(value?: string | null, includeDate = false) {
  if (!value) return '暂无'
  const options: Intl.DateTimeFormatOptions = {
    timeZone: 'UTC',
    hour: '2-digit',
    minute: '2-digit',
    hour12: false,
  }
  if (includeDate) {
    options.month = '2-digit'
    options.day = '2-digit'
  }
  return `${new Intl.DateTimeFormat('zh-CN', options).format(new Date(value))} UTC`
}

function formatRunOption(value: string) {
  const timestamp = new Date(value)
  const local = new Intl.DateTimeFormat('zh-CN', {
    timeZone: 'Asia/Taipei',
    month: '2-digit',
    day: '2-digit',
    hour: '2-digit',
    minute: '2-digit',
    hour12: false,
  }).format(timestamp)
  const utc = new Intl.DateTimeFormat('zh-CN', {
    timeZone: 'UTC',
    month: '2-digit',
    day: '2-digit',
    hour: '2-digit',
    minute: '2-digit',
    hour12: false,
  }).format(timestamp)
  return `${local} CST · ${utc} UTC`
}

function cycleID(time: string, gridID: string) {
  return `${gridID}:${new Date(time).toISOString()}`
}

function buildCycleCatalog(
  runs: ForecastRun[],
  analyses: AnalysisCycle[],
  ensembles: EnsembleProductCycle[],
) {
  const cycles = new Map<string, CycleItem>()
  runs.forEach((run) => {
    const id = cycleID(run.issue_time, run.grid_id)
    const existing = cycles.get(id)
    cycles.set(id, {
      id,
      time: run.issue_time,
      gridID: run.grid_id,
      run: existing?.run ?? run,
      analysis: existing?.analysis,
      ensemble: existing?.ensemble,
    })
  })
  analyses.forEach((analysis) => {
    const id = cycleID(analysis.analysis_time, analysis.grid_id)
    const existing = cycles.get(id)
    cycles.set(id, {
      id,
      time: analysis.analysis_time,
      gridID: analysis.grid_id,
      run: existing?.run,
      analysis,
      ensemble: existing?.ensemble,
    })
  })
  ensembles.forEach((ensemble) => {
    const id = cycleID(ensemble.issue_time, ensemble.grid_id)
    const existing = cycles.get(id)
    cycles.set(id, {
      id,
      time: ensemble.issue_time,
      gridID: ensemble.grid_id,
      run: existing?.run,
      analysis: existing?.analysis,
      ensemble,
    })
  })
  return Array.from(cycles.values())
    .sort((left, right) => Date.parse(right.time) - Date.parse(left.time))
}

function TimeCatalogPicker({
  dataMode,
  items,
  selectedCycleID,
  realtimeFresh,
  onSelect,
}: {
  dataMode: DataMode
  items: CycleItem[]
  selectedCycleID: string | null
  realtimeFresh: boolean
  onSelect: (value: 'realtime' | string) => void
}) {
  const [open, setOpen] = useState(false)
  const rootRef = useRef<HTMLDivElement>(null)
  const latestForecast = items.find((item) => item.run != null) ?? null
  const selected = dataMode === 'historical'
    ? items.find((item) => item.id === selectedCycleID) ?? null
    : latestForecast
  const selectedValue = dataMode === 'realtime' ? 'realtime' : selected?.id ?? ''
  const selectedKind = dataMode === 'realtime' ? '实时周期' : '历史周期'

  useEffect(() => {
    if (!open) return
    const closeOutside = (event: PointerEvent) => {
      if (!rootRef.current?.contains(event.target as Node)) setOpen(false)
    }
    const closeWithEscape = (event: KeyboardEvent) => {
      if (event.key === 'Escape') setOpen(false)
    }
    document.addEventListener('pointerdown', closeOutside)
    document.addEventListener('keydown', closeWithEscape)
    return () => {
      document.removeEventListener('pointerdown', closeOutside)
      document.removeEventListener('keydown', closeWithEscape)
    }
  }, [open])

  const choose = (value: 'realtime' | string) => {
    onSelect(value)
    setOpen(false)
  }

  return (
    <div className="forecast-time-control" ref={rootRef}>
      <span className="forecast-control-label">时间</span>
      <button
        type="button"
        className="time-picker-trigger"
        aria-label="选择数据时次"
        aria-haspopup="listbox"
        aria-expanded={open}
        onClick={() => setOpen((value) => !value)}
      >
        <i className={dataMode === 'realtime' && realtimeFresh ? 'fresh' : ''} aria-hidden="true" />
        <span><small>{selectedKind}</small><strong>{selected ? formatRunOption(selected.time) : '暂无可用时次'}</strong></span>
        <b aria-hidden="true">⌄</b>
      </button>
      {open ? (
        <div className="time-picker-popover" role="listbox" aria-label="数据时次">
          <header><strong>选择分析周期</strong><small>每个周期都是当时的完整实时工作台</small></header>
          <button
            type="button"
            role="option"
            aria-selected={selectedValue === 'realtime'}
            className={`time-picker-option realtime${selectedValue === 'realtime' ? ' active' : ''}`}
            disabled={!latestForecast}
            onClick={() => choose('realtime')}
          >
            <i className={realtimeFresh ? 'fresh' : ''} aria-hidden="true" />
            <span><strong>实时跟随</strong><small>{realtimeFresh ? '跟随最新正式周期' : '当前无实时更新，显示最新正式周期'}</small></span>
            <time>{latestForecast ? formatRunOption(latestForecast.time) : '暂无'}</time>
          </button>
          <p>历史周期</p>
          <div className="time-picker-history">
            {items.map((item) => (
              <button
                type="button"
                role="option"
                aria-selected={selectedValue === item.id}
                className={`time-picker-option${selectedValue === item.id ? ' active' : ''}`}
                key={item.id}
                onClick={() => choose(item.id)}
              >
                <div className="cycle-capabilities" aria-label="周期可用产品">
                  <em data-available={item.analysis != null}>实况</em>
                  <em data-available={item.run != null}>LK</em>
                  <em data-available={item.ensemble != null}>STEPS</em>
                </div>
                <span><strong>{formatRunOption(item.time)}</strong><small>{item.analysis && item.run ? '实况与预报同周期' : item.analysis ? '当前仅有雷达实况' : '当前有正式预报'}</small></span>
              </button>
            ))}
          </div>
        </div>
      ) : null}
    </div>
  )
}

function formatLead(value?: number | null) {
  return value == null ? '暂无' : `T+${value}`
}

function percent(value?: number | null) {
  return value == null ? '暂无' : `${(value * 100).toFixed(1)}%`
}

function formatCoordinate(value: number) {
  return value.toFixed(2)
}

export function NowcastWorkspace({ refreshToken }: { refreshToken: number }) {
  const [run, setRun] = useState<ForecastRun | null>(null)
  const [analysis, setAnalysis] = useState<AnalysisCycle | null>(null)
  const [analysisQPE, setAnalysisQPE] = useState<AnalysisQPEMetrics | null>(null)
  const [analysisAsset, setAnalysisAsset] = useState<DisplayAsset | null>(null)
  const [dataMode, setDataMode] = useState<DataMode>('realtime')
  const [availableCycles, setAvailableCycles] = useState<CycleItem[]>([])
  const [selectedCycleID, setSelectedCycleID] = useState<string | null>(null)
  const [products, setProducts] = useState<Product[]>([])
  const [assets, setAssets] = useState<Record<string, ProductAsset[]>>({})
  const [productType, setProductType] = useState<SupportedProductType>('rain_rate')
  const [productSource, setProductSource] = useState<ProductSource>('lk')
  const [displayMode, setDisplayMode] = useState<DisplayMode>('deterministic')
  const [ensembleBundle, setEnsembleBundle] = useState<EnsembleProductBundle | null>(null)
  const [verification, setVerification] = useState<VerificationSummary | null>(null)
  const [probabilityThreshold, setProbabilityThreshold] = useState(5)
  const [quantileValue, setQuantileValue] = useState(0.5)
  const [selectedLead, setSelectedLead] = useState<number | null>(null)
  const [loading, setLoading] = useState(true)
  const [error, setError] = useState<string | null>(null)
  const [layerError, setLayerError] = useState(false)
  const [updatedAt, setUpdatedAt] = useState<Date | null>(null)

  const [point, setPoint] = useState<Coordinate>(DEFAULT_POINT)
  const [pointDraft, setPointDraft] = useState({
    longitude: String(DEFAULT_POINT.longitude),
    latitude: String(DEFAULT_POINT.latitude),
  })
  const [pointForecast, setPointForecast] = useState<PointForecast | null>(null)
  const [pointLoading, setPointLoading] = useState(false)
  const [pointError, setPointError] = useState<string | null>(null)

  const [bbox, setBbox] = useState<number[]>([...DEFAULT_AREA])
  const [bboxDraft, setBboxDraft] = useState(DEFAULT_AREA.map(String))
  const [areaStatistics, setAreaStatistics] = useState<AreaStatistics | null>(null)
  const [areaLoading, setAreaLoading] = useState(false)
  const [areaError, setAreaError] = useState<string | null>(null)

  const [drawerOpen, setDrawerOpen] = useState(false)
  const [drawerTab, setDrawerTab] = useState<'point' | 'area' | 'provenance'>('point')
  const productSourceRef = useRef(productSource)

  useEffect(() => {
    productSourceRef.current = productSource
  }, [productSource])

  useEffect(() => {
    const controller = new AbortController()
    const load = async () => {
      setLoading(true)
      try {
        const [runPages, analysisPage, ensembleCycles] = await Promise.all([
          Promise.all(['PUBLISHED', 'VERIFYING', 'VERIFIED'].map(async (status) => {
            const response = await fetch(`/api/v1/runs?status=${status}&limit=50`, {
              signal: controller.signal,
            })
            if (!response.ok) throw new Error(`预报运行目录接口响应 ${response.status}`)
            return await response.json() as ForecastRunPage
          })),
          fetch('/api/v1/analysis-cycles?status=ANALYSIS_READY&limit=200', {
            signal: controller.signal,
          }).then(async (response) => {
            if (!response.ok) return { items: [] } as AnalysisCyclePage
            return await response.json() as AnalysisCyclePage
          }),
          fetch('/api/v1/ensemble-products/cycles', {
            signal: controller.signal,
          }).then(async (response) => {
            if (!response.ok) return [] as EnsembleProductCycle[]
            const candidate = await response.json() as unknown
            return Array.isArray(candidate) ? candidate as EnsembleProductCycle[] : []
          }),
        ])
        const forecastCatalog = Array.from(
          new Map(runPages.flatMap((page) => page.items)
            .map((item) => [item.run_id, item])).values(),
        ).sort((left, right) => Date.parse(right.issue_time) - Date.parse(left.issue_time))
        const analysisCatalog = (Array.isArray(analysisPage.items) ? analysisPage.items : [])
          .filter((item) => item.grid_id === FUZHOU_GRID_ID
            && item.radar_count >= 2
            && item.analysis_uri != null)
          .sort((left, right) => Date.parse(right.analysis_time) - Date.parse(left.analysis_time))
        const cycleCatalog = buildCycleCatalog(forecastCatalog, analysisCatalog, ensembleCycles)
        const selected = dataMode === 'historical'
          ? cycleCatalog.find((item) => item.id === selectedCycleID) ?? cycleCatalog[0]
          : cycleCatalog.find((item) => item.run != null) ?? null
        if (!selected) {
          throw new Error(dataMode === 'historical'
            ? '暂无可展示的分析周期'
            : '暂无已发布或已检验的正式周期')
        }
        setAvailableCycles(cycleCatalog)
        if (dataMode === 'historical' && selectedCycleID !== selected.id) {
          setSelectedCycleID(selected.id)
        }

        let nextAnalysisAsset: DisplayAsset | null = null
        let nextAnalysisQPE: AnalysisQPEMetrics | null = null
        if (selected.analysis) {
          const [diagnosticResponse, qpeResponse] = await Promise.all([
            fetch(`/api/v1/analysis-cycles/${selected.analysis.analysis_id}/diagnostics`, {
              signal: controller.signal,
            }),
            fetch(`/api/v1/analysis-cycles/${selected.analysis.analysis_id}/qpe-summary`, {
              signal: controller.signal,
            }),
          ])
          if (!diagnosticResponse.ok) {
            throw new Error(`雷达分析图层接口响应 ${diagnosticResponse.status}`)
          }
          if (!qpeResponse.ok) throw new Error(`雷达 QPE 接口响应 ${qpeResponse.status}`)
          const diagnostic = await diagnosticResponse.json() as DiagnosticBundle
          const qpe = await qpeResponse.json() as AnalysisQPEMetrics
          const rateLayer = diagnostic.layers.find((item) =>
            item.scope === 'grid' && item.field === 'RATE_QPE')
          if (!rateLayer) throw new Error('当前雷达分析缺少瞬时雨强图层')
          nextAnalysisQPE = qpe
          nextAnalysisAsset = {
            asset_id: `${selected.analysis.analysis_id}:grid-rate-qpe`,
            asset_type: 'rendered_png',
            content_url: rateLayer.image_url,
            media_type: 'image/png',
            lead_time_minutes: 0,
            valid_time: selected.analysis.analysis_time,
            unit: rateLayer.unit,
            coverage_ratio: qpe.valid_coverage_ratio,
            valid_cell_count: qpe.valid_cell_count,
            missing_cell_count: qpe.missing_cell_count,
          }
        }

        const selectedRun = selected.run
        let supported: Product[] = []
        let nextAssets: Record<string, ProductAsset[]> = {}
        if (selectedRun) {
          const productResponse = await fetch(
            `/api/v1/products?run_id=${encodeURIComponent(selectedRun.run_id)}`,
            { signal: controller.signal },
          )
          if (!productResponse.ok) throw new Error(`产品目录接口响应 ${productResponse.status}`)
          const page = await productResponse.json() as ProductPage
          supported = page.items.filter((item) => isSupportedProduct(item.product_type))
        }
        const assetPairs = await Promise.all(supported.map(async (product) => {
          const response = await fetch(`/api/v1/products/${product.product_id}/assets`, {
            signal: controller.signal,
          })
          if (!response.ok) throw new Error(`产品资产接口响应 ${response.status}`)
          return [product.product_id, await response.json() as ProductAsset[]] as const
        }))
        nextAssets = Object.fromEntries(assetPairs)
        let cycleEnsemble: EnsembleProductBundle | null = null
        if (selected.ensemble) {
          try {
            const query = new URLSearchParams({
              issue_time: new Date(selected.time).toISOString(),
              grid_id: selected.gridID,
            })
            const ensembleResponse = await fetch(`/api/v1/ensemble-products/by-cycle?${query}`, {
              signal: controller.signal,
            })
            if (ensembleResponse.ok) {
              const candidate = await ensembleResponse.json() as unknown
              if (isEnsembleBundle(candidate)) cycleEnsemble = candidate
            }
          } catch (ensembleRequestError: unknown) {
            if (ensembleRequestError instanceof DOMException
              && ensembleRequestError.name === 'AbortError') throw ensembleRequestError
          }
        }
        let latestVerification: VerificationSummary | null = null
        try {
          if (!selectedRun) throw new Error('skip verification without forecast run')
          const verificationResponse = await fetch(
            `/api/v1/verification/summary?run_id=${encodeURIComponent(selectedRun.run_id)}`,
            { signal: controller.signal },
          )
          if (verificationResponse.ok) {
            const candidate = await verificationResponse.json() as unknown
            if (isVerificationSummary(candidate)) latestVerification = candidate
          }
        } catch (verificationRequestError: unknown) {
          if (verificationRequestError instanceof DOMException
            && verificationRequestError.name === 'AbortError') throw verificationRequestError
        }
        const preferred = supported.find((item) => item.product_type === 'rain_rate')
          ?? supported[0]
          ?? null
        const firstLKPNG = preferred
          ? nextAssets[preferred.product_id]
            ?.filter((item) => item.asset_type === 'rendered_png')
            .sort(sortAssets)[0]
          : null
        const defaultEnsembleLayer = cycleEnsemble?.layers.find((layer) =>
          layer.product_type === 'probability_exceedance' && layer.threshold_mm_h === 5)
        const firstStepsPNG = defaultEnsembleLayer?.assets
          .filter((item) => item.asset_type === 'rendered_png')
          .sort(sortAssets)[0]
        const sourceAvailability: Record<ProductSource, boolean> = {
          radar: nextAnalysisAsset != null,
          lk: firstLKPNG != null,
          steps: firstStepsPNG != null,
        }
        const desiredSource = productSourceRef.current
        const nextSource: ProductSource = sourceAvailability[desiredSource]
          ? desiredSource
          : sourceAvailability.lk ? 'lk'
          : sourceAvailability.radar ? 'radar'
          : sourceAvailability.steps ? 'steps'
          : 'radar'

        setRun(selectedRun ?? null)
        setAnalysis(selected.analysis ?? null)
        setAnalysisQPE(nextAnalysisQPE)
        setAnalysisAsset(nextAnalysisAsset)
        setProducts(supported)
        setAssets(nextAssets)
        setEnsembleBundle(cycleEnsemble)
        setVerification(latestVerification)
        setProductSource(nextSource)
        setDisplayMode(nextSource === 'steps' ? 'probability' : 'deterministic')
        if (preferred && isSupportedProduct(preferred.product_type)) {
          setProductType(preferred.product_type)
        } else {
          setProductType('rain_rate')
        }
        setProbabilityThreshold(5)
        setSelectedLead(nextSource === 'radar'
          ? 0
          : nextSource === 'steps'
            ? firstStepsPNG?.lead_time_minutes ?? null
            : firstLKPNG?.lead_time_minutes ?? null)
        if (nextSource === 'radar') {
          setPointForecast(null)
          setAreaStatistics(null)
          setPointError(null)
          setAreaError(null)
          setDrawerTab('provenance')
          setDrawerOpen(false)
        }
        setUpdatedAt(new Date())
        setError(null)
      } catch (requestError: unknown) {
        if (!(requestError instanceof DOMException && requestError.name === 'AbortError')) {
          setError(requestError instanceof Error ? requestError.message : '读取短临产品失败')
        }
      } finally {
        if (!controller.signal.aborted) setLoading(false)
      }
    }
    void load()
    return () => controller.abort()
  }, [dataMode, refreshToken, selectedCycleID])

  const selectedProduct = useMemo(
    () => products.find((item) => item.product_type === productType) ?? null,
    [productType, products],
  )
  const rainProduct = useMemo(
    () => products.find((item) => item.product_type === 'rain_rate') ?? null,
    [products],
  )
  const selectedEnsembleLayer = useMemo(
    () => ensembleBundle?.layers.find((layer) => displayMode === 'probability'
      ? layer.product_type === 'probability_exceedance'
        && layer.threshold_mm_h === probabilityThreshold
      : displayMode === 'quantile'
        && layer.product_type === 'quantile'
        && layer.quantile === quantileValue) ?? null,
    [displayMode, ensembleBundle, probabilityThreshold, quantileValue],
  )
  const deterministicRenderedAssets = useMemo(
    () => selectedProduct
      ? (assets[selectedProduct.product_id] ?? [])
        .filter((item) => item.asset_type === 'rendered_png')
        .map(toDisplayAsset)
        .sort(sortAssets)
      : [],
    [assets, selectedProduct],
  )
  const ensembleRenderedAssets = useMemo(
    () => (selectedEnsembleLayer?.assets ?? [])
      .filter((item) => item.asset_type === 'rendered_png')
      .map(toDisplayAsset)
      .sort(sortAssets),
    [selectedEnsembleLayer],
  )
  const renderedAssets = productSource === 'radar'
    ? analysisAsset ? [analysisAsset] : []
    : productSource === 'lk' ? deterministicRenderedAssets : ensembleRenderedAssets
  const selectedAsset = renderedAssets.find((item) => item.lead_time_minutes === selectedLead)
    ?? renderedAssets[0]
    ?? null
  const currentLead = selectedAsset?.lead_time_minutes ?? null
  const currentAssets = useMemo(
    () => productSource === 'radar'
      ? analysisAsset ? [analysisAsset] : []
      : productSource === 'lk'
      ? selectedProduct
        ? (assets[selectedProduct.product_id] ?? [])
          .filter((item) => item.lead_time_minutes === currentLead
            && item.asset_type !== 'point_query_index')
          .map(toDisplayAsset)
        : []
      : (selectedEnsembleLayer?.assets ?? [])
        .filter((item) => item.lead_time_minutes === currentLead)
        .map(toDisplayAsset),
    [analysisAsset, assets, currentLead, productSource, selectedEnsembleLayer, selectedProduct],
  )

  useEffect(() => {
    if (!rainProduct || productSource !== 'lk') return
    const controller = new AbortController()
    const loadPoint = async () => {
      setPointLoading(true)
      setPointForecast(null)
      try {
        const query = new URLSearchParams({
          product_id: rainProduct.product_id,
          longitude: String(point.longitude),
          latitude: String(point.latitude),
        })
        const response = await fetch(`/api/v1/point-forecast?${query}`, {
          signal: controller.signal,
        })
        if (!response.ok) throw new Error(`点预报接口响应 ${response.status}`)
        setPointForecast(await response.json() as PointForecast)
        setPointError(null)
      } catch (requestError: unknown) {
        if (!(requestError instanceof DOMException && requestError.name === 'AbortError')) {
          setPointError(requestError instanceof Error ? requestError.message : '读取点预报失败')
        }
      } finally {
        if (!controller.signal.aborted) setPointLoading(false)
      }
    }
    void loadPoint()
    return () => controller.abort()
  }, [point, productSource, rainProduct])

  useEffect(() => {
    if (!rainProduct || currentLead == null || productSource !== 'lk') return
    const controller = new AbortController()
    const loadArea = async () => {
      setAreaLoading(true)
      try {
        const query = new URLSearchParams({
          product_id: rainProduct.product_id,
          bbox: bbox.join(','),
          lead_time_minutes: String(currentLead),
        })
        const response = await fetch(`/api/v1/area-statistics?${query}`, {
          signal: controller.signal,
        })
        if (!response.ok) throw new Error(`区域统计接口响应 ${response.status}`)
        setAreaStatistics(await response.json() as AreaStatistics)
        setAreaError(null)
      } catch (requestError: unknown) {
        if (!(requestError instanceof DOMException && requestError.name === 'AbortError')) {
          setAreaError(requestError instanceof Error ? requestError.message : '读取区域统计失败')
        }
      } finally {
        if (!controller.signal.aborted) setAreaLoading(false)
      }
    }
    void loadArea()
    return () => controller.abort()
  }, [bbox, currentLead, productSource, rainProduct])

  const switchProduct = (nextType: SupportedProductType) => {
    const nextProduct = products.find((item) => item.product_type === nextType)
    if (!nextProduct) return
    const firstFrame = (assets[nextProduct.product_id] ?? [])
      .filter((item) => item.asset_type === 'rendered_png')
      .sort(sortAssets)[0]
    setDisplayMode('deterministic')
    setProductSource('lk')
    setProductType(nextType)
    setSelectedLead(firstFrame?.lead_time_minutes ?? null)
    setLayerError(false)
  }

  const switchEnsembleLayer = (
    nextMode: Exclude<DisplayMode, 'deterministic'>,
    nextThreshold = probabilityThreshold,
    nextQuantile = quantileValue,
  ) => {
    if (!ensembleBundle) return
    const layer = ensembleBundle.layers.find((item) => nextMode === 'probability'
      ? item.product_type === 'probability_exceedance'
        && item.threshold_mm_h === nextThreshold
      : item.product_type === 'quantile' && item.quantile === nextQuantile)
    if (!layer) return
    const frames = layer.assets
      .filter((item) => item.asset_type === 'rendered_png')
      .sort(sortAssets)
    setDisplayMode(nextMode)
    setProductSource('steps')
    setProbabilityThreshold(nextThreshold)
    setQuantileValue(nextQuantile)
    setSelectedLead((current) => frames.some((item) => item.lead_time_minutes === current)
      ? current
      : frames[0]?.lead_time_minutes ?? null)
    setDrawerOpen(false)
    setLayerError(false)
  }

  const switchSource = (source: ProductSource) => {
    if (source === 'radar') {
      if (!analysisAsset) return
      setProductSource('radar')
      setDisplayMode('deterministic')
      setProductType('rain_rate')
      setSelectedLead(0)
      setDrawerOpen(false)
    } else if (source === 'lk') {
      switchProduct(productType)
    } else {
      switchEnsembleLayer(displayMode === 'quantile' ? 'quantile' : 'probability')
    }
    setLayerError(false)
  }

  const openDrawer = (tab: 'point' | 'area' | 'provenance') => {
    setDrawerTab(tab)
    setDrawerOpen(true)
  }

  const selectPoint = useCallback((nextPoint: Coordinate) => {
    const normalized = {
      longitude: Number(nextPoint.longitude.toFixed(2)),
      latitude: Number(nextPoint.latitude.toFixed(2)),
    }
    setPointDraft({
      longitude: String(normalized.longitude),
      latitude: String(normalized.latitude),
    })
    setPoint(normalized)
  }, [])

  const selectTimelineAsset = useCallback((asset: TimelineAsset) => {
    setSelectedLead(asset.lead_time_minutes ?? null)
    setLayerError(false)
  }, [])

  const submitPoint = (event: FormEvent) => {
    event.preventDefault()
    const longitude = Number(pointDraft.longitude)
    const latitude = Number(pointDraft.latitude)
    if (!Number.isFinite(longitude) || !Number.isFinite(latitude)
      || longitude < FUZHOU_GRID.west || longitude > FUZHOU_GRID.east
      || latitude < FUZHOU_GRID.south || latitude > FUZHOU_GRID.north) {
      setPointError('经纬度必须位于 118–123°E、25–27°N')
      return
    }
    selectPoint({ longitude, latitude })
  }

  const submitArea = (event: FormEvent) => {
    event.preventDefault()
    const values = bboxDraft.map(Number)
    if (values.some((value) => !Number.isFinite(value))
      || values[0] < FUZHOU_GRID.west || values[2] > FUZHOU_GRID.east
      || values[1] < FUZHOU_GRID.south || values[3] > FUZHOU_GRID.north
      || values[0] >= values[2] || values[1] >= values[3]) {
      setAreaError('区域必须位于当前网格内，且西南角小于东北角')
      return
    }
    setBbox(values)
  }

  const applyAreaPreset = (nextBbox: readonly number[]) => {
    setBboxDraft(nextBbox.map(String))
    setBbox([...nextBbox])
  }

  const currentPointValue = pointForecast?.values.find(
    (value) => value.lead_time_minutes === currentLead,
  ) ?? pointForecast?.values[0] ?? null
  const hasCurrentPointValue = currentPointValue?.valid === true
    && currentPointValue.rain_rate != null
  const analysisActive = productSource === 'radar'
  const ensembleActive = productSource === 'steps'
  const lkActive = productSource === 'lk'
  const radarAvailable = analysisAsset != null
  const lkAvailable = deterministicRenderedAssets.length > 0
  const stepsAvailable = ensembleBundle?.layers.some((layer) =>
    layer.assets.some((asset) => asset.asset_type === 'rendered_png')) === true
  const currentProductLabel = analysisActive
    ? '雷达瞬时雨强'
    : displayMode === 'probability'
    ? `超过 ${probabilityThreshold} mm/h 概率`
    : displayMode === 'quantile'
      ? `P${Math.round(quantileValue * 100)} 雨强分位数`
      : productLabels[productType]
  const currentProductNote = analysisActive
    ? '质控、拼图后的雷达 QPE 分析，非未来预报'
    : displayMode === 'probability'
    ? '有效时刻瞬时雨强超阈值的原始集合相对频率'
    : displayMode === 'quantile'
      ? '有效时刻瞬时雨强的集合成员分位数'
      : productNotes[productType]
  const legend = displayMode === 'probability'
    ? (selectedEnsembleLayer?.legend ?? []).map(
      (entry) => [entry.minimum * 100, entry.color] as const,
    )
    : displayMode === 'quantile'
      ? (selectedEnsembleLayer?.legend ?? []).map(
        (entry) => [entry.minimum, entry.color] as const,
      )
      : productType === 'rain_rate' ? rainRateLegend : rainfallAmountLegend
  const selectedCycle = dataMode === 'historical'
    ? availableCycles.find((item) => item.id === selectedCycleID) ?? null
    : availableCycles.find((item) => item.run != null) ?? null
  const issueTime = selectedCycle?.time
  const latestForecastItem = availableCycles.find((item) => item.run != null) ?? null
  const realtimeAgeMs = latestForecastItem != null && updatedAt != null
    ? updatedAt.getTime() - Date.parse(latestForecastItem.time)
    : null
  const realtimeFresh = realtimeAgeMs != null && realtimeAgeMs >= 0
    && realtimeAgeMs <= 15 * 60 * 1000

  const selectCatalogTime = (value: 'realtime' | string) => {
    if (value === 'realtime') {
      setDataMode('realtime')
      setSelectedCycleID(null)
    } else {
      setDataMode('historical')
      setSelectedCycleID(value)
    }
    setLayerError(false)
  }

  return (
    <section className="forecast-page" aria-labelledby="forecast-title">
      <header className="page-heading forecast-heading">
        <div>
          <p className="section-kicker">工程验证 / RP-023</p>
          <h1 id="forecast-title">0–2 小时降水预报</h1>
          <p>当前产品用于工程回放与检验；业务可用性由上游质量门控和实况评分共同决定。</p>
        </div>
        <div className="update-time">
          <span>产品目录更新</span>
          <strong>{updatedAt ? updatedAt.toLocaleTimeString('zh-CN', { hour12: false }) : '暂无'}</strong>
          <small>起报时次显示 CST 与 UTC</small>
        </div>
      </header>

      {error ? <div className="error-banner" role="alert"><strong>产品读取异常</strong><span>{error}</span></div> : null}

      <section className="forecast-stage">
        <div className="forecast-mode-bar" aria-label="数据时次、预报模型与集合产品选择">
          <TimeCatalogPicker
            dataMode={dataMode}
            items={availableCycles}
            selectedCycleID={selectedCycleID}
            realtimeFresh={realtimeFresh}
            onSelect={selectCatalogTime}
          />
          <div className="forecast-mode-switch source-switch" role="group" aria-label="周期产品">
            <span>产品</span>
            <button
              type="button"
              className={analysisActive ? 'active' : ''}
              aria-pressed={analysisActive}
              disabled={!radarAvailable}
              title={radarAvailable ? '质控、拼图后的雷达 QPE 实况' : '该周期未生成雷达 QPE'}
              onClick={() => switchSource('radar')}
            >雷达实况</button>
            <button
              type="button"
              className={lkActive ? 'active' : ''}
              aria-pressed={lkActive}
              disabled={!lkAvailable}
              title={lkAvailable ? 'LK 确定性 0–2 小时预报' : '该周期未生成 LK 预报'}
              onClick={() => switchSource('lk')}
            >LK 确定性</button>
            <button
              type="button"
              className={ensembleActive ? 'active' : ''}
              aria-pressed={ensembleActive}
              disabled={!stepsAvailable}
              title={stepsAvailable ? 'STEPS 集合概率与分位数' : '该周期未生成 STEPS 集合产品'}
              onClick={() => switchSource('steps')}
            >STEPS 集合</button>
          </div>
          {ensembleActive && ensembleBundle ? (
            <div className="ensemble-layer-switch" aria-label="离线集合图层">
              <div role="group" aria-label="超阈概率">
                <span>超阈概率</span>
                {probabilityThresholds.map((threshold) => (
                  <button
                    key={threshold}
                    type="button"
                    className={displayMode === 'probability'
                      && probabilityThreshold === threshold ? 'active' : ''}
                    aria-pressed={displayMode === 'probability'
                      && probabilityThreshold === threshold}
                    onClick={() => switchEnsembleLayer('probability', threshold)}
                  >&gt;{threshold}</button>
                ))}
              </div>
              <div role="group" aria-label="雨强分位数">
                <span>分位数</span>
                {quantileValues.map((quantile) => (
                  <button
                    key={quantile}
                    type="button"
                    className={displayMode === 'quantile'
                      && quantileValue === quantile ? 'active' : ''}
                    aria-pressed={displayMode === 'quantile' && quantileValue === quantile}
                    onClick={() => switchEnsembleLayer('quantile', probabilityThreshold, quantile)}
                  >P{Math.round(quantile * 100)}</button>
                ))}
              </div>
            </div>
          ) : (
            <div className="cycle-availability" aria-label="当前周期产品可用性">
              <span data-available={radarAvailable}>实况 {radarAvailable ? '可用' : '未生成'}</span>
              <span data-available={lkAvailable}>LK {lkAvailable ? '可用' : '未生成'}</span>
              <span data-available={stepsAvailable}>STEPS {stepsAvailable ? '可用' : '未生成'}</span>
            </div>
          )}
        </div>
        <div className="forecast-map-host">
          <NowcastMap
            imageUrl={selectedAsset?.content_url}
            imageDescription={analysisActive
              ? `${issueTime ? formatRunOption(issueTime) : '当前周期'} 雷达 QPE 瞬时雨强图层`
              : displayLayerAlt(displayMode, productType, currentLead, currentProductLabel)}
            validTimeLabel={formatUtc(selectedAsset?.valid_time)}
            leadLabel={analysisActive ? '雷达分析' : formatLead(currentLead)}
            productLabel={currentProductLabel}
            legend={legend}
            legendUnit={displayMode === 'probability'
              ? '%'
              : productType === 'rain_rate' || displayMode === 'quantile' ? 'mm/h' : 'mm'}
            footerNote={displayMode === 'probability'
              ? '透明含缺测与 <1%'
              : '透明含缺测与 <0.1'}
            point={point}
            emptyStateHint={ensembleActive && !selectedAsset
              ? '当前离线集合包缺少所选图层'
              : run?.status === 'FAILED'
              ? `当前起报 ${formatUtc(run.issue_time, true)} 发布失败，等待下一次起报`
              : undefined}
            bbox={bbox}
            loading={loading}
            layerError={layerError}
            onLayerError={setLayerError}
            onSelectPoint={selectPoint}
            picker={lkActive ? (
              <div className="gis-picker">
                {hasCurrentPointValue ? <strong>{formatRate(currentPointValue)}</strong> : null}
                <span>{formatCoordinate(point.longitude)}°E  {formatCoordinate(point.latitude)}°N</span>
                {hasCurrentPointValue
                  ? <button type="button" onClick={() => openDrawer('point')}>展开单点曲线</button>
                  : null}
              </div>
            ) : undefined}
          />

          <div className="stage-float stage-products" role="group" aria-label="降水产品">
            {analysisActive ? (
              <button type="button" className="active" aria-pressed="true" disabled>
                <strong>瞬时雨强</strong>
              </button>
            ) : ensembleActive ? (
              <button type="button" className="active" aria-pressed="true" disabled>
                <strong>{currentProductLabel}</strong>
              </button>
            ) : (Object.keys(productLabels) as SupportedProductType[]).map((type) => {
                const available = products.some((item) => item.product_type === type)
                return (
                  <button
                    key={type}
                    type="button"
                    className={productType === type ? 'active' : ''}
                    aria-pressed={productType === type}
                    disabled={!available}
                    title={productNotes[type]}
                    onClick={() => switchProduct(type)}
                  >
                    <strong>{productLabels[type]}</strong>
                  </button>
                )
              })}
            <small className="stage-products-note">{currentProductNote}</small>
          </div>

          <div className="stage-float stage-status" aria-label="短临产品状态">
            <div className="stage-status-row">
              <span className={`run-state${ensembleActive ? ' offline' : ''}${analysisActive ? ' analysis' : ''}${['PUBLISHED', 'VERIFYING', 'VERIFIED'].includes(run?.status ?? '') && lkActive ? ' published' : ''}${run?.status === 'FAILED' && lkActive ? ' failed' : ''}`}>
                {analysisActive ? analysis?.status ?? '无实况' : ensembleActive ? 'OFFLINE' : run?.status ?? (loading ? '读取中' : '无产品')}
              </span>
              <strong>{analysisActive ? `${analysis?.radar_count ?? 0} 站融合` : formatLead(currentLead)}</strong>
            </div>
            <div className="stage-status-row"><span>分析周期</span><strong>{formatUtc(issueTime, true)}</strong></div>
            <div className="stage-status-row stage-status-valid-time"><span>有效时间</span><strong>{formatUtc(selectedAsset?.valid_time, true)}</strong></div>
            <div className="stage-status-row"><span>有效覆盖</span><strong>{percent(selectedAsset?.coverage_ratio)}</strong></div>
            <div className="stage-status-row"><span>缺测格点</span><strong>{selectedAsset?.missing_cell_count?.toLocaleString('zh-CN') ?? '暂无'}</strong></div>
            {lkActive ? (
              <>
                <div className="stage-status-row verification-status-row">
                  <span>实况检验</span>
                  <strong>{verificationStatusLabel(verification)}</strong>
                </div>
                {verification?.status === 'succeeded' ? (
                  <div className="stage-status-row verification-score-row">
                    <span>FSS 5–60 分钟</span>
                    <strong>{verificationMetric(verification, 'mean_fss_lk')}</strong>
                  </div>
                ) : null}
              </>
            ) : null}
            <small>{analysisActive
              ? '基础质控、拼图与 QPE 已完成；当前结果未通过业务预报输入门控'
              : ensembleActive
              ? '原始未校准概率，仅供离线验收'
              : verification?.status === 'succeeded'
                ? '5 mm/h · 10 km；完成仅表示已评分，不代表业务技巧通过'
                : '发布后收齐未来 24 帧实况再自动评分'}</small>
          </div>

          <div className="stage-float stage-assets" aria-label="产品交付与溯源">
            <button type="button" onClick={() => openDrawer('provenance')}>
              <span>溯源</span>
              <small>{analysisActive ? 'basic-zr-qpe' : ensembleActive ? ensembleBundle?.model_id : selectedProduct?.model_id ?? '暂无'}</small>
            </button>
            {currentAssets.map((asset) => (
              <a key={asset.asset_id} href={asset.content_url} download>
                <span>{assetFormat(asset)}</span>
                <small>{asset.size_bytes == null ? '诊断图层' : formatBytes(asset.size_bytes)}</small>
              </a>
            ))}
          </div>
        </div>

        <NowcastTimeline
          key={`${productSource}-${displayMode}-${productType}-${selectedEnsembleLayer?.layer_id ?? 'none'}-${selectedCycle?.id ?? 'none'}`}
          assets={renderedAssets}
          selectedAsset={selectedAsset}
          issueTime={issueTime}
          fixedWindow={analysisActive || (lkActive && productType !== 'rain_rate')}
          productLabel={currentProductLabel}
          mode={analysisActive ? 'analysis' : 'forecast'}
          onSelect={selectTimelineAsset}
        />

        <section className={`forecast-drawer${drawerOpen ? ' open' : ''}`} aria-label="预报细节抽屉">
          <header className="drawer-bar">
            <div className="drawer-tabs" role="tablist" aria-label="预报细节">
              {([['point', '单点雨强'], ['area', '区域统计'], ['provenance', '产品溯源']] as const).map(([key, label]) => (
                <button
                  key={key}
                  type="button"
                  role="tab"
                  aria-selected={drawerTab === key}
                  className={drawerTab === key ? 'active' : ''}
                  disabled={(ensembleActive || analysisActive) && key !== 'provenance'}
                  onClick={() => openDrawer(key)}
                >
                  {label}
                </button>
              ))}
            </div>
            <p className="drawer-summary">{analysisActive
              ? `${issueTime ? formatRunOption(issueTime) : '当前周期'} · 雷达 QPE · 工程分析`
              : ensembleActive
              ? `${currentProductLabel} · ${formatLead(currentLead)} · 离线未校准`
              : `${formatCoordinate(point.longitude)}°E ${formatCoordinate(point.latitude)}°N · ${formatRate(currentPointValue)} · ${formatLead(currentLead)}`}</p>
            <button type="button" className="drawer-toggle" aria-expanded={drawerOpen} onClick={() => setDrawerOpen((value) => !value)}>
              {drawerOpen ? '收起 ▴' : '展开 ▾'}
            </button>
          </header>
          <div className="drawer-panels">
            <div className={`drawer-tabpanel${drawerTab === 'point' ? ' active' : ''}`} role="tabpanel" aria-label="单点雨强">
              <section className="query-panel point-panel">
              <header><div><p className="panel-label">Point forecast</p><h2>单点雨强</h2></div><span>{pointLoading ? '读取中' : formatLead(currentLead)}</span></header>
              <form className="coordinate-form" onSubmit={submitPoint}>
                <label><span>经度 °E</span><input aria-label="点预报经度" type="number" min="118" max="123" step="0.01" value={pointDraft.longitude} onChange={(event) => setPointDraft((current) => ({ ...current, longitude: event.target.value }))} /></label>
                <label><span>纬度 °N</span><input aria-label="点预报纬度" type="number" min="25" max="27" step="0.01" value={pointDraft.latitude} onChange={(event) => setPointDraft((current) => ({ ...current, latitude: event.target.value }))} /></label>
                <button type="submit">查询</button>
              </form>
              {pointError ? <p className="query-error" role="alert">{pointError}</p> : null}
              <div className="point-current">
                <div><span>当前格点</span><strong>{pointForecast ? `${formatCoordinate(pointForecast.grid_longitude)}°E  ${formatCoordinate(pointForecast.grid_latitude)}°N` : '等待查询'}</strong></div>
                <div><span>当前雨强</span><strong>{formatRate(currentPointValue)}</strong><small>{currentPointValue?.valid === false ? '缺测' : `技术质量 ${(currentPointValue?.confidence ?? 0).toFixed(2)}（非概率）`}</small></div>
              </div>
              {pointForecast ? (
                <>
                  <PointForecastChart values={pointForecast.values} currentLead={currentLead} />
                  <PointMilestones values={pointForecast.values} />
                </>
              ) : <div className="query-empty">点击地图或输入经纬度。</div>}
            </section>
            </div>
            <div className={`drawer-tabpanel${drawerTab === 'area' ? ' active' : ''}`} role="tabpanel" aria-label="区域统计">
              <section className="query-panel area-panel">
              <header><div><p className="panel-label">Area statistics</p><h2>区域雨强</h2></div><span>{areaLoading ? '读取中' : formatLead(areaStatistics?.lead_time_minutes)}</span></header>
              <div className="area-presets" aria-label="区域快捷选择">
                {areaPresets.map((preset) => <button type="button" key={preset.label} onClick={() => applyAreaPreset(preset.bbox)}>{preset.label}</button>)}
              </div>
              <form className="bbox-form" onSubmit={submitArea}>
                {['西经度', '南纬度', '东经度', '北纬度'].map((label, index) => (
                  <label key={label}><span>{label}</span><input aria-label={label} type="number" step="0.01" value={bboxDraft[index]} onChange={(event) => setBboxDraft((current) => current.map((value, itemIndex) => itemIndex === index ? event.target.value : value))} /></label>
                ))}
                <button type="submit">更新区域</button>
              </form>
              {areaError ? <p className="query-error" role="alert">{areaError}</p> : null}
              <dl className="area-results">
                <div><dt>平均雨强</dt><dd>{areaStatistics ? `${areaStatistics.mean_rain_rate.toFixed(2)} mm/h` : '暂无'}</dd></div>
                <div><dt>最大雨强</dt><dd>{areaStatistics ? `${areaStatistics.max_rain_rate.toFixed(2)} mm/h` : '暂无'}</dd></div>
                <div><dt>有效覆盖</dt><dd>{percent(areaStatistics?.valid_pixel_ratio)}</dd></div>
                <div><dt>格点数</dt><dd>{areaStatistics ? `${areaStatistics.valid_pixel_count.toLocaleString('zh-CN')} / ${areaStatistics.missing_pixel_count.toLocaleString('zh-CN')} 缺测` : '暂无'}</dd></div>
              </dl>
            </section>
            </div>
            <div className={`drawer-tabpanel${drawerTab === 'provenance' ? ' active' : ''}`} role="tabpanel" aria-label="产品溯源">
              <section className="provenance-panel">
              <header><p className="panel-label">Product provenance</p><h2>产品溯源</h2></header>
              <dl>
                <div><dt>Run ID</dt><dd>{analysisActive ? analysis?.run_id ?? '暂无' : ensembleActive ? ensembleBundle?.run_id : run?.run_id ?? '暂无'}</dd></div>
                <div><dt>{analysisActive ? 'Analysis ID' : 'Product ID'}</dt><dd>{analysisActive ? analysis?.analysis_id ?? '暂无' : ensembleActive ? selectedEnsembleLayer?.layer_id : selectedProduct?.product_id ?? '暂无'}</dd></div>
                <div><dt>网格</dt><dd>{analysisActive ? analysis?.grid_id ?? '暂无' : ensembleActive ? ensembleBundle?.grid_id : selectedProduct?.grid_id ?? '暂无'}</dd></div>
                <div><dt>{analysisActive ? '分析配置' : '产品配置'}</dt><dd>{analysisActive ? analysis?.config_version ?? '暂无' : ensembleActive ? ensembleBundle?.product_config_version : selectedProduct?.config_version ?? '暂无'}</dd></div>
                {!analysisActive ? <div><dt>源预报 SHA</dt><dd>{ensembleActive && ensembleBundle
                  ? shortSHA(ensembleBundle.source_forecast_sha256)
                  : selectedProduct ? shortSHA(selectedProduct.source_forecast_sha256) : '暂无'}</dd></div> : null}
                <div><dt>当前资产 SHA</dt><dd>{selectedAsset?.sha256 ? shortSHA(selectedAsset.sha256) : '清单未提供'}</dd></div>
                <div><dt>{analysisActive ? '分析产品' : '源预报'}</dt><dd title={analysisActive ? analysis?.analysis_uri ?? undefined : ensembleActive ? ensembleBundle?.source_forecast_uri : selectedProduct?.source_forecast_uri}>{analysisActive ? analysis?.analysis_uri ?? '暂无' : ensembleActive ? ensembleBundle?.source_forecast_uri : selectedProduct?.source_forecast_uri ?? '暂无'}</dd></div>
                <div><dt>{analysisActive ? '平均 QI' : '成员数'}</dt><dd>{analysisActive ? analysisQPE?.mean_quality_index.toFixed(3) ?? '暂无' : ensembleActive ? ensembleBundle?.member_count : selectedProduct?.member_count ?? '暂无'}</dd></div>
                {ensembleActive ? <div><dt>校准状态</dt><dd>原始未校准</dd></div> : null}
              </dl>
          </section>
            </div>
          </div>
        </section>
      </section>
    </section>
  )
}


function PointForecastChart({ values, currentLead }: { values: PointForecastValue[], currentLead: number | null }) {
  const width = 440
  const height = 138
  const inset = { top: 14, right: 12, bottom: 25, left: 34 }
  const validRates = values.filter((item) => item.valid && item.rain_rate != null).map((item) => item.rain_rate as number)
  const maximum = Math.max(1, ...validRates)
  const x = (index: number) => inset.left + (values.length <= 1 ? 0 : index / (values.length - 1)) * (width - inset.left - inset.right)
  const y = (value: number) => inset.top + (1 - value / maximum) * (height - inset.top - inset.bottom)
  const points = values.map((item, index) => item.valid && item.rain_rate != null ? `${x(index)},${y(item.rain_rate)}` : null).filter(Boolean).join(' ')
  const currentIndex = values.findIndex((item) => item.lead_time_minutes === currentLead)

  return (
    <figure className="point-chart">
      <svg viewBox={`0 0 ${width} ${height}`} role="img" aria-label="未来两小时单点雨强趋势">
        {[0, 0.5, 1].map((ratio) => {
          const lineY = y(maximum * ratio)
          return <g key={ratio}><line x1={inset.left} x2={width - inset.right} y1={lineY} y2={lineY} /><text x={inset.left - 6} y={lineY + 3}>{(maximum * ratio).toFixed(1)}</text></g>
        })}
        {currentIndex >= 0 ? <line className="current-line" x1={x(currentIndex)} x2={x(currentIndex)} y1={inset.top} y2={height - inset.bottom} /> : null}
        <polyline points={points} />
        {values.map((item, index) => item.valid && item.rain_rate != null ? <circle className={index === currentIndex ? 'current' : ''} key={item.lead_time_minutes} cx={x(index)} cy={y(item.rain_rate)} r={index === currentIndex ? 3.7 : 2.1} /> : null)}
        <text className="axis-label" x={inset.left} y={height - 7}>+{values[0]?.lead_time_minutes ?? 0}</text>
        <text className="axis-label" x={width - inset.right} y={height - 7} textAnchor="end">+{values.at(-1)?.lead_time_minutes ?? 0} min</text>
      </svg>
      <figcaption><span>雨强 mm/h</span><strong>峰值 {maximum.toFixed(2)} · {values.filter((item) => item.valid).length} / {values.length} 有效</strong></figcaption>
    </figure>
  )
}

function PointMilestones({ values }: { values: PointForecastValue[] }) {
  const milestones = [30, 60, 120].map((lead) => ({
    lead,
    value: values.find((item) => item.lead_time_minutes === lead) ?? null,
  }))
  return (
    <div className="point-milestones" aria-label="关键时效点预报">
      <header><span>关键时效</span><small>雨强 / 技术质量</small></header>
      {milestones.map(({ lead, value }) => (
        <div key={lead}>
          <strong>T+{lead}</strong>
          <span>{formatRate(value)}</span>
          <small>{value?.confidence == null ? '暂无' : value.confidence.toFixed(2)}</small>
        </div>
      ))}
    </div>
  )
}

function sortAssets(left: TimelineAsset, right: TimelineAsset) {
  return (left.lead_time_minutes ?? 0) - (right.lead_time_minutes ?? 0)
}

function displayLayerAlt(
  mode: DisplayMode,
  productType: SupportedProductType,
  lead: number | null,
  label: string,
) {
  if (mode !== 'deterministic') return `${formatLead(lead)} ${label}图层`
  if (productType === 'rain_rate') return `${formatLead(lead)} 分钟降水率图层`
  return productType === 'accumulation_60' ? '0–1 小时累计降水图层' : '0–2 小时累计降水图层'
}

function assetFormat(asset: DisplayAsset) {
  if (asset.asset_type === 'rendered_png') return 'PNG'
  if (asset.asset_type === 'cloud_optimized_geotiff') return 'COG'
  if (asset.asset_type === 'application_netcdf') return 'NetCDF'
  return asset.asset_type
}

function toDisplayAsset(asset: ProductAsset | EnsembleProductAsset): DisplayAsset {
  return {
    asset_id: asset.asset_id,
    asset_type: asset.asset_type,
    content_url: asset.content_url,
    media_type: asset.media_type,
    sha256: asset.sha256,
    size_bytes: asset.size_bytes,
    lead_time_minutes: asset.lead_time_minutes,
    valid_time: asset.valid_time,
    unit: asset.unit,
    coverage_ratio: asset.coverage_ratio,
    valid_cell_count: asset.valid_cell_count,
    missing_cell_count: asset.missing_cell_count,
  }
}

function isEnsembleBundle(value: unknown): value is EnsembleProductBundle {
  if (!value || typeof value !== 'object') return false
  const candidate = value as Partial<EnsembleProductBundle>
  return typeof candidate.bundle_id === 'string'
    && typeof candidate.issue_time === 'string'
    && candidate.operational_eligible === false
    && typeof candidate.member_count === 'number'
    && candidate.member_count >= 2
    && Array.isArray(candidate.layers)
    && candidate.layers.every((layer: EnsembleProductLayer) =>
      Array.isArray(layer.assets) && Array.isArray(layer.legend))
}

function isVerificationSummary(value: unknown): value is VerificationSummary {
  if (!value || typeof value !== 'object') return false
  const candidate = value as Partial<VerificationSummary>
  return typeof candidate.run_id === 'string'
    && typeof candidate.issue_time === 'string'
    && typeof candidate.status === 'string'
    && typeof candidate.truth_frame_count === 'number'
    && Array.isArray(candidate.missing_lead_minutes)
    && Array.isArray(candidate.metrics)
}

function verificationStatusLabel(value: VerificationSummary | null) {
  if (!value) return '状态未提供'
  if (value.status === 'waiting_truth') return `等待实况 ${value.truth_frame_count}/24`
  if (value.status === 'running') return '评分运行中'
  if (value.status === 'succeeded') return '已完成'
  if (value.status === 'failed') return '评分失败'
  return '等待产品发布'
}

function verificationMetric(value: VerificationSummary, name: string) {
  const metric = value.metrics.find((item) => item.name === name)
  return metric ? metric.value.toFixed(3) : '不可计算'
}

function formatBytes(value: number) {
  if (value < 1024) return `${value} B`
  if (value < 1024 * 1024) return `${(value / 1024).toFixed(1)} KB`
  return `${(value / 1024 / 1024).toFixed(1)} MB`
}

function formatRate(value: PointForecastValue | null) {
  if (!value) return '暂无'
  if (!value.valid || value.rain_rate == null) return '缺测'
  return `${value.rain_rate.toFixed(2)} mm/h`
}

function shortSHA(value: string) {
  return `${value.slice(0, 12)}…${value.slice(-8)}`
}
