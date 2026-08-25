import {
  useCallback,
  useEffect,
  useMemo,
  useState,
  type FormEvent,
} from 'react'

import type { components } from './api/generated/schema'
import { NowcastMap } from './NowcastMap'
import { NowcastTimeline } from './NowcastTimeline'

type ForecastRun = components['schemas']['ForecastRun']
type Product = components['schemas']['Product']
type ProductAsset = components['schemas']['ProductAsset']
type ProductPage = components['schemas']['ProductPage']
type ProductType = components['schemas']['ProductType']
type PointForecast = components['schemas']['PointForecast']
type PointForecastValue = components['schemas']['PointForecastValue']
type AreaStatistics = components['schemas']['AreaStatistics']

type SupportedProductType = 'rain_rate' | 'accumulation_60' | 'accumulation_120'
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
  const renderedAssets = useMemo(
    () => selectedProduct
      ? (assets[selectedProduct.product_id] ?? [])
        .filter((item) => item.asset_type === 'rendered_png')
        .sort(sortAssets)
      : [],
    [assets, selectedProduct],
  )
  const selectedAsset = renderedAssets.find((item) => item.lead_time_minutes === selectedLead)
    ?? renderedAssets[0]
    ?? null
  const currentLead = selectedAsset?.lead_time_minutes ?? null
  const currentAssets = useMemo(
    () => selectedProduct
      ? (assets[selectedProduct.product_id] ?? []).filter(
        (item) => item.lead_time_minutes === currentLead && item.asset_type !== 'point_query_index',
      )
      : [],
    [assets, currentLead, selectedProduct],
  )

  useEffect(() => {
    if (!rainProduct) return
    const controller = new AbortController()
    const loadPoint = async () => {
      setPointLoading(true)
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
    setProductType(nextType)
    setSelectedLead(firstFrame?.lead_time_minutes ?? null)
    setLayerError(false)
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

  const selectTimelineAsset = useCallback((asset: ProductAsset) => {
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
  const legend = productType === 'rain_rate' ? rainRateLegend : rainfallAmountLegend

  return (
    <section className="forecast-page" aria-labelledby="forecast-title">
      <header className="page-heading forecast-heading">
        <div>
          <p className="section-kicker">Operational nowcast / RP-015</p>
          <h1 id="forecast-title">0–2 小时降水预报</h1>
          <p>在固定等经纬网格上查看五分钟短临时效，并查询单点与区域雨强。</p>
        </div>
        <div className="update-time">
          <span>产品目录更新</span>
          <strong>{updatedAt ? updatedAt.toLocaleTimeString('zh-CN', { hour12: false }) : '暂无'}</strong>
          <small>所有时间均为 UTC</small>
        </div>
      </header>

      {error ? <div className="error-banner" role="alert"><strong>产品读取异常</strong><span>{error}</span></div> : null}

      <section className="forecast-status-strip" aria-label="短临产品状态">
        <ForecastMetric label="起报时间" value={formatUtc(run?.issue_time, true)} note={run?.status === 'PUBLISHED' ? '产品已发布' : run?.status ?? '等待产品'} tone={run?.status === 'PUBLISHED' ? 'healthy' : 'neutral'} />
        <ForecastMetric label="当前时效" value={formatLead(currentLead)} note={formatUtc(selectedAsset?.valid_time)} />
        <ForecastMetric label="有效覆盖" value={percent(selectedAsset?.coverage_ratio)} note={`${selectedAsset?.missing_cell_count?.toLocaleString('zh-CN') ?? '暂无'} 缺测格点`} />
        <ForecastMetric label="预报模型" value={selectedProduct?.model_id ?? '暂无'} note={selectedProduct?.model_version ?? '等待模型产品'} />
      </section>

      <section className="forecast-console">
        <header className="forecast-toolbar">
          <div className="product-switcher" aria-label="降水产品">
            {(Object.keys(productLabels) as SupportedProductType[]).map((type) => {
              const available = products.some((item) => item.product_type === type)
              return (
                <button
                  key={type}
                  type="button"
                  className={productType === type ? 'active' : ''}
                  aria-pressed={productType === type}
                  disabled={!available}
                  onClick={() => switchProduct(type)}
                >
                  <strong>{productLabels[type]}</strong>
                  <small>{productNotes[type]}</small>
                </button>
              )
            })}
          </div>
          <div className="publication-note">
            <span className={`run-state ${run?.status === 'PUBLISHED' ? 'published' : ''}`}>{run?.status ?? (loading ? '读取中' : '无产品')}</span>
            <small>发布状态不等同于预报技巧通过</small>
          </div>
        </header>

        <div className="forecast-workspace">
          <div className="forecast-visual-column">
            <div className="forecast-map-wrap">
              <NowcastMap
                imageUrl={selectedAsset?.content_url}
                imageDescription={layerAlt(productType, currentLead)}
                validTimeLabel={formatUtc(selectedAsset?.valid_time)}
                leadLabel={formatLead(currentLead)}
                productLabel={productLabels[productType]}
                legend={legend}
                legendUnit={productType === 'rain_rate' ? 'mm/h' : 'mm'}
                point={point}
                pointValueLabel={formatRate(currentPointValue)}
                bbox={bbox}
                loading={loading}
                layerError={layerError}
                onLayerError={setLayerError}
                onSelectPoint={selectPoint}
              />
            </div>

            <NowcastTimeline
              key={productType}
              assets={renderedAssets}
              selectedAsset={selectedAsset}
              issueTime={run?.issue_time}
              fixedWindow={productType !== 'rain_rate'}
              productLabel={productLabels[productType]}
              onSelect={selectTimelineAsset}
            />

            <div className="asset-delivery">
              <div><span>当前产品交付</span><strong>{formatLead(currentLead)} · {productLabels[productType]}</strong></div>
              <div className="asset-links">
                {currentAssets.map((asset) => (
                  <a key={asset.asset_id} href={asset.content_url} download>
                    <span>{assetFormat(asset)}</span>
                    <small>{formatBytes(asset.size_bytes)}</small>
                  </a>
                ))}
              </div>
            </div>
          </div>

          <aside className="forecast-insight-rail" aria-label="单点降水预报">
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
                <div><span>当前雨强</span><strong>{formatRate(currentPointValue)}</strong><small>{currentPointValue?.valid === false ? '缺测' : `置信 ${(currentPointValue?.confidence ?? 0).toFixed(2)}`}</small></div>
              </div>
              {pointForecast ? (
                <>
                  <PointForecastChart values={pointForecast.values} currentLead={currentLead} />
                  <PointMilestones values={pointForecast.values} />
                </>
              ) : <div className="query-empty">点击地图或输入经纬度。</div>}
            </section>
          </aside>

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

          <section className="provenance-panel">
              <header><p className="panel-label">Product provenance</p><h2>产品溯源</h2></header>
              <dl>
                <div><dt>Run ID</dt><dd>{run?.run_id ?? '暂无'}</dd></div>
                <div><dt>Product ID</dt><dd>{selectedProduct?.product_id ?? '暂无'}</dd></div>
                <div><dt>网格</dt><dd>{selectedProduct?.grid_id ?? '暂无'}</dd></div>
                <div><dt>产品配置</dt><dd>{selectedProduct?.config_version ?? '暂无'}</dd></div>
                <div><dt>源预报 SHA</dt><dd>{selectedProduct ? shortSHA(selectedProduct.source_forecast_sha256) : '暂无'}</dd></div>
                <div><dt>当前资产 SHA</dt><dd>{selectedAsset ? shortSHA(selectedAsset.sha256) : '暂无'}</dd></div>
                <div><dt>源预报</dt><dd title={selectedProduct?.source_forecast_uri}>{selectedProduct?.source_forecast_uri ?? '暂无'}</dd></div>
                <div><dt>成员数</dt><dd>{selectedProduct?.member_count ?? '暂无'}</dd></div>
              </dl>
          </section>
        </div>
      </section>
    </section>
  )
}

function ForecastMetric({ label, value, note, tone = 'neutral' }: {
  label: string
  value: string
  note: string
  tone?: 'neutral' | 'healthy'
}) {
  return <div className={`forecast-metric ${tone}`}><span>{label}</span><strong>{value}</strong><small>{note}</small></div>
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
      <header><span>关键时效</span><small>雨强 / 置信</small></header>
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

function sortAssets(left: ProductAsset, right: ProductAsset) {
  return (left.lead_time_minutes ?? 0) - (right.lead_time_minutes ?? 0)
}

function layerAlt(productType: SupportedProductType, lead: number | null) {
  if (productType === 'rain_rate') return `${formatLead(lead)} 分钟降水率图层`
  return productType === 'accumulation_60' ? '0–1 小时累计降水图层' : '0–2 小时累计降水图层'
}

function assetFormat(asset: ProductAsset) {
  if (asset.asset_type === 'rendered_png') return 'PNG'
  if (asset.asset_type === 'cloud_optimized_geotiff') return 'COG'
  if (asset.asset_type === 'application_netcdf') return 'NetCDF'
  return asset.asset_type
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
