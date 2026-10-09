export type DataflowJobStage = {
  stage: string
  job_id: string
  status: 'PENDING' | 'RUNNING' | 'SUCCEEDED' | 'FAILED' | 'SKIPPED' | string
  created_at: string
  started_at?: string | null
  finished_at?: string | null
  runtime_ms?: number | null
  error_code?: string
  error_message?: string
  model_id?: string
}

export type DataflowScanBlock = {
  scan_id: string
  run_id: string
  volume_start: string
  volume_end: string
  received_at: string
  status: string
  degraded_reason?: string
  scan_completeness?: number | null
  mean_quality_index?: number | null
  stages: DataflowJobStage[]
}

export type DataflowAnalysisBlock = {
  analysis_id: string
  run_id: string
  analysis_time: string
  grid_id: string
  status: string
  degraded_reason?: string
  radar_count: number
  coverage_ratio?: number | null
  created_at: string
  stages: DataflowJobStage[]
}

export type DataflowForecastBlock = {
  run_id: string
  issue_time: string
  grid_id: string
  status: string
  created_at: string
  updated_at?: string | null
  stages: DataflowJobStage[]
}

export type DataflowRadarLane = {
  radar_id: string
  blocks: DataflowScanBlock[]
}

export type DataflowStageSummary = {
  key: string
  label: string
  completed: number
  running: number
  queued: number
  failed: number
  p50_ms: number
}

export type DataflowRadarStatus = {
  radar_id: string
  display_name?: string
  health: 'HEALTHY' | 'DEGRADED' | 'UNAVAILABLE' | string
  latest_scan_time?: string | null
  scan_status?: string
  scan_completeness?: number | null
  mean_quality_index?: number | null
  data_delay_seconds?: number | null
  participating_in_latest_analysis: boolean
  qc_duration_ms?: number | null
}

export type DataflowEvent = {
  time: string
  kind: 'scan.received' | 'job.succeeded' | 'job.failed' | 'analysis.created' | 'run.published' | string
  label: string
  radar_id?: string
  detail?: string
}

export type DataflowStageTrendSeries = {
  key: string
  label: string
  values: (number | null)[]
}

export type DataflowStageTrend = {
  schema_version: string
  generated_at: string
  hours: number
  bucket_minutes: number
  buckets: string[]
  series: DataflowStageTrendSeries[]
}

export type DataflowSnapshot = {
  schema_version: string
  generated_at: string
  window_start: string
  window_minutes: number
  stages: DataflowStageSummary[]
  radar_lanes: DataflowRadarLane[]
  analysis_blocks: DataflowAnalysisBlock[]
  forecast_blocks: DataflowForecastBlock[]
  radar_statuses: DataflowRadarStatus[]
  events: DataflowEvent[]
  warnings?: string[]
}

export type IngestSource = {
  source_id?: string
  radar_id?: string
  latest_scan_at?: string
  latest_success_at?: string
  latest_volume_at?: string
  registered_count?: number
  failure_count?: number
  last_error?: string
}

export type IngestStatus = {
  sources?: IngestSource[]
} | {
  status?: string
  reason?: string
}
