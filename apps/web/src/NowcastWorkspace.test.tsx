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

describe('RainPulse short-nowcast workspace', () => {
  afterEach(() => {
    cleanup()
    vi.unstubAllGlobals()
  })

  it('renders published products, scrubs frames and exposes point and area evidence', async () => {
    const fetchStatus = vi.fn().mockImplementation((input: string) => {
      let body: unknown = {}
      if (input.endsWith('/runs/latest')) body = run
      else if (input.includes('/products?run_id=')) body = { items: products }
      else if (input.endsWith('/products/rain-product/assets')) body = rainAssets
      else if (input.endsWith('/products/accum-60/assets')) body = accumulationAsset('accum-60', 60)
      else if (input.endsWith('/products/accum-120/assets')) body = accumulationAsset('accum-120', 120)
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
    expect(screen.getByText('工程验证 / RP-016')).toBeTruthy()
    const firstLayer = await screen.findByRole('img', { name: 'T+5 分钟降水率图层' })
    expect(firstLayer.getAttribute('data-source')).toBe('/api/lead-5.png')
    expect(screen.getByRole('application', { name: /可交互降水 GIS 地图/ })).toBeTruthy()
    expect(screen.getByRole('button', { name: '播放全部时效' })).toBeTruthy()
    expect(await screen.findByText('1.83 mm/h')).toBeTruthy()
    expect(await screen.findByText('技术质量 0.78（非概率）')).toBeTruthy()
    expect(screen.getByText(/峰值 2.40/)).toBeTruthy()

    fireEvent.click(screen.getByRole('button', { name: 'T+10，10:10 UTC' }))
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
})
