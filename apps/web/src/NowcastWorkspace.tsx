import {
  useCallback,
  useEffect,
  useMemo,
  useState,
  type FormEvent,
} from 'react'

import type { components } from './api/generated/schema'
import { NowcastMap } from './NowcastMap'
import { NowcastTimeline, type TimelineAsset } from './NowcastTimeline'

type ForecastRun = components['schemas']['ForecastRun']
type Product = components['schemas']['Product']
type ProductAsset = components['schemas']['ProductAsset']
type ProductPage = components['schemas']['ProductPage']
type ProductType = components['schemas']['ProductType']
type PointForecast = components['schemas']['PointForecast']
type PointForecastValue = components['schemas']['PointForecastValue']
type AreaStatistics = components['schemas']['AreaStatistics']
type EnsembleProductBundle = components['schemas']['EnsembleProductBundle']
type EnsembleProductLayer = components['schemas']['EnsembleProductLayer']
type EnsembleProductAsset = components['schemas']['EnsembleProductAsset']

type SupportedProductType = 'rain_rate' | 'accumulation_60' | 'accumulation_120'
type DisplayMode = 'deterministic' | 'probability' | 'quantile'
type DisplayAsset = TimelineAsset & {
  asset_type: string
  content_url: string
  media_type: string
  sha256: string
  size_bytes: number
  unit?: string | null
  coverage_ratio?: number | null
  valid_cell_count?: number | null
  missing_cell_count?: number | null
}
type Coordinate = { longitude: number, latitude: number }
type GridBounds = { west: number, south: number, east: number, north: number }

const FUZHOU_GRID: GridBounds = { west: 118, south: 25, east: 123, north: 27 }
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
  const [products, setProducts] = useState<Product[]>([])
  const [assets, setAssets] = useState<Record<string, ProductAsset[]>>({})
  const [productType, setProductType] = useState<SupportedProductType>('rain_rate')
  const [displayMode, setDisplayMode] = useState<DisplayMode>('deterministic')
  const [ensembleBundle, setEnsembleBundle] = useState<EnsembleProductBundle | null>(null)
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

  useEffect(() => {
    const controller = new AbortController()
    const load = async () => {
      setLoading(true)
      try {
        const runResponse = await fetch('/api/v1/runs/latest', { signal: controller.signal })
        if (!runResponse.ok) throw new Error(`最新预报运行接口响应 ${runResponse.status}`)
        const latestRun = await runResponse.json() as ForecastRun
        const productResponse = await fetch(
          `/api/v1/products?run_id=${encodeURIComponent(latestRun.run_id)}`,
          { signal: controller.signal },
        )
        if (!productResponse.ok) throw new Error(`产品目录接口响应 ${productResponse.status}`)
        const page = await productResponse.json() as ProductPage
        const supported = page.items.filter((item) => isSupportedProduct(item.product_type))
        const assetPairs = await Promise.all(supported.map(async (product) => {
          const response = await fetch(`/api/v1/products/${product.product_id}/assets`, {
            signal: controller.signal,
          })
          if (!response.ok) throw new Error(`产品资产接口响应 ${response.status}`)
          return [product.product_id, await response.json() as ProductAsset[]] as const
        }))
        const nextAssets = Object.fromEntries(assetPairs)
        let latestEnsemble: EnsembleProductBundle | null = null
        try {
          const ensembleResponse = await fetch('/api/v1/ensemble-products/latest', {
            signal: controller.signal,
          })
          if (ensembleResponse.ok) {
            const candidate = await ensembleResponse.json() as unknown
            if (isEnsembleBundle(candidate)) latestEnsemble = candidate
          }
        } catch (ensembleRequestError: unknown) {
          if (ensembleRequestError instanceof DOMException
            && ensembleRequestError.name === 'AbortError') throw ensembleRequestError
        }
        const preferred = supported.find((item) => item.product_type === 'rain_rate')
          ?? supported[0]
          ?? null
        const firstPNG = preferred
          ? nextAssets[preferred.product_id]
            ?.filter((item) => item.asset_type === 'rendered_png')
            .sort(sortAssets)[0]
          : null

        setRun(latestRun)
        setProducts(supported)
        setAssets(nextAssets)
        setEnsembleBundle(latestEnsemble)
        if (!latestEnsemble) setDisplayMode('deterministic')
        if (preferred && isSupportedProduct(preferred.product_type)) {
          setProductType(preferred.product_type)
        }
        setSelectedLead(firstPNG?.lead_time_minutes ?? null)
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
  }, [refreshToken])

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
  const renderedAssets = displayMode === 'deterministic'
    ? deterministicRenderedAssets
    : ensembleRenderedAssets
  const selectedAsset = renderedAssets.find((item) => item.lead_time_minutes === selectedLead)
    ?? renderedAssets[0]
    ?? null
  const currentLead = selectedAsset?.lead_time_minutes ?? null
  const currentAssets = useMemo(
    () => displayMode === 'deterministic'
      ? selectedProduct
        ? (assets[selectedProduct.product_id] ?? [])
          .filter((item) => item.lead_time_minutes === currentLead
            && item.asset_type !== 'point_query_index')
          .map(toDisplayAsset)
        : []
      : (selectedEnsembleLayer?.assets ?? [])
        .filter((item) => item.lead_time_minutes === currentLead)
        .map(toDisplayAsset),
    [assets, currentLead, displayMode, selectedEnsembleLayer, selectedProduct],
  )

  useEffect(() => {
    if (!rainProduct) return
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
  }, [point, rainProduct])

  useEffect(() => {
    if (!rainProduct || currentLead == null) return
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
  }, [bbox, currentLead, rainProduct])

  const switchProduct = (nextType: SupportedProductType) => {
    const nextProduct = products.find((item) => item.product_type === nextType)
    if (!nextProduct) return
    const firstFrame = (assets[nextProduct.product_id] ?? [])
      .filter((item) => item.asset_type === 'rendered_png')
      .sort(sortAssets)[0]
    setDisplayMode('deterministic')
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
    setProbabilityThreshold(nextThreshold)
    setQuantileValue(nextQuantile)
    setSelectedLead((current) => frames.some((item) => item.lead_time_minutes === current)
      ? current
      : frames[0]?.lead_time_minutes ?? null)
    setDrawerOpen(false)
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
  const ensembleActive = displayMode !== 'deterministic'
  const currentProductLabel = displayMode === 'probability'
    ? `超过 ${probabilityThreshold} mm/h 概率`
    : displayMode === 'quantile'
      ? `P${Math.round(quantileValue * 100)} 雨强分位数`
      : productLabels[productType]
  const currentProductNote = displayMode === 'probability'
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
  const issueTime = ensembleActive ? ensembleBundle?.issue_time : run?.issue_time

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
          <small>所有时间均为 UTC</small>
        </div>
      </header>

      {error ? <div className="error-banner" role="alert"><strong>产品读取异常</strong><span>{error}</span></div> : null}

      <section className="forecast-stage">
        <div className="forecast-mode-bar" aria-label="预报模型与集合产品选择">
          <div className="forecast-mode-switch" role="group" aria-label="预报模型">
            <span>模型</span>
            <button
              type="button"
              className={displayMode === 'deterministic' ? 'active' : ''}
              aria-pressed={displayMode === 'deterministic'}
              onClick={() => switchProduct(productType)}
            >LK 确定性</button>
            <button
              type="button"
              className={ensembleActive ? 'active' : ''}
              aria-pressed={ensembleActive}
              disabled={!ensembleBundle}
              onClick={() => switchEnsembleLayer('probability')}
            >STEPS 集合</button>
          </div>
          {ensembleBundle ? (
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
          ) : <span className="ensemble-unavailable">等待离线集合产品</span>}
          <div className={`ensemble-boundary${ensembleActive ? ' active' : ''}`}>
            <strong>{ensembleBundle ? `${ensembleBundle.member_count} 成员` : '未装载'}</strong>
            <span>离线 · 原始未校准 · 不进入业务发布</span>
          </div>
        </div>
        <div className="forecast-map-host">
          <NowcastMap
            imageUrl={selectedAsset?.content_url}
            imageDescription={displayLayerAlt(displayMode, productType, currentLead, currentProductLabel)}
            validTimeLabel={formatUtc(selectedAsset?.valid_time)}
            leadLabel={formatLead(currentLead)}
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
            picker={!ensembleActive ? (
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
            {ensembleActive ? (
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
              <span className={`run-state${ensembleActive ? ' offline' : ''}${run?.status === 'PUBLISHED' && !ensembleActive ? ' published' : ''}${run?.status === 'FAILED' && !ensembleActive ? ' failed' : ''}`}>
                {ensembleActive ? 'OFFLINE' : run?.status ?? (loading ? '读取中' : '无产品')}
              </span>
              <strong>{formatLead(currentLead)}</strong>
            </div>
            <div className="stage-status-row"><span>起报</span><strong>{formatUtc(issueTime, true)}</strong></div>
            <div className="stage-status-row stage-status-valid-time"><span>有效时间</span><strong>{formatUtc(selectedAsset?.valid_time, true)}</strong></div>
            <div className="stage-status-row"><span>有效覆盖</span><strong>{percent(selectedAsset?.coverage_ratio)}</strong></div>
            <div className="stage-status-row"><span>缺测格点</span><strong>{selectedAsset?.missing_cell_count?.toLocaleString('zh-CN') ?? '暂无'}</strong></div>
            <small>{ensembleActive ? '原始未校准概率，仅供离线验收' : '发布状态不等同于预报技巧通过'}</small>
          </div>

          <div className="stage-float stage-assets" aria-label="产品交付与溯源">
            <button type="button" onClick={() => openDrawer('provenance')}>
              <span>溯源</span>
              <small>{ensembleActive ? ensembleBundle?.model_id : selectedProduct?.model_id ?? '暂无'}</small>
            </button>
            {currentAssets.map((asset) => (
              <a key={asset.asset_id} href={asset.content_url} download>
                <span>{assetFormat(asset)}</span>
                <small>{formatBytes(asset.size_bytes)}</small>
              </a>
            ))}
          </div>
        </div>

        <NowcastTimeline
          key={`${displayMode}-${productType}-${selectedEnsembleLayer?.layer_id ?? 'none'}`}
          assets={renderedAssets}
          selectedAsset={selectedAsset}
          issueTime={issueTime}
          fixedWindow={!ensembleActive && productType !== 'rain_rate'}
          productLabel={currentProductLabel}
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
                  disabled={ensembleActive && key !== 'provenance'}
                  onClick={() => openDrawer(key)}
                >
                  {label}
                </button>
              ))}
            </div>
            <p className="drawer-summary">{ensembleActive
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
                <div><dt>Run ID</dt><dd>{ensembleActive ? ensembleBundle?.run_id : run?.run_id ?? '暂无'}</dd></div>
                <div><dt>Product ID</dt><dd>{ensembleActive ? selectedEnsembleLayer?.layer_id : selectedProduct?.product_id ?? '暂无'}</dd></div>
                <div><dt>网格</dt><dd>{ensembleActive ? ensembleBundle?.grid_id : selectedProduct?.grid_id ?? '暂无'}</dd></div>
                <div><dt>产品配置</dt><dd>{ensembleActive ? ensembleBundle?.product_config_version : selectedProduct?.config_version ?? '暂无'}</dd></div>
                <div><dt>源预报 SHA</dt><dd>{ensembleActive && ensembleBundle
                  ? shortSHA(ensembleBundle.source_forecast_sha256)
                  : selectedProduct ? shortSHA(selectedProduct.source_forecast_sha256) : '暂无'}</dd></div>
                <div><dt>当前资产 SHA</dt><dd>{selectedAsset ? shortSHA(selectedAsset.sha256) : '暂无'}</dd></div>
                <div><dt>源预报</dt><dd title={ensembleActive ? ensembleBundle?.source_forecast_uri : selectedProduct?.source_forecast_uri}>{ensembleActive ? ensembleBundle?.source_forecast_uri : selectedProduct?.source_forecast_uri ?? '暂无'}</dd></div>
                <div><dt>成员数</dt><dd>{ensembleActive ? ensembleBundle?.member_count : selectedProduct?.member_count ?? '暂无'}</dd></div>
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
