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
      json: async () => input.includes('/radars/status')
        ? [status]
        : { service: 'rainpulse-control', status: 'ready', version: 'rp007-test' },
    }))
    vi.stubGlobal('fetch', fetchStatus)

    render(<App />)

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
})
