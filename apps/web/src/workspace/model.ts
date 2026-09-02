export type WorkspacePreset = 'forecast' | 'qc' | 'verification'

export type CycleCapabilities = {
  radar: boolean
  lk: boolean
  steps: boolean
  nowcastnet: boolean
}

export type CycleSummary = {
  cycle_id: string
  issue_time: string
  grid_id: string
  execution_mode: string
  freshness_seconds: number
  capabilities: CycleCapabilities
  analysis_id?: string
  run_id?: string
  ensemble_bundle_id?: string
}

export type CycleList = {
  schema_version: '1.0'
  items: CycleSummary[]
  generated_at: string
  degraded_sources?: string[]
}

export type WorkspaceLegendEntry = {
  minimum?: number
  label?: string
  color: string
}

export type WorkspaceFrame = {
  asset_id: string
  valid_time: string
  lead_time_minutes: number
  image_url: string
  media_type: string
  unit?: string
  sha256?: string
  coverage_ratio?: number
  valid_cell_count?: number
  missing_cell_count?: number
  bounds?: [number, number, number, number]
}

export type WorkspacePanel = {
  panel_id: string
  algorithm_id: string
  display_name: string
  role: 'observation' | 'forecast' | 'qc' | 'diagnostic'
  lifecycle: 'analysis' | 'shadow' | 'offline' | 'operational'
  data_kind: string
  cadence_minutes: number
  status: 'ready' | 'unavailable' | 'running' | 'failed'
  unavailable_reason?: string
  radar_id?: string
  legend_unit?: string
  legend?: WorkspaceLegendEntry[]
  frames: WorkspaceFrame[]
}

export type WorkspaceRadar = {
  radar_id: string
  state: string
  scan_id?: string
  time_offset_seconds?: number
  mean_quality_index?: number
}

export type WorkspaceCycleDetail = CycleSummary & {
  schema_version: '1.0'
  analysis_trace?: {
    analysis_id: string
    analysis_config_version?: string
    analysis_created_at?: string
    mosaic_config_version?: string
    mosaic_algorithm_version?: string
    input_mosaic_uri?: string
    qpe_config_version?: string
  }
  grid: {
    grid_id: string
    bounds: [number, number, number, number]
    raster_bounds: [number, number, number, number]
  }
  quality: {
    coverage_ratio?: number
    mean_quality_index?: number
    maximum_rate_mm_h?: number
    p95_rate_mm_h?: number
  }
  radars: WorkspaceRadar[]
  timeline: string[]
  panels: WorkspacePanel[]
  warnings?: string[]
}

const forecastPanelIDs = ['qpe', 'lk', 'steps', 'nowcastnet'] as const

export function panelByID(detail: WorkspaceCycleDetail, panelID: string) {
  return detail.panels.find((panel) => panel.panel_id === panelID) ?? null
}

export function radarIDs(detail: WorkspaceCycleDetail) {
  const values = new Set(
    detail.panels
      .filter((panel) => panel.role === 'qc' && panel.radar_id)
      .map((panel) => panel.radar_id as string),
  )
  detail.radars.forEach((radar) => values.add(radar.radar_id))
  return Array.from(values).sort()
}

export function analysisCycleAt(
  cycles: CycleSummary[],
  gridID: string,
  issueTime: string,
) {
  const target = Date.parse(issueTime)
  if (!Number.isFinite(target)) return null
  return cycles.find((cycle) => (
    cycle.grid_id === gridID
    && cycle.capabilities.radar
    && Date.parse(cycle.issue_time) === target
  )) ?? null
}

export function timelineForPreset(
  detail: WorkspaceCycleDetail,
  cycles: CycleSummary[],
  preset: WorkspacePreset,
) {
  if (preset !== 'qc') return detail.timeline
  return detail.timeline.filter((value) => (
    Date.parse(value) === Date.parse(detail.issue_time)
    || analysisCycleAt(cycles, detail.grid_id, value) != null
  ))
}

export function panelsForPreset(
  detail: WorkspaceCycleDetail,
  preset: WorkspacePreset,
  radarID: string | null,
) {
  if (preset !== 'qc') {
    return forecastPanelIDs
      .map((panelID) => panelByID(detail, panelID))
      .filter((panel): panel is WorkspacePanel => panel != null)
  }
  const selectedRadar = radarID ?? radarIDs(detail)[0] ?? ''
  const radarFlagsPanelID = `qc_flags:${selectedRadar}`
  const candidates = [
    `dbzh_raw:${selectedRadar}`,
    `dbzh_qc:${selectedRadar}`,
    panelByID(detail, radarFlagsPanelID) ? radarFlagsPanelID : 'analysis:qc_flags',
    'qpe',
  ]
  const selected = candidates
    .map((panelID) => panelByID(detail, panelID))
    .filter((panel): panel is WorkspacePanel => panel != null)
  if (selected.length >= 2) return selected.slice(0, 4)
  return detail.panels.filter((panel) => panel.role === 'qc' || panel.panel_id === 'qpe').slice(0, 4)
}

export function frameAt(panel: WorkspacePanel, validTime: string | null) {
  if (!validTime) return panel.frames[0] ?? null
  const target = Date.parse(validTime)
  return panel.frames.find((frame) => Date.parse(frame.valid_time) === target) ?? null
}

export function availabilityAt(panel: WorkspacePanel, validTime: string) {
  return frameAt(panel, validTime) != null
}

export function formatCycleTime(value: string) {
  const date = new Date(value)
  const local = new Intl.DateTimeFormat('zh-CN', {
    timeZone: 'Asia/Taipei',
    month: '2-digit',
    day: '2-digit',
    hour: '2-digit',
    minute: '2-digit',
    hour12: false,
  }).format(date)
  const utc = new Intl.DateTimeFormat('zh-CN', {
    timeZone: 'UTC',
    hour: '2-digit',
    minute: '2-digit',
    hour12: false,
  }).format(date)
  return `${local} CST · ${utc} UTC`
}

export function formatValidTime(value: string | null) {
  if (!value) return '暂无有效时间'
  return formatCycleTime(value)
}

export function leadLabel(issueTime: string, validTime: string | null) {
  if (!validTime) return '无时效'
  const minutes = Math.round((Date.parse(validTime) - Date.parse(issueTime)) / 60_000)
  if (minutes === 0) return 'T0'
  return `${minutes > 0 ? '+' : ''}${minutes} min`
}

export function reasonLabel(reason?: string) {
  const labels: Record<string, string> = {
    radar_qpe_unavailable: '当前周期未生成雷达 QPE',
    lk_product_unavailable: '当前周期未生成 LK 产品',
    steps_product_unavailable: '当前周期未生成 STEPS 产品',
    shadow_input_or_product_unavailable: '影子输入不满足或尚未生成 NowcastNet 产品',
    fixed_roi_has_missing_cells: '固定输入区域存在缺测',
    fixed_roi_outside_grid: '固定输入区域超出当前雷达网格',
    invalid_rain_rate: '输入雨强包含无效值',
    missing_required_frame: '缺少模型要求的连续输入帧',
    source_grid_shape_mismatch: '连续输入帧的空间网格不一致',
    spatial_shape_not_supported: '当前模型不支持福建固定空间尺寸',
    spatial_shape_not_validated: '福建固定空间尺寸尚未通过 GPU 验证',
    shadow_inference_disabled: '影子推理尚未启用',
    shadow_inference_pending: '影子输入已通过，等待推理结果',
    shadow_probe_starting: 'NowcastNet 输入探测正在启动',
    shadow_probe_failed: 'NowcastNet 输入探测失败',
    shadow_input_ineligible: 'NowcastNet 影子输入未通过门禁',
    shadow_cycle_not_yet_evaluated: '该周期尚未完成 NowcastNet 输入探测',
    shadow_status_unknown: 'NowcastNet 影子状态未知',
  }
  return labels[reason ?? ''] ?? reason ?? '当前面板不可用'
}

const qcFlagLabels: Record<string, string> = {
  GROUND_CLUTTER: '地物杂波',
  SEA_CLUTTER: '海杂波',
  ANOMALOUS_PROPAGATION: '异常传播',
  RADIAL_INTERFERENCE: '径向干扰',
  HARDWARE_ANOMALY: '硬件异常',
  BIOLOGICAL_ECHO: '生物回波',
  BEAM_BLOCKED: '波束遮挡',
  ATTENUATED: '信号衰减',
  WET_RADOME: '湿天线罩',
  BRIGHT_BAND: '零度层亮带',
  VELOCITY_ALIASED: '速度模糊',
  LOW_SNR: '低信噪比',
  MISSING: '缺测',
  CORRECTED: '已订正',
  LOW_QUALITY: '低质量',
}

export function qcFlagLabel(value: string) {
  return qcFlagLabels[value] ?? value
}
