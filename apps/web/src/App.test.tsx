import { fireEvent, render, screen } from '@testing-library/react'
import { afterEach, describe, expect, it, vi } from 'vitest'

import App from './App'

const status = {
  radar_id: 'z9598',
  display_name: 'SanMing Z9598',
  lifecycle: 'draft',
  config_version: 'z9598-fmt-v1',
  health: 'DEGRADED',
  latest_scan_id: '10000000-0000-4000-8000-000000000001',
  latest_scan_time: '2026-06-15T12:04:42.090681Z',
  scan_status: 'QC_READY',
  scan_completeness: 0.999,
  data_delay_seconds: 120,
  participating_in_latest_analysis: false,
  health_metrics: {
    scan_id: '10000000-0000-4000-8000-000000000001',
    radar_id: 'z9598',
    radar_config_version: 'z9598-fmt-v1',
    health_profile_version: 'rp007-integrity-v1',
    health: 'DEGRADED',
    health_reasons: ['CONFIG_NOT_READY', 'NOISE_TELEMETRY_MISSING'],
    scan_completeness: 0.999,
    expected_sweep_count: 11,
    actual_sweep_count: 11,
    missing_sweep_numbers: [],
    expected_radial_count: 3960,
    actual_radial_count: 3994,
    missing_radial_count: 0,
    maximum_azimuth_gap_deg: 1.1,
    field_availability_ratio: 1,
    field_availability: [{ field: 'DBZH', available: true, present_sweep_count: 11, finite_gate_ratio: 0.82, out_of_range_gate_count: 0, unit: 'dBZ' }],
    noise_level: { source: 'RSTM_RADIAL_HEADER', horizontal_dbm: null, vertical_dbm: null, sample_count: 0 },
    channel_status: 'UNKNOWN',
    out_of_range_gate_count: 0,
    out_of_range_gate_ratio: 0,
    anomaly_count: 0,
    layer_anomalies: [],
    warnings: [],
    measured_at: '2026-08-24T12:00:00Z',
  },
  qc_metrics: {
    scan_id: '10000000-0000-4000-8000-000000000001',
    radar_id: 'z9598',
    qc_profile: 'rp008-basic-v1',
    qc_pipeline_version: 'rp008-basic-1.0.4',
    flag_definition_version: 'qc-flags-v1',
    health_state: 'DEGRADED',
    mean_quality_index: 0.784,
    valid_gate_count: 127531,
    missing_gate_count: 420881,
    low_quality_gate_count: 1174,
    no_rain_gate_count: 48211,
    radial_interference_ray_count: 3,
    ground_clutter_gate_count: 0,
    sea_clutter_gate_count: 0,
    ap_gate_count: 0,
    module_statuses: {
      health_gate: 'applied',
      missing_and_echo_state: 'applied',
      radial_interference: 'applied',
      static_ground_clutter: 'skipped',
      sea_ap: 'skipped',
      quality_index: 'applied',
    },
    measured_at: '2026-08-24T13:00:00Z',
  },
}

describe('RainPulse radar operations overview', () => {
  afterEach(() => {
    vi.unstubAllGlobals()
  })

  it('renders real radar integrity status and refreshes both APIs', async () => {
    const fetchStatus = vi.fn().mockImplementation((input: string) => Promise.resolve({
      ok: true,
      status: 200,
      json: async () => {
        if (input.includes('/radars/status')) return [status]
        if (input.includes('/analysis-cycles')) return { items: [] }
        return { service: 'rainpulse-control', status: 'ready', version: 'rp007-test' }
      },
    }))
    vi.stubGlobal('fetch', fetchStatus)

    render(<App />)

    fireEvent.click(screen.getByRole('button', { name: '雷达运行' }))
    expect(screen.getByRole('heading', { name: '雷达运行总览' })).toBeTruthy()
    expect(await screen.findByRole('heading', { name: 'SanMing Z9598' })).toBeTruthy()
    expect(screen.getByText('基数据未提供有效噪声遥测')).toBeTruthy()
    expect(screen.getByRole('heading', { name: '基础极坐标质控' })).toBeTruthy()
    expect(screen.getAllByText('78.4%').length).toBeGreaterThan(0)
    expect(screen.getByText('静态地物杂波')).toBeTruthy()
    expect(screen.getAllByText('已跳过').length).toBeGreaterThan(0)
    expect(screen.getAllByText('降级运行').length).toBeGreaterThan(0)
    expect(fetchStatus).toHaveBeenCalledWith('/api/v1/system/status', expect.objectContaining({ signal: expect.any(AbortSignal) }))
    expect(fetchStatus).toHaveBeenCalledWith('/api/v1/radars/status', expect.objectContaining({ signal: expect.any(AbortSignal) }))

    fireEvent.click(screen.getByRole('button', { name: '刷新雷达状态' }))
    expect(fetchStatus.mock.calls.length).toBeGreaterThanOrEqual(4)
  })

  it('renders the analysis evidence rail and real diagnostic layer switcher', async () => {
    const analysisID = '85000000-0000-4000-8000-000000000001'
    const cycle = {
      analysis_id: analysisID,
      run_id: '82000000-0000-4000-8000-000000000001',
      analysis_time: '2026-06-15T12:05:00Z',
      grid_id: 'fuzhou_118_123_25_27_0p01deg_v1',
      config_version: 'rp010-qi-mosaic-v1',
      status: 'ANALYSIS_READY',
      degraded_reason: 'insufficient_operational_contributors,input_not_operational:z9598',
      radar_count: 1,
      valid_coverage_ratio: 0.031489,
      mean_quality_index: 0.28919,
      mosaic_uri: 's3://rainpulse/mosaic.zarr',
      analysis_uri: 's3://rainpulse/analysis.zarr',
      radars: [{
        radar_id: 'z9598',
        scan_id: '86000000-0000-4000-8000-000000000001',
        state: 'PARTICIPATING',
        time_offset_seconds: -18,
        mean_quality_index: 0.28919,
      }],
      created_at: '2026-08-25T08:00:00Z',
      updated_at: '2026-08-25T08:16:05Z',
    }
    const diagnostics = {
      contract_version: '1.0',
      job_id: '83000000-0000-4000-8000-000000000001',
      analysis_id: analysisID,
      analysis_time: cycle.analysis_time,
      grid_id: cycle.grid_id,
      diagnostic_config_version: 'rp012-operational-diagnostics-v1',
      renderer_version: 'radar-diagnostic-renderer-1.0.0',
      palette_version: 'rainpulse-meteorological-v1',
      flag_definition_version: 'qc-flags-v1',
      operational_eligible: false,
      operational_reasons: ['insufficient_operational_contributors'],
      created_at: '2026-08-25T08:20:00Z',
      layers: [
        {
          layer_id: 'grid-rate-qpe', title: '瞬时雨强', scope: 'grid',
          field: 'RATE_QPE', rendering: 'scalar', unit: 'mm/h',
          image_url: '/api/v1/diagnostics/job/layers/grid-rate-qpe',
          width: 1002, height: 402, palette_version: 'rainpulse-meteorological-v1',
          legend: [{ label: '≥ 0 mm/h', color: '#dce9ee', value: 0 }],
          bounds: [117.995, 24.995, 123.005, 27.005],
        },
        {
          layer_id: 'radar-z9598-dbzh-raw', title: 'Z9598 · 原始反射率', scope: 'polar',
          field: 'DBZH_RAW', rendering: 'scalar', unit: 'dBZ',
          image_url: '/api/v1/diagnostics/job/layers/radar-z9598-dbzh-raw',
          width: 640, height: 640, palette_version: 'rainpulse-meteorological-v1',
          legend: [{ label: '≥ 0 dBZ', color: '#4ba3f2', value: 0 }],
          radar_id: 'z9598', scan_id: cycle.radars[0].scan_id,
          sweep_number: 0, elevation_deg: 0.5, maximum_range_km: 230,
        },
        {
          layer_id: 'radar-z9598-dbzh-qc', title: 'Z9598 · 质控后反射率', scope: 'polar',
          field: 'DBZH_QC', rendering: 'scalar', unit: 'dBZ',
          image_url: '/api/v1/diagnostics/job/layers/radar-z9598-dbzh-qc',
          width: 640, height: 640, palette_version: 'rainpulse-meteorological-v1',
          legend: [{ label: '≥ 0 dBZ', color: '#4ba3f2', value: 0 }],
          radar_id: 'z9598', scan_id: cycle.radars[0].scan_id,
          sweep_number: 0, elevation_deg: 0.5, maximum_range_km: 230,
        },
      ],
    }
    const fetchStatus = vi.fn().mockImplementation((input: string) => {
      let body: unknown = { service: 'rainpulse-control', status: 'ready', version: 'rp012-test' }
      if (input.includes('/radars/status')) body = [status]
      else if (input.endsWith('/analysis-cycles?limit=12')) body = { items: [cycle] }
      else if (input.endsWith(`/${analysisID}`)) body = cycle
      else if (input.endsWith('/mosaic-summary')) body = {
        profile_version: 'rp010-qi-mosaic-v1', algorithm_version: 'qi-mosaic-1.0.0',
        valid_coverage_ratio: 0.031489, mean_quality_index: 0.28919,
      }
      else if (input.endsWith('/qpe-summary')) body = {
        qpe_config_version: 'rp011-basic-qpe-v1', qpe_algorithm_version: 'basic-zr-qpe-1.0.0',
        valid_coverage_ratio: 0.031489, valid_cell_count: 3171,
        low_quality_cell_count: 3165, mean_quality_index: 0.28919,
        rain_cell_count: 2842, no_rain_cell_count: 329,
        maximum_observed_rate_mm_h: 31.5759, p95_rate_mm_h: 10.7301,
        gauge_adjustment_enabled: false,
      }
      else if (input.endsWith('/diagnostics')) body = diagnostics
      return Promise.resolve({ ok: true, status: 200, json: async () => body })
    })
    vi.stubGlobal('fetch', fetchStatus)

    render(<App />)

    expect(screen.getByRole('heading', { name: '分析诊断' })).toBeTruthy()
    expect(await screen.findByText('当前分析不可用于业务发布')).toBeTruthy()
    expect(screen.getByAltText('瞬时雨强诊断图层')).toBeTruthy()
    expect(screen.getByText('2,842')).toBeTruthy()
    fireEvent.click(screen.getByRole('button', { name: '单雷达极坐标' }))
    expect(await screen.findByText('原始')).toBeTruthy()
    expect(screen.getByText('质控后')).toBeTruthy()
    expect(screen.getAllByText('DBZH_QC').length).toBeGreaterThan(0)
  })
})
