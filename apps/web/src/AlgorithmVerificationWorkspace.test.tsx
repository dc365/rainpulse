import { cleanup, fireEvent, render, screen, waitFor } from '@testing-library/react'
import { afterEach, describe, expect, it, vi } from 'vitest'

import { AlgorithmVerificationWorkspace } from './AlgorithmVerificationWorkspace'

const run = {
  profile_version: 'rp016-mrms-v1',
  run_id: 'full-202108-v2',
  schema_version: '1.0',
  verification_kind: 'deterministic_spatial',
  primary_truth_kind: 'observed_mrms_10min',
  operational_eligible: false,
  completed_issue_count: 53,
  failed_issue_count: 0,
  motion_fallback_issue_count: 13,
  metric_row_count: 57240,
  skill_status: 'lk_supported',
  maps_available: true,
  map_bundle_count: 53,
  map_layer_count: 2544,
  map_renderer_version: 'algorithm-verification-map-renderer-1.0.0',
  probability_maps_available: false,
  probability_map_bundle_count: 0,
  probability_map_layer_count: 0,
  probability_map_renderer_version: null,
  modified_at: '2026-08-26T08:00:00Z',
}

const legacyRun = {
  ...run,
  run_id: 'full-202108-v1',
  maps_available: false,
  map_bundle_count: 0,
  map_layer_count: 0,
  map_renderer_version: null,
  modified_at: '2026-08-26T07:00:00Z',
}

const rigorousRun = {
  ...run,
  profile_version: 'rp018-mrms-v1',
  run_id: 'rp018-smoke',
  schema_version: '1.2',
  completed_issue_count: 1,
  metric_row_count: 1080,
  skill_status: 'insufficient_evidence',
  map_bundle_count: 1,
  map_layer_count: 60,
}

const probabilisticRun = {
  ...run,
  profile_version: 'rp026-mrms-nowcastnet-v1',
  run_id: 'holdout-v1',
  verification_kind: 'probabilistic_ensemble',
  primary_truth_kind: 'observed_mrms_preciprate_10min',
  completed_issue_count: 50,
  motion_fallback_issue_count: 0,
  metric_row_count: 3000,
  skill_status: 'steps_retained_nowcastnet_offline',
  maps_available: false,
  map_bundle_count: 0,
  map_layer_count: 0,
  map_renderer_version: null,
  modified_at: '2026-08-30T08:00:00Z',
}

const probabilisticMapRun = {
  ...probabilisticRun,
  run_id: 'holdout-map-v1',
  maps_available: true,
  map_bundle_count: 50,
  map_layer_count: 3600,
  map_renderer_version: 'algorithm-verification-map-renderer-1.0.0',
  modified_at: '2026-08-30T09:00:00Z',
}

const probabilisticProbabilityMapRun = {
  ...probabilisticMapRun,
  run_id: 'holdout-probability-map-v1',
  probability_maps_available: true,
  probability_map_bundle_count: 50,
  probability_map_layer_count: 9000,
  probability_map_renderer_version: 'algorithm-verification-probability-map-renderer-1.0.0',
  modified_at: '2026-08-30T10:00:00Z',
}

const brierValues = (value: number) => ({
  '1.0': value, '5.0': value, '10.0': value, '20.0': value, '50.0': value,
})

const probabilisticBand = (band: 'near' | 'far', minimumLead: number, maximumLead: number) => ({
  band,
  minimum_lead_minutes: minimumLead,
  maximum_lead_minutes: maximumLead,
  minimum_common_verification_coverage: band === 'near' ? .728 : .424,
  minimum_candidate_member_mean_coverage: 1,
  minimum_reference_member_mean_coverage: band === 'near' ? .901 : .784,
  scores: [
    ['nowcastnet', 1.504, 3.854, .424],
    ['steps', 1.101, 3.505, 1.939],
    ['lk', 1.750, 4.215, 0],
    ['persistence', 1.884, 4.520, 0],
    ['phase_correlation', 1.862, 4.493, 0],
  ].map(([model, crps, rmse, spread]) => ({
    model, crps_mm_h: crps, ensemble_mean_rmse_mm_h: rmse,
    mean_ensemble_spread_mm_h: spread, brier_score_by_threshold: brierValues(.1),
  })),
  candidate_skills: [
    ['steps', -.366], ['lk', .141], ['persistence', .202], ['phase_correlation', .193],
  ].map(([baseline, skill]) => ({
    baseline, crps_skill: skill, brier_skill_by_threshold: brierValues(Number(skill)),
  })),
})

const probabilisticDetail = {
  run: probabilisticRun,
  cases: [],
  filters: {
    models: ['nowcastnet', 'steps', 'lk', 'persistence', 'phase_correlation'],
    lead_minutes: null, thresholds_mm_h: null, windows_pixels: null, fss_scales: null,
  },
  skill_summary: {
    status: 'steps_retained_nowcastnet_offline', comparison_metric: 'CRPS', comparisons: [],
  },
  probabilistic_summary: {
    split: 'holdout',
    calibration_status: 'raw_ensemble_relative_frequency_uncalibrated',
    product_publication_enabled: false,
    candidate_model: 'nowcastnet', reference_model: 'steps',
    candidate_member_count: 4, reference_member_count: 12,
    device_name: 'NVIDIA RTX 6000D',
    lead_bands: [probabilisticBand('near', 10, 60), probabilisticBand('far', 70, 120)],
    performance: {
      candidate_runtime_ms: { p50: 149, p95: 158, max: 407 },
      reference_runtime_ms: { p50: 27602, p95: 29506, max: 29806 },
      total_runtime_ms: { p50: 30090, p95: 34774, max: 35937 },
      gpu_peak_allocated_bytes: { p50: 1058564096, p95: 1061185536, max: 1061443584 },
      peak_rss_bytes: { p50: 5832470528, p95: 6134890496, max: 6153449472 },
    },
  },
}

const probabilisticMapDetail = {
  ...probabilisticDetail,
  run: probabilisticMapRun,
  cases: [{
    case_id: 'holdout_convection_20250304', category: 'wet',
    issue_times: ['2025-03-04T06:00:00Z'],
  }],
  filters: {
    models: ['nowcastnet', 'steps', 'lk', 'persistence', 'phase_correlation'],
    lead_minutes: [10, 20], thresholds_mm_h: [1, 5, 10, 20, 50],
    windows_pixels: [], fss_scales: [],
  },
}

const probabilisticProbabilityMapDetail = {
  ...probabilisticMapDetail,
  run: probabilisticProbabilityMapRun,
}

const comparison = (baseline: string, threshold: number, difference: number) => ({
  baseline,
  bootstrap_sample_count: 2000,
  case_mean_differences: { midwest_convection_20210810: difference },
  evaluable_case_count: 4,
  maximum_lead_minutes: 60,
  mean_difference_95pct_interval: [difference / 2, difference * 1.5],
  mean_fss_difference: difference,
  passes_case_gate: true,
  positive_case_count: baseline === 'translation' && threshold === 10 ? 3 : 4,
  threshold_mm_h: threshold,
  total_wet_case_count: 4,
  window_pixels: 11,
})

const detail = {
  run,
  cases: [
    { case_id: 'socal_dry_20210805', category: 'dry', issue_times: ['2021-08-05T06:00:00Z'] },
    { case_id: 'midwest_convection_20210810', category: 'wet', issue_times: ['2021-08-10T17:00:00Z'] },
  ],
  filters: {
    models: ['lk', 'persistence', 'translation'],
    lead_minutes: [10, 20],
    thresholds_mm_h: [1, 5, 10],
    windows_pixels: [1, 5, 11],
    fss_scales: [
      { window_pixels: 1, target_km: 1, actual_km_min: .97, actual_km_max: 1.04 },
      { window_pixels: 5, target_km: 5, actual_km_min: 4.83, actual_km_max: 5.2 },
      { window_pixels: 11, target_km: 10, actual_km_min: 10.63, actual_km_max: 11.43 },
    ],
  },
  skill_summary: {
    status: 'lk_supported',
    comparison_metric: 'FSS',
    comparisons: [
      comparison('persistence', 1, .027), comparison('persistence', 5, .026), comparison('persistence', 10, .042),
      comparison('translation', 1, .008), comparison('translation', 5, .0072), comparison('translation', 10, .0135),
    ],
  },
}

const metric = (model: string, lead: number, fss: number) => ({
  case_id: 'midwest_convection_20210810', case_category: 'wet',
  issue_time: '2021-08-10T17:00:00Z', truth_kind: 'observed_mrms_10min',
  model, lead_minutes: lead, threshold_mm_h: 5, window_pixels: 11, window_km: 11.1, window_target_km: 10,
  hits: 10, misses: 2, false_alarms: 1, correct_negatives: 88,
  csi: .76, pod: .83, far: .09, fss,
  mae_mm_h: .8, rmse_mm_h: 1.2, mean_error_mm_h: .1,
  truth_coverage: 1, forecast_coverage: .99, common_coverage: .99,
  forecast_to_truth_coverage: .98, advection_domain_to_truth_coverage: .98,
  advection_boundary_loss_ratio: .02, interior_missing_loss_ratio: 0,
  boundary_adjusted_forecast_to_truth_coverage: 1,
  coverage_decomposition_closure_error: 0,
})

const mapFrame = {
  contract_version: '1.0', renderer_version: 'algorithm-verification-map-renderer-1.0.0',
  palette_version: 'rainfall-operational-v1', profile_version: 'rp016-mrms-v1',
  run_id: 'full-202108-v2', case_id: 'midwest_convection_20210810',
  issue_time: '2021-08-10T17:00:00Z', valid_time: '2021-08-10T17:10:00Z', lead_minutes: 10,
  truth_kind: 'observed_mrms_10min', operational_eligible: false, projection: 'EPSG:4326',
  pixel_edge_bounds: [-94.995, 38.995, -89.995, 41.005], fit_bounds: [-94.99, 39, -90, 41],
  width: 501, height: 201, rain_threshold_mm_h: .1, valid_no_rain_color: '#dce6e2',
  legend: [{ minimum_mm_h: .1, color: '#9dd9ff' }, { minimum_mm_h: 5, color: '#3ca85b' }],
  layers: [
    ['truth', null], ['forecast', 'lk'], ['forecast', 'persistence'], ['forecast', 'translation'],
  ].map(([role, model]) => ({
    asset_id: `lead-010-${model ?? 'truth'}`, role, model, lead_minutes: 10,
    valid_time: '2021-08-10T17:10:00Z', image_url: `/maps/${model ?? 'truth'}.png`,
    width: 501, height: 201, sha256: 'a'.repeat(64), size_bytes: 100,
    valid_cell_count: 100000, no_rain_cell_count: 90000, rain_cell_count: 10000,
    missing_cell_count: 701,
  })),
  motion: {
    fallback_used: false, fallback_reason: null, feature_count: 18,
    trackable_rain_pixel_count: 3400, unit: 'grid_cells_per_5_minutes',
    vectors: [{
      longitude: -92, latitude: 40, end_longitude: -91.98, end_latitude: 40.01,
      u_pixels_per_step: 2, v_pixels_per_step: 1,
    }],
  },
}

const probabilisticMapFrame = {
  ...mapFrame,
  profile_version: probabilisticMapRun.profile_version,
  run_id: probabilisticMapRun.run_id,
  case_id: 'holdout_convection_20250304',
  issue_time: '2025-03-04T06:00:00Z',
  valid_time: '2025-03-04T06:10:00Z',
  truth_kind: 'observed_mrms_preciprate_10min',
  layers: [
    ['truth', null], ['forecast', 'nowcastnet'], ['forecast', 'steps'],
    ['forecast', 'lk'], ['forecast', 'persistence'], ['forecast', 'phase_correlation'],
  ].map(([role, model]) => ({
    asset_id: `lead-010-${model ?? 'truth'}`, role, model, lead_minutes: 10,
    valid_time: '2025-03-04T06:10:00Z', image_url: `/maps/${model ?? 'truth'}.png`,
    width: 501, height: 201, sha256: 'b'.repeat(64), size_bytes: 100,
    valid_cell_count: 100000, no_rain_cell_count: 90000, rain_cell_count: 10000,
    missing_cell_count: 701,
  })),
  motion: { ...mapFrame.motion, vectors: [] },
}

const probabilisticProbabilityMapFrame = {
  contract_version: '1.0',
  renderer_version: 'algorithm-verification-probability-map-renderer-1.0.0',
  palette_version: 'raw-exceedance-probability-v1',
  profile_version: probabilisticProbabilityMapRun.profile_version,
  run_id: probabilisticProbabilityMapRun.run_id,
  case_id: 'holdout_convection_20250304',
  issue_time: '2025-03-04T06:00:00Z',
  valid_time: '2025-03-04T06:10:00Z',
  lead_minutes: 10,
  threshold_mm_h: 5,
  truth_kind: 'observed_mrms_preciprate_10min',
  calibration_status: 'raw_ensemble_relative_frequency_uncalibrated',
  operational_eligible: false,
  product_publication_enabled: false,
  projection: 'EPSG:4326',
  pixel_edge_bounds: [-94.995, 38.995, -89.995, 41.005],
  fit_bounds: [-94.99, 39, -90, 41],
  width: 501,
  height: 201,
  valid_no_event_color: '#dce6e2',
  legend: [
    { minimum_probability_percent: .1, color: '#bfe9ec' },
    { minimum_probability_percent: 25, color: '#3eb6c5' },
    { minimum_probability_percent: 50, color: '#2279b8' },
    { minimum_probability_percent: 75, color: '#5145a4' },
    { minimum_probability_percent: 100, color: '#b31945' },
  ],
  layers: [
    ['truth', null], ['forecast', 'nowcastnet'], ['forecast', 'steps'],
  ].map(([role, model]) => ({
    asset_id: `lead-010-threshold-005-${model ?? 'truth'}`,
    role, model, lead_minutes: 10, threshold_mm_h: 5,
    valid_time: '2025-03-04T06:10:00Z', image_url: `/probability-maps/${model ?? 'truth'}.png`,
    width: 501, height: 201, sha256: 'c'.repeat(64), size_bytes: 100,
    valid_cell_count: 100000, no_event_cell_count: 90000, event_cell_count: 10000,
    missing_cell_count: 701,
  })),
}

describe('AlgorithmVerificationWorkspace', () => {
  afterEach(() => {
    cleanup()
    vi.unstubAllGlobals()
    window.history.replaceState(null, '', '/')
  })

  it('shows one conclusion-first workflow and keeps the selected evidence in the URL', async () => {
    const fetchStatus = vi.fn().mockImplementation((input: string) => {
      let body: unknown = { items: [run, legacyRun] }
      if (input.includes('/metrics?')) body = { items: [
        metric('lk', 10, .72), metric('lk', 20, .66),
        metric('persistence', 10, .68), metric('persistence', 20, .60),
        metric('translation', 10, .70), metric('translation', 20, .65),
      ] }
      else if (input.includes('/map-frame?')) body = mapFrame
      else if (input.endsWith('/rp016-mrms-v1/full-202108-v2')) body = detail
      else if (input.endsWith('/rp016-mrms-v1/full-202108-v1')) body = { ...detail, run: legacyRun }
      return Promise.resolve({ ok: true, status: 200, json: async () => body })
    })
    vi.stubGlobal('fetch', fetchStatus)

    render(<AlgorithmVerificationWorkspace refreshToken={0} />)

    expect(screen.getByRole('heading', { name: '算法离线验证' })).toBeTruthy()
    expect(await screen.findByText('通过本轮工程门槛')).toBeTruthy()
    expect(await screen.findByRole('heading', { name: '同一时效空间对比' })).toBeTruthy()
    expect(await screen.findByText('MRMS 实况')).toBeTruthy()
    expect(await screen.findByText(/回波运动矢量 1 个/)).toBeTruthy()
    expect(await screen.findByText('排除边界后的域内覆盖')).toBeTruthy()
    expect(screen.getByText('原始 98.0% · 边界 2.0% · 域内缺测 0.0%')).toBeTruthy()
    expect(screen.getAllByText('+0.0200').length).toBeGreaterThan(0)
    expect(screen.getByText('工程证据 · 非福建业务验收')).toBeTruthy()
    fireEvent.click(screen.getByText('高级设置'))
    expect(screen.getByRole('button', { name: '10 km' }).getAttribute('title')).toBe('10 km，内部使用 11×11 网格窗口；当前报告实际覆盖 10.6 km–11.4 km')
    expect(screen.getByText('目标物理尺度，内部按 11×11 网格窗口计算')).toBeTruthy()
    expect(screen.queryByText('11 px')).toBeNull()
    expect(screen.getByText('10 km · 实际 11.1 km · 11×11 网格')).toBeTruthy()
    const caseSelect = screen.getByRole('combobox', { name: '典型案例' }) as HTMLSelectElement
    expect(caseSelect.value).toBe('midwest_convection_20210810')

    fireEvent.click(screen.getByRole('button', { name: '持续性' }))
    expect((await screen.findAllByText('+0.0400')).length).toBeGreaterThan(0)

    await waitFor(() => {
      expect(window.location.search).toContain('view=verification')
      expect(window.location.search).toContain('case=midwest_convection_20210810')
      expect(window.location.search).toContain('baseline=persistence')
    })
    await waitFor(() => expect(fetchStatus).toHaveBeenCalledWith(
      expect.stringContaining('/metrics?case_id=midwest_convection_20210810'),
      expect.objectContaining({ signal: expect.any(AbortSignal) }),
    ))
    expect(fetchStatus.mock.calls.some(([url]) => String(url).endsWith('/metrics.csv'))).toBe(false)

    fireEvent.click(screen.getByText('展开通过门槛'))
    expect(await screen.findByRole('heading', { name: 'LK 相对基线的 FSS 技巧' })).toBeTruthy()

    fireEvent.change(screen.getByRole('combobox', { name: '验证运行' }), {
      target: { value: 'rp016-mrms-v1/full-202108-v1' },
    })
    expect(await screen.findByText('该运行没有空间图层')).toBeTruthy()
    await waitFor(() => expect(screen.queryByText(/回波运动矢量 1 个/)).toBeNull())
  })

  it('shows a clear empty state when no verification report is mounted', async () => {
    vi.stubGlobal('fetch', vi.fn().mockResolvedValue({ ok: true, status: 200, json: async () => ({ items: [] }) }))
    render(<AlgorithmVerificationWorkspace refreshToken={0} />)
    expect(await screen.findByText(/尚未挂载算法验证报告/)).toBeTruthy()
  })

  it('shows the frozen probabilistic comparison without querying deterministic evidence', async () => {
    const fetchStatus = vi.fn().mockImplementation((input: string) => {
      const body = input.endsWith('/rp026-mrms-nowcastnet-v1/holdout-v1')
        ? probabilisticDetail
        : { items: [probabilisticRun, run] }
      return Promise.resolve({ ok: true, status: 200, json: async () => body })
    })
    vi.stubGlobal('fetch', fetchStatus)

    render(<AlgorithmVerificationWorkspace refreshToken={0} />)

    expect(await screen.findByText('STEPS 保持主基线，NowcastNet 保留为离线候选')).toBeTruthy()
    expect(await screen.findByRole('heading', { name: '模型概率技巧对比' })).toBeTruthy()
    expect(screen.getAllByText('+20.2%').length).toBeGreaterThan(0)
    expect(screen.getAllByText('-36.6%').length).toBeGreaterThan(0)
    expect(screen.getByRole('heading', { name: 'NowcastNet 相对基线的 Brier 技巧' })).toBeTruthy()
    expect(screen.getAllByText('主概率基线 · 12 成员').length).toBe(2)
    expect(screen.getByText('完整流程')).toBeTruthy()
    expect(screen.getByText('30.1 s')).toBeTruthy()
    expect(fetchStatus.mock.calls.some(([url]) => String(url).includes('/metrics?'))).toBe(false)
    expect(fetchStatus.mock.calls.some(([url]) => String(url).includes('/map-frame?'))).toBe(false)
    await waitFor(() => {
      expect(window.location.search).toContain('run=rp026-mrms-nowcastnet-v1%2Fholdout-v1')
      expect(window.location.search).not.toContain('case=')
    })
  })

  it('shows georeferenced ensemble-mean evidence when a probabilistic map bundle exists', async () => {
    const fetchStatus = vi.fn().mockImplementation((input: string) => {
      let body: unknown = { items: [probabilisticMapRun] }
      if (input.includes('/map-frame?')) body = probabilisticMapFrame
      else if (input.endsWith('/rp026-mrms-nowcastnet-v1/holdout-map-v1')) {
        body = probabilisticMapDetail
      }
      return Promise.resolve({ ok: true, status: 200, json: async () => body })
    })
    vi.stubGlobal('fetch', fetchStatus)

    render(<AlgorithmVerificationWorkspace refreshToken={0} />)

    expect(await screen.findByRole('heading', { name: '集合均值空间对比' })).toBeTruthy()
    expect((await screen.findAllByText('NowcastNet 集合均值')).length).toBeGreaterThan(0)
    expect((await screen.findAllByText('STEPS 集合均值')).length).toBeGreaterThan(0)
    expect(screen.getAllByText(/不是阈值概率图/).length).toBeGreaterThan(0)
    await waitFor(() => expect(fetchStatus).toHaveBeenCalledWith(
      expect.stringContaining('/map-frame?case_id=holdout_convection_20250304'),
      expect.objectContaining({ signal: expect.any(AbortSignal) }),
    ))
    await waitFor(() => {
      expect(window.location.search).toContain('case=holdout_convection_20250304')
      expect(window.location.search).toContain('lead=10')
    })
    expect(fetchStatus.mock.calls.some(([url]) => String(url).includes('/metrics?'))).toBe(false)
  })

  it('shows synchronized raw threshold-exceedance probability GIS without changing scores', async () => {
    const fetchStatus = vi.fn().mockImplementation((input: string) => {
      let body: unknown = { items: [probabilisticProbabilityMapRun] }
      if (input.includes('/probability-map-frame?')) body = probabilisticProbabilityMapFrame
      else if (input.endsWith('/rp026-mrms-nowcastnet-v1/holdout-probability-map-v1')) {
        body = probabilisticProbabilityMapDetail
      }
      return Promise.resolve({ ok: true, status: 200, json: async () => body })
    })
    vi.stubGlobal('fetch', fetchStatus)

    render(<AlgorithmVerificationWorkspace refreshToken={0} />)

    expect(await screen.findByRole('heading', { name: '超阈值概率空间对比' })).toBeTruthy()
    expect(screen.getByRole('button', { name: '超阈概率' }).getAttribute('aria-pressed')).toBe('true')
    expect(screen.getByRole('button', { name: '≥ 5 mm/h' }).getAttribute('aria-pressed')).toBe('true')
    expect((await screen.findAllByText('NowcastNet 超阈概率')).length).toBeGreaterThan(0)
    expect((await screen.findAllByText('STEPS 超阈概率')).length).toBeGreaterThan(0)
    expect(screen.getByText(/原始相对频率，未经校准、不可发布/)).toBeTruthy()
    await waitFor(() => expect(fetchStatus).toHaveBeenCalledWith(
      expect.stringContaining('/probability-map-frame?case_id=holdout_convection_20250304'),
      expect.objectContaining({ signal: expect.any(AbortSignal) }),
    ))
    await waitFor(() => {
      expect(window.location.search).toContain('map=probability')
      expect(window.location.search).toContain('probability_threshold=5')
    })
    fireEvent.click(screen.getByRole('button', { name: '集合均值雨强' }))
    expect(await screen.findByRole('heading', { name: '集合均值空间对比' })).toBeTruthy()
    await waitFor(() => expect(fetchStatus.mock.calls.some(([url]) => String(url).includes('/map-frame?'))).toBe(true))
  })

  it('does not query a newly selected run with stale selectors and labels its fixed truth domain', async () => {
    const rigorousDetail = {
      ...detail,
      run: rigorousRun,
      cases: [{ case_id: 'fred_20210816', category: 'wet', issue_times: ['2021-08-16T12:00:00Z'] }],
      filters: {
        ...detail.filters,
        models: ['lk', 'persistence', 'translation', 'phase_correlation'],
        lead_minutes: [60],
        windows_pixels: [1, 5, 9],
        fss_scales: [
          { window_pixels: 1, target_km: 1, actual_km_min: 1.01, actual_km_max: 1.01 },
          { window_pixels: 5, target_km: 5, actual_km_min: 5.06, actual_km_max: 5.06 },
          { window_pixels: 9, target_km: 10, actual_km_min: 9.11, actual_km_max: 9.11 },
        ],
      },
    }
    const fetchStatus = vi.fn().mockImplementation((input: string) => {
      let body: unknown = { items: [run, rigorousRun] }
      if (input.includes('/metrics?')) body = input.includes('/rp018-mrms-v1/rp018-smoke/')
        ? { items: [{
            ...metric('lk', 60, .7),
            case_id: 'fred_20210816',
            issue_time: '2021-08-16T12:00:00Z',
            window_pixels: 11,
            window_km: 10.7,
            window_target_km: 10,
          }] }
        : { items: [] }
      else if (input.includes('/map-frame?')) body = mapFrame
      else if (input.endsWith('/rp016-mrms-v1/full-202108-v2')) body = detail
      else if (input.endsWith('/rp018-mrms-v1/rp018-smoke')) body = rigorousDetail
      return Promise.resolve({ ok: true, status: 200, json: async () => body })
    })
    vi.stubGlobal('fetch', fetchStatus)

    render(<AlgorithmVerificationWorkspace refreshToken={0} />)
    expect(await screen.findByText('Midwest Convection')).toBeTruthy()

    fireEvent.change(screen.getByRole('combobox', { name: '验证运行' }), {
      target: { value: 'rp018-mrms-v1/rp018-smoke' },
    })

    expect(await screen.findByText('Fred')).toBeTruthy()
    expect(fetchStatus.mock.calls.some(([url]) => (
      String(url).includes('/rp018-mrms-v1/rp018-smoke/metrics?case_id=midwest_convection_20210810')
    ))).toBe(false)
    expect(fetchStatus.mock.calls.some(([url]) => (
      String(url).includes('/rp018-mrms-v1/rp018-smoke/map-frame?case_id=midwest_convection_20210810')
    ))).toBe(false)
    expect(screen.getByText('实况固定有效域（模型缺测按无预报）')).toBeTruthy()
    fireEvent.click(screen.getByText('高级设置'))
    expect(screen.getByRole('button', { name: '10 km' }).getAttribute('aria-pressed')).toBe('true')
    await waitFor(() => expect(window.location.search).toContain('window=9'))
    expect(await screen.findByText('10 km · 实际 10.7 km · 11×11 网格')).toBeTruthy()
  })
})
