import { cleanup, fireEvent, render, screen, waitFor } from '@testing-library/react'
import { afterEach, describe, expect, it, vi } from 'vitest'

import { NowcastWorkspace } from './NowcastWorkspace'

const productsSourceSHA = '6344c411c9e3f3b2feb7da959f38563577383487e2efb65092609eaf37a0da9f'

const run = {
  run_id: '0ce8e90c-3160-5e5d-874d-1eda09bf1084',
  issue_time: '2026-08-25T10:00:00Z',
  grid_id: 'fuzhou_118_123_25_27_0p01deg_v1',
  config_version: 'rp013-fixed-5min-v1.1',
  status: 'PUBLISHED',
  created_at: '2026-08-25T14:10:02Z',
  updated_at: '2026-08-25T15:12:40Z',
}

const verificationSummary = {
  run_id: run.run_id,
  issue_time: run.issue_time,
  run_status: 'VERIFIED',
  status: 'succeeded',
  truth_frame_count: 24,
  missing_lead_minutes: [],
  profile_version: 'rp031-operational-deterministic-v1',
  metrics: [
    { name: 'mean_fss_lk', threshold: 5, value: 0.713 },
    { name: 'mean_fss_persistence', threshold: 5, value: 0.631 },
  ],
  verified_at: '2026-08-25T12:06:00Z',
  truth_operational_eligible: true,
  promotion_eligible: false,
}

const analysisCycles = ['09:50', '09:55', '10:00'].map((time, index) => ({
  analysis_id: `1922303f-eef9-583b-acb6-11644f3f1c${index}`,
  run_id: `d8540b49-067f-5a9d-a50d-2ebcf1d0c1${index}`,
  analysis_time: `2026-08-28T${time}:00Z`,
  grid_id: run.grid_id,
  config_version: 'rp034-fujian-four-radar-engineering-v1',
  status: 'ANALYSIS_READY',
  radar_count: 4,
  valid_coverage_ratio: 0.41,
  mean_quality_index: 0.39,
  analysis_uri: `s3://rainpulse/analysis/${time}/analysis.zarr`,
  mosaic_uri: `s3://rainpulse/analysis/${time}/mosaic.zarr`,
  radars: [
    { radar_id: 'z9591', state: 'PARTICIPATING' },
    { radar_id: 'z9593', state: 'PARTICIPATING' },
  ],
  created_at: '2026-08-30T15:40:00Z',
  updated_at: '2026-08-30T15:50:00Z',
}))

const analysisDiagnostic = (analysisID: string, analysisTime: string) => ({
  contract_version: '1.0',
  job_id: '7b8eb073-06c6-563b-a492-a33da8a1fa74',
  analysis_id: analysisID,
  analysis_time: analysisTime,
  grid_id: run.grid_id,
  diagnostic_config_version: 'rp012-operational-diagnostics-v1',
  renderer_version: 'radar-diagnostic-renderer-1.0.0',
  palette_version: 'rainpulse-meteorological-v1',
  flag_definition_version: 'qc-flags-v1',
  operational_eligible: false,
  operational_reasons: ['input_not_operational:z9591'],
  layers: [{
    layer_id: 'grid-rate-qpe', title: '瞬时雨强', scope: 'grid', field: 'RATE_QPE',
    rendering: 'scalar', unit: 'mm/h',
    image_url: '/api/v1/diagnostics/job/layers/grid-rate-qpe',
    width: 1002, height: 402, palette_version: 'rainpulse-meteorological-v1',
    legend: [], bounds: [117.995, 24.995, 123.005, 27.005],
  }],
  created_at: '2026-08-30T15:50:00Z',
})

const analysisQPE = (analysisID: string, analysisTime: string) => ({
  analysis_id: analysisID, analysis_time: analysisTime, grid_id: run.grid_id,
  grid_config_version: 'fuzhou-grid-0p01deg-v1',
  qpe_config_version: 'rp011-basic-qpe-v1', qpe_algorithm_version: 'basic-zr-qpe-1.0.0',
  mosaic_config_version: 'rp034-fujian-four-radar-engineering-v1',
  mosaic_algorithm_version: 'qi-mosaic-1.1.0', flag_definition_version: 'qc-flags-v1',
  input_mosaic_uri: 's3://rainpulse/analysis/mosaic.zarr', input_field: 'DBZH_QC',
  coefficient_a: 200, exponent_b: 1.6, no_rain_below_dbz: 10, maximum_rate_mm_h: 300,
  gauge_adjustment_enabled: false, operational_eligible: false,
  operational_reasons: ['input_not_operational:z9591'], grid_cell_count: 100701,
  valid_cell_count: 41287, missing_cell_count: 59414, low_quality_cell_count: 20000,
  no_rain_cell_count: 7000, rain_cell_count: 34287, capped_cell_count: 0,
  valid_coverage_ratio: 0.41, mean_quality_index: 0.39, mean_rate_mm_h: 1.8,
  maximum_observed_rate_mm_h: 80, uncapped_max_rate_mm_h: 80, p95_rate_mm_h: 6.4,
  measured_at: '2026-08-30T15:49:00Z',
})

const products = [
  {
    product_id: 'rain-product', run_id: run.run_id, product_type: 'rain_rate',
    model_id: 'pysteps-lk', model_version: 'pysteps-lk-1.1.0',
    config_version: 'rp015-application-products-v1', grid_id: run.grid_id,
    issue_time: run.issue_time,
    valid_times: ['2026-08-25T10:05:00Z', '2026-08-25T10:10:00Z'],
    member_count: 1, source_forecast_uri: 's3://rainpulse/forecast.zarr',
    source_forecast_sha256: '6344c411c9e3f3b2feb7da959f38563577383487e2efb65092609eaf37a0da9f',
    created_at: '2026-08-25T15:07:56Z',
  },
  {
    product_id: 'accum-60', run_id: run.run_id, product_type: 'accumulation_60',
    model_id: 'pysteps-lk', model_version: 'pysteps-lk-1.1.0',
    config_version: 'rp015-application-products-v1', grid_id: run.grid_id,
    issue_time: run.issue_time, valid_times: ['2026-08-25T11:00:00Z'],
    member_count: 1, source_forecast_uri: 's3://rainpulse/forecast.zarr',
    source_forecast_sha256: productsSourceSHA,
    created_at: '2026-08-25T15:07:56Z',
  },
  {
    product_id: 'accum-120', run_id: run.run_id, product_type: 'accumulation_120',
    model_id: 'pysteps-lk', model_version: 'pysteps-lk-1.1.0',
    config_version: 'rp015-application-products-v1', grid_id: run.grid_id,
    issue_time: run.issue_time, valid_times: ['2026-08-25T12:00:00Z'],
    member_count: 1, source_forecast_uri: 's3://rainpulse/forecast.zarr',
    source_forecast_sha256: productsSourceSHA,
    created_at: '2026-08-25T15:07:56Z',
  },
] as const

const rainAssets = [5, 10].flatMap((lead) => [
  {
    asset_id: `png-${lead}`, asset_type: 'rendered_png',
    uri: `s3://rainpulse/lead-${lead}.png`, content_url: `/api/lead-${lead}.png`,
    media_type: 'image/png', sha256: `${lead}`.padStart(64, '0'), size_bytes: 1679,
    lead_time_minutes: lead, valid_time: `2026-08-25T10:${String(lead).padStart(2, '0')}:00Z`,
    unit: 'mm h-1', coverage_ratio: 0.9500998, valid_cell_count: 95676,
    missing_cell_count: 5025, no_rain_cell_count: 10050,
    created_at: '2026-08-25T15:07:56Z',
  },
  {
    asset_id: `nc-${lead}`, asset_type: 'application_netcdf',
    uri: `s3://rainpulse/lead-${lead}.nc`, content_url: `/api/lead-${lead}.nc`,
    media_type: 'application/x-netcdf', sha256: `${lead + 1}`.padStart(64, '0'),
    size_bytes: 408164, lead_time_minutes: lead,
    valid_time: `2026-08-25T10:${String(lead).padStart(2, '0')}:00Z`, unit: 'mm h-1',
    created_at: '2026-08-25T15:07:56Z',
  },
])

const accumulationAsset = (productID: string, lead: number) => [{
  asset_id: `${productID}-png`, asset_type: 'rendered_png',
  uri: `s3://rainpulse/${productID}.png`, content_url: `/api/${productID}.png`,
  media_type: 'image/png', sha256: `${lead}`.padStart(64, '0'), size_bytes: 1820,
  lead_time_minutes: lead, valid_time: lead === 60 ? '2026-08-25T11:00:00Z' : '2026-08-25T12:00:00Z',
  unit: 'mm', coverage_ratio: 0.94, valid_cell_count: 94666,
  missing_cell_count: 6035, no_rain_cell_count: 8050,
  created_at: '2026-08-25T15:07:56Z',
}]

const ensembleLayerAssets = (layerID: string) => [5, 10].flatMap((lead) => [
  {
    asset_id: `${layerID}-lead-${String(lead).padStart(3, '0')}-png`,
    asset_type: 'rendered_png',
    content_url: `/api/v1/ensemble-products/ensemble-run/assets/${layerID}-lead-${String(lead).padStart(3, '0')}-png`,
    media_type: 'image/png', sha256: `${lead + 3}`.padStart(64, '0'), size_bytes: 1900,
    lead_time_minutes: lead, valid_time: `2026-08-25T10:${String(lead).padStart(2, '0')}:00Z`,
    unit: layerID.startsWith('probability') ? '1' : 'mm h-1', coverage_ratio: 0.91,
    valid_cell_count: 91638, missing_cell_count: 9063,
  },
  {
    asset_id: `${layerID}-lead-${String(lead).padStart(3, '0')}-nc`,
    asset_type: 'application_netcdf',
    content_url: `/api/v1/ensemble-products/ensemble-run/assets/${layerID}-lead-${String(lead).padStart(3, '0')}-nc`,
    media_type: 'application/x-netcdf', sha256: `${lead + 4}`.padStart(64, '0'),
    size_bytes: 408164, lead_time_minutes: lead,
    valid_time: `2026-08-25T10:${String(lead).padStart(2, '0')}:00Z`,
    unit: layerID.startsWith('probability') ? '1' : 'mm h-1', coverage_ratio: 0.91,
    valid_cell_count: 91638, missing_cell_count: 9063,
  },
])

const ensembleBundle = {
  bundle_id: '9b000000-0000-4000-8000-000000000001',
  run_id: '9b000000-0000-4000-8000-000000000001',
  issue_time: run.issue_time, grid_id: run.grid_id,
  pixel_edge_bounds: [117.995, 24.995, 123.005, 27.005], width: 501, height: 201,
  model_id: 'pysteps-steps', model_version: 'pysteps-steps-1.0.0',
  model_config_version: 'rp022-pysteps-steps-v1',
  product_config_version: 'rp023-ensemble-application-products-v1', member_count: 12,
  calibration_status: 'raw_ensemble_relative_frequency_uncalibrated',
  operational_eligible: false,
  operational_gate: 'independent_fujian_probabilistic_acceptance_required',
  source_forecast_uri: 's3://rainpulse/ensemble-forecast.zarr',
  source_forecast_sha256: productsSourceSHA,
  layers: [
    {
      layer_id: 'probability-gt-5', product_type: 'probability_exceedance',
      variable_name: 'prob_gt_5', threshold_mm_h: 5, quantile: null, unit: '1',
      legend: [{ minimum: 0.01, color: '#d6eef7' }, { minimum: 0.5, color: '#2d8ea8' }],
      assets: ensembleLayerAssets('probability-gt-5'),
    },
    {
      layer_id: 'quantile-p90', product_type: 'quantile',
      variable_name: 'p90', threshold_mm_h: null, quantile: 0.9, unit: 'mm h-1',
      legend: [{ minimum: 0.1, color: '#9dd9ff' }, { minimum: 5, color: '#3ca85b' }],
      assets: ensembleLayerAssets('quantile-p90'),
    },
  ],
  created_at: '2026-08-29T02:00:00Z',
}

describe('RainPulse short-nowcast workspace', () => {
  afterEach(() => {
    cleanup()
    vi.unstubAllGlobals()
  })

  it('renders published products, scrubs frames and exposes point and area evidence', async () => {
    const fetchStatus = vi.fn().mockImplementation((input: string) => {
      let body: unknown = {}
      if (input.includes('/runs?status=')) body = { items: [run] }
      else if (input.includes('/products?run_id=')) body = { items: products }
      else if (input.endsWith('/products/rain-product/assets')) body = rainAssets
      else if (input.endsWith('/products/accum-60/assets')) body = accumulationAsset('accum-60', 60)
      else if (input.endsWith('/products/accum-120/assets')) body = accumulationAsset('accum-120', 120)
      else if (input.includes('/verification/summary?')) body = verificationSummary
      else if (input.includes('/point-forecast?')) body = {
        product_id: 'rain-product', longitude: 119.3, latitude: 26.08,
        grid_longitude: 119.3, grid_latitude: 26.08,
        values: [
          { valid_time: '2026-08-25T10:05:00Z', lead_time_minutes: 5, rain_rate: 2, valid: true, confidence: 0.78 },
          { valid_time: '2026-08-25T10:10:00Z', lead_time_minutes: 10, rain_rate: 2.4, valid: true, confidence: 0.74 },
        ],
      }
      else if (input.includes('/area-statistics?')) body = {
        product_id: 'rain-product', bbox: [119, 25.9, 119.6, 26.3],
        valid_time: '2026-08-25T10:05:00Z', lead_time_minutes: 5,
        valid_pixel_count: 5151, missing_pixel_count: 0, valid_pixel_ratio: 1,
        max_rain_rate: 7.2, mean_rain_rate: 1.83,
      }
      return Promise.resolve({ ok: true, status: 200, json: async () => body })
    })
    vi.stubGlobal('fetch', fetchStatus)

    render(<NowcastWorkspace refreshToken={0} />)

    expect(screen.getByRole('heading', { name: '0–2 小时降水预报' })).toBeTruthy()
    expect(screen.getByText('实况检验')).toBeTruthy()
    expect(await screen.findByText('0.713')).toBeTruthy()
    expect(screen.getByText('工程验证 / RP-023')).toBeTruthy()
    expect(screen.getByRole('button', { name: '实时' }).getAttribute('aria-pressed')).toBe('true')
    expect(await screen.findByText('当前无实时更新')).toBeTruthy()
    const firstLayer = await screen.findByRole('img', { name: 'T+5 分钟降水率图层' })
    expect(firstLayer.getAttribute('data-source')).toBe('/api/lead-5.png')
    expect(screen.getByRole('application', { name: /可交互降水 GIS 地图/ })).toBeTruthy()
    expect(screen.getByRole('button', { name: '播放全部时效' })).toBeTruthy()
    expect((await screen.findAllByText('1.83 mm/h')).length).toBeGreaterThan(0)
    expect(await screen.findByText('技术质量 0.78（非概率）')).toBeTruthy()
    expect(screen.getByText(/峰值 2.40/)).toBeTruthy()

    const drawer = screen.getByRole('region', { name: '预报细节抽屉' })
    expect(drawer.classList.contains('open')).toBe(false)
    fireEvent.click(screen.getByRole('button', { name: '展开单点曲线' }))
    expect(drawer.classList.contains('open')).toBe(true)
    fireEvent.click(screen.getByRole('tab', { name: '区域统计' }))
    expect(screen.getByRole('tab', { name: '区域统计' }).getAttribute('aria-selected')).toBe('true')
    expect(screen.getByRole('tabpanel', { name: '区域统计' }).classList.contains('active')).toBe(true)
    fireEvent.click(screen.getByRole('button', { name: '收起 ▴' }))
    expect(drawer.classList.contains('open')).toBe(false)

    const timeline = screen.getByLabelText('五分钟预报时间轴')
    const rail = timeline.querySelector<HTMLElement>('.nowcast-timeline-rail')
    expect(rail).toBeTruthy()
    Object.defineProperty(rail as HTMLElement, 'scrollWidth', { configurable: true, value: 200 })
    vi.spyOn(rail as HTMLElement, 'getBoundingClientRect').mockReturnValue({
      bottom: 42, height: 42, left: 0, right: 200, top: 0, width: 200, x: 0, y: 0,
      toJSON: () => ({}),
    })
    fireEvent.pointerDown(rail as HTMLElement, { clientX: 200, pointerId: 1 })
    fireEvent.pointerUp(rail as HTMLElement, { pointerId: 1 })
    expect(await screen.findByRole('img', { name: 'T+10 分钟降水率图层' })).toBeTruthy()
    expect(screen.getByText('95.0%')).toBeTruthy()

    fireEvent.click(screen.getByRole('button', { name: /^2 小时累计/ }))
    expect(await screen.findByRole('img', { name: '0–2 小时累计降水图层' })).toBeTruthy()
    expect(screen.getByRole('button', { name: '播放全部时效' }).hasAttribute('disabled')).toBe(true)

    await waitFor(() => {
      expect(fetchStatus.mock.calls.some(([url]) => String(url).includes('/point-forecast?'))).toBe(true)
      expect(fetchStatus.mock.calls.some(([url]) => String(url).includes('/area-statistics?'))).toBe(true)
    })
  })

  it('keeps the map picker compact when the selected cell is missing', async () => {
    const fetchStatus = vi.fn().mockImplementation((input: string) => {
      let body: unknown = {}
      if (input.includes('/runs?status=')) body = { items: [run] }
      else if (input.includes('/products?run_id=')) body = { items: products }
      else if (input.endsWith('/products/rain-product/assets')) body = rainAssets
      else if (input.endsWith('/products/accum-60/assets')) body = accumulationAsset('accum-60', 60)
      else if (input.endsWith('/products/accum-120/assets')) body = accumulationAsset('accum-120', 120)
      else if (input.includes('/point-forecast?')) body = {
        product_id: 'rain-product', longitude: 119.3, latitude: 26.08,
        grid_longitude: 119.3, grid_latitude: 26.08,
        values: [
          { valid_time: '2026-08-25T10:05:00Z', lead_time_minutes: 5, rain_rate: null, valid: false, confidence: null },
        ],
      }
      else if (input.includes('/area-statistics?')) body = {
        product_id: 'rain-product', bbox: [119, 25.9, 119.6, 26.3],
        valid_time: '2026-08-25T10:05:00Z', lead_time_minutes: 5,
        valid_pixel_count: 0, missing_pixel_count: 5151, valid_pixel_ratio: 0,
        max_rain_rate: 0, mean_rain_rate: 0,
      }
      return Promise.resolve({ ok: true, status: 200, json: async () => body })
    })
    vi.stubGlobal('fetch', fetchStatus)

    const { container } = render(<NowcastWorkspace refreshToken={0} />)

    expect((await screen.findAllByText('缺测')).length).toBeGreaterThan(0)
    const picker = container.querySelector('.gis-picker')
    expect(picker).toBeTruthy()
    expect(picker?.textContent).toContain('119.30°E')
    expect(picker?.textContent).not.toContain('缺测')
    expect(picker?.querySelector('button')).toBeNull()
  })

  it('switches offline STEPS probability and quantile layers on the shared GIS timeline', async () => {
    const fetchStatus = vi.fn().mockImplementation((input: string) => {
      let body: unknown = {}
      if (input.includes('/runs?status=')) body = { items: [run] }
      else if (input.includes('/products?run_id=')) body = { items: products }
      else if (input.endsWith('/products/rain-product/assets')) body = rainAssets
      else if (input.endsWith('/products/accum-60/assets')) body = accumulationAsset('accum-60', 60)
      else if (input.endsWith('/products/accum-120/assets')) body = accumulationAsset('accum-120', 120)
      else if (input.endsWith('/ensemble-products/latest')) body = ensembleBundle
      else if (input.includes('/point-forecast?')) body = {
        product_id: 'rain-product', longitude: 119.3, latitude: 26.08,
        grid_longitude: 119.3, grid_latitude: 26.08,
        values: [
          { valid_time: '2026-08-25T10:05:00Z', lead_time_minutes: 5, rain_rate: 2, valid: true, confidence: 0.78 },
        ],
      }
      else if (input.includes('/area-statistics?')) body = {
        product_id: 'rain-product', bbox: [119, 25.9, 119.6, 26.3],
        valid_time: '2026-08-25T10:05:00Z', lead_time_minutes: 5,
        valid_pixel_count: 5151, missing_pixel_count: 0, valid_pixel_ratio: 1,
        max_rain_rate: 7.2, mean_rain_rate: 1.83,
      }
      return Promise.resolve({ ok: true, status: 200, json: async () => body })
    })
    vi.stubGlobal('fetch', fetchStatus)

    const { container } = render(<NowcastWorkspace refreshToken={0} />)

    const ensembleButton = await screen.findByRole('button', { name: 'STEPS 集合' })
    expect(ensembleButton.hasAttribute('disabled')).toBe(false)
    expect(container.querySelector('.gis-picker-wrap')).toBeTruthy()
    fireEvent.click(ensembleButton)
    const probabilityLayer = await screen.findByRole('img', {
      name: 'T+5 超过 5 mm/h 概率图层',
    })
    expect(probabilityLayer.getAttribute('data-source')).toContain('probability-gt-5')
    expect(screen.getByText('OFFLINE')).toBeTruthy()
    expect(screen.getByText('离线 · 原始未校准 · 不进入业务发布')).toBeTruthy()
    expect(screen.getByRole('tab', { name: '单点雨强' }).hasAttribute('disabled')).toBe(true)
    expect(container.querySelector('.gis-picker-wrap')).toBeTruthy()
    expect(container.querySelector('.gis-picker')).toBeNull()

    fireEvent.click(screen.getByRole('button', { name: 'P90' }))
    const quantileLayer = await screen.findByRole('img', { name: 'T+5 P90 雨强分位数图层' })
    expect(quantileLayer.getAttribute('data-source')).toContain('quantile-p90')
    expect(screen.getByText('NetCDF')).toBeTruthy()

    fireEvent.click(screen.getByRole('button', { name: 'LK 确定性' }))
    expect(await screen.findByRole('img', { name: 'T+5 分钟降水率图层' })).toBeTruthy()
  })

  it('pins a selected published run in historical mode', async () => {
    const olderRun = {
      ...run,
      run_id: '1ce8e90c-3160-5e5d-874d-1eda09bf1084',
      issue_time: '2026-08-24T10:00:00Z',
      created_at: '2026-08-24T14:10:02Z',
      updated_at: '2026-08-24T15:12:40Z',
    }
    const fetchStatus = vi.fn().mockImplementation((input: string) => {
      let body: unknown = {}
      if (input.includes('/runs?status=')) body = { items: [run, olderRun] }
      else if (input.includes('/products?run_id=')) body = { items: products }
      else if (input.endsWith('/products/rain-product/assets')) body = rainAssets
      else if (input.endsWith('/products/accum-60/assets')) body = accumulationAsset('accum-60', 60)
      else if (input.endsWith('/products/accum-120/assets')) body = accumulationAsset('accum-120', 120)
      else if (input.includes('/point-forecast?')) body = {
        product_id: 'rain-product', longitude: 119.3, latitude: 26.08,
        grid_longitude: 119.3, grid_latitude: 26.08,
        values: [],
      }
      else if (input.includes('/area-statistics?')) body = {
        product_id: 'rain-product', bbox: [119, 25.9, 119.6, 26.3],
        valid_time: '2026-08-25T10:05:00Z', lead_time_minutes: 5,
        valid_pixel_count: 0, missing_pixel_count: 5151, valid_pixel_ratio: 0,
        max_rain_rate: 0, mean_rain_rate: 0,
      }
      return Promise.resolve({ ok: true, status: 200, json: async () => body })
    })
    vi.stubGlobal('fetch', fetchStatus)

    render(<NowcastWorkspace refreshToken={0} />)

    await screen.findByRole('img', { name: 'T+5 分钟降水率图层' })
    fireEvent.click(screen.getByRole('button', { name: '历史时次' }))
    const picker = await screen.findByRole('combobox', { name: '历史数据时次' })
    expect(picker.querySelectorAll('option')).toHaveLength(2)
    fireEvent.change(picker, { target: { value: olderRun.run_id } })

    await waitFor(() => {
      expect(fetchStatus.mock.calls.some(([url]) => String(url).includes(
        `/products?run_id=${olderRun.run_id}`,
      ))).toBe(true)
    })
    expect(screen.getByRole('button', { name: '历史时次' }).getAttribute('aria-pressed')).toBe('true')
    expect(screen.getByRole<HTMLSelectElement>('combobox', { name: '历史数据时次' }).value)
      .toBe(olderRun.run_id)
  })

  it('reuses the historical picker for multiple radar QPE analysis times', async () => {
    const latestAnalysis = analysisCycles.at(-1)!
    const fetchStatus = vi.fn().mockImplementation((input: string) => {
      let body: unknown = {}
      if (input.includes('/runs?status=')) body = { items: [run] }
      else if (input.includes('/analysis-cycles?')) body = { items: analysisCycles }
      else if (input.endsWith(`/${latestAnalysis.analysis_id}/diagnostics`)) {
        body = analysisDiagnostic(latestAnalysis.analysis_id, latestAnalysis.analysis_time)
      }
      else if (input.endsWith(`/${latestAnalysis.analysis_id}/qpe-summary`)) {
        body = analysisQPE(latestAnalysis.analysis_id, latestAnalysis.analysis_time)
      }
      else if (input.includes('/products?run_id=')) body = { items: products }
      else if (input.endsWith('/products/rain-product/assets')) body = rainAssets
      else if (input.endsWith('/products/accum-60/assets')) body = accumulationAsset('accum-60', 60)
      else if (input.endsWith('/products/accum-120/assets')) body = accumulationAsset('accum-120', 120)
      else if (input.includes('/point-forecast?')) body = {
        product_id: 'rain-product', longitude: 119.3, latitude: 26.08,
        grid_longitude: 119.3, grid_latitude: 26.08, values: [],
      }
      else if (input.includes('/area-statistics?')) body = {
        product_id: 'rain-product', bbox: [119, 25.9, 119.6, 26.3],
        valid_time: '2026-08-25T10:05:00Z', lead_time_minutes: 5,
        valid_pixel_count: 0, missing_pixel_count: 5151, valid_pixel_ratio: 0,
        max_rain_rate: 0, mean_rain_rate: 0,
      }
      return Promise.resolve({ ok: true, status: 200, json: async () => body })
    })
    vi.stubGlobal('fetch', fetchStatus)

    render(<NowcastWorkspace refreshToken={0} />)

    await screen.findByRole('img', { name: 'T+5 分钟降水率图层' })
    fireEvent.click(screen.getByRole('button', { name: '历史时次' }))
    const picker = await screen.findByRole<HTMLSelectElement>('combobox', {
      name: '历史数据时次',
    })
    expect(picker.querySelectorAll('option')).toHaveLength(4)
    expect(picker.value).toBe(`analysis:${latestAnalysis.analysis_id}`)
    expect((await screen.findByText('雷达 QPE')).textContent).toBe('雷达 QPE')
    const layer = await screen.findByRole('img', { name: /雷达 QPE 瞬时雨强图层/ })
    expect(layer.getAttribute('data-source')).toBe('/api/v1/diagnostics/job/layers/grid-rate-qpe')
    expect(screen.getByText('41.0%')).toBeTruthy()
    expect(screen.getByRole('tab', { name: '单点雨强' }).hasAttribute('disabled')).toBe(true)
    expect(screen.queryByLabelText('五分钟预报时间轴')).toBeNull()
  })
})
